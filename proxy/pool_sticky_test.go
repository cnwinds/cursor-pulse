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
	next := p.nextAvailableForQuotaWithin("c1", quotaPoolUnknown, nil, map[string]bool{"c2": true})
	if next == nil || next.credentialID != "c3" {
		t.Fatalf("want c3, got %#v", next)
	}
}

func TestNextAvailableAfter(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
		{CredentialID: "c3", APIKey: "k3"},
	})
	p.keys[0].setFullyQuotaExhausted()
	next := p.nextAvailableAfter("c1")
	if next == nil || next.credentialID != "c2" {
		t.Fatalf("next after c1: got %v", next)
	}
	p.keys[1].setFullyQuotaExhausted()
	next = p.nextAvailableAfter("c1")
	if next == nil || next.credentialID != "c3" {
		t.Fatalf("next after c1 with c2 exhausted: got %v", next)
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
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err != nil || tok == "" || entry == nil {
		t.Fatalf("assign: %v", err)
	}
	if binding.StickyCredentialID != entry.credentialID {
		t.Fatalf("sticky=%q cred=%q", binding.StickyCredentialID, entry.credentialID)
	}
	sessions.Bind("jwt1", binding)

	entry2, tok2, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
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

	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)

	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err == nil {
		t.Fatal("expected transient exchange error")
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.StickyCredentialID != "c1" {
		t.Fatalf("sticky should remain c1, got %q", b.StickyCredentialID)
	}

	failExchange.Store(false)
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
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
	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("want c2: err=%v entry=%v", err, entry)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.StickyCredentialID != "c2" {
		t.Fatalf("sticky=%q want c2", b.StickyCredentialID)
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
	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("api pool want c2: err=%v entry=%v", err, entry)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.StickyCredentialID != "c2" {
		t.Fatalf("sticky=%q want c2", b.StickyCredentialID)
	}

	// Auto pool still prefers c2 sticky (was rotated) if c2 has auto quota.
	binding = b
	entry2, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || entry2.credentialID != "c2" {
		t.Fatalf("auto pool should keep sticky c2: err=%v cred=%s", err, entry2.credentialID)
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
	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)

	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)
	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.StickyCredentialID != "c2" {
		t.Fatalf("sticky=%q want c2", binding.StickyCredentialID)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.StickyCredentialID != "c2" {
		t.Fatalf("persisted sticky=%q want c2", b.StickyCredentialID)
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
		ProxyKeyID:         "pk1",
		StickyCredentialID: "c1",
		StickySince:        time.Now(),
	}

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || tok == "" {
		t.Fatalf("dwell hold: err=%v", err)
	}
	if entry.credentialID != "c1" {
		t.Fatalf("dwell should hold c1, got %s", entry.credentialID)
	}
	if binding.StickyCredentialID != "c1" {
		t.Fatalf("sticky should stay c1, got %q", binding.StickyCredentialID)
	}
}

func TestStickyDwellExpiredRotates(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{
		ProxyKeyID:         "pk1",
		StickyCredentialID: "c1",
		StickySince:        time.Now().Add(-31 * time.Minute),
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("dwell expired should rotate to c2: err=%v entry=%v", err, entry)
	}
	if binding.StickySince.IsZero() || time.Since(binding.StickySince) > time.Minute {
		t.Fatalf("rotation should reset StickySince, got %v", binding.StickySince)
	}
}

func TestStickyDwellZeroSinceRotates(t *testing.T) {
	// 存量绑定没有 StickySince：不能因此永久卡在无额度账号上
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("zero StickySince should rotate: err=%v entry=%v", err, entry)
	}
}

func TestStickyDwellDoesNotBlockAuthCooldownRotation(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	binding := SessionBinding{
		ProxyKeyID:         "pk1",
		StickyCredentialID: "c1",
		StickySince:        time.Now(),
	}
	p.markBad(p.keys[0])

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAPI)
	if err != nil || entry.credentialID != "c2" {
		t.Fatalf("auth cooling must rotate inside dwell: err=%v entry=%v", err, entry)
	}
}

func TestStickyDwellNotRefreshedOnReuse(t *testing.T) {
	// 驻留窗口从「上次切换」起算；同一账号续用不能刷新它
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 30*time.Minute)
	since := time.Now().Add(-10 * time.Minute)
	binding := SessionBinding{
		ProxyKeyID:         "pk1",
		StickyCredentialID: "c1",
		StickySince:        since,
	}
	sessions.Bind("jwt1", binding)

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolAuto)
	if err != nil || tok == "" || entry.credentialID != "c1" {
		t.Fatalf("auto pool should reuse c1: err=%v entry=%v", err, entry)
	}
	if !binding.StickySince.Equal(since) {
		t.Fatalf("reuse must not refresh StickySince: %v -> %v", since, binding.StickySince)
	}
}

