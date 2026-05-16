param(
    # يُمرَّر من restart_all.ps1 — إعادة تحميل الكود بدون إعادة تشغيل يدوية (يفيد مسارات جديدة مثل /audit).
    [switch]$Reload
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = $root
# Avoid --reload in normal use: it restarts the API on file changes and drops WebSocket + dashboard connections.
# Auto-reload إذا: -Reload أو $env:ALKARRAR_UVICORN_RELOAD = "1"
$useReload = $Reload -or ($env:ALKARRAR_UVICORN_RELOAD -eq "1")
$reload = if ($useReload) { @("--reload") } else { @() }
& .\.venv\Scripts\uvicorn.exe backend.api.app:app @reload --host 0.0.0.0 --port 8090
