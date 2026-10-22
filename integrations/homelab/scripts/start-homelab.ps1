<#
.SYNOPSIS
Run the hub/worker in the foreground, or explicitly install a current-user logon task.
.DESCRIPTION
No administrator installation, network change, or existing-task replacement occurs.
The optional task runs only in this user's logged-in session. It is not a boot service.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [ValidateSet('hub', 'worker')][string]$Role = 'hub',
    [switch]$InstallStartup,
    [string]$TaskName = ''
)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This launcher is intended for Windows. Use start-homelab.sh on macOS.'
}
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).ProviderPath
$resolvedPython = (Get-Command -Name $PythonPath -CommandType Application).Source
$labPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'lab.py'
if (-not (Test-Path -LiteralPath $resolvedConfig -PathType Leaf)) {
    throw 'Configuration path must name an existing JSON file.'
}
if (-not (Test-Path -LiteralPath $labPath -PathType Leaf)) {
    throw 'lab.py is missing beside the scripts directory.'
}
$command = if ($Role -eq 'hub') { 'serve' } else { 'worker' }

if ($InstallStartup) {
    if (-not $TaskName) { $TaskName = "OdysseusHomelab-$Role" }
    if ($TaskName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        throw 'Task name must contain only letters, numbers, dots, underscores, or hyphens.'
    }
    if (Get-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction SilentlyContinue) {
        throw 'A task with that name already exists. Review it manually; no task was replaced.'
    }
    # Schedule Python directly; startup does not depend on a PowerShell script policy.
    foreach ($path in @($labPath, $resolvedConfig, $resolvedPython)) {
        if ($path -match '["\r\n]') { throw 'Startup paths cannot contain quotes or newlines.' }
    }
    $arguments = '-m homelab.windows_supervisor --role {0} --config "{1}"' -f $Role, $resolvedConfig
    $action = New-ScheduledTaskAction -Execute $resolvedPython -Argument $arguments `
        -WorkingDirectory (Split-Path -Parent $labPath)
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action `
        -Principal $principal -Trigger $trigger -Settings $settings `
        -Description 'Opt-in home lab process for this logged-in user; no boot-before-login guarantee.' | Out-Null
    Write-Output "Created logon task '$TaskName'. It starts at your next login."
    Write-Output 'Run this script without -InstallStartup for an immediate foreground check.'
    exit 0
}

& $resolvedPython $labPath $command --config $resolvedConfig
exit $LASTEXITCODE