func TestStickyDwellDisabledWhenZero(t *testing.T) {
	p, sessions := dwellTestPool(t)
	sticky := NewStickySelectWithDwell(p, sessions, 0)
	binding := SessionBinding{
		ProxyKeyID:         "pk1",
		StickyCredentialID: "c1",
		StickySince:        time.Now(),
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

	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err != nil || tok == "" {
		t.Fatalf("select: %v", err)
	}
	if entry.credentialID != "c2" {
		t.Fatalf("must start inside the allowlist, got %s", entry.credentialID)
	}
	if binding.StickyCredentialID != "c2" {
		t.Fatalf("sticky=%q", binding.StickyCredentialID)
	}
}

func TestStickySelectRotatesWithinAllowlist(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	// c1 exhausted; c3 has quota but is NOT a candidate for this loan.
	p.keys[0].setFullyQuotaExhausted()
	binding := SessionBinding{
		Mode:                 "loan_alias",
		StickyCredentialID:   "c1",
		AllowedCredentialIDs: []string{"c1", "c2"},
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
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
		StickyCredentialID:   "c1",
		AllowedCredentialIDs: []string{"c2"},
	}

	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
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
		StickyCredentialID:   "c1",
		AllowedCredentialIDs: []string{"c1"},
	}

	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if !errors.Is(err, errAllExhausted) {
		t.Fatalf("want errAllExhausted, got %v", err)
	}
}

func TestRotateOnExhaustionStaysInsideAllowlist(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		Mode:                 "loan_alias",
		StickyCredentialID:   "c1",
		AllowedCredentialIDs: []string{"c1", "c3"},
	}
	sessions.Bind("jwt1", binding)
	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)

	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.StickyCredentialID != "c3" {
		t.Fatalf("must advance to c3, got %q", binding.StickyCredentialID)
	}
}

func TestRotateOnExhaustionNoCandidateLeft(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	binding := SessionBinding{
		Mode:                 "loan_alias",
		StickyCredentialID:   "c1",
		AllowedCredentialIDs: []string{"c1"},
	}
	sessions.Bind("jwt1", binding)
	p.markQuotaExhausted(p.keys[0], quotaPoolAPI)

	sticky.RotateOnExhaustion("jwt1", &binding, "c1", quotaPoolAPI)
	if binding.StickyCredentialID != "c1" {
		t.Fatalf("must not escape the allowlist, got %q", binding.StickyCredentialID)
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
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool) (string, []string, bool, error) {
		current = cur
		release = rel
		return "c2", []string{"c1"}, true, nil
	})
	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("want c2: err=%v entry=%v", err, entry)
	}
	if current != "c1" || !release {
		t.Fatalf("advisor current=%q release=%v", current, release)
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
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool) (string, []string, bool, error) {
		return "", []string{"c2"}, true, nil
	})
	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", StickyCredentialID: "c1"}
	entry, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if !errors.Is(err, errAllExhausted) || entry != nil {
		t.Fatalf("want exhausted, err=%v entry=%v", err, entry)
	}
	if binding.StickyCredentialID != "c1" {
		t.Fatalf("sticky moved to %q", binding.StickyCredentialID)
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
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool) (string, []string, bool, error) {
		return "", nil, false, errors.New("web down")
	})
	binding := SessionBinding{
		ProxyKeyID:           "pk1",
		PulseKey:             "pk_ok",
		StickyCredentialID:   "c1",
		BlockedCredentialIDs: []string{"c2"},
	}
	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if !errors.Is(err, errAllExhausted) {
		t.Fatalf("blocked account must stay skipped, err=%v", err)
	}
	if binding.StickyCredentialID != "c1" {
		t.Fatalf("sticky=%q", binding.StickyCredentialID)
	}
}

func TestUnscopedBindingStillWalksWholePool(t *testing.T) {
	p, sessions := allowlistPool(t)
	sticky := NewStickySelect(p, sessions)
	// No allowlist → shared-pool behaviour, may pick any credential.
	entry, _, err := sticky.Select(context.Background(), "jwt1", &SessionBinding{}, quotaPoolUnknown)
	if err != nil || entry == nil {
		t.Fatalf("unscoped select: %v", err)
	}
}
