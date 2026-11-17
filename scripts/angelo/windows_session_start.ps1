# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# The desktop session's start, proven (dot-sourced by windows_bootstrap_stages.ps1 and windows_final_runs.ps1; functions
# only). Both desktop stages sign a throwaway user in after a reboot and wait for that session's DONE: a session that
# never starts (no automatic sign-in, no Run-key entry) and one that is still working look the same for as long as the
# stage waits (35+ minutes for the GUI checks, the bench's hours). 2026-11-04: a held GUI session's boot log, read while
# it held, was taken for one that never started. Each session now writes its STARTED marker first; the stage waits for
# it a few minutes (angelo-session-start-minutes, default 2) and, without it, fails at once saying why.

if (-not (Get-Command Invoke-NativeCapture -ErrorAction SilentlyContinue)) {   # Its caller may have loaded it.
    . (Join-Path $PSScriptRoot "windows_native_call.ps1")
}

# $true once $Marker was written since this boot, or any of $Done exists; $false after $Minutes.
function Wait-SessionStarted([string]$Marker, [int]$Minutes, [string[]]$Done = @()) {
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $until = (Get-Date).AddMinutes($Minutes)
    while ((Get-Date) -lt $until) {
        if ((Test-Path $Marker) -and (Get-Item $Marker).LastWriteTime -ge $boot) { return $true }
        foreach ($d in $Done) { if (Test-Path $d) { return $true } }
        Start-Sleep -Seconds 5
    }
    return $false
}

# Why the session of $User (started by the Run-key value $RunValue) did not start: the automatic sign-in's settings,
# the user and its profiles, the Run-key entry, the signed-in sessions and this boot's Winlogon events.
function Get-SessionNotStarted([string]$User, [string]$RunValue, [int]$Minutes) {
    $ErrorActionPreference = "Continue"   # query.exe writes "No User exists" to stderr
    $w = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue
    $exists = [bool](Get-LocalUser -Name $User -ErrorAction SilentlyContinue)
    $profiles = @(Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "C:\Users\$User*" } |
        ForEach-Object { "$($_.LocalPath) loaded=$($_.Loaded)" }) -join ", "
    $run = [bool](Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue).$RunValue
    $sessions = @((Invoke-NativeCapture query.exe @("user")).Lines | ForEach-Object { "$_".Trim() }) -join " | "
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = "System"; ProviderName = "Microsoft-Windows-Winlogon"; StartTime = $boot } `
        -MaxEvents 6 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Id)@$($_.TimeCreated.ToString('HH:mm:ss'))" }) -join " "
    "the desktop session of $User did not start within $Minutes min of this boot: AutoAdminLogon=$($w.AutoAdminLogon)" +
        " DefaultUserName=$($w.DefaultUserName) password set=$([bool]$w.DefaultPassword) user exists=$exists" +
        " profiles=[$profiles] Run key $RunValue=$run sessions=[$sessions] Winlogon events=[$events]"
}
