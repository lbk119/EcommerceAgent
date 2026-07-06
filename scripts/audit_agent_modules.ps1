param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'

$agentRoot = Join-Path $Root 'agent'
$extensionRoot = Join-Path $Root 'agent_extensions'
$hotPathPatterns = @(
  'agent/main_agent.py',
  'agent/chat_agent_runtime.py',
  'agent/runtime/agent_runtime.py',
  'agent/runtime/plan_registry.py',
  'agent/runtime/parallel_executor.py',
  'agent/runtime/reducer.py',
  'agent/workflows/workflow_runner.py'
)

function Get-ModuleClassification {
  param([string]$RelativePath)
  if ($RelativePath -like 'agent_extensions/*') { return 'optional_extension' }
  if ($RelativePath -like 'agent/runtime/*' -or $RelativePath -eq 'agent/chat_agent_runtime.py' -or $RelativePath -eq 'agent/main_agent.py') { return 'core_runtime' }
  if ($RelativePath -like 'agent/workflows/*') { return 'workflow_core' }
  if ($RelativePath -like 'agent/memory/*') { return 'background_memory' }
  if ($RelativePath -like 'agent/critic/*' -or $RelativePath -like 'agent/evolution/*') { return 'background_enrichment' }
  if ($RelativePath -like 'agent/sub_agents/*') { return 'core_subagent' }
  if ($RelativePath -like 'agent/observability/*') { return 'observability' }
  if ($RelativePath -like 'agent/security/*' -or $RelativePath -like 'agent/core/*') { return 'foundation' }
  return 'supporting'
}

function Get-Recommendation {
  param([string]$Classification, [bool]$HotPath)
  if ($Classification -eq 'optional_extension') { return 'Keep disabled by default; enable only via DEEP_AGENT_ENABLE_* or MEMORY_VECTOR_* env vars.' }
  if ($HotPath) { return 'Keep lean: no model construction, vector store, network search, or descriptor imports at module import time.' }
  if ($Classification -in @('background_memory', 'background_enrichment')) { return 'Run after user-visible result; never block realtime/standard response.' }
  return 'No immediate action.'
}

$roots = @($agentRoot)
if (Test-Path $extensionRoot) { $roots += $extensionRoot }
$files = foreach ($rootItem in $roots) {
  Get-ChildItem -Path $rootItem -Recurse -File -Filter '*.py' | Sort-Object FullName
}

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
  $referenceCount = ($imports | Where-Object { $_ -match 'agent|agent_extensions|api|tools' }).Count
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

Write-Host ''
Write-Host 'Optional extensions:'
$rows | Where-Object { $_.Classification -eq 'optional_extension' } | Select-Object Module, Recommendation | Format-Table -AutoSize
