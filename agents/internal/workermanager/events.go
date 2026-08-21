package workermanager

// dispatchEvent is the actual wire shape BE mezon publishes to trigger
// agent spawn/stop, confirmed 2026-08-19 against BE mezon's own dispatch
// code (dispatchSfuAgentMessage/addAgentDispatch/deleteAgentDispatch) --
// see mezon-sfu-migration-checklist.md D4. Two things this corrects versus
// what was assumed when this package was first written:
//   - It is NOT published on a dedicated subject. BE mezon hardcodes the
//     NATS subject on its own side, independent of mezon-sfu's config, as
//     the *value* of a Go constant it names `SFU_HOOK_EVENT` -- but that
//     value is the string "mezon_sfu_hook_event", not the literal text
//     "SFU_HOOK_EVENT" (a naming-vs-value mixup caught 2026-08-20; see
//     Config.Subject's doc in config.go for the real BE mezon source and
//     the fix). This happens to match mezon-sfu's own default hook-event
//     subject too (mezon-sfu commit 88984d6, 2026-08-20), so it's still one
//     shared subject for both -- just under the corrected string.
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
