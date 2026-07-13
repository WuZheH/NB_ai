$ErrorActionPreference = "Stop"
$inspector = $env:NOTEBOOK_AI_MCP_INSPECTOR
if (-not (Test-Path -LiteralPath $inspector)) {
    throw "Set NOTEBOOK_AI_MCP_INSPECTOR to an existing @modelcontextprotocol/inspector@0.22.0 executable."
}
& $inspector
