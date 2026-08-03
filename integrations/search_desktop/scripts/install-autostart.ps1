[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "autostart-common.ps1")
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "search_desktop_executable_unavailable"
}
if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
    throw "search_desktop_working_directory_unavailable"
}

$Identity = Get-SearchDesktopUserIdentity
$Existing = Get-ScheduledTask -TaskName $SearchDesktopTaskName -TaskPath $SearchDesktopTaskPath -ErrorAction SilentlyContinue
if ($null -ne $Existing -and
    -not (Test-OwnedSearchDesktopTask $Existing $ExecutablePath $WorkingDirectory $Identity)) {
    throw "search_desktop_autostart_ownership_mismatch"
}

$Action = New-ScheduledTaskAction -Execute $ExecutablePath -WorkingDirectory $WorkingDirectory
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity.name
$Trigger.Delay = "PT20S"
$Principal = New-ScheduledTaskPrincipal -UserId $Identity.name -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $SearchDesktopTaskName `
    -TaskPath $SearchDesktopTaskPath `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description $SearchDesktopTaskDescription `
    -Force | Out-Null

$Task = Get-ScheduledTask -TaskName $SearchDesktopTaskName -TaskPath $SearchDesktopTaskPath -ErrorAction Stop
if (-not (Test-OwnedSearchDesktopTask $Task $ExecutablePath $WorkingDirectory $Identity)) {
    throw "search_desktop_autostart_postcondition_failed"
}
[ordered]@{
    status = "installed"
    available = $true
    enabled = $true
    task_name = $Task.TaskName
    executable = $Task.Actions[0].Execute
    working_directory = $Task.Actions[0].WorkingDirectory
    current_user = $Identity.name
    delay = $Task.Triggers[0].Delay
} | ConvertTo-Json -Compress
