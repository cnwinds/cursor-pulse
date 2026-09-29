package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func assertMintedSessionToken(t *testing.T, tok, upstream string) {
	t.Helper()
	if tok == "" || tok == upstream {
		t.Fatalf("client token %q must be proxy-minted, not the upstream JWT %q", tok, upstream)
	}
	if len(strings.Split(tok, ".")) != 3 {
		t.Fatalf("client token %q is not a JWT", tok)
	}
}

func makeTestJWT(t *testing.T, claims map[string]any) string {
	t.Helper()
	payload, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	enc := base64.RawURLEncoding
	return enc.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`)) + "." +
		enc.EncodeToString(payload) + ".upstream-signature"
}

func TestMintSessionTokenMirrorsClaimsWithoutAPIKeyID(t *testing.T) {
	upstream := makeTestJWT(t, map[string]any{
		"sub":        "auth0|user_lender",
		"time":       "1700000000",
		"randomness": "aaaa-bbbb",
		"exp":        1700003600,
		"iss":        "https://authentication.cursor.sh",
		"type":       "api_key_token",
		"apiKeyId":   363796,
	})
	now := time.Unix(1800000000, 0)
	m := newSessionTokenMinter()
	m.now = func() time.Time { return now }

	tok, exp := m.mint(upstream)
	assertMintedSessionToken(t, tok, upstream)
	if !exp.Equal(now.Add(sessionTokenTTL)) {
		t.Fatalf("exp=%v want %v", exp, now.Add(sessionTokenTTL))
	}
	if strings.HasSuffix(tok, ".upstream-signature") {
		t.Fatal("minted token must not reuse the upstream signature")
	}

	claims := jwtClaims(tok)
	if _, ok := claims["apiKeyId"]; ok {
		t.Fatalf("apiKeyId leaked: %v", claims)
	}
	if claims["sub"] != "auth0|user_lender" || claims["type"] != "api_key_token" {
		t.Fatalf("claims not mirrored: %v", claims)
	}
	if claims["time"] != "1800000000" {
		t.Fatalf("time=%v want string of now", claims["time"])
	}
	if claims["randomness"] == "aaaa-bbbb" {
		t.Fatal("randomness must be regenerated")
	}
	if got, err := jwtExpiry(tok); err != nil || !got.Equal(exp) {
		t.Fatalf("jwtExpiry=%v err=%v want %v", got, err, exp)
	}

	again, _ := m.mint(upstream)
	if again == tok {
		t.Fatal("two mints from the same upstream JWT must differ")
	}
}

func TestMintSessionTokenFromOpaqueUpstream(t *testing.T) {
	tok, exp := newSessionTokenMinter().mint("tokA")
	assertMintedSessionToken(t, tok, "tokA")
	if got, err := jwtExpiry(tok); err != nil || !got.Equal(exp) {
		t.Fatalf("jwtExpiry=%v err=%v want %v", got, err, exp)
	}
}

func TestSessionMapPruneDropsOnlyLongExpired(t *testing.T) {
	now := time.Now()
	m := NewSessionMap()
	m.Bind("stale", SessionBinding{ExpiresAt: now.Add(-sessionPruneGrace - time.Minute)})
	m.Bind("lapsed", SessionBinding{ExpiresAt: now.Add(-time.Minute)})
	m.Bind("legacy", SessionBinding{})

	if n := m.Prune(now); n != 1 {
		t.Fatalf("pruned %d want 1", n)
	}
	if _, ok := m.Lookup("stale"); ok {
		t.Fatal("stale binding should be pruned")
	}
	for _, k := range []string{"lapsed", "legacy"} {
		if _, ok := m.Lookup(k); !ok {
			t.Fatalf("%s binding should be kept", k)
		}
	}
}

// TestExchangeHidesUpstreamJWT covers the leak fixed by minted tokens: two
// pk_ holders on the same pool credential get distinct client tokens, and the
// proxy swaps in the upstream JWT for business and /auth/* calls.
func TestExchangeHidesUpstreamJWT(t *testing.T) {
	upstreamJWT := makeTestJWT(t, map[string]any{"sub": "auth0|pool", "exp": time.Now().Add(time.Hour).Unix(), "apiKeyId": 1})
	var mu sync.Mutex
	seen := map[string]string{}
	record := func(path string, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		seen[path] = r.Header.Get("Authorization")
	}

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"accessToken": upstreamJWT, "refreshToken": "r"})
	})
	mux.HandleFunc("/aiserver.v1.TestService/Unary", func(w http.ResponseWriter, r *http.Request) {
		record("unary", r)
		w.Header().Set("Content-Type", "application/proto")
		w.Write([]byte{0x01})
	})
	mux.HandleFunc("/auth/full_stripe_profile", func(w http.ResponseWriter, r *http.Request) {
		record("auth:"+r.URL.Query().Get("c"), r)
		w.Write([]byte(`{}`))
	})
	srv := httptest.NewUnstartedServer(mux)
	srv.EnableHTTP2 = true
	srv.StartTLS()
	t.Cleanup(srv.Close)
	fu := &fakeUpstream{Server: srv}

	proxyAddr, caPEM, sessions := newPulseTestProxy(t, fu, newFakePulse(t).URL)
	client := connectClient(t, proxyAddr, caPEM)
	base := "https://" + strings.TrimPrefix(fu.URL, "https://")

	do := func(method, path, bearer string, body []byte) *http.Response {
		t.Helper()
		req, err := http.NewRequest(method, base+path, bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+bearer)
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}
	exchange := func(pulseKey string) string {
		t.Helper()
		resp := do(http.MethodPost, exchangePath, pulseKey, []byte("{}"))
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			b, _ := io.ReadAll(resp.Body)
			t.Fatalf("%s exchange: status %d body %s", pulseKey, resp.StatusCode, b)
		}
		var out struct {
			AccessToken string `json:"accessToken"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
			t.Fatal(err)
		}
		assertMintedSessionToken(t, out.AccessToken, upstreamJWT)
		return out.AccessToken
	}

	tok1 := exchange("pk_ok")
	tok2 := exchange("pk_ok2")
	if tok1 == tok2 {
		t.Fatal("holders sharing a pool credential must get distinct tokens")
	}
	b1, ok1 := sessions.Lookup(tok1)
	b2, ok2 := sessions.Lookup(tok2)
	if !ok1 || !ok2 || b1.ProxyKeyID != "pk1" || b2.ProxyKeyID != "pk2" {
		t.Fatalf("bindings: %v %+v / %v %+v", ok1, b1, ok2, b2)
	}
	if b1.StickyCredentialID != b2.StickyCredentialID {
		t.Fatalf("no collision rotation expected: %q vs %q", b1.StickyCredentialID, b2.StickyCredentialID)
	}
	if b1.ExpiresAt.IsZero() {
		t.Fatal("minted binding must carry ExpiresAt")
	}

	for _, tc := range []struct{ path, bearer, key, want string }{
		{"/aiserver.v1.TestService/Unary", tok1, "unary", "Bearer " + upstreamJWT},
		{"/auth/full_stripe_profile?c=bound", tok1, "auth:bound", "Bearer " + upstreamJWT},
		{"/auth/full_stripe_profile?c=unbound", "someone-elses-token", "auth:unbound", "Bearer someone-elses-token"},
	} {
		resp := do(http.MethodPost, tc.path, tc.bearer, []byte{0x0A})
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s: status %d", tc.path, resp.StatusCode)
		}
		mu.Lock()
		got := seen[tc.key]
		mu.Unlock()
		if got != tc.want {
			t.Fatalf("%s: upstream Authorization=%q want %q", tc.path, got, tc.want)
		}
	}
}
