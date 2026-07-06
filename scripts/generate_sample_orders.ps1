[CmdletBinding()]
param(
    [string]$OutputPath = 'data\sample_import_200_orders.csv',
    [int]$Rows = 200,
    [int]$Seed = 20250217
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = 'python'
}

Push-Location $ProjectRoot
try {
    & $PythonExe (Join-Path $PSScriptRoot 'generate_sample_orders.py') --output $OutputPath --rows $Rows --seed $Seed
    if ($LASTEXITCODE -ne 0) {
        throw "generate_sample_orders.py failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}