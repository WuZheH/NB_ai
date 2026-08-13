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
    [ordered]@{
        status = "ownership_mismatch"
        available = $true
        enabled = $false
        task_name = $SearchDesktopTaskName
        error_code = "search_desktop_autostart_ownership_mismatch"
    } | ConvertTo-Json -Compress
    exit 0
}

$Info = Get-ScheduledTaskInfo -TaskName $SearchDesktopTaskName -TaskPath $SearchDesktopTaskPath -ErrorAction Stop
[ordered]@{
    status = "installed"
    available = $true
    enabled = $true
    task_name = $Task.TaskName
    state = [string]$Task.State
    executable = $Task.Actions[0].Execute
    working_directory = $Task.Actions[0].WorkingDirectory
    user_id = $Task.Principal.UserId
    delay = $Task.Triggers[0].Delay
    last_task_result = $Info.LastTaskResult
} | ConvertTo-Json -Compress
