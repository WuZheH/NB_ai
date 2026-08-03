[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("inspect", "validate", "set", "migrate-legacy", "backup")]
    [string]$Action,
    [string]$ConfigPath,
    [string]$DataDir,
    [string]$PythonExe,
    [string]$NodeExe,
    [string]$LegacyConfigPath,
    [string]$NodeRuntime
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $ConfigPath) {
    $Roaming = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if (-not $Roaming) { throw "search_roaming_app_data_unavailable" }
    $ConfigPath = Join-Path $Roaming "Search\desktop-runtime.json"
}
if (-not [System.IO.Path]::IsPathRooted($ConfigPath)) {
    throw "desktop_runtime_path_not_absolute"
}
if (-not $NodeRuntime) {
    $NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($NodeCommand) { $NodeRuntime = $NodeCommand.Source }
}
if (-not $NodeRuntime -or -not (Test-Path -LiteralPath $NodeRuntime -PathType Leaf)) {
    throw "search_node_executable_required"
}

$Arguments = @(
    (Join-Path $PSScriptRoot "configure_search_desktop_runtime.mjs"),
    $Action,
    "--config-path", [System.IO.Path]::GetFullPath($ConfigPath)
)
if ($Action -eq "set") {
    if (-not $DataDir -or -not $PythonExe -or -not $NodeExe) {
        throw "desktop_runtime_required_field_missing"
    }
    $Arguments += @(
        "--data-dir", $DataDir,
        "--python-exe", $PythonExe,
        "--node-exe", $NodeExe
    )
}
if ($Action -eq "migrate-legacy") {
    if (-not $LegacyConfigPath) { throw "desktop_runtime_required_field_missing" }
    $Arguments += @("--legacy-config-path", $LegacyConfigPath)
}

& $NodeRuntime @Arguments
exit $LASTEXITCODE
