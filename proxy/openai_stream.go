package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"
)

const openAIStreamScanMax = 8 << 20 // match MITM stream usage tap cap

// ensureOpenAIStreamUsageInRequest sets stream_options.include_usage so upstream
// emits token usage on the final SSE chunk (OpenAI-compatible).
// Numbers and raw "<", ">", "&" in the original JSON are preserved.
func ensureOpenAIStreamUsageInRequest(body []byte) []byte {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var payload map[string]any
	if err := dec.Decode(&payload); err != nil {
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
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(payload); err != nil {
		return body
	}
	return bytes.TrimRight(buf.Bytes(), "\n")
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

// sseLineTap parses SSE lines without altering the bytes forwarded to the client.
// A single line over max is skipped for parsing; following lines are still read.
type sseLineTap struct {
	tap  *openAIStreamUsageTap
	line []byte
	skip bool
	max  int
}

func (t *sseLineTap) feed(p []byte) {
	max := t.max
	if max <= 0 {
		max = openAIStreamScanMax
	}
	for _, b := range p {
		if t.skip {
			if b == '\n' {
				t.skip = false
			}
			continue
		}
		if b == '\n' {
			line := t.line
			if len(line) > 0 && line[len(line)-1] == '\r' {
				line = line[:len(line)-1]
			}
			t.tap.ingestSSELine(line)
			t.line = t.line[:0]
			continue
		}
		if len(t.line) >= max {
			t.skip = true
			t.line = t.line[:0]
			continue
		}
		t.line = append(t.line, b)
	}
}

func (t *sseLineTap) flush() {
	if t.skip || len(t.line) == 0 {
		return
	}
	line := t.line
	if len(line) > 0 && line[len(line)-1] == '\r' {
		line = line[:len(line)-1]
	}
	t.tap.ingestSSELine(line)
	t.line = t.line[:0]
}

// relayOpenAISSEStream copies upstream SSE bytes unchanged and returns usage seen on the stream.
// Client disconnects do not stop the upstream read, so a trailing usage chunk is still recorded.
func relayOpenAISSEStream(w http.ResponseWriter, body io.Reader) openAIStreamUsageTap {
	var tap openAIStreamUsageTap
	lineTap := sseLineTap{tap: &tap, max: openAIStreamScanMax}
	var flusher http.Flusher
	if f, ok := w.(http.Flusher); ok {
		flusher = f
	}
	buf := make([]byte, 32*1024)
	clientOK := true
	for {
		n, err := body.Read(buf)
		if n > 0 {
			lineTap.feed(buf[:n])
			if clientOK {
				if werr := writeAll(w, buf[:n]); werr != nil {
					clientOK = false
				} else if flusher != nil {
					flusher.Flush()
				}
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			log.Printf("[openai] stream read: %v", err)
			break
		}
	}
	lineTap.flush()
	return tap
}

func writeAll(w io.Writer, p []byte) error {
	for len(p) > 0 {
		n, err := w.Write(p)
		if n > 0 {
			p = p[n:]
		}
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
	}
	return nil
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
