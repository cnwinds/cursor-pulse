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

// serveScript emits a PowerShell onboarding/offboarding template with
// __PROXY_ADDR__ set to the Host header the member actually used, so the same
// endpoint works for LAN-shared and local deployments.
func serveScript(w http.ResponseWriter, r *http.Request, tmpl string) {
	raw := strings.TrimSpace(r.Host)
	host := ""
	switch {
	case raw == "":
		host = "127.0.0.1:8317"
	case isSafeProxyHost(raw):
		host = raw
	default:
		// Refuse rather than embed attacker-controlled Host into PowerShell.
		http.Error(w, "invalid Host header", http.StatusBadRequest)
		return
	}
	script := strings.ReplaceAll(tmpl, "__PROXY_ADDR__", "http://"+host)
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Write([]byte(script))
}

// cursorIDEUninstallScriptTemplate is emitted at GET /uninstall-cursor.ps1:
// the offboarding counterpart of the setup template. Same constraints: PS 5.1
// compatible, no backticks, idempotent. Local cleanup (settings + CA) must
// proceed even when the proxy is unreachable.
const cursorIDEUninstallScriptTemplate = `param(
    [string]$Key = '',
    [string]$Proxy = '__PROXY_ADDR__'
)
# cursor-pulse IDE offboarding - reverses setup-cursor.ps1 (its counterpart;
# the two scripts must stay in sync about which settings keys are managed):
# removes http.proxy and related settings, removes the proxy CA from the
# current-user trusted roots, and (legacy) releases a dedicated IDE port when
# the old http.proxy pointed at a non-main port.
# Idempotent: safe to re-run. The pre-install backup is kept untouched.
$ErrorActionPreference = 'Stop'
$addr = $Proxy
$scheme = 'http'
if ($addr -like 'https://*') { $scheme = 'https' }
$mainHost = ($addr -replace '^https?://', '')

Write-Host '[1/4] Checking for legacy dedicated IDE port...'
$settingsPath = Join-Path $env:APPDATA 'Cursor\User\settings.json'
$legacyPortRelease = $false
if (Test-Path $settingsPath) {
    $rawCheck = Get-Content $settingsPath -Raw -Encoding UTF8
    try {
        $cfgCheck = $rawCheck | ConvertFrom-Json
        if ($cfgCheck.PSObject.Properties['http.proxy']) {
            $oldProxy = [string]$cfgCheck.'http.proxy'
            if ($oldProxy -match ':(\d+)(?:/|$)') {
                $oldPort = [int]$Matches[1]
                $mainPort = 8317
                if ($mainHost -match ':(\d+)$') { $mainPort = [int]$Matches[1] }
                if ($oldPort -ne $mainPort) { $legacyPortRelease = $true }
            }
        }
    } catch { }
}
if ($legacyPortRelease -and $Key -ne '') {
    try {
        $rel = Invoke-WebRequest -UseBasicParsing -Method Delete -Uri ($scheme + '://' + $mainHost + '/ide-port?key=' + [System.Uri]::EscapeDataString($Key))
        Write-Host ("  legacy dedicated port released (HTTP {0})." -f $rel.StatusCode)
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($code -eq 404) { Write-Host '  no legacy port was allocated for this key.' }
        else { Write-Host '  proxy unreachable - continuing with local cleanup.' }
    }
} else {
    Write-Host '  no legacy dedicated port to release.'
}

Write-Host '[2/4] Removing Cursor IDE proxy settings...'
if (Test-Path $settingsPath) {
    $raw = Get-Content $settingsPath -Raw -Encoding UTF8
    try {
        $cfg = $raw | ConvertFrom-Json
    } catch {
        throw ('settings.json is not strict JSON - remove http.proxy / cursor.general.disableHttp2 / http.systemCertificates manually: ' + $settingsPath)
    }
    $removed = @()
    foreach ($name in @('http.proxy', 'cursor.general.disableHttp2', 'http.systemCertificates')) {
        if ($cfg.PSObject.Properties[$name]) {
            $cfg.PSObject.Properties.Remove($name)
            $removed += $name
        }
    }
    if ($cfg.settingsSync -and $cfg.settingsSync.PSObject.Properties['ignoredSettings']) {
        $ignored = @($cfg.settingsSync.ignoredSettings | Where-Object { $_ -ne 'http.proxy' })
        if ($ignored.Count -eq 0) {
            $cfg.settingsSync.PSObject.Properties.Remove('ignoredSettings')
            if ($cfg.settingsSync.PSObject.Properties.Count -eq 0) {
                $cfg.PSObject.Properties.Remove('settingsSync')
            }
        } else {
            $cfg.settingsSync.ignoredSettings = $ignored
        }
        $removed += 'settingsSync.ignoredSettings[http.proxy]'
    }
    $json = $cfg | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    if ($removed.Count) { Write-Host ("  removed: {0}" -f ($removed -join ', ')) }
    else { Write-Host '  nothing to remove (already clean).' }
    $backup = "$settingsPath.bak-cursor-pulse"
    if (Test-Path $backup) {
        Write-Host "  pre-install backup kept at $backup"
        Write-Host '  if you had your own http.proxy before onboarding, restore it from that backup.'
    }
} else {
    Write-Host '  settings.json not found - skipping.'
}

Write-Host '[3/4] Removing proxy CA from current-user trusted roots...'
$certs = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -like '*cursor-quota-proxy*' }
if ($certs) {
    foreach ($c in $certs) {
        $null = certutil -user -delstore Root $c.Thumbprint
        Write-Host ("  removed CA {0}" -f $c.Thumbprint)
    }
} else {
    Write-Host '  CA not present - skipping.'
}

Write-Host '[4/4] Done. Fully quit Cursor (all windows) and start it again;'
Write-Host '      it now talks to Cursor directly, outside the team proxy.'
`

