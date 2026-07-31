package main

import (
	"bytes"
	"io"
	"testing"
	"time"
)

func TestResolveMaxRequestBody(t *testing.T) {
	t.Setenv("PROXY_MAX_BODY", "1048576")
	if got := maxNonStreamBodyLimit(); got != 1<<20 {
		t.Fatalf("got %d want %d", got, 1<<20)
	}
	t.Setenv("PROXY_MAX_BODY", "")
	if got := maxNonStreamBodyLimit(); got != defaultMaxRequestBody {
		t.Fatalf("default: got %d want %d", got, defaultMaxRequestBody)
	}
}

func TestResolveExhaustedResetInterval(t *testing.T) {
	t.Setenv("PROXY_EXHAUSTED_RESET", "15m")
	if got := resolveExhaustedResetInterval(); got != 15*time.Minute {
		t.Fatalf("got %v", got)
	}
	t.Setenv("PROXY_EXHAUSTED_RESET", "")
	if got := resolveExhaustedResetInterval(); got != 30*time.Minute {
		t.Fatalf("default: got %v want 30m", got)
	}
	t.Setenv("PROXY_EXHAUSTED_RESET", "off")
	if got := resolveExhaustedResetInterval(); got != 0 {
		t.Fatalf("off: got %v want 0", got)
	}
	t.Setenv("PROXY_EXHAUSTED_RESET", "0")
	if got := resolveExhaustedResetInterval(); got != 0 {
		t.Fatalf("0: got %v want 0", got)
	}
}

func TestPoolResetClearsExhausted(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.keys[0].setFullyQuotaExhausted()
	p.keys[1].badUntil = time.Now().Add(time.Hour)
	p.cur = 1
	p.reset()
	for _, e := range p.keys {
		if e.quotaFullyExhausted() {
			t.Fatalf("quota exhaustion not cleared for %s", e.credentialID)
		}
		if !e.badUntil.IsZero() {
			t.Fatalf("badUntil not cleared for %s", e.credentialID)
		}
	}
	if p.cur != 0 {
		t.Fatalf("cur=%d want 0", p.cur)
	}
}

func TestUsageTapBufferCapStopsGrowth(t *testing.T) {
	old := maxUsageTapBuffer
	maxUsageTapBuffer = 64
	t.Cleanup(func() { maxUsageTapBuffer = old })

	var dst bytes.Buffer
	tap := &usageTapWriter{w: &dst}
	payload := bytes.Repeat([]byte("x"), 128)
	if _, err := tap.Write(payload); err != nil {
		t.Fatal(err)
	}
	if !tap.stopped {
		t.Fatal("expected stopped after exceeding cap")
	}
	if len(tap.buf) > maxUsageTapBuffer {
		t.Fatalf("buf len %d exceeds cap %d", len(tap.buf), maxUsageTapBuffer)
	}
	if dst.Len() != len(payload) {
		t.Fatalf("forwarded %d want %d", dst.Len(), len(payload))
	}
	// Further writes still forward but do not grow buffer.
	before := len(tap.buf)
	if _, err := tap.Write([]byte("more")); err != nil {
		t.Fatal(err)
	}
	if len(tap.buf) != before {
		t.Fatalf("buffer grew after stop: %d -> %d", before, len(tap.buf))
	}
}

func TestUsageTapStillForwardsWhenStopped(t *testing.T) {
	old := maxUsageTapBuffer
	maxUsageTapBuffer = 8
	t.Cleanup(func() { maxUsageTapBuffer = old })

	var dst bytes.Buffer
	tap := &usageTapWriter{w: &dst}
	if _, err := io.Copy(tap, bytes.NewReader(bytes.Repeat([]byte("a"), 32))); err != nil {
		t.Fatal(err)
	}
	if dst.Len() != 32 {
		t.Fatalf("forwarded %d want 32", dst.Len())
	}
}
