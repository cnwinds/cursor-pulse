package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestMarkApiQuotaExhaustedKeepsAutoPool(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)
	if p.keys[0].apiQuotaExhausted != true || p.keys[0].autoQuotaExhausted {
		t.Fatalf("expected api-only exhaustion")
	}
	if p.cur != 0 {
		t.Fatalf("cursor should not advance until both pools exhausted, cur=%d", p.cur)
	}
	if !p.keys[0].hasQuotaForPool(quotaPoolAuto) {
		t.Fatal("auto pool should still be available on c1")
	}
	if p.keys[0].hasQuotaForPool(quotaPoolAPI) {
		t.Fatal("api pool should be blocked on c1")
	}
}

func TestMarkExhaustedAdvancesOnce(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.markExhausted(p.keys[0])
	if !p.keys[0].quotaFullyExhausted() || p.cur != 1 {
		t.Fatalf("after first mark: fully=%v cur=%d", p.keys[0].quotaFullyExhausted(), p.cur)
	}
	p.markExhausted(p.keys[0])
	if p.cur != 1 {
		t.Fatalf("duplicate mark should not advance cur again, got %d", p.cur)
	}
}

func TestNextAvailableSkipsBlocked(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
		{CredentialID: "c3", APIKey: "k3"},
	})
	next := p.nextAvailableForQuotaWithin("c1", quotaPoolAuto, nil, map[string]bool{"c2": true})
	if next == nil || next.credentialID != "c3" {
		t.Fatalf("want c3, got %#v", next)
	}
}

func TestNextAvailableWalksPoolOrderFromTop(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
		{CredentialID: "c3", APIKey: "k3"},
	})
	next := p.nextAvailableForQuotaWithin("c3", quotaPoolAuto, nil, nil)
	if next == nil || next.credentialID != "c1" {
		t.Fatalf("leaving c3 should restart at the top (c1), got %v", next)
	}
	p.keys[0].setFullyQuotaExhausted()
	next = p.nextAvailableForQuotaWithin("c3", quotaPoolAuto, nil, nil)
	if next == nil || next.credentialID != "c2" {
		t.Fatalf("c1 exhausted, want c2, got %v", next)
	}
}

func TestStickySelectAssignAndReuse(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPool([]string{"keyA", "keyB"})
	p.exchangeBase = fu.URL
	p.client = fu.Client()

	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)

	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok"}
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok == "" || entry == nil {
		t.Fatalf("assign: %v", err)
	}
	if binding.AutoSticky.CredentialID != entry.credentialID || binding.APISticky.CredentialID != "" {
		t.Fatalf("auto=%q api=%q cred=%q", binding.AutoSticky.CredentialID, binding.APISticky.CredentialID, entry.credentialID)
	}
	sessions.Bind("jwt1", binding)

	entry2, tok2, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok2 == "" {
		t.Fatalf("reuse sticky: %v", err)
	}
	if entry2.credentialID != entry.credentialID {
		t.Fatalf("expected same credential %s got %s", entry.credentialID, entry2.credentialID)
	}
}

func TestStickySelectTransientExchangeKeepsSticky(t *testing.T) {
	var failExchange atomic.Bool
	failExchange.Store(true)

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if key == "keyA" && failExchange.Load() {
			w.WriteHeader(http.StatusInternalServerError)
			io.WriteString(w, "upstream blip")
			return
		}
		if key == "keyA" || key == "keyB" {
			json.NewEncoder(w).Encode(map[string]string{
				"accessToken":  "tok" + key[len(key)-1:],
				"refreshToken": "r",
			})
			return
		}
		w.WriteHeader(http.StatusUnauthorized)
	})
	srv := httptest.NewUnstartedServer(mux)
	srv.EnableHTTP2 = true
	srv.StartTLS()
	t.Cleanup(srv.Close)

	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = srv.URL
	p.client = srv.Client()

	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)

	binding := SessionBinding{ProxyKeyID: "pk1", AutoSticky: stickySlot{CredentialID: "c1"}}
	sessions.Bind("jwt1", binding)

	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err == nil {
		t.Fatal("expected transient exchange error")
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.AutoSticky.CredentialID != "c1" {
		t.Fatalf("sticky should remain c1, got %q", b.AutoSticky.CredentialID)
	}

	failExchange.Store(false)
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok == "" || entry.credentialID != "c1" {
		t.Fatalf("retry after transient: err=%v cred=%s", err, entry.credentialID)
	}
}

