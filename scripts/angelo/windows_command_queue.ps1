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

$script:QueueRun = @{}        # name -> $true: commands this session already ran (never twice, even if a removal failed)
$script:QueueTries = @{}      # name -> failed fetches so far
$script:FetchTries = 3

function Write-QueueProblem([string]$Gcloud, [string]$Bucket, [string]$Prefix, [string]$Text) {
    # In the transcript, and as the newest problem in cmd-out (best effort: the upload may fail for the same reason).
    Write-Host ("== {0} queue problem ({1}): {2}" -f $Prefix, (Get-Date -Format o), $Text)
    $note = Join-Path ([IO.Path]::GetTempPath()) "cmd-$Prefix-queue-problem.txt"
    Set-Content $note ("{0} {1}" -f (Get-Date -Format o), $Text)
    & $Gcloud storage cp --quiet $note "gs://$Bucket/cmd-out/" 2>&1 | Out-Null
}

function Get-QueuedCommand([string]$Gcloud, [string]$Object, [string]$Local) {
    # Downloads one command; "" when it arrived whole and parses as PowerShell, else why not.
    Remove-Item $Local -Force -ErrorAction SilentlyContinue
    $said = @(& $Gcloud storage cp --quiet $Object $Local 2>&1 | ForEach-Object { [string]$_ })
    if ($LASTEXITCODE -ne 0) { return "download failed (exit $LASTEXITCODE): " + (($said -join " ").Trim() -replace "\s+", " ") }
    if (-not (Test-Path $Local)) { return "download reported success but wrote no file" }
    $tokens = $null; $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Local, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { return "does not parse as PowerShell: " + $errors[0].Message }
    return ""
}

function Invoke-QueuedCommands([string]$Gcloud, [string]$Bucket, [string]$Prefix, [string]$Logs, [string]$Work,
                               [int]$TimeoutMinutes = 150, [string]$Shell = "powershell.exe") {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $ran = 0
    try {
        # Captured with 2>&1: an empty folder's "matched no objects" is no problem, any other failed listing is.
        $said = @(& $Gcloud storage ls "gs://$Bucket/$Prefix/" 2>&1 | ForEach-Object { [string]$_ })
        if ($LASTEXITCODE -ne 0 -and -not ($said -match "matched no objects")) {
            Write-QueueProblem $Gcloud $Bucket $Prefix ("listing gs://$Bucket/$Prefix/ failed (exit $LASTEXITCODE): " +
                                                       (($said -join " ").Trim() -replace "\s+", " "))
            return 0
        }
        $listed = @($said | Where-Object { $_ -match "\.ps1$" } | Sort-Object)
        foreach ($object in $listed) {
            $name = [IO.Path]::GetFileNameWithoutExtension(($object -split "/")[-1])
            if ($script:QueueRun.ContainsKey($name)) {   # Ran already; its removal failed before: try that again only.
                & $Gcloud storage rm --quiet $object 2>&1 | Out-Null
                continue
            }
            New-Item -ItemType Directory -Force $Work | Out-Null
            $local = Join-Path $Work "$name.ps1"
            $log = Join-Path $Logs "cmd-$Prefix-$name.log"
            $why = Get-QueuedCommand $Gcloud $object $local
            if ($why) {
                $tries = 1 + [int]$script:QueueTries[$name]
                $script:QueueTries[$name] = $tries
                Write-QueueProblem $Gcloud $Bucket $Prefix ("$name, try $tries of $($script:FetchTries): $why")
                if ($tries -lt $script:FetchTries) { continue }   # Kept in the bucket: the next look tries again.
                New-Item -ItemType Directory -Force $Logs | Out-Null
                Set-Content $log ("NOT RUN: the command could not be fetched after {0} tries; last: {1}" -f $tries, $why)
                Set-Content "$log.exit" "fetch-failed"
                & $Gcloud storage cp --quiet $log "$log.exit" "gs://$Bucket/cmd-out/" 2>&1 | Out-Null
                & $Gcloud storage rm --quiet $object 2>&1 | Out-Null
                continue
            }
            $script:QueueRun[$name] = $true
            New-Item -ItemType Directory -Force $Logs | Out-Null
            & $Gcloud storage rm --quiet $object 2>&1 | Out-Null   # Only now: it is here, whole, and will run once.
            Write-Host ("== remote command {0}/{1} ({2})" -f $Prefix, $name, (Get-Date -Format o))
            $hidden = if ($env:OS -eq "Windows_NT") { @{ WindowStyle = "Hidden" } } else { @{} }   # (tests: pwsh elsewhere)
            $process = Start-Process $Shell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$local`"" `
                -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru @hidden
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
    $pending = @($script:QueueTries.Keys | Where-Object { -not $script:QueueRun.ContainsKey($_) })
    Write-Host ("== $Prefix idle for $IdleMinutes minutes" + $(if ($pending) { "; never fetched: " + ($pending -join ", ") } else { "" }))
}
