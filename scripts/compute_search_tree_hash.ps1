[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$Culture,
    [switch]$IncludeFiles
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8

if ($Culture) {
    $CultureInfo = [System.Globalization.CultureInfo]::GetCultureInfo($Culture)
    [System.Threading.Thread]::CurrentThread.CurrentCulture = $CultureInfo
    [System.Threading.Thread]::CurrentThread.CurrentUICulture = $CultureInfo
}

. (Join-Path $PSScriptRoot "lib\search_tree_hash.ps1")
Get-SearchTreeHash -Root $Root -IncludeFiles:$IncludeFiles | ConvertTo-Json -Depth 5 -Compress
