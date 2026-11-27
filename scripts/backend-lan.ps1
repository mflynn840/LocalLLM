param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LinuxProjectPath = '/home/michael/AI/LocalLLM',
    [switch]$StartBackend
)

$ErrorActionPreference = 'Stop'
if ($Distro -notmatch '^[A-Za-z0-9._-]+$' -or $LinuxProjectPath -notmatch '^/[A-Za-z0-9._/-]+$') { throw 'Invalid WSL distribution or project path.' }
$logDirectory = Join-Path $env:LOCALAPPDATA 'LocalLLM'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logPath = Join-Path $logDirectory 'backend-forward.log'
Set-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) Refreshing WSL forwarding."

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Add-Content -LiteralPath $logPath -Value 'Current task token is not elevated; requesting administrator token.'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Distro', $Distro, '-LinuxProjectPath', $LinuxProjectPath)
    if ($StartBackend) { $arguments += '-StartBackend' }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait
    exit 0
}

if ($StartBackend) {
    $logDirectory = Join-Path $env:LOCALAPPDATA 'LocalLLM'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logPath = Join-Path $logDirectory 'backend-start.log'
    Set-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) Starting backend autostart."
    # Docker Desktop often becomes ready shortly after the Windows user's logon.
    Start-Sleep -Seconds 30
    $started = $false
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        try {
            $startOutput = (& wsl.exe --distribution $Distro --exec bash -lc "cd '$LinuxProjectPath' && ./start.sh" 2>&1 | Out-String).Trim()
            $startExitCode = $LASTEXITCODE
        } catch {
            $startExitCode = 1
            $startOutput = $_.ToString()
        }
        Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) Attempt $attempt exit=$startExitCode"
        if ($startOutput) { $startOutput -split "`r?`n" | Select-Object -Last 20 | Add-Content -LiteralPath $logPath }
        if ($startExitCode -eq 0) { $started = $true; break }
        Start-Sleep -Seconds 15
    }
    if (-not $started) { throw "Could not start backend in WSL after retries. See $logPath" }
}

$addressOutput = (& wsl.exe --distribution $Distro -- hostname -I 2>&1 | Out-String).Trim()
Add-Content -LiteralPath $logPath -Value "WSL IP command exit=$LASTEXITCODE output=$addressOutput"
if ($LASTEXITCODE -ne 0) { throw "Could not get an IP address from WSL '$Distro': $addressOutput" }
$wslAddress = ($addressOutput -split '\s+' | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' -and $_ -notlike '127.*' } | Select-Object -First 1)
if (-not $wslAddress) { throw "No WSL IPv4 address found: $addressOutput" }

# This elevated task can attach the shared Jetson USB gadget on demand. The
# OpenHands and host helpers trigger this task only after a Jetson request.
try {
    $usbipd = (Get-Command usbipd.exe -ErrorAction Stop).Source
    $deviceLine = & $usbipd list | Where-Object { $_ -match '\b0955:7020\b' } | Select-Object -First 1
    if ($deviceLine -and $deviceLine -match '^\s*(\S+)\s+0955:7020\s+.*\s+(Not shared|Shared|Attached)\s*$') {
        $busId = $Matches[1]
        $deviceState = $Matches[2]
        if ($deviceState -eq 'Not shared') {
            & $usbipd bind --busid $busId | Out-String | Add-Content -LiteralPath $logPath
            if ($LASTEXITCODE -ne 0) { throw "usbipd bind failed for $busId." }
            $deviceState = 'Shared'
        }
        if ($deviceState -ne 'Attached') {
            & $usbipd attach --wsl --busid $busId | Out-String | Add-Content -LiteralPath $logPath
            if ($LASTEXITCODE -ne 0) { throw "usbipd attach failed for $busId." }
        }
        Add-Content -LiteralPath $logPath -Value "Jetson USB device $busId is attached to WSL."
    } else {
        Add-Content -LiteralPath $logPath -Value 'Jetson USB device 0955:7020 is not connected; skipping USB attach.'
    }
} catch {
    Add-Content -LiteralPath $logPath -Value "Jetson USB attach check failed: $($_.Exception.Message)"
}

# Remove the old shared OpenHands LAN exposure. Each visitor now runs their own local agent.
& netsh.exe interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0 2>$null | Out-Null
Get-NetFirewallRule -DisplayName 'OpenHands WSL LAN TCP 8000' -ErrorAction SilentlyContinue | Remove-NetFirewallRule

foreach ($mapping in @(@{ Listen = 3000; Connect = 3001 }, @{ Listen = 4000; Connect = 4001 })) {
    $port = $mapping.Listen
    & netsh.exe interface portproxy delete v4tov4 "listenport=$port" listenaddress=0.0.0.0 2>$null | Out-Null
    & netsh.exe interface portproxy add v4tov4 "listenport=$port" listenaddress=0.0.0.0 "connectport=$($mapping.Connect)" "connectaddress=$wslAddress"
    if ($LASTEXITCODE -ne 0) { throw "Could not configure Windows-to-WSL TCP $port forwarding." }
    Add-Content -LiteralPath $logPath -Value "Configured Windows TCP $port -> WSL TCP $($mapping.Connect)."

    $ruleName = "Local AI backend WSL LAN TCP $port"
    & netsh.exe advfirewall firewall delete rule "name=$ruleName" 2>$null | Out-Null
    & netsh.exe advfirewall firewall add rule "name=$ruleName" dir=in action=allow `
        protocol=TCP "localport=$port" remoteip=localsubnet profile=any enable=yes | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Add-Content -LiteralPath $logPath -Value "Failed to create firewall rule for TCP $port; netsh exit=$LASTEXITCODE."
        throw "Could not configure LocalSubnet firewall rule for TCP $port."
    }
    Add-Content -LiteralPath $logPath -Value "Configured LocalSubnet firewall rule for TCP $port."
}

Write-Output "Backend LAN forwarding is active: Windows TCP 3000/4000 -> WSL TCP 3001/4001 at $wslAddress (LocalSubnet only). OpenHands TCP 8000 is no longer forwarded."