func TestStickySelectExhaustedRotates(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	p.keys[0].setFullyQuotaExhausted()

	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{ProxyKeyID: "pk1", AutoSticky: stickySlot{CredentialID: "c1"}}
	sessions.Bind("jwt1", binding)

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("want c2: err=%v entry=%v", err, entry)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.AutoSticky.CredentialID != "c2" {
		t.Fatalf("sticky=%q want c2", b.AutoSticky.CredentialID)
	}
}

func TestStickySelectApiSnapshotRotates(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	apiFull := 100.0
	apiOK := 20.0
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA", AutoPct: ptrFloat(10), ApiPct: &apiFull},
		{CredentialID: "c2", APIKey: "keyB", AutoPct: ptrFloat(30), ApiPct: &apiOK},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()

	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		AutoSticky: stickySlot{CredentialID: "c1"},
		APISticky:  stickySlot{CredentialID: "c1"},
	}
	sessions.Bind("jwt1", binding)

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("api pool want c2: err=%v entry=%v", err, entry)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.APISticky.CredentialID != "c2" {
		t.Fatalf("api sticky=%q want c2", b.APISticky.CredentialID)
	}
	if b.AutoSticky.CredentialID != "c1" {
		t.Fatalf("api rotation must not touch the auto slot, got %q", b.AutoSticky.CredentialID)
	}

	binding = b
	entry2, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry2.credentialID != "c1" {
		t.Fatalf("auto slot should keep c1: err=%v cred=%s", err, entry2.credentialID)
	}
}

func TestStickyAPIRequestFillsFromAPIOrderNotAutoSlot(t *testing.T) {
	// auto 槽的账号 api 桶也有余量，但 api 请求必须按 api 表从头选，不能顺用
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials(nil)
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	c1 := PoolCredential{CredentialID: "c1", APIKey: "keyA", AutoPct: ptrFloat(10), ApiPct: ptrFloat(10)}
	c2 := PoolCredential{CredentialID: "c2", APIKey: "keyB", AutoPct: ptrFloat(30), ApiPct: ptrFloat(20)}
	p.ReplaceFromPulseSnapshot(PulsePoolSnapshot{
		Default: []PoolCredential{c1, c2},
		Auto:    []PoolCredential{c2, c1},
		API:     []PoolCredential{c1, c2},
	})
	sessions := NewSessionMap()
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{ProxyKeyID: "pk1"}

	autoEntry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || autoEntry.credentialID != "c2" {
		t.Fatalf("auto should take the auto order head c2: err=%v entry=%v", err, autoEntry)
	}
	apiEntry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || apiEntry.credentialID != "c1" {
		t.Fatalf("api must take the api order head c1, not reuse c2: err=%v entry=%v", err, apiEntry)
	}
	if binding.AutoSticky.CredentialID != "c2" || binding.APISticky.CredentialID != "c1" {
		t.Fatalf("slots: auto=%q api=%q", binding.AutoSticky.CredentialID, binding.APISticky.CredentialID)
	}
	again, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || again.credentialID != "c2" {
		t.Fatalf("switching back to auto keeps the auto slot: err=%v entry=%v", err, again)
	}
}

func TestStickySelectRotateOnExhaustion(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()

	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		AutoSticky: stickySlot{CredentialID: "c1"},
		APISticky:  stickySlot{CredentialID: "c1"},
	}
	sessions.Bind("jwt1", binding)

	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)
	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.APISticky.CredentialID != "c2" {
		t.Fatalf("api sticky=%q want c2", binding.APISticky.CredentialID)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.APISticky.CredentialID != "c2" || b.AutoSticky.CredentialID != "c1" {
		t.Fatalf("persisted auto=%q api=%q", b.AutoSticky.CredentialID, b.APISticky.CredentialID)
	}
}

func TestRotateOnExhaustionIgnoresOtherSlot(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	sticky := NewStickySelect(p, NewSessionMap())
	binding := SessionBinding{AutoSticky: stickySlot{CredentialID: "c1"}, APISticky: stickySlot{CredentialID: "c2"}}

	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)
	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.APISticky.CredentialID != "c2" || binding.AutoSticky.CredentialID != "c1" {
		t.Fatalf("api exhaustion on the auto slot's account must not move anything: %+v", binding)
	}
}

