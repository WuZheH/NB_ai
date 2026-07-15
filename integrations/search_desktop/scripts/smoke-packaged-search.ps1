[CmdletBinding()]
param(
    [string]$ExecutablePath,
    [string]$ProjectRoot,
    [string]$PythonExe = "D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe",
    [string]$TestRoot,
    [ValidateRange(10, 120)]
    [int]$HoldSeconds = 10,
    [ValidateRange(30, 180)]
    [int]$RuntimeReadyTimeoutSeconds = 120,
    [ValidateRange(10, 180)]
    [int]$CleanupTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$DesktopRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $ExecutablePath) {
    $ExecutablePath = Join-Path $DesktopRoot "dist\win-unpacked\Search.exe"
}
if (-not $ProjectRoot) {
    $ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $DesktopRoot "..\.."))
}
if (-not $TestRoot) {
    $TestRoot = Join-Path $ProjectRoot ".codex_tmp\search-desktop-startup-0.1.2\packaged-smoke"
}

$ExecutablePath = [System.IO.Path]::GetFullPath($ExecutablePath)
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$PythonExe = [System.IO.Path]::GetFullPath($PythonExe)
$TestRoot = [System.IO.Path]::GetFullPath($TestRoot)
$LauncherScript = Join-Path $ProjectRoot "scripts\runtime\notebook_ai_launcher.py"

foreach ($RequiredPath in @($ExecutablePath, $PythonExe, $LauncherScript)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "search_packaged_smoke_required_file_missing:$RequiredPath"
    }
}

function Get-ExactSearchProcesses {
    param([Parameter(Mandatory = $true)][string]$ExpectedExecutable)

    $Expected = [System.IO.Path]::GetFullPath($ExpectedExecutable)
    @(Get-CimInstance Win32_Process -Filter "Name = 'Search.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            [System.IO.Path]::GetFullPath([string]$_.ExecutablePath).Equals(
                $Expected,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        })
}

function Get-DescendantProcessIds {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][object[]]$ProcessSnapshot
    )

    $Known = [System.Collections.Generic.HashSet[int]]::new()
    [void]$Known.Add($RootProcessId)
    $Changed = $true
    while ($Changed) {
        $Changed = $false
        foreach ($Candidate in $ProcessSnapshot) {
            $CandidateId = [int]$Candidate.ProcessId
            if (-not $Known.Contains($CandidateId) -and $Known.Contains([int]$Candidate.ParentProcessId)) {
                [void]$Known.Add($CandidateId)
                $Changed = $true
            }
        }
    }
    @($Known)
}

function Stop-SmokeSearchProcessTree {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedExecutable
    )

    $Snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $OwnedIds = @(Get-DescendantProcessIds -RootProcessId $RootProcessId -ProcessSnapshot $Snapshot)
    $AllowedIds = [System.Collections.Generic.List[int]]::new()
    foreach ($Candidate in $Snapshot) {
        $CandidateId = [int]$Candidate.ProcessId
        if (-not ($OwnedIds -contains $CandidateId)) { continue }
        if (-not $Candidate.ExecutablePath) { continue }
        $Observed = [System.IO.Path]::GetFullPath([string]$Candidate.ExecutablePath)
        if ($Observed.Equals($ExpectedExecutable, [System.StringComparison]::OrdinalIgnoreCase)) {
            $AllowedIds.Add($CandidateId)
        }
    }

    # Children are stopped before the browser process.  Every PID is checked
    # against the exact packaged executable before Stop-Process is called.
    foreach ($ProcessId in @($AllowedIds | Sort-Object -Descending)) {
        $Current = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
        if (-not $Current -or -not $Current.ExecutablePath) { continue }
        $Observed = [System.IO.Path]::GetFullPath([string]$Current.ExecutablePath)
        if (-not $Observed.Equals($ExpectedExecutable, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "search_packaged_smoke_process_identity_changed:$ProcessId"
        }
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    }

    return @($AllowedIds)
}

function Get-PortOwners {
    param([int[]]$Ports)

    $Owners = @{}
    foreach ($Port in $Ports) {
        $Owners[[string]$Port] = @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }
    $Owners
}

function Assert-PortOwnershipUnchanged {
    param(
        [hashtable]$Before,
        [hashtable]$After
    )

    foreach ($Port in @("8000", "8787")) {
        $BeforeIds = @($Before[$Port] | Sort-Object)
        $AfterIds = @($After[$Port] | Sort-Object)
        if (Compare-Object -ReferenceObject $BeforeIds -DifferenceObject $AfterIds) {
            throw "search_packaged_smoke_port_owner_changed:$Port"
        }
    }
}

