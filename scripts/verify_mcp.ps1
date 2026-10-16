# Raw JSON-RPC walkthrough of the Threshold MCP server (Streamable HTTP, protocol 2025-11-25).
# Start the server first:  .\.venv\Scripts\python.exe -m core.mcp_server
# Usage: .\scripts\verify_mcp.ps1 [-Url http://127.0.0.1:8000/mcp] [-SkipDoor]
param([string]$Url = "http://127.0.0.1:8000/mcp", [switch]$SkipDoor)

$tmp = Join-Path $env:TEMP "threshold-rpc.json"
$script:sid = $null
$script:id = 0

function Send-Rpc([string]$method, $params = $null, [switch]$Notify) {
    $msg = [ordered]@{ jsonrpc = "2.0"; method = $method }
    if (-not $Notify) { $script:id++; $msg.id = $script:id }
    if ($null -ne $params) { $msg.params = $params }
    $json = $msg | ConvertTo-Json -Depth 20 -Compress
    [IO.File]::WriteAllText($tmp, $json)
    $hdr = @("-H", "Content-Type: application/json", "-H", "Accept: application/json, text/event-stream")
    if ($script:sid) { $hdr += @("-H", "Mcp-Session-Id: $($script:sid)", "-H", "MCP-Protocol-Version: 2025-11-25") }
    $raw = curl.exe -s -i -X POST $Url @hdr --data-binary "@$tmp"
    $sidLine = $raw | Where-Object { $_ -match '^mcp-session-id:\s*(\S+)' } | Select-Object -First 1
    if ($sidLine -and -not $script:sid) { $script:sid = ($sidLine -replace '^mcp-session-id:\s*', '').Trim() }
    Write-Host "`n>>> $json" -ForegroundColor Cyan
    if ($Notify) { Write-Host "<<< $(($raw | Select-Object -First 1))"; return $null }
    $data = ($raw | Where-Object { $_ -like 'data: *' } | Select-Object -Last 1) -replace '^data: ', ''
    $resp = $data | ConvertFrom-Json
    return $resp
}

function Show-Call($name, $arguments = @{}) {
    $r = Send-Rpc "tools/call" @{ name = $name; arguments = $arguments }
    if ($r.error) { Write-Host "<<< JSON-RPC error $($r.error.code): $($r.error.message)" -ForegroundColor Red; return $r }
    $res = $r.result
    $kinds = ($res.content | ForEach-Object { $_.type }) -join ","
    Write-Host "<<< isError=$($res.isError) content=[$kinds]" -ForegroundColor Green
    Write-Host "    text: $(($res.content | Where-Object type -eq 'text' | Select-Object -First 1).text)"
    if ($res.structuredContent) {
        $sc = $res.structuredContent | ConvertTo-Json -Depth 20 -Compress
        if ($sc.Length -gt 600) { $sc = $sc.Substring(0, 600) + "..." }
        Write-Host "    structuredContent: $sc"
    }
    return $r
}

$init = Send-Rpc "initialize" @{ protocolVersion = "2025-11-25"; capabilities = @{}; clientInfo = @{ name = "verify_mcp.ps1"; version = "0.1" } }
Write-Host "<<< negotiated protocolVersion=$($init.result.protocolVersion) server=$($init.result.serverInfo.name) session=$($script:sid.Substring(0,8))..." -ForegroundColor Green
Send-Rpc "notifications/initialized" -Notify | Out-Null

$list = Send-Rpc "tools/list"
foreach ($t in $list.result.tools) {
    $req = ($t.inputSchema.required) -join ","
    Write-Host ("<<< tool {0,-20} required=[{1}] readOnly={2} destructive={3} outputSchema={4}" -f $t.name, $req, $t.annotations.readOnlyHint, $t.annotations.destructiveHint, [bool]$t.outputSchema) -ForegroundColor Green
}

Show-Call "get_routine_status" | Out-Null
Show-Call "record_checkin" @{ status = "ok"; note = "slept well" } | Out-Null
Show-Call "get_routine_status" | Out-Null
Show-Call "add_expected_visit" @{ label = "Carer"; start = "11:00"; end = "12:00"; days = @("tue", "fri") } | Out-Null
Show-Call "record_checkin" @{ status = "needs_help"; note = "feeling dizzy" } | Out-Null
Show-Call "acknowledge_alert" @{ acknowledged_by = "Asha"; note = "Called him, on my way" } | Out-Null
Show-Call "pause_monitoring" @{ hours = 4; reason = "Dad is at our place" } | Out-Null
Show-Call "pause_monitoring" @{ hours = 4; reason = "Dad is at our place"; confirm = $true } | Out-Null
Show-Call "pause_monitoring" @{ hours = 0; confirm = $true } | Out-Null
Show-Call "record_checkin" @{ status = "fine" } | Out-Null
if (-not $SkipDoor) {
    $door = Show-Call "check_the_door"
    # Note: PowerShell variables are case-insensitive, so don't name this $url (it would clobber -Url).
    $snapUrl = $door.result.structuredContent.snapshot_url
    if ($snapUrl) {
        $out = Join-Path (Split-Path -Parent $PSScriptRoot) "data\verify_door.jpg"
        $code = curl.exe -s -o $out -w "%{http_code} %{content_type} %{size_download}" $snapUrl
        Write-Host "    GET snapshot_url -> $code (saved $out)"
    }
}
Show-Call "get_day_summary" | Out-Null
