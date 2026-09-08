// Package gateway is the core of agents-bot: it initializes the mezon-sdk-go
// client, listens for voice/chat events to populate the user cache, manages
// an active-room registry, and serves an HTTP API for agents to resolve
// user profiles and register rooms.
package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	mezon "github.com/quangledang23/mezon-sdk-go"
	mezonapi "github.com/quangledang23/mezon-sdk-go/api"
	"github.com/quangledang23/mezon-sdk-go/rtapi"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/config"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/orchestratorclient"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/userresolver"
)

// RoomInfo holds the active room metadata registered by an agent.
type RoomInfo struct {
	RoomName string `json:"room_name"` // SFU numeric room id as string
	RoomID   string `json:"room_id"`   // orchestrator UUID
}

type orchestratorAPI interface {
	PushChatExternal(ctx context.Context, roomName, roomID, participantIdentity, message, timeStr string) error
}

// Gateway is the main service struct.
type Gateway struct {
	cfg        config.Config
	client     *mezon.MezonClient
	accountAPI *mezon.MezonApi
	resolver   *userresolver.Resolver
	orch       orchestratorAPI

	// activeRooms maps room_name (SFU numeric id as string) → RoomInfo.
	// The agent registers its room here so the gateway knows which
	// channel messages to forward and has the orchestrator UUID.
	roomsMu     sync.RWMutex
	activeRooms map[string]*RoomInfo // keyed by room_name
}

// New creates a new Gateway (does not start it — call Run).
func New(cfg config.Config) (*Gateway, error) {
	useSSL := cfg.MezonUseSSL
	mezonCfg := mezon.ClientConfig{
		BotID:                 cfg.BotID,
		Token:                 cfg.BotToken,
		TLSInsecureSkipVerify: cfg.MezonTLSInsecure,
	}
	if cfg.MezonHost != "" {
		mezonCfg.Host = cfg.MezonHost
	}
	if cfg.MezonPort != "" {
		mezonCfg.Port = cfg.MezonPort
	}
	if !useSSL {
		f := false
		mezonCfg.UseSSL = &f
	}

	client, err := mezon.NewMezonClient(mezonCfg)
	if err != nil {
		return nil, fmt.Errorf("gateway: init mezon client: %w", err)
	}

	g := &Gateway{
		cfg:         cfg,
		client:      client,
		resolver:    userresolver.New(),
		orch:        orchestratorclient.New(cfg.OrchestratorBaseURL, cfg.InternalAPISecret),
		activeRooms: make(map[string]*RoomInfo),
	}

	g.registerEventHandlers()
	return g, nil
}

// Run starts the gateway: logs into Mezon and starts the HTTP server.
// Blocks until ctx is cancelled.
func (g *Gateway) Run(ctx context.Context) error {
	logging.L.Info("agents-bot: logging into Mezon")
	if err := g.client.Login(); err != nil {
		return fmt.Errorf("gateway: mezon login: %w", err)
	}
	logging.L.Info("agents-bot: logged in", "client_id", g.client.ClientID)
	logging.L.Info("agents-bot: clans cached", "count", g.client.Clans.Size())
	g.accountAPI = newAccountAPI(g.client)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", g.handleHealthz)
	mux.HandleFunc("GET /api/bot/profile", g.handleGetBotProfile)
	mux.HandleFunc("GET /api/users/{id}", g.handleGetUser)
	mux.HandleFunc("POST /api/users", g.handleBatchUsers)
	mux.HandleFunc("POST /api/rooms/register", g.handleRoomRegister)
	mux.HandleFunc("POST /api/rooms/unregister", g.handleRoomUnregister)
	mux.HandleFunc("GET /api/rooms/{room_name}/participants", g.handleGetRoomParticipants)
	mux.HandleFunc("POST /api/rooms/{room_name}/chat", g.handleSendChatMessage)

	addr := fmt.Sprintf(":%d", g.cfg.GatewayPort)
	srv := &http.Server{Addr: addr, Handler: mux}

	go func() {
		<-ctx.Done()
		logging.L.Info("agents-bot: shutting down HTTP server")
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutCtx)
	}()

	logging.L.Info("agents-bot: HTTP server listening", "address", addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return fmt.Errorf("gateway: http server: %w", err)
	}
	return nil
}

