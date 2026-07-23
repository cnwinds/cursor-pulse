package main

import "testing"

func TestSessionMapBindAndLookup(t *testing.T) {
	m := NewSessionMap()
	m.Bind("jwt1", SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_abc"})
	b, ok := m.Lookup("jwt1")
	if !ok || b.ProxyKeyID != "pk1" {
		t.Fatalf("%+v %v", b, ok)
	}
	if _, ok := m.Lookup("missing"); ok {
		t.Fatal("expected miss")
	}
}
