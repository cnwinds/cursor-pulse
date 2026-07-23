package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"testing"
)

func quotaEndStreamPayload(t *testing.T, enum int) []byte {
	t.Helper()
	// ErrorDetails protobuf: field 1 (error), varint = enum → [0x08, enum]
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
	b, err := json.Marshal(ce)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestErrorDetailsEnum(t *testing.T) {
	if v, ok := errorDetailsEnum([]byte{0x08, 0x0A}); !ok || v != 10 {
		t.Fatalf("got %d, %v", v, ok)
	}
	// field 2 (length-delimited) then field 1
	msg := []byte{0x12, 0x03, 'a', 'b', 'c', 0x08, 0x32}
	if v, ok := errorDetailsEnum(msg); !ok || v != 50 {
		t.Fatalf("got %d, %v", v, ok)
	}
	if _, ok := errorDetailsEnum([]byte{0xFF}); ok {
		t.Fatal("expected failure on garbage")
	}
}

func TestClassifyEndStreamQuota(t *testing.T) {
	for _, enum := range []int{7, 8, 9, 10, 14, 22, 23, 34, 41, 44, 45, 50, 51, 54, 65} {
		if k := classifyEndStream(quotaEndStreamPayload(t, enum)); k != failAccount {
			t.Fatalf("enum %d: got %s", enum, k)
		}
	}
	for _, enum := range []int{11, 12} {
		if k := classifyEndStream(quotaEndStreamPayload(t, enum)); k != failAuth {
			t.Fatalf("enum %d: got %s", enum, k)
		}
	}
	// unknown enum with a non-quota code → none
	payload := []byte(`{"error":{"code":"invalid_argument","message":"bad","details":[{"type":"aiserver.v1.ErrorDetails","value":"CAQ="}]},"metadata":{}}`)
	if k := classifyEndStream(payload); k != failNone {
		t.Fatalf("unknown enum: got %s", k)
	}
	// unknown enum but bare resource_exhausted code → account (fallback)
	if k := classifyEndStream(quotaEndStreamPayload(t, 3)); k != failAccount {
		t.Fatalf("bare resource_exhausted: got %s", k)
	}
	// clean end (no error) → none
	if k := classifyEndStream([]byte(`{"metadata":{}}`)); k != failNone {
		t.Fatalf("clean end: got %s", k)
	}
}

func TestClassifyHTTPError(t *testing.T) {
	if k := classifyHTTPError(429, nil); k != failAccount {
		t.Fatalf("429: got %s", k)
	}
	if k := classifyHTTPError(401, nil); k != failAuth {
		t.Fatalf("401: got %s", k)
	}
	// unary JSON error with resource_exhausted, no details
	if k := classifyHTTPError(400, []byte(`{"code":"resource_exhausted","message":"x"}`)); k != failAccount {
		t.Fatalf("json resource_exhausted: got %s", k)
	}
	if k := classifyHTTPError(500, []byte("boom")); k != failNone {
		t.Fatalf("500: got %s", k)
	}
}

func TestIsNonFatalAuthPath(t *testing.T) {
	cases := []struct {
		path string
		want bool
	}{
		{"/aiserver.v1.RepositoryService/FastRepoInitHandshakeV2", true},
		{"/aiserver.v1.RepositoryService/SomeOther", true},
		{"/aiserver.v1.AgentService/Run", false},
		{"/auth/exchange_user_api_key", false},
		{"/FastRepoSomething", true},
	}
	for _, tc := range cases {
		if got := isNonFatalAuthPath(tc.path); got != tc.want {
			t.Fatalf("path %q: got %v want %v", tc.path, got, tc.want)
		}
	}
}

func TestShouldRotateOnFailure(t *testing.T) {
	path := "/aiserver.v1.RepositoryService/FastRepoInitHandshakeV2"
	if shouldRotateOnFailure(path, failAuth) {
		t.Fatal("FastRepo 401 must not rotate")
	}
	if !shouldRotateOnFailure("/aiserver.v1.AgentService/Run", failAuth) {
		t.Fatal("Agent Run 401 must rotate")
	}
	if !shouldRotateOnFailure(path, failAccount) {
		t.Fatal("FastRepo quota failure must still rotate")
	}
	if shouldRotateOnFailure(path, failNone) {
		t.Fatal("failNone must not rotate")
	}
}

func TestEnvelopeRoundTrip(t *testing.T) {
	var buf bytes.Buffer
	if err := writeEnvelope(&buf, endStreamFlag, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	flags, payload, err := readEnvelope(&buf)
	if err != nil {
		t.Fatal(err)
	}
	if flags != endStreamFlag || string(payload) != "hello" {
		t.Fatalf("got flags=%d payload=%q", flags, payload)
	}
	if _, _, err := readEnvelope(&buf); err != io.EOF {
		t.Fatalf("expected EOF, got %v", err)
	}
}

func TestFrameSourceReplay(t *testing.T) {
	// Feed a body slowly; two sequential readers must each see the full stream.
	pr, pw := io.Pipe()
	fs := newFrameSource(pr)
	go func() {
		for i := 0; i < 5; i++ {
			fmt.Fprintf(pw, "chunk%d", i)
		}
		pw.Close()
	}()

	readAll := func(r io.Reader) string {
		b, err := io.ReadAll(r)
		if err != nil {
			t.Fatal(err)
		}
		return string(b)
	}
	first := readAll(fs.reader())
	second := readAll(fs.reader())
	if first != "chunk0chunk1chunk2chunk3chunk4" || second != first {
		t.Fatalf("first=%q second=%q", first, second)
	}
}
