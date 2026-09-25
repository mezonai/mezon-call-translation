// Package gateway is the core of agents-bot: it initializes the mezon-sdk-go
// client, forwards chat messages from active rooms to orchestrator, manages
// the active-room registry, and serves room-registration, health, and bot-profile
// HTTP endpoints.
package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	mezon "github.com/quangledang23/mezon-sdk-go"
	mezonapi "github.com/quangledang23/mezon-sdk-go/api"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/config"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/orchestratorclient"
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
	mux.HandleFunc("POST /api/rooms/register", g.handleRoomRegister)
	mux.HandleFunc("POST /api/rooms/unregister", g.handleRoomUnregister)

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
	// ChannelMessage: forward to orchestrator if room is active
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

		g.forwardChatIfActive(m)
	})

	g.client.OnReady(func() {
		logging.L.Info(
			"agents-bot: SDK ready",
			"client_id", g.client.ClientID,
			"clans", g.client.Clans.Size(),
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

	// Forward the stable numeric identity used by the agent's STT pipeline.
	// Participant username persistence is handled separately from SFU metadata.
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
		"status": "ok",
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

// ─── Helpers ────────────────────────────────────────────────────────────

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
