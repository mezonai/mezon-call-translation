// Package sfuauth signs the HS256 JWT mezon-sfu requires on `join`.
//
// Claim shape mirrors mezon-sfu's sfu_jwt_claims_t (mezon-sfu/src/protocol/signaling/handshake.h):
// identity/sub -> user_id, roomJoin (must be true), room (room_id, sole source
// of truth as of the 2026-08-16 protocol update -- the `join` message no
// longer carries a `room` field). See mezon-sfu/CLAUDE.md section 5.
package sfuauth

import (
	"fmt"
	"strconv"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type joinClaims struct {
	Identity string `json:"identity"`
	RoomJoin bool   `json:"roomJoin"`
	Room     uint64 `json:"room"`
	jwt.RegisteredClaims
}

// SignJoinToken produces the HS256 token to send as `join.token`.
func SignJoinToken(secret string, agentUserID int64, roomID uint64, ttl time.Duration) (string, error) {
	now := time.Now()
	claims := joinClaims{
		Identity: strconv.FormatInt(agentUserID, 10),
		RoomJoin: true,
		Room:     roomID,
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(now.Add(ttl)),
			NotBefore: jwt.NewNumericDate(now.Add(-1 * time.Minute)),
			IssuedAt:  jwt.NewNumericDate(now),
		},
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString([]byte(secret))
	if err != nil {
		return "", fmt.Errorf("sfuauth: sign join token: %w", err)
	}
	return signed, nil
}
