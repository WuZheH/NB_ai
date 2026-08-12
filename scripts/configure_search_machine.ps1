[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("inspect", "validate", "set")]
    [string]$Action,
    [string]$ConfigPath,
    [string]$EmbeddingModelPath,
    [string]$RerankerModelPath,
    [string]$PythonExe = $env:SEARCH_PYTHON
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "search_python_executable_required"
}
if (-not $ConfigPath) {
    $Roaming = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if (-not $Roaming) { throw "search_roaming_app_data_unavailable" }
    $ConfigPath = Join-Path $Roaming "Search\machine-config.json"
}
$Arguments = @(
    "-B",
    (Join-Path $PSScriptRoot "configure_search_machine.py"),
    $Action,
    "--config-path",
    [System.IO.Path]::GetFullPath($ConfigPath)
)
if ($Action -eq "set") {
    if (-not $EmbeddingModelPath -or -not $RerankerModelPath) {
        throw "required_field_missing"
    }
    $Arguments += @(
        "--embedding-model-path", [System.IO.Path]::GetFullPath($EmbeddingModelPath),
        "--reranker-model-path", [System.IO.Path]::GetFullPath($RerankerModelPath)
    )
}
& $PythonExe @Arguments
exit $LASTEXITCODE
