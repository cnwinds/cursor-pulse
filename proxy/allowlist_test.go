package main

import (
	"testing"
)

func TestParseConnectAllowlist(t *testing.T) {
	got := parseConnectAllowlist("")
	if len(got) != 2 || got[0] != "*.cursor.sh" || got[1] != "cursor.sh" {
		t.Fatalf("default: %#v", got)
	}
	got = parseConnectAllowlist(" example.com , *.example.com ")
	if len(got) != 2 || got[0] != "example.com" || got[1] != "*.example.com" {
		t.Fatalf("custom: %#v", got)
	}
}

func TestMatchHostPattern(t *testing.T) {
	cases := []struct {
		host, pattern string
		want          bool
	}{
		{"api2.cursor.sh", "*.cursor.sh", true},
		{"cursor.sh", "*.cursor.sh", true},
		{"evil.cursor.sh.attacker.com", "*.cursor.sh", false},
		{"cursor.sh", "cursor.sh", true},
		{"api2.cursor.sh", "cursor.sh", false},
		{"example.com", "example.com", true},
		{"sub.example.com", "*.example.com", true},
	}
	for _, tc := range cases {
		if got := matchHostPattern(tc.host, tc.pattern); got != tc.want {
			t.Fatalf("host %q pattern %q: got %v want %v", tc.host, tc.pattern, got, tc.want)
		}
	}
}

func TestHostAllowed(t *testing.T) {
	patterns := parseConnectAllowlist("*.cursor.sh,cursor.sh")
	for _, host := range []string{"api2.cursor.sh:443", "cursor.sh", "foo.bar.cursor.sh:8443"} {
		if !hostAllowed(host, patterns) {
			t.Fatalf("expected allow: %q", host)
		}
	}
	for _, host := range []string{"example.com:443", "evil.cursor.sh.attacker.com"} {
		if hostAllowed(host, patterns) {
			t.Fatalf("expected deny: %q", host)
		}
	}
}
