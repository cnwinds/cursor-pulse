package main

import "testing"

func varint(n int) []byte {
	var b []byte
	v := uint64(n)
	for {
		c := byte(v & 0x7f)
		v >>= 7
		if v != 0 {
			c |= 0x80
		}
		b = append(b, c)
		if v == 0 {
			break
		}
	}
	return b
}

func varintField(no, value int) []byte {
	return append(varint((no<<3)|0), varint(value)...)
}

func msgField(no int, payload []byte) []byte {
	out := append(varint((no<<3)|2), varint(len(payload))...)
	return append(out, payload...)
}

func TestFindTurnEnded(t *testing.T) {
	inner := append(append(varintField(1, 1234), varintField(2, 56)...), varintField(5, 7)...)
	payload := msgField(1, msgField(14, inner))
	tok := findTurnEnded(payload)
	if tok == nil || tok.Input != 1234 || tok.Output != 56 || tok.Reasoning != 7 {
		t.Fatalf("%+v", tok)
	}
	if findTurnEnded([]byte("plain")) != nil {
		t.Fatal("expected nil")
	}
}
