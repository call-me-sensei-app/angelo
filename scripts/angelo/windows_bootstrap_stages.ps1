# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The validation and bench machines' stages that run from the sent tree (dot-sourced by windows_cloud_bootstrap.ps1).

.DESCRIPTION
  The startup script dot-sources this file once the tree is proven to be the sent commit (Assert-SentTree), so these
  stages always come from the commit under test. Each reads the bootstrap's own variables and helpers ($src, $root,
  $state, $logs, $bucket, $gcloud, $holdMinutes, $syncer; Get-Meta, Stage-Done, Stage-Mark, Sync-Logs, ...). A stage
  never restarts the machine itself: it sets $Reboot.Now and returns, and the startup script restarts (an exit here
  would end only this file).
  Defines the functions and nothing else.
#>

# 3b. The full-feature build's GPU toolkits (the 'release' stage only; see the bootstrap's 3b).
function Invoke-ToolkitStage([hashtable]$Reboot) {
    if ($releaseRun) {
        # Installers added or replaced in the bucket since the last install are installed again.
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $kitList = ((& $gcloud storage ls -l "gs://$bucket/toolkits/" 2>$null) | Sort-Object) -join "`n"
        $ErrorActionPreference = $old
        $kitFile = Join-Path $state "toolkits.list"
        $haveKits = if (Test-Path $kitFile) { (Get-Content $kitFile -Raw) } else { "" }
        if ("$haveKits".Trim() -ne $kitList.Trim()) {
            Remove-Item (Join-Path $state "toolkits.done") -ErrorAction SilentlyContinue
            Set-Content $kitFile $kitList
        }
    }
    if ($releaseRun -and -not (Stage-Done "toolkits")) {
        Write-Host "== stage: GPU toolkits for the full-feature build"
        $kits = Join-Path $root "toolkits"
        $kitDownload = Join-Path $kits "download"
        New-Item -ItemType Directory -Force $kitDownload | Out-Null
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & $gcloud storage cp --quiet "gs://$bucket/toolkits/*" $kitDownload 2>&1 | Out-Host
        $ErrorActionPreference = $old
        # windows_toolkits.ps1 checks each installer's Authenticode signature (NVIDIA Corporation, Advanced Micro
        # Devices) and refuses anything else; it never installs a display driver (the GRID driver stays).
        # The overrides travel in the environment: PowerShell drops an empty argument to a program and reads "-install"
        # after -File as a parameter name.
        $env:ANGELO_CUDA_COMPONENTS = Get-Meta "angelo-cuda-components" ""
        $env:ANGELO_HIP_ARGS = Get-Meta "angelo-hip-args" ""
        $ErrorActionPreference = "Continue"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_toolkits.ps1") `
            -Download $kitDownload -Kits $kits *>&1 | Tee-Object (Join-Path $logs "toolkits.log")
        $kitExit = $LASTEXITCODE
        $ErrorActionPreference = "Stop"
        if ($kitExit -eq 1) {
            # Not marked done: the next run tries again. The build then stops in seconds for the missing toolkit.
            Write-Host "toolkits: an installer was refused or failed (toolkits.log)"
        } else {
            Stage-Mark "toolkits"
        }
        if ($kitExit -eq 3010) { Write-Host "a toolkit asked for a restart"; $Reboot.Now = $true; return }
    }
}

