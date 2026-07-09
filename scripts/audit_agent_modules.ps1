param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'

$agentRoot = Join-Path $Root 'agent'
$hotPathPatterns = @(
  'agent/main_agent.py',
  'agent/runtime/agent_runtime.py',
  'agent/subagent/runtime.py'
)

function Get-ModuleClassification {
  param([string]$RelativePath)
  if ($RelativePath -like 'agent/runtime/*' -or $RelativePath -like 'agent/subagent/*' -or $RelativePath -eq 'agent/main_agent.py') { return 'core_runtime' }
  if ($RelativePath -like 'agent/memory/*' -or $RelativePath -like 'agent/platform/*' -or $RelativePath -like 'agent/plan/*') { return 'foundation' }
  if ($RelativePath -like 'agent/evaluation/*') { return 'evaluation' }
  if ($RelativePath -like 'agent/reflection/*') { return 'background_enrichment' }
  if ($RelativePath -like 'agent/tools/*') { return 'tooling' }
  if ($RelativePath -like 'agent/trace/*') { return 'observability' }
  if ($RelativePath -like 'agent/security/*') { return 'foundation' }
  return 'supporting'
}

function Get-Recommendation {
  param([string]$Classification, [bool]$HotPath)
  if ($HotPath) { return 'Keep lean: no model construction, vector store, network search, or descriptor imports at module import time.' }
  if ($Classification -in @('background_memory', 'background_enrichment')) { return 'Run after user-visible result; never block realtime/standard response.' }
  return 'No immediate action.'
}

$files = Get-ChildItem -Path $agentRoot -Recurse -File -Filter '*.py' | Sort-Object FullName

function Get-RelativePath {
  param([string]$BasePath, [string]$TargetPath)
  $baseUri = [Uri]((Join-Path (Resolve-Path $BasePath).Path '.') -replace '\\', '/')
  $targetUri = [Uri]((Resolve-Path $TargetPath).Path -replace '\\', '/')
  return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString())
}

$rows = foreach ($file in $files) {
  $relative = Get-RelativePath -BasePath $Root -TargetPath $file.FullName
  $content = Get-Content -Path $file.FullName -Raw -Encoding UTF8
  $imports = ([regex]::Matches($content, '(?m)^\s*(from\s+[\w\.]+\s+import\s+[^#\r\n]+|import\s+[\w\.,\s]+)') | ForEach-Object { $_.Value.Trim() })
  $referenceCount = ($imports | Where-Object { $_ -match 'agent|api' }).Count
  $hotPath = $false
  foreach ($pattern in $hotPathPatterns) {
    if ($relative -eq $pattern) { $hotPath = $true; break }
  }
  $classification = Get-ModuleClassification $relative
  [pscustomobject]@{
    Module = $relative
    Classification = $classification
    References = $referenceCount
    HotPath = $hotPath
    Recommendation = Get-Recommendation $classification $hotPath
  }
}

Write-Host 'Agent module audit'
Write-Host ('Root: {0}' -f $Root)
Write-Host ''
$rows | Format-Table -AutoSize

Write-Host ''
Write-Host 'Hot path modules:'
$rows | Where-Object HotPath | Select-Object Module, Classification, Recommendation | Format-Table -AutoSize
