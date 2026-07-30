package main

import "testing"

func TestQuotaPoolForModel(t *testing.T) {
	cases := []struct {
		model string
		want  quotaPoolKind
	}{
		{"composer-2.5", quotaPoolAuto},
		{"cursor-grok-4.5-high", quotaPoolAuto},
		{"auto", quotaPoolAuto},
		{"claude-4-sonnet", quotaPoolAPI},
		{"gpt-5.6-sol-medium", quotaPoolAPI},
		{"glm-4", quotaPoolAPI},
		{"", quotaPoolUnknown},
	}
	for _, c := range cases {
		got := quotaPoolForModel(c.model)
		if got != c.want {
			t.Errorf("model %q: got %v want %v", c.model, got, c.want)
		}
	}
}
