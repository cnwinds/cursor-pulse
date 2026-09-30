package main

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// quotaPoolKind identifies which Cursor included-usage bucket a request draws from.
// There are exactly two. A request that names no model is auto.
type quotaPoolKind int

const (
	quotaPoolAuto quotaPoolKind = iota // Auto + Composer (+ grok), and every request without a model
	quotaPoolAPI                       // premium / named API models (+ Cursor catalog GLM)
)

func normalizeCursorModel(model string) string {
	m := strings.ToLower(strings.TrimSpace(model))
	if strings.HasPrefix(m, "cursor-") {
		return m[len("cursor-"):]
	}
	return m
}

func isAutoComposerModel(model string) bool {
	n := normalizeCursorModel(model)
	return n == "auto" || n == "default" ||
		strings.HasPrefix(n, "composer") ||
		strings.HasPrefix(n, "grok")
}

func isThirdPartyModel(model string) bool {
	m := strings.ToLower(strings.TrimSpace(model))
	if m == "" {
		return false
	}
	markers := []string{
		"glm", "minimax", "deepseek", "qwen", "kimi",
		"moonshot", "doubao", "baichuan",
	}
	for _, marker := range markers {
		if strings.Contains(m, marker) {
			return true
		}
	}
	return false
}

// isLikelyByokModel guesses USER_API_KEY / self-hosted third-party from the
// model string when kind is unavailable (MITM request). Keep in sync with
// pulse/pricing/billing_scope.py is_likely_byok_model. Cursor catalog slugs
// like glm-5.2-high are lowercase-with-hyphens and consume the API quota pool.
func isLikelyByokModel(model string) bool {
	text := strings.TrimSpace(model)
	if text == "" || !isThirdPartyModel(text) {
		return false
	}
	if text == strings.ToLower(text) && strings.Contains(text, "-") {
		return false
	}
	return true
}

// quotaPoolForModel maps a billed model to the Pulse auto_pct vs api_pct bucket.
// No model and BYOK models (user's own key, no Cursor quota) are auto.
func quotaPoolForModel(model string) quotaPoolKind {
	if model == "" || isAutoComposerModel(model) || isLikelyByokModel(model) {
		return quotaPoolAuto
	}
	return quotaPoolAPI
}

func isAgentRunPath(path string) bool {
	return strings.Contains(path, "AgentService/Run")
}

func resolveQuotaPool(ctx context.Context, path string, bodySnap func() []byte, streamFS *frameSource) quotaPoolKind {
	if !isAgentRunPath(path) || bodySnap == nil {
		return quotaPoolAuto
	}
	if streamFS != nil {
		deadline := time.Now().Add(streamModelWaitTimeout)
		if ctx != nil {
			if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
				deadline = d
			}
		}
		streamFS.waitForRunnableModel(deadline)
	}
	return quotaPoolForModel(findModelName(bodySnap()))
}

// effectiveMarkQuotaPool re-parses the request body at failure time when the
// model had not arrived at selection time (slow stream body defaulted to auto).
func effectiveMarkQuotaPool(path string, bodySnap func() []byte, initial quotaPoolKind) quotaPoolKind {
	if initial == quotaPoolAPI || bodySnap == nil || !isAgentRunPath(path) {
		return initial
	}
	return quotaPoolForModel(findModelName(bodySnap()))
}

func formatSnapshotPct(p *float64) string {
	if p == nil {
		return "?"
	}
	return fmt.Sprintf("%.1f%%", *p)
}

func (k quotaPoolKind) String() string {
	if k == quotaPoolAPI {
		return "api"
	}
	return "auto"
}
