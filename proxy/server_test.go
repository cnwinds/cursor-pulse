package main

import (
	"bufio"
	"fmt"
	"net"
	"net/http"
	"testing"
)

func TestWriteHTTPError(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		status int
		want   string
	}{
		{http.StatusBadGateway, "502 Bad Gateway"},
		{http.StatusForbidden, "403 Forbidden"},
		{http.StatusInternalServerError, "500 Internal Server Error"},
	} {
		t.Run(tc.want, func(t *testing.T) {
			t.Parallel()
			client, server := net.Pipe()
			defer client.Close()
			done := make(chan struct{})
			go func() {
				writeHTTPError(server, tc.status)
				server.Close()
				close(done)
			}()
			resp, err := http.ReadResponse(bufio.NewReader(client), &http.Request{Method: http.MethodConnect})
			if err != nil {
				t.Fatal(err)
			}
			if resp.StatusCode != tc.status || resp.Status != tc.want {
				t.Fatalf("got %d %q want %d %q", resp.StatusCode, resp.Status, tc.status, tc.want)
			}
			if cl := resp.Header.Get("Content-Length"); cl != "0" {
				t.Fatalf("Content-Length: %q", cl)
			}
			<-done
		})
	}
}

func TestConnectAllowlistRejectsNonCursor(t *testing.T) {
	pool := NewPool([]string{"keyA"})
	ca, _, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, nil, nil)
	s.connectAllowlist = parseConnectAllowlist("*.cursor.sh,cursor.sh")

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go http.Serve(ln, s)

	conn, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprintf(conn, "CONNECT evil.example.com:443 HTTP/1.1\r\nHost: evil.example.com:443\r\n\r\n")
	resp, err := http.ReadResponse(bufio.NewReader(conn), &http.Request{Method: http.MethodConnect})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status %d want 403", resp.StatusCode)
	}
}
