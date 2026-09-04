// Package gateway is the core of agents-bot: it initializes the mezon-sdk-go
// client, listens for voice/chat events to populate the user cache, manages
// an active-room registry, and serves an HTTP API for agents to resolve
// user profiles and register rooms.
package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	mezon "github.com/quangledang23/mezon-sdk-go"
	"github.com/quangledang23/mezon-sdk-go/rtapi"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/config"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/orchestratorclient"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/userresolver"
)

// RoomInfo holds the active room metadata registered by an agent.
type RoomInfo struct {
	RoomName string `json:"room_name"` // SFU numeric room id as string
	RoomID   string `json:"room_id"`   // orchestrator UUID
}

type orchestratorAPI interface {
	GetActiveRoomID(ctx context.Context, roomName string) (string, error)
	PushChatExternal(ctx context.Context, roomName, roomID, participantIdentity, message, timeStr string) error
}

// Gateway is the main service struct.
type Gateway struct {
	cfg      config.Config
	client   *mezon.MezonClient
	resolver *userresolver.Resolver
	orch     orchestratorAPI

	// activeRooms maps room_name (SFU numeric id as string) → RoomInfo.
	// The agent registers its room here so the gateway knows which
	// channel messages to forward and has the orchestrator UUID.
	registrationMu sync.Mutex // serializes verified register/unregister mutations
	roomsMu        sync.RWMutex
	activeRooms    map[string]*RoomInfo // keyed by room_name

	// channelToRoom maps channel_id (string) → room_name, populated
	// from VoiceJoinedEvent.VoiceChannelId. This is how we match an
	// incoming ChannelMessage to an active meeting room.
	chanMu        sync.RWMutex
	channelToRoom map[string]string // channel_id → room_name
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
		cfg:           cfg,
		client:        client,
		resolver:      userresolver.New(),
		orch:          orchestratorclient.New(cfg.OrchestratorBaseURL, cfg.InternalAPISecret),
		activeRooms:   make(map[string]*RoomInfo),
		channelToRoom: make(map[string]string),
	}

	g.registerEventHandlers()
	return g, nil
}

// Run starts the gateway: logs into Mezon and starts the HTTP server.
// Blocks until ctx is cancelled.
func (g *Gateway) Run(ctx context.Context) error {
	log.Println("agents-bot: logging into Mezon...")
	if err := g.client.Login(); err != nil {
		return fmt.Errorf("gateway: mezon login: %w", err)
	}
	log.Printf("agents-bot: logged in as %s", g.client.ClientID)
	log.Printf("agents-bot: clans cached: %d", g.client.Clans.Size())

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", g.handleHealthz)
	mux.HandleFunc("GET /api/users/{id}", g.handleGetUser)
	mux.HandleFunc("POST /api/users/batch", g.handleBatchUsers)
	mux.HandleFunc("POST /api/rooms/register", g.handleRoomRegister)
	mux.HandleFunc("POST /api/rooms/unregister", g.handleRoomUnregister)
	mux.HandleFunc("GET /api/rooms/{room_name}/participants", g.handleGetRoomParticipants)

	addr := fmt.Sprintf(":%d", g.cfg.GatewayPort)
	srv := &http.Server{Addr: addr, Handler: mux}

	go func() {
		<-ctx.Done()
		log.Println("agents-bot: shutting down HTTP server...")
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutCtx)
	}()

	log.Printf("agents-bot: HTTP server listening on %s", addr)
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

		g.resolver.CacheFromVoiceJoined(userID, ev.Participant, channelID)
		log.Printf("agents-bot: voice_joined user=%s name=%q channel=%s label=%q clan=%q",
			userID, ev.Participant, channelID, ev.VoiceChannelLabel, ev.ClanName)

		// Map this voice channel to a room_name (= channel_id as string,
		// which is also how agents format their SFU ROOM_ID).
		g.chanMu.Lock()
		g.channelToRoom[channelID] = channelID
		g.chanMu.Unlock()
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
		log.Printf("agents-bot: voice_leaved user=%s channel=%s",
			userID, channelID)
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

		// Cache user profile from message fields
		if m.SenderID != "" {
			g.resolver.CacheFromMessage(
				m.SenderID, m.Username, m.DisplayName, m.ClanNick, m.Avatar,
			)
		}

		// Forward chat to orchestrator if this channel belongs to an active room
		g.forwardChatIfActive(m)
	})

	g.client.OnReady(func() {
		log.Printf("agents-bot: SDK ready, client_id=%s, clans=%d, user_cache=%d",
			g.client.ClientID, g.client.Clans.Size(), g.resolver.Size())
	})
}

