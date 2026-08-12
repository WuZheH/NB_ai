[CmdletBinding()]
param(
    [string]$PythonExe = $env:SEARCH_PYTHON,
    [string]$NodeExe = $env:SEARCH_NODE,
    [string]$DataDir = $env:SEARCH_DATA_DIR,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $DataDir) { $DataDir = Join-Path $ProjectRoot "data" }
$DataDir = [System.IO.Path]::GetFullPath($DataDir)

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
$NpmCandidate = Join-Path (Split-Path -Parent $NodeExe) "npm.cmd"
$NpmCmd = Resolve-Executable `
    $(if (Test-Path -LiteralPath $NpmCandidate -PathType Leaf) { $NpmCandidate } else { "" }) `
    @("npm.cmd", "npm") `
    "search_npm_executable_unavailable"

$Required = @(
    (Join-Path $ProjectRoot "scripts\runtime\notebook_ai_launcher.py"),
    (Join-Path $ProjectRoot "frontend\dist\index.html"),
    (Join-Path $ProjectRoot "integrations\search_desktop\node_modules\electron\cli.js"),
    (Join-Path $ProjectRoot "integrations\notebook_ai_chatgpt_app\dist\server\index.js"),
    (Join-Path $ProjectRoot "integrations\notebook_ai_chatgpt_app\web\dist\widget.html")
)
foreach ($Path in $Required) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "search_dev_prerequisite_missing:$Path"
    }
}

if ($CheckOnly) {
    [ordered]@{
        status = "ready"
        mode = "check_only"
        project_root = $ProjectRoot
        data_dir = $DataDir
        python = $PythonExe
        node = $NodeExe
        path_modified = $false
        registry_modified = $false
    } | ConvertTo-Json -Depth 3
    exit 0
}

$Original = @{
    SEARCH_RUNTIME_ROOT = $env:SEARCH_RUNTIME_ROOT
    SEARCH_DATA_DIR = $env:SEARCH_DATA_DIR
    SEARCH_PYTHON = $env:SEARCH_PYTHON
    SEARCH_NODE = $env:SEARCH_NODE
}
try {
    $env:SEARCH_RUNTIME_ROOT = $ProjectRoot
    $env:SEARCH_DATA_DIR = $DataDir
    $env:SEARCH_PYTHON = $PythonExe
    $env:SEARCH_NODE = $NodeExe

    $Launcher = Join-Path $ProjectRoot "scripts\runtime\notebook_ai_launcher.py"
    & $PythonExe -B $Launcher ensure-running
    if ($LASTEXITCODE -ne 0) { throw "search_local_runtime_start_failed" }

    & $NpmCmd --prefix (Join-Path $ProjectRoot "integrations\search_desktop") start
    if ($LASTEXITCODE -ne 0) { throw "search_desktop_start_failed" }
}
finally {
    foreach ($Name in $Original.Keys) {
        Set-Item "Env:$Name" $Original[$Name]
    }
}
