package workermanager

// dispatchEvent is the actual wire shape BE mezon publishes to trigger
// agent spawn/stop, confirmed 2026-08-19 against BE mezon's own dispatch
// code (dispatchSfuAgentMessage/addAgentDispatch/deleteAgentDispatch) --
// see mezon-sfu-migration-checklist.md D4. Two things this corrects versus
// what was assumed when this package was first written:
//   - It is NOT published on a dedicated subject. BE mezon reuses the exact
//     same NATS subject ("SFU_HOOK_EVENT") that mezon-sfu's own C code
//     publishes its participant hook events on (mezon-sfu/CLAUDE.md section
//     6, payload shape {"user_id","room_id","name","event"}). Subscriber.go
//     tells the two apart by field: this shape carries "action", SFU's own
//     hook events carry "event" instead -- anything without a recognized
//     "action" is silently ignored, not treated as a decode error.
//   - room_id is sent as a JSON *string* (BE mezon builds it with
//     fmt.Sprintf(`"%v"`, channelId), quotes included), not a bare number.
type dispatchEvent struct {
	Action string `json:"action"` // "add" | "delete"
	RoomID string `json:"room_id"`
}

// StartEvent / StopEvent are Manager's own input shapes -- built by
// subscriber.go from dispatchEvent, never unmarshaled from JSON directly
// (that's dispatchEvent's job), hence no json tags here.
//
// There is deliberately no AgentUserID field: the real dispatch event never
// carries one (see dispatchEvent's doc above), so there is nothing for a
// caller to plausibly set it from. Manager.Start uses Config.AgentUserIDBase
// directly instead -- see that field's doc for why one fixed id for every
// room is fine, and why it's still an interim/configured choice, not a
// final one (mezon-sfu-migration-checklist.md D4/B1).
type StartEvent struct {
	RoomID uint64
	// Role: "audience" (default, record-only) or "speaker". Optional --
	// also has no source in the real dispatch event today; kept as a field
	// for whichever caller eventually has one to give it.
	Role string
}

type StopEvent struct {
	RoomID uint64
}
