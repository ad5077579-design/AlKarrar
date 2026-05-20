# AlKarrar Pro — تشغيل موحّد (أمر واحد = خلفية + واجهة)
#   .\start.ps1          ← الأبسط (نفس هذا الملف)
#   .\restart_all.ps1
# لا حاجة لتشغيل scripts\run_api.ps1 و scripts\run_frontend.ps1 يدوياً.
# خيار: .\start.ps1 -NoApiReload

param(
    [switch]$NoApiReload
)

$ErrorActionPreference = "Continue"
$root = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
Set-Location $root

function Stop-AlKarrarPort {
    param([int]$Port)
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    try {
        $rows = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        foreach ($r in $rows) {
            $pid = [int]$r.OwningProcess
            if ($pid -lt 1) { continue }
            if ($pid -eq 4) { continue } # System — لا نقتله أبداً
            [void]$seen.Add($pid)
        }
    } catch {}
    foreach ($pid in $seen) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

Stop-AlKarrarPort -Port 8090
Stop-AlKarrarPort -Port 3000
Start-Sleep -Seconds 1

$apiScript = Join-Path $root "scripts\run_api.ps1"
$feScript  = Join-Path $root "scripts\run_frontend.ps1"

if (-not (Test-Path $apiScript)) {
    Write-Error "Missing scripts\run_api.ps1"
    exit 1
}
if (-not (Test-Path $feScript)) {
    Write-Error "Missing scripts\run_frontend.ps1"
    exit 1
}

$shell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

# نافذتان منفصلتان: سجلات واضحة دون إدارة يدوية للمنافذ
$apiArgs = @(
    "-NoExit"
    "-ExecutionPolicy", "Bypass"
    "-File", $apiScript
)
if (-not $NoApiReload) {
    $apiArgs += "-Reload"
}

Start-Process -FilePath $shell -WorkingDirectory $root -ArgumentList $apiArgs -WindowStyle Normal

Start-Process -FilePath $shell -WorkingDirectory $root -ArgumentList @(
    "-NoExit"
    "-ExecutionPolicy", "Bypass"
    "-File", $feScript
) -WindowStyle Normal

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AlKarrar Pro — تشغيل واحد (امرين داخلياً فقط)" -ForegroundColor Cyan
Write-Host "  لا تغلق هاتين النافذتين:" -ForegroundColor Cyan
Write-Host "    [1] API خلفية  -> http://127.0.0.1:8090" -ForegroundColor Green
Write-Host "    [2] الواجهة     -> http://localhost:3000" -ForegroundColor Green
Write-Host "  ايقاف: .\stop.ps1" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Waiting for Nuxt (first start may take ~60s)..."
Start-Sleep -Seconds 12
Start-Process "http://localhost:3000"