// forwardChatIfActive checks if the message's channel belongs to an active
// meeting room and, if so, POSTs it to orchestrator's agent_push_chat_external.
func (g *Gateway) forwardChatIfActive(m *mezon.ChannelMessage) {
	// Check if this channel is mapped to a room
	g.chanMu.RLock()
	roomName, mapped := g.channelToRoom[m.ChannelID]
	g.chanMu.RUnlock()
	if !mapped {
		return
	}

	// Check if that room is actively registered by an agent
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
		log.Printf("agents-bot: push_chat_external failed: %v", err)
	} else {
		log.Printf("agents-bot: forwarded chat from %s in room %s", identity, room.RoomName)
	}
}

// ─── HTTP Handlers ──────────────────────────────────────────────────────

func (g *Gateway) handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     "ok",
		"user_cache": g.resolver.Size(),
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
	writeJSON(w, http.StatusOK, user)
}

type batchRequest struct {
	UserIDs []string `json:"user_ids"`
}

type batchResponse struct {
	Users    []*userresolver.UserInfo `json:"users"`
	NotFound []string                 `json:"not_found"`
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
	found, notFound := g.resolver.GetBatch(req.UserIDs)
	if found == nil {
		found = []*userresolver.UserInfo{}
	}
	if notFound == nil {
		notFound = []string{}
	}
	writeJSON(w, http.StatusOK, batchResponse{Users: found, NotFound: notFound})
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
	if g.orch == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "orchestrator unavailable"})
		return
	}

	// Keep verification and the subsequent registry mutation ordered against
	// every other register/unregister request. roomsMu remains free during the
	// network call so chat forwarding can continue reading the current room.
	g.registrationMu.Lock()
	defer g.registrationMu.Unlock()

	activeRoomID, err := g.orch.GetActiveRoomID(r.Context(), req.RoomName)
	if err != nil {
		// Verification failed, so no room ID is safe to forward to. Remove a
		// potentially stale prior mapping instead of leaving chat fail-open.
		g.clearActiveRoomUnless(req.RoomName, "")
		log.Printf("agents-bot: room status lookup failed for room=%s: %v", req.RoomName, err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "failed to verify active room session"})
		return
	}
	if activeRoomID != req.RoomID {
		// Preserve a local mapping only when it already matches the UUID that
		// orchestrator says is current; otherwise discard the stale mapping.
		g.clearActiveRoomUnless(req.RoomName, activeRoomID)
		log.Printf(
			"agents-bot: stale register rejected for room=%s (active=%s, requested=%s)",
			req.RoomName,
			activeRoomID,
			req.RoomID,
		)
		writeJSON(w, http.StatusConflict, map[string]string{"error": "stale_room_session"})
		return
	}

	g.roomsMu.Lock()
	g.activeRooms[req.RoomName] = &RoomInfo{
		RoomName: req.RoomName,
		RoomID:   req.RoomID,
	}
	g.roomsMu.Unlock()

	// Also map this room_name as a channel_id (since ROOM_ID == voice_channel_id)
	g.chanMu.Lock()
	g.channelToRoom[req.RoomName] = req.RoomName
	g.chanMu.Unlock()

	log.Printf("agents-bot: room registered: name=%s id=%s", req.RoomName, req.RoomID)
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "room_name": req.RoomName})
}

// clearActiveRoomUnless removes roomName unless its local mapping matches
// keepRoomID. An empty keepRoomID always clears the mapping (fail closed).
// Callers serialize this helper with registrationMu.
func (g *Gateway) clearActiveRoomUnless(roomName, keepRoomID string) {
	g.roomsMu.Lock()
	defer g.roomsMu.Unlock()
	current, exists := g.activeRooms[roomName]
	if exists && (keepRoomID == "" || current.RoomID != keepRoomID) {
		delete(g.activeRooms, roomName)
	}
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

	g.registrationMu.Lock()
	defer g.registrationMu.Unlock()

	g.roomsMu.Lock()
	current, exists := g.activeRooms[req.RoomName]
	if exists {
		if current.RoomID != req.RoomID {
			g.roomsMu.Unlock()
			log.Printf("agents-bot: stale unregister ignored for room=%s (active=%s, requested=%s)", req.RoomName, current.RoomID, req.RoomID)
			writeJSON(w, http.StatusOK, map[string]string{"status": "ignored_stale_session", "room_name": req.RoomName})
			return
		}
		delete(g.activeRooms, req.RoomName)
	}
	g.roomsMu.Unlock()

	log.Printf("agents-bot: room unregistered: name=%s id=%s", req.RoomName, req.RoomID)
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

	users := g.resolver.GetChannelUsers(roomName)

	participants := make([]roomParticipant, 0, len(users))
	for _, u := range users {
		participants = append(participants, roomParticipant{
			ParticipantIdentity: u.UserID,
			Username:            u.KnownDisplayLabel(),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"room_name":    roomName,
		"participants": participants,
	})
}

// ─── Helpers ────────────────────────────────────────────────────────────

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
