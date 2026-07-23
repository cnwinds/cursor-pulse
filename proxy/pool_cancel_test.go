package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestTokenCanceledParentDoesNotMarkBad(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != exchangePath {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  opaqueJWT(time.Now().Add(30 * time.Minute)),
			"refreshToken": "r",
		})
	}))
	t.Cleanup(srv.Close)

	pool := NewPool([]string{"keyA", "keyB", "keyC"})
	pool.exchangeBase = srv.URL
	pool.client = srv.Client()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // parent already dead — classic agent disconnect

	_, _, err := pool.token(ctx)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context.Canceled, got %v", err)
	}
	if hits.Load() != 0 {
		t.Fatalf("canceled parent must not hit upstream; hits=%d", hits.Load())
	}
	for _, e := range pool.keys {
		if e.exhausted {
			t.Fatalf("key %s marked exhausted after canceled parent", e.masked())
		}
	}
}

func TestTokenDetachedExchangeIgnoresLiveParentCancelMidFlight(t *testing.T) {
	started := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		time.Sleep(50 * time.Millisecond)
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  opaqueJWT(time.Now().Add(30 * time.Minute)),
			"refreshToken": "r",
		})
	}))
	t.Cleanup(srv.Close)

	pool := NewPool([]string{"keyA", "keyB"})
	pool.exchangeBase = srv.URL
	pool.client = srv.Client()

	ctx, cancel := context.WithCancel(context.Background())
	errCh := make(chan error, 1)
	go func() {
		_, _, err := pool.token(ctx)
		errCh <- err
	}()

	<-started
	cancel() // cancel while upstream exchange in flight

	select {
	case err := <-errCh:
		if err != nil {
			t.Fatalf("detached exchange should succeed despite parent cancel: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout")
	}
	for _, e := range pool.keys {
		if e.exhausted {
			t.Fatalf("key %s should not be marked bad", e.masked())
		}
	}
}

func TestTokenAuthFailureMarksBadButNetworkDoesNot(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		switch key {
		case "badKey":
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"error":"no"}`))
		case "goodKey":
			_ = json.NewEncoder(w).Encode(map[string]string{
				"accessToken":  opaqueJWT(time.Now().Add(30 * time.Minute)),
				"refreshToken": "r",
			})
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	}))
	t.Cleanup(srv.Close)

	pool := NewPool([]string{"badKey", "goodKey"})
	pool.exchangeBase = srv.URL
	pool.client = srv.Client()

	entry, _, err := pool.token(context.Background())
	if err != nil {
		t.Fatalf("token: %v", err)
	}
	if entry.apiKey != "goodKey" {
		t.Fatalf("want goodKey, got %s", entry.apiKey)
	}
	if !pool.keys[0].unavailable() {
		t.Fatal("badKey should be marked bad after 401")
	}
	if pool.keys[0].exhausted {
		t.Fatal("badKey auth failure must use cooldown, not quota exhausted")
	}
	if pool.keys[1].unavailable() {
		t.Fatal("goodKey must not be unavailable")
	}
}

func opaqueJWT(exp time.Time) string {
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none"}`))
	payload, _ := json.Marshal(map[string]any{"exp": exp.Unix()})
	return header + "." + base64.RawURLEncoding.EncodeToString(payload) + ".x"
}
