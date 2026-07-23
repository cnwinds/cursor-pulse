package main

import (
	"net/http"
	"net/url"
	"testing"
)

func TestParseUpstreamProxy(t *testing.T) {
	u, err := parseUpstreamProxy("")
	if err != nil || u != nil {
		t.Fatalf("empty: got %v %v", u, err)
	}
	u, err = parseUpstreamProxy("http://127.0.0.1:7890")
	if err != nil || u.Host != "127.0.0.1:7890" {
		t.Fatalf("plain: %v %v", u, err)
	}
	u, err = parseUpstreamProxy("http://alice:s3cret@proxy.local:8080")
	if err != nil {
		t.Fatal(err)
	}
	if u.User.Username() != "alice" {
		t.Fatalf("user=%q", u.User.Username())
	}
	pass, ok := u.User.Password()
	if !ok || pass != "s3cret" {
		t.Fatalf("pass=%q ok=%v", pass, ok)
	}
	if _, err := parseUpstreamProxy("socks5://127.0.0.1:1080"); err == nil {
		t.Fatal("want error for socks5")
	}
}

func TestRedactUpstreamProxy(t *testing.T) {
	if got := redactUpstreamProxy(""); got != "(direct)" {
		t.Fatalf("got %q", got)
	}
	if got := redactUpstreamProxy("http://127.0.0.1:7890"); got != "http://127.0.0.1:7890" {
		t.Fatalf("got %q", got)
	}
	if got := redactUpstreamProxy("http://u:p@127.0.0.1:7890"); got != "http://***@127.0.0.1:7890" {
		t.Fatalf("got %q", got)
	}
}

func TestOutboundTransportProxyBehavior(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "https://api2.cursor.sh/x", nil)

	trDirect := newOutboundTransport(nil)
	got, err := trDirect.Proxy(req)
	if err != nil || got != nil {
		t.Fatalf("direct: want nil proxy, got %v %v", got, err)
	}

	up, _ := url.Parse("http://user:pass@127.0.0.1:7890")
	trUp := newOutboundTransport(up)
	got, err = trUp.Proxy(req)
	if err != nil || got == nil || got.Host != "127.0.0.1:7890" {
		t.Fatalf("upstream: got %v %v", got, err)
	}
	if got.User.Username() != "user" {
		t.Fatalf("user=%q", got.User.Username())
	}
}
