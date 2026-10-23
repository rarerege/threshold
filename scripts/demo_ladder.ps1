# Escalation-ladder demo on port 8001 with a fresh database and a compressed schedule.
# The check-in deadline is set $Minutes from now, then each step follows 1 minute later:
#   deadline -> checking_in, +1 min -> caregiver_alerted, +2 min -> secondary_alerted.
# Re-run it right before recording the ladder segment.
# Usage: .\scripts\demo_ladder.ps1 [-Minutes 2]
param([int]$Minutes = 2)
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Remove-Item data\ladder.db* -ErrorAction SilentlyContinue
    $env:THRESHOLD_DB = "data/ladder.db"
    $env:MCP_PORT = "8001"
    $env:THRESHOLD_CHECKIN_BY = (Get-Date).AddMinutes($Minutes).ToString("HH:mm")
    $env:THRESHOLD_GRACE_MIN = "0"
    $env:THRESHOLD_REPROMPT_MIN = "1"
    $env:THRESHOLD_SECONDARY_AFTER_MIN = "1"
    Write-Host "Ladder demo: check-in deadline $($env:THRESHOLD_CHECKIN_BY), then caregiver +1 min, secondary +2 min"
    & (Join-Path $root ".venv\Scripts\python.exe") -m core.mcp_server
} finally {
    Pop-Location
}