func ptrFloat(v float64) *float64 {
	return &v
}

// --- Switch dwell -----------------------------------------------------------

func dwellTestPool(t *testing.T) (*Pool, *SessionMap) {
	t.Helper()
	fu := newFakeUpstreamSession(t)
	apiFull := 100.0
	apiOK := 20.0
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA", AutoPct: ptrFloat(10), ApiPct: &apiFull},
		{CredentialID: "c2", APIKey: "keyB", AutoPct: ptrFloat(30), ApiPct: &apiOK},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	return p, NewSessionMap()
}

func TestStickyDwellHoldsOnBucketExhaustion(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		APISticky:  stickySlot{CredentialID: "c1", Since: time.Now()},
	}

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || tok == "" {
		t.Fatalf("dwell hold: err=%v", err)
	}
	if entry.credentialID != "c1" {
		t.Fatalf("dwell should hold c1, got %s", entry.credentialID)
	}
	if binding.APISticky.CredentialID != "c1" {
		t.Fatalf("sticky should stay c1, got %q", binding.APISticky.CredentialID)
	}
}

func TestStickyDwellExpiredRotates(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	idle := time.Now().Add(-31 * time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		APISticky:  stickySlot{CredentialID: "c1", Since: idle, LastActive: idle},
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("dwell expired should rotate to c2: err=%v entry=%v", err, entry)
	}
	if binding.APISticky.Since.IsZero() || time.Since(binding.APISticky.Since) > time.Minute {
		t.Fatalf("rotation should reset Since, got %v", binding.APISticky.Since)
	}
}

func TestStickyDwellZeroSinceRotates(t *testing.T) {
	// 存量绑定没有 Since：不能因此永久卡在无额度账号上
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{ProxyKeyID: "pk1", APISticky: stickySlot{CredentialID: "c1"}}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("zero StickySince should rotate: err=%v entry=%v", err, entry)
	}
}

func TestStickyDwellDoesNotBlockAuthCooldownRotation(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		APISticky:  stickySlot{CredentialID: "c1", Since: time.Now()},
	}
	p.markBad(p.keys[0])

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("auth cooling must rotate inside dwell: err=%v entry=%v", err, entry)
	}
}

func TestStickyDwellNotRefreshedOnReuse(t *testing.T) {
	// Since 只在换号时刷新；续用只刷新 LastActive
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	since := time.Now().Add(-10 * time.Minute)
	lastActive := time.Now().Add(-2 * time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		AutoSticky: stickySlot{CredentialID: "c1", Since: since, LastActive: lastActive},
	}
	sessions.Bind("jwt1", binding)

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok == "" || entry.credentialID != "c1" {
		t.Fatalf("auto pool should reuse c1: err=%v entry=%v", err, entry)
	}
	if !binding.AutoSticky.Since.Equal(since) {
		t.Fatalf("reuse must not refresh Since: %v -> %v", since, binding.AutoSticky.Since)
	}
	if !binding.AutoSticky.LastActive.After(lastActive) {
		t.Fatalf("reuse should refresh LastActive")
	}
}

func TestStickyDwellHoldsWhenBoundLongButRecentlyActive(t *testing.T) {
	// 绑定很久但仍在密集聊天：距上次请求间隔短，不应因桶耗尽换号
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		APISticky: stickySlot{
			CredentialID: "c1",
			Since:        time.Now().Add(-2 * time.Hour),
			LastActive:   time.Now().Add(-1 * time.Minute),
		},
	}
	sessions.Bind("jwt1", binding)

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c1" {
		t.Fatalf("recent activity should hold c1 within dwell: err=%v entry=%v", err, entry)
	}
}

func TestStickyFirstAPIRequestFillsEmptySlotIgnoringAutoDwell(t *testing.T) {
	// auto 槽正在驻留期内用 c1；首次 api 请求时 api 槽为空，按 api 表选有余量的 c2
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		AutoSticky: stickySlot{CredentialID: "c1", Since: time.Now(), LastActive: time.Now()},
	}
	sessions.Bind("jwt1", binding)

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("api slot should fill with c2: err=%v entry=%v", err, entry)
	}
	stored, _ := sessions.Lookup("jwt1")
	if stored.APISticky.CredentialID != "c2" || stored.AutoSticky.CredentialID != "c1" {
		t.Fatalf("slots not persisted: auto=%q api=%q", stored.AutoSticky.CredentialID, stored.APISticky.CredentialID)
	}
}

