[CmdletBinding()]
param(
    [string]$TenantId = 'tenant_demo',
    [string]$ShopId = 'default_shop',
    [string]$OutputPath = 'data\sample_import_200_orders.csv',
    [int]$Rows = 200,
    [int]$Seed = 20250217,
    [switch]$ResetUsers,
    [switch]$AllowNonDevDatabase,
    [switch]$ConfirmResetDevData
)

$ErrorActionPreference = 'Stop'

& (Join-Path $PSScriptRoot 'reset_dev_data.ps1') -TenantId $TenantId -ShopId $ShopId -ResetUsers:$ResetUsers -AllowNonDevDatabase:$AllowNonDevDatabase -ConfirmResetDevData:$ConfirmResetDevData
& (Join-Path $PSScriptRoot 'generate_sample_orders.ps1') -OutputPath $OutputPath -Rows $Rows -Seed $Seed

Write-Host "[prepare_fresh_eval] ready: $OutputPath"