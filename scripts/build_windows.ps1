[CmdletBinding()]
param(
    [string]$PythonExe = $env:SEARCH_PYTHON,
    [string]$NodeExe = $env:SEARCH_NODE,
    [string]$BuildId,
    [string]$OutputRoot,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DesktopRoot = Join-Path $ProjectRoot "integrations\search_desktop"
if (-not $BuildId) { throw "search_build_id_required" }
if ($BuildId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "search_build_id_invalid"
}
if (-not $OutputRoot) { throw "search_output_root_required" }
if (
    -not [System.IO.Path]::IsPathRooted($OutputRoot) `
    -or $OutputRoot -notmatch '^[A-Za-z]:[\\/]'
) {
    throw "search_output_root_must_be_absolute"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot).TrimEnd('\', '/')
$ProjectDrive = [System.IO.Path]::GetPathRoot($ProjectRoot)
$OutputDrive = [System.IO.Path]::GetPathRoot($OutputRoot)
if (
    -not $OutputDrive `
    -or -not $ProjectDrive `
    -or -not $OutputDrive.Equals($ProjectDrive, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "search_output_root_drive_not_allowed"
}
$NormalizedOutput = $OutputRoot.Replace('/', '\').ToLowerInvariant()
if (
    $NormalizedOutput -match '\\(?:notebook_ai|notebook_ai_worktrees|notebook_ai_clean_clones)(?:\\|$)' `
    -or $NormalizedOutput.StartsWith((Join-Path $ProjectRoot "data").ToLowerInvariant() + '\') `
    -or $NormalizedOutput.Equals((Join-Path $ProjectRoot "data").ToLowerInvariant()) `
    -or $NormalizedOutput.StartsWith((Join-Path $ProjectRoot ".git").ToLowerInvariant() + '\') `
    -or $NormalizedOutput.StartsWith((Join-Path $DesktopRoot "dist").ToLowerInvariant() + '\') `
    -or $NormalizedOutput.Equals((Join-Path $DesktopRoot "dist").ToLowerInvariant())
) {
    throw "search_output_root_not_allowed"
}
if (Test-Path -LiteralPath $OutputRoot) {
    throw "search_candidate_output_already_exists:$OutputRoot"
}

function Resolve-Executable {
    param([string]$Configured, [string[]]$Names, [string]$ErrorCode)
    if ($Configured) {
        if (Test-Path -LiteralPath $Configured -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($Configured)
        }
        $Command = Get-Command $Configured -CommandType Application -ErrorAction SilentlyContinue
        if ($Command) { return [System.IO.Path]::GetFullPath($Command.Source) }
        throw $ErrorCode
    }
    foreach ($Name in $Names) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
        if ($Command) { return [System.IO.Path]::GetFullPath($Command.Source) }
    }
    throw $ErrorCode
}

if (-not $PythonExe) { throw "search_python_not_configured_set_SEARCH_PYTHON" }
$PythonExe = Resolve-Executable $PythonExe @() "search_python_executable_unavailable"
$NodeExe = Resolve-Executable $NodeExe @("node.exe", "node") "search_node_executable_unavailable"
$GitExe = Resolve-Executable "" @("git.exe", "git") "search_git_executable_unavailable"

$GitStatus = @(& $GitExe -C $ProjectRoot status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0) { throw "search_git_status_unavailable" }
if ($GitStatus.Count -ne 0) { throw "search_build_requires_clean_worktree" }
$SourceCommit = (& $GitExe -C $ProjectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $SourceCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "search_source_commit_unavailable"
}
$VerifiedHead = (& $GitExe -C $ProjectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $VerifiedHead -ne $SourceCommit) {
    throw "search_source_commit_changed"
}
$SourceBranch = [string](& $GitExe -C $ProjectRoot symbolic-ref --short -q HEAD)
$SourceBranch = $SourceBranch.Trim()
if ($LASTEXITCODE -ne 0 -or -not $SourceBranch) { $SourceBranch = "(detached)" }

$PackagePath = Join-Path $DesktopRoot "package.json"
$Package = Get-Content -Raw -LiteralPath $PackagePath | ConvertFrom-Json
if ([string]$Package.productName -ne "Search" -or [string]$Package.build.productName -ne "Search") {
    throw "search_package_product_invalid"
}
$Version = [string]$Package.version
if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "search_package_version_invalid"
}
$BuildTimestampUtc = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'")
$BuildIdentity = [ordered]@{
    schema_version = "search.build-identity.v1"
    build_mode = "packaged"
    product = "Search"
    version = $Version
    build_id = $BuildId
    source_commit = $SourceCommit.ToLowerInvariant()
    source_branch = $SourceBranch
    build_timestamp_utc = $BuildTimestampUtc
}
$LockFiles = @(
    (Join-Path $ProjectRoot "frontend\package-lock.json"),
    (Join-Path $ProjectRoot "integrations\notebook_ai_chatgpt_app\package-lock.json"),
    (Join-Path $ProjectRoot "integrations\search_desktop\package-lock.json"),
    (Join-Path $ProjectRoot "packages\search-design-system\package-lock.json")
)
$LockHashesBefore = @{}
foreach ($LockFile in $LockFiles) {
    if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
        throw "search_package_lock_missing:$LockFile"
    }
    $LockHashesBefore[$LockFile] = (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash
}

$Vite = Join-Path $ProjectRoot "frontend\node_modules\vite\bin\vite.js"
$ElectronBuilder = Join-Path $DesktopRoot "node_modules\electron-builder\cli.js"
$Finalize = Join-Path $DesktopRoot "scripts\finalize-windows-exe.mjs"
$BuildWidget = Join-Path $ProjectRoot "integrations\notebook_ai_chatgpt_app\scripts\build-widget.mjs"
$BuildServer = Join-Path $ProjectRoot "integrations\notebook_ai_chatgpt_app\scripts\build-server.mjs"
$VerifyModule = "./integrations/search_desktop/scripts/verify-packaged-resources.mjs"
foreach ($Required in @($Vite, $ElectronBuilder, $Finalize, $BuildWidget, $BuildServer)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "search_build_prerequisite_missing:$Required"
    }
}

