package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"time"
)

// sessionTokenTTL is the exp handed to clients, matching the ~1h lifetime of
// Cursor's API-key tokens. Clients re-exchange their pka_/pk_ after it lapses.
const sessionTokenTTL = time.Hour

// sessionPruneGrace keeps a binding past its exp: clients may keep sending a
// lapsed token until a call fails, and dropping it early would force relogin.
const sessionPruneGrace = 12 * time.Hour

// sessionTokenMinter issues the accessToken returned from exchange. It carries
// the upstream JWT's claims so clients parse it as before, but is signed with a
// per-process secret, so api2.cursor.sh rejects it. The upstream JWT can mint
// Cursor API keys on the lending account and must never reach the borrower.
type sessionTokenMinter struct {
	secret []byte
	now    func() time.Time
}

func newSessionTokenMinter() *sessionTokenMinter {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		panic("session token secret: " + err.Error())
	}
	return &sessionTokenMinter{secret: secret, now: time.Now}
}

// sessionTokensEnabled reads PROXY_OPAQUE_SESSION_TOKEN (default on).
func sessionTokensEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("PROXY_OPAQUE_SESSION_TOKEN"))) {
	case "0", "off", "false", "no":
		return false
	}
	return true
}

// mint returns a unique client token derived from upstream and its expiry.
func (m *sessionTokenMinter) mint(upstream string) (string, time.Time) {
	now := m.now()
	exp := now.Add(sessionTokenTTL).Truncate(time.Second)
	claims := jwtClaims(upstream)
	delete(claims, "apiKeyId")
	claims["exp"] = exp.Unix()
	switch claims["time"].(type) {
	case string:
		claims["time"] = strconv.FormatInt(now.Unix(), 10)
	case json.Number:
		claims["time"] = now.Unix()
	}
	claims["randomness"] = randomHex(16)

	payload, _ := json.Marshal(claims)
	enc := base64.RawURLEncoding
	signing := enc.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`)) + "." + enc.EncodeToString(payload)
	mac := hmac.New(sha256.New, m.secret)
	mac.Write([]byte(signing))
	return signing + "." + enc.EncodeToString(mac.Sum(nil)), exp
}

// jwtClaims decodes a JWT payload without verifying it; non-JWT input yields
// an empty claim set.
func jwtClaims(tok string) map[string]any {
	claims := map[string]any{}
	parts := strings.Split(tok, ".")
	if len(parts) != 3 {
		return claims
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return claims
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var out map[string]any
	if err := dec.Decode(&out); err != nil || out == nil {
		return claims
	}
	return out
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic("session token randomness: " + err.Error())
	}
	return hex.EncodeToString(b)
}

// clientSessionToken maps an upstream JWT to the token handed to the client
// and the binding expiry. With minting disabled the upstream JWT is returned
// unchanged and the binding never expires (legacy behaviour).
func (s *Server) clientSessionToken(upstream string) (string, time.Time) {
	if s.sessionTokens == nil {
		return upstream, time.Time{}
	}
	return s.sessionTokens.mint(upstream)
}
