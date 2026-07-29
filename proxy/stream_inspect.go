package main

import (
	"encoding/base64"
	"encoding/json"
	"strings"
	"unicode/utf8"
)

// classifyStreamEnvelope inspects one Connect frame payload for account-level
// failures that should mark the pool key. endStream frames use the existing
// JSON ErrorDetails path; data frames are scanned for InteractionUpdate signals.
func classifyStreamEnvelope(flags byte, payload []byte) failKind {
	body := connectPayloadForInspect(flags, payload)
	if flags&endStreamFlag != 0 {
		return classifyEndStream(body)
	}
	return classifyDataPayload(body)
}

func classifyDataPayload(payload []byte) failKind {
	if title, msg, ok := findPostRequestPrompt(payload); ok && looksLikeUsageLimitPrompt(title, msg) {
		return failAccount
	}
	if looksLikeUsageLimitBlob(payload) {
		return failAccount
	}
	return failNone
}

func findPostRequestPrompt(buf []byte) (title, message string, ok bool) {
	return findPostRequestPromptDepth(buf, 0)
}

func findPostRequestPromptDepth(buf []byte, depth int) (string, string, bool) {
	if depth > 8 {
		return "", "", false
	}
	for _, f := range iterProtoFields(buf) {
		if f.wire != 2 {
			continue
		}
		if f.fieldNo == 19 {
			if t, m, hit := parsePostRequestPrompt(f.bytes); hit {
				return t, m, true
			}
		}
		if t, m, hit := findPostRequestPromptDepth(f.bytes, depth+1); hit {
			return t, m, true
		}
	}
	return "", "", false
}

func parsePostRequestPrompt(buf []byte) (title, message string, ok bool) {
	for _, f := range iterProtoFields(buf) {
		if f.wire != 2 {
			continue
		}
		s := string(f.bytes)
		if !utf8.Valid(f.bytes) {
			continue
		}
		switch f.fieldNo {
		case 1:
			title = s
		case 2:
			message = s
		}
	}
	return title, message, title != "" || message != ""
}

var usageLimitPhrases = []string{
	"hit your usage limit",
	"usage limits will reset",
	"set a spend limit",
	"set a [spend limit]",
	"resource_exhausted",
}

func looksLikeUsageLimitPrompt(title, message string) bool {
	combined := strings.ToLower(title + "\n" + message)
	for _, p := range usageLimitPhrases {
		if strings.Contains(combined, p) {
			return true
		}
	}
	return false
}

func looksLikeUsageLimitBlob(buf []byte) bool {
	strs := collectQuotaHintStrings(buf, 0, nil)
	var b strings.Builder
	for _, s := range strs {
		if b.Len() > 0 {
			b.WriteByte('\n')
		}
		b.WriteString(s)
	}
	s := strings.ToLower(b.String())
	if s == "" {
		return false
	}
	hits := 0
	for _, p := range usageLimitPhrases {
		if strings.Contains(s, p) {
			hits++
		}
	}
	return hits >= 2
}

func collectQuotaHintStrings(buf []byte, depth int, out []string) []string {
	if depth > 8 || len(out) > 32 {
		return out
	}
	for _, f := range iterProtoFields(buf) {
		if f.wire == 2 && isQuotaHintString(f.bytes) {
			out = append(out, string(f.bytes))
		}
		out = collectQuotaHintStrings(f.bytes, depth+1, out)
	}
	return out
}

func isQuotaHintString(b []byte) bool {
	if len(b) < 8 || len(b) > 2048 || !utf8.Valid(b) {
		return false
	}
	s := string(b)
	if strings.ContainsAny(s, "\x00") {
		return false
	}
	return true
}

// connectErrorFromPayload is a test helper for building end-stream JSON bodies.
func connectErrorFromPayload(enum int) []byte {
	raw := []byte{0x08, byte(enum)}
	ce := map[string]any{
		"error": map[string]any{
			"code":    "resource_exhausted",
			"message": "quota",
			"details": []map[string]string{
				{"type": "aiserver.v1.ErrorDetails", "value": base64.StdEncoding.EncodeToString(raw)},
			},
		},
		"metadata": map[string]any{},
	}
	b, _ := json.Marshal(ce)
	return b
}