$PackagedRoot = Join-Path $OutputRoot "win-unpacked"

if ($CheckOnly) {
    [ordered]@{
        status = "ready"
        mode = "check_only"
        build_identity = $BuildIdentity
        output_root = $OutputRoot
        candidate = $PackagedRoot
        python = $PythonExe
        node = $NodeExe
        current_formal_package_untouched = $true
    } | ConvertTo-Json -Depth 3
    exit 0
}

function Get-TreeInfo {
    param([Parameter(Mandatory = $true)][string]$Root)
    $ResolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $Rows = [System.Collections.Generic.List[string]]::new()
    [int64]$TotalBytes = 0
    $Files = @(Get-ChildItem -LiteralPath $ResolvedRoot -Recurse -File | Sort-Object FullName)
    foreach ($File in $Files) {
        $Relative = $File.FullName.Substring($ResolvedRoot.Length).TrimStart('\', '/').Replace('\', '/')
        $Hash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        $Rows.Add("$Relative`t$Hash`t$($File.Length)")
        $TotalBytes += [int64]$File.Length
    }
    $Payload = if ($Rows.Count) { ($Rows -join "`n") + "`n" } else { "" }
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Payload)
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Digest = $Hasher.ComputeHash($Bytes)
    }
    finally {
        $Hasher.Dispose()
    }
    [pscustomobject]@{
        file_count = $Files.Count
        total_bytes = $TotalBytes
        sha256 = ([System.BitConverter]::ToString($Digest)).Replace("-", "")
    }
}

$SafeBuildName = $BuildId -replace '[^A-Za-z0-9._-]', '-'
$BuildRunRoot = Join-Path $ProjectRoot ".codex_tmp\build\$SafeBuildName"
$TempDir = Join-Path $BuildRunRoot "temp"
$ElectronCache = Join-Path $BuildRunRoot "electron-cache"
$BuilderCache = Join-Path $BuildRunRoot "electron-builder-cache"
$ImportData = Join-Path $BuildRunRoot "empty-data"
$RuntimeDir = Join-Path $BuildRunRoot "runtime"
$LogDir = Join-Path $BuildRunRoot "logs"
$ConfigDir = Join-Path $BuildRunRoot "config"
$LocalAppDataDir = Join-Path $BuildRunRoot "local-app-data"
$RoamingAppDataDir = Join-Path $BuildRunRoot "roaming-app-data"
foreach ($Directory in @(
    $TempDir,
    $ElectronCache,
    $BuilderCache,
    $RuntimeDir,
    $LogDir,
    $ConfigDir,
    $LocalAppDataDir,
    $RoamingAppDataDir
)) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}

