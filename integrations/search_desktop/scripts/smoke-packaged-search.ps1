[CmdletBinding()]
param(
    [string]$ExecutablePath,
    [string]$ProjectRoot,
    [string]$DataDir,
    [string]$PythonExe,
    [string]$NodeExe,
    [string]$MachineConfigPath,
    [string]$TestRoot,
    [ValidateSet("valid", "missing", "invalid", "legacy-migration")]
    [string]$Scenario = "valid",
    [ValidateRange(10, 120)]
    [int]$HoldSeconds = 10,
    [ValidateRange(30, 240)]
    [int]$RuntimeReadyTimeoutSeconds = 180,
    [ValidateRange(10, 180)]
    [int]$CleanupTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$DesktopRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $ExecutablePath) { $ExecutablePath = Join-Path $DesktopRoot "dist\win-unpacked\Search.exe" }
if (-not $ProjectRoot) { $ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $DesktopRoot "..\..")) }
if (-not $TestRoot) { $TestRoot = Join-Path $ProjectRoot ".codex_tmp\search-desktop-startup-0.1.4\packaged-smoke" }
if (-not $DataDir) { $DataDir = Join-Path $TestRoot "empty-data" }
if (-not $PythonExe) { throw "search_packaged_smoke_python_not_configured" }
if (-not $NodeExe) { throw "search_packaged_smoke_node_not_configured" }

$ExecutablePath = [System.IO.Path]::GetFullPath($ExecutablePath)
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
$PythonExe = [System.IO.Path]::GetFullPath($PythonExe)
$NodeExe = [System.IO.Path]::GetFullPath($NodeExe)
$TestRoot = [System.IO.Path]::GetFullPath($TestRoot)
$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $ExecutablePath) "resources\app\runtime-project"))
$ConfigureTool = Join-Path $ProjectRoot "scripts\configure_search_desktop_runtime.ps1"
$ProjectDrive = [System.IO.Path]::GetPathRoot($ProjectRoot)
$TestDrive = [System.IO.Path]::GetPathRoot($TestRoot)
$TestPrefix = $TestRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
$SameDrive = (
    $ProjectDrive -and
    $TestDrive -and
    $ProjectDrive.Equals($TestDrive, [System.StringComparison]::OrdinalIgnoreCase)
)
$DataIsIsolated = (
    $DataDir.Equals($TestRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $DataDir.StartsWith($TestPrefix, [System.StringComparison]::OrdinalIgnoreCase)
)
if (-not $SameDrive -or -not $DataIsIsolated) {
    throw "search_packaged_smoke_writable_root_not_isolated"
}

foreach ($RequiredFile in @($ExecutablePath, $PythonExe, $NodeExe, $ConfigureTool)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "search_packaged_smoke_required_file_missing:$([System.IO.Path]::GetFileName($RequiredFile))"
    }
}
if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
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

function Get-PackagedRuntimeProcesses {
    param([Parameter(Mandatory = $true)][string]$ExpectedRuntimeRoot)

    $Escaped = [Regex]::Escape([System.IO.Path]::GetFullPath($ExpectedRuntimeRoot))
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^(python|pythonw|node)\.exe$' -and
        $_.CommandLine -and
        $_.CommandLine -match $Escaped
    })
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

function Get-FreeLoopbackPort {
    $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $Listener.Start()
        [int]$Listener.LocalEndpoint.Port
    }
    finally { $Listener.Stop() }
}

function Assert-PortOwnershipUnchanged {
    param([hashtable]$Before, [hashtable]$After)

    foreach ($Port in @($Before.Keys | Sort-Object)) {
        if (Compare-Object -ReferenceObject @($Before[$Port] | Sort-Object) -DifferenceObject @($After[$Port] | Sort-Object)) {
            throw "search_packaged_smoke_port_owner_changed:$Port"
        }
    }
}

