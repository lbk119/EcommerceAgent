# Gateway + Python Brain end-to-end smoke test.
# Start Python Brain and Go Gateway first. Override gateway with $env:GATEWAY_URL.

param(
    [switch]$WaitAgentJob,
    [switch]$StrictAgentComplete,
    [switch]$AllowNonMysqlUserStore
)

$ErrorActionPreference = 'Stop'

$GatewayUrl = if ($env:GATEWAY_URL) { $env:GATEWAY_URL.TrimEnd('/') } else { 'http://127.0.0.1:9090' }
$ApiBase = "$GatewayUrl/api/v1"
$RequestTimeoutSec = 120
$AgentWaitTimeoutSec = 180
$AgentPollIntervalSec = 5
$AgentEventSmokeMaxMs = 3000
$StillRunningMessage = "$([char]0x4EFB)$([char]0x52A1)$([char]0x4ECD)$([char]0x5728)$([char]0x540E)$([char]0x53F0)$([char]0x6267)$([char]0x884C)"

function Fail-Smoke {
    param([string]$Message)
    Write-Error "[smoke_e2e] $Message"
    exit 1
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        Fail-Smoke $Message
    }
}

function Assert-DisplaySku {
    param([string]$Sku)
    Assert-True (-not [string]::IsNullOrWhiteSpace($Sku)) 'SKU is empty'
    Assert-True ($Sku.Length -le 32) "SKU is too long: $Sku"
    Assert-True (-not $Sku.Contains([string][char]0x00E5)) "SKU contains mojibake char: $Sku"
    Assert-True (-not ($Sku -match '__+')) "SKU contains repeated underscores: $Sku"
    Assert-True (-not ($Sku -match '[0-9a-fA-F]{8}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{12}')) "SKU contains UUID: $Sku"
    Assert-True ($Sku -match '^[A-Z0-9-]+$') "SKU contains non-display chars: $Sku"
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
        foreach ($key in $Headers.Keys) {
            $request.Headers[[string]$key] = [string]$Headers[$key]
        }
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
    $text = $reader.ReadToEnd()
    return $text | ConvertFrom-Json
}

function Wait-AiChatTimeline {
    param(
        [string]$TaskId,
        [int]$MinEvents = 3,
        [string]$RequiredEventType = '',
        [int]$RequiredEventCount = 1,
        [int]$MaxAttempts = 30
    )
    $timeline = $null
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt += 1) {
        $timeline = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/ai-chat/tasks/$TaskId/timeline" -Headers $headers -TimeoutSec $RequestTimeoutSec
        $events = @($timeline.events)
        $requiredEvents = if ($RequiredEventType) { @($events | Where-Object { $_.event_type -eq $RequiredEventType }) } else { @() }
        if ($events.Count -ge $MinEvents -and (-not $RequiredEventType -or $requiredEvents.Count -ge $RequiredEventCount)) {
            return $timeline
        }
        Start-Sleep -Seconds 1
    }
    return $timeline
}

function Wait-AiChatMessage {
    param(
        [string]$MessageId,
        [int]$MaxAttempts = 20
    )
    $message = $null
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt += 1) {
        $messageResponse = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/ai-chat/messages/$MessageId" -Headers $headers -TimeoutSec $RequestTimeoutSec
        $message = $messageResponse.message
        if ($message.status -in @('completed', 'failed', 'timeout', 'cancelled')) {
            return $message
        }
        Start-Sleep -Seconds 2
    }
    return $message
}

