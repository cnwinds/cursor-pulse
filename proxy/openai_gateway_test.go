package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestIsOpenAICompatPath(t *testing.T) {
	t.Parallel()
	cases := []struct {
		method string
		path   string
		want   bool
	}{
		{http.MethodGet, "/openai/v1/models", true},
		{http.MethodPost, "/openai/v1/chat/completions", true},
		{http.MethodConnect, "/openai/v1/models", false},
		{http.MethodGet, "/health", false},
	}
	for _, tc := range cases {
		req := httptest.NewRequest(tc.method, tc.path, nil)
		if got := isOpenAICompatPath(req); got != tc.want {
			t.Fatalf("%s %s: got %v want %v", tc.method, tc.path, got, tc.want)
		}
	}
}

func TestOpenAIModelsEndpoint(t *testing.T) {
	t.Parallel()
	s := &Server{pulse: &PulseClient{}}
	req := httptest.NewRequest(http.MethodGet, "/openai/v1/models", nil)
	rr := httptest.NewRecorder()
	s.handleOpenAICompat(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d", rr.Code)
	}
	body, _ := io.ReadAll(rr.Body)
	if len(body) < 20 {
		t.Fatalf("short body: %q", body)
	}
}

func TestOpenAIGatewayEndsCallOnFinalCredential(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") == "Bearer sk-busy" {
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"model":"glm-5","choices":[]}`))
	}))
	defer upstream.Close()

	var resolves atomic.Int32
	ended := make(chan map[string]any, 4)
	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/openai-proxy/resolve":
			cred, key := "cred-busy", "sk-busy"
			if resolves.Add(1) > 1 {
				cred, key = "cred-ok", "sk-ok"
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status":            "ok",
				"proxy_key_id":      "pkcp1",
				"credential_id":     cred,
				"api_key":           key,
				"upstream_chat_url": upstream.URL + "/chat/completions",
			})
		case "/api/internal/v1/openai-proxy/end":
			var body map[string]any
			_ = json.NewDecoder(r.Body).Decode(&body)
			ended <- body
		}
	}))
	defer pulse.Close()

	s := &Server{pulse: NewPulseClient(pulse.URL, "tok", 0), transport: newOutboundTransport(nil)}
	req := httptest.NewRequest(http.MethodPost, "/openai/v1/chat/completions", bytes.NewReader([]byte(`{"model":"glm-5"}`)))
	req.Header.Set("Authorization", "Bearer pkcp_testkey")
	rr := httptest.NewRecorder()
	s.handleOpenAICompat(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}

	select {
	case body := <-ended:
		if body["proxy_key_id"] != "pkcp1" || body["credential_id"] != "cred-ok" {
			t.Fatalf("end body=%v", body)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("end call not sent")
	}
	select {
	case extra := <-ended:
		t.Fatalf("unexpected second end call: %v", extra)
	case <-time.After(100 * time.Millisecond):
	}
}

func TestPulseClientStampsStableBootID(t *testing.T) {
	t.Parallel()
	boots := make(chan string, 4)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		boots <- r.Header.Get("X-Proxy-Boot")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	a := NewPulseClient(srv.URL, "tok", 0)
	a.EndOpenAI("k", "c")
	a.EndOpenAI("k", "c")
	NewPulseClient(srv.URL, "tok", 0).EndOpenAI("k", "c")
	first, second, other := <-boots, <-boots, <-boots
	if first == "" || first != second {
		t.Fatalf("boot id not stable within a process: %q %q", first, second)
	}
	if other == first {
		t.Fatalf("new process reused boot id %q", other)
	}
}

func TestOpenAIChatBodyTooLarge(t *testing.T) {
	t.Setenv("PROXY_MAX_BODY", "64")
	s := &Server{pulse: &PulseClient{}}
	body := bytes.Repeat([]byte("x"), 128)
	req := httptest.NewRequest(http.MethodPost, "/openai/v1/chat/completions", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer pkcp_test")
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	s.handleOpenAICompat(rr, req)
	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status %d body %s", rr.Code, rr.Body.String())
	}
}
