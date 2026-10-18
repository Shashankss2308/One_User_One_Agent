param(
    [Parameter(Mandatory = $true)]
    [string]$PackPath,
    [string]$OutDir = "sample_run",
    [string]$BaseDb = "state.db"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pack = (Resolve-Path -LiteralPath $PackPath).Path
$output = Join-Path $root $OutDir
$database = Join-Path $root $BaseDb
$workspacePython = Join-Path $root "..\.venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $workspacePython) { $workspacePython } else { "python" }
$env:PYTHONPATH = Join-Path $root "src"

& $python (Join-Path $root "src\ott_agent\ingest.py") (Join-Path $pack "data\events.jsonl") $database
if ($LASTEXITCODE -ne 0) { throw "Event ingestion failed with exit code $LASTEXITCODE" }
& $python (Join-Path $root "src\ott_agent\runtime.py") $pack $database $output
if ($LASTEXITCODE -ne 0) { throw "Replay failed with exit code $LASTEXITCODE" }
& $python (Join-Path $pack "simulator\simulate_outcomes.py") --pack $pack --actions (Join-Path $output "actions.jsonl") --out (Join-Path $output "sim")
if ($LASTEXITCODE -ne 0) { throw "Outcome simulation failed with exit code $LASTEXITCODE" }
& $python -m ott_agent.outcomes $pack $output
if ($LASTEXITCODE -ne 0) { throw "Goal outcome reconciliation failed with exit code $LASTEXITCODE" }
& $python -m ott_agent.impact_report $pack $output
if ($LASTEXITCODE -ne 0) { throw "Impact report generation failed with exit code $LASTEXITCODE" }

Write-Host "Replay, simulator, outcomes, and impact report completed in $output"