func TestStickyDwellDisabledWhenZero(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 0)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		APISticky:  stickySlot{CredentialID: "c1", Since: time.Now()},
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("dwell=0 should rotate immediately: err=%v entry=%v", err, entry)
	}
}

func TestNewStickySelectDefaultsToNoDwell(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelect(p, sessions)
	if sticky.minDwell != 0 {
		t.Fatalf("legacy constructor must not impose dwell, got %v", sticky.minDwell)
	}
}

func TestResolveStickyMinDwell(t *testing.T) {
	t.Setenv("PROXY_STICKY_MIN_DWELL", "")
	if got := resolveStickyMinDwell(0); got != defaultStickyMinDwell {
		t.Fatalf("default: got %v", got)
	}
	if got := resolveStickyMinDwell(5 * time.Minute); got != 5*time.Minute {
		t.Fatalf("flag wins: got %v", got)
	}
	t.Setenv("PROXY_STICKY_MIN_DWELL", "0")
	if got := resolveStickyMinDwell(0); got != 0 {
		t.Fatalf("env 0 disables: got %v", got)
	}
	t.Setenv("PROXY_STICKY_MIN_DWELL", "off")
	if got := resolveStickyMinDwell(0); got != 0 {
		t.Fatalf("env off disables: got %v", got)
	}
	t.Setenv("PROXY_STICKY_MIN_DWELL", "90")
	if got := resolveStickyMinDwell(0); got != 90*time.Second {
		t.Fatalf("env seconds: got %v", got)
	}
	t.Setenv("PROXY_STICKY_MIN_DWELL", "2m")
	if got := resolveStickyMinDwell(0); got != 2*time.Minute {
		t.Fatalf("env duration: got %v", got)
	}
}

// --- Candidate allowlist (loan_alias roaming) --------------------------------

func allowlistPool(t *testing.T) (*Pool, *SessionMap) {
	t.Helper()
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
		{CredentialID: "c3", APIKey: "keyC"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	return p, NewSessionMap()
}

func TestAllowedSetCollapsesBlanks(t *testing.T) {
	if got := (SessionBinding{}).allowedSet(); got != nil {
		t.Fatalf("empty binding must be unscoped, got %v", got)
	}
	if got := (SessionBinding{AllowedCredentialIDs: []string{"", "  "}}).allowedSet(); got != nil {
		t.Fatalf("blank-only must collapse to nil, got %v", got)
	}
	got := (SessionBinding{AllowedCredentialIDs: []string{"c2", "", "c3"}}).allowedSet()
	if len(got) != 2 || !got["c2"] || !got["c3"] {
		t.Fatalf("got %v", got)
	}
}

func TestStickySelectStartsInsideAllowlist(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		ProxyKeyID:           "loan-1",
		Mode:                 "loan_alias",
		AllowedCredentialIDs: []string{"c2"},
	}

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok == "" {
		t.Fatalf("select: %v", err)
	}
	if entry.credentialID != "c2" {
		t.Fatalf("must start inside the allowlist, got %s", entry.credentialID)
	}
	if binding.AutoSticky.CredentialID != "c2" {
		t.Fatalf("sticky=%q", binding.AutoSticky.CredentialID)
	}
}

func TestStickySelectRotatesWithinAllowlist(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	// c1 exhausted; c3 has quota but is NOT a candidate for this loan.
	p.keys[0].setFullyQuotaExhausted()
	binding := SessionBinding{
		Mode:                 "loan_alias",
		AutoSticky:           stickySlot{CredentialID: "c1"},
		AllowedCredentialIDs: []string{"c1", "c2"},
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("must rotate to c2 (not c3): err=%v entry=%v", err, entry)
	}
}

func TestStickySelectDropsCredentialLeavingAllowlist(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	// c1 still has quota but is no longer a candidate → must not keep serving.
	binding := SessionBinding{
		Mode:                 "loan_alias",
		AutoSticky:           stickySlot{CredentialID: "c1"},
		AllowedCredentialIDs: []string{"c2"},
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("stale sticky outside allowlist must rotate: err=%v entry=%v", err, entry)
	}
}

