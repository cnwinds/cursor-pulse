package main

import (
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

// AgentRunRequest field numbers (agent.v1.AgentRunRequest from cursor-agent).
const (
	runFieldModelDetails   = 3
	runFieldRequestedModel = 9
	runFieldDevRawSlug     = 18
)

// RequestedModel / ModelDetails field numbers.
const (
	modelFieldModelID  = 1
	modelFieldMaxMode  = 2 // RequestedModel
	modelFieldParams   = 3 // RequestedModel.parameters
	modelDetailsMaxMode = 7 // ModelDetails.max_mode
)

var (
	debugUsage    = strings.TrimSpace(os.Getenv("PROXY_DEBUG_USAGE"))
	debugUsageDir = strings.TrimSpace(os.Getenv("PROXY_DEBUG_USAGE_DIR"))
	debugDumpSeq  atomic.Uint64
)

func debugUsageEnabled() bool {
	v := strings.ToLower(debugUsage)
	return v == "1" || v == "true" || v == "yes" || v == "on"
}

type modelTapReport struct {
	BodyLen      int
	AllStrings   []string
	Matched      []string
	Picked       string
	EnvelopeHits int
}

// findModelName extracts the billed model from an agent Run Connect request body.
func findModelName(buf []byte) string {
	return analyzeModelTap(buf).Picked
}

func analyzeModelTap(buf []byte) modelTapReport {
	rep := modelTapReport{BodyLen: len(buf)}
	payloads := iterConnectPayloads(buf)
	rep.EnvelopeHits = len(payloads)

	var strs []string
	if rep.EnvelopeHits == 0 {
		strs = collectProtoStrings(buf, 0, nil)
	} else {
		for _, payload := range payloads {
			strs = append(strs, collectProtoStrings(payload, 0, nil)...)
		}
	}
	rep.AllStrings = uniqStrings(strs)
	rep.Matched = matchedModels(rep.AllStrings)

	// Prefer structured AgentRunRequest fields (last envelope = current turn).
	if rep.EnvelopeHits > 0 {
		for i := len(payloads) - 1; i >= 0; i-- {
			if m := modelFromAgentRunPayload(payloads[i]); m != "" {
				rep.Picked = m
				return rep
			}
		}
	} else if m := modelFromAgentRunPayload(buf); m != "" {
		rep.Picked = m
		return rep
	}

	// Legacy/simplified test payloads: bare model string at message field 1.
	if m := legacyModelField1(buf); m != "" {
		rep.Picked = m
		return rep
	}

	rep.Picked = pickSelectedModel(rep.AllStrings)
	return rep
}

func iterConnectPayloads(buf []byte) [][]byte {
	var out [][]byte
	for i := 0; i+5 <= len(buf); {
		size := int(binary.BigEndian.Uint32(buf[i+1 : i+5]))
		total := 5 + size
		if size < 0 || i+total > len(buf) {
			break
		}
		out = append(out, buf[i+5:i+total])
		i += total
	}
	return out
}

// modelFromAgentRunPayload reads agent.v1.AgentRunRequest.{requested_model|model_details}.
func modelFromAgentRunPayload(buf []byte) string {
	if m := extractModelFromRunRequest(buf); m != "" {
		return m
	}
	return findRunRequestModelDepth(buf, 0)
}

func findRunRequestModelDepth(buf []byte, depth int) string {
	if depth > 12 || len(buf) == 0 {
		return ""
	}
	if m := extractModelFromRunRequest(buf); m != "" {
		return m
	}
	for _, f := range iterProtoFields(buf) {
		if f.wire != 2 {
			continue
		}
		if m := findRunRequestModelDepth(f.bytes, depth+1); m != "" {
			return m
		}
	}
	return ""
}

func extractModelFromRunRequest(run []byte) string {
	var modelDetails []byte
	var requestedModel []byte
	var devSlug string
	for _, f := range iterProtoFields(run) {
		switch f.fieldNo {
		case runFieldRequestedModel:
			if f.wire == 2 {
				requestedModel = f.bytes
			}
		case runFieldModelDetails:
			if f.wire == 2 {
				modelDetails = f.bytes
			}
		case runFieldDevRawSlug:
			if f.wire == 2 && isModelCandidateBytes(f.bytes) {
				devSlug = string(f.bytes)
			}
		}
	}
	if len(requestedModel) > 0 {
		if m := modelFromRequestedModel(requestedModel); m != "" {
			return m
		}
	}
	if len(modelDetails) > 0 {
		if m := modelFromModelDetails(modelDetails); m != "" {
			return m
		}
	}
	if devSlug != "" && looksLikeModelID(devSlug) {
		return devSlug
	}
	return ""
}

func modelFromRequestedModel(msg []byte) string {
	var modelID string
	var maxMode bool
	var params []modelParameter
	for _, f := range iterProtoFields(msg) {
		switch f.fieldNo {
		case modelFieldModelID:
			if f.wire == 2 {
				modelID = string(f.bytes)
			}
		case modelFieldMaxMode:
			if f.wire == 0 {
				maxMode = f.varint != 0
			}
		case modelFieldParams:
			if f.wire == 2 {
				if p := parseModelParameter(f.bytes); p.id != "" {
					params = append(params, p)
				}
			}
		}
	}
	return buildModelSlug(modelID, maxMode, params)
}

func modelFromModelDetails(msg []byte) string {
	var modelID string
	var maxMode bool
	for _, f := range iterProtoFields(msg) {
		switch f.fieldNo {
		case modelFieldModelID:
			if f.wire == 2 {
				modelID = string(f.bytes)
			}
		case modelDetailsMaxMode:
			if f.wire == 0 {
				maxMode = f.varint != 0
			}
		}
	}
	return buildModelSlug(modelID, maxMode, nil)
}

type modelParameter struct {
	id, value string
}

func parseModelParameter(msg []byte) modelParameter {
	var p modelParameter
	for _, f := range iterProtoFields(msg) {
		switch f.fieldNo {
		case 1:
			if f.wire == 2 {
				p.id = string(f.bytes)
			}
		case 2:
			if f.wire == 2 {
				p.value = string(f.bytes)
			}
		}
	}
	return p
}

func buildModelSlug(modelID string, maxMode bool, params []modelParameter) string {
	if modelID == "" || !looksLikeModelID(modelID) {
		return ""
	}
	out := modelID
	lower := strings.ToLower(out)
	fast := strings.HasSuffix(lower, "-fast")
	if maxMode && !strings.HasSuffix(lower, "-max") {
		out += "-max"
		lower = strings.ToLower(out)
	}
	for _, p := range params {
		if p.id == "fast" && p.value == "true" {
			fast = true
		}
	}
	if fast && !strings.HasSuffix(lower, "-fast") {
		out += "-fast"
	}
	return out
}

// legacyModelField1 supports minimal test/e2e payloads: field 1 = model id string.
func legacyModelField1(buf []byte) string {
	for _, f := range iterProtoFields(buf) {
		if f.fieldNo == 1 && f.wire == 2 && isModelCandidateBytes(f.bytes) {
			s := string(f.bytes)
			if looksLikeModelID(s) {
				return s
			}
		}
	}
	return ""
}

// pickSelectedModel: fallback heuristic before catalog "default"; apply fast/max suffixes.
func pickSelectedModel(candidates []string) string {
	end := len(candidates)
	for i, s := range candidates {
		if s == "default" {
			end = i
			break
		}
	}
	return pickSelectedModelInWindow(candidates[:end])
}

func pickSelectedModelInWindow(candidates []string) string {
	baseIdx := -1
	var base string
	for i, s := range candidates {
		if looksLikeModelID(s) {
			baseIdx = i
			base = s
			break
		}
	}
	if baseIdx < 0 {
		return ""
	}
	return applyModelSuffixes(candidates, base, baseIdx)
}

func applyModelSuffixes(candidates []string, base string, baseIdx int) string {
	fast := false
	maxMode := false
	lower := strings.ToLower(base)
	if strings.HasSuffix(lower, "-fast") {
		fast = true
		base = base[:len(base)-len("-fast")]
		lower = strings.ToLower(base)
	}
	if strings.HasSuffix(lower, "-max") {
		maxMode = true
		base = base[:len(base)-len("-max")]
	}
	for i := baseIdx + 1; i < len(candidates); i++ {
		s := candidates[i]
		if looksLikeModelID(s) {
			break
		}
		if s == "fast" && i+1 < len(candidates) {
			switch candidates[i+1] {
			case "true":
				fast = true
			case "false":
				fast = false
			}
		}
		if s == "max" && i+1 < len(candidates) {
			switch candidates[i+1] {
			case "true":
				maxMode = true
			case "false":
				maxMode = false
			}
		}
	}
	out := base
	outLower := strings.ToLower(out)
	if maxMode && !strings.HasSuffix(outLower, "-max") {
		out += "-max"
		outLower = strings.ToLower(out)
	}
	if fast && !strings.HasSuffix(outLower, "-fast") {
		out += "-fast"
	}
	return out
}

func matchedModels(candidates []string) []string {
	var out []string
	for _, s := range candidates {
		if looksLikeModelID(s) {
			out = append(out, s)
		}
	}
	return out
}

func uniqStrings(in []string) []string {
	seen := map[string]struct{}{}
	var out []string
	for _, s := range in {
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	return out
}

func collectProtoStrings(buf []byte, depth int, out []string) []string {
	if depth > 8 {
		return out
	}
	for _, f := range iterProtoFields(buf) {
		if f.wire != 2 {
			continue
		}
		if isModelCandidateBytes(f.bytes) {
			out = append(out, string(f.bytes))
		}
		out = collectProtoStrings(f.bytes, depth+1, out)
	}
	return out
}

func isModelCandidateBytes(b []byte) bool {
	maxLen := 64
	if debugUsageEnabled() {
		maxLen = 256
	}
	if len(b) < 2 || len(b) > maxLen {
		return false
	}
	if !utf8.Valid(b) {
		return false
	}
	s := string(b)
	if strings.ContainsAny(s, " \t\n\r\x00") {
		return false
	}
	for _, r := range s {
		if r < 0x20 || r > 0x7e {
			return false
		}
	}
	return true
}

func looksLikeModelID(s string) bool {
	if len(s) < 3 || len(s) > 64 {
		return false
	}
	if strings.ContainsAny(s, `/\`) {
		lower := strings.ToLower(s)
		if !strings.HasPrefix(lower, "accounts/") {
			return false
		}
	}
	lower := strings.ToLower(s)
	switch lower {
	case "opus", "sonnet", "haiku", "claude", "gemini", "grok", "composer", "fable":
		return false
	}
	keywords := []string{
		"claude", "gpt-", "gpt4", "o1-", "o3-", "o4-", "gemini",
		"composer-", "composer2", "grok", "deepseek",
		"sonnet", "opus", "haiku", "fable",
		"cursor-small", "cursor-fast", "cursor-grok",
	}
	hit := false
	for _, k := range keywords {
		if strings.Contains(lower, k) {
			hit = true
			break
		}
	}
	if !hit {
		return false
	}
	for _, r := range lower {
		if r >= '0' && r <= '9' {
			return true
		}
	}
	switch lower {
	case "cursor-small", "cursor-fast":
		return true
	}
	return false
}

func logUsageModelTap(path, proxyKeyID, credID string, tc TokenCounts, body []byte) string {
	rep := analyzeModelTap(body)
	log.Printf("[usage] path=%s proxy_key=%s cred=%s tokens={in:%d out:%d cache_r:%d cache_w:%d reason:%d} model=%q matched_first=%q body_len=%d envelopes=%d",
		path, proxyKeyID, credID,
		tc.Input, tc.Output, tc.CacheRead, tc.CacheWrite, tc.Reasoning,
		rep.Picked, firstOrEmpty(rep.Matched), rep.BodyLen, rep.EnvelopeHits,
	)
	if !debugUsageEnabled() {
		return rep.Picked
	}

	const maxList = 80
	listed := rep.AllStrings
	if len(listed) > maxList {
		listed = listed[:maxList]
	}
	log.Printf("[usage-debug] path=%s all_strings(%d)=%q", path, len(rep.AllStrings), listed)

	dir := debugUsageDir
	if dir == "" {
		home, _ := os.UserHomeDir()
		dir = filepath.Join(home, ".cursor-quota-proxy", "debug-usage")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		log.Printf("[usage-debug] mkdir %s: %v", dir, err)
		return rep.Picked
	}
	seq := debugDumpSeq.Add(1)
	stamp := time.Now().UTC().Format("20060102-150405")
	base := filepath.Join(dir, fmt.Sprintf("%s-%04d", stamp, seq))
	binPath := base + ".bin"
	txtPath := base + ".txt"
	if err := os.WriteFile(binPath, body, 0o600); err != nil {
		log.Printf("[usage-debug] write bin: %v", err)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "path=%s\nproxy_key=%s\ncred=%s\npicked=%s\nmatched=%v\nbody_len=%d\nenvelopes=%d\n\n",
		path, proxyKeyID, credID, rep.Picked, rep.Matched, rep.BodyLen, rep.EnvelopeHits)
	b.WriteString("=== all printable proto strings ===\n")
	for i, s := range rep.AllStrings {
		fmt.Fprintf(&b, "%d\t%q\n", i, s)
	}
	b.WriteString("\n=== body head hex (256B) ===\n")
	n := 256
	if len(body) < n {
		n = len(body)
	}
	b.WriteString(hex.EncodeToString(body[:n]))
	b.WriteByte('\n')
	if err := os.WriteFile(txtPath, []byte(b.String()), 0o600); err != nil {
		log.Printf("[usage-debug] write txt: %v", err)
	} else {
		log.Printf("[usage-debug] dumped %s and %s", binPath, txtPath)
	}
	return rep.Picked
}

func firstOrEmpty(ss []string) string {
	if len(ss) == 0 {
		return ""
	}
	return ss[0]
}
