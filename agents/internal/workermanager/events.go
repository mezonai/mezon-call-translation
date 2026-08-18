package workermanager

// StartEvent / StopEvent are the NATS payload shapes. Neither the subject
// names nor this exact schema are finalized with BE mezon yet
// (mezon-sfu-migration-plan.md section 1) -- room_id is the one field
// that's certain. agent_user_id in particular is a placeholder: who issues
// the bot's user_id (BE mezon, or this manager itself picking from a
// reserved range) is still an open question -- see
// mezon-sfu-migration-checklist.md B1. For now Start() requires it to be
// present in the event rather than inventing an id scheme unilaterally.
type StartEvent struct {
	RoomID      uint64 `json:"room_id"`
	AgentUserID int64  `json:"agent_user_id"`
	// Role: "audience" (default, record-only) or "speaker". Optional.
	Role string `json:"role,omitempty"`
}

type StopEvent struct {
	RoomID uint64 `json:"room_id"`
}
