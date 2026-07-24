package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadEnvFileOverride(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "FOO=from-file\nBAR=quoted\n# comment\nexport BAZ=exported\nQUX=\"double\"\nQUY='single'\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("FOO", "old")
	t.Setenv("KEEP", "keep")

	if err := loadEnvFile(path, true); err != nil {
		t.Fatal(err)
	}
	if got := os.Getenv("FOO"); got != "from-file" {
		t.Fatalf("FOO=%q want from-file", got)
	}
	if got := os.Getenv("BAZ"); got != "exported" {
		t.Fatalf("BAZ=%q want exported", got)
	}
	if got := os.Getenv("QUX"); got != "double" {
		t.Fatalf("QUX=%q want double", got)
	}
	if got := os.Getenv("QUY"); got != "single" {
		t.Fatalf("QUY=%q want single", got)
	}
	if got := os.Getenv("KEEP"); got != "keep" {
		t.Fatalf("KEEP=%q want keep", got)
	}
}

func TestLoadEnvFileNoOverride(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	if err := os.WriteFile(path, []byte("FOO=from-file\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FOO", "old")
	if err := loadEnvFile(path, false); err != nil {
		t.Fatal(err)
	}
	if got := os.Getenv("FOO"); got != "old" {
		t.Fatalf("FOO=%q want old", got)
	}
}

func TestBootstrapDockerDotenvPrefersProxyPulseBaseURL(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "PULSE_BASE_URL=http://web:8080\nPROXY_PULSE_BASE_URL=http://host.docker.internal:8080\nPULSE_INTERNAL_SERVICE_TOKEN=new-token\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("PULSE_BASE_URL", "http://compose-injected:8080")
	t.Setenv("PULSE_INTERNAL_SERVICE_TOKEN", "old-token")

	orig := dockerEnvPathForTest
	dockerEnvPathForTest = path
	t.Cleanup(func() { dockerEnvPathForTest = orig })

	bootstrapDockerDotenv()
	if got := os.Getenv("PULSE_BASE_URL"); got != "http://host.docker.internal:8080" {
		t.Fatalf("PULSE_BASE_URL=%q", got)
	}
	if got := os.Getenv("PULSE_INTERNAL_SERVICE_TOKEN"); got != "new-token" {
		t.Fatalf("token=%q", got)
	}
}

func TestBootstrapDockerDotenvKeepsComposePulseURL(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "PULSE_BASE_URL=http://web:8080\nPULSE_INTERNAL_SERVICE_TOKEN=new-token\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("PULSE_BASE_URL", "http://host.docker.internal:8080")
	t.Setenv("PULSE_INTERNAL_SERVICE_TOKEN", "old-token")

	orig := dockerEnvPathForTest
	dockerEnvPathForTest = path
	t.Cleanup(func() { dockerEnvPathForTest = orig })

	bootstrapDockerDotenv()
	if got := os.Getenv("PULSE_BASE_URL"); got != "http://host.docker.internal:8080" {
		t.Fatalf("PULSE_BASE_URL=%q want compose-injected host.docker.internal", got)
	}
	if got := os.Getenv("PULSE_INTERNAL_SERVICE_TOKEN"); got != "new-token" {
		t.Fatalf("token=%q", got)
	}
}