# 4b. Checks that need a signed-in desktop.
function Invoke-DesktopStage([hashtable]$Reboot) {
    # A throwaway local user signs in automatically after one reboot; a Run-key entry at its sign-in starts
    # windows_gui_checks.ps1 in that interactive session (default browser, Angelo's window, screenshots). The user,
    # the automatic sign-in and the task are removed again afterwards.
    $stagesGui = (Get-Meta "angelo-stages" "identity+teams").Split("+")
    $guiDir = Join-Path $root "results\gui"
    if ($stagesGui -contains "gui" -and -not (Stage-Done "gui")) {
      try {
        $guiUser = "angeloqa"
        $winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
        if (-not (Stage-Done "gui-prep")) {
            Write-Host "== stage: desktop session setup"
            Assert-SentTree $src
            Add-Type -AssemblyName System.Web
            $pw = [System.Web.Security.Membership]::GeneratePassword(20, 4) + "aA1!"
            Remove-LocalUser -Name $guiUser -ErrorAction SilentlyContinue
            # Earlier throwaway users' profiles (their sign-ins, browser data and credential vaults) go too: removing an
            # account keeps its profile, and a machine image must carry none.
            Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "C:\Users\$guiUser*" -and -not $_.Loaded } |
                Remove-CimInstance -ErrorAction SilentlyContinue
            New-LocalUser -Name $guiUser -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null
            Add-LocalGroupMember -Group "Administrators" -Member $guiUser
            Set-ItemProperty $winlogon AutoAdminLogon "1"
            Set-ItemProperty $winlogon DefaultUserName $guiUser
            Set-ItemProperty $winlogon DefaultDomainName $env:COMPUTERNAME
            Set-ItemProperty $winlogon DefaultPassword $pw
            Remove-Variable pw
            # Quiet first run for the default browser and no Server Manager window over the screenshots.
            $edge = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"
            if (-not (Test-Path $edge)) { New-Item $edge | Out-Null }   # no -Force: on a registry key it tries to delete a subkey tree
            foreach ($name in "HideFirstRunExperience", "BrowserSignin", "DefaultBrowserSettingEnabled", "SyncDisabled", "StartupBoostEnabled") {
                Set-ItemProperty $edge $name 1 -Type DWord
            }
            Set-ItemProperty $edge BrowserSignin 0 -Type DWord
            Set-ItemProperty $edge DefaultBrowserSettingEnabled 0 -Type DWord
            if (-not (Test-Path "HKLM:\SOFTWARE\Microsoft\ServerManager")) { New-Item "HKLM:\SOFTWARE\Microsoft\ServerManager" | Out-Null }
            Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\ServerManager" DoNotOpenServerManagerAtLogon 1 -Type DWord
            Remove-Item $guiDir -Recurse -Force -ErrorAction SilentlyContinue
            # Started from the Run key, the checks run as the user's own sign-in (the normal logon token, as Explorer's
            # children do). With User Account Control off that token is an administrator's, which the policy checks need.
            $system = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
            Set-ItemProperty $system EnableLUA 0 -Type DWord
            $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -SourceDir "{1}" -ResultsDir "{2}"' -f (Join-Path $src "scripts\angelo\windows_gui_checks.ps1"), $src, $guiDir
            Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" AngeloGuiChecks $command
            Stage-Mark "gui-prep"
            Write-Host "restarting so the test user signs in"
            $Reboot.Now = $true
            return
        }
        Write-Host "== stage: desktop session checks (waiting for the signed-in task)"
        # The agent's desktop checks run the GUI native suites; a held session also waits for its remote commands.
        $minutes = 35 + $(if ($stagesGui -contains "agent") { 150 } else { 0 }) + $(if ($holdMinutes -gt 0) { 4 * $holdMinutes } else { 0 })
        $deadline = (Get-Date).AddMinutes($minutes)
        while (-not (Test-Path (Join-Path $guiDir "DONE")) -and (Get-Date) -lt $deadline) {
            if ($holdMinutes -gt 0) { [void](Invoke-QueuedCommands $gcloud $bucket "cmd" $logs (Join-Path $root "cmd")) }
            Start-Sleep -Seconds 10
        }
        Write-Host ("desktop session checks: " + $(if (Test-Path (Join-Path $guiDir "DONE")) { Get-Content (Join-Path $guiDir "DONE") } else { "TIMED OUT" }))
        # Take the test user, the automatic sign-in and the task away again.
        Set-ItemProperty $winlogon AutoAdminLogon "0"
        Remove-ItemProperty $winlogon DefaultPassword -ErrorAction SilentlyContinue
        Remove-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" AngeloGuiChecks -ErrorAction SilentlyContinue
        Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" EnableLUA 1 -Type DWord
        Remove-LocalUser -Name $guiUser -ErrorAction SilentlyContinue
        Report-Leftovers $src "desktop session"
        Stage-Mark "gui"
      } catch {
        # The desktop-session checks are an extra: a failure here must not lose the results of the others.
        Write-Host "desktop session stage failed: $_"
        Write-Host $_.ScriptStackTrace
        Stage-Mark "gui"
      }
    }
}