function Assert-ParallelPlanTimeline {
    param(
        [object]$Timeline,
        [string]$TaskName
    )
    $events = @($Timeline.events)
    $planStarted = @($events | Where-Object { $_.event_type -eq 'plan_step_started' })
    $started = if ($planStarted.Count -gt 0) { $planStarted } else { @($events | Where-Object { $_.event_type -eq 'workflow_step_started' }) }
    Assert-True ($started.Count -ge 2) "$TaskName should start at least 2 agent/tool events, got $($started.Count)"
    $planFinished = @($events | Where-Object { $_.event_type -eq 'plan_step_finished' })
    $finished = if ($planFinished.Count -gt 0) { $planFinished } else { @($events | Where-Object { $_.event_type -eq 'workflow_step_finished' }) }
    $planFailed = @($events | Where-Object { $_.event_type -eq 'plan_step_failed' })
    $failed = if ($planFailed.Count -gt 0) { $planFailed } else { @($events | Where-Object { $_.event_type -eq 'workflow_step_failed' }) }
    $terminalSteps = @($finished + $failed)
    Assert-True ($terminalSteps.Count -ge 2) "$TaskName should finish or budget-stop at least 2 agent/tool events, got $($terminalSteps.Count)"
    $slowSteps = @($finished | Where-Object { $_.latency_ms -and [double]$_.latency_ms -gt $AgentEventSmokeMaxMs })
    Assert-True ($slowSteps.Count -eq 0) "$TaskName agent/tool event should be <= ${AgentEventSmokeMaxMs}ms in local smoke; slow steps=$($slowSteps.Count)"
}

function Assert-StandardExecutionBudget {
    param(
        [string]$TaskId,
        [double]$MaxSeconds = 12
    )
    $timeline = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/traces/$TaskId/timeline" -Headers $headers -TimeoutSec $RequestTimeoutSec
    $events = @($timeline.events)
    $startEvent = $events | Where-Object { $_.event_type -in @('prompt_guard_started', 'plan_execution_started') } | Select-Object -First 1
    $finishEvent = $events | Where-Object { $_.event_type -eq 'agent_finished' } | Select-Object -Last 1
    Assert-True ($null -ne $startEvent -and $null -ne $finishEvent) 'standard job trace did not include start/finish events'
    $elapsedSeconds = ([datetime]::Parse([string]$finishEvent.timestamp) - [datetime]::Parse([string]$startEvent.timestamp)).TotalSeconds
    Assert-True ($elapsedSeconds -lt $MaxSeconds) "standard product analyze execution exceeded ${MaxSeconds}s: ${elapsedSeconds}s"
    Assert-ParallelPlanTimeline -Timeline $timeline -TaskName 'standard_product_optimization'
}


