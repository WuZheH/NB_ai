$ErrorActionPreference = "Stop"
$inspector = $env:SEARCH_MCP_INSPECTOR
if (-not $inspector) { $inspector = $env:NOTEBOOK_AI_MCP_INSPECTOR }
if (-not $inspector -or -not (Test-Path -LiteralPath $inspector -PathType Leaf)) {
    throw "Set SEARCH_MCP_INSPECTOR to an existing @modelcontextprotocol/inspector@0.22.0 executable."
}
& $inspector
