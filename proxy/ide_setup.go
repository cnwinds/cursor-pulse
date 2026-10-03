package main

import (
	"log"
	"net/http"
	"os"
	"strings"
)

// serveCAPEM serves the MITM root CA so member machines can install trust with
// nothing but the proxy address (see serveSetupScript).
func (s *Server) serveCAPEM(w http.ResponseWriter) {
	if s.caPEMPath == "" {
		http.Error(w, "CA path not configured", http.StatusServiceUnavailable)
		return
	}
	pem, err := os.ReadFile(s.caPEMPath)
	if err != nil {
		log.Printf("[ide] read CA %s: %v", s.caPEMPath, err)
		http.Error(w, "CA unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/x-pem-file")
	w.Header().Set("Content-Disposition", "attachment; filename=cursor-pulse-proxy-ca.pem")
	w.Write(pem)
}

// serveSetupScript emits a PowerShell one-liner target that installs the proxy
// CA into the current-user trusted roots and points the local Cursor IDE at
// this proxy. The address is taken from the Host header the member actually
// used, so the same endpoint works for LAN-shared and local deployments. The
// member passes their assigned Proxy Key via -Key; the script then fetches a
// dedicated per-key IDE port from GET /ide-port so IDE usage attributes to
// that key (the same pk_ key their agent CLI uses).
func (s *Server) serveSetupScript(w http.ResponseWriter, r *http.Request) {
	host := sanitizeHostHeader(r)
	if host == "127.0.0.1" && r.Host == "" {
		host = "127.0.0.1:8317"
	}
	script := strings.ReplaceAll(cursorIDESetupScriptTemplate, "__PROXY_ADDR__", "http://"+host)
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Write([]byte(script))
}

// cursorIDESetupScriptTemplate is emitted at GET /setup-cursor.ps1. It must run
// on Windows PowerShell 5.1 (no PS7-only features, no backticks so it survives
// being embedded in a Go raw string). Keep it idempotent and always back up
// settings.json before touching it.
const cursorIDESetupScriptTemplate = `param(
    [string]$Key = '',
    [string]$Proxy = '__PROXY_ADDR__'
)
# cursor-pulse IDE onboarding - served by cursor-pulse-proxy at __PROXY_ADDR__
# With -Key (Proxy Key, pk_/pka_): IDE traffic gets a dedicated port and
#   attributes to that key - the same key your agent CLI uses.
# Without -Key: IDE traffic uses the proxy main port (server-wide IDE key).
# Idempotent: safe to re-run. Rollback notes are printed at the end.
$ErrorActionPreference = 'Stop'
$addr = $Proxy

Write-Host '[1/3] Installing proxy CA into current-user trusted roots...'
$caFile = Join-Path $env:TEMP 'cursor-pulse-proxy-ca.pem'
Invoke-WebRequest -UseBasicParsing ($addr + '/ca.pem') -OutFile $caFile
$store = certutil -user -store Root 2>$null | Out-String
if ($store -match 'cursor-quota-proxy') {
    Write-Host '  CA already trusted, skipping.'
} else {
    $null = certutil -user -addstore Root $caFile
    Write-Host '  CA installed into user trusted roots.'
}

Write-Host '[2/3] Configuring Cursor IDE settings...'
$settingsPath = Join-Path $env:APPDATA 'Cursor\User\settings.json'
if (-not (Test-Path $settingsPath)) {
    throw "Cursor settings not found at $settingsPath - start Cursor once, then re-run."
}
$backup = "$settingsPath.bak-cursor-pulse"
if (-not (Test-Path $backup)) { Copy-Item $settingsPath $backup }
$raw = Get-Content $settingsPath -Raw -Encoding UTF8
try {
    $cfg = $raw | ConvertFrom-Json
} catch {
    throw ('settings.json is not strict JSON (comments or trailing commas). Edit manually: set http.proxy to your dedicated port and cursor.general.disableHttp2 to true')
}
$cfg | Add-Member -NotePropertyName 'http.proxy' -NotePropertyValue $addr -Force
$cfg | Add-Member -NotePropertyName 'cursor.general.disableHttp2' -NotePropertyValue $true -Force
$cfg | Add-Member -NotePropertyName 'http.systemCertificates' -NotePropertyValue $true -Force
$json = $cfg | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  http.proxy=$addr, disableHttp2=true written (backup: $backup)."

Write-Host '[3/3] Checking Cursor IDE...'
if ($Key -ne '') {
    $scheme = 'http'
    if ($addr -like 'https://*') { $scheme = 'https' }
    $mainHost = ($addr -replace '^https?://', '')
    $portResp = Invoke-RestMethod -UseBasicParsing ($scheme + '://' + $mainHost + '/ide-port?key=' + [System.Uri]::EscapeDataString($Key))
    $addr = $portResp.proxy_host + ':' + $portResp.port
    $raw = Get-Content $settingsPath -Raw -Encoding UTF8
    $cfg = $raw | ConvertFrom-Json
    $cfg | Add-Member -NotePropertyName 'http.proxy' -NotePropertyValue $addr -Force
    $json = $cfg | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host ("  per-key IDE port allocated: {0} (attributed to your key)" -f $addr)
}
$cursorRunning = Get-Process Cursor -ErrorAction SilentlyContinue
if ($cursorRunning) {
    Write-Host '  Cursor is running. Fully quit it (all windows) and start it again to apply.'
} else {
    $cursorExe = "$env:LOCALAPPDATA\Programs\cursor\Cursor.exe"
    if (-not (Test-Path $cursorExe)) { $cursorExe = "$env:ProgramFiles\cursor\Cursor.exe" }
    if (Test-Path $cursorExe) {
        Start-Process $cursorExe
        Write-Host '  Cursor started. Sign in and send a chat message to verify.'
    } else {
        Write-Host '  Cursor.exe not found in default locations - start Cursor manually.'
    }
}
Write-Host 'Done. AI traffic is now served from the team credential pool.'
Write-Host 'Rollback: restore the .bak-cursor-pulse file over settings.json and run:'
Write-Host '  certutil -user -delstore Root <thumbprint of cursor-quota-proxy CA>'
`
