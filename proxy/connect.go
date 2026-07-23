package main

import (
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"io"
	"strings"
)

// Connect streaming envelope: 1 byte flags + 4 byte big-endian length.
const endStreamFlag = 0x02

func readEnvelope(r io.Reader) (flags byte, payload []byte, err error) {
	var hdr [5]byte
	if _, err := io.ReadFull(r, hdr[:]); err != nil {
		return 0, nil, err
	}
	n := binary.BigEndian.Uint32(hdr[1:])
	payload = make([]byte, n)
	if _, err := io.ReadFull(r, payload); err != nil {
		return 0, nil, err
	}
	return hdr[0], payload, nil
}

func writeEnvelope(w io.Writer, flags byte, payload []byte) error {
	var hdr [5]byte
	hdr[0] = flags
	binary.BigEndian.PutUint32(hdr[1:], uint32(len(payload)))
	if _, err := w.Write(hdr[:]); err != nil {
		return err
	}
	_, err := w.Write(payload)
	return err
}

type connectErrorJSON struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details []struct {
		Type  string `json:"type"`
		Value string `json:"value"`
	} `json:"details"`
}

type endStreamJSON struct {
	Error *connectErrorJSON `json:"error"`
}

type failKind int

const (
	failNone    failKind = iota // not an account problem — forward as-is
	failAccount                 // quota / rate-limit / blocked — rotate key and replay
	failAuth                    // token rejected — rotate key and replay
)

func (k failKind) String() string {
	switch k {
	case failAccount:
		return "account"
	case failAuth:
		return "auth"
	default:
		return "none"
	}
}

// isNonFatalAuthPath reports paths where HTTP 401/403 must not burn a pool key.
// FastRepo / Repository indexing can fail auth while Agent Run still works.
func isNonFatalAuthPath(path string) bool {
	return strings.Contains(path, "FastRepo") || strings.Contains(path, "RepositoryService")
}

// shouldRotateOnFailure decides whether a classified upstream failure should
// mark/rotate the current pool credential.
func shouldRotateOnFailure(path string, kind failKind) bool {
	if kind == failNone {
		return false
	}
	if kind == failAuth && isNonFatalAuthPath(path) {
		return false
	}
	return true
}

// aiserver.v1.ErrorDetails.Error values that mean "this account is done" —
// rotate to the next key. All other codes pass through untouched.
var accountErrorEnums = map[int]bool{
	7:  true, // FREE_USER_RATE_LIMIT_EXCEEDED
	8:  true, // PRO_USER_RATE_LIMIT_EXCEEDED
	9:  true, // FREE_USER_USAGE_LIMIT
	10: true, // PRO_USER_USAGE_LIMIT
	14: true, // OPENAI_RATE_LIMIT_EXCEEDED
	22: true, // GENERIC_RATE_LIMIT_EXCEEDED
	23: true, // PRO_USER_ONLY
	34: true, // API_KEY_RATE_LIMIT
	41: true, // RESOURCE_EXHAUSTED
	44: true, // USAGE_PRICING_REQUIRED
	45: true, // USAGE_PRICING_REQUIRED_CHANGEABLE
	50: true, // RATE_LIMITED
	51: true, // RATE_LIMITED_CHANGEABLE
	54: true, // SUSPICIOUS_USAGE_BLOCKED
	65: true, // ACCOUNT_CLOSED
}

var authErrorEnums = map[int]bool{
	11: true, // AUTH_TOKEN_NOT_FOUND
	12: true, // AUTH_TOKEN_EXPIRED
}

func classifyErrorJSON(ce *connectErrorJSON) failKind {
	if ce == nil {
		return failNone
	}
	for _, d := range ce.Details {
		if !hasSuffixFold(d.Type, "ErrorDetails") {
			continue
		}
		raw, err := base64.StdEncoding.DecodeString(d.Value)
		if err != nil {
			continue
		}
		if enum, ok := errorDetailsEnum(raw); ok {
			if accountErrorEnums[enum] {
				return failAccount
			}
			if authErrorEnums[enum] {
				return failAuth
			}
		}
	}
	// No parseable details: fall back to the Connect code.
	switch ce.Code {
	case "resource_exhausted":
		return failAccount
	case "unauthenticated":
		return failAuth
	}
	return failNone
}

// classifyHTTPError classifies a non-200 response (body already read).
func classifyHTTPError(status int, body []byte) failKind {
	if status == 429 {
		return failAccount
	}
	if status == 401 || status == 403 {
		return failAuth
	}
	var ce connectErrorJSON
	if err := json.Unmarshal(body, &ce); err == nil && ce.Code != "" {
		return classifyErrorJSON(&ce)
	}
	return failNone
}

// classifyEndStream classifies the payload of an end-stream envelope
// (HTTP 200 streaming error path).
func classifyEndStream(payload []byte) failKind {
	var es endStreamJSON
	if err := json.Unmarshal(payload, &es); err != nil || es.Error == nil {
		return failNone
	}
	return classifyErrorJSON(es.Error)
}

// errorDetailsEnum extracts field 1 (varint enum "error") from a serialized
// aiserver.v1.ErrorDetails protobuf message using a minimal wire-format walk.
func errorDetailsEnum(b []byte) (int, bool) {
	i := 0
	for i < len(b) {
		tag, n := binary.Uvarint(b[i:])
		if n <= 0 {
			return 0, false
		}
		i += n
		field, wire := tag>>3, tag&7
		switch wire {
		case 0: // varint
			v, n := binary.Uvarint(b[i:])
			if n <= 0 {
				return 0, false
			}
			i += n
			if field == 1 {
				return int(v), true
			}
		case 2: // length-delimited
			l, n := binary.Uvarint(b[i:])
			if n <= 0 || i+n+int(l) > len(b) {
				return 0, false
			}
			i += n + int(l)
		case 5: // 32-bit
			if i+4 > len(b) {
				return 0, false
			}
			i += 4
		case 1: // 64-bit
			if i+8 > len(b) {
				return 0, false
			}
			i += 8
		default:
			return 0, false
		}
	}
	return 0, false
}

func hasSuffixFold(s, suffix string) bool {
	if len(s) < len(suffix) {
		return false
	}
	return equalFold(s[len(s)-len(suffix):], suffix)
}

func equalFold(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := 0; i < len(a); i++ {
		ca, cb := a[i], b[i]
		if 'A' <= ca && ca <= 'Z' {
			ca += 'a' - 'A'
		}
		if 'A' <= cb && cb <= 'Z' {
			cb += 'a' - 'A'
		}
		if ca != cb {
			return false
		}
	}
	return true
}
