package main

import (
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
}

func TestOpenAIRequiresPulse(t *testing.T) {
	t.Parallel()
	s := NewServer(NewPool(nil), nil, nil, nil)
	req := httptest.NewRequest(http.MethodPost, "/openai/v1/chat/completions", nil)
	rr := httptest.NewRecorder()
	s.handleOpenAICompat(rr, req)
	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("status %d", rr.Code)
	}
}
