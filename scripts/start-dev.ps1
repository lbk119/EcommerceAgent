[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '')]
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$SeedDemo,
    [int]$PythonPort = 9000,
    [int]$GatewayPort = 9090,
    [int]$UiPort = 5173,
    [string]$PythonHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $ProjectRoot ".run-logs"
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$GatewayUrl = "http://127.0.0.1:$GatewayPort"
$PythonBrainUrl = "http://$PythonHost`:$PythonPort"

function WriteStep($Message) {
    Write-Host "[EcomAgent] $Message" -ForegroundColor Cyan
}

function WriteOk($Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function TestRequiredCommand($Name, $Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing command '$Name'. $Hint"
    }
}

function TestPortOpen($Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait(300)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function WaitForPortOpen($Name, $Port, $TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (TestPortOpen $Port) {
            WriteOk "$Name is listening on port $Port"
            return $true
        }
        [Threading.Thread]::Sleep(500)
    }

    Write-Host "[WARN] $Name did not start listening on port $Port within $TimeoutSeconds seconds. Check logs in $LogDir." -ForegroundColor Yellow
    return $false
}

function ConvertToSingleQuotedValue($Value) {
    return $Value.Replace("'", "''")
}

function StartDevWindow($Name, $Command) {
    $safeRoot = ConvertToSingleQuotedValue $ProjectRoot
    $safeLog = ConvertToSingleQuotedValue (Join-Path $LogDir "$Name.log")
    $script = @"
Set-Location -LiteralPath '$safeRoot'
`$Host.UI.RawUI.WindowTitle = 'EcomAgent - $Name'
Start-Transcript -Path '$safeLog' -Append | Out-Null
try {
$Command
}
finally {
    Stop-Transcript | Out-Null
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell.exe -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) | Out-Null
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

WriteStep "Project root: $ProjectRoot"
TestRequiredCommand "go" "Install Go and make sure it is available in PATH."
TestRequiredCommand "node" "Install Node.js 20+ and make sure it is available in PATH."
TestRequiredCommand "npm" "Install npm with Node.js."

if (-not (Test-Path $PythonExe)) {
    TestRequiredCommand "python" "Install Python 3.12+ or create .venv manually."
    WriteStep "Creating Python virtual environment..."
    python -m venv .venv
}

if ($Install) {
    WriteStep "Installing Python dependencies..."
    & $PythonExe -m pip install -r requirements.txt

    WriteStep "Downloading Go modules..."
    go mod download

    WriteStep "Installing frontend dependencies..."
    Push-Location ui
    npm install
    Pop-Location
}
elseif (-not (Test-Path (Join-Path $ProjectRoot "ui\node_modules"))) {
    Write-Host "[WARN] ui\node_modules not found. Run scripts\start-dev.ps1 -Install once if the frontend fails." -ForegroundColor Yellow
}

if ($SeedDemo) {
    WriteStep "Seeding ecommerce_demo database..."
    & $PythonExe .\data\ecommerce_demo\seed_ecommerce_demo.py --reset --database ecommerce_demo
}

$pythonCommand = @"
`$env:TASK_QUEUE_BACKEND = if (`$env:TASK_QUEUE_BACKEND) { `$env:TASK_QUEUE_BACKEND } else { 'inline' }
`$env:PYTHONUNBUFFERED = '1'
& '$($PythonExe.Replace("'", "''"))' -m uvicorn api.server:app --host $PythonHost --port $PythonPort
"@

$gatewayCommand = @"
`$env:PYTHON_BRAIN_URL = '$PythonBrainUrl'
`$env:GATEWAY_ADDR = ':$GatewayPort'
`$env:OUTPUT_DIR = 'output'
`$env:GIN_MODE = if (`$env:GIN_MODE) { `$env:GIN_MODE } else { 'debug' }
go run ./gateway/cmd/server
"@

$uiCommand = @"
`$env:VITE_API_BASE_URL = '$GatewayUrl'
`$env:VITE_WS_BASE_URL = 'ws://127.0.0.1:$GatewayPort'
Push-Location ui
npm run dev -- --host 127.0.0.1 --port $UiPort
Pop-Location
"@

WriteStep "Starting services..."

if (TestPortOpen $PythonPort) {
    Write-Host "[SKIP] Python brain port $PythonPort is already in use: $PythonBrainUrl" -ForegroundColor Yellow
}
else {
    StartDevWindow "python-brain" $pythonCommand
    WriteOk "Python brain starting at $PythonBrainUrl"
    WaitForPortOpen "Python brain" $PythonPort | Out-Null
}

if (TestPortOpen $GatewayPort) {
    Write-Host "[SKIP] Go gateway port $GatewayPort is already in use: $GatewayUrl" -ForegroundColor Yellow
}
else {
    StartDevWindow "go-gateway" $gatewayCommand
    WriteOk "Go gateway starting at $GatewayUrl"
}

if (TestPortOpen $UiPort) {
    Write-Host "[SKIP] Vue UI port $UiPort is already in use: http://127.0.0.1:$UiPort" -ForegroundColor Yellow
}
else {
    StartDevWindow "vue-ui" $uiCommand
    WriteOk "Vue UI starting at http://127.0.0.1:$UiPort"
}

Write-Host ""
Write-Host "EcomAgent is starting. Open: http://127.0.0.1:$UiPort" -ForegroundColor Green
Write-Host "Gateway health: $GatewayUrl/health"
Write-Host "Logs: $LogDir"
Write-Host "Close the opened service windows, or press Ctrl+C inside them, to stop services."
