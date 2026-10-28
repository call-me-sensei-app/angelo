# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Google Compute Engine startup script: provisions a throwaway Windows build machine and runs
  scripts\angelo\windows_validate.ps1 on it, unattended.

.DESCRIPTION
  Started by scripts/angelo/gcp_windows_validate.sh (windows-startup-script-ps1 metadata). It runs at
  every boot and is idempotent: each stage leaves a marker in C:\angelo\state, so the reboots a GPU
  driver or Visual Studio install may need just continue where it stopped. Everything it writes is
  also copied to gs://<angelo-bucket>/logs/ so progress is visible from outside; the final results go
  to gs://<angelo-bucket>/result/ and the last object written there is DONE.

  Metadata it reads: angelo-bucket (required), angelo-libs-sha (the pinned lib-windows_x64 commit),
  angelo-gpu (1 to install the NVIDIA GRID driver), angelo-stages, and angelo-hold-minutes (above zero: run remote
  commands from the bucket before powering off, windows_command_queue.ps1). The 'release' stage (the full-feature
  build, windows_release_validate.ps1) also installs the GPU toolkits found in gs://<angelo-bucket>/toolkits/
  (windows_toolkits.ps1: each installer's signature is checked first; angelo-cuda-components and angelo-hip-args
  override their installers' arguments), brings the libraries to
  angelo-libs-sha, fetches the agent runtime from gs://<angelo-bucket>/agent-runtime/ (put there by
  `gcp_windows_validate.sh agent-runtime`; checked against its SHA256SUMS) into C:\angelo\agent-runtime as
  ANGELO_AGENT_RUNTIME_DIR, and passes angelo-release-allow-missing (parts joined by +) to the build. It never reads or stores credentials: the VM's
  service account can only reach that one bucket. Not run on a real VM before its first use: read the
  logs rather than trusting a green status.
#>
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = "C:\angelo"
$state = Join-Path $root "state"
$logs = Join-Path $root "logs"

