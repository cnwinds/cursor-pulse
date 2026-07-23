package main

import (
	"testing"
	"time"
)

func TestPoolReplaceFromPulseKeepsExhaustion(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.keys[0].exhausted = true
	p.keys[0].jwt = "oldjwt"
	p.ReplaceFromPulse([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1-new"},
		{CredentialID: "c3", APIKey: "k3"},
	})
	if len(p.keys) != 2 {
		t.Fatalf("len=%d", len(p.keys))
	}
	if p.keys[0].credentialID != "c1" || !p.keys[0].exhausted || p.keys[0].jwt != "oldjwt" || p.keys[0].apiKey != "k1-new" {
		t.Fatalf("c1 not preserved: %+v", *p.keys[0])
	}
	if p.keys[1].credentialID != "c3" {
		t.Fatalf("expected c3, got %s", p.keys[1].credentialID)
	}
}

func TestPoolReplaceFromPulseClearsAuthBad(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.keys[0].badUntil = time.Now().Add(time.Hour)
	p.keys[0].jwt = "stale"
	p.ReplaceFromPulse([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1-new"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	if !p.keys[0].badUntil.IsZero() {
		t.Fatalf("auth-bad cooldown should clear on hot-update, got %v", p.keys[0].badUntil)
	}
	if p.keys[0].unavailable() {
		t.Fatal("c1 should be usable after hot-update cleared badUntil")
	}
}

func TestMarkBadCooldownExpires(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
	})
	p.markBad(p.keys[0])
	if !p.keys[0].unavailable() {
		t.Fatal("expected unavailable during cooldown")
	}
	p.keys[0].badUntil = time.Now().Add(-time.Second)
	if p.keys[0].unavailable() {
		t.Fatal("expected available after cooldown expired")
	}
}

func TestPoolReplaceFromPulseResetsCursor(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "a", APIKey: "keyA"},
		{CredentialID: "b", APIKey: "keyB"},
		{CredentialID: "c", APIKey: "keyC"},
	})
	p.cur = 2
	p.ReplaceFromPulse([]PoolCredential{
		{CredentialID: "b", APIKey: "keyB"},
		{CredentialID: "a", APIKey: "keyA"},
	})
	if p.cur != 0 {
		t.Fatalf("cur after replace: got %d want 0", p.cur)
	}
	if p.keys[0].credentialID != "b" {
		t.Fatalf("order[0]=%s want b (Pulse recommend order)", p.keys[0].credentialID)
	}
}
