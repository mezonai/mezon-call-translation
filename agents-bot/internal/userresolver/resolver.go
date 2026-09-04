// Package userresolver maintains an in-memory cache of user_id → profile info,
// populated passively from SDK events (VoiceJoinedEvent, ChannelMessage) rather
// than active REST lookups. The SDK's Users.Fetch only creates DM channels and
// does NOT return username/display_name — so this cache is the primary source
// of truth for username resolution.
//
// Scope: meeting rooms only. Every meeting-room participant is guaranteed a
// cache entry — joining the voice channel fires VoiceJoinedEvent — so a user
// who never chats still resolves (with the coarser voice name). Merge precedence
// when both sources have seen the same user: ChannelMessage fields win,
// VoiceJoinedEvent fills only empty fields (see CacheFromVoiceJoined).
package userresolver

import (
	"sort"
	"sync"
)

// UserInfo holds the displayable profile fields for one Mezon user.
type UserInfo struct {
	UserID      string `json:"user_id"`
	Username    string `json:"username"`
	DisplayName string `json:"display_name"`
	ClanNick    string `json:"clan_nick,omitempty"`
	Avatar      string `json:"avatar,omitempty"`

	// LastVoiceChannelID is the most recent voice channel this user was seen
	// joining (VoiceJoinedEvent.VoiceChannelId, as string). Internal only
	// (json:"-"): used to move roster membership atomically when a user
	// switches channels; not part of the HTTP API contract.
	LastVoiceChannelID string `json:"-"`
}

// DisplayLabel returns the best available display name, falling back to UserID.
func (u *UserInfo) DisplayLabel() string {
	if label := u.KnownDisplayLabel(); label != "" {
		return label
	}
	return u.UserID
}

// KnownDisplayLabel returns the best available display name without falling back to UserID.
// Returns empty string if no actual human name has been seen yet.
func (u *UserInfo) KnownDisplayLabel() string {
	if u.ClanNick != "" {
		return u.ClanNick
	}
	if u.DisplayName != "" {
		return u.DisplayName
	}
	return u.Username
}

// Resolver is a thread-safe in-memory user profile cache.
type Resolver struct {
	mu           sync.RWMutex
	users        map[string]*UserInfo           // keyed by user_id as string
	channelUsers map[string]map[string]struct{} // channel_id  -> set of user_id
}

func New() *Resolver {
	return &Resolver{
		users:        make(map[string]*UserInfo),
		channelUsers: make(map[string]map[string]struct{}),
	}
}

// Get returns the cached UserInfo for the given user ID, or nil if not found.
func (r *Resolver) Get(userID string) *UserInfo {
	r.mu.RLock()
	defer r.mu.RUnlock()
	u := r.users[userID]
	if u == nil {
		return nil
	}
	// Return a copy to avoid data races on reads
	copy := *u
	return &copy
}

// GetBatch returns cached UserInfo for each user ID. IDs not in cache are
// returned in the notFound slice.
func (r *Resolver) GetBatch(userIDs []string) (found []*UserInfo, notFound []string) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	for _, id := range userIDs {
		if u, ok := r.users[id]; ok {
			copy := *u
			found = append(found, &copy)
		} else {
			notFound = append(notFound, id)
		}
	}
	return
}

// CacheFromVoiceJoined stores user info extracted from a VoiceJoinedEvent.
// The Participant field is the single display string the Mezon server
// attaches to the voice event — the only username source for users who never
// chat, since Users.Fetch does not return username.
//
// Fill-if-empty policy: voice data is strictly coarser than ChannelMessage
// data (no clan_nick/avatar, one display string), so a voice (re-)join must
// never clobber a richer entry a prior message populated. Name changes are
// picked up only from chat — accepted trade-off.
//
// NOTE: VoiceJoinedEvent.ClanName is the CLAN's name, not the user's
// clan_nick — deliberately NOT stored (it would win DisplayLabel's
// clan_nick priority and label every member with the clan's name). Logged
// at the call site for context instead.
func (r *Resolver) CacheFromVoiceJoined(userID, participant, voiceChannelID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	u, ok := r.users[userID]
	if !ok {
		u = &UserInfo{UserID: userID}
		r.users[userID] = u
	}
	if voiceChannelID != "" {
		if u.LastVoiceChannelID != "" && u.LastVoiceChannelID != voiceChannelID {
			delete(r.channelUsers[u.LastVoiceChannelID], userID)
			if len(r.channelUsers[u.LastVoiceChannelID]) == 0 {
				delete(r.channelUsers, u.LastVoiceChannelID)
			}
		}

		u.LastVoiceChannelID = voiceChannelID
		if r.channelUsers[voiceChannelID] == nil {
			r.channelUsers[voiceChannelID] = make(map[string]struct{})
		}
		r.channelUsers[voiceChannelID][userID] = struct{}{}
	}
	if participant != "" {
		if u.Username == "" {
			u.Username = participant
		}
		if u.DisplayName == "" {
			u.DisplayName = participant
		}
	}
}

// CacheFromMessage stores user info extracted from a ChannelMessage.
// ChannelMessage carries Username, DisplayName, ClanNick, Avatar directly
// from the Mezon server — richer than voice events, and the only source that
// reflects name changes, so non-empty fields overwrite what voice events
// filled in earlier.
func (r *Resolver) CacheFromMessage(userID, username, displayName, clanNick, avatar string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	u, ok := r.users[userID]
	if !ok {
		u = &UserInfo{UserID: userID}
		r.users[userID] = u
	}
	if username != "" {
		u.Username = username
	}
	if displayName != "" {
		u.DisplayName = displayName
	}
	if clanNick != "" {
		u.ClanNick = clanNick
	}
	if avatar != "" {
		u.Avatar = avatar
	}
}

// Size returns the number of cached users.
func (r *Resolver) Size() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.users)
}

// RemoveFromVoiceChannel removes a user's current membership without deleting
// their cached profile, which remains useful for later display-name lookups.
func (r *Resolver) RemoveFromVoiceChannel(userID, channelID string) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if users, ok := r.channelUsers[channelID]; ok {
		delete(users, userID)
		if len(users) == 0 {
			delete(r.channelUsers, channelID)
		}
	}

	if u, ok := r.users[userID]; ok && u.LastVoiceChannelID == channelID {
		u.LastVoiceChannelID = ""
	}
}

// GetChannelUsers returns stable, copied user records for the current channel
// roster, sorted by UserID. The returned values are safe for callers to mutate.
func (r *Resolver) GetChannelUsers(channelID string) []*UserInfo {
	r.mu.RLock()
	defer r.mu.RUnlock()

	userSet := r.channelUsers[channelID]
	if len(userSet) == 0 {
		return []*UserInfo{}
	}

	result := make([]*UserInfo, 0, len(userSet))
	for uid := range userSet {
		if u, ok := r.users[uid]; ok {
			copyUser := *u
			result = append(result, &copyUser)
		} else {
			result = append(result, &UserInfo{UserID: uid})
		}
	}

	sort.Slice(result, func(i, j int) bool {
		return result[i].UserID < result[j].UserID
	})

	return result
}
