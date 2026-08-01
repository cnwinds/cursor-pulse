package main

import (
	"context"
	"io"
	"testing"
	"time"
)

func TestResolveQuotaPoolWaitsForStreamBody(t *testing.T) {
	pr, pw := io.Pipe()
	fs := newFrameSource(pr)
	go func() {
		time.Sleep(30 * time.Millisecond)
		req := buildRequestedModel("gpt-5.6-sol", true, false)
		body := buildAgentRunEnvelope(req)
		_, _ = pw.Write(body)
		_ = pw.Close()
	}()

	pool := resolveQuotaPool(context.Background(), "/agent.v1.AgentService/Run", fs.snapshot, fs)
	if pool != quotaPoolAPI {
		t.Fatalf("got %v want api", pool)
	}
}

func TestResolveQuotaPoolEmptyStreamUnknown(t *testing.T) {
	pr, pw := io.Pipe()
	fs := newFrameSource(pr)
	_ = pw.Close()

	pool := resolveQuotaPool(context.Background(), "/agent.v1.AgentService/Run", fs.snapshot, fs)
	if pool != quotaPoolUnknown {
		t.Fatalf("got %v want unknown", pool)
	}
}

func TestEffectiveMarkQuotaPoolFromBody(t *testing.T) {
	req := buildRequestedModel("composer-2.5", false, true)
	body := buildAgentRunEnvelope(req)
	snap := func() []byte { return body }

	got := effectiveMarkQuotaPool("/agent.v1.AgentService/Run", snap, quotaPoolUnknown)
	if got != quotaPoolAuto {
		t.Fatalf("got %v want auto", got)
	}
	if effectiveMarkQuotaPool("/agent.v1.AgentService/Run", snap, quotaPoolAPI) != quotaPoolAPI {
		t.Fatal("initial pool should win when set")
	}
}

func TestUnknownSnapshotRequiresBothPools(t *testing.T) {
	apiFull := 100.0
	autoOK := 15.0
	e := &keyEntry{credentialQuotaState: credentialQuotaState{autoPct: &autoOK, apiPct: &apiFull}}
	if e.hasQuotaForPool(quotaPoolUnknown) {
		t.Fatal("unknown pool should fail when api snapshot full")
	}
	if !e.hasQuotaForPool(quotaPoolAuto) {
		t.Fatal("auto pool should still pass")
	}
	if e.hasQuotaForPool(quotaPoolAPI) {
		t.Fatal("api pool should fail")
	}
}

func TestFrameSourceWaitForRunnableModel(t *testing.T) {
	pr, pw := io.Pipe()
	fs := newFrameSource(pr)
	done := make(chan struct{})
	go func() {
		time.Sleep(20 * time.Millisecond)
		req := buildRequestedModel("composer-2.5", false, false)
		body := buildAgentRunEnvelope(req)
		_, _ = pw.Write(body)
		_ = pw.Close()
		close(done)
	}()

	fs.waitForRunnableModel(time.Now().Add(time.Second))
	if findModelName(fs.snapshot()) != "composer-2.5" {
		t.Fatalf("model not parsed after wait")
	}
	<-done
}