function Invoke-IsolatedRuntimeCommand {
    param([Parameter(Mandatory = $true)][string]$Command)

    $Output = @(& $PythonExe -B $LauncherScript $Command)
    if ($LASTEXITCODE -ne 0) {
        throw "search_packaged_smoke_runtime_command_failed:$Command"
    }
    $LastLine = @($Output | Where-Object { $_ -and $_.Trim() }) | Select-Object -Last 1
    if (-not $LastLine) {
        throw "search_packaged_smoke_runtime_response_empty:$Command"
    }
    try {
        $LastLine | ConvertFrom-Json
    }
    catch {
        throw "search_packaged_smoke_runtime_response_invalid:$Command"
    }
}

function Test-LocalRuntimeReady {
    param([Parameter(Mandatory = $true)]$Status)

    if (-not $Status.PSObject.Properties["components"]) { return $false }
    if (-not $Status.components.PSObject.Properties["fastapi"]) { return $false }
    if (-not $Status.components.PSObject.Properties["mcp"]) { return $false }

    $AcceptableState = $Status.state -in @("ready", "local_ready_tunnel_missing")
    $FastApiState = [string]$Status.components.fastapi.state
    $McpState = [string]$Status.components.mcp.state
    $AcceptableComponentStates = @("ready", "external")
    $AcceptableState -and
        $FastApiState -in $AcceptableComponentStates -and
        $McpState -in $AcceptableComponentStates
}

function Get-HealthyExternalRuntimeFixture {
    $Endpoints = @{
        fastapi = "http://127.0.0.1:8000/api/v1/retrieval/index/status"
        mcp = "http://127.0.0.1:8787/healthz"
    }
    foreach ($Endpoint in $Endpoints.GetEnumerator()) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Endpoint.Value -TimeoutSec 5
            if ([int]$Response.StatusCode -lt 200 -or [int]$Response.StatusCode -ge 300) {
                return $null
            }
        }
        catch {
            return $null
        }
    }

    [pscustomobject]@{
        state = "ready"
        components = [pscustomobject]@{
            fastapi = [pscustomobject]@{ state = "external" }
            mcp = [pscustomobject]@{ state = "external" }
        }
    }
}

function Start-IsolatedRuntimeFixture {
    $ExternalRuntime = Get-HealthyExternalRuntimeFixture
    if ($ExternalRuntime) { return $ExternalRuntime }

    $Status = Invoke-IsolatedRuntimeCommand -Command "start"
    $Deadline = (Get-Date).AddSeconds($RuntimeReadyTimeoutSeconds)
    while (-not (Test-LocalRuntimeReady -Status $Status)) {
        if ((Get-Date) -ge $Deadline) {
            throw "search_packaged_smoke_runtime_fixture_timeout"
        }
        Start-Sleep -Milliseconds 500
        $Status = Invoke-IsolatedRuntimeCommand -Command "status"
    }
    $Status
}

$ExistingSearch = @(Get-ExactSearchProcesses -ExpectedExecutable $ExecutablePath)
if ($ExistingSearch.Count -gt 0) {
    throw "search_packaged_smoke_exact_executable_already_running"
}

$RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$RunRoot = Join-Path $TestRoot $RunId
$UserData = Join-Path $RunRoot "electron-user-data"
$LocalAppData = Join-Path $RunRoot "local-app-data"
$RoamingAppData = Join-Path $RunRoot "roaming-app-data"
$TempDirectory = Join-Path $RunRoot "temp"
$StdoutPath = Join-Path $RunRoot "search.stdout.log"
$StderrPath = Join-Path $RunRoot "search.stderr.log"
$StartupLog = Join-Path $UserData "logs\search-startup.log"

foreach ($Directory in @($UserData, $LocalAppData, $RoamingAppData, $TempDirectory)) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}

$OriginalEnvironment = @{
    LOCALAPPDATA = $env:LOCALAPPDATA
    APPDATA = $env:APPDATA
    TEMP = $env:TEMP
    TMP = $env:TMP
    ELECTRON_DISABLE_CRASH_REPORTING = $env:ELECTRON_DISABLE_CRASH_REPORTING
}
$PortOwnersBefore = Get-PortOwners -Ports @(8000, 8787)
$SearchProcess = $null
$SmokeProcessIds = @()
$SmokeResult = $null
$PrimaryError = $null

