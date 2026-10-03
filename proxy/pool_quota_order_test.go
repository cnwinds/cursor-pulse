package main

import "testing"

func TestPoolKeysOrderedForUsesPerPoolLists(t *testing.T) {
	p := NewPoolFromCredentials(nil)
	p.ReplaceFromPulseSnapshot(PulsePoolSnapshot{
		Default: []PoolCredential{
			{CredentialID: "a", APIKey: "k-a"},
			{CredentialID: "b", APIKey: "k-b"},
		},
		Auto: []PoolCredential{{CredentialID: "b", APIKey: "k-b"}, {CredentialID: "a", APIKey: "k-a"}},
		API:  []PoolCredential{{CredentialID: "a", APIKey: "k-a"}},
	})

	auto := p.keysOrderedFor(quotaPoolAuto)
	if len(auto) != 2 || auto[0].credentialID != "b" || auto[1].credentialID != "a" {
		t.Fatalf("auto order: %+v", auto)
	}
	api := p.keysOrderedFor(quotaPoolAPI)
	if len(api) != 1 || api[0].credentialID != "a" {
		t.Fatalf("api order: %+v", api)
	}
	if api[0] != p.findEntry("a") {
		t.Fatal("per-pool entries must share keyEntry with the credential set")
	}
}

func TestPoolEmptyPulseOrderDoesNotFallBackToAllKeys(t *testing.T) {
	p := NewPoolFromCredentials(nil)
	p.ReplaceFromPulseSnapshot(PulsePoolSnapshot{
		Default: []PoolCredential{{CredentialID: "a", APIKey: "k-a"}},
		Auto:    []PoolCredential{{CredentialID: "a", APIKey: "k-a"}},
		API:     []PoolCredential{},
	})
	if got := p.keysOrderedFor(quotaPoolAPI); len(got) != 0 {
		t.Fatalf("no account qualifies for api; got %+v", got)
	}
}

func TestPoolWithoutPulseOrdersUsesAllKeys(t *testing.T) {
	p := NewPool([]string{"key-one-1234", "key-two-5678"})
	if got := p.keysOrderedFor(quotaPoolAPI); len(got) != 2 {
		t.Fatalf("local mode should use every key, got %d", len(got))
	}
}