// cursorIDESetupScriptTemplate is emitted at GET /setup-cursor.ps1: installs
// the proxy CA into the current-user trusted roots and points the local Cursor
// IDE at this proxy via http.proxy userinfo (pkide_ or cr*). It must run on
// Windows PowerShell 5.1 (no PS7-only features, no backticks so it survives
// being embedded in a Go raw string). Keep it idempotent and always back up
// settings.json before touching it.
const cursorIDESetupScriptTemplate = `param(
    [string]$Key = '',
    [string]$Proxy = '__PROXY_ADDR__'
)
# cursor-pulse IDE onboarding - served by cursor-pulse-proxy at __PROXY_ADDR__
# Counterpart: uninstall-cursor.ps1 reverses this script (keep the two in sync).
# With -Key (pkide_ or cr*): http.proxy uses userinfo on the shared main port.
# Full proxy keys (pk_/pka_) are rejected - copy the IDE command from the console.
# Idempotent: safe to re-run. Rollback notes are printed at the end.
$ErrorActionPreference = 'Stop'
$addr = $Proxy

if (($Key -notlike 'pkide_*') -and ($Key -notlike 'cr*')) {
    throw '-Key must be an IDE key (pkide_...) - copy the Cursor IDE command from the Pulse console; full proxy keys (pk_/pka_) are not accepted.'
}

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

Write-Host '[2/3] Writing Cursor IDE proxy settings...'
$settingsPath = Join-Path $env:APPDATA 'Cursor\User\settings.json'
if (-not (Test-Path $settingsPath)) {
    throw "Cursor settings not found at $settingsPath - start Cursor once, then re-run."
}
$scheme = 'http'
if ($addr -like 'https://*') { $scheme = 'https' }
$mainHost = ($addr -replace '^https?://', '')
$proxyURL = $scheme + '://' + [System.Uri]::EscapeDataString($Key) + ':x@' + $mainHost
$backup = "$settingsPath.bak-cursor-pulse"
if (-not (Test-Path $backup)) { Copy-Item $settingsPath $backup }
$raw = Get-Content $settingsPath -Raw -Encoding UTF8
try {
    $cfg = $raw | ConvertFrom-Json
} catch {
    throw ('settings.json is not strict JSON (comments or trailing commas). Edit manually: set http.proxy with your IDE key and cursor.general.disableHttp2 to true')
}
$cfg | Add-Member -NotePropertyName 'http.proxy' -NotePropertyValue $proxyURL -Force
$cfg | Add-Member -NotePropertyName 'cursor.general.disableHttp2' -NotePropertyValue $true -Force
$cfg | Add-Member -NotePropertyName 'http.systemCertificates' -NotePropertyValue $true -Force
if (-not $cfg.settingsSync) {
    $cfg | Add-Member -NotePropertyName 'settingsSync' -NotePropertyValue ([pscustomobject]@{}) -Force
}
if (-not $cfg.settingsSync.PSObject.Properties['ignoredSettings']) {
    $cfg.settingsSync | Add-Member -NotePropertyName 'ignoredSettings' -NotePropertyValue @() -Force
}
$ignored = @($cfg.settingsSync.ignoredSettings)
if ($ignored -notcontains 'http.proxy') {
    $ignored += 'http.proxy'
    $cfg.settingsSync.ignoredSettings = $ignored
}
$json = $cfg | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  http.proxy written with IDE key on main port (backup: $backup)."

Write-Host '[3/3] Checking Cursor IDE...'
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
Write-Host 'Rollback: irm __PROXY_ADDR__/uninstall-cursor.ps1 | iex'
Write-Host '  (or restore the .bak-cursor-pulse file over settings.json).'
`
