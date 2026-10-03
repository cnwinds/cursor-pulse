package main

import "context"

// Tunnel keys arrive on the shared main port via Proxy-Authorization userinfo
// (pkide_ / cr*). The CONNECT handler stashes the key on the request context
// so MITM can TOFU-bind IDE sessions.

type tunnelKeySource string

const tunnelKeySourceUserinfo tunnelKeySource = "userinfo"

type tunnelKeyCtx struct {
	key    string
	source tunnelKeySource
}

type tunnelKeyCtxKey struct{}

func withTunnelKey(ctx context.Context, key string, source tunnelKeySource) context.Context {
	return context.WithValue(ctx, tunnelKeyCtxKey{}, tunnelKeyCtx{key: key, source: source})
}

func tunnelKeyFromCtx(ctx context.Context) (string, tunnelKeySource) {
	v, ok := ctx.Value(tunnelKeyCtxKey{}).(tunnelKeyCtx)
	if !ok {
		return "", ""
	}
	return v.key, v.source
}
