[CmdletBinding()]
param(
    [string]$PythonExe = $env:SEARCH_PYTHON,
    [string]$NodeExe = $env:SEARCH_NODE,
    [switch]$CheckOnly,
    [switch]$IncludePackagedSmoke,
    [string]$PackagedExecutable
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

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

$FrontendTests = @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "frontend\tests") -Filter "*.test.mjs" -File | Select-Object -ExpandProperty FullName)
$DesktopTests = @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "integrations\search_desktop\tests") -Filter "*.test.mjs" -File | Select-Object -ExpandProperty FullName)
$McpTestRunner = Join-Path $ProjectRoot "integrations\notebook_ai_chatgpt_app\scripts\run-tests.mjs"
foreach ($Required in @($McpTestRunner)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "search_test_prerequisite_missing:$Required"
    }
}

if ($CheckOnly) {
    [ordered]@{
        status = "ready"
        mode = "check_only"
        python_test_root = "tests/core"
        frontend_test_count = $FrontendTests.Count
        desktop_test_count = $DesktopTests.Count
        mcp_test_runner = $McpTestRunner
    } | ConvertTo-Json -Depth 3
    exit 0
}

$RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$RunRoot = Join-Path $ProjectRoot ".codex_tmp\test-all\$RunId"
$TempDir = Join-Path $RunRoot "temp"
$DataDir = Join-Path $RunRoot "data"
$RuntimeDir = Join-Path $RunRoot "runtime"
$LogDir = Join-Path $RunRoot "logs"
$ConfigDir = Join-Path $RunRoot "config"
$LocalAppDataDir = Join-Path $RunRoot "local-app-data"
$RoamingAppDataDir = Join-Path $RunRoot "roaming-app-data"
$NpmCache = Join-Path $RunRoot "npm-cache"
foreach ($Directory in @(
    $RunRoot,
    $TempDir,
    $RuntimeDir,
    $LogDir,
    $ConfigDir,
    $LocalAppDataDir,
    $RoamingAppDataDir,
    $NpmCache
)) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}

$Original = @{
    TEMP = $env:TEMP
    TMP = $env:TMP
    LOCALAPPDATA = $env:LOCALAPPDATA
    APPDATA = $env:APPDATA
    SEARCH_DATA_DIR = $env:SEARCH_DATA_DIR
    SEARCH_RUNTIME_DIR = $env:SEARCH_RUNTIME_DIR
    SEARCH_LOG_DIR = $env:SEARCH_LOG_DIR
    SEARCH_CONFIG_DIR = $env:SEARCH_CONFIG_DIR
    SEARCH_PYTHON = $env:SEARCH_PYTHON
    SEARCH_NODE = $env:SEARCH_NODE
    SEARCH_ELECTRON_TEST_MODE = $env:SEARCH_ELECTRON_TEST_MODE
    ELECTRON_DISABLE_CRASH_REPORTING = $env:ELECTRON_DISABLE_CRASH_REPORTING
    PYTHONDONTWRITEBYTECODE = $env:PYTHONDONTWRITEBYTECODE
    PYTEST_ADDOPTS = $env:PYTEST_ADDOPTS
    npm_config_cache = $env:npm_config_cache
}

try {
    $env:TEMP = $TempDir
    $env:TMP = $TempDir
    $env:LOCALAPPDATA = $LocalAppDataDir
    $env:APPDATA = $RoamingAppDataDir
    $env:SEARCH_DATA_DIR = $DataDir
    $env:SEARCH_RUNTIME_DIR = $RuntimeDir
    $env:SEARCH_LOG_DIR = $LogDir
    $env:SEARCH_CONFIG_DIR = $ConfigDir
    $env:SEARCH_PYTHON = $PythonExe
    $env:SEARCH_NODE = $NodeExe
    $env:SEARCH_ELECTRON_TEST_MODE = "1"
    $env:ELECTRON_DISABLE_CRASH_REPORTING = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTEST_ADDOPTS = "-p no:cacheprovider"
    $env:npm_config_cache = $NpmCache

    & $PythonExe -B -m pytest -q (Join-Path $ProjectRoot "tests\core") `
        --basetemp (Join-Path $RunRoot "pytest-temp")
    if ($LASTEXITCODE -ne 0) { throw "search_python_tests_failed" }

    if ($FrontendTests.Count -gt 0) {
        & $NodeExe --test @FrontendTests
        if ($LASTEXITCODE -ne 0) { throw "search_frontend_tests_failed" }
    }

    if ($DesktopTests.Count -gt 0) {
        & $NodeExe --test @DesktopTests
        if ($LASTEXITCODE -ne 0) { throw "search_desktop_tests_failed" }
    }

    & $NodeExe $McpTestRunner
    if ($LASTEXITCODE -ne 0) { throw "search_mcp_tests_failed" }

    if ($IncludePackagedSmoke) {
        if (-not $PackagedExecutable) { throw "search_packaged_executable_required" }
        & (Join-Path $ProjectRoot "integrations\search_desktop\scripts\smoke-packaged-search.ps1") `
            -ExecutablePath $PackagedExecutable `
            -ProjectRoot $ProjectRoot `
            -PythonExe $PythonExe `
            -NodeExe $NodeExe `
            -TestRoot (Join-Path $RunRoot "packaged-smoke")
        if ($LASTEXITCODE -ne 0) { throw "search_packaged_smoke_failed" }
    }

    [ordered]@{
        status = "passed"
        run_root = $RunRoot
        python = "passed"
        frontend = "passed"
        desktop = "passed"
        mcp = "passed"
        packaged_smoke = if ($IncludePackagedSmoke) { "passed" } else { "not_requested" }
    } | ConvertTo-Json -Depth 3
}
finally {
    foreach ($Name in $Original.Keys) {
        Set-Item "Env:$Name" $Original[$Name]
    }
}
