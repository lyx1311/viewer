param(
    [ValidateSet('Install','Uninstall','Run','Status')]
    [string]$Action = 'Install'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python.exe -ErrorAction Stop).Source
$PythonwCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
$Pythonw = if ($PythonwCommand) { $PythonwCommand.Source } else { $Python }
$Script = Join-Path $Root 'sync_sustech.py'
$TaskName = 'Viewer SUSTech Sync'

if ($Action -eq 'Uninstall') {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

if ($Action -eq 'Run') {
    & $Python $Script --sync
    exit $LASTEXITCODE
}

if ($Action -eq 'Status') {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Get-ScheduledTaskInfo
    & $Python $Script --status
    exit $LASTEXITCODE
}

$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$ActionObject = New-ScheduledTaskAction -Execute $Pythonw -Argument ('"{0}" --sync' -f $Script) -WorkingDirectory $Root
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$LogonTrigger.Delay = 'PT2M'
$RepeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10)
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $ActionObject -Trigger @($LogonTrigger, $RepeatTrigger) -Settings $Settings -Principal $Principal -Description 'Publish D:\SUSTech Markdown and referenced images to viewer.lyx1311.top.'
Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Write-Host "Installed scheduled task: $TaskName"
