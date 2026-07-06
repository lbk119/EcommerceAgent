[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
[CmdletBinding()]
param(
    [string]$TenantId = 'tenant_demo',
    [string]$ShopId = 'default_shop',
    [switch]$ResetUsers,
    [switch]$AllowNonDevDatabase,
    [switch]$ConfirmResetDevData
)

$ErrorActionPreference = 'Stop'

if (-not $ConfirmResetDevData) {
    throw 'Refusing to reset data. Re-run with -ConfirmResetDevData for a disposable dev database.'
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = 'python'
}

$arguments = @(
    (Join-Path $PSScriptRoot 'reset_dev_data.py'),
    '--tenant-id', $TenantId,
    '--shop-id', $ShopId
)
if ($ResetUsers) { $arguments += '--reset-users' }
if ($AllowNonDevDatabase) { $arguments += '--allow-non-dev-database' }

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "reset_dev_data.py failed with exit code $LASTEXITCODE"
}