func TestStickySelectAllowlistExhaustedDoesNotEscape(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	p.keys[0].setFullyQuotaExhausted()
	// Only c1 is a candidate and it is exhausted; c2/c3 must not be used.
	binding := SessionBinding{
		Mode:                 "loan_alias",
		AutoSticky:           stickySlot{CredentialID: "c1"},
		AllowedCredentialIDs: []string{"c1"},
	}

	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if !errors.Is(err, errAllExhausted) {
		t.Fatalf("want errAllExhausted, got %v", err)
	}
}

func TestRotateOnExhaustionStaysInsideAllowlist(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		Mode:                 "loan_alias",
		APISticky:            stickySlot{CredentialID: "c1"},
		AllowedCredentialIDs: []string{"c1", "c3"},
	}
	sessions.Bind("jwt1", binding)
	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)

	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.APISticky.CredentialID != "c3" {
		t.Fatalf("must advance to c3, got %q", binding.APISticky.CredentialID)
	}
}

func TestRotateOnExhaustionNoCandidateLeft(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		Mode:                 "loan_alias",
		APISticky:            stickySlot{CredentialID: "c1"},
		AllowedCredentialIDs: []string{"c1"},
	}
	sessions.Bind("jwt1", binding)
	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)

	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.APISticky.CredentialID != "c1" {
		t.Fatalf("must not escape the allowlist, got %q", binding.APISticky.CredentialID)
	}
}

func TestStickyAdvisorAssignsOnRotation(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	p.keys[0].setFullyQuotaExhausted()
	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	var current string
	var release bool
	askedPool := quotaPoolAuto
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		current = cur
		release = rel
		askedPool = pool
		return "c2", []string{"c1"}, true, nil
	})
	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", APISticky: stickySlot{CredentialID: "c1", LastActive: time.Now()}}
	sessions.Bind("jwt1", binding)
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("want c2: err=%v entry=%v", err, entry)
	}
	if current != "c1" || !release {
		t.Fatalf("advisor current=%q release=%v", current, release)
	}
	if binding.APISticky.CredentialID != "c2" {
		t.Fatalf("api slot=%q", binding.APISticky.CredentialID)
	}
	if askedPool != quotaPoolAPI {
		t.Fatalf("advisor should receive the request pool, got %v", askedPool)
	}
	if len(binding.BlockedCredentialIDs) != 1 || binding.BlockedCredentialIDs[0] != "c1" {
		t.Fatalf("blocked=%v", binding.BlockedCredentialIDs)
	}
}

func TestStickyAdvisorFailClosedDoesNotPickLocal(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	p.keys[0].setFullyQuotaExhausted()
	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		return "", []string{"c2"}, true, nil
	})
	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", AutoSticky: stickySlot{CredentialID: "c1"}}
	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if !errors.Is(err, errAllExhausted) || entry != nil {
		t.Fatalf("want exhausted, err=%v entry=%v", err, entry)
	}
	if binding.AutoSticky.CredentialID != "c1" {
		t.Fatalf("sticky moved to %q", binding.AutoSticky.CredentialID)
	}
}

func TestStickyAdvisorFailOpenSkipsBlocked(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.keys[0].setFullyQuotaExhausted()
	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		return "", nil, false, errors.New("web down")
	})
	binding := SessionBinding{
		ProxyKeyID:           "pk1",
		PulseKey:             "pk_ok",
		AutoSticky:           stickySlot{CredentialID: "c1"},
		BlockedCredentialIDs: []string{"c2"},
	}
	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if !errors.Is(err, errAllExhausted) {
		t.Fatalf("blocked account must stay skipped, err=%v", err)
	}
	if binding.AutoSticky.CredentialID != "c1" {
		t.Fatalf("sticky=%q", binding.AutoSticky.CredentialID)
	}
}

func TestUnscopedBindingStillWalksWholePool(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	// No allowlist → shared-pool behaviour, may pick any credential.
	entry, _, err := sticky.Select(context.Background(), "jwt1", &SessionBinding{}, quotaPoolAuto)
	if err != nil || entry == nil {
		t.Fatalf("unscoped select: %v", err)
	}
}