# 4c. Final measurement runs (doc/angelo/final_runs.rst).
function Invoke-BenchStage([hashtable]$Reboot) {
    # Mode from metadata angelo-bench-mode: prep (after the release stage: install, freeze, wait for the RDP sign-in), dry (unbilled)
    # or run (billed; only with `gcp_windows_validate.sh bench run --authorize-billed-runs`). Exit code 194: the
    # desktop session it needs starts after a reboot; the stage then waits for that session at the next boot.
    $stagesBench = (Get-Meta "angelo-stages" "identity+teams").Split("+")
    if ($stagesBench -contains "bench" -and -not (Stage-Done "bench")) {
        Write-Host "== stage: bench"
        Assert-SentTree $src
        # The periodic log upload would compete with the timed trials; the logs go up when the stage ends.
        Stop-Job $syncer -ErrorAction SilentlyContinue
        $benchExit = 1
        # The preparation before the desktop session (tools, CLIs, the candidate, the arms) has its own deadline:
        # until bench\state\session-<nonce> exists the stage runs at most angelo-bench-prep-minutes (a prep's
        # sign-in wait added), then it is ended and the run fails, saying so, instead of hanging until the machine's
        # maximum run time. Its output goes to this boot's bench-stage-*.out as it runs (the newest log: the heartbeat
        # reads its tail) and is added to bench.log when the pass ends.
        $benchLog = Join-Path $logs "bench.log"
        $prepared = Join-Path $root ("bench\state\session-" + (Get-Meta "angelo-src-nonce" "0"))
        $prepMinutes = [int](Get-Meta "angelo-bench-prep-minutes" "90")
        if ((Get-Meta "angelo-bench-mode" "") -eq "prep") { $prepMinutes += [int](Get-Meta "angelo-bench-signin-minutes" "60") }
        $prepDeadline = (Get-Date).AddMinutes($prepMinutes)
        $pass = Get-Date -Format "yyyyMMdd-HHmmss"
        $stageOut, $stageErr = (Join-Path $logs "bench-stage-$pass.out"), (Join-Path $logs "bench-stage-$pass.err")
        $ErrorActionPreference = "Continue"   # Its programs write to stderr.
        try {
            $stageArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Stage -SourceDir "{1}" -Bucket "{2}" -Gcloud "{3}"' -f `
                (Join-Path $src "scripts\angelo\windows_final_runs.ps1"), $src, $bucket, $gcloud
            $process = Start-Process powershell.exe -ArgumentList $stageArgs -PassThru -NoNewWindow `
                -RedirectStandardOutput $stageOut -RedirectStandardError $stageErr
            $null = $process.Handle   # Keeps the exit code readable after it ends.
            $hung = $false
            $walking = Join-Path $root ("bench\state\granting-" + (Get-Meta "angelo-src-nonce" "0"))
            while (-not $process.HasExited) {
                # The bench's one-time rights walk lists every item as it goes (the heartbeat's progress): the deadline
                # waits for it.
                if (Test-Path $walking) { $prepDeadline = (Get-Date).AddMinutes($prepMinutes) }
                if ((Get-Date) -gt $prepDeadline -and -not (Test-Path $prepared)) { $hung = $true; break }
                Start-Sleep -Seconds 15
            }
            if ($hung) {
                & taskkill.exe /T /F /PID $process.Id 2>&1 | Out-Null
                Get-Content $stageOut, $stageErr -ErrorAction SilentlyContinue | Add-Content $benchLog
                $last = (Get-Content $stageOut -Tail 8 -ErrorAction SilentlyContinue) -join " | "
                throw "bench preparation did not finish within $prepMinutes minutes (ended): $last"
            }
            $process.WaitForExit()
            Get-Content $stageOut, $stageErr -ErrorAction SilentlyContinue | Add-Content $benchLog
            $benchExit = $process.ExitCode
        } catch {
            if ("$_" -like "bench preparation did not finish*") { throw }   # The run fails (result/FAILED says why).
            Write-Host "bench stage failed: $_"
        }
        finally { $ErrorActionPreference = "Stop" }
        if ($benchExit -eq 194) { $Reboot.Now = $true; return }
        if ($benchExit -ne 0) { Set-Content (Join-Path $state "validate.exit") $benchExit }   # Keeps an earlier stage's failure.
        Report-Leftovers $src "bench"
        Stage-Mark "bench"
    }
}