// registerEventHandlers wires SDK event listeners.
func (g *Gateway) registerEventHandlers() {
	// VoiceJoinedEvent: primary source of user_id → participant name mapping
	g.client.On(mezon.EventVoiceJoined, func(payload any) {
		ev, ok := payload.(*rtapi.VoiceJoinedEvent)
		if !ok || ev == nil {
			return
		}
		userID := strconv.FormatInt(ev.UserId, 10)
		channelID := strconv.FormatInt(ev.VoiceChannelId, 10)
		clanID := ""
		if ev.ClanId != 0 {
			clanID = strconv.FormatInt(ev.ClanId, 10)
		}

		g.resolver.CacheFromVoiceJoined(userID, ev.Participant, channelID, clanID)
		logging.L.Debug(
			"agents-bot: voice joined",
			"user_id", userID,
			"name", ev.Participant,
			"channel_id", channelID,
			"clan_id", clanID,
			"channel_label", ev.VoiceChannelLabel,
			"clan_name", ev.ClanName,
		)
	})

	// VoiceLeavedEvent: remove current channel membership but keep the user
	// profile cache because the user may rejoin later.
	g.client.On(mezon.EventVoiceLeaved, func(payload any) {
		ev, ok := payload.(*rtapi.VoiceLeavedEvent)
		if !ok || ev == nil {
			return
		}
		userID := strconv.FormatInt(ev.VoiceUserId, 10)
		channelID := strconv.FormatInt(ev.VoiceChannelId, 10)
		g.resolver.RemoveFromVoiceChannel(userID, channelID)
		logging.L.Debug("agents-bot: voice left", "user_id", userID, "channel_id", channelID)
	})

	// ChannelMessage: cache user info + forward to orchestrator if room is active
	g.client.OnChannelMessage(func(m *mezon.ChannelMessage) {
		if m == nil {
			return
		}
		// Skip messages from the bot itself
		if m.SenderID == g.client.ClientID {
			return
		}
		logging.L.Info(
			"agents-bot: channel message received",
			"sender_id", m.SenderID,
			"username", m.Username,
			"room_name", m.ChannelID,
			"clan_id", m.ClanID,
			"message_id", m.MessageID,
		)

		// Cache user profile from message fields
		if m.SenderID != "" {
			g.resolver.CacheFromMessage(
				m.SenderID, m.Username, m.DisplayName, m.ClanID, m.ClanNick, m.Avatar,
			)
		}

		// Forward chat to orchestrator if this channel belongs to an active room
		g.forwardChatIfActive(m)
	})

	g.client.OnReady(func() {
		logging.L.Info(
			"agents-bot: SDK ready",
			"client_id", g.client.ClientID,
			"clans", g.client.Clans.Size(),
			"user_cache", g.resolver.Size(),
		)
	})
}

// forwardChatIfActive checks if the message's channel belongs to an active
// meeting room and, if so, POSTs it to orchestrator's agent_push_chat_external.
func (g *Gateway) forwardChatIfActive(m *mezon.ChannelMessage) {
	// room_name is the Mezon voice channel ID represented as a string.
	roomName := m.ChannelID
	g.roomsMu.RLock()
	room, active := g.activeRooms[roomName]
	g.roomsMu.RUnlock()
	if !active || room == nil {
		return
	}

	// Stable numeric identity -- match the agent's STT pipeline (SenderID).
	// Display names are resolved at render time via agents-bot's /api/users.
	identity := m.SenderID

	message := m.ContentText()
	if message == "" {
		return
	}

	timeStr := ""
	if m.CreateTimeSeconds > 0 {
		timeStr = time.Unix(int64(m.CreateTimeSeconds), 0).UTC().Format(time.RFC3339)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := g.orch.PushChatExternal(ctx, room.RoomName, room.RoomID, identity, message, timeStr); err != nil {
		logging.L.Error(
			"agents-bot: push chat external failed",
			append(logging.ErrAttrs(err), "room_name", room.RoomName, "participant_identity", identity)...,
		)
	} else {
		logging.L.Info(
			"agents-bot: chat forwarded",
			"participant_identity", identity,
			"room_name", room.RoomName,
			"room_id", room.RoomID,
		)
	}
}

// ─── HTTP Handlers ──────────────────────────────────────────────────────

func (g *Gateway) handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     "ok",
		"user_cache": g.resolver.Size(),
	})
}

type botProfileResponse struct {
	Username string `json:"username"`
	Avatar   string `json:"avatar"`
}

func (g *Gateway) handleGetBotProfile(w http.ResponseWriter, r *http.Request) {
	// The SDK creates the pseudo-clan "0" during login and keeps its session
	// token refreshed. GetAccount returns the currently authenticated bot.
	clan, ok := g.client.Clans.Get("0")
	if !ok || clan == nil || clan.SessionToken == "" || g.accountAPI == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "bot_profile_unavailable"})
		return
	}

	account := &mezonapi.Account{}
	if err := g.accountAPI.Call(clan.SessionToken, "GetAccount", nil, account); err != nil {
		logging.L.Error("agents-bot: get bot profile failed", logging.ErrAttrs(err)...)
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "bot_profile_lookup_failed"})
		return
	}

	user := account.GetUser()
	if user == nil || user.GetUsername() == "" {
		logging.L.Error("agents-bot: get bot profile returned no user")
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "bot_profile_invalid"})
		return
	}

	writeJSON(w, http.StatusOK, botProfileResponse{
		Username: user.GetUsername(),
		Avatar:   user.GetAvatarUrl(),
	})
}

