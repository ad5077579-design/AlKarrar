# AlKarrar Pro — أمر تشغيل واحد (واجهة + خلفية)
# من جذر المشروع:
#   .\start.ps1
# يفتح نافذتين: API (8090) + Nuxt (3000) ويفتح المتصفح.

param(
    [switch]$NoApiReload
)

$here = $PSScriptRoot
& (Join-Path $here "restart_all.ps1") @PSBoundParameters