func TestStickyStaleCopyDoesNotClobberOtherSlot(t *testing.T) {
	// 同一会话并发：auto 请求拿着旧副本（api 槽为空），写回时不能抹掉刚填好的 api 槽
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	sessions.Bind("jwt1", SessionBinding{ProxyKeyID: "pk1", AutoSticky: stickySlot{CredentialID: "c1"}})
	stale, _ := sessions.Lookup("jwt1")
	fresh, _ := sessions.Lookup("jwt1")

	if _, _, err := sticky.Select(context.Background(), "jwt1", &fresh, quotaPoolAPI); err != nil {
		t.Fatalf("api select: %v", err)
	}
	if _, _, err := sticky.Select(context.Background(), "jwt1", &stale, quotaPoolAuto); err != nil {
		t.Fatalf("auto select: %v", err)
	}
	stored, _ := sessions.Lookup("jwt1")
	if stored.APISticky.CredentialID != "c2" || stored.AutoSticky.CredentialID != "c1" {
		t.Fatalf("slots: auto=%q api=%q", stored.AutoSticky.CredentialID, stored.APISticky.CredentialID)
	}
	if stale.APISticky.CredentialID != "c2" {
		t.Fatalf("select should refresh the copy's other slot, got %q", stale.APISticky.CredentialID)
	}
}

// --- Slot activity (in-flight requests, idle seats) --------------------------

func TestSlotActiveCountsInFlightAndRecentUse(t *testing.T) {
	now := time.Now()
	old := now.Add(-slotIdleAfter - time.Second)
	b := SessionBinding{
		AutoSticky: stickySlot{CredentialID: "a", LastActive: now},
		APISticky:  stickySlot{CredentialID: "b", LastActive: old},
	}
	if !b.slotActive(quotaPoolAuto, now) {
		t.Fatal("recently used slot is active")
	}
	if b.slotActive(quotaPoolAPI, now) {
		t.Fatal("slot idle past slotIdleAfter with nothing in flight is not active")
	}
	if got := b.heldOutside(quotaPoolAuto); got != nil {
		t.Fatalf("idle api slot must not be held: %v", got)
	}
	b.inFlight[quotaPoolAPI] = 1
	if !b.slotActive(quotaPoolAPI, now) {
		t.Fatal("a long stream in flight keeps the slot active")
	}
	if got := b.heldOutside(quotaPoolAuto); len(got) != 1 || got[0] != "b" {
		t.Fatalf("in-flight api slot must be held: %v", got)
	}
	if (&SessionBinding{}).slotActive(quotaPoolAuto, now) {
		t.Fatal("empty slot is never active")
	}
}

func TestTrackKeepsSlotActiveAndRefreshesLastActiveAtEnd(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	started := time.Now().Add(-10 * time.Minute)
	sessions.Bind("jwt1", SessionBinding{APISticky: stickySlot{CredentialID: "c2", Since: started, LastActive: started}})

	done := sticky.Track("jwt1", quotaPoolAPI)
	mid, _ := sessions.Lookup("jwt1")
	if mid.inFlight[quotaPoolAPI] != 1 || !mid.slotActive(quotaPoolAPI, time.Now()) {
		t.Fatalf("stream in flight should keep api slot active: inFlight=%v", mid.inFlight)
	}
	done()
	end, _ := sessions.Lookup("jwt1")
	if end.inFlight[quotaPoolAPI] != 0 {
		t.Fatalf("inFlight not released: %v", end.inFlight)
	}
	if !end.APISticky.LastActive.After(started) || time.Since(end.APISticky.LastActive) > time.Minute {
		t.Fatalf("LastActive should move to stream end, got %v", end.APISticky.LastActive)
	}
	if !end.APISticky.Since.Equal(started) {
		t.Fatalf("stream end must not reset Since")
	}
}

func TestSelectDoesNotClobberInFlight(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	sessions.Bind("jwt1", SessionBinding{
		AutoSticky: stickySlot{CredentialID: "c1", LastActive: time.Now()},
		APISticky:  stickySlot{CredentialID: "c2", LastActive: time.Now()},
	})
	done := sticky.Track("jwt1", quotaPoolAPI)
	stale, _ := sessions.Lookup("jwt1")
	stale.inFlight = [2]int{}
	if _, _, err := sticky.Select(context.Background(), "jwt1", &stale, quotaPoolAuto); err != nil {
		t.Fatalf("select: %v", err)
	}
	stored, _ := sessions.Lookup("jwt1")
	if stored.inFlight[quotaPoolAPI] != 1 {
		t.Fatalf("select must not overwrite the in-flight count: %v", stored.inFlight)
	}
	done()
}

