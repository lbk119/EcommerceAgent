# Fresh evaluation smoke for reset + generated order import + AI Chat completion.
# Start Python Brain and Go Gateway first. Override gateway with $env:GATEWAY_URL.

[CmdletBinding()]
param(
    [switch]$SkipReset,
    [switch]$AllowNonDevDatabase,
    [string]$SamplePath = 'data\sample_import_200_orders.csv'
)

$ErrorActionPreference = 'Stop'

$GatewayUrl = if ($env:GATEWAY_URL) { $env:GATEWAY_URL.TrimEnd('/') } else { 'http://127.0.0.1:9090' }
$ApiBase = "$GatewayUrl/api/v1"
$RequestTimeoutSec = 120

function Fail-Smoke {
    param([string]$Message)
    Write-Error "[smoke_fresh_eval] $Message"
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

function Invoke-MultipartUpload {
    param(
        [string]$Uri,
        [hashtable]$Headers,
        [string]$FilePath,
        [int]$TimeoutSec
    )
    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
    try {
        if ($Headers) {
            foreach ($key in $Headers.Keys) {
                $null = $client.DefaultRequestHeaders.TryAddWithoutValidation([string]$key, [string]$Headers[$key])
            }
        }
        $form = New-Object System.Net.Http.MultipartFormDataContent
        $stream = [System.IO.File]::OpenRead($FilePath)
        try {
            $content = New-Object System.Net.Http.StreamContent($stream)
            $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('text/csv')
            $form.Add($content, 'file', [System.IO.Path]::GetFileName($FilePath))
            $response = $client.PostAsync($Uri, $form).GetAwaiter().GetResult()
            $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) { throw $text }
            return ($text | ConvertFrom-Json)
        }
        finally {
            $form.Dispose()
            $stream.Dispose()
        }
    }
    finally {
        $client.Dispose()
    }
}

function Wait-AiChatMessage {
    param([string]$MessageId, [hashtable]$Headers, [int]$MaxAttempts = 30)
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt += 1) {
        $response = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/ai-chat/messages/$MessageId" -Headers $Headers -TimeoutSec $RequestTimeoutSec
        $message = $response.message
        if ($message.status -in @('completed', 'failed', 'timeout', 'cancelled')) { return $message }
        Start-Sleep -Seconds 2
    }
    return $message
}

