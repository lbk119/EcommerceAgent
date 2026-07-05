# Gateway + Python Brain end-to-end smoke test.
# Start Python Brain and Go Gateway first. Override gateway with $env:GATEWAY_URL.

param(
    [switch]$WaitAgentJob,
    [switch]$StrictAgentComplete
)

$ErrorActionPreference = 'Stop'

$GatewayUrl = if ($env:GATEWAY_URL) { $env:GATEWAY_URL.TrimEnd('/') } else { 'http://127.0.0.1:9090' }
$ApiBase = "$GatewayUrl/api/v1"
$RequestTimeoutSec = 120
$AgentWaitTimeoutSec = 180
$AgentPollIntervalSec = 5
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

try {
    Write-Host "[smoke_e2e] Gateway: $GatewayUrl"

    Write-Host '[smoke_e2e] GET /health'
    $health = Invoke-RestMethod -Method Get -Uri "$GatewayUrl/health" -TimeoutSec $RequestTimeoutSec
    Assert-True ($health.status -eq 'ok') 'GET /health did not return ok'

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

    Write-Host '[smoke_e2e] POST /api/v1/data-import/sample'
    $import = Invoke-RestMethod -Method Post -Uri "$ApiBase/data-import/sample" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($import.job.rows -gt 0) 'Sample import rows is 0'

    Write-Host '[smoke_e2e] GET /api/v1/workspace'
    $workspace = Invoke-RestMethod -Method Get -Uri "$ApiBase/workspace" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True ($null -ne $workspace.workspace.metrics) 'workspace.metrics is missing'
    Assert-True ($workspace.workspace.products.Count -gt 0) 'workspace.products is empty'
    Assert-True ($workspace.workspace.reports.Count -gt 0) 'workspace.reports is empty'
    $reportsBeforeAgent = $workspace.workspace.reports.Count

    $reportId = $workspace.workspace.reports[0].id
    Assert-True (-not [string]::IsNullOrWhiteSpace($reportId)) 'First report id is empty'
    Write-Host '[smoke_e2e] GET /api/v1/reports/{id}'
    $report = Invoke-RestMethod -Method Get -Uri "$ApiBase/reports/$reportId" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True (-not [string]::IsNullOrWhiteSpace($report.report.contentMarkdown)) 'report.contentMarkdown is empty'

    $analyzeBody = @{ title = 'Smoke Product Analysis'; params = @{} } | ConvertTo-Json -Depth 5
    Write-Host '[smoke_e2e] POST /api/v1/products/analyze'
    $productJob = Invoke-RestMethod -Method Post -Uri "$ApiBase/products/analyze" -Headers $headers -ContentType 'application/json' -Body $analyzeBody -TimeoutSec $RequestTimeoutSec
    $jobId = if ($productJob.job.jobId) { $productJob.job.jobId } else { $productJob.job.id }
    Assert-True (-not [string]::IsNullOrWhiteSpace($jobId)) 'products/analyze did not return jobId'

    Write-Host '[smoke_e2e] GET /api/v1/agents/jobs/{id}'
    $jobDetail = Invoke-RestMethod -Method Get -Uri "$ApiBase/agents/jobs/$jobId" -Headers $headers -TimeoutSec $RequestTimeoutSec
    Assert-True (-not [string]::IsNullOrWhiteSpace($jobDetail.job.id)) 'job detail did not return job.id'

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
