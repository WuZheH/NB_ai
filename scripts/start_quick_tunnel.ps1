[CmdletBinding()]
param(
    [string]$CloudflaredPath = $env:SEARCH_CLOUDFLARED,
    [uri]$Target = "http://127.0.0.1:8787",
    [string]$StateDirectory = $env:SEARCH_TUNNEL_STATE_DIR,
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30,
    [switch]$Check,
    [switch]$AllowParallel
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-Cloudflared {
    param(
        [string]$ConfiguredPath,
        [string]$ProjectRoot
    )

    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        $candidates.Add($ConfiguredPath)
    }
    $candidates.Add((Join-Path $ProjectRoot "tools\cloudflared\cloudflared.exe"))
    $candidates.Add((Join-Path $ProjectRoot "integrations\search_desktop\vendor\cloudflared.exe"))

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command "cloudflared.exe" -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    throw "未找到 cloudflared。请将现有可执行文件路径设置到 SEARCH_CLOUDFLARED；脚本不会下载或安装软件。"
}

function Assert-LoopbackTarget {
    param([uri]$Value)

    if (
        $Value.Scheme -ne "http" -or
        $Value.Host -notin @("127.0.0.1", "localhost") -or
        -not [string]::IsNullOrWhiteSpace($Value.UserInfo) -or
        -not [string]::IsNullOrWhiteSpace($Value.Query) -or
        -not [string]::IsNullOrWhiteSpace($Value.Fragment)
    ) {
        throw "Target 必须是无凭据的 loopback HTTP 地址，例如 http://127.0.0.1:8787。"
    }
    return $Value.AbsoluteUri.TrimEnd("/")
}

function Get-JsonHealth {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )

    Add-Type -AssemblyName System.Net.Http
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    try {
        $response = $client.GetAsync($Url).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            return [pscustomobject]@{ Ready = $false; Error = "http_$([int]$response.StatusCode)" }
        }
        $value = $body | ConvertFrom-Json
        $ready = $value.status -eq "ok" -and $value.service -eq "notebook-ai-mcp"
        return [pscustomobject]@{
            Ready = $ready
            Error = $(if ($ready) { $null } else { "unexpected_health_payload" })
        }
    }
    catch {
        return [pscustomobject]@{ Ready = $false; Error = "health_unreachable" }
    }
    finally {
        $client.Dispose()
    }
}

function Get-ExistingQuickTunnels {
    param([string]$Target)

    $processes = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue
    foreach ($item in @($processes)) {
        $commandLine = [string]$item.CommandLine
        if (
            $commandLine -match "(?i)\btunnel\b" -and
            $commandLine.IndexOf($Target, [StringComparison]::OrdinalIgnoreCase) -ge 0
        ) {
            [pscustomobject]@{ pid = [int]$item.ProcessId }
        }
    }
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$cloudflared = Resolve-Cloudflared -ConfiguredPath $CloudflaredPath -ProjectRoot $projectRoot
$targetUrl = Assert-LoopbackTarget -Value $Target
$localHealth = Get-JsonHealth -Url "$targetUrl/healthz" -TimeoutSeconds 3

if (-not $localHealth.Ready) {
    throw "本地 MCP 健康检查失败：$targetUrl/healthz。请先启动 Search 或 MCP 8787。"
}

if ($Check) {
    [pscustomobject]@{
        status = "ready"
        mode = "check"
        cloudflared = $cloudflared
        target = $targetUrl
        local_mcp_ready = $true
        process_started = $false
        chatgpt_configuration_changed = $false
    } | ConvertTo-Json -Compress
    exit 0
}

$existing = @(Get-ExistingQuickTunnels -Target $targetUrl)
if ($existing.Count -gt 0 -and -not $AllowParallel) {
    throw "检测到指向 $targetUrl 的现有 Quick Tunnel。脚本不会停止它；确认需要并行临时地址后请使用 -AllowParallel。"
}

if ([string]::IsNullOrWhiteSpace($StateDirectory)) {
    $StateDirectory = Join-Path $projectRoot ".codex_tmp\quick-tunnel"
}
$stateRoot = [System.IO.Path]::GetFullPath($StateDirectory)
[System.IO.Directory]::CreateDirectory($stateRoot) | Out-Null
$stamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss-fff")
$logFile = Join-Path $stateRoot "cloudflared-$stamp.log"

$arguments = @(
    "tunnel",
    "--url", $targetUrl,
    "--no-autoupdate",
    "--logfile", $logFile,
    "--loglevel", "info"
)
$startOptions = @{
    FilePath = $cloudflared
    ArgumentList = $arguments
    WindowStyle = "Hidden"
    PassThru = $true
}
$process = Start-Process @startOptions

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$publicUrl = $null
$publicHealth = $null
do {
    if ($process.HasExited) {
        throw "cloudflared 已提前退出（exit code $($process.ExitCode)）。日志：$logFile"
    }
    if (Test-Path -LiteralPath $logFile) {
        $source = Get-Content -LiteralPath $logFile -Raw -ErrorAction SilentlyContinue
        $matches = [regex]::Matches(
            [string]$source,
            "https://[a-z0-9-]+\.trycloudflare\.com",
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        if ($matches.Count -gt 0) {
            $publicUrl = $matches[$matches.Count - 1].Value.TrimEnd("/")
            $publicHealth = Get-JsonHealth -Url "$publicUrl/healthz" -TimeoutSeconds 3
            if ($publicHealth.Ready) {
                break
            }
        }
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

$publicReady = $null -ne $publicHealth -and $publicHealth.Ready
if ([string]::IsNullOrWhiteSpace($publicUrl) -or -not $publicReady) {
    throw "Quick Tunnel 未在 $TimeoutSeconds 秒内通过公网健康检查。进程保持运行，PID $($process.Id)；日志：$logFile"
}

[pscustomobject]@{
    status = "quick_tunnel_online"
    tunnel_type = "quick"
    public_url = $publicUrl
    mcp_url = "$publicUrl/mcp"
    pid = $process.Id
    health_checked_at = [DateTime]::UtcNow.ToString("o")
    log_file = $logFile
    chatgpt_configuration_changed = $false
} | ConvertTo-Json -Compress
