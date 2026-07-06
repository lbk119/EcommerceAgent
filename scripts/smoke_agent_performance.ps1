# Agent runtime performance smoke.
# Start Python Brain and Go Gateway first. Override gateway with $env:GATEWAY_URL.

$ErrorActionPreference = 'Stop'

$GatewayUrl = if ($env:GATEWAY_URL) { $env:GATEWAY_URL.TrimEnd('/') } else { 'http://127.0.0.1:9090' }
$ApiBase = "$GatewayUrl/api/v1"
$RequestTimeoutSec = 120

function Fail-Smoke {
    param([string]$Message)
    Write-Error "[smoke_agent_performance] $Message"
    exit 1
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { Fail-Smoke $Message }
}

function Invoke-JsonUtf8 {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Headers,
        [string]$Body,
        [int]$TimeoutSec,
        [string]$ContentType = 'application/json; charset=utf-8'
    )
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = $Method
    $request.Timeout = $TimeoutSec * 1000
    $request.Accept = 'application/json'
    $request.ContentType = $ContentType
    if ($Headers) {
        foreach ($key in $Headers.Keys) { $request.Headers[[string]$key] = [string]$Headers[$key] }
    }
    if ($Body) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream()
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Close()
    }
    try {
        $response = $request.GetResponse()
    } catch [System.Net.WebException] {
        $errorResponse = $_.Exception.Response
        if ($errorResponse -and $errorResponse.GetResponseStream()) {
            $reader = New-Object System.IO.StreamReader($errorResponse.GetResponseStream(), [System.Text.Encoding]::UTF8)
            throw $reader.ReadToEnd()
        }
        throw
    }
    $reader = New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)
    return ($reader.ReadToEnd() | ConvertFrom-Json)
}

function Wait-AiChatTimeline {
    param([string]$TaskId, [hashtable]$Headers, [int]$MinEvents = 3, [int]$MaxAttempts = 15)
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt += 1) {
        $timeline = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/ai-chat/tasks/$TaskId/timeline" -Headers $Headers -TimeoutSec $RequestTimeoutSec
        if ($timeline.events -and $timeline.events.Count -ge $MinEvents) { return $timeline }
        Start-Sleep -Seconds 1
    }
    return $timeline
}

