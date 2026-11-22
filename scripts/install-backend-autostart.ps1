$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait
    exit 0
}

$taskDirectory = Join-Path $env:LOCALAPPDATA 'LocalLLM'
New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
$backendScript = Join-Path $taskDirectory 'backend-lan.ps1'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'backend-lan.ps1') -Destination $backendScript -Force
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$userPrincipal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Limited
$adminPrincipal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$linuxStartup = "cd '/home/michael/AI/LocalLLM' && ./scripts/start-backend-boot.sh"
$startupAction = New-ScheduledTaskAction -Execute 'wsl.exe' `
    -Argument "--distribution Ubuntu-24.04 --exec bash -lc `"$linuxStartup`""
Register-ScheduledTask -TaskName 'Local AI Backend Startup' `
    -Action $startupAction `
    -Trigger $trigger -Principal $userPrincipal -Settings $settings `
    -Description 'Starts LocalLLM backend mode in the current WSL user session at Windows logon.' -Force | Out-Null
Register-ScheduledTask -TaskName 'Local AI Backend' `
    -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backendScript`"") `
    -Trigger $trigger -Principal $adminPrincipal -Settings $settings `
    -Description 'Refreshes Windows-to-WSL LocalSubnet forwarding for LocalLLM at Windows logon.' -Force | Out-Null

Write-Output 'Installed normal-user WSL startup and elevated LAN forwarding tasks for the current Windows user.'
