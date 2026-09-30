package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

func TestEnsureOpenAIStreamUsageInRequest(t *testing.T) {
	t.Parallel()
	in := []byte(`{"model":"m","stream":true}`)
	out := ensureOpenAIStreamUsageInRequest(in)
	var payload map[string]any
	if err := json.Unmarshal(out, &payload); err != nil {
		t.Fatal(err)
	}
	so, _ := payload["stream_options"].(map[string]any)
	if so == nil || so["include_usage"] != true {
		t.Fatalf("want include_usage true, got %v", so)
	}
	already := []byte(`{"model":"m","stream":true,"stream_options":{"include_usage":true}}`)
	if got := ensureOpenAIStreamUsageInRequest(already); !bytes.Equal(got, already) {
		t.Fatalf("should not rewrite when already set: %s", got)
	}
	nonStream := []byte(`{"model":"m","stream":false}`)
	if got := ensureOpenAIStreamUsageInRequest(nonStream); !bytes.Equal(got, nonStream) {
		t.Fatalf("non-stream unchanged: %s", got)
	}
}

func TestOpenAIChunkUsageFromSSE(t *testing.T) {
	t.Parallel()
	body := []byte(
		"data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\n" +
			"data: {\"model\":\"glm-5\",\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":7,\"total_tokens\":10}}\n\n" +
			"data: [DONE]\n\n",
	)
	rr := httptest.NewRecorder()
	tap := relayOpenAISSEStream(rr, bytes.NewReader(body))
	if tap.model != "glm-5" {
		t.Fatalf("model=%q", tap.model)
	}
	if tap.usage == nil || int(tap.usage["total_tokens"].(float64)) != 10 {
		t.Fatalf("usage=%v", tap.usage)
	}
	if rr.Body.Len() == 0 {
		t.Fatal("expected relayed body")
	}
}

func TestOpenAIStreamGatewayRecordsUsage(t *testing.T) {
	var usagePosts atomic.Int32
	var upstreamBody []byte
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamBody, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n"))
		_, _ = w.Write([]byte("data: {\"model\":\"glm-5\",\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":2,\"total_tokens\":3}}\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer upstream.Close()

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/openai-proxy/resolve":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status":             "ok",
				"proxy_key_id":       "pkcp1",
				"coding_plan_vendor": "glm",
				"credential_id":      "cred1",
				"api_key":            "sk-up",
				"upstream_chat_url":  upstream.URL + "/chat/completions",
			})
		case "/api/internal/v1/openai-proxy/usage":
			usagePosts.Add(1)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"recorded":1}`))
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer pulse.Close()

	s := &Server{pulse: NewPulseClient(pulse.URL, "tok", 0), transport: newOutboundTransport(nil)}
	req := httptest.NewRequest(http.MethodPost, "/openai/v1/chat/completions", bytes.NewReader([]byte(`{"model":"glm-5","stream":true}`)))
	req.Header.Set("Authorization", "Bearer pkcp_testkey")
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	s.handleOpenAICompat(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}
	if usagePosts.Load() != 1 {
		t.Fatalf("usage posts=%d", usagePosts.Load())
	}
	var sent map[string]any
	if err := json.Unmarshal(upstreamBody, &sent); err != nil {
		t.Fatal(err)
	}
	so, _ := sent["stream_options"].(map[string]any)
	if so == nil || so["include_usage"] != true {
		t.Fatalf("upstream should get include_usage: %v", sent)
	}
}
