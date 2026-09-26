package main

import "testing"

func TestPoolKeysOrderedForUsesPerPoolLists(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "def", APIKey: "k-def"},
	})
	p.keysAuto = []*keyEntry{{credentialID: "auto1", apiKey: "k-a1"}}
	p.keysAPI = []*keyEntry{{credentialID: "api1", apiKey: "k-p1"}}

	auto := p.keysOrderedFor(quotaPoolAuto)
	if len(auto) != 1 || auto[0].credentialID != "auto1" {
		t.Fatalf("auto order: %+v", auto)
	}
	api := p.keysOrderedFor(quotaPoolAPI)
	if len(api) != 1 || api[0].credentialID != "api1" {
		t.Fatalf("api order: %+v", api)
	}
	unknown := p.keysOrderedFor(quotaPoolUnknown)
	if len(unknown) != 1 || unknown[0].credentialID != "def" {
		t.Fatalf("default order: %+v", unknown)
	}
}
