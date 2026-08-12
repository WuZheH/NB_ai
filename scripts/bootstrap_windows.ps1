[CmdletBinding()]
param(
    [string]$PythonExe = $env:SEARCH_PYTHON,
    [string]$NodeExe = $env:SEARCH_NODE,
    [switch]$Install,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Install -and $CheckOnly) {
    throw "search_bootstrap_mode_conflict"
}

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$LockFile = Join-Path $ProjectRoot "requirements.lock.txt"
$TempRoot = Join-Path $ProjectRoot ".codex_tmp\bootstrap"
$PipCache = Join-Path $TempRoot "pip-cache"
$NpmCache = Join-Path $TempRoot "npm-cache"

function Resolve-SearchExecutable {
    param(
        [string]$Configured,
        [string[]]$ProbeNames,
        [string]$ErrorCode
    )

    if ($Configured) {
        $Candidate = $Configured
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($Candidate)
        }
        $Command = Get-Command $Candidate -CommandType Application -ErrorAction SilentlyContinue
        if ($Command) { return [System.IO.Path]::GetFullPath($Command.Source) }
        throw $ErrorCode
    }
    foreach ($Name in $ProbeNames) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
        if ($Command) { return [System.IO.Path]::GetFullPath($Command.Source) }
    }
    throw $ErrorCode
}

if (-not $PythonExe) {
    throw "search_python_not_configured_set_SEARCH_PYTHON"
}
$PythonExe = Resolve-SearchExecutable `
    -Configured $PythonExe `
    -ProbeNames @() `
    -ErrorCode "search_python_executable_unavailable"
$NodeExe = Resolve-SearchExecutable `
    -Configured $NodeExe `
    -ProbeNames @("node.exe", "node") `
    -ErrorCode "search_node_executable_unavailable"

$NpmCandidate = Join-Path (Split-Path -Parent $NodeExe) "npm.cmd"
$NpmCmd = if (Test-Path -LiteralPath $NpmCandidate -PathType Leaf) {
    $NpmCandidate
} else {
    Resolve-SearchExecutable `
        -Configured "" `
        -ProbeNames @("npm.cmd", "npm") `
        -ErrorCode "search_npm_executable_unavailable"
}

if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    throw "search_python_lock_file_missing"
}

$PythonInfo = (& $PythonExe -B -c (
    "import json,platform,sys; " +
    "print(json.dumps({'version':platform.python_version(),'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"
) | ConvertFrom-Json)
if (-not ([string]$PythonInfo.version).StartsWith("3.11.")) {
    throw "search_python_3_11_required"
}

$NodeVersion = (& $NodeExe --version).Trim()
$NpmVersion = (& $NpmCmd --version).Trim()

if ($Install) {
    $ProjectVenv = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv"))
    $PythonPrefix = [System.IO.Path]::GetFullPath([string]$PythonInfo.prefix)
    $InsideProjectVenv = $PythonPrefix.StartsWith(
        $ProjectVenv,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $ActiveConda = $env:CONDA_PREFIX -and
        ([System.IO.Path]::GetFullPath($env:CONDA_PREFIX)).Equals(
            $PythonPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        $env:CONDA_DEFAULT_ENV -and
        $env:CONDA_DEFAULT_ENV -ne "base"
    if (-not $InsideProjectVenv -and -not $ActiveConda) {
        throw "search_install_requires_project_venv_or_active_non_base_conda"
    }

    foreach ($Directory in @($TempRoot, $PipCache, $NpmCache)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }
    & $PythonExe -B -m pip install --cache-dir $PipCache -r $LockFile
    if ($LASTEXITCODE -ne 0) { throw "search_python_dependency_install_failed" }

    foreach ($PackageRoot in @(
        "frontend",
        "integrations\search_desktop",
        "integrations\notebook_ai_chatgpt_app",
        "packages\search-design-system"
    )) {
        & $NpmCmd --prefix (Join-Path $ProjectRoot $PackageRoot) ci `
            --cache $NpmCache --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            throw "search_node_dependency_install_failed:$PackageRoot"
        }
    }
}

& $PythonExe -B -c (
    "import bs4,fastapi,fitz,httpx,lancedb,numpy,PIL,psutil,pydantic,pypdfium2,sqlalchemy,typer,uvicorn; " +
    "print('SEARCH_PYTHON_IMPORTS_OK')"
)
if ($LASTEXITCODE -ne 0) {
    throw "search_python_dependency_check_failed_run_bootstrap_with_Install"
}

foreach ($RequiredNodePath in @(
    "frontend\node_modules\vite\bin\vite.js",
    "integrations\search_desktop\node_modules\electron\cli.js",
    "integrations\search_desktop\node_modules\electron-builder\cli.js",
    "integrations\notebook_ai_chatgpt_app\node_modules\esbuild\bin\esbuild"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $RequiredNodePath) -PathType Leaf)) {
        throw "search_node_dependency_check_failed_run_bootstrap_with_Install:$RequiredNodePath"
    }
}

[ordered]@{
    status = "ready"
    mode = if ($Install) { "installed_and_checked" } else { "checked" }
    project_root = $ProjectRoot
    python = [string]$PythonInfo.version
    node = $NodeVersion
    npm = $NpmVersion
    path_modified = $false
    registry_modified = $false
    service_installed = $false
} | ConvertTo-Json -Depth 3
