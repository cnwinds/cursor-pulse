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

func TestResolveQuotaPoolEmptyStreamIsAuto(t *testing.T) {
	pr, pw := io.Pipe()
	fs := newFrameSource(pr)
	_ = pw.Close()

	pool := resolveQuotaPool(context.Background(), "/agent.v1.AgentService/Run", fs.snapshot, fs)
	if pool != quotaPoolAuto {
		t.Fatalf("got %v want auto", pool)
	}
}

func TestResolveQuotaPoolNonRunIsAuto(t *testing.T) {
	body := []byte("x")
	pool := resolveQuotaPool(context.Background(), "/aiserver.v1.AiService/AvailableModels", func() []byte { return body }, nil)
	if pool != quotaPoolAuto {
		t.Fatalf("got %v want auto", pool)
	}
}

func TestEffectiveMarkQuotaPoolFromBody(t *testing.T) {
	req := buildRequestedModel("gpt-5.6-sol", false, true)
	body := buildAgentRunEnvelope(req)
	snap := func() []byte { return body }

	got := effectiveMarkQuotaPool("/agent.v1.AgentService/Run", snap, quotaPoolAuto)
	if got != quotaPoolAPI {
		t.Fatalf("model arrived after selection: got %v want api", got)
	}
	if effectiveMarkQuotaPool("/agent.v1.AgentService/Run", snap, quotaPoolAPI) != quotaPoolAPI {
		t.Fatal("api at selection time should win")
	}
}

func TestSnapshotHeadroomIsPerPool(t *testing.T) {
	apiFull := 100.0
	autoOK := 15.0
	e := &keyEntry{credentialQuotaState: credentialQuotaState{autoPct: &autoOK, apiPct: &apiFull}}
	if !e.hasQuotaForPool(quotaPoolAuto) {
		t.Fatal("auto pool should pass")
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
