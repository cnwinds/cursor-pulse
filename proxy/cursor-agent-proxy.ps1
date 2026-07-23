# cursor-agent 包装脚本：自动注入代理环境变量后启动 agent
# 用法: .\cursor-agent-proxy.ps1 [agent 的所有参数...]
#   .\cursor-agent-proxy.ps1
#   .\cursor-agent-proxy.ps1 -p "帮我重构这个文件" --resume xxx

$ProxyAddr = if ($env:CURSOR_QUOTA_PROXY) { $env:CURSOR_QUOTA_PROXY } else { "http://127.0.0.1:8317" }
$CaPath    = Join-Path $env:USERPROFILE ".cursor-quota-proxy\ca.pem"

$env:HTTPS_PROXY = $ProxyAddr
if (Test-Path $CaPath) {
    $env:NODE_EXTRA_CA_CERTS = $CaPath
} else {
    Write-Warning "CA 证书不存在于 $CaPath（代理还没启动过？），回退到 -k 模式"
}

$agent = Get-Command agent -ErrorAction SilentlyContinue
if ($agent) {
    $cursorAgent = $agent.Source
} else {
    $legacy = "C:\Users\lvan\AppData\Local\cursor-agent\cursor-agent.cmd"
    if (Test-Path $legacy) { $cursorAgent = $legacy } else { $cursorAgent = "agent" }
}

& $cursorAgent @args
exit $LASTEXITCODE
