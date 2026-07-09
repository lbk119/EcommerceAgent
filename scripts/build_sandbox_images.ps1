Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    docker build -t ecommerce-agent-sandbox-base:latest docker/sandbox/base
    if ($LASTEXITCODE -ne 0) { throw "failed to build ecommerce-agent-sandbox-base:latest" }
    docker build -t ecommerce-agent-sandbox-python:latest docker/sandbox/python
    if ($LASTEXITCODE -ne 0) { throw "failed to build ecommerce-agent-sandbox-python:latest" }
    docker build -t ecommerce-agent-sandbox-node:latest docker/sandbox/node
    if ($LASTEXITCODE -ne 0) { throw "failed to build ecommerce-agent-sandbox-node:latest" }
} finally {
    Pop-Location
}