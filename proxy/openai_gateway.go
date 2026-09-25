package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"
)

func (s *Server) handleOpenAICompat(w http.ResponseWriter, r *http.Request) {
	if s.pulse == nil {
		http.Error(w, `{"error":{"message":"Pulse mode required","type":"server_error"}}`, http.StatusServiceUnavailable)
		return
	}
	path := r.URL.Path
	if path == "/openai/v1/models" && r.Method == http.MethodGet {
		writeOpenAIModels(w)
		return
	}
	if path != "/openai/v1/chat/completions" || r.Method != http.MethodPost {
		http.NotFound(w, r)
		return
	}
	pulseKey := extractBearer(r)
	if pulseKey == "" || !strings.HasPrefix(pulseKey, "pkcp_") {
		writeOpenAIError(w, http.StatusUnauthorized, "Invalid API key provided")
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeOpenAIError(w, http.StatusBadRequest, "Invalid body")
		return
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		writeOpenAIError(w, http.StatusBadRequest, "Invalid JSON body")
		return
	}
	stream, _ := payload["stream"].(bool)
	model, _ := payload["model"].(string)

	excluded := []string{}
	const maxAttempts = 8
	for attempt := 0; attempt < maxAttempts; attempt++ {
		res, err := s.pulse.ResolveOpenAI(pulseKey, excluded)
		if err != nil {
			log.Printf("[openai] resolve error: %v", err)
			writeOpenAIError(w, http.StatusBadGateway, "Control plane unavailable")
			return
		}
		if res.Status != "ok" {
			if res.Status == "no_pool" {
				writeOpenAIError(w, http.StatusServiceUnavailable, "No available Coding Plan account in pool")
				return
			}
			if res.Status == "window_limited" {
				writeOpenAIError(w, http.StatusTooManyRequests, "Pulse proxy key window limit exceeded")
				return
			}
			writeOpenAIError(w, http.StatusUnauthorized, "Invalid API key provided")
			return
		}
		upResp, upErr := s.forwardOpenAIChat(res, body)
		if upErr != nil {
			log.Printf("[openai] upstream error: %v", upErr)
			excluded = append(excluded, res.CredentialID)
			continue
		}
		if upResp.StatusCode == 429 || upResp.StatusCode == 502 || upResp.StatusCode == 503 || upResp.StatusCode == 529 {
			upResp.Body.Close()
			excluded = append(excluded, res.CredentialID)
			continue
		}
		if stream {
			copyOpenAIUpstream(w, upResp)
			return
		}
		respBody, readErr := io.ReadAll(upResp.Body)
		upResp.Body.Close()
		if readErr != nil {
			writeOpenAIError(w, http.StatusBadGateway, "Upstream read failed")
			return
		}
		if upResp.StatusCode == 200 && res.ProxyKeyID != "" {
			s.recordOpenAIUsage(res, model, respBody)
		}
		copyOpenAIUpstreamBody(w, upResp.StatusCode, upResp.Header, respBody)
		return
	}
	writeOpenAIError(w, http.StatusTooManyRequests, "All pool accounts rejected or unavailable")
}

func (s *Server) forwardOpenAIChat(res OpenAIResolveResult, body []byte) (*http.Response, error) {
	req, err := http.NewRequest(http.MethodPost, res.UpstreamChatURL, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+res.APIKey)
	if res.CodingPlanVendor == "glm" {
		req.Header.Set("Accept-Language", "en-US,en")
	}
	return s.transport.RoundTrip(req)
}

func copyOpenAIUpstream(w http.ResponseWriter, upResp *http.Response) {
	defer upResp.Body.Close()
	for k, vals := range upResp.Header {
		for _, v := range vals {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(upResp.StatusCode)
	_, _ = io.Copy(w, upResp.Body)
}

func copyOpenAIUpstreamBody(w http.ResponseWriter, status int, hdr http.Header, body []byte) {
	for k, vals := range hdr {
		if len(vals) > 0 && strings.EqualFold(k, "Content-Type") {
			w.Header().Set(k, vals[0])
		}
	}
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func (s *Server) recordOpenAIUsage(res OpenAIResolveResult, model string, respBody []byte) {
	var parsed struct {
		Model string         `json:"model"`
		Usage map[string]any `json:"usage"`
	}
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		return
	}
	m := model
	if parsed.Model != "" {
		m = parsed.Model
	}
	if err := s.pulse.RecordOpenAIUsage(res.ProxyKeyID, res.CredentialID, m, parsed.Usage); err != nil {
		log.Printf("[openai] usage record: %v", err)
	}
}

func extractBearer(r *http.Request) string {
	auth := r.Header.Get("Authorization")
	if len(auth) > 7 && strings.EqualFold(auth[:7], "Bearer ") {
		return strings.TrimSpace(auth[7:])
	}
	return ""
}

func writeOpenAIError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(`{"error":{"message":"` + escapeJSON(msg) + `","type":"invalid_request_error"}}`))
}

func escapeJSON(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return s
}

func writeOpenAIModels(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"object":"list","data":[` +
		`{"id":"glm-5.2","object":"model","owned_by":"glm"},` +
		`{"id":"MiniMax-M2.5","object":"model","owned_by":"minimax"},` +
		`{"id":"kimi-k2.5","object":"model","owned_by":"kimi"}]}`))
}

func isOpenAICompatPath(r *http.Request) bool {
	if r.Method == http.MethodConnect {
		return false
	}
	p := r.URL.Path
	return strings.HasPrefix(p, "/openai/v1/")
}
