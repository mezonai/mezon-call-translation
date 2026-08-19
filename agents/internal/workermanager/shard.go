package workermanager

// numShards is a fixed pool size, not tied to any expected room count --
// each shard is just 1 idle goroutine + 1 small buffered channel (a few KB
// total for the whole pool, see the sizing note below), so being generous
// here costs nothing.
//
// Why sharded at all: routing every dispatch event straight to Manager.Start/
// Stop from the single NATS subscription goroutine (subscriber.go) means one
// room's slow Stop (waiting out SIGTERM/SIGKILL) would block every other
// room's start/stop until it's done -- rooms are otherwise fully independent,
// so that's a real, avoidable head-of-line problem, not a hypothetical one.
// Routing every event for the same room_id to the same shard, always,
// serializes each room against itself (preserving order -- a start then a
// stop for the same room can never race or reorder) while different rooms on
// different shards run independently. This also removes the need to make
// Manager.Start's "already running?" check atomic against concurrent calls
// for the same room some other way: only one shard's goroutine ever touches
// a given room_id, so there is no concurrent call to guard against.
//
// Sizing: at ~10 concurrently active rooms today (expected to grow), the
// birthday-paradox chance that at least one pair of rooms lands on the same
// shard is ~50% at numShards=64, ~30% at 128, ~16% at 256 -- and even then,
// a collision only serializes those 2 specific rooms against each other, not
// against the rest. 256 keeps that low while staying cheap enough not to
// think about again until room counts grow by another order of magnitude.
const numShards = 256

// shardQueueSize: buffered so a burst of events doesn't make dispatch (called
// from the NATS callback goroutine, see subscriber.go) block on send under
// normal conditions -- filling this would mean 128+ pending start/stop calls
// queued for rooms that hashed to the same shard, at which point blocking the
// NATS callback is the right behavior (backpressure), not something to design
// around.
const shardQueueSize = 128

// mix64 avalanches the bits of a uint64 (Murmur3's 64-bit finalizer) so
// shardFor's distribution doesn't depend on room_id's own bit pattern --
// room_id is not known to increment sequentially or otherwise have uniform
// low bits, so `room_id % numShards` directly could produce a permanently
// hot shard if BE mezon's id generation happens to cluster in some way.
// Hashing first makes shard assignment independent of whatever that pattern
// turns out to be.
func mix64(x uint64) uint64 {
	x ^= x >> 33
	x *= 0xff51afd7ed558ccd
	x ^= x >> 33
	x *= 0xc4ceb9fe1a85ec53
	x ^= x >> 33
	return x
}

// startShards launches the fixed shard pool. Each shard's goroutine runs for
// the lifetime of the process (workermanager is a single-purpose binary, see
// the package doc) -- there is no teardown path and none is needed.
func (m *Manager) startShards() {
	for i := range m.shards {
		ch := make(chan func(), shardQueueSize)
		m.shards[i] = ch
		go func() {
			for job := range ch {
				job()
			}
		}()
	}
}

// dispatch runs fn serially with every other event for the same roomID
// (same room always maps to the same shard, see numShards's doc), without
// blocking events for rooms that hash to a different shard. Called from the
// NATS callback goroutine (subscriber.go) -- blocks only if that specific
// shard's queue is full (shardQueueSize), which is intentional backpressure.
func (m *Manager) dispatch(roomID uint64, fn func()) {
	m.shards[mix64(roomID)%numShards] <- fn
}
