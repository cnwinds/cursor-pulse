package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestEnsureTokenReexchangesWhenAPIKeyChanges(t *testing.T) {
	var exchangeKeys []string
	var n int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != exchangePath {
			http.NotFound(w, r)
			return
		}
		auth := r.Header.Get("Authorization")
		exchangeKeys = append(exchangeKeys, auth)
		n++
		_ = json.NewEncoder(w).Encode(map[string]string{
			// Distinct exp so opaqueJWT payloads differ across exchanges.
			"accessToken": opaqueJWT(time.Now().Add(time.Duration(30+n) * time.Minute)),
		})
	}))
	t.Cleanup(srv.Close)

	pool := NewPool([]string{"crsr_old"})
	pool.client = srv.Client()
	pool.exchangeBase = srv.URL

	entry := pool.keys[0]
	tok1, err := entry.ensureToken(context.Background(), pool.client, pool.exchangeBase)
	if err != nil || tok1 == "" {
		t.Fatalf("first ensureToken: tok=%q err=%v", tok1, err)
	}
	if len(exchangeKeys) != 1 {
		t.Fatalf("expected 1 exchange, got %d", len(exchangeKeys))
	}

	// Same apiKey + fresh JWT: should not re-exchange.
	tok2, err := entry.ensureToken(context.Background(), pool.client, pool.exchangeBase)
	if err != nil {
		t.Fatal(err)
	}
	if tok2 != tok1 {
		t.Fatalf("cached jwt changed unexpectedly")
	}
	if len(exchangeKeys) != 1 {
		t.Fatalf("expected cache hit, exchanges=%d", len(exchangeKeys))
	}

	// Simulate loan reassign: underlying Cursor key changed.
	entry.apiKey = "crsr_new"
	tok3, err := entry.ensureToken(context.Background(), pool.client, pool.exchangeBase)
	if err != nil || tok3 == "" {
		t.Fatalf("re-exchange: tok=%q err=%v", tok3, err)
	}
	if len(exchangeKeys) != 2 {
		t.Fatalf("expected re-exchange after apiKey change, got %d", len(exchangeKeys))
	}
	if exchangeKeys[1] != "Bearer crsr_new" {
		t.Fatalf("second exchange auth=%q", exchangeKeys[1])
	}
	if tok3 == tok1 {
		t.Fatal("expected a new JWT after apiKey change")
	}
}
