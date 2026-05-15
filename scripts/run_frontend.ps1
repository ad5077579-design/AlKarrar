$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\frontend"
npm run dev -- --host 0.0.0.0 --port 3000
