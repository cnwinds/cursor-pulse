package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
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
	if !bytes.Equal(body, codingPlanOpenAIModelsJSON) {
		t.Fatalf("models body must match embedded catalog")
	}
	var parsed struct {
		Object string `json:"object"`
		Data   []struct {
			ID      string `json:"id"`
			OwnedBy string `json:"owned_by"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		t.Fatalf("json: %v", err)
	}
	if parsed.Object != "list" || len(parsed.Data) < 3 {
		t.Fatalf("unexpected catalog: %+v", parsed)
	}
	ids := map[string]struct{}{}
	for _, m := range parsed.Data {
		ids[m.ID] = struct{}{}
	}
	for _, required := range []string{"glm-5.2", "MiniMax-M2.5", "kimi-k2.5", "glm-5.3", "MiniMax-M3", "kimi-for-coding"} {
		if _, ok := ids[required]; !ok {
			t.Fatalf("missing model id %q", required)
		}
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
