package main

import (
	"io"
	"sync"
)

// frameSource tees a streaming request body: every chunk read from the client
// is retained so a failed upstream attempt can be replayed from the beginning,
// while new chunks keep flowing to the current attempt.
type frameSource struct {
	mu     sync.Mutex
	cond   *sync.Cond
	chunks [][]byte
	closed bool
	err    error
}

func newFrameSource(body io.Reader) *frameSource {
	fs := &frameSource{}
	fs.cond = sync.NewCond(&fs.mu)
	go func() {
		buf := make([]byte, 64*1024)
		for {
			n, err := body.Read(buf)
			if n > 0 {
				chunk := make([]byte, n)
				copy(chunk, buf[:n])
				fs.mu.Lock()
				fs.chunks = append(fs.chunks, chunk)
				fs.cond.Broadcast()
				fs.mu.Unlock()
			}
			if err != nil {
				fs.mu.Lock()
				fs.closed = true
				fs.err = err
				fs.cond.Broadcast()
				fs.mu.Unlock()
				return
			}
		}
	}()
	return fs
}

// snapshot returns a concatenation of all chunks buffered so far.
func (fs *frameSource) snapshot() []byte {
	fs.mu.Lock()
	defer fs.mu.Unlock()
	n := 0
	for _, c := range fs.chunks {
		n += len(c)
	}
	out := make([]byte, 0, n)
	for _, c := range fs.chunks {
		out = append(out, c...)
	}
	return out
}

// reader returns a new reader over everything buffered so far plus any future
// chunks. Multiple readers may exist sequentially (one per replay attempt).
func (fs *frameSource) reader() *fsReader {
	return &fsReader{fs: fs}
}

type fsReader struct {
	fs  *frameSource
	idx int
	off int
}

func (r *fsReader) Read(p []byte) (int, error) {
	r.fs.mu.Lock()
	defer r.fs.mu.Unlock()
	for {
		if r.idx < len(r.fs.chunks) {
			c := r.fs.chunks[r.idx]
			n := copy(p, c[r.off:])
			r.off += n
			if r.off >= len(c) {
				r.idx++
				r.off = 0
			}
			return n, nil
		}
		if r.fs.closed {
			if r.fs.err == nil || r.fs.err == io.EOF {
				return 0, io.EOF
			}
			return 0, r.fs.err
		}
		r.fs.cond.Wait()
	}
}

func (r *fsReader) Close() error { return nil }