try {
    $env:LOCALAPPDATA = $LocalAppData
    $env:APPDATA = $RoamingAppData
    $env:TEMP = $TempDirectory
    $env:TMP = $TempDirectory
    $env:ELECTRON_DISABLE_CRASH_REPORTING = "1"

    # The packaged-executable assertion is timed against an already healthy
    # local runtime, matching the normal persistent Desktop runtime.  This
    # fixture is isolated on D: and is always stopped in the finally block.
    $RuntimeFixture = Start-IsolatedRuntimeFixture

    $Arguments = @(
        "--user-data-dir=$UserData",
        "--no-first-run",
        "--disable-breakpad",
        "--disable-crash-reporter"
    )
    $StartedAt = Get-Date
    $SearchProcess = Start-Process `
        -FilePath $ExecutablePath `
        -ArgumentList $Arguments `
        -WorkingDirectory (Split-Path -Parent $ExecutablePath) `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru

    Start-Sleep -Seconds $HoldSeconds
    $SearchProcess.Refresh()
    if ($SearchProcess.HasExited) {
        throw "search_packaged_smoke_process_exited:$($SearchProcess.ExitCode)"
    }

    $Observed = Get-CimInstance Win32_Process -Filter "ProcessId = $($SearchProcess.Id)" -ErrorAction SilentlyContinue
    if (-not $Observed -or -not $Observed.ExecutablePath) {
        throw "search_packaged_smoke_process_identity_unavailable"
    }
    if (-not ([System.IO.Path]::GetFullPath([string]$Observed.ExecutablePath)).Equals(
        $ExecutablePath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "search_packaged_smoke_process_identity_mismatch"
    }

    $SearchProcess.Refresh()
    $WindowHandle = [int64]$SearchProcess.MainWindowHandle
    $WindowTitle = [string]$SearchProcess.MainWindowTitle
    if ($WindowHandle -eq 0) {
        throw "search_packaged_smoke_window_missing"
    }
    if ($WindowTitle -notmatch "Search") {
        throw "search_packaged_smoke_window_title_invalid"
    }
    if (-not (Test-Path -LiteralPath $StartupLog -PathType Leaf)) {
        throw "search_packaged_smoke_startup_log_missing"
    }

    $Entries = @(Get-Content -LiteralPath $StartupLog -Encoding UTF8 |
        Where-Object { $_.Trim() } |
        ForEach-Object { $_ | ConvertFrom-Json })
    $ReadyEntries = @($Entries | Where-Object {
        $_.event -eq "stage_completed" -and
        $_.stage -eq "ready" -and
        $_.lastSuccessfulStage -eq "ready"
    })
    if ($ReadyEntries.Count -eq 0) {
        throw "search_packaged_smoke_ready_stage_missing"
    }

    $RuntimeSeconds = [Math]::Round(((Get-Date) - $StartedAt).TotalSeconds, 3)
    $SmokeResult = [ordered]@{
        status = "ready"
        pid = $SearchProcess.Id
        runtime_seconds = $RuntimeSeconds
        main_window_handle = $WindowHandle
        main_window_title = $WindowTitle
        startup_log = $StartupLog
        last_startup_stage = "ready"
        runtime_fixture_state = [string]$RuntimeFixture.state
        run_root = $RunRoot
    }
}
catch {
    $PrimaryError = $_
}
finally {
    if ($SearchProcess) {
        try {
            $SmokeProcessIds = @(Stop-SmokeSearchProcessTree `
                -RootProcessId $SearchProcess.Id `
                -ExpectedExecutable $ExecutablePath)
        }
        catch {
            if (-not $PrimaryError) { $PrimaryError = $_ }
        }
    }

    # Runtime state is isolated under this run's LOCALAPPDATA.  Launcher stop
    # can only terminate identities recorded as owned in that isolated state;
    # pre-existing healthy services are represented as external and retained.
    try {
        [void](Invoke-IsolatedRuntimeCommand -Command "stop")
    }
    catch {
        if (-not $PrimaryError) { $PrimaryError = $_ }
    }

    $Deadline = (Get-Date).AddSeconds($CleanupTimeoutSeconds)
    do {
        $ResidualIds = @($SmokeProcessIds | Where-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        })
        if ($ResidualIds.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)

    if ($ResidualIds.Count -gt 0 -and -not $PrimaryError) {
        $PrimaryError = [System.Management.Automation.RuntimeException]::new(
            "search_packaged_smoke_residual_processes:$($ResidualIds -join ',')"
        )
    }

    try {
        Assert-PortOwnershipUnchanged `
            -Before $PortOwnersBefore `
            -After (Get-PortOwners -Ports @(8000, 8787))
    }
    catch {
        if (-not $PrimaryError) { $PrimaryError = $_ }
    }

    foreach ($Name in $OriginalEnvironment.Keys) {
        $Value = $OriginalEnvironment[$Name]
        if ($null -eq $Value) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item "Env:$Name" $Value
        }
    }
}

if ($PrimaryError) {
    throw $PrimaryError
}

$SmokeResult["stopped_process_ids"] = @($SmokeProcessIds)
$SmokeResult["residual_process_count"] = 0
$SmokeResult | ConvertTo-Json -Depth 5