try {
    Write-Host "[smoke_agent_performance] Gateway: $GatewayUrl"
    Write-Host '[smoke_agent_performance] GET /health'
    $health = Invoke-JsonUtf8 -Method 'GET' -Uri "$GatewayUrl/health" -TimeoutSec 20
    Assert-True ($health.status -eq 'ok') 'Gateway health is not ok'

    $stamp = Get-Date -Format 'yyyyMMddHHmmssfff'
    $email = "perf_$stamp@example.com"
    $password = 'Admin123456'
    $registerBody = @{
        email = $email
        password = $password
        name = 'Perf Smoke User'
        companyName = 'Perf Smoke Team'
        plan = 'Team'
    } | ConvertTo-Json
    Write-Host '[smoke_agent_performance] POST /api/v1/auth/register'
    $register = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/auth/register" -Body $registerBody -TimeoutSec $RequestTimeoutSec
    $headers = @{ Authorization = "Bearer $($register.accessToken)" }

    $onboardingBody = @{
        shopName = "Perf Orange Shop $stamp"
        category = 'Orange'
        shopType = 'Brand Owned'
        businessStage = 'Growth'
        selectedPlatforms = @('Taobao')
        dataMode = 'sample'
        enabledAgentIds = @('store-analyst', 'product-assistant', 'inventory-inspector', 'campaign-reviewer', 'report-specialist')
    } | ConvertTo-Json -Depth 5
    Write-Host '[smoke_agent_performance] POST /api/v1/onboarding/complete'
    $onboarding = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/onboarding/complete" -Headers $headers -Body $onboardingBody -TimeoutSec $RequestTimeoutSec
    $shopId = if ($onboarding.workspace) { $onboarding.workspace.currentShopId } else { $onboarding.shop.id }
    $headers['X-Shop-ID'] = $shopId

    $shopBody = @{
        shop_id = $shopId
        shop_name = if ($onboarding.workspace) { $onboarding.workspace.shops[0].name } else { "Perf Orange Shop $stamp" }
    } | ConvertTo-Json -Depth 5
    Write-Host '[smoke_agent_performance] POST /api/v1/auth/shops'
    $auth = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/auth/shops" -Headers @{ Authorization = $headers.Authorization } -Body $shopBody -TimeoutSec $RequestTimeoutSec
    $headers = @{ Authorization = "Bearer $($auth.accessToken)"; 'X-Shop-ID' = $shopId }

    Write-Host '[smoke_agent_performance] POST /api/v1/account/onboarding-completed'
    Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/account/onboarding-completed" -Headers $headers -Body '{}' -TimeoutSec $RequestTimeoutSec | Out-Null
    Write-Host '[smoke_agent_performance] POST /api/v1/data-import/sample'
    Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/data-import/sample" -Headers $headers -Body '{}' -TimeoutSec $RequestTimeoutSec | Out-Null

    $chatBody = @{ content = 'topproduct recommendation' } | ConvertTo-Json
    $started = [Diagnostics.Stopwatch]::StartNew()
    Write-Host '[smoke_agent_performance] POST /api/v1/ai-chat/messages'
    $chat = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/messages" -Headers $headers -Body $chatBody -TimeoutSec $RequestTimeoutSec
    $acceptedMs = $started.ElapsedMilliseconds
    Assert-True ($acceptedMs -lt 1000) "AI Chat realtime acceptance should be < 1000ms, got $acceptedMs"
    Assert-True ($chat.status -eq 'running') 'AI Chat should return running immediately'
    Assert-True ($chat.intent -eq 'hot_product_analysis') "AI Chat should classify ASCII hot product smoke as hot_product_analysis, got $($chat.intent)"

    Write-Host '[smoke_agent_performance] GET /api/v1/ai-chat/tasks/{id}/timeline'
    $timeline = Wait-AiChatTimeline -TaskId $chat.taskId -Headers $headers -MinEvents 3 -MaxAttempts 5
    Assert-True ($timeline.events.Count -ge 3) 'AI Chat should have first progress within 5s'
    $eventTypes = (($timeline.events | ForEach-Object { $_.event_type }) -join ',')
    Assert-True ($eventTypes -match 'queued|task_classified|workflow_route_decided') "AI Chat timeline missing runtime stages: $eventTypes"

    Write-Host '[smoke_agent_performance] GET /api/v1/agent-runtime/tasks/{id}/diagnosis'
    $diagnosis = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/agent-runtime/tasks/$($chat.taskId)/diagnosis" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($diagnosis.found -eq $true) 'Diagnosis endpoint did not find AI Chat task'
    Assert-True ($null -ne $diagnosis.modelCalls) 'Diagnosis missing modelCalls'
    Assert-True ($null -ne $diagnosis.toolCalls) 'Diagnosis missing toolCalls'
    Assert-True ($diagnosis.recommendations.Count -ge 1) 'Diagnosis missing recommendations'

    $jobBody = @{ jobType = 'product_optimization'; title = 'Perf product optimization'; params = @{} } | ConvertTo-Json -Depth 5
    $jobStarted = [Diagnostics.Stopwatch]::StartNew()
    Write-Host '[smoke_agent_performance] POST /api/v1/agents/{agentId}/jobs'
    $jobResponse = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/agents/product-assistant/jobs" -Headers $headers -Body $jobBody -TimeoutSec $RequestTimeoutSec
    $job = $jobResponse.job
    $jobAcceptedMs = $jobStarted.ElapsedMilliseconds
    Assert-True ($jobAcceptedMs -lt 5000) "Standard agent job should return jobId within 5s, got $jobAcceptedMs"
    Assert-True ($job.runtimeProfile -eq 'standard') "Standard agent job runtimeProfile should be standard, got $($job.runtimeProfile)"

    Write-Host '[smoke_agent_performance] GET /api/v1/agent-runtime/slow-tasks'
    $slowTasks = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/agent-runtime/slow-tasks?limit=5" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($null -ne $slowTasks.tasks) 'slow-tasks endpoint missing tasks'

    [pscustomobject]@{
        status = 'ok'
        email = $email
        shopId = $shopId
        aiChatAcceptedMs = $acceptedMs
        aiChatIntent = $chat.intent
        diagnosisModelCalls = $diagnosis.modelCalls
        diagnosisToolCalls = $diagnosis.toolCalls
        standardJobAcceptedMs = $jobAcceptedMs
        standardJobRuntimeProfile = $job.runtimeProfile
        standardJobId = $job.jobId
    } | ConvertTo-Json
}
catch {
    Fail-Smoke $_.Exception.Message
}