function Invoke-SearchTestModeFullyQuit {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedExecutable,
        [Parameter(Mandatory = $true)][string]$UserData,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $QuitArguments = @(
        "--user-data-dir=$UserData",
        "--no-first-run",
        "--disable-breakpad",
        "--disable-crash-reporter",
        "--search-test-mode",
        "--search-test-quit"
    )
    $QuitRequest = Start-Process -FilePath $ExpectedExecutable -ArgumentList $QuitArguments `
        -WorkingDirectory (Split-Path -Parent $ExpectedExecutable) -WindowStyle Hidden -PassThru
    if (-not $QuitRequest.WaitForExit(10000)) {
        throw "search_packaged_smoke_quit_request_did_not_exit"
    }
    if ($QuitRequest.ExitCode -ne 0) {
        throw "search_packaged_smoke_quit_request_failed:$($QuitRequest.ExitCode)"
    }

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $Remaining = @(Get-ExactSearchProcesses -ExpectedExecutable $ExpectedExecutable)
    } while ($Remaining.Count -gt 0 -and (Get-Date) -lt $Deadline)
    if ($Remaining.Count -gt 0) { throw "search_packaged_smoke_test_mode_quit_timeout" }
}

if (@(Get-ExactSearchProcesses -ExpectedExecutable $ExecutablePath).Count -gt 0) {
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
$DeletionArchiveRoot = Join-Path (
    Split-Path -Parent (
        Split-Path -Parent (
            Split-Path -Parent $DataDir
        )
    )
) "Archives\SearchBookDeletion"
$StartupLog = Join-Path $UserData "logs\search-startup.log"
$DesktopRuntimePath = Join-Path $UserData "desktop-runtime.json"
foreach ($Directory in @($UserData, $LocalAppData, $RoamingAppData, $TempDirectory)) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}

$ToolResult = $null
$ExpectedRuntimeResult = "ready"
$ExpectedConfigSource = "user_data"
$ExpectedRuntimeStatus = "desktop_runtime_ready"
$ExpectedErrorCode = $null
$ExpectedLauncherSpawned = $true
$LegacyInputPath = $null
$LegacyInputHash = $null

switch ($Scenario) {
    "valid" {
        $ToolOutput = @(& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -NoLogo -NoProfile -ExecutionPolicy Bypass -File $ConfigureTool `
            -Action set -ConfigPath $DesktopRuntimePath -DataDir $DataDir `
            -PythonExe $PythonExe -NodeExe $NodeExe -NodeRuntime $NodeExe)
        if ($LASTEXITCODE -ne 0) { throw "search_packaged_smoke_desktop_runtime_config_failed" }
        $ToolResult = $ToolOutput | Select-Object -Last 1 | ConvertFrom-Json
        if ($ToolResult.status -ne "written" -or -not $ToolResult.desktop_runtime.ready) {
            throw "search_packaged_smoke_desktop_runtime_config_not_ready"
        }
    }
    "missing" {
        $ExpectedRuntimeResult = "failed"
        $ExpectedConfigSource = "none"
        $ExpectedRuntimeStatus = "desktop_runtime_config_missing"
        $ExpectedErrorCode = "desktop_runtime_config_missing"
        $ExpectedLauncherSpawned = $false
    }
    "invalid" {
        [System.IO.File]::WriteAllText($DesktopRuntimePath, "{", [System.Text.UTF8Encoding]::new($false))
        $ExpectedRuntimeResult = "failed"
        $ExpectedRuntimeStatus = "desktop_runtime_config_invalid_json"
        $ExpectedErrorCode = "desktop_runtime_config_invalid_json"
        $ExpectedLauncherSpawned = $false
    }
    "legacy-migration" {
        $LegacyInputDirectory = Join-Path $RunRoot "legacy-input"
        New-Item -ItemType Directory -Path $LegacyInputDirectory -Force | Out-Null
        $LegacyInputPath = Join-Path $LegacyInputDirectory "search-desktop.local.json"
        $LegacyPayload = [ordered]@{
            schemaVersion = 3
            dataDir = $DataDir
            pythonExe = $PythonExe
            nodeExe = $NodeExe
        } | ConvertTo-Json -Depth 3
        [System.IO.File]::WriteAllText($LegacyInputPath, $LegacyPayload + "`n", [System.Text.UTF8Encoding]::new($false))
        $LegacyInputHash = (Get-FileHash -LiteralPath $LegacyInputPath -Algorithm SHA256).Hash
        $ToolOutput = @(& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -NoLogo -NoProfile -ExecutionPolicy Bypass -File $ConfigureTool `
            -Action migrate-legacy -ConfigPath $DesktopRuntimePath `
            -LegacyConfigPath $LegacyInputPath -NodeRuntime $NodeExe)
        if ($LASTEXITCODE -ne 0) { throw "search_packaged_smoke_legacy_migration_failed" }
        $ToolResult = $ToolOutput | Select-Object -Last 1 | ConvertFrom-Json
        if ($ToolResult.status -ne "migrated" -or -not $ToolResult.desktop_runtime.ready) {
            throw "search_packaged_smoke_legacy_migration_not_ready"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $UserData "desktop-runtime.legacy-sidecar.bak.json") -PathType Leaf)) {
            throw "search_packaged_smoke_legacy_backup_missing"
        }
    }
}
if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $ExecutablePath) "desktop-runtime.json")) {
    throw "search_packaged_smoke_candidate_contains_desktop_runtime_config"
}
if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $ExecutablePath) "search-desktop.local.json")) {
    throw "search_packaged_smoke_candidate_contains_legacy_sidecar"
}
if ($MachineConfigPath) {
    if (-not (Test-Path -LiteralPath $MachineConfigPath -PathType Leaf)) {
        throw "search_packaged_smoke_machine_config_missing"
    }
    Copy-Item -LiteralPath $MachineConfigPath -Destination (Join-Path $UserData "machine-config.json")
}