func (g *Gateway) handleGetUser(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing user id"})
		return
	}

	user := g.resolver.Get(id)
	if user == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{
			"error":   "user_not_found",
			"user_id": id,
		})
		return
	}

	clanID, _ := g.resolveRoomClanContext(r.URL.Query().Get("room_name"))
	writeJSON(w, http.StatusOK, newUserResponseItem(user, clanID))
}

type batchRequest struct {
	UserIDs  []string `json:"user_ids"`
	RoomName string   `json:"room_name,omitempty"`
}

type userResponseItem struct {
	UserID       string `json:"user_id"`
	Username     string `json:"username"`
	DisplayName  string `json:"display_name"`
	ClanNick     string `json:"clan_nick,omitempty"`
	DisplayLabel string `json:"display_label"`
	Avatar       string `json:"avatar,omitempty"`
}

type batchResponse struct {
	Users           []userResponseItem `json:"users"`
	NotFound        []string           `json:"not_found"`
	ContextResolved bool               `json:"context_resolved"`
}

func (g *Gateway) handleBatchUsers(w http.ResponseWriter, r *http.Request) {
	var req batchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if len(req.UserIDs) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "user_ids required"})
		return
	}
	clanID, contextResolved := g.resolveRoomClanContext(req.RoomName)
	found, notFound := g.resolver.GetBatch(req.UserIDs)
	users := make([]userResponseItem, 0, len(found))
	for _, user := range found {
		users = append(users, newUserResponseItem(user, clanID))
	}
	if notFound == nil {
		notFound = []string{}
	}
	writeJSON(w, http.StatusOK, batchResponse{
		Users:           users,
		NotFound:        notFound,
		ContextResolved: contextResolved,
	})
}

type roomRegisterRequest struct {
	RoomName string `json:"room_name"` // SFU numeric room id
	RoomID   string `json:"room_id"`   // orchestrator UUID
}

func (g *Gateway) handleRoomRegister(w http.ResponseWriter, r *http.Request) {
	var req roomRegisterRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if req.RoomName == "" || req.RoomID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "room_name and room_id required"})
		return
	}

	g.roomsMu.Lock()
	g.activeRooms[req.RoomName] = &RoomInfo{
		RoomName: req.RoomName,
		RoomID:   req.RoomID,
	}
	g.roomsMu.Unlock()

	logging.L.Info("agents-bot: room registered", "room_name", req.RoomName, "room_id", req.RoomID)
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "room_name": req.RoomName})
}

type roomUnregisterRequest struct {
	RoomName string `json:"room_name"`
	RoomID   string `json:"room_id"`
}

func (g *Gateway) handleRoomUnregister(w http.ResponseWriter, r *http.Request) {
	var req roomUnregisterRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if req.RoomName == "" || req.RoomID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "room_name and room_id required"})
		return
	}

	g.roomsMu.Lock()
	current, exists := g.activeRooms[req.RoomName]
	if exists {
		if current.RoomID != req.RoomID {
			g.roomsMu.Unlock()
			logging.L.Warn(
				"agents-bot: stale unregister ignored",
				"room_name", req.RoomName,
				"active_room_id", current.RoomID,
				"requested_room_id", req.RoomID,
			)
			writeJSON(w, http.StatusOK, map[string]string{"status": "ignored_stale_session", "room_name": req.RoomName})
			return
		}
		delete(g.activeRooms, req.RoomName)
	}
	g.roomsMu.Unlock()

	logging.L.Info("agents-bot: room unregistered", "room_name", req.RoomName, "room_id", req.RoomID)
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "room_name": req.RoomName})
}

type roomParticipant struct {
	ParticipantIdentity string `json:"participant_identity"`
	Username            string `json:"username,omitempty"`
}

func (g *Gateway) handleGetRoomParticipants(w http.ResponseWriter, r *http.Request) {
	roomName := r.PathValue("room_name")
	if roomName == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing room_name"})
		return
	}

	clanID := g.resolver.GetChannelClan(roomName)
	users := g.resolver.GetChannelUsers(roomName)

	participants := make([]roomParticipant, 0, len(users))
	for _, u := range users {
		participants = append(participants, roomParticipant{
			ParticipantIdentity: u.UserID,
			Username:            u.KnownDisplayLabel(clanID),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"room_name":    roomName,
		"participants": participants,
	})
}

