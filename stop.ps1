# إيقاف API (8090) و Nuxt (3000) — أمر واحد
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot

function Stop-AlKarrarPort {
    param([int]$Port)
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    try {
        $rows = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        foreach ($r in $rows) {
            $pid = [int]$r.OwningProcess
            if ($pid -lt 1 -or $pid -eq 4) { continue }
            [void]$seen.Add($pid)
        }
    } catch {}
    foreach ($pid in $seen) {
        try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
    }
}

Stop-AlKarrarPort -Port 8090
Stop-AlKarrarPort -Port 3000
Write-Host "AlKarrar Pro: تم إيقاف المنافذ 8090 و 3000."
