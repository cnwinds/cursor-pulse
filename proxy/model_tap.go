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

// findModelName best-effort extracts the selected model id from a Connect/protobuf request body.
func findModelName(buf []byte) string {
	return analyzeModelTap(buf).Picked
}

func analyzeModelTap(buf []byte) modelTapReport {
	rep := modelTapReport{BodyLen: len(buf)}
	var strs []string

	// Prefer Connect envelope payloads (preserves real field order from agent Run).
	for i := 0; i+5 <= len(buf); {
		size := int(binary.BigEndian.Uint32(buf[i+1 : i+5]))
		total := 5 + size
		if size < 0 || i+total > len(buf) {
			break
		}
		payload := buf[i+5 : i+total]
		i += total
		rep.EnvelopeHits++
		strs = append(strs, collectProtoStrings(payload, 0, nil)...)
	}
	if rep.EnvelopeHits == 0 {
		strs = collectProtoStrings(buf, 0, nil)
	}
	rep.AllStrings = uniqStrings(strs)
	rep.Matched = matchedModels(rep.AllStrings)
	rep.Picked = pickSelectedModel(rep.AllStrings)
	return rep
}

// pickSelectedModel: first model-like id in order; if fast=true before next model, append "-fast".
func pickSelectedModel(candidates []string) string {
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
	fast := false
	for i := baseIdx + 1; i < len(candidates); i++ {
		s := candidates[i]
		if looksLikeModelID(s) {
			break // next model / catalog entry
		}
		if s != "fast" {
			continue
		}
		if i+1 < len(candidates) {
			switch candidates[i+1] {
			case "true":
				fast = true
			case "false":
				fast = false
			}
		}
	}
	if fast && !strings.HasSuffix(strings.ToLower(base), "-fast") {
		return base + "-fast"
	}
	return base
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
	// Bare family/UI labels show up early in some payloads ("opus") and must
	// not beat real ids like "claude-opus-4-8". Display names with spaces
	// ("opus 4.8") are rejected by isModelCandidateBytes and never reach here.
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
	// Real model ids are versioned; reject unversioned family fragments.
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

// logUsageModelTap records model-extraction diagnostics for one usage event.
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