// maxChatBodyBytes bounds the request body for handleSendChatMessage only --
// scoped to this new handler deliberately, not retrofitted onto the older
// handlers above (a separate, already-tracked finding, left alone here to
// avoid conflicting with other in-flight work on this file). 64KB is
// generous headroom over the SDK's own 8000 UTF-16-code-unit content cap
// (content.go's maxContentLength, enforced inside Send below).
const maxChatBodyBytes = 64 * 1024

type chatMessageRequest struct {
	Message string `json:"message"`
}

// handleSendChatMessage lets orchestrator post a message into a room's Mezon
// chat as this bot -- the send_chat_message agent-request flow
// (mezon-sfu-migration-plan.md). Orchestrator calls this directly instead of
// relaying through the per-room Go agent's SSE agent-request listener
// (unlike tts_play/transcript_control): agents-bot, not the Go agent, is the
// only thing holding a live Mezon bot session, so routing this through the
// Go agent first would just be a pass-through hop with no state of its own
// to contribute.
//
// No auth on this endpoint yet -- deliberate, deferred to a dedicated
// security epic (mezon-sfu-migration-plan.md) rather than bolted on here.
func (g *Gateway) handleSendChatMessage(w http.ResponseWriter, r *http.Request) {
	roomName := r.PathValue("room_name")
	if roomName == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing room_name"})
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxChatBodyBytes)
	var req chatMessageRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		logging.L.Warn("agents-bot: send_chat_message: invalid request body",
			append(logging.ErrAttrs(err), "room_name", roomName)...)
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	message := strings.TrimSpace(req.Message)
	if message == "" {
		logging.L.Warn("agents-bot: send_chat_message: empty message, refusing to send", "room_name", roomName)
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "message required"})
		return
	}

	// Gate: only send into a room this instance currently considers live --
	// same activeRooms registry forwardChatIfActive already gates the
	// opposite (inbound) direction on, so "is this room active" has exactly
	// one source of truth for both directions.
	g.roomsMu.RLock()
	_, active := g.activeRooms[roomName]
	g.roomsMu.RUnlock()
	if !active {
		logging.L.Warn("agents-bot: send_chat_message: room not active, refusing to send", "room_name", roomName)
		writeJSON(w, http.StatusConflict, map[string]string{"error": "room_not_active", "room_name": roomName})
		return
	}

	logging.L.Debug("agents-bot: send_chat_message: sending", "room_name", roomName, "message_length", len(message))

	channel, err := g.client.Channels.Fetch(roomName)
	if err != nil {
		logging.L.Error("agents-bot: send_chat_message: fetch channel failed",
			append(logging.ErrAttrs(err), "room_name", roomName)...)
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "channel_fetch_failed"})
		return
	}

	msg, err := channel.Send(mezon.Text(message), &mezon.SendOptions{})
	if err != nil {
		logging.L.Error("agents-bot: send_chat_message: send failed",
			append(logging.ErrAttrs(err), "room_name", roomName)...)
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "send_failed"})
		return
	}

	messageID := ""
	if msg != nil {
		messageID = msg.MessageID()
	}
	logging.L.Info("agents-bot: send_chat_message: sent",
		"room_name", roomName, "message_id", messageID, "message_length", len(message))
	writeJSON(w, http.StatusOK, map[string]string{"status": "sent", "room_name": roomName, "message_id": messageID})
}

// ─── Helpers ────────────────────────────────────────────────────────────

func newUserResponseItem(user *userresolver.UserInfo, clanID string) userResponseItem {
	return userResponseItem{
		UserID:       user.UserID,
		Username:     user.Username,
		DisplayName:  user.DisplayName,
		ClanNick:     user.ClanNick(clanID),
		DisplayLabel: user.KnownDisplayLabel(clanID),
		Avatar:       user.Avatar,
	}
}

// resolveRoomClanContext looks up the clan internally from the room/channel ID.
// A request without a room intentionally uses the generic, non-clan label.
func (g *Gateway) resolveRoomClanContext(roomName string) (string, bool) {
	roomName = strings.TrimSpace(roomName)
	if roomName == "" {
		return "", true
	}

	roomClanID := g.resolver.GetChannelClan(roomName)
	if roomClanID == "" {
		return "", false
	}
	return roomClanID, true
}

func newAccountAPI(client *mezon.MezonClient) *mezon.MezonApi {
	scheme := "http"
	if client.UseSSL {
		scheme = "https"
	}
	baseURL := fmt.Sprintf("%s://%s:%s", scheme, client.Host, client.Port)
	apiClient := mezon.NewMezonApi(client.Token, baseURL, client.Timeout)
	apiClient.AttachSocket(client.Socket())
	return apiClient
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// splitPath is unused in Go 1.22+ (PathValue handles routing) but kept
// for potential fallback if needed.
func splitPath(path string) []string {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	return parts
}