$Original = @{
    TEMP = $env:TEMP
    TMP = $env:TMP
    LOCALAPPDATA = $env:LOCALAPPDATA
    APPDATA = $env:APPDATA
    ELECTRON_CACHE = $env:ELECTRON_CACHE
    ELECTRON_BUILDER_CACHE = $env:ELECTRON_BUILDER_CACHE
    CSC_IDENTITY_AUTO_DISCOVERY = $env:CSC_IDENTITY_AUTO_DISCOVERY
    SEARCH_DATA_DIR = $env:SEARCH_DATA_DIR
    SEARCH_RUNTIME_DIR = $env:SEARCH_RUNTIME_DIR
    SEARCH_LOG_DIR = $env:SEARCH_LOG_DIR
    SEARCH_CONFIG_DIR = $env:SEARCH_CONFIG_DIR
    SEARCH_PYTHON = $env:SEARCH_PYTHON
    SEARCH_NODE = $env:SEARCH_NODE
    PYTHONDONTWRITEBYTECODE = $env:PYTHONDONTWRITEBYTECODE
}

try {
    $env:TEMP = $TempDir
    $env:TMP = $TempDir
    $env:LOCALAPPDATA = $LocalAppDataDir
    $env:APPDATA = $RoamingAppDataDir
    $env:ELECTRON_CACHE = $ElectronCache
    $env:ELECTRON_BUILDER_CACHE = $BuilderCache
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    $env:SEARCH_DATA_DIR = $ImportData
    $env:SEARCH_RUNTIME_DIR = $RuntimeDir
    $env:SEARCH_LOG_DIR = $LogDir
    $env:SEARCH_CONFIG_DIR = $ConfigDir
    $env:SEARCH_PYTHON = $PythonExe
    $env:SEARCH_NODE = $NodeExe
    $env:PYTHONDONTWRITEBYTECODE = "1"

    & $NodeExe $BuildWidget
    if ($LASTEXITCODE -ne 0) { throw "search_mcp_widget_build_failed" }
    & $NodeExe $BuildServer
    if ($LASTEXITCODE -ne 0) { throw "search_mcp_server_build_failed" }

    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        & $NodeExe $Vite build
        if ($LASTEXITCODE -ne 0) { throw "search_frontend_build_failed" }
    }
    finally { Pop-Location }

    Push-Location $ProjectRoot
    try {
        & $NodeExe --input-type=module -e (
            "const m=await import('$VerifyModule');" +
            "console.log(JSON.stringify(await m.verifySourceResources()));"
        )
        if ($LASTEXITCODE -ne 0) { throw "search_source_resource_verification_failed" }
    }
    finally { Pop-Location }

    Push-Location $DesktopRoot
    try {
        & $NodeExe $ElectronBuilder --win --x64 --dir `
            "--config.directories.output=$OutputRoot" `
            "--config.extraMetadata.searchBuildIdentity.schema_version=$($BuildIdentity.schema_version)" `
            "--config.extraMetadata.searchBuildIdentity.build_mode=$($BuildIdentity.build_mode)" `
            "--config.extraMetadata.searchBuildIdentity.product=$($BuildIdentity.product)" `
            "--config.extraMetadata.searchBuildIdentity.version=$($BuildIdentity.version)" `
            "--config.extraMetadata.searchBuildIdentity.build_id=$($BuildIdentity.build_id)" `
            "--config.extraMetadata.searchBuildIdentity.source_commit=$($BuildIdentity.source_commit)" `
            "--config.extraMetadata.searchBuildIdentity.source_branch=$($BuildIdentity.source_branch)" `
            "--config.extraMetadata.searchBuildIdentity.build_timestamp_utc=$($BuildIdentity.build_timestamp_utc)"
        if ($LASTEXITCODE -ne 0) { throw "search_electron_packaging_failed" }
    }
    finally { Pop-Location }

    & $NodeExe $Finalize --packaged-root $PackagedRoot
    if ($LASTEXITCODE -ne 0) { throw "search_windows_resource_finalize_failed" }

    Push-Location $ProjectRoot
    try {
        $PackagedRootJson = $PackagedRoot.Replace('\', '\\')
        & $NodeExe --input-type=module -e (
            "const m=await import('$VerifyModule');" +
            "console.log(JSON.stringify(await m.verifyPackagedResources('$PackagedRootJson')));"
        )
        if ($LASTEXITCODE -ne 0) { throw "search_packaged_resource_verification_failed" }
    }
    finally { Pop-Location }

    if (Test-Path -LiteralPath (Join-Path $PackagedRoot "search-desktop.local.json")) {
        throw "search_candidate_contains_machine_local_config"
    }

    $RuntimeRoot = Join-Path $PackagedRoot "resources\app\runtime-project"
    Push-Location $RuntimeRoot
    try {
        $env:SEARCH_RUNTIME_ROOT = $RuntimeRoot
        & $PythonExe -B -c "import app.main; print('SEARCH_PACKAGED_PYTHON_IMPORT_OK')"
        if ($LASTEXITCODE -ne 0) { throw "search_packaged_python_import_failed" }
    }
    finally { Pop-Location }

    $SourceText = $ProjectRoot.Replace('\', '/').ToLowerInvariant()
    foreach ($File in @(Get-ChildItem -LiteralPath $PackagedRoot -Recurse -File | Where-Object {
        $_.Extension.ToLowerInvariant() -in @('.json', '.js', '.cjs', '.mjs', '.py', '.txt', '.html', '.css', '.yml', '.yaml')
    })) {
        $Value = (Get-Content -Raw -LiteralPath $File.FullName).Replace('\\', '\').Replace('\', '/').ToLowerInvariant()
        if ($Value.Contains($SourceText)) {
            throw "search_candidate_contains_source_path:$($File.FullName)"
        }
    }

    $FrontendInfo = Get-TreeInfo (Join-Path $ProjectRoot "frontend\dist")
    $PackagedFrontendInfo = Get-TreeInfo (Join-Path $PackagedRoot "resources\search-assets\frontend")
    if ($FrontendInfo.sha256 -ne $PackagedFrontendInfo.sha256) {
        throw "search_packaged_frontend_does_not_match_latest_build"
    }

    $PackagedPackagePath = Join-Path $PackagedRoot "resources\app\package.json"
    $PackagedPackage = Get-Content -Raw -LiteralPath $PackagedPackagePath | ConvertFrom-Json
    $PackagedIdentity = $PackagedPackage.searchBuildIdentity
    foreach ($Field in $BuildIdentity.Keys) {
        if ([string]$PackagedIdentity.$Field -ne [string]$BuildIdentity[$Field]) {
            throw "search_packaged_build_identity_mismatch:$Field"
        }
    }
    $FinalHead = (& $GitExe -C $ProjectRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $FinalHead -ne $SourceCommit) {
        throw "search_source_commit_changed"
    }
    $FinalStatus = @(& $GitExe -C $ProjectRoot status --porcelain --untracked-files=normal)
    if ($LASTEXITCODE -ne 0 -or $FinalStatus.Count -ne 0) {
        throw "search_build_changed_tracked_worktree"
    }
    foreach ($LockFile in $LockFiles) {
        $FinalLockHash = (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash
        if ($FinalLockHash -ne $LockHashesBefore[$LockFile]) {
            throw "search_package_lock_changed:$LockFile"
        }
    }
    $Executable = Join-Path $PackagedRoot "Search.exe"
    $AppInfo = Get-TreeInfo (Join-Path $PackagedRoot "resources\app")
    $TreeInfo = Get-TreeInfo $PackagedRoot
    $Manifest = [ordered]@{
        status = "ready"
        build_identity = $BuildIdentity
        product = $BuildIdentity.product
        version = $BuildIdentity.version
        build_id = $BuildIdentity.build_id
        source_commit = $BuildIdentity.source_commit
        source_branch = $BuildIdentity.source_branch
        build_timestamp_utc = $BuildIdentity.build_timestamp_utc
        output_root = $OutputRoot
        candidate_path = $PackagedRoot
        file_count = $TreeInfo.file_count
        total_bytes = $TreeInfo.total_bytes
        search_exe_sha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToUpperInvariant()
        resources_app_sha256 = $AppInfo.sha256
        complete_tree_sha256 = $TreeInfo.sha256
        frontend_tree_sha256 = $FrontendInfo.sha256
        production_data_bundled = $false
        machine_local_config_bundled = $false
        current_formal_package_untouched = $true
    }
    $ManifestPath = Join-Path $OutputRoot "search-build-report.json"
    $Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
    $Manifest | ConvertTo-Json -Depth 5
}
finally {
    foreach ($Name in $Original.Keys) {
        Set-Item "Env:$Name" $Original[$Name]
    }
}
