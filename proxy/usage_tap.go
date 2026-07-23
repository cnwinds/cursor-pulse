package main

import (
	"encoding/binary"
	"io"
)

// usageTapWriter tees Connect envelopes to the client while best-effort
// scanning payloads for TurnEnded token counts. Parse failures never affect
// the forwarded write.
type usageTapWriter struct {
	w        io.Writer
	buf      []byte
	onTokens func(TokenCounts)
}

func (t *usageTapWriter) Write(p []byte) (int, error) {
	n, err := t.w.Write(p)
	if n > 0 {
		t.buf = append(t.buf, p[:n]...)
	}
	for {
		if len(t.buf) < 5 {
			break
		}
		size := int(binary.BigEndian.Uint32(t.buf[1:5]))
		total := 5 + size
		if size < 0 || total > len(t.buf) {
			break
		}
		payload := t.buf[5:total]
		t.buf = t.buf[total:]
		if tok := findTurnEnded(payload); tok != nil && t.onTokens != nil {
			t.onTokens(*tok)
		}
	}
	return n, err
}

// findTurnEnded best-effort extracts TurnEndedUpdate token counts from a
// protobuf payload at any nesting depth (agent.v1 InteractionUpdate field 14).
// Fields: 1 input, 2 output, 3 cache_read, 4 cache_write, 5 reasoning.
//
// Field 1 is often the inclusive input-side total
// (no_cache + cache_write + cache_read), not Dashboard inputTokens.
// Pulse canonical_turn_ended_tokens derives no_cache before storage/pricing.
func findTurnEnded(buf []byte) *TokenCounts {
	return findTurnEndedDepth(buf, 0)
}

func findTurnEndedDepth(buf []byte, depth int) *TokenCounts {
	if depth > 8 {
		return nil
	}
	for _, f := range iterProtoFields(buf) {
		if f.wire != 2 {
			continue
		}
		if f.fieldNo == 14 {
			if tok := looksLikeTurnEnded(f.bytes); tok != nil {
				return tok
			}
		}
		if nested := findTurnEndedDepth(f.bytes, depth+1); nested != nil {
			return nested
		}
	}
	return nil
}

type protoField struct {
	fieldNo int
	wire    int
	varint  uint64
	bytes   []byte
}

func iterProtoFields(buf []byte) []protoField {
	var out []protoField
	i := 0
	for i < len(buf) {
		tag, n := readUvarint(buf[i:])
		if n <= 0 {
			break
		}
		i += n
		fieldNo := int(tag >> 3)
		wire := int(tag & 7)
		if fieldNo == 0 {
			break
		}
		switch wire {
		case 0:
			v, n := readUvarint(buf[i:])
			if n <= 0 {
				return out
			}
			i += n
			out = append(out, protoField{fieldNo: fieldNo, wire: wire, varint: v})
		case 2:
			l, n := readUvarint(buf[i:])
			if n <= 0 || i+n+int(l) > len(buf) {
				return out
			}
			start := i + n
			end := start + int(l)
			out = append(out, protoField{fieldNo: fieldNo, wire: wire, bytes: buf[start:end]})
			i = end
		case 5:
			if i+4 > len(buf) {
				return out
			}
			i += 4
			out = append(out, protoField{fieldNo: fieldNo, wire: wire})
		case 1:
			if i+8 > len(buf) {
				return out
			}
			i += 8
			out = append(out, protoField{fieldNo: fieldNo, wire: wire})
		default:
			return out
		}
	}
	return out
}

func readUvarint(b []byte) (uint64, int) {
	var x uint64
	var s uint
	for i := 0; i < len(b) && i < 10; i++ {
		c := b[i]
		if c < 0x80 {
			if i == 9 && c > 1 {
				return 0, -1
			}
			return x | uint64(c)<<s, i + 1
		}
		x |= uint64(c&0x7f) << s
		s += 7
	}
	return 0, -1
}

func looksLikeTurnEnded(buf []byte) *TokenCounts {
	tok := &TokenCounts{}
	hasField1 := false
	hasField2 := false
	found := false
	for _, f := range iterProtoFields(buf) {
		if f.wire != 0 || f.fieldNo < 1 || f.fieldNo > 5 {
			return nil
		}
		found = true
		switch f.fieldNo {
		case 1:
			hasField1 = true
			tok.Input = int64(f.varint)
		case 2:
			hasField2 = true
			tok.Output = int64(f.varint)
		case 3:
			tok.CacheRead = int64(f.varint)
		case 4:
			tok.CacheWrite = int64(f.varint)
		case 5:
			tok.Reasoning = int64(f.varint)
		}
	}
	if !found {
		return nil
	}
	if !hasField1 && !hasField2 {
		return nil
	}
	return tok
}
