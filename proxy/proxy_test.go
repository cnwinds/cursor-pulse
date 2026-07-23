package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// fakeUpstream emulates api2.cursor.sh: the token exchange endpoint plus a
// streaming agent endpoint that fails with a quota error for key A and
// succeeds for key B.
type fakeUpstream struct {
	*httptest.Server
	runCalls   atomic.Int32
	unaryCalls atomic.Int32
}

func newFakeUpstream(t *testing.T) *fakeUpstream {
	t.Helper()
	fu := &fakeUpstream{}
	mux := http.NewServeMux()

	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if key != "keyA" && key != "keyB" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "tok" + key[len(key)-1:], // tokA / tokB (opaque; pool falls back to 30min exp)
			"refreshToken": "r",
		})
	})

	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		fu.runCalls.Add(1)
		w.Header().Set("Content-Type", "application/connect+proto")
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if tok == "tokA" {
			// Quota error as HTTP-200 end-stream envelope with ErrorDetails.
			raw := []byte{0x08, 0x0A} // field 1 varint = 10 (PRO_USER_USAGE_LIMIT)
			payload := fmt.Sprintf(`{"error":{"code":"resource_exhausted","message":"out of quota","details":[{"type":"aiserver.v1.ErrorDetails","value":%q}]},"metadata":{}}`,
				base64.StdEncoding.EncodeToString(raw))
			writeEnvelope(w, endStreamFlag, []byte(payload))
			return
		}
		if tok != "tokB" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		writeEnvelope(w, 0x00, []byte{0xde, 0xad, 0xbe, 0xef})
		w.(http.Flusher).Flush()
		writeEnvelope(w, endStreamFlag, []byte(`{"metadata":{}}`))
	})

	mux.HandleFunc("/aiserver.v1.TestService/Unary", func(w http.ResponseWriter, r *http.Request) {
		fu.unaryCalls.Add(1)
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if tok == "tokA" {
			w.WriteHeader(http.StatusTooManyRequests)
			io.WriteString(w, `{"code":"resource_exhausted","message":"slow down"}`)
			return
		}
		w.Header().Set("Content-Type", "application/proto")
		w.Write([]byte{0x01, 0x02, 0x03})
	})

	srv := httptest.NewUnstartedServer(mux)
	srv.EnableHTTP2 = true
	srv.StartTLS()
	fu.Server = srv
	t.Cleanup(srv.Close)
	return fu
}

// newTestProxy starts the proxy against the fake upstream and returns its
// address plus the PEM of its CA for the test client to trust.
func newTestProxy(t *testing.T, fu *fakeUpstream) (addr string, caPEM []byte) {
	t.Helper()
	pool := NewPool([]string{"keyA", "keyB"})
	pool.exchangeBase = fu.URL
	pool.client = fu.Client()

	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	// sessions=nil skips JWT gate so baseline rotation tests need no Pulse exchange.
	s := NewServer(pool, ca, nil, nil)
	s.shouldMITM = func(string) bool { return true }
	s.transport = &http.Transport{
		ForceAttemptHTTP2: true,
		TLSClientConfig:   &tls.Config{InsecureSkipVerify: true},
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, s)
	t.Cleanup(func() { ln.Close() })

	pemBytes, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatal(err)
	}
	return ln.Addr().String(), pemBytes
}

// connectClient returns an HTTP client that dials addr through the CONNECT
// proxy at proxyAddr, trusting proxyCA.
func connectClient(t *testing.T, proxyAddr string, caPEM []byte) *http.Client {
	t.Helper()
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		t.Fatal("bad CA PEM")
	}
	return &http.Client{
		Timeout: 30 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{RootCAs: roots},
			DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
				conn, err := (&net.Dialer{}).DialContext(ctx, "tcp", proxyAddr)
				if err != nil {
					return nil, err
				}
				fmt.Fprintf(conn, "CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n", addr, addr)
				br := bufio.NewReader(conn)
				resp, err := http.ReadResponse(br, &http.Request{Method: "CONNECT"})
				if err != nil {
					conn.Close()
					return nil, err
				}
				if resp.StatusCode != http.StatusOK {
					conn.Close()
					return nil, fmt.Errorf("CONNECT failed: %s", resp.Status)
				}
				return conn, nil
			},
		},
	}
}

func TestEndToEndRotationStreaming(t *testing.T) {
	fu := newFakeUpstream(t)
	proxyAddr, caPEM := newTestProxy(t, fu)
	client := connectClient(t, proxyAddr, caPEM)

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	body := []byte{0x00, 0x00, 0x00, 0x00, 0x02, 0xAA, 0xBB} // one data envelope
	resp, err := client.Post(
		"https://"+upstreamAddr+"/agent.v1.AgentService/Run",
		"application/connect+proto",
		bytes.NewReader(body),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d", resp.StatusCode)
	}
	// First envelope must be the success data frame (0xdeadbeef), proving the
	// quota error from key A was swallowed and the request replayed with key B.
	flags, payload, err := readEnvelope(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	if flags != 0x00 || !bytes.Equal(payload, []byte{0xde, 0xad, 0xbe, 0xef}) {
		t.Fatalf("unexpected first envelope flags=%d payload=%x", flags, payload)
	}
	if got := fu.runCalls.Load(); got != 2 {
		t.Fatalf("expected 2 upstream Run calls (A then B), got %d", got)
	}
}

func TestEndToEndRotationUnary429(t *testing.T) {
	fu := newFakeUpstream(t)
	proxyAddr, caPEM := newTestProxy(t, fu)
	client := connectClient(t, proxyAddr, caPEM)

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	resp, err := client.Post(
		"https://"+upstreamAddr+"/aiserver.v1.TestService/Unary",
		"application/proto",
		bytes.NewReader([]byte{0x0A}),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d", resp.StatusCode)
	}
	b, _ := io.ReadAll(resp.Body)
	if !bytes.Equal(b, []byte{0x01, 0x02, 0x03}) {
		t.Fatalf("unexpected body %x", b)
	}
	if got := fu.unaryCalls.Load(); got != 2 {
		t.Fatalf("expected 2 upstream unary calls, got %d", got)
	}
}
