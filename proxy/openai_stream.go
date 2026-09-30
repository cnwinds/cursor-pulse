package main

import (
	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"strings"
)

const openAIStreamScanMax = 8 << 20 // match MITM stream usage tap cap

// ensureOpenAIStreamUsageInRequest sets stream_options.include_usage so upstream
// emits token usage on the final SSE chunk (OpenAI-compatible).
func ensureOpenAIStreamUsageInRequest(body []byte) []byte {
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return body
	}
	stream, _ := payload["stream"].(bool)
	if !stream {
		return body
	}
	so, _ := payload["stream_options"].(map[string]any)
	if so == nil {
		so = map[string]any{}
		payload["stream_options"] = so
	}
	if include, ok := so["include_usage"].(bool); ok && include {
		return body
	}
	so["include_usage"] = true
	out, err := json.Marshal(payload)
	if err != nil {
		return body
	}
	return out
}

type openAIStreamUsageTap struct {
	model string
	usage map[string]any
}

func (t *openAIStreamUsageTap) ingestSSELine(line []byte) {
	payload, ok := openAISSEDataPayload(line)
	if !ok {
		return
	}
	model, usage := openAIChunkUsage(payload)
	if model != "" {
		t.model = model
	}
	if usage != nil {
		t.usage = usage
	}
}

func openAISSEDataPayload(line []byte) ([]byte, bool) {
	s := strings.TrimSpace(string(line))
	if !strings.HasPrefix(s, "data:") {
		return nil, false
	}
	data := strings.TrimSpace(s[5:])
	if data == "" || data == "[DONE]" {
		return nil, false
	}
	return []byte(data), true
}

func openAIChunkUsage(jsonPayload []byte) (model string, usage map[string]any) {
	var chunk struct {
		Model string         `json:"model"`
		Usage map[string]any `json:"usage"`
	}
	if err := json.Unmarshal(jsonPayload, &chunk); err != nil {
		return "", nil
	}
	if chunk.Model != "" {
		model = chunk.Model
	}
	if chunk.Usage != nil && len(chunk.Usage) > 0 {
		usage = chunk.Usage
	}
	return model, usage
}

// relayOpenAISSEStream copies an upstream SSE body to w and returns usage seen on the stream.
func relayOpenAISSEStream(w http.ResponseWriter, body io.Reader) openAIStreamUsageTap {
	var tap openAIStreamUsageTap
	sc := bufio.NewScanner(body)
	buf := make([]byte, 0, 64*1024)
	sc.Buffer(buf, openAIStreamScanMax)
	var flusher http.Flusher
	if f, ok := w.(http.Flusher); ok {
		flusher = f
	}
	for sc.Scan() {
		line := sc.Bytes()
		_, _ = w.Write(line)
		_, _ = w.Write([]byte("\n"))
		if flusher != nil {
			flusher.Flush()
		}
		tap.ingestSSELine(line)
	}
	return tap
}

func copyOpenAIUpstreamHeaders(w http.ResponseWriter, upResp *http.Response) {
	for k, vals := range upResp.Header {
		for _, v := range vals {
			w.Header().Add(k, v)
		}
	}
}

func copyOpenAIUpstreamStream(w http.ResponseWriter, upResp *http.Response) openAIStreamUsageTap {
	defer upResp.Body.Close()
	copyOpenAIUpstreamHeaders(w, upResp)
	w.WriteHeader(upResp.StatusCode)
	if upResp.StatusCode != http.StatusOK {
		_, _ = io.Copy(w, upResp.Body)
		return openAIStreamUsageTap{}
	}
	return relayOpenAISSEStream(w, upResp.Body)
}

func usageMapFromJSONBody(respBody []byte) (model string, usage map[string]any) {
	var parsed struct {
		Model string         `json:"model"`
		Usage map[string]any `json:"usage"`
	}
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		return "", nil
	}
	return parsed.Model, parsed.Usage
}
