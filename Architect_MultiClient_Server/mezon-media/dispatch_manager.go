package main

import (
	"encoding/json"
	"net/http"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
)


func createDispatch(w http.ResponseWriter, r *http.Request) {
	// w.Header().Set("Access-Control-Allow-Origin", "*")

	// token := r.Header.Get("Authorization")
	// if token == "" {
	// 	http.Error(w, "missing token", http.StatusUnauthorized)
	// 	return
	// }
	// if len(token) > 7 && token[:7] == "Bearer " {
	// 	token = token[7:]
	// }
	// _, _, _, _, _, ok := parseToken([]byte(encryptedSecret), token)
	// if !ok {
	// 	log.Println("authentication failed")
	// 	http.Error(w, "invalid or expired token", http.StatusUnauthorized)
	// 	return
	// }


	var req struct {
		Room      string `json:"room"`
		AgentName string `json:"agent_name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}


	client := lksdk.NewAgentDispatchServiceClient(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_SECRET_KEY)

	listReq := &livekit.ListAgentDispatchRequest{Room: req.Room}
	listResp, err := client.ListDispatch(r.Context(), listReq)
	if err != nil {
		http.Error(w, "LiveKit server error: "+err.Error(), http.StatusInternalServerError)
		return
	}
	for _, d := range listResp.AgentDispatches {
		if d.AgentName == req.AgentName {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":   "exists",
				"message":  "Dispatch already exists",
				"dispatch": d,
			})
			return
		}
	}


	createReq := &livekit.CreateAgentDispatchRequest{
		AgentName: req.AgentName,
		Room:      req.Room,
	}
	dispatch, err := client.CreateDispatch(r.Context(), createReq)
	if err != nil {
		http.Error(w, "LiveKit server error: "+err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "created",
		"dispatch": dispatch,
	})
}

func cancelDispatch(w http.ResponseWriter, r *http.Request) {
	// w.Header().Set("Access-Control-Allow-Origin", "*")

	// token := r.Header.Get("Authorization")
	// if token == "" {
	// 	http.Error(w, "missing token", http.StatusUnauthorized)
	// 	return
	// }
	// if len(token) > 7 && token[:7] == "Bearer " {
	// 	token = token[7:]
	// }
	// _, _, _, _, _, ok := parseToken([]byte(encryptedSecret), token)
	// if !ok {
	// 	log.Println("authentication failed")
	// 	http.Error(w, "invalid or expired token", http.StatusUnauthorized)
	// 	return
	// }

	var req struct {
		Room      string `json:"room"`
		AgentName string `json:"agent_name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}


	client := lksdk.NewAgentDispatchServiceClient(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_SECRET_KEY)


	listReq := &livekit.ListAgentDispatchRequest{Room: req.Room}
	listResp, err := client.ListDispatch(r.Context(), listReq)
	if err != nil {
		http.Error(w, "LiveKit server error: "+err.Error(), http.StatusInternalServerError)
		return
	}


	var target *livekit.AgentDispatch
	for _, d := range listResp.AgentDispatches {
		if d.AgentName == req.AgentName {
			target = d
			break
		}
	}
	if target == nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":  "not_found",
			"message": "No active dispatch found for agent",
		})
		return
	}


	deleteReq := &livekit.DeleteAgentDispatchRequest{
		DispatchId:   target.Id,
		Room: target.Room,
	}
	_, err = client.DeleteDispatch(r.Context(), deleteReq)
	if err != nil {
		http.Error(w, "Failed to cancel dispatch: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "cancelled",
		"message":  "Dispatch cancelled",
		"dispatch": target,
	})
}
