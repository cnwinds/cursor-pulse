#Requires -Version 5.1
<#
.SYNOPSIS
  Cursor Pulse 开发环境启停脚本

.EXAMPLE
  .\cursor-pulse.ps1 start
  .\cursor-pulse.ps1 start web admin
  .\cursor-pulse.ps1 stop
  .\cursor-pulse.ps1 restart
  .\cursor-pulse.ps1 log web
  .\cursor-pulse.ps1 log web -f
  .\cursor-pulse.ps1 status
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "log", "logs", "status", "help")]
    [string]$Command = "help",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Get-PulseExe {
    $venvPulse = Join-Path $Root ".venv\Scripts\pulse.exe"
    if (Test-Path $venvPulse) { return $venvPulse }
    $globalPulse = Get-Command pulse -ErrorAction SilentlyContinue
    if ($globalPulse) { return $globalPulse.Source }
    throw "未找到 pulse，请先执行: python -m venv .venv; .\.venv\Scripts\pip install -e `".[dev,web]`""
}

function Get-PythonExe {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    $globalPy = Get-Command python -ErrorAction SilentlyContinue
    if ($globalPy) { return $globalPy.Source }
    throw "未找到 python，请先执行: python -m venv .venv; .\.venv\Scripts\pip install -e `".[dev,web]`""
}

function Show-Help {
    @"
Cursor Pulse 开发环境管理

用法:
  cursor-pulse start [web] [admin] [channel] [assistant] [proxy]   启动服务（默认 web+admin+channel+assistant）
  cursor-pulse stop  [web] [admin] [channel] [assistant] [proxy]   停止服务（默认停止所有在运行的）
  cursor-pulse restart [web] [admin] [channel] [assistant] [proxy] 重启服务
  cursor-pulse log <web|admin|channel|assistant|proxy> [-f] [-n N]  查看日志（-f 持续跟踪）
  cursor-pulse status                      查看运行状态

服务:
  web       管理后台 API          http://127.0.0.1:8080  （代码变更自动重载）
  admin     Vue 开发前端          http://127.0.0.1:5173  （Vite HMR）
  channel   渠道适配 + 调度       （代码变更自动重启）
  assistant Assistant Platform    http://127.0.0.1:8090  （代码变更自动重启）
  proxy     Cursor 代理（Go）     http://127.0.0.1:8317  （需 Go；不在默认 start 集合）

开发模式默认启用热重载，监视 pulse / assistant_platform。
修改帮助、聚合、钉钉回复等逻辑后无需手动重启；若仍不生效可执行 restart。

代理建议：先 start（起 web），再 ``cursor-pulse start proxy``（读取 .env 中的 PULSE_BASE_URL / PULSE_INTERNAL_SERVICE_TOKEN）。

日志目录: .dev/logs/
"@
}

function Invoke-PulseDev {
    param([string[]]$PulseArgs)
    $pulse = Get-PulseExe
    & $pulse @PulseArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Command) {
    "help" {
        Show-Help
    }
    "start" {
        $services = @($Rest | Where-Object { $_ -in @("web", "admin", "channel", "assistant", "proxy") })
        $pulseArgs = @("dev", "start")
        if ($services.Count -gt 0) { $pulseArgs += $services }
        Invoke-PulseDev -PulseArgs $pulseArgs
    }
    "stop" {
        $services = @($Rest | Where-Object { $_ -in @("web", "admin", "channel", "assistant", "proxy") })
        $pulseArgs = @("dev", "stop")
        if ($services.Count -gt 0) { $pulseArgs += $services }
        Invoke-PulseDev -PulseArgs $pulseArgs
    }
    "restart" {
        $services = @($Rest | Where-Object { $_ -in @("web", "admin", "channel", "assistant", "proxy") })
        $pulseArgs = @("dev", "restart")
        if ($services.Count -gt 0) { $pulseArgs += $services }
        Invoke-PulseDev -PulseArgs $pulseArgs
    }
    { $_ -in @("log", "logs") } {
        $service = ($Rest | Where-Object { $_ -in @("web", "admin", "channel", "assistant", "proxy") } | Select-Object -First 1)
        if (-not $service) { $service = "web" }
        $flags = @($Rest | Where-Object { $_ -notin @("web", "admin", "channel", "assistant", "proxy") })
        $python = Get-PythonExe
        $devArgs = @("-m", "pulse.dev", "logs", $service) + $flags
        & $python @devArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "status" {
        Invoke-PulseDev -PulseArgs @("dev", "status")
    }
}
