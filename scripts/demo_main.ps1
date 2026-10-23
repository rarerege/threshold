# Main demo server on port 8000 with its own fresh database (data/demo.db; data/threshold.db is untouched).
# Dad's check-in time is set $Minutes from now, so "Did Dad check in today?" starts at "Not yet".
# Re-run it before each recording take.
# Usage: .\scripts\demo_main.ps1 [-Minutes 60]
param([int]$Minutes = 60)
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Remove-Item data\demo.db* -ErrorAction SilentlyContinue
    $env:THRESHOLD_DB = "data/demo.db"
    $env:MCP_PORT = "8000"
    $env:THRESHOLD_CHECKIN_BY = (Get-Date).AddMinutes($Minutes).ToString("HH:mm")
    Write-Host "Main demo: Dad checks in by $($env:THRESHOLD_CHECKIN_BY) (grace 30 min); open http://127.0.0.1:8000/"
    & (Join-Path $root ".venv\Scripts\python.exe") -m core.mcp_server
} finally {
    Pop-Location
}