try {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    Push-Location $ProjectRoot
    try {
        Write-Host "[smoke_fresh_eval] Gateway: $GatewayUrl"
        Write-Host '[smoke_fresh_eval] GET /health'
        $health = Invoke-JsonUtf8 -Method 'GET' -Uri "$GatewayUrl/health" -TimeoutSec 20
        Assert-True ($health.status -eq 'ok') 'Gateway health is not ok'

        if (-not $SkipReset) {
            Write-Host '[smoke_fresh_eval] reset default dev data'
            & (Join-Path $PSScriptRoot 'reset_dev_data.ps1') -ConfirmResetDevData -AllowNonDevDatabase:$AllowNonDevDatabase
            if ($LASTEXITCODE -ne 0) { Fail-Smoke 'reset_dev_data.ps1 failed' }
        }

        Write-Host '[smoke_fresh_eval] generate 200-order CSV'
        & (Join-Path $PSScriptRoot 'generate_sample_orders.ps1') -OutputPath $SamplePath -Rows 200
        if ($LASTEXITCODE -ne 0) { Fail-Smoke 'generate_sample_orders.ps1 failed' }
        $csvPath = (Resolve-Path -LiteralPath $SamplePath).Path
        $csvRows = @((Import-Csv -LiteralPath $csvPath))
        Assert-True ($csvRows.Count -eq 200) "generated CSV should contain 200 rows, got $($csvRows.Count)"

        $stamp = Get-Date -Format 'yyyyMMddHHmmssfff'
        $email = "fresh_$stamp@example.com"
        $password = 'Admin123456'
        $registerBody = @{
            companyName = 'Fresh Eval Team'
            name = 'Fresh Eval User'
            email = $email
            password = $password
            confirmPassword = $password
        } | ConvertTo-Json
        Write-Host '[smoke_fresh_eval] POST /api/v1/auth/register'
        $auth = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/auth/register" -Body $registerBody -TimeoutSec $RequestTimeoutSec
        $headers = @{ Authorization = "Bearer $($auth.accessToken)" }

        $onboardingBody = @{
            shopName = "Fresh Eval Shop $stamp"
            category = 'Orange'
            shopType = 'Brand Owned'
            businessStage = 'Growth'
            selectedPlatforms = @('Taobao')
            dataMode = 'upload'
            enabledAgentIds = @('store-analyst', 'product-assistant', 'inventory-inspector', 'campaign-reviewer', 'report-specialist')
        } | ConvertTo-Json -Depth 5
        Write-Host '[smoke_fresh_eval] POST /api/v1/onboarding/complete'
        $onboarding = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/onboarding/complete" -Headers $headers -Body $onboardingBody -TimeoutSec $RequestTimeoutSec
        $shopId = $onboarding.workspace.currentShopId
        Assert-True (-not [string]::IsNullOrWhiteSpace($shopId)) 'onboarding did not return shop id'
        $headers['X-Shop-ID'] = $shopId

        $shopBody = @{ shop_id = $shopId; shop_name = $onboarding.workspace.shops[0].name } | ConvertTo-Json -Depth 5
        Write-Host '[smoke_fresh_eval] POST /api/v1/auth/shops'
        $auth = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/auth/shops" -Headers @{ Authorization = $headers.Authorization } -Body $shopBody -TimeoutSec $RequestTimeoutSec
        $headers = @{ Authorization = "Bearer $($auth.accessToken)"; 'X-Shop-ID' = $shopId }

        Write-Host '[smoke_fresh_eval] POST /api/v1/account/onboarding-completed'
        Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/account/onboarding-completed" -Headers $headers -Body '{}' -TimeoutSec $RequestTimeoutSec | Out-Null

        Write-Host '[smoke_fresh_eval] POST /api/v1/data-import/upload'
        $upload = Invoke-MultipartUpload -Uri "$ApiBase/data-import/upload" -Headers $headers -FilePath $csvPath -TimeoutSec $RequestTimeoutSec
        Assert-True (-not [string]::IsNullOrWhiteSpace($upload.job.id)) 'upload did not return job id'

        Write-Host '[smoke_fresh_eval] GET /api/v1/data-import/{id}/preview'
        $preview = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/data-import/$($upload.job.id)/preview" -Headers $headers -TimeoutSec $RequestTimeoutSec
        Assert-True ($preview.rows.Count -eq 20) "preview should show 20 rows, got $($preview.rows.Count)"
        Assert-True ($preview.fields.Count -ge 10) 'preview should detect import fields'

        Write-Host '[smoke_fresh_eval] POST /api/v1/data-import/{id}/confirm'
        $confirmed = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/data-import/$($upload.job.id)/confirm" -Headers $headers -Body '{}' -TimeoutSec $RequestTimeoutSec
        Assert-True ($confirmed.rows -eq 200) "confirmed import should write 200 rows, got $($confirmed.rows)"
        Assert-True ([bool]$confirmed.workspaceShouldRefresh) 'confirm response should request workspace refresh'
        Assert-True (-not [string]::IsNullOrWhiteSpace($confirmed.generatedReportId)) 'confirm response missing generatedReportId'

        Write-Host '[smoke_fresh_eval] GET /api/v1/workspace after confirm'
        $workspace = Invoke-JsonUtf8 -Method 'GET' -Uri "$ApiBase/workspace" -Headers $headers -TimeoutSec $RequestTimeoutSec
        Assert-True ($workspace.workspace.metrics.orders -ge 200) "workspace orders should be >= 200, got $($workspace.workspace.metrics.orders)"
        Assert-True ($workspace.workspace.products.Count -gt 0) 'workspace products is empty after import'
        Assert-True ($workspace.workspace.campaigns.Count -gt 0) 'workspace campaigns is empty after import'
        Assert-True ($workspace.workspace.reports.Count -gt 0) 'workspace reports is empty after import'
        Assert-True ($workspace.workspace.strategies.Count -gt 0) 'workspace strategies is empty after import'
        Assert-True ($workspace.workspace.metrics.inventoryRiskSkuCount -gt 0) 'workspace inventory risk count should be > 0'

        $chatBody = @{ content = 'fresh eval hot products and inventory risks' } | ConvertTo-Json
        Write-Host '[smoke_fresh_eval] POST /api/v1/ai-chat/messages'
        $chat = Invoke-JsonUtf8 -Method 'POST' -Uri "$ApiBase/ai-chat/messages" -Headers $headers -Body $chatBody -TimeoutSec $RequestTimeoutSec
        Assert-True ($chat.status -eq 'running') 'AI Chat should return running acceptance'
        Assert-True (-not [string]::IsNullOrWhiteSpace($chat.messageId)) 'AI Chat messageId is empty'
        Assert-True (-not [string]::IsNullOrWhiteSpace($chat.taskId)) 'AI Chat taskId is empty'
        Assert-True ($chat.wsThreadId -eq $chat.conversationId) 'AI Chat wsThreadId must equal conversationId'

        Write-Host '[smoke_fresh_eval] poll AI Chat message completion'
        $message = Wait-AiChatMessage -MessageId $chat.messageId -Headers $headers -MaxAttempts 30
        Assert-True ($null -ne $message) 'AI Chat message query returned empty'
        Assert-True ($message.status -eq 'completed') "AI Chat message should complete, got $($message.status)"
        Assert-True (-not [string]::IsNullOrWhiteSpace($message.content)) 'completed AI Chat content is empty'
        Assert-True ($null -ne $message.structuredResult) 'completed AI Chat structuredResult is empty'

        [pscustomobject]@{
            status = 'ok'
            email = $email
            shopId = $shopId
            csvRows = $csvRows.Count
            importedRows = $confirmed.rows
            products = $workspace.workspace.products.Count
            campaigns = $workspace.workspace.campaigns.Count
            inventoryRisks = $workspace.workspace.metrics.inventoryRiskSkuCount
            aiChatStatus = $message.status
            aiChatTaskId = $chat.taskId
        } | ConvertTo-Json
    }
    finally {
        Pop-Location
    }
}
catch {
    Fail-Smoke $_.Exception.Message
}