$ErrorActionPreference = "Stop"

$scriptPath = "C:\dev\hyeve\scripts\start-aix-hybrid.ps1"
$runlogDir = "C:\dev\hyeve\_runlogs"
$envFile = Join-Path $runlogDir "wh-operator-env-latest.ps1"
$env:TEST_BOOTSTRAP_SENTINEL = ""

New-Item -ItemType Directory -Force -Path $runlogDir | Out-Null
Set-Content -LiteralPath $envFile -Value @'
$env:HYEVE_OPERATOR_TOKEN = 'bootstrap-token'
'@ -Encoding UTF8

Remove-Item Env:HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:HYEVE_AIX_SERVICE_OPSBUS_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:HYEVE_AIX_SERVICE_CODEINTEL_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:HYEVE_AIX_SERVICE_RUNTIME_PLANNER_AUTH_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:HYEVE_OPERATOR_TOKEN -ErrorAction SilentlyContinue

& $scriptPath -NoStart -NoTokenRefresh | Out-Null

if ($env:HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN -ne "bootstrap-token") { throw "workhub token fanout missing" }
if ($env:HYEVE_AIX_SERVICE_OPSBUS_API_TOKEN -ne "bootstrap-token") { throw "opsbus token fanout missing" }
if ($env:HYEVE_AIX_SERVICE_CODEINTEL_API_TOKEN -ne "bootstrap-token") { throw "codeintel token fanout missing" }
if ($env:HYEVE_AIX_SERVICE_RUNTIME_PLANNER_AUTH_TOKEN -ne "bootstrap-token") { throw "planner token fanout missing" }
