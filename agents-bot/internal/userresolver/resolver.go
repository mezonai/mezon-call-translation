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
	UserID      string            `json:"user_id"`
	Username    string            `json:"username"`
	DisplayName string            `json:"display_name"`
	Avatar      string            `json:"avatar,omitempty"`
	clanNicks   map[string]string // clan_id -> clan_nick; internal only
}

// DisplayLabel returns the best available display name for a clan context,
// falling back to UserID. An empty clanID deliberately bypasses clan nicknames.
func (u *UserInfo) DisplayLabel(clanID string) string {
	if label := u.KnownDisplayLabel(clanID); label != "" {
		return label
	}
	return u.UserID
}

// KnownDisplayLabel returns the best human-readable name for a clan context
// without falling back to UserID. An empty clanID never uses a clan nickname.
func (u *UserInfo) KnownDisplayLabel(clanID string) string {
	if nick := u.ClanNick(clanID); nick != "" {
		return nick
	}
	if u.DisplayName != "" {
		return u.DisplayName
	}
	return u.Username
}

// ClanNick returns the nickname for exactly one clan. It never falls back to
// another clan's nickname when the context is empty or unknown.
func (u *UserInfo) ClanNick(clanID string) string {
	if clanID == "" || u.clanNicks == nil {
		return ""
	}
	return u.clanNicks[clanID]
}

// Resolver is a thread-safe in-memory user profile cache.
type Resolver struct {
	mu           sync.RWMutex
	users        map[string]*UserInfo           // keyed by user_id as string
	channelUsers map[string]map[string]struct{} // channel_id  -> set of user_id
	channelClans map[string]string              // channel_id -> clan_id
}

func New() *Resolver {
	return &Resolver{
		users:        make(map[string]*UserInfo),
		channelUsers: make(map[string]map[string]struct{}),
		channelClans: make(map[string]string),
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
	return cloneUserInfo(u)
}

// GetBatch returns cached UserInfo for each user ID. IDs not in cache are
// returned in the notFound slice.
func (r *Resolver) GetBatch(userIDs []string) (found []*UserInfo, notFound []string) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	for _, id := range userIDs {
		if u, ok := r.users[id]; ok {
			found = append(found, cloneUserInfo(u))
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
func (r *Resolver) CacheFromVoiceJoined(userID, participant, voiceChannelID, clanID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if voiceChannelID != "" && clanID != "" && clanID != "0" {
		r.channelClans[voiceChannelID] = clanID
	}
	u, ok := r.users[userID]
	if !ok {
		u = &UserInfo{UserID: userID}
		r.users[userID] = u
	}
	if voiceChannelID != "" {
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
// from the Mezon server — richer than voice events and the only source that
// reflects name changes. An empty nickname clears only that clan's entry.
func (r *Resolver) CacheFromMessage(userID, username, displayName, clanID, clanNick, avatar string) {
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
	if clanID != "" && clanID != "0" {
		if u.clanNicks == nil {
			u.clanNicks = make(map[string]string)
		}
		if clanNick != "" {
			u.clanNicks[clanID] = clanNick
		} else {
			delete(u.clanNicks, clanID)
		}
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

// GetChannelClan returns the clan containing a voice channel, or an empty
// string when no voice event has established the relationship yet.
func (r *Resolver) GetChannelClan(channelID string) string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.channelClans[channelID]
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
			result = append(result, cloneUserInfo(u))
		} else {
			result = append(result, &UserInfo{UserID: uid})
		}
	}

	sort.Slice(result, func(i, j int) bool {
		return result[i].UserID < result[j].UserID
	})

	return result
}

// cloneUserInfo must be called while the resolver lock is held. It deep-copies
// the nickname map so callers can safely read the result after the lock is released.
func cloneUserInfo(u *UserInfo) *UserInfo {
	if u == nil {
		return nil
	}
	copyUser := *u
	if u.clanNicks != nil {
		copyUser.clanNicks = make(map[string]string, len(u.clanNicks))
		for clanID, nick := range u.clanNicks {
			copyUser.clanNicks[clanID] = nick
		}
	}
	return &copyUser
}
