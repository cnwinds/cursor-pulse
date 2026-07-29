package main

import (
	"bytes"
	"encoding/hex"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"
)

var (
	debugStream    = strings.TrimSpace(os.Getenv("PROXY_DEBUG_STREAM"))
	debugStreamDir = strings.TrimSpace(os.Getenv("PROXY_DEBUG_STREAM_DIR"))
	debugStreamSeq atomic.Uint64
)

func debugStreamEnabled() bool {
	v := strings.ToLower(debugStream)
	return v == "1" || v == "true" || v == "yes" || v == "on"
}

type httpFlusher interface {
	Flush()
}

// passthroughConnectStream forwards every Connect frame to the client immediately
// while scanning for quota/auth failures. onFailure is invoked at most once so the
// pool can advance; the current response is still returned to the CLI as-is.
func passthroughConnectStream(
	w io.Writer,
	r io.Reader,
	firstFlags byte,
	firstPayload []byte,
	onTokens func(TokenCounts),
	onFailure func(failKind),
	path, proxyKeyID, credID string,
) error {
	reported := false
	reportFailure := func(kind failKind) {
		if reported || !shouldMarkOnFailure(path, kind) {
			return
		}
		reported = true
		if onFailure != nil {
			onFailure(kind)
		}
	}

	flush := func() {
		if f, ok := w.(httpFlusher); ok {
			f.Flush()
		}
	}

	writeFrame := func(flags byte, payload []byte) error {
		if debugStreamEnabled() {
			dumpStreamFrame(path, proxyKeyID, credID, flags, payload)
		}
		reportFailure(classifyStreamEnvelope(flags, payload))
		if err := writeEnvelope(w, flags, payload); err != nil {
			return err
		}
		flush()
		return nil
	}

	process := func(flags byte, payload []byte) error {
		if tok := findTurnEnded(payload); tok != nil && onTokens != nil {
			onTokens(*tok)
		}
		if err := writeFrame(flags, payload); err != nil {
			return err
		}
		return nil
	}

	if err := process(firstFlags, firstPayload); err != nil {
		return err
	}
	if firstFlags&endStreamFlag != 0 {
		return nil
	}

	for {
		flags, payload, err := readEnvelope(r)
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if err := process(flags, payload); err != nil {
			return err
		}
		if flags&endStreamFlag != 0 {
			return nil
		}
	}
}

func dumpStreamFrame(path, proxyKeyID, credID string, flags byte, payload []byte) {
	dir := debugStreamDir
	if dir == "" {
		home, _ := os.UserHomeDir()
		dir = filepath.Join(home, ".cursor-quota-proxy", "debug-stream")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		log.Printf("[stream-debug] mkdir %s: %v", dir, err)
		return
	}
	seq := debugStreamSeq.Add(1)
	stamp := time.Now().UTC().Format("20060102-150405")
	base := filepath.Join(dir, fmt.Sprintf("%s-%04d", stamp, seq))
	binPath := base + ".bin"
	txtPath := base + ".txt"

	var frame bytes.Buffer
	if err := writeEnvelope(&frame, flags, payload); err != nil {
		log.Printf("[stream-debug] encode frame: %v", err)
		return
	}
	if err := os.WriteFile(binPath, frame.Bytes(), 0o600); err != nil {
		log.Printf("[stream-debug] write bin: %v", err)
		return
	}

	kind := classifyStreamEnvelope(flags, payload)
	title, msg, hasPrompt := findPostRequestPrompt(connectPayloadForInspect(flags, payload))
	var b strings.Builder
	fmt.Fprintf(&b, "path=%s\nproxy_key=%s\ncred=%s\nflags=0x%02x\npayload_len=%d\nclassified=%s\n",
		path, proxyKeyID, credID, flags, len(payload), kind)
	if hasPrompt {
		fmt.Fprintf(&b, "post_request_prompt_title=%q\npost_request_prompt_message=%q\n", title, msg)
	}
	b.WriteString("\n=== payload head hex (256B) ===\n")
	nh := 256
	if len(payload) < nh {
		nh = len(payload)
	}
	b.WriteString(hex.EncodeToString(payload[:nh]))
	b.WriteByte('\n')
	if err := os.WriteFile(txtPath, []byte(b.String()), 0o600); err != nil {
		log.Printf("[stream-debug] write txt: %v", err)
	} else {
		log.Printf("[stream-debug] dumped %s and %s classified=%s", binPath, txtPath, kind)
	}
}
