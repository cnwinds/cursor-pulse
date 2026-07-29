package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestMarkExhaustedAdvancesOnce(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.markExhausted(p.keys[0])
	if !p.keys[0].exhausted || p.cur != 1 {
		t.Fatalf("after first mark: exhausted=%v cur=%d", p.keys[0].exhausted, p.cur)
	}
	p.markExhausted(p.keys[0])
	if p.cur != 1 {
		t.Fatalf("duplicate mark should not advance cur again, got %d", p.cur)
	}
}

func TestNextAvailableAfter(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
		{CredentialID: "c3", APIKey: "k3"},
	})
	p.keys[0].exhausted = true
	next := p.nextAvailableAfter("c1")
	if next == nil || next.credentialID != "c2" {
		t.Fatalf("next after c1: got %v", next)
	}
	p.keys[1].exhausted = true
	next = p.nextAvailableAfter("c1")
	if next == nil || next.credentialID != "c3" {
		t.Fatalf("next after c1 with c2 exhausted: got %v", next)
	}
}

func TestPoolTokenForBindingSticky(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPool([]string{"keyA", "keyB"})
	p.exchangeBase = fu.URL
	p.client = fu.Client()

	sessions := NewSessionMap()
	srv := NewServer(p, nil, nil, sessions)

	binding := SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_ok"}
	entry, tok, err := srv.poolTokenForBinding(context.Background(), "jwt1", &binding)
	if err != nil || tok == "" || entry == nil {
		t.Fatalf("assign: %v", err)
	}
	if binding.StickyCredentialID != entry.credentialID {
		t.Fatalf("sticky=%q cred=%q", binding.StickyCredentialID, entry.credentialID)
	}
	sessions.Bind("jwt1", binding)

	entry2, tok2, err := srv.poolTokenForBinding(context.Background(), "jwt1", &binding)
	if err != nil || tok2 == "" {
		t.Fatalf("reuse sticky: %v", err)
	}
	if entry2.credentialID != entry.credentialID {
		t.Fatalf("expected same credential %s got %s", entry.credentialID, entry2.credentialID)
	}
}

func TestPoolTokenForBindingTransientExchangeKeepsSticky(t *testing.T) {
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
	srvProxy := NewServer(p, nil, nil, sessions)

	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)

	_, _, err := srvProxy.poolTokenForBinding(context.Background(), "jwt1", &binding)
	if err == nil {
		t.Fatal("expected transient exchange error")
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.StickyCredentialID != "c1" {
		t.Fatalf("sticky should remain c1, got %q", b.StickyCredentialID)
	}

	failExchange.Store(false)
	entry, tok, err := srvProxy.poolTokenForBinding(context.Background(), "jwt1", &binding)
	if err != nil || tok == "" || entry.credentialID != "c1" {
		t.Fatalf("retry after transient: err=%v cred=%s", err, entry.credentialID)
	}
}

func TestPoolTokenForBindingExhaustedRotatesSticky(t *testing.T) {
	fu := newFakeUpstreamSession(t)
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "keyA"},
		{CredentialID: "c2", APIKey: "keyB"},
	})
	p.exchangeBase = fu.URL
	p.client = fu.Client()
	p.keys[0].exhausted = true

	sessions := NewSessionMap()
	srv := NewServer(p, nil, nil, sessions)
	binding := SessionBinding{ProxyKeyID: "pk1", StickyCredentialID: "c1"}
	sessions.Bind("jwt1", binding)

	entry, tok, err := srv.poolTokenForBinding(context.Background(), "jwt1", &binding)
	if err != nil || tok == "" || entry.credentialID != "c2" {
		t.Fatalf("want c2: err=%v entry=%v", err, entry)
	}
	b, ok := sessions.Lookup("jwt1")
	if !ok || b.StickyCredentialID != "c2" {
		t.Fatalf("sticky=%q want c2", b.StickyCredentialID)
	}
}