function Get-Meta([string]$Name, [string]$Default = "") {
    # A key that is not set reads as the default at once; any other failure (the metadata server busy after a
    # restart) is tried again before the default stands.
    for ($try = 1; $try -le 3; $try++) {
        try {
            return Invoke-RestMethod -TimeoutSec 5 -Headers @{ "Metadata-Flavor" = "Google" } `
                -Uri "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$Name"
        } catch {
            $response = $_.Exception.Response
            if ($response -and [int]$response.StatusCode -eq 404) { return $Default }
            Start-Sleep -Seconds 2
        }
    }
    $Default
}

# --- 0. Room on C: before anything writes (a full disk fails the transcript, the log upload and the source refresh).
# An A/B keeps, per arm label, a copy of the frozen app (bench\ab\candidate-<label>-candidate-msi-<sha12>) and, for an
# arm other than A, of the source tree (src-<label>, overlay-<label>, arm-<label>-overlay.tar.gz): caches its
# preparation makes again when missing. On a new send only (its nonce read and different from the last one's), every
# such copy goes except this send's own: its labels' source copies, and their app copies of this send's candidate
# (an app copy of an older candidate goes even under a label in use). Nothing else is touched: sign-ins
# (bench\connections), profiles, the frozen candidates (bench\candidate-*), every result folder (bench\ab\out*), state
# and logs; links are never followed. Each removal is logged with its size. Nothing here can end the script.
$hygiene = @()
function Get-FreeGb { try { [Math]::Round((Get-PSDrive C).Free / 1GB, 1) } catch { -1 } }
function Get-Bytes([string]$Path) {
    # robocopy lists without copying, past 260 characters and without following junctions (/XJ).
    $line = @(& robocopy.exe $Path "C:\angelo-nul-target" /L /S /NJH /NFL /NDL /BYTES /XJ /R:0 /W:0 |
        Where-Object { $_ -match "^\s*Bytes\s*:\s*(\d+)" })
    if ($line.Count -and $line[0] -match "^\s*Bytes\s*:\s*(\d+)") { [double]$Matches[1] } else { 0 }
}
$newSend = $false
try {
    $oldPreference = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $abRoot = Join-Path $root "bench\ab"
    $nonceNow = "$(Get-Meta 'angelo-src-nonce' '')".Trim()
    $lastNonce = "$(Get-Content -LiteralPath (Join-Path $state 'src.nonce') -ErrorAction SilentlyContinue)".Trim()
    $newSend = $nonceNow -and $nonceNow -ne $lastNonce   # Unread: not a new send (a reboot within one keeps everything).
    if ($newSend -and (Test-Path -LiteralPath $abRoot)) {
        $labels = @(@(Get-Meta "angelo-bench-ab-arm-a" "") + @("$(Get-Meta 'angelo-bench-ab-arms' '')".Split(" ") |
            ForEach-Object { $_.Split("=")[0] }) + @(Get-Meta "angelo-bench-ab-arm-b" "") | Where-Object { $_ })
        $sha = "$(Get-Meta 'angelo-bench-candidate-sha256' '')".Trim().ToLowerInvariant()
        $leaf = if ($sha.Length -ge 12) { "candidate-msi-" + $sha.Substring(0, 12) } else { "" }
        $keep = @()
        foreach ($label in $labels) {
            $keep += @("src-$label", "overlay-$label", "arm-$label-overlay.tar.gz")
            if ($leaf) { $keep += "candidate-$label-$leaf" }
        }
        $hygiene += "free before: $(Get-FreeGb) GB; this send's arms: $($labels -join ', '); candidate: $(if ($leaf) { $leaf } else { 'unknown' })"
        foreach ($item in @(Get-ChildItem -LiteralPath $abRoot -Force -ErrorAction SilentlyContinue)) {
            $name = $item.Name
            $copy = ($item.PSIsContainer -and $name -match "^(src|overlay|candidate)-.") -or
                    (-not $item.PSIsContainer -and $name -match "^arm-.+-overlay\.tar\.gz$")
            if (-not $copy -or @($keep | Where-Object { $_ -eq $name }).Count) { continue }   # -eq: case-insensitive.
            if (-not $leaf -and $name -like "candidate-*" -and
                @($labels | Where-Object { $name.StartsWith("candidate-$_-", [StringComparison]::OrdinalIgnoreCase) }).Count) {
                continue   # This send's candidate is not named: its labels' app copies stay.
            }
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $hygiene += "skipped (a link): $($item.FullName)"; continue }
            $bytes = if ($item.PSIsContainer) { Get-Bytes $item.FullName } else { $item.Length }
            try {
                if ($item.PSIsContainer) { [IO.Directory]::Delete("\\?\" + $item.FullName, $true) }   # Does not follow links.
                else { [IO.File]::Delete($item.FullName) }
            } catch {
                if ($item.PSIsContainer) { & cmd.exe /d /c "rd /s /q `"\\?\$($item.FullName)`"" 2>&1 | Out-Null }
            }
            $gone = -not (Test-Path -LiteralPath $item.FullName)
            $hygiene += ("{0} {1:N2} GB {2}" -f $(if ($gone) { "removed" } else { "NOT REMOVED" }), ($bytes / 1GB), $item.FullName)
        }
        $hygiene += "free after: $(Get-FreeGb) GB"
    }
    New-Item -ItemType Directory -Force $root, $state, $logs | Out-Null
    if ($hygiene.Count) { Set-Content (Join-Path $logs ("disk-hygiene-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))) $hygiene }
    Start-Transcript -Path (Join-Path $logs ("bootstrap-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))) -Append | Out-Null
} catch {
    $hygiene += "before the stages: $_"
} finally {
    $ErrorActionPreference = $oldPreference
}
$hygiene | ForEach-Object { Write-Host "disk: $_" }

# A validation or bench VM runs agents' scripts, so it never runs as a signing account (named *-signer@): that account's
# token reads the signing secrets (sign_windows.ps1 -Backend Esigner). The signer has its own bootstrap,
# windows_sign_bootstrap.ps1, which refuses every other account.
$account = try {
    Invoke-RestMethod -Headers @{ "Metadata-Flavor" = "Google" } -Uri "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
} catch { "" }
if ($account -match "^[^@]*-signer@") {
    try { Stop-Transcript | Out-Null } catch { }   # Not started when the disk step above failed.
    throw "this VM runs as $account, a signing account: a validation VM never does (use windows_sign_bootstrap.ps1 for the signer)"
}

$bucket = Get-Meta "angelo-bucket"
$libsSha = Get-Meta "angelo-libs-sha"
$wantGpu = (Get-Meta "angelo-gpu" "0") -eq "1"
if (-not $bucket) { throw "metadata angelo-bucket is missing" }

$gcloud = @("C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
            "C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $gcloud) { throw "the Cloud SDK is not installed on this image" }

function Sync-Logs {
    # Upload a copy: the transcript is still open, and native stderr must not abort the script.
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $tmp = Join-Path $root "logs-upload"
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory $tmp | Out-Null
        Get-ChildItem $logs -File | ForEach-Object { Copy-Item $_.FullName $tmp -ErrorAction SilentlyContinue }
        & $gcloud storage cp --quiet "$tmp\*" "gs://$bucket/logs/" 2>&1 | Out-Null
    } catch { } finally { $ErrorActionPreference = $old }
}
function Upload-Results {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $folder = Join-Path $root "results"
    if (Test-Path $folder) { & $gcloud storage cp --quiet --recursive "$folder\*" "gs://$bucket/result/" 2>&1 | Out-Null }
    $ErrorActionPreference = $old
}
function Stage-Done([string]$Name) { Test-Path (Join-Path $state "$Name.done") }
function Stage-Mark([string]$Name) { Set-Content (Join-Path $state "$Name.done") (Get-Date -Format o); Sync-Logs }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Run([string]$File, [string]$Arguments, [int[]]$Accept = @(0, 3010)) {
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -notin $Accept) { throw "$File exited with $($p.ExitCode)" }
    return $p.ExitCode
}

# Visual Studio's parts the builds need. clang-cl and its MSBuild toolset: the release builds with them, as official
# Blender (build_release_windows.ps1).
$vsParts = @("Microsoft.VisualStudio.Workload.VCTools", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "Microsoft.VisualStudio.Component.Windows11SDK.22621", "Microsoft.VisualStudio.Component.VC.Llvm.Clang",
             "Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset")
# The tools stage runs once, so a machine set up before a part joined $vsParts never got it, and the release stage
# refused that machine only after a send (a Windows build machine on 2026-10-24: set up before the release moved to clang-cl).
# Every boot adds what an installed Visual Studio 2022 lacks with its own installer. Nothing here ends the boot: a part
# still missing is logged, and the stage that needs it refuses with what to install (windows_release_toolchain.ps1).
function Add-MissingVsParts {
    $installer = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer"
    $vswhere = Join-Path $installer "vswhere.exe"; if (-not (Test-Path $vswhere)) { return }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $has = { param($Parts) @(& $vswhere -products * -version "[17.0,18.0)" -requires @Parts -property installationPath 2>$null) }
        foreach ($path in @(& $vswhere -products * -version "[17.0,18.0)" -property installationPath 2>$null)) {
            if ((& $has $vsParts) -contains $path) { continue }
            $missing = @($vsParts | Where-Object { -not ((& $has @($_)) -contains $path) })
            Write-Host "== Visual Studio at $path lacks $($missing -join ', '): adding them"
            $add = ($missing | ForEach-Object { "--add $_" }) -join " "
            $p = Start-Process -FilePath (Join-Path $installer "setup.exe") -PassThru -NoNewWindow `
                -ArgumentList "modify --installPath `"$path`" $add --quiet --norestart"
            $null = $p.Handle   # Windows PowerShell reports no exit code for a process whose handle was never read
            if (-not $p.WaitForExit(40 * 60 * 1000)) { $p.Kill(); Write-Host "the installer ran over 40 minutes: stopped" }
            Write-Host "installer exit $($p.ExitCode); every part present now: $((& $has $vsParts) -contains $path)"
            if ($p.ExitCode -eq 3010) { Write-Host "Visual Studio asked for a restart"; Sync-Logs; Restart-Computer -Force; exit 0 }
        }
    } catch { Write-Host "adding Visual Studio parts failed: $_" } finally { $ErrorActionPreference = $old }
}

# A background job keeps the bucket's copy of the logs fresh while the long stages run.
$syncer = Start-Job -ScriptBlock {
    param($gcloud, $logs, $bucket, $root)
    $ErrorActionPreference = "Continue"
    while ($true) {
        Start-Sleep 90
        $tmp = Join-Path $root "logs-upload-job"
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory $tmp | Out-Null
        Get-ChildItem $logs -File | ForEach-Object { Copy-Item $_.FullName $tmp -ErrorAction SilentlyContinue }
        & $gcloud storage cp --quiet "$tmp\*" "gs://$bucket/logs/" 2>&1 | Out-Null
    }
} -ArgumentList $gcloud, $logs, $bucket, $root

# A second job, never stopped by a stage, proves the run alive and moving to the Mac's wait (gcp_windows_validate.sh
# wait_off): every 5 minutes gs://<bucket>/live/HEARTBEAT holds the time, the last write in any folder a stage writes
# (progress; windows_final_runs.ps1's bench\state, bench\out, bench\campaign-<candidate> too, never a load sample, which a
# hung trial keeps writing) and the newest log's tail. A stage that hangs keeps the heartbeat but stops the progress.
$heartbeat = Start-Job -ScriptBlock {
    param($gcloud, $logs, $bucket, $root)
    $ErrorActionPreference = "Continue"
    $watched = @($logs, "state", "results", "download", "bench\download", "bench\ab\out", "bench\state", "bench\out",
                 "bench\campaign-*") | ForEach-Object { if ($_ -eq $logs) { $_ } else { Join-Path $root $_ } }
    while ($true) {
        try {
            $newest = $watched | ForEach-Object { Get-Item $_ -ErrorAction SilentlyContinue } |   # Wildcards resolved each pass.
                ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Recurse -File -ErrorAction SilentlyContinue } |
                Where-Object { $_.Name -notlike "*load.jsonl" } | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            $progress = if ($newest) { ([DateTimeOffset]$newest.LastWriteTimeUtc).ToUnixTimeSeconds() } else { 0 }
            $log = Get-ChildItem $logs -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending |
                Select-Object -First 1
            $tail = if ($log) { @(Get-Content $log.FullName -Tail 15 -ErrorAction SilentlyContinue) } else { @() }
            $file = Join-Path $root "HEARTBEAT"
            # Each field in parentheses: a comma binds tighter than +, which would make one line of them all.
            $free = [Math]::Round((Get-PSDrive C).Free / 1GB, 1)
            Set-Content $file (@(("at: " + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()), "progress: $progress", "free_gb: $free",
                                 ("newest: " + $(if ($newest) { $newest.FullName } else { "none" })),
                                 ("log: " + $(if ($log) { $log.Name } else { "none" })), "--") + $tail)
            & $gcloud storage cp --quiet $file "gs://$bucket/live/HEARTBEAT" 2>&1 | Out-Null
        } catch { }
        Start-Sleep 300
    }
} -ArgumentList $gcloud, $logs, $bucket, $root

try {
    # A send needs room: starting below angelo-min-free-gb (the sender sets it on every send; 30 by default) free on
    # C: it fails at once, naming the largest folders. Only a hold-only send (remote commands, no stage that builds or
    # runs) is never refused: it is how a full machine is cleaned by hand.
    $minFree = [double](Get-Meta "angelo-min-free-gb" "30")
    $working = @((Get-Meta "angelo-stages" "").Split("+") | Where-Object { $_ -and $_ -ne "none" })
    $needsRoom = -not ([int](Get-Meta "angelo-hold-minutes" "0") -gt 0 -and $working.Count -eq 0)
    if ($newSend -and $needsRoom -and (Get-FreeGb) -lt $minFree) {
        $largest = @(Get-ChildItem -LiteralPath $root, (Join-Path $root "bench"), (Join-Path $root "bench\ab") -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
            ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Bytes = (Get-Bytes $_.FullName) } } |
            Sort-Object Bytes -Descending | Select-Object -First 10 | ForEach-Object { "{0:N1} GB {1}" -f ($_.Bytes / 1GB), $_.Path })
        throw ("C: has $(Get-FreeGb) GB free, under the $minFree GB a send needs, after removing other sends' arm copies. " +
               "Largest folders: $($largest -join '; '). Free space by hand on a hold-only send " +
               "(ANGELO_GCP_STAGES=none ANGELO_GCP_HOLD_MINUTES=30 gcp_windows_validate.sh redo, then send a script), " +
               "or lower the floor with ANGELO_GCP_MIN_FREE_GB on the sender.")
    }
    # --- 1. GPU driver (NVIDIA GRID / RTX Virtual Workstation) ---------------------------------------
    if ($wantGpu -and -not (Stage-Done "driver")) {
        Write-Host "== stage: GPU driver"
        # A hidden stderr line under Stop would end the script (Windows PowerShell 5.1 makes it an error record).
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $name = & $gcloud storage ls "gs://nvidia-drivers-us-public/GRID/vGPU20.2/" 2>$null |
            Where-Object { $_ -match "grid_win10_win11_server2022.*international\.exe$" } | Select-Object -First 1
        $ErrorActionPreference = $old
        if (-not $name) { throw "no Windows GRID driver found in the public bucket" }
        $installer = Join-Path $root "grid-driver.exe"
        & $gcloud storage cp --quiet $name $installer
        $code = Run $installer "-s -noreboot -clean" @(0, 1, 3010)   # 1 = a restart is pending
        Stage-Mark "driver"
        Write-Host "driver installer exit code $code; restarting"
        Sync-Logs
        Restart-Computer -Force
        exit 0
    }

    # --- 2. Tools ------------------------------------------------------------------------------------
    if (-not (Stage-Done "tools")) {
        Write-Host "== stage: tools"
        Install-WindowsFeature NET-Framework-Core | Out-Null   # WiX 3 needs .NET 3.5
        Set-ExecutionPolicy Bypass -Scope Process -Force
        Invoke-Expression ((New-Object Net.WebClient).DownloadString("https://community.chocolatey.org/install.ps1"))
        Refresh-Path
        & choco install -y --no-progress git git-lfs 7zip wixtoolset
        & choco install -y --no-progress cmake --installargs "ADD_CMAKE_TO_PATH=System"
        $wix = Get-ChildItem "C:\Program Files (x86)" -Directory -Filter "WiX Toolset*" | Select-Object -First 1
        if ($wix) {
            $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
            [Environment]::SetEnvironmentVariable("Path", "$machine;$($wix.FullName)\bin", "Machine")
        }
        Refresh-Path
        git lfs install --system | Out-Null
        $vs = Join-Path $root "vs_BuildTools.exe"
        Invoke-WebRequest "https://aka.ms/vs/17/release/vs_BuildTools.exe" -OutFile $vs
        $code = Run $vs ("--quiet --wait --norestart --nocache --includeRecommended " + (($vsParts | ForEach-Object { "--add $_" }) -join " "))
        Stage-Mark "tools"
        if ($code -eq 3010) { Write-Host "Visual Studio asked for a restart"; Sync-Logs; Restart-Computer -Force; exit 0 }
    }
    Add-MissingVsParts
    Refresh-Path

    # --- 3. Source (sent from the Mac: nothing was pushed) and libraries ----------------------------------------
    # A send is the folder gs://<bucket>/input/send-<nonce>/ that metadata angelo-src-nonce names, complete once its
    # send.json is up: the archives, the commit's objects and the tools that receive them (windows_source_send.ps1,
    # source_send.py, source_tree.py). They unpack with exact UTF-8 names (Windows tar.exe garbled non-ASCII ones),
    # install the commit, repair and clean the tree, and prove it is the commit by release_checks.py source-clean's rule;
    # only then is gs://<bucket>/input/confirmed-commit, the launcher's one delta base, set to it. A send cut off on
    # either side leaves that marker as it was; a tree that is not provably its commit clears it (the next send is all
    # of HEAD). Every boot proves the confirmed commit again and publishes it, and every stage below refuses a tree
    # that is not the commit sent (Assert-SentTree).
    $src = Join-Path $root "src"
    $tools = Join-Path $root "source-tools"   # the receiving tools of the last send applied
    $nonceFile = Join-Path $state "src.nonce"
    $wantNonce = Get-Meta "angelo-src-nonce" ""
    $sentCommit = Get-Meta "angelo-src-commit" ""   # what this send says it is; proven below, never trusted as such
    $haveNonce = if (Test-Path $nonceFile) { (Get-Content $nonceFile).Trim() } else { "" }
    if ($wantNonce -and $haveNonce -ne $wantNonce) {
        Write-Host "== stage: receive source (send $wantNonce)"
        $folder = Join-Path $root "download\send-$wantNonce"
        Remove-Item $folder -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $folder, $src | Out-Null
        & $gcloud storage cp --quiet --recursive "gs://$bucket/input/send-$wantNonce/*" $folder
        if (-not (Test-Path (Join-Path $folder "send.json"))) { throw "send $wantNonce is not complete in gs://$bucket/input/ (cut off?): send again" }
        . (Join-Path $folder "windows_source_send.ps1")
        Receive-Send $folder $src $gcloud $bucket $sentCommit
        Remove-Item $tools -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $tools | Out-Null
        Copy-Item (Join-Path $folder "*.py"), (Join-Path $folder "*.ps1") $tools
        Remove-Item (Join-Path $state "validate.done"), (Join-Path $state "gui.done"), (Join-Path $state "gui-prep.done"), (Join-Path $state "hold.done"), (Join-Path $state "bench.done"), (Join-Path $state "validate.exit"), (Join-Path $root "DONE"), (Join-Path $root "FAILED") -ErrorAction SilentlyContinue
        Set-Content $nonceFile $wantNonce
    } else {
        if (-not (Test-Path (Join-Path $tools "windows_source_send.ps1"))) { throw "no source send has been received here: send one (redo)" }
        . (Join-Path $tools "windows_source_send.ps1")
        Publish-Confirmed $src $gcloud $bucket
    }
    $libs = Join-Path $src "lib\windows_x64"
    if (-not (Test-Path (Join-Path $libs ".git"))) {
        Write-Host "== stage: libraries"
        Remove-Item -Recurse -Force $libs -ErrorAction SilentlyContinue
        $env:GIT_LFS_SKIP_SMUDGE = "1"
        git clone -q https://projects.blender.org/blender/lib-windows_x64.git $libs
        if ($libsSha) { git -C $libs checkout -q $libsSha }
        $env:GIT_LFS_SKIP_SMUDGE = ""
        git -C $libs lfs install --local --skip-repo | Out-Null
        git -C $libs lfs pull
        if ($LASTEXITCODE -ne 0) { throw "git lfs pull of the Windows libraries failed" }
    }
    # Before anything reads the tree (toolkit installer script, release inputs, stages). Every check that names the
    # commit under test asks Get-SentCommit, which proves the tree first; no environment variable carries it.
    Assert-SentTree $src
    # The stages below that need the sent tree are in it (windows_bootstrap_stages.ps1); a stage that needs a restart
    # says so and the restart happens here, in the startup script itself.
    . (Join-Path $src "scripts\angelo\windows_bootstrap_stages.ps1")
    function Invoke-StageStep([string]$Stage) {
        $reboot = @{ Now = $false }
        & $Stage $reboot
        if ($reboot.Now) { Sync-Logs; Restart-Computer -Force; exit 0 }
    }

    # --- 3b. The full-feature build's GPU toolkits and pinned libraries (the 'release' stage only) -----------
    # Installers come from gs://<bucket>/toolkits/, put there by `gcp_windows_validate.sh toolkits` after someone
    # downloaded them and accepted their licenses: the CUDA Toolkit (cuda_<version>_..._windows.exe), the OptiX SDK
    # (a .zip holding include\optix.h, or NVIDIA's installer) and the AMD HIP SDK. A toolkit not supplied is not
    # installed; build_release_windows.ps1 then stops in seconds unless the run allows it to be missing.
    $releaseRun = (Get-Meta "angelo-stages" "identity+teams").Split("+") -contains "release"
    Invoke-StageStep Invoke-ToolkitStage
    if ($releaseRun) {
        # The libraries at the commit this source pins and the agent runtime the build bundles (in this scope).
        . (Join-Path $src "scripts\angelo\windows_release_inputs.ps1")
    }

    # --- 4. Build, package, install, check -----------------------------------------------------------
    if (-not (Stage-Done "validate")) {
        Write-Host "== stage: validate"
        Assert-SentTree $src
        $results = Join-Path $root "results"
        # Optional: an official stock Blender installer (URL from metadata) for the side-by-side checks.
        $stockArgs = @()
        $stockUrl = Get-Meta "angelo-stock-blender-url"
        if ($stockUrl) {
            $stock = Join-Path $root ("download\" + (Split-Path $stockUrl -Leaf))
            if (-not (Test-Path $stock)) { Invoke-WebRequest $stockUrl -OutFile $stock }
            $stockArgs = @("-StockBlenderMsi", $stock)
        }
        # Which checks run: "identity" (build, installer, side by side) and/or "teams" (the team account client).
        $stages = (Get-Meta "angelo-stages" "identity+teams").Split("+")
        Remove-Item $results -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $results | Out-Null
        $code = 0
        # Every check script runs with its output merged (*>&1): a stderr line of one of its programs would be an error
        # record, which under this script's Stop preference ends the stage early. Each runs under Continue; its exit
        # code decides.
        if ($stages -contains "identity") {
            $ErrorActionPreference = "Continue"
            try {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_validate.ps1") `
                    -SourceDir $src -SkipLibs -MakeArgs 2022b -ResultsDir $results @stockArgs *>&1 | Tee-Object (Join-Path $logs "validate.log")
                if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
            } catch { Write-Host "validate failed: $_"; $code = 1 }
            finally { $ErrorActionPreference = "Stop" }
        }
        if ($stages -contains "teams") {
            $ErrorActionPreference = "Continue"
            try {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_teams_validate.ps1") `
                    -SourceDir $src -ResultsDir $results *>&1 | Tee-Object (Join-Path $logs "teams.log")
                if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
            } catch { Write-Host "teams checks failed: $_"; $code = 1 }
            finally { $ErrorActionPreference = "Stop" }
        }
        if ($stages -contains "agent") {
            # The agent's Windows checks without a desktop: host suite, storage suites, NTFS, BACKGROUND native suites.
            $agentArgs = @("-SourceDir", $src, "-ResultsDir", (Join-Path $results "agent"))
            if ($stages -contains "identity") { $agentArgs += "-SkipBuild" }   # windows_validate.ps1 just built it
            # Its programs write to stderr; under this script's Stop preference that would end the stage early.
            $ErrorActionPreference = "Continue"
            try {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_agent_validate.ps1") `
                    @agentArgs *>&1 | Tee-Object (Join-Path $logs "agent.log")
                if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
            } catch { Write-Host "agent checks failed: $_"; $code = 1 }
            finally { $ErrorActionPreference = "Stop" }
        }
        if ($stages -contains "release") {
            # The full-feature build (blender_release, every GPU device), its MSI and the BACKGROUND native suites on it.
            $ErrorActionPreference = "Continue"
            # -AllowMissing only when set: PowerShell drops an empty argument to a program, and the script's parameter
            # then has no value (the first full-feature run stopped there before building).
            $releaseArgs = @("-SourceDir", $src, "-ResultsDir", (Join-Path $results "release"))
            $allowMissing = Get-Meta "angelo-release-allow-missing" ""
            if ($allowMissing) { $releaseArgs += @("-AllowMissing", $allowMissing) }
            try {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_release_validate.ps1") `
                    @releaseArgs *>&1 | Tee-Object (Join-Path $logs "release.log")
                if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
            } catch { Write-Host "release stage failed: $_"; $code = 1 }
            finally { $ErrorActionPreference = "Stop" }
        }
        if ($stages -contains "release") { Upload-Results }   # release-results.json now: wait-off stops on a FAIL
        if ($stages -contains "thumbs") {
            $ErrorActionPreference = "Continue"
            try {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_thumbnailer_build.ps1") `
                    -SourceDir $src -ResultsDir $results *>&1 | Tee-Object (Join-Path $logs "thumbs.log")
                if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
            } catch { Write-Host "thumbnailer build failed: $_"; $code = 1 }
            finally { $ErrorActionPreference = "Stop" }
        }
        Set-Content (Join-Path $state "validate.exit") $code
        Report-Leftovers $src "validate"
        Stage-Mark "validate"
    }

    # --- 4a. Remote commands before the desktop stage (validation work; windows_command_queue.ps1) ----------
    $holdMinutes = [int](Get-Meta "angelo-hold-minutes" "0")
    if ($holdMinutes -gt 0) { . (Join-Path $src "scripts\angelo\windows_command_queue.ps1") }
    if ($holdMinutes -gt 0 -and -not (Stage-Done "hold")) {
        Upload-Results
        Write-Host "== stage: hold (remote commands from gs://$bucket/cmd/, released by cmd-release or $holdMinutes idle minutes)"
        Wait-QueuedCommands $gcloud $bucket "cmd" $logs (Join-Path $root "cmd") $holdMinutes
        Stage-Mark "hold"
    }

    # --- 4b. Checks that need a signed-in desktop (windows_bootstrap_stages.ps1) ------------------------------
    Invoke-StageStep Invoke-DesktopStage

    # --- 4c. Final measurement runs (windows_bootstrap_stages.ps1, scripts\angelo\windows_final_runs.ps1) ------------
    Invoke-StageStep Invoke-BenchStage

    # --- 5. Hand the results back --------------------------------------------------------------------
    Write-Host "== stage: results"
    Upload-Results
    if ($wantGpu) {
        try { (& "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe") | Set-Content (Join-Path $logs "nvidia-smi.txt") } catch { }
    }
    Sync-Logs
    $exitCode = if (Test-Path (Join-Path $state "validate.exit")) { Get-Content (Join-Path $state "validate.exit") } else { "unknown" }
    Set-Content (Join-Path $root "DONE") "validate exit code: $exitCode"
    & $gcloud storage cp --quiet (Join-Path $root "DONE") "gs://$bucket/result/DONE"
} catch {
    Write-Host "BOOTSTRAP FAILED: $_"
    Write-Host $_.ScriptStackTrace
    Sync-Logs
    $failedText = "$_"
    $ErrorActionPreference = "Continue"   # On a full disk the file may not be written: the text goes up from memory.
    Set-Content (Join-Path $root "FAILED") $failedText -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath (Join-Path $root "FAILED")) {
        & $gcloud storage cp --quiet (Join-Path $root "FAILED") "gs://$bucket/result/FAILED" 2>&1 | Out-Null
    } else {
        $failedText | & $gcloud storage cp --quiet - "gs://$bucket/result/FAILED" 2>&1 | Out-Null
    }
    $failedRun = $true
} finally {
    Stop-Job $syncer -ErrorAction SilentlyContinue
    Stop-Job $heartbeat -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
    Sync-Logs
}
# A finished (or failed) unattended run powers the machine off: a stopped VM costs only its disk.
if ((Get-Meta "angelo-stop-when-done" "0") -eq "1" -and ((Test-Path (Join-Path $root "DONE")) -or (Test-Path (Join-Path $root "FAILED")) -or $failedRun)) {
    Stop-Computer -Force
}
