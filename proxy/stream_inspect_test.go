package main

import (
	"bytes"
	"compress/gzip"
	"os"
	"testing"
)

func TestFindPostRequestPrompt(t *testing.T) {
	inner := append(strField(1, "You've hit your usage limit"), strField(2, "Switch to a different model")...)
	prompt := msgField(19, inner)
	payload := msgField(1, prompt)

	title, msg, ok := findPostRequestPrompt(payload)
	if !ok {
		t.Fatal("expected post_request_prompt")
	}
	if title != "You've hit your usage limit" {
		t.Fatalf("title=%q", title)
	}
	if msg != "Switch to a different model" {
		t.Fatalf("message=%q", msg)
	}
	if classifyDataPayload(payload) != failAccount {
		t.Fatalf("expected failAccount, got %s", classifyDataPayload(payload))
	}
}

func strField(no int, s string) []byte {
	return msgField(no, []byte(s))
}

func TestClassifyStreamEnvelopeEndStream(t *testing.T) {
	payload := connectErrorFromPayload(10) // PRO_USER_USAGE_LIMIT
	if k := classifyStreamEnvelope(endStreamFlag, payload); k != failAccount {
		t.Fatalf("got %s", k)
	}
}

func TestClassifyStreamEnvelopeDelayedQuota(t *testing.T) {
	inner := append(strField(1, "Usage cap reached"), strField(2, "Your usage limits will reset next month")...)
	prompt := msgField(19, inner)
	payload := msgField(1, prompt)
	if k := classifyStreamEnvelope(0x00, payload); k != failAccount {
		t.Fatalf("got %s", k)
	}
}

func TestIsHeartbeatOnly(t *testing.T) {
	heartbeat := msgField(13, []byte{})
	payload := msgField(1, heartbeat)
	if !isHeartbeatOnly(payload) {
		t.Fatal("expected heartbeat-only payload")
	}
	text := msgField(1, msgField(1, []byte("hello")))
	if isHeartbeatOnly(text) {
		t.Fatal("text_delta must not be heartbeat-only")
	}
}

// isHeartbeatOnly reports whether payload is only an InteractionUpdate heartbeat.
func isHeartbeatOnly(payload []byte) bool {
	if len(payload) == 0 {
		return true
	}
	foundHeartbeat := false
	for _, f := range iterProtoFields(payload) {
		if f.wire != 2 {
			continue
		}
		inner := iterProtoFields(f.bytes)
		if len(inner) == 0 {
			continue
		}
		if len(inner) == 1 && inner[0].fieldNo == 13 && inner[0].wire == 2 {
			foundHeartbeat = true
			continue
		}
		return false
	}
	return foundHeartbeat
}

func TestClassifyGzipEndStreamFromCapture(t *testing.T) {
	data, err := os.ReadFile("../.dev/proxy-debug-stream/20260728-230738-0002.bin")
	if err != nil {
		t.Skip("capture not present:", err)
	}
	if len(data) < 6 {
		t.Fatal("short capture")
	}
	flags := data[0]
	payload := data[5:]
	if k := classifyStreamEnvelope(flags, payload); k != failAccount {
		t.Fatalf("got %s want account", k)
	}
}

func TestConnectPayloadForInspectGzip(t *testing.T) {
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	_, _ = zw.Write(connectErrorFromPayload(51)) // RATE_LIMITED_CHANGEABLE
	_ = zw.Close()
	payload := buf.Bytes()
	body := connectPayloadForInspect(compressFlag|endStreamFlag, payload)
	if k := classifyEndStream(body); k != failAccount {
		t.Fatalf("got %s", k)
	}
}

func TestPassthroughConnectStreamMarksOnQuota(t *testing.T) {
	heartbeat := msgField(1, msgField(13, []byte{}))
	quota := connectErrorFromPayload(10)

	var upstream bytes.Buffer
	_ = writeEnvelope(&upstream, endStreamFlag, quota)

	var client bytes.Buffer
	var marked failKind
	err := passthroughConnectStream(&client, &upstream, 0x00, heartbeat, nil, func(k failKind) {
		marked = k
	}, "/agent.v1.AgentService/Run", "pk1", "c1")
	if err != nil {
		t.Fatal(err)
	}
	if marked != failAccount {
		t.Fatalf("marked=%s want account", marked)
	}
	flags, payload, err := readEnvelope(&client)
	if err != nil || flags != 0x00 || !bytes.Equal(payload, heartbeat) {
		t.Fatalf("first frame flags=%d payload=%q err=%v", flags, payload, err)
	}
	flags, payload, err = readEnvelope(&client)
	if err != nil || flags&endStreamFlag == 0 {
		t.Fatalf("expected quota end-stream flags=%d err=%v", flags, err)
	}
	if classifyEndStream(payload) != failAccount {
		t.Fatalf("end-stream not quota: %s", payload)
	}
}

func TestPassthroughConnectStreamForwardsLiveRun(t *testing.T) {
	live := []byte{0xde, 0xad}
	var upstream bytes.Buffer
	_ = writeEnvelope(&upstream, 0x00, live)
	_ = writeEnvelope(&upstream, endStreamFlag, []byte(`{"metadata":{}}`))

	var client bytes.Buffer
	marked := false
	err := passthroughConnectStream(&client, &upstream, 0x00, live, nil, func(failKind) {
		marked = true
	}, "/agent.v1.AgentService/Run", "pk1", "c1")
	if err != nil {
		t.Fatal(err)
	}
	if marked {
		t.Fatal("live run should not mark pool")
	}
	flags, payload, err := readEnvelope(&client)
	if err != nil || flags != 0x00 || !bytes.Equal(payload, live) {
		t.Fatalf("first frame flags=%d payload=%q err=%v", flags, payload, err)
	}
}
