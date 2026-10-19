# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Remote commands for validation work on the throwaway Windows machine (dot-sourced; defines two functions).

.DESCRIPTION
  With the metadata value angelo-hold-minutes above zero, the machine does not power off after its stages: it runs
  each PowerShell script that appears under gs://<bucket>/<prefix>/ (cmd for the administrator, cmd-gui for the
  signed-in test user's desktop session), one at a time, and uploads its output and exit code to
  gs://<bucket>/cmd-out/. It stops waiting after that many idle minutes, or at once when gs://<bucket>/<prefix>-release
  appears, and the run then ends as usual (results uploaded, machine powered off). ANGELO_GCP_MAX_HOURS still caps the
  whole run. The trust is the source archive's: whoever can write the private bucket already decides what this machine
  builds and runs. scripts/angelo/gcp_windows_validate.sh send / sync / out / release drive it.
#>

function Invoke-QueuedCommands([string]$Gcloud, [string]$Bucket, [string]$Prefix, [string]$Logs, [string]$Work,
                               [int]$TimeoutMinutes = 150) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $ran = 0
    try {
        # Captured with 2>&1: an empty folder's "matched no objects" stays out of the transcript.
        $listed = @(& $Gcloud storage ls "gs://$Bucket/$Prefix/" 2>&1 | Where-Object { $_ -is [string] -and $_ -match "\.ps1$" } | Sort-Object)
        foreach ($object in $listed) {
            $name = [IO.Path]::GetFileNameWithoutExtension(($object -split "/")[-1])
            New-Item -ItemType Directory -Force $Work | Out-Null
            $local = Join-Path $Work "$name.ps1"
            & $Gcloud storage cp --quiet $object $local 2>&1 | Out-Null
            & $Gcloud storage rm --quiet $object 2>&1 | Out-Null
            if (-not (Test-Path $local)) { continue }
            $log = Join-Path $Logs "cmd-$Prefix-$name.log"
            Write-Host ("== remote command {0}/{1} ({2})" -f $Prefix, $name, (Get-Date -Format o))
            $process = Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$local`"" `
                -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru -WindowStyle Hidden
            $null = $process.Handle   # Keeps the exit code readable after the process ends.
            if (-not $process.WaitForExit($TimeoutMinutes * 60000)) {
                & taskkill.exe /T /F /PID $process.Id 2>&1 | Out-Null
                Add-Content $log "TIMED OUT after $TimeoutMinutes minutes"
            }
            $process.WaitForExit()
            if (Test-Path "$log.err") { Get-Content "$log.err" | Add-Content $log; Remove-Item "$log.err" -ErrorAction SilentlyContinue }
            Set-Content "$log.exit" ([string]$process.ExitCode)
            & $Gcloud storage cp --quiet $log "$log.exit" "gs://$Bucket/cmd-out/" 2>&1 | Out-Null
            $ran++
        }
    } finally { $ErrorActionPreference = $old }
    return $ran
}

function Wait-QueuedCommands([string]$Gcloud, [string]$Bucket, [string]$Prefix, [string]$Logs, [string]$Work,
                             [int]$IdleMinutes) {
    $idle = (Get-Date).AddMinutes($IdleMinutes)
    while ((Get-Date) -lt $idle) {
        if ((Invoke-QueuedCommands $Gcloud $Bucket $Prefix $Logs $Work) -gt 0) { $idle = (Get-Date).AddMinutes($IdleMinutes) }
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $release = @(& $Gcloud storage ls "gs://$Bucket/" 2>&1) -contains "gs://$Bucket/$Prefix-release"
        if ($release) { & $Gcloud storage rm --quiet "gs://$Bucket/$Prefix-release" 2>&1 | Out-Null }
        $ErrorActionPreference = $old
        if ($release) { Write-Host "== $Prefix released"; return }
        Start-Sleep -Seconds 15
    }
    Write-Host "== $Prefix idle for $IdleMinutes minutes"
}
