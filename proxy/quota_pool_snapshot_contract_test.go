package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

type snapshotContractCase struct {
	Name       string             `json:"name"`
	AutoPct    *float64           `json:"auto_pct"`
	ApiPct     *float64           `json:"api_pct"`
	IntakeOK   bool               `json:"intake_ok"`
	SnapshotOK map[string]bool    `json:"snapshot_ok"`
}

func TestQuotaPoolSnapshotContract(t *testing.T) {
	path := filepath.Join("..", "tests", "fixtures", "quota_pool_snapshot_contract.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read contract fixture: %v", err)
	}
	var cases []snapshotContractCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatalf("parse contract fixture: %v", err)
	}
	if len(cases) == 0 {
		t.Fatal("contract fixture empty")
	}
	for _, c := range cases {
		if got := snapshotIntakeOK(c.AutoPct, c.ApiPct); got != c.IntakeOK {
			t.Errorf("%s: intake_ok want %v got %v", c.Name, c.IntakeOK, got)
		}
		e := &keyEntry{credentialQuotaState: credentialQuotaState{autoPct: c.AutoPct, apiPct: c.ApiPct}}
		checks := map[string]quotaPoolKind{
			"unknown": quotaPoolUnknown,
			"auto":    quotaPoolAuto,
			"api":     quotaPoolAPI,
		}
		for name, pool := range checks {
			got := e.snapshotQuotaOK(pool)
			want, ok := c.SnapshotOK[name]
			if !ok {
				t.Fatalf("%s: missing snapshot_ok.%s", c.Name, name)
			}
			if got != want {
				t.Errorf("%s: snapshot_ok[%s] want %v got %v", c.Name, name, want, got)
			}
		}
	}
}
