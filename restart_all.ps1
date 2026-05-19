# AlKarrar Pro — تشغيل موحّد: إيقاف ما يغلق المنافذ 8090 / 3000 ثم تشغيل API + الواجهة.
# الاستخدام: انقر يميناً → Run with PowerShell، أو من جذر المشروع:  .\restart_all.ps1
# خيارات:
#   .\restart_all.ps1 -NoApiReload     إيقاف --reload لـ uvicorn (اتصال WS أثقل استقراراً عند تعديلات نادرة)

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

Write-Host "AlKarrar Pro: freed ports 8090 & 3000; API (uvicorn$(if (-not $NoApiReload) { ' +reload' })) + Nuxt dev starting in new windows."
Write-Host "  API:      http://127.0.0.1:8090   |  UI: http://localhost:3000"
Write-Host "Waiting for Nuxt (first start may take ~60s)..."
Start-Sleep -Seconds 12
Start-Process "http://localhost:3000"
