$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = $root
& .\.venv\Scripts\uvicorn.exe backend.api:app --reload --host 0.0.0.0 --port 8090
