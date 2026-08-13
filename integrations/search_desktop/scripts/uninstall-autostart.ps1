[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "autostart-common.ps1")
$Identity = Get-SearchDesktopUserIdentity
$Task = Get-ScheduledTask -TaskName $SearchDesktopTaskName -TaskPath $SearchDesktopTaskPath -ErrorAction SilentlyContinue

if ($null -eq $Task) {
    [ordered]@{
        status = "not_installed"
        available = $true
        enabled = $false
        task_name = $SearchDesktopTaskName
    } | ConvertTo-Json -Compress
    exit 0
}
if (-not (Test-OwnedSearchDesktopTask $Task $ExecutablePath $WorkingDirectory $Identity)) {
    throw "search_desktop_autostart_ownership_mismatch"
}

Unregister-ScheduledTask -TaskName $SearchDesktopTaskName -TaskPath $SearchDesktopTaskPath -Confirm:$false
if ($null -ne (Get-ScheduledTask -TaskName $SearchDesktopTaskName -TaskPath $SearchDesktopTaskPath -ErrorAction SilentlyContinue)) {
    throw "search_desktop_autostart_remove_failed"
}
[ordered]@{
    status = "uninstalled"
    available = $true
    enabled = $false
    task_name = $SearchDesktopTaskName
} | ConvertTo-Json -Compress
