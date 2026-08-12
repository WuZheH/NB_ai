$SearchDesktopTaskName = "Search Desktop"
$SearchDesktopTaskPath = "\"
$SearchDesktopTaskDescription = "Starts Search Desktop for the current user."

function Get-SearchDesktopUserIdentity {
    return [ordered]@{
        name = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        short_name = [Environment]::UserName
    }
}

function Test-SearchDesktopPathEqual {
    param([string]$Left, [string]$Right)
    try {
        return [string]::Equals(
            [System.IO.Path]::GetFullPath($Left).TrimEnd('\'),
            [System.IO.Path]::GetFullPath($Right).TrimEnd('\'),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        return $false
    }
}

function Test-SearchDesktopUserEqual {
    param([string]$Value, $Identity)
    return (
        [string]::Equals($Value, $Identity.name, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($Value, $Identity.sid, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($Value, $Identity.short_name, [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function Test-OwnedSearchDesktopTask {
    param($Task, [string]$ExecutablePath, [string]$WorkingDirectory, $Identity)
    if ($null -eq $Task -or
        $Task.TaskPath -ne $SearchDesktopTaskPath -or
        $Task.Description -cne $SearchDesktopTaskDescription -or
        @($Task.Actions).Count -ne 1 -or
        @($Task.Triggers).Count -ne 1) {
        return $false
    }
    $Action = $Task.Actions[0]
    $Trigger = $Task.Triggers[0]
    return (
        (Test-SearchDesktopPathEqual $Action.Execute $ExecutablePath) -and
        [string]::IsNullOrEmpty([string]$Action.Arguments) -and
        (Test-SearchDesktopPathEqual $Action.WorkingDirectory $WorkingDirectory) -and
        (Test-SearchDesktopUserEqual $Task.Principal.UserId $Identity) -and
        ([string]$Task.Principal.LogonType -eq "Interactive") -and
        ([string]$Task.Principal.RunLevel -eq "Limited") -and
        ($Trigger.Delay -eq "PT20S") -and
        (Test-SearchDesktopUserEqual $Trigger.UserId $Identity)
    )
}
