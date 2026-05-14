$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Error "Missing .venv. Run: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
}
$env:PYTHONPATH = $root
& .\.venv\Scripts\python.exe -m backend.main_engine @args
