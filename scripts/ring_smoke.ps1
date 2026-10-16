# Ring smoke test. Usage: .\scripts\ring_smoke.ps1 [-Look] [-WaitEvent]
param([switch]$Look, [switch]$WaitEvent)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$argsList = @((Join-Path $root "scripts\ring_smoke.py"))
if ($Look) { $argsList += "--look" }
if ($WaitEvent) { $argsList += "--wait-event" }
Push-Location $root
try { & $py @argsList; exit $LASTEXITCODE } finally { Pop-Location }
