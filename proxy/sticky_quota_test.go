package main

import (
	"context"
	"errors"
	"testing"
)

// Simulates Pulse seat advice that ping-pongs among top-ranked credentials
// unless Go passes skip_credential_ids for quota-rejected IDs.
func TestStickyAdvisorSkipsQuotaRejectedSeats(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	auto50 := 50.0
	apiFull := 100.0
	apiOK := 0.0
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA", AutoPct: &auto50, ApiPct: &apiFull},
		{CredentialID: "c2", APIKey: "keyB", AutoPct: &auto50, ApiPct: &apiFull},
		{CredentialID: "c3", APIKey: "keyC", AutoPct: &auto50, ApiPct: &apiOK},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()

	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	call := 0
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, quotaSkip map[string]bool) (string, []string, bool, error) {
		call++
		if quotaSkip["c1"] && quotaSkip["c2"] {
			return "c3", nil, true, nil
		}
		if cur == "" || cur == "c1" {
			return "c2", nil, true, nil
		}
		return "c1", nil, true, nil
	})

	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err != nil || tok == "" || entry.credentialID != "c3" {
		t.Fatalf("want c3 via skip: err=%v entry=%v", err, entry)
	}
	if call < 2 {
		t.Fatalf("expected multiple advisor calls with quota skip, calls=%d", call)
	}
}

func TestStickyAdvisorQuotaFallbackWhenSeatEmpty(t *testing.T) {
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
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, quotaSkip map[string]bool) (string, []string, bool, error) {
		if len(quotaSkip) == 0 {
			return "c1", nil, true, nil
		}
		return "", nil, true, nil
	})
	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", StickyCredentialID: "c1"}
	entry, tok, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("want local quota fallback c2, err=%v entry=%v", err, entry)
	}
}

func TestStickyAdvisorConcurrencyEmptyStaysExhausted(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
	})
	p.keys[0].setFullyQuotaExhausted()
	sessions := NewSessionMap()
	sticky := NewStickySelect(p, sessions)
	sticky.SetAdvisor(func(binding *SessionBinding, cur string, rel bool, quotaSkip map[string]bool) (string, []string, bool, error) {
		return "", []string{"c1"}, true, nil
	})
	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok", StickyCredentialID: "c1"}
	_, _, err := sticky.Select(context.Background(), "jwt1", &binding, quotaPoolUnknown)
	if !errors.Is(err, errAllExhausted) {
		t.Fatalf("concurrency fail-closed without quota skip, err=%v", err)
	}
}