func TestIdleSlotReseatsBeforeServing(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	var calls []string
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		calls = append(calls, cur)
		if rel || pool != quotaPoolAuto {
			t.Fatalf("reseat must keep current (release=false) on the slot's pool: rel=%v pool=%v", rel, pool)
		}
		return "c2", []string{"c1"}, true, nil
	})
	idle := time.Now().Add(-slotIdleAfter - time.Minute)
	binding := SessionBinding{
		ProxyKeyID: "pk1",
		PulseKey:   "pk_ok",
		AutoSticky: stickySlot{CredentialID: "c1", Since: idle, LastActive: idle},
	}
	sessions.Bind("jwt1", binding)

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("idle slot whose account filled up should move to c2: err=%v entry=%v", err, entry)
	}
	if len(calls) != 1 || calls[0] != "c1" {
		t.Fatalf("advisor calls=%v", calls)
	}

	// Active again: the next request must not ask Pulse.
	if _, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto); err != nil || len(calls) != 1 {
		t.Fatalf("active slot should not reseat: err=%v calls=%v", err, calls)
	}
}

func TestIdleSlotReseatKeepsAccountWithRoom(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		return cur, nil, true, nil
	})
	idle := time.Now().Add(-slotIdleAfter - time.Minute)
	binding := SessionBinding{PulseKey: "pk_ok", AutoSticky: stickySlot{CredentialID: "c1", Since: idle, LastActive: idle}}
	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry.credentialID != "c1" {
		t.Fatalf("room left on c1, keep it: err=%v entry=%v", err, entry)
	}
	if !binding.AutoSticky.Since.Equal(idle) {
		t.Fatal("keeping the same account must not reset Since")
	}
}

func TestIdleSlotReseatNoSeatFailsClosed(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		return "", []string{"c1", "c2"}, true, nil
	})
	idle := time.Now().Add(-slotIdleAfter - time.Minute)
	binding := SessionBinding{PulseKey: "pk_ok", AutoSticky: stickySlot{CredentialID: "c1", Since: idle, LastActive: idle}}
	if _, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto); !errors.Is(err, errAllExhausted) {
		t.Fatalf("no seat anywhere: want errAllExhausted, got %v", err)
	}
}

func TestDwellHoldsWhileStreamInFlight(t *testing.T) {
	// 长流开始于 25 分钟前仍在跑：并发的新 api 请求不能因驻留过期把槽换走
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 20*time.Minute)
	started := time.Now().Add(-25 * time.Minute)
	sessions.Bind("jwt1", SessionBinding{ProxyKeyID: "pk1", APISticky: stickySlot{CredentialID: "c1", Since: started, LastActive: started}})
	done := sticky.Track("jwt1", quotaPoolAPI)
	defer done()

	binding, _ := sessions.Lookup("jwt1")
	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c1" {
		t.Fatalf("in-flight stream keeps dwell: want c1, err=%v entry=%v", err, entry)
	}
}

func TestIdleReseatReleasesUnusableAssignment(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	var calls []struct {
		cur string
		rel bool
	}
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, pool quotaPoolKind) (string, []string, bool, error) {
		calls = append(calls, struct {
			cur string
			rel bool
		}{cur, rel})
		if len(calls) == 1 {
			return "c3", nil, true, nil
		}
		return "c2", nil, true, nil
	})
	idle := time.Now().Add(-slotIdleAfter - time.Minute)
	binding := SessionBinding{
		Mode:                 "loan_alias",
		PulseKey:             "pka_x",
		AutoSticky:           stickySlot{CredentialID: "c1", Since: idle, LastActive: idle},
		AllowedCredentialIDs: []string{"c1", "c2"},
	}
	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("want c2 after releasing out-of-allowlist c3: err=%v entry=%v", err, entry)
	}
	if len(calls) != 2 || calls[1].cur != "c3" || !calls[1].rel {
		t.Fatalf("the unusable seat must be released: %+v", calls)
	}
}