try {
    Write-Host "[smoke_e2e] Gateway: $GatewayUrl"

    Write-Host '[smoke_e2e] GET /health'
    $health = Invoke-RestMethod -Method Get -Uri "$GatewayUrl/health" -TimeoutSec $RequestTimeoutSec
    Assert-True ($health.status -eq 'ok') 'GET /health did not return ok'
    if (-not $AllowNonMysqlUserStore) {
        Assert-True ($health.user_store_backend -eq 'mysql') "Gateway user store backend must be mysql, got '$($health.user_store_backend)'. Use -AllowNonMysqlUserStore only for dev/test memory store."
    }

    $stamp = Get-Date -Format 'yyyyMMddHHmmssfff'
    $email = "smoke_$stamp@example.com"
    $password = 'Admin123456'

    $registerBody = @{
        companyName = 'Smoke Test Team'
        name = 'Smoke User'
        email = $email
        password = $password
        confirmPassword = $password
    } | ConvertTo-Json
    Write-Host '[smoke_e2e] POST /api/v1/auth/register'
    $auth = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/register" -ContentType 'application/json' -Body $registerBody -TimeoutSec $RequestTimeoutSec
    $token = $auth.accessToken
    Assert-True (-not [string]::IsNullOrWhiteSpace($token)) 'Register response token is empty'

    $headers = @{ Authorization = "Bearer $token" }
    $onboardingBody = @{
        shopName = "Smoke Orange Shop $stamp"
        category = 'Orange'
        shopType = 'Brand Owned'
        businessStage = 'Growth'
        selectedPlatforms = @('Taobao')
        dataMode = 'sample'
        enabledAgentIds = @('store-analyst', 'product-assistant', 'inventory-inspector', 'campaign-reviewer', 'report-specialist')
    } | ConvertTo-Json -Depth 5
    Write-Host '[smoke_e2e] POST /api/v1/onboarding/complete'
    $onboarding = Invoke-RestMethod -Method Post -Uri "$ApiBase/onboarding/complete" -Headers $headers -ContentType 'application/json' -Body $onboardingBody -TimeoutSec $RequestTimeoutSec
    $shopId = $onboarding.workspace.currentShopId
    Assert-True (-not [string]::IsNullOrWhiteSpace($shopId)) 'Onboarding did not return currentShopId'
    $headers['X-Shop-ID'] = $shopId

    $shopBody = @{
        shop_id = $shopId
        shop_name = $onboarding.workspace.shops[0].name
    } | ConvertTo-Json -Depth 5
    Write-Host '[smoke_e2e] POST /api/v1/auth/shops'
    $auth = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/shops" -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' -Body $shopBody -TimeoutSec $RequestTimeoutSec
    $token = $auth.accessToken
    Assert-True (-not [string]::IsNullOrWhiteSpace($token)) 'auth/shops response token is empty'
    $headers = @{ Authorization = "Bearer $token"; 'X-Shop-ID' = $shopId }

    Write-Host '[smoke_e2e] POST /api/v1/account/onboarding-completed'
    $account = Invoke-RestMethod -Method Post -Uri "$ApiBase/account/onboarding-completed" -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec $RequestTimeoutSec
    Assert-True ([bool]$account.user.onboardingCompleted) 'Account onboardingCompleted was not persisted'

    Write-Host '[smoke_e2e] POST /api/v1/data-import/sample'
    $import = Invoke-RestMethod -Method Post -Uri "$ApiBase/data-import/sample" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($import.job.rows -gt 0) 'Sample import rows is 0'

    Write-Host '[smoke_e2e] GET /api/v1/workspace'
    $workspace = Invoke-RestMethod -Method Get -Uri "$ApiBase/workspace" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($null -ne $workspace.workspace.metrics) 'workspace.metrics is missing'
    Assert-True ($workspace.workspace.products.Count -gt 0) 'workspace.products is empty'
    Assert-True ($workspace.workspace.reports.Count -gt 0) 'workspace.reports is empty'
    foreach ($product in $workspace.workspace.products) {
        Assert-DisplaySku ([string]$product.sku)
    }
    $reportsBeforeAgent = $workspace.workspace.reports.Count

    $seasonBody = '{"content":"\u8fd9\u4e2a\u5b63\u8282\u9002\u5408\u5356\u4ec0\u4e48\u4e1c\u897f"}'
    $optBody = '{"content":"\u54ea\u4e2a\u5546\u54c1\u6700\u503c\u5f97\u4f18\u5316"}'
    $hotBody = '{"content":"\u80fd\u4e0d\u80fd\u63a8\u8350\u6211\u6700\u8fd1\u7206\u54c1"}'
    $cancelBody = '{"content":"\u8bf7\u505a\u4e00\u4e2a\u9700\u8981\u6df1\u5ea6\u63a8\u7406\u7684\u957f\u4efb\u52a1\u5206\u6790"}'
    Write-Host '[smoke_e2e] POST /api/v1/ai-chat/messages seasonal selection'
    $seasonWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $seasonChat = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/messages" -Headers $headers -Body $seasonBody -TimeoutSec $RequestTimeoutSec
    $seasonWatch.Stop()
    Write-Host '[smoke_e2e] POST /api/v1/ai-chat/messages product optimization'
    $optChat = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/messages" -Headers $headers -Body $optBody -TimeoutSec $RequestTimeoutSec
    Write-Host '[smoke_e2e] POST /api/v1/ai-chat/messages hot product'
    $hotChat = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/messages" -Headers $headers -Body $hotBody -TimeoutSec $RequestTimeoutSec
    Write-Host '[smoke_e2e] POST /api/v1/ai-chat/messages cancel probe'
    $cancelChat = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/messages" -Headers $headers -Body $cancelBody -TimeoutSec $RequestTimeoutSec
    Assert-True ($seasonWatch.Elapsed.TotalSeconds -lt 3) "Seasonal AI chat acceptance took too long: $($seasonWatch.Elapsed.TotalSeconds)s"
    Assert-True ($seasonChat.acceptedLatencyMs -lt 1000) "Backend AI chat acceptedLatencyMs should be < 1000ms, got $($seasonChat.acceptedLatencyMs)"
    Assert-True ($seasonChat.status -eq 'running' -and $seasonChat.source -eq 'agent') 'Seasonal AI chat did not return running agent task acceptance'
    Assert-True (-not [string]::IsNullOrWhiteSpace($seasonChat.messageId)) 'Seasonal AI chat messageId is empty'
    Assert-True (-not [string]::IsNullOrWhiteSpace($seasonChat.taskId)) 'Seasonal AI chat taskId is empty'
    Assert-True ($seasonChat.wsThreadId -eq $seasonChat.conversationId) 'AI chat wsThreadId must equal conversationId'
    Assert-True ($seasonChat.intent -eq 'seasonal_selection') "Seasonal AI chat intent mismatch: $($seasonChat.intent)"
    Assert-True ($optChat.intent -eq 'product_optimization') "Optimization AI chat intent mismatch: $($optChat.intent)"
    Assert-True ($hotChat.intent -eq 'hot_product_analysis') "Hot product AI chat intent mismatch: $($hotChat.intent)"
    Assert-True ($seasonChat.intent -ne $optChat.intent -and $optChat.intent -ne $hotChat.intent) 'AI chat intents should differ across seasonal/optimization/hot product questions'
    foreach ($chat in @($seasonChat, $optChat, $hotChat)) {
        Assert-True ($chat.source -ne 'backend' -and $chat.source -ne 'local_fallback' -and $chat.source -ne 'fallback') 'AI chat used forbidden backend/local fallback source'
    }
    $seasonTimeline = Wait-AiChatTimeline -TaskId $seasonChat.taskId -MinEvents 8 -RequiredEventType 'plan_step_finished' -RequiredEventCount 2
    Assert-True ($seasonTimeline.events -and $seasonTimeline.events.Count -ge 3) 'AI chat timeline did not contain at least 3 real progress events'
    $seasonEventTypes = ($seasonTimeline.events | ForEach-Object { $_.event_type }) -join ','
    Assert-True ($seasonEventTypes -match 'queued|prompt_guard_started|task_classified|workflow_route_decided') "AI chat timeline does not contain expected runtime stages: $seasonEventTypes"
    Assert-True ($seasonEventTypes -match 'plan_execution_started|plan_step_started') "AI chat timeline missing plan-first parallel execution events: $seasonEventTypes"
    Assert-ParallelPlanTimeline -Timeline $seasonTimeline -TaskName 'seasonal_selection'
    $optTimeline = Wait-AiChatTimeline -TaskId $optChat.taskId -MinEvents 8 -RequiredEventType 'plan_step_finished' -RequiredEventCount 2
    Assert-ParallelPlanTimeline -Timeline $optTimeline -TaskName 'product_optimization'
    $healthRuntime = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/agent-runtime/health" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($healthRuntime.agentRuntime -eq 'ok') 'Agent runtime health did not return ok'
    Assert-True ($null -ne $healthRuntime.monitor.websocketManager) 'Agent runtime health missing monitor status'
    Assert-True ($null -ne $healthRuntime.tracer.backend) 'Agent runtime health missing tracer backend'
    Assert-True ($null -ne $healthRuntime.memory.store) 'Agent runtime health missing memory store'
    Assert-True ($null -ne $healthRuntime.evolution.policyProposalEnabled) 'Agent runtime health missing evolution status'
    $runtimeMetrics = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/agent-runtime/metrics" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($null -ne $runtimeMetrics.chat_latency) 'Agent runtime metrics missing chat_latency'
    Assert-True ($null -ne $runtimeMetrics.workflow_latency) 'Agent runtime metrics missing workflow_latency'
    Assert-True ($null -ne $runtimeMetrics.llm_latency) 'Agent runtime metrics missing llm_latency'
    Assert-True ($null -ne $runtimeMetrics.slow_stages) 'Agent runtime metrics missing slow_stages'
    Assert-True ($null -ne $runtimeMetrics.taskQueue) 'Agent runtime metrics missing taskQueue'
    $slowTasks = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/agent-runtime/slow-tasks" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($null -ne $slowTasks.tasks) 'Agent runtime slow-tasks endpoint missing tasks'
    $cancelProbe = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/tasks/$($cancelChat.taskId)/cancel" -Headers $headers -Body '{}' -TimeoutSec $RequestTimeoutSec
    Assert-True ($cancelProbe.status -in @('cancelled', 'completed', 'failed', 'timeout')) "AI chat cancel endpoint returned unexpected status: $($cancelProbe.status)"
    $seasonMessage = Wait-AiChatMessage -MessageId $seasonChat.messageId -MaxAttempts 10
    Assert-True ($null -ne $seasonMessage) 'AI chat message query returned empty'
    if ($seasonMessage.status -eq 'completed') {
        Assert-True (-not [string]::IsNullOrWhiteSpace($seasonMessage.content)) 'Completed AI chat assistant content is empty'
        Assert-True ($seasonMessage.source -in @('agent', 'deepagents_native')) "Completed AI chat source is invalid: $($seasonMessage.source)"
        Assert-True ($null -ne $seasonMessage.structuredResult) 'Completed AI chat structuredResult is empty'
        Assert-True (-not [string]::IsNullOrWhiteSpace($seasonMessage.structuredResult.conclusion)) 'Completed AI chat structuredResult.conclusion is empty'
    } else {
        Assert-True ($seasonMessage.status -in @('running', 'failed', 'timeout', 'cancelled')) "AI chat message has unexpected status: $($seasonMessage.status)"
    }

    $loginBody = @{
        account = $email
        password = $password
    } | ConvertTo-Json
    Write-Host '[smoke_e2e] POST /api/v1/auth/login after import'
    $loginAgain = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/login" -ContentType 'application/json' -Body $loginBody -TimeoutSec $RequestTimeoutSec
    Assert-True ([bool]$loginAgain.user.onboardingCompleted) 'Relogin user lost onboardingCompleted'
    $reloginHeaders = @{ Authorization = "Bearer $($loginAgain.accessToken)" }
    Write-Host '[smoke_e2e] GET /api/v1/workspace after relogin without X-Shop-ID'
    $workspaceAfterRelogin = Invoke-RestMethod -Method Get -Uri "$ApiBase/workspace" -Headers $reloginHeaders -TimeoutSec $RequestTimeoutSec
    Assert-True ($workspaceAfterRelogin.workspace.products.Count -gt 0) 'Relogin workspace products is empty'
    Assert-True ($workspaceAfterRelogin.workspace.reports.Count -gt 0) 'Relogin workspace reports is empty'
    Assert-True ($workspaceAfterRelogin.workspace.currentShopId -eq $shopId) 'Relogin workspace did not use persisted default shop'
    foreach ($product in $workspaceAfterRelogin.workspace.products) {
        Assert-DisplaySku ([string]$product.sku)
    }

    $reportId = $workspace.workspace.reports[0].id
    Assert-True (-not [string]::IsNullOrWhiteSpace($reportId)) 'First report id is empty'
    Write-Host '[smoke_e2e] GET /api/v1/reports/{id}'
    $report = Invoke-RestMethod -Method Get -Uri "$ApiBase/reports/$reportId" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True (-not [string]::IsNullOrWhiteSpace($report.report.contentMarkdown)) 'report.contentMarkdown is empty'

    $analyzeBody = @{ title = 'Smoke Product Analysis'; params = @{} } | ConvertTo-Json -Depth 5
    Write-Host '[smoke_e2e] POST /api/v1/products/analyze'
    $standardAcceptWatch = [Diagnostics.Stopwatch]::StartNew()
    $productJob = Invoke-RestMethod -Method Post -Uri "$ApiBase/products/analyze" -Headers $headers -ContentType 'application/json' -Body $analyzeBody -TimeoutSec $RequestTimeoutSec
    $standardAcceptWatch.Stop()
    Assert-True ($standardAcceptWatch.Elapsed.TotalSeconds -lt 5) "products/analyze should accept background job within 5s, elapsed=$($standardAcceptWatch.Elapsed.TotalSeconds)s"
    $jobId = if ($productJob.job.jobId) { $productJob.job.jobId } else { $productJob.job.id }
    Assert-True (-not [string]::IsNullOrWhiteSpace($jobId)) 'products/analyze did not return jobId'
    if ($productJob.job.runtimeProfile) {
        Assert-True ($productJob.job.runtimeProfile -eq 'standard') "products/analyze should use standard runtimeProfile, got $($productJob.job.runtimeProfile)"
    }

    Write-Host '[smoke_e2e] GET /api/v1/agents/jobs/{id}'
    $jobDetail = Invoke-RestMethod -Method Get -Uri "$ApiBase/agents/jobs/$jobId" -Headers $headers -TimeoutSec $RequestTimeoutSec
    $standardDeadline = (Get-Date).AddSeconds(60)
    while ($jobDetail.job.status -eq 'running' -and (Get-Date) -lt $standardDeadline) {
        Start-Sleep -Seconds 2
        $jobDetail = Invoke-RestMethod -Method Get -Uri "$ApiBase/agents/jobs/$jobId" -Headers $headers -TimeoutSec $RequestTimeoutSec
    }
    Assert-True (-not [string]::IsNullOrWhiteSpace($jobDetail.job.id)) 'job detail did not return job.id'
    Assert-True ($jobDetail.job.status -eq 'completed') "standard product analyze job should complete, status=$($jobDetail.job.status)"
    Assert-True ($null -ne $jobDetail.job.structuredResult) 'Completed agent job structuredResult is empty'
    Assert-StandardExecutionBudget -TaskId $productJob.job.taskId -MaxSeconds 12
    if (-not [string]::IsNullOrWhiteSpace($jobDetail.job.resultReportId)) {
        $generatedReport = Invoke-RestMethod -Method Get -Uri "$ApiBase/reports/$($jobDetail.job.resultReportId)" -Headers $headers -TimeoutSec $RequestTimeoutSec
        Assert-True ($null -ne $generatedReport.report.structuredResult) 'Generated report structuredResult is empty'
        Assert-True (-not [string]::IsNullOrWhiteSpace($generatedReport.report.structuredResult.conclusion)) 'Generated report structuredResult.conclusion is empty'
    }

    $agentWaitTimedOut = $false
    if ($WaitAgentJob) {
        Write-Host "[smoke_e2e] waiting for agent job up to $AgentWaitTimeoutSec seconds"
        $deadline = (Get-Date).AddSeconds($AgentWaitTimeoutSec)
        while ((Get-Date) -lt $deadline) {
            $jobDetail = Invoke-RestMethod -Method Get -Uri "$ApiBase/agents/jobs/$jobId" -Headers $headers -TimeoutSec $RequestTimeoutSec
            if ($jobDetail.job.status -eq 'completed') {
                Write-Host '[smoke_e2e] agent job completed; refreshing workspace'
                $workspace = Invoke-RestMethod -Method Get -Uri "$ApiBase/workspace" -Headers $headers -TimeoutSec $RequestTimeoutSec
                Assert-True ($workspace.workspace.reports.Count -ge $reportsBeforeAgent) 'reports count decreased after agent completion'
                break
            }
            if ($jobDetail.job.status -eq 'failed' -or $jobDetail.job.status -eq 'cancelled') {
                $message = if ($jobDetail.job.errorMessage) { $jobDetail.job.errorMessage } else { "Agent job $($jobDetail.job.status)" }
                Fail-Smoke $message
            }
            Start-Sleep -Seconds $AgentPollIntervalSec
        }

        if ($jobDetail.job.status -ne 'completed') {
            $agentWaitTimedOut = $true
            Write-Host "[smoke_e2e] $StillRunningMessage"
            if ($StrictAgentComplete) {
                Fail-Smoke 'Agent job did not complete before timeout'
            }
        }
    }

    [pscustomobject]@{
        status = 'ok'
        email = $email
        shopId = $shopId
        gmv = $workspace.workspace.metrics.gmv
        products = $workspace.workspace.products.Count
        reports = $workspace.workspace.reports.Count
        reportId = $reportId
        productAnalyzeJobId = $jobId
        productAnalyzeJobStatus = $jobDetail.job.status
        waitedForAgentJob = [bool]$WaitAgentJob
        strictAgentComplete = [bool]$StrictAgentComplete
        agentWaitTimedOut = $agentWaitTimedOut
    } | ConvertTo-Json -Depth 5
    exit 0
} catch {
    Fail-Smoke $_.Exception.Message
}