$EnvironmentNames = @(
    "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "ELECTRON_DISABLE_CRASH_REPORTING",
    "SEARCH_ELECTRON_TEST_MODE", "SEARCH_RENDERER_PORT", "SEARCH_RUNTIME_ROOT",
    "SEARCH_DATA_DIR", "SEARCH_LOG_DIR", "SEARCH_PYTHON", "SEARCH_NODE", "SEARCH_MACHINE_CONFIG_PATH",
    "NOTEBOOK_AI_RUNTIME_ROOT", "NOTEBOOK_AI_DATA_PROJECT_ROOT", "NOTEBOOK_AI_PROJECT_ROOT",
    "NOTEBOOK_AI_PYTHON_EXE", "NOTEBOOK_AI_NODE_EXE", "PYTHONPATH", "NODE_PATH"
)
$OriginalEnvironment = @{}
foreach ($Name in $EnvironmentNames) { $OriginalEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process") }

$RendererPort = 5173
$PortOwnersBefore = Get-PortOwners -Ports @(8000, 8787, $RendererPort)
$CloudflaredBefore = @(Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id | Sort-Object)
$SearchProcess = $null
$PrimaryError = $null
$SmokeResult = $null
$DeletionSmokeVerified = $false

try {
    $env:LOCALAPPDATA = $LocalAppData
    $env:APPDATA = $RoamingAppData
    $env:TEMP = $TempDirectory
    $env:TMP = $TempDirectory
    $env:ELECTRON_DISABLE_CRASH_REPORTING = "1"
    foreach ($Name in @(
        "SEARCH_ELECTRON_TEST_MODE", "SEARCH_RENDERER_PORT", "SEARCH_RUNTIME_ROOT", "SEARCH_DATA_DIR", "SEARCH_LOG_DIR", "SEARCH_PYTHON", "SEARCH_NODE", "SEARCH_MACHINE_CONFIG_PATH",
        "NOTEBOOK_AI_RUNTIME_ROOT", "NOTEBOOK_AI_DATA_PROJECT_ROOT", "NOTEBOOK_AI_PROJECT_ROOT",
        "NOTEBOOK_AI_PYTHON_EXE", "NOTEBOOK_AI_NODE_EXE", "PYTHONPATH", "NODE_PATH"
    )) { Set-Item "Env:$Name" $null }

    $Arguments = @(
        "--user-data-dir=$UserData",
        "--no-first-run",
        "--disable-breakpad",
        "--disable-crash-reporter",
        "--search-test-mode"
    )
    $StartedAt = Get-Date
    $SearchProcess = Start-Process -FilePath $ExecutablePath -ArgumentList $Arguments `
        -WorkingDirectory (Split-Path -Parent $ExecutablePath) `
        -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath `
        -WindowStyle Hidden -PassThru

    $Deadline = (Get-Date).AddSeconds($RuntimeReadyTimeoutSeconds)
    $RuntimeEntry = $null
    $ApplicationReadyEntry = $null
    do {
        Start-Sleep -Milliseconds 500
        $SearchProcess.Refresh()
        if ($SearchProcess.HasExited) { throw "search_packaged_smoke_process_exited:$($SearchProcess.ExitCode)" }
        if (Test-Path -LiteralPath $StartupLog -PathType Leaf) {
            $Entries = @(Get-Content -LiteralPath $StartupLog -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
            $RuntimeEntry = $Entries | Where-Object {
                $_.stage -eq "runtime_checked" -and $_.result -eq $ExpectedRuntimeResult -and
                $_.config_source -eq $ExpectedConfigSource -and
                $_.desktop_runtime_status -eq $ExpectedRuntimeStatus -and
                $_.launcher_spawned -eq $ExpectedLauncherSpawned
            } | Select-Object -Last 1
            $ApplicationReadyEntry = $Entries | Where-Object {
                $_.event -eq "stage_completed" -and $_.stage -eq "ready"
            } | Select-Object -Last 1
        }
    } while ((-not $RuntimeEntry -or -not $ApplicationReadyEntry) -and (Get-Date) -lt $Deadline)
    if (-not $RuntimeEntry) { throw "search_packaged_smoke_runtime_stage_missing:$Scenario" }
    if (-not $ApplicationReadyEntry) { throw "search_packaged_smoke_application_ready_stage_missing:$Scenario" }
    if ($ExpectedErrorCode -and $RuntimeEntry.error_code -ne $ExpectedErrorCode) {
        throw "search_packaged_smoke_runtime_error_code_mismatch:$Scenario"
    }
    if ($ExpectedRuntimeResult -eq "ready") {
        if ($RuntimeEntry.event -ne "stage_completed" -or $RuntimeEntry.runtime_owner -ne "managed-by-search" -or $RuntimeEntry.desktop_started_runtime -ne $true) {
            throw "search_packaged_smoke_runtime_ownership_invalid:$Scenario"
        }
    }
    elseif ($RuntimeEntry.event -ne "stage_failed" -or $RuntimeEntry.desktop_started_runtime -ne $false) {
        throw "search_packaged_smoke_runtime_failure_not_structured:$Scenario"
    }

    $RendererResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$RendererPort/retrieval" -TimeoutSec 10
    if ([int]$RendererResponse.StatusCode -ne 200) { throw "search_packaged_smoke_renderer_not_ready:$Scenario" }

    if ($ExpectedRuntimeResult -eq "ready") {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
        $McpHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8787/healthz" -TimeoutSec 10
        if ($Health.status -ne "ok" -or $McpHealth.status -ne "ok") { throw "search_packaged_smoke_health_not_ready" }
        if ($Scenario -eq "valid") {
            $OpenApi = Invoke-RestMethod -Uri "http://127.0.0.1:8000/openapi.json" -TimeoutSec 10
            foreach ($Route in @(
                "/api/v1/library/read-shelf",
                "/api/v1/library/documents/{document_id}/deletion-preview",
                "/api/v1/library/documents/{document_id}/delete",
                "/api/v1/library/management/archive",
                "/api/v1/library/management/restore"
            )) {
                if (-not $OpenApi.paths.PSObject.Properties[$Route]) {
                    throw "search_packaged_smoke_library_route_missing"
                }
            }
            if (-not $OpenApi.paths.PSObject.Properties["/api/v1/library/documents/{document_id}/deletion-preview"].Value.get) {
                throw "search_packaged_smoke_deletion_preview_not_get"
            }
            if (-not $OpenApi.paths.PSObject.Properties["/api/v1/library/documents/{document_id}/delete"].Value.post) {
                throw "search_packaged_smoke_delete_not_post"
            }

            $WorkspaceResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$RendererPort/workspace" -TimeoutSec 10
            if ([int]$WorkspaceResponse.StatusCode -ne 200) {
                throw "search_packaged_smoke_workspace_deep_link_unavailable"
            }
            $FixtureScript = Join-Path $RuntimeRoot "scripts\maintenance\smoke_search_book_deletion_isolated.py"
            if (-not (Test-Path -LiteralPath $FixtureScript -PathType Leaf)) {
                throw "search_packaged_smoke_deletion_fixture_script_missing"
            }
            $SeedOutput = @(
                & $PythonExe $FixtureScript --action seed --data-dir $DataDir `
                    --archive-root $DeletionArchiveRoot --allow-isolated-smoke
            )
            if ($LASTEXITCODE -ne 0) { throw "search_packaged_smoke_deletion_fixture_seed_failed" }
            $Seed = $SeedOutput | Select-Object -Last 1 | ConvertFrom-Json
            if ($Seed.status -ne "seeded") { throw "search_packaged_smoke_deletion_fixture_seed_invalid" }
            $FixtureDocumentId = [int]$Seed.document_id

            $RendererHeaders = @{ Origin = "http://127.0.0.1:5173" }
            $MutationSession = Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:8000/api/v1/library/management/mutation-session" `
                -Headers $RendererHeaders -ContentType "application/json" -Body "{}" -TimeoutSec 10
            if ($MutationSession.status -ne "ok" -or -not $MutationSession.mutation_token) {
                throw "search_packaged_smoke_mutation_session_failed"
            }
            $MutationHeaders = @{
                Origin = "http://127.0.0.1:5173"
                "X-Search-Mutation-Token" = [string]$MutationSession.mutation_token
            }
            $ActiveShelf = Invoke-RestMethod `
                -Uri "http://127.0.0.1:8000/api/v1/library/read-shelf?view=active" -TimeoutSec 10
            if ($ActiveShelf.status -ne "ok" -or @($ActiveShelf.items).Count -ne 1) {
                throw "search_packaged_smoke_read_shelf_contract_failed"
            }

            $ArchiveBody = @{ document_ids = @($FixtureDocumentId) } | ConvertTo-Json -Compress
            $ArchiveResult = Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:8000/api/v1/library/management/archive" `
                -Headers $MutationHeaders -ContentType "application/json" -Body $ArchiveBody -TimeoutSec 10
            if ($ArchiveResult.status -ne "ok" -or $ArchiveResult.action -ne "archived") {
                throw "search_packaged_smoke_archive_failed"
            }
            $ArchivedShelf = Invoke-RestMethod `
                -Uri "http://127.0.0.1:8000/api/v1/library/read-shelf?view=archived" -TimeoutSec 10
            if ($ArchivedShelf.status -ne "ok" -or @($ArchivedShelf.items).Count -ne 1) {
                throw "search_packaged_smoke_archived_shelf_contract_failed"
            }
            $RestoreResult = Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:8000/api/v1/library/management/restore" `
                -Headers $MutationHeaders -ContentType "application/json" -Body $ArchiveBody -TimeoutSec 10
            if ($RestoreResult.status -ne "ok" -or $RestoreResult.action -ne "restored") {
                throw "search_packaged_smoke_archive_restore_failed"
            }

            $Preview = Invoke-RestMethod `
                -Uri "http://127.0.0.1:8000/api/v1/library/documents/$FixtureDocumentId/deletion-preview" `
                -Headers $RendererHeaders -TimeoutSec 20
            if (
                $Preview.status -ne "ok" -or
                $Preview.read_only -ne $true -or
                $Preview.whether_safe_to_delete -ne $true -or
                -not $Preview.preview_token -or
                -not $Preview.document_revision
            ) {
                throw "search_packaged_smoke_deletion_preview_invalid"
            }
            $DeleteBody = [ordered]@{
                document_id = $FixtureDocumentId
                preview_token = [string]$Preview.preview_token
                expected_document_revision = [string]$Preview.document_revision
                confirmation_text = [string]$Seed.title
                deletion_options = $Preview.deletion_options
            } | ConvertTo-Json -Depth 8 -Compress
            $DeleteResult = Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:8000/api/v1/library/documents/$FixtureDocumentId/delete" `
                -Headers $MutationHeaders -ContentType "application/json" -Body $DeleteBody -TimeoutSec 30
            if ($DeleteResult.status -ne "completed" -or -not $DeleteResult.audit_id) {
                throw "search_packaged_smoke_isolated_delete_failed"
            }
            $VerifyOutput = @(
                & $PythonExe $FixtureScript --action verify --data-dir $DataDir `
                    --archive-root $DeletionArchiveRoot --allow-isolated-smoke
            )
            if ($LASTEXITCODE -ne 0) { throw "search_packaged_smoke_deletion_fixture_verify_failed" }
            $FixtureVerification = $VerifyOutput | Select-Object -Last 1 | ConvertFrom-Json
            if (
                $FixtureVerification.status -ne "verified" -or
                [int]$FixtureVerification.foreign_key_issue_count -ne 0
            ) {
                throw "search_packaged_smoke_deletion_fixture_verify_invalid"
            }
            $DeletionSmokeVerified = $true
        }
    }
    else {
        if (@(Get-PackagedRuntimeProcesses -ExpectedRuntimeRoot $RuntimeRoot).Count -ne 0) {
            throw "search_packaged_smoke_unavailable_spawned_runtime:$Scenario"
        }
        if (Test-Path -LiteralPath (Join-Path $LocalAppData "Search\data")) {
            throw "search_packaged_smoke_created_fallback_data:$Scenario"
        }
    }

    Start-Sleep -Seconds $HoldSeconds
    $Visible = @(Get-ExactSearchProcesses -ExpectedExecutable $ExecutablePath | ForEach-Object {
        Get-Process -Id ([int]$_.ProcessId) -ErrorAction SilentlyContinue
    } | Where-Object { $_ -and [int64]$_.MainWindowHandle -ne 0 })
    if ($Visible.Count -gt 0) { throw "search_packaged_smoke_window_visible" }
    $VisibleRuntime = @(Get-PackagedRuntimeProcesses -ExpectedRuntimeRoot $RuntimeRoot | ForEach-Object {
        Get-Process -Id ([int]$_.ProcessId) -ErrorAction SilentlyContinue
    } | Where-Object { $_ -and [int64]$_.MainWindowHandle -ne 0 })
    if ($VisibleRuntime.Count -gt 0) { throw "search_packaged_smoke_runtime_console_visible" }
    $CloudflaredDuring = @(Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id | Sort-Object)
    if (Compare-Object -ReferenceObject $CloudflaredBefore -DifferenceObject $CloudflaredDuring) {
        throw "search_packaged_smoke_cloudflared_process_changed"
    }

    $FirstInstanceIds = @(Get-ExactSearchProcesses -ExpectedExecutable $ExecutablePath | Select-Object -ExpandProperty ProcessId | Sort-Object)
    $SecondProcess = Start-Process -FilePath $ExecutablePath -ArgumentList $Arguments `
        -WorkingDirectory (Split-Path -Parent $ExecutablePath) -WindowStyle Hidden -PassThru
    if (-not $SecondProcess.WaitForExit(10000)) { throw "search_packaged_smoke_second_instance_did_not_exit" }
    Start-Sleep -Milliseconds 500
    $SecondInstanceIds = @(Get-ExactSearchProcesses -ExpectedExecutable $ExecutablePath | Select-Object -ExpandProperty ProcessId | Sort-Object)
    if (Compare-Object -ReferenceObject $FirstInstanceIds -DifferenceObject $SecondInstanceIds) {
        throw "search_packaged_smoke_second_instance_changed_process_tree"
    }

    Invoke-SearchTestModeFullyQuit -ExpectedExecutable $ExecutablePath -UserData $UserData -TimeoutSeconds $CleanupTimeoutSeconds
    $SearchProcess = $null
    $CleanupDeadline = (Get-Date).AddSeconds($CleanupTimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $RuntimeResidual = @(Get-PackagedRuntimeProcesses -ExpectedRuntimeRoot $RuntimeRoot)
        $PortResidual = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8000, 8787 })
    } while (($RuntimeResidual.Count -gt 0 -or $PortResidual.Count -gt 0) -and (Get-Date) -lt $CleanupDeadline)
    if ($RuntimeResidual.Count -gt 0 -or $PortResidual.Count -gt 0) { throw "search_packaged_smoke_owned_runtime_residual" }

    $LogText = Get-Content -Raw -LiteralPath $StartupLog -Encoding UTF8
    foreach ($Sensitive in @($DataDir, $PythonExe, $NodeExe, $UserData)) {
        if ($LogText.IndexOf($Sensitive, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw "search_packaged_smoke_startup_log_path_leak"
        }
    }
    if ($LegacyInputPath) {
        if ((Get-FileHash -LiteralPath $LegacyInputPath -Algorithm SHA256).Hash -ne $LegacyInputHash) {
            throw "search_packaged_smoke_legacy_input_changed"
        }
    }
    $SmokeResult = [ordered]@{
        status = if ($ExpectedRuntimeResult -eq "ready") { "ready" } else { "structured_unavailable" }
        scenario = $Scenario
        error_code = $ExpectedErrorCode
        runtime_owner = if ($ExpectedRuntimeResult -eq "ready") { "managed-by-search" } else { "unavailable" }
        config_source = $ExpectedConfigSource
        desktop_runtime_status = $ExpectedRuntimeStatus
        launcher_spawned = $ExpectedLauncherSpawned
        desktop_started_runtime = ($ExpectedRuntimeResult -eq "ready")
        duplicate_instance_reused = $true
        graceful_tray_exit = $true
        renderer_ready = $true
        read_shelf_ready = ($Scenario -eq "valid" -and $DeletionSmokeVerified)
        workspace_deep_link_ready = ($Scenario -eq "valid" -and $DeletionSmokeVerified)
        archive_restore_verified = ($Scenario -eq "valid" -and $DeletionSmokeVerified)
        deletion_preview_verified = ($Scenario -eq "valid" -and $DeletionSmokeVerified)
        isolated_deletion_verified = ($Scenario -eq "valid" -and $DeletionSmokeVerified)
        recovery_package_verified = ($Scenario -eq "valid" -and $DeletionSmokeVerified)
        visible_console_count = 0
        cloudflared_started = $false
        runtime_residual_count = 0
        runtime_seconds = [Math]::Round(((Get-Date) - $StartedAt).TotalSeconds, 3)
        renderer_port = $RendererPort
        config_file = if ($ToolResult) { $ToolResult.config_file } else { $null }
        legacy_backup = ($Scenario -eq "legacy-migration")
        run_root = $RunRoot
    }
}
catch { $PrimaryError = $_ }
finally {
    if ($SearchProcess -and -not $SearchProcess.HasExited) {
        try { Invoke-SearchTestModeFullyQuit -ExpectedExecutable $ExecutablePath -UserData $UserData -TimeoutSeconds $CleanupTimeoutSeconds }
        catch { if (-not $PrimaryError) { $PrimaryError = $_ } }
    }
    try { Assert-PortOwnershipUnchanged -Before $PortOwnersBefore -After (Get-PortOwners -Ports @(8000, 8787, $RendererPort)) }
    catch { if (-not $PrimaryError) { $PrimaryError = $_ } }
    foreach ($Name in $EnvironmentNames) {
        [Environment]::SetEnvironmentVariable($Name, $OriginalEnvironment[$Name], "Process")
    }
}

if ($PrimaryError) { throw $PrimaryError }
$SmokeResult | ConvertTo-Json -Depth 5
