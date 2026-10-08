# Live research day - M14 (DECISIONS #24). Run by Windows Task Scheduler.
#   -Phase worker : 08:40, live worker until 15:30 (records data/research/live/snapshots)
#   -Phase label  : 15:45, paper day report, fetch the day's 1-min bars, label outcomes, write reports
# Read-only market data; never places orders (paper trading is simulated in-process). Logs: logs\<phase>_<date>.log
param([Parameter(Mandatory)][ValidateSet("worker", "label")][string]$Phase)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$day = Get-Date -Format "yyyy-MM-dd"
$py = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "logs\$($Phase)_$day.log"
New-Item -ItemType Directory -Force (Join-Path $repo "logs") | Out-Null
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

function Run([string[]]$argv) {
    "[$(Get-Date -Format 'HH:mm:ss')] python $($argv -join ' ')" | Out-File -Append -Encoding utf8 $log
    & $py @argv *>&1 | Out-File -Append -Encoding utf8 $log
    "[$(Get-Date -Format 'HH:mm:ss')] exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
}

if ($Phase -eq "worker") {
    Run @("-m", "src.app.worker", "--paper")   # + paper trading, simulated only (DECISIONS #30)
} else {
    Run @("scripts\paper_day_report.py", "--day", $day)   # paper P&L (DECISIONS #30)
    Run @("scripts\label_outcomes.py", "--day", $day, "--fetch")
    Run @("scripts\evaluate.py", "--labeled", "data\research\live\labeled", "--from", $day, "--to", $day,
          "--title", "Live research $day (one day: description, not evidence)", "--out", "reports\live_$day.md")
    Run @("scripts\evaluate.py", "--labeled", "data\research\live\labeled",
          "--title", "Live research, all days", "--out", "reports\live_all.md")
}
