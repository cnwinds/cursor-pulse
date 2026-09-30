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
	in := []byte(`{"model":"m","stream":true,"messages":[{"content":"a < b & c"}],"seed":9007199254740993}`)
	out := ensureOpenAIStreamUsageInRequest(in)
	if !bytes.Contains(out, []byte("a < b & c")) {
		t.Fatalf("HTML escaped: %s", out)
	}
	if !bytes.Contains(out, []byte("9007199254740993")) {
		t.Fatalf("integer precision lost: %s", out)
	}
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
	if !bytes.Equal(rr.Body.Bytes(), body) {
		t.Fatalf("relay mutated bytes:\n got %q\nwant %q", rr.Body.Bytes(), body)
	}
}

func TestRelayPreservesCRLF(t *testing.T) {
	t.Parallel()
	body := []byte("data: {\"model\":\"m\",\"usage\":{\"total_tokens\":1}}\r\n\r\n")
	rr := httptest.NewRecorder()
	tap := relayOpenAISSEStream(rr, bytes.NewReader(body))
	if !bytes.Equal(rr.Body.Bytes(), body) {
		t.Fatalf("CRLF mutated:\n got %q\nwant %q", rr.Body.Bytes(), body)
	}
	if tap.model != "m" || int(tap.usage["total_tokens"].(float64)) != 1 {
		t.Fatalf("tap=%+v", tap)
	}
}

func TestSSETapSkipsOversizedLine(t *testing.T) {
	t.Parallel()
	var tap openAIStreamUsageTap
	lt := sseLineTap{tap: &tap, max: 64}
	var buf bytes.Buffer
	buf.WriteString("data: ")
	buf.Write(bytes.Repeat([]byte("a"), 80))
	buf.WriteString("\n")
	buf.WriteString("data: {\"model\":\"m\",\"usage\":{\"total_tokens\":2}}\n")
	lt.feed(buf.Bytes())
	lt.flush()
	if tap.model != "m" || tap.usage == nil || int(tap.usage["total_tokens"].(float64)) != 2 {
		t.Fatalf("tap=%+v", tap)
	}
}

type limitedResponseWriter struct {
	header http.Header
	allow  int
	body   bytes.Buffer
}

func (w *limitedResponseWriter) Header() http.Header {
	if w.header == nil {
		w.header = http.Header{}
	}
	return w.header
}

func (w *limitedResponseWriter) WriteHeader(int) {}

func (w *limitedResponseWriter) Write(p []byte) (int, error) {
	if w.allow <= 0 {
		return 0, io.ErrClosedPipe
	}
	if len(p) > w.allow {
		w.body.Write(p[:w.allow])
		n := w.allow
		w.allow = 0
		return n, io.ErrClosedPipe
	}
	w.body.Write(p)
	w.allow -= len(p)
	return len(p), nil
}

func TestRelayKeepsParsingAfterClientDisconnect(t *testing.T) {
	t.Parallel()
	body := []byte("data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\ndata: {\"model\":\"glm-5\",\"usage\":{\"prompt_tokens\":4,\"completion_tokens\":6,\"total_tokens\":10}}\n\n")
	w := &limitedResponseWriter{allow: 8}
	tap := relayOpenAISSEStream(w, bytes.NewReader(body))
	if tap.model != "glm-5" || tap.usage == nil || int(tap.usage["prompt_tokens"].(float64)) != 4 {
		t.Fatalf("usage lost after client disconnect: %+v", tap)
	}
}

func TestOpenAIStreamGatewayRecordsUsage(t *testing.T) {
	var usagePosts atomic.Int32
	var upstreamBody []byte
	var usageRaw []byte
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
			usageRaw, _ = io.ReadAll(r.Body)
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
	var reported map[string]any
	if err := json.Unmarshal(usageRaw, &reported); err != nil {
		t.Fatal(err)
	}
	if reported["model"] != "glm-5" || reported["proxy_key_id"] != "pkcp1" || reported["credential_id"] != "cred1" {
		t.Fatalf("reported=%v", reported)
	}
	usage, _ := reported["usage"].(map[string]any)
	if usage == nil || int(usage["total_tokens"].(float64)) != 3 {
		t.Fatalf("usage=%v", reported["usage"])
	}
}
