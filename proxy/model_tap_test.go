package main

import (
	"encoding/binary"
	"testing"
)

// buildModelTapEnvelope wraps proto string fields in a Connect envelope, mirroring
// real agent Run payloads used for model extraction (see PROXY_DEBUG_USAGE dumps).
func buildModelTapEnvelope(cands []string) []byte {
	var payload []byte
	for i, s := range cands {
		payload = append(payload, msgField(i+1, []byte(s))...)
	}
	frame := make([]byte, 5+len(payload))
	frame[0] = 0
	binary.BigEndian.PutUint32(frame[1:5], uint32(len(payload)))
	copy(frame[5:], payload)
	return frame
}


func TestFindModelNameFromProto(t *testing.T) {
	inner := msgField(1, []byte("claude-4-sonnet"))
	payload := msgField(3, inner)
	got := findModelName(payload)
	if got != "claude-4-sonnet" {
		t.Fatalf("got %q", got)
	}
}

func TestFindModelNameFromConnectEnvelope(t *testing.T) {
	inner := msgField(2, []byte("composer-2.5"))
	payload := msgField(1, inner)
	frame := make([]byte, 5+len(payload))
	frame[0] = 0
	binary.BigEndian.PutUint32(frame[1:5], uint32(len(payload)))
	copy(frame[5:], payload)
	got := findModelName(frame)
	if got != "composer-2.5" {
		t.Fatalf("got %q", got)
	}
}

func TestFindModelNameIgnoresNoise(t *testing.T) {
	payload := msgField(1, []byte("hello world"))
	payload = append(payload, msgField(2, []byte("/path/to/file.go"))...)
	if got := findModelName(payload); got != "" {
		t.Fatalf("want empty, got %q", got)
	}
}

func TestFindModelNameIgnoresSkillPath(t *testing.T) {
	path := "/root/.cursor/plugins/cache/cursor-public/superpowers/abc/skills/using-superpowers/SKILL.md"
	short := path
	if len(short) > 64 {
		short = short[:64]
	}
	payload := msgField(1, []byte(short))
	payload = append(payload, msgField(2, []byte("claude-4-sonnet"))...)
	got := findModelName(payload)
	if got != "claude-4-sonnet" {
		t.Fatalf("got %q want claude-4-sonnet", got)
	}
}

func TestPickSelectedModelFirstNotShortest(t *testing.T) {
	// Mirrors real dumps: selected id first, then catalog with shorter ids.
	cands := []string{
		"/root/.cursor/skills/x",
		"composer-2.5",
		"fast",
		"true",
		"default",
		"gpt-5.2",
		"composer-2.5-fast",
		"grok-4.5",
	}
	got := pickSelectedModel(cands)
	if got != "composer-2.5-fast" {
		t.Fatalf("got %q want composer-2.5-fast", got)
	}
}

func TestPickSelectedModelOpusNoFast(t *testing.T) {
	cands := []string{
		"claude-opus-4-8",
		"thinking",
		"true",
		"effort",
		"high",
		"fast",
		"false",
		"default",
		"grok-4.5",
		"composer-2.5",
	}
	got := pickSelectedModel(cands)
	if got != "claude-opus-4-8" {
		t.Fatalf("got %q want claude-opus-4-8", got)
	}
}

func TestPickSelectedModelIgnoresBareOpusLabel(t *testing.T) {
	// Some IDE payloads put a bare family label before the real id.
	cands := []string{
		"opus",
		"sonnet",
		"claude-opus-4-8",
		"thinking",
		"true",
		"fast",
		"false",
		"grok-4.5",
	}
	got := pickSelectedModel(cands)
	if got != "claude-opus-4-8" {
		t.Fatalf("got %q want claude-opus-4-8", got)
	}
}

func TestLooksLikeModelIDRejectsBareFamily(t *testing.T) {
	for _, s := range []string{"opus", "sonnet", "haiku", "claude", "grok"} {
		if looksLikeModelID(s) {
			t.Fatalf("%q should not look like a model id", s)
		}
	}
	for _, s := range []string{"claude-opus-4-8", "gpt-5.6-sol-max", "composer-2.5"} {
		if !looksLikeModelID(s) {
			t.Fatalf("%q should look like a model id", s)
		}
	}
}

func TestPickSelectedModelFromRealDumpOrderComposer(t *testing.T) {
	// Subset of 0001.txt string order around selection.
	cands := []string{
		"f6319b8d-1e6a-4cd3-a105-7c90d0b81b97",
		"composer-2.5",
		"fast",
		"true",
		"default",
		"gpt-5.3-codex-low",
		"gpt-5.2",
	}
	if got := pickSelectedModel(cands); got != "composer-2.5-fast" {
		t.Fatalf("got %q", got)
	}
}

func TestPickSelectedModelFromRealDumpOrderOpus(t *testing.T) {
	// Subset of 0002.txt string order around selection (Opus 4.8 → claude-opus-4-8).
	cands := []string{
		"f6319b8d-1e6a-4cd3-a105-7c90d0b81b97",
		"claude-opus-4-8",
		"thinking",
		"true",
		"context",
		"300k",
		"effort",
		"high",
		"fast",
		"false",
		"default",
		"grok-4.5",
		"composer-2.5",
		"gpt-5.6-sol",
	}
	if got := pickSelectedModel(cands); got != "claude-opus-4-8" {
		t.Fatalf("got %q want claude-opus-4-8", got)
	}
}

func TestFindModelNameFromRealDumpBinComposer(t *testing.T) {
	// String order captured from a real debug dump (0001); body built inline for CI.
	cands := []string{
		"f6319b8d-1e6a-4cd3-a105-7c90d0b81b97",
		"composer-2.5",
		"fast",
		"true",
		"default",
		"gpt-5.3-codex-low",
		"gpt-5.2",
	}
	body := buildModelTapEnvelope(cands)
	if got := findModelName(body); got != "composer-2.5-fast" {
		t.Fatalf("got %q want composer-2.5-fast", got)
	}
}

func TestFindModelNameFromRealDumpBinOpus(t *testing.T) {
	// String order captured from a real debug dump (0002); body built inline for CI.
	cands := []string{
		"f6319b8d-1e6a-4cd3-a105-7c90d0b81b97",
		"claude-opus-4-8",
		"thinking",
		"true",
		"context",
		"300k",
		"effort",
		"high",
		"fast",
		"false",
		"default",
		"grok-4.5",
		"composer-2.5",
		"gpt-5.6-sol",
	}
	body := buildModelTapEnvelope(cands)
	if got := findModelName(body); got != "claude-opus-4-8" {
		t.Fatalf("got %q want claude-opus-4-8", got)
	}
}
