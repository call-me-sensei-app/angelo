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
New-Item -ItemType Directory -Force $root, $state, $logs | Out-Null
Start-Transcript -Path (Join-Path $logs ("bootstrap-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))) -Append | Out-Null

function Get-Meta([string]$Name, [string]$Default = "") {
    try {
        Invoke-RestMethod -Headers @{ "Metadata-Flavor" = "Google" } `
            -Uri "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$Name"
    } catch { $Default }
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

try {
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
        $code = Run $vs ("--quiet --wait --norestart --nocache --includeRecommended " +
            "--add Microsoft.VisualStudio.Workload.VCTools " +
            "--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 " +
            "--add Microsoft.VisualStudio.Component.Windows11SDK.22621")
        Stage-Mark "tools"
        if ($code -eq 3010) { Write-Host "Visual Studio asked for a restart"; Sync-Logs; Restart-Computer -Force; exit 0 }
    }
    Refresh-Path

    # --- 3. Source (sent from the Mac as an archive: nothing was pushed) and libraries ----------------
    $src = Join-Path $root "src"
    if (-not (Stage-Done "source")) {
        Write-Host "== stage: source and libraries"
        $dl = Join-Path $root "download"
        New-Item -ItemType Directory -Force $dl, $src | Out-Null
        & $gcloud storage cp --quiet "gs://$bucket/input/angelo-src.tar.gz" "gs://$bucket/input/angelo-lfs.tar.gz" $dl
        & tar -xzf (Join-Path $dl "angelo-src.tar.gz") -C $src
        & tar -xzf (Join-Path $dl "angelo-lfs.tar.gz") -C $src       # real files over the LFS pointers
        git -C $src init -q                                           # make.bat expects a git checkout
        $libs = Join-Path $src "lib\windows_x64"
        Remove-Item -Recurse -Force $libs -ErrorAction SilentlyContinue
        $env:GIT_LFS_SKIP_SMUDGE = "1"
        git clone -q https://projects.blender.org/blender/lib-windows_x64.git $libs
        if ($libsSha) { git -C $libs checkout -q $libsSha }
        $env:GIT_LFS_SKIP_SMUDGE = ""
        git -C $libs lfs install --local --skip-repo | Out-Null
        git -C $libs lfs pull
        if ($LASTEXITCODE -ne 0) { throw "git lfs pull of the Windows libraries failed" }
        Set-Content (Join-Path $state "src.nonce") (Get-Meta "angelo-src-nonce" "0")
        Set-Content (Join-Path $state "src.commit") (Get-Meta "angelo-src-commit" "")
        Stage-Mark "source"
    }

    # A newer archive of the source (the orchestrator's `redo`): extract it over the tree (the libraries
    # and any build folder stay) and run the validate stage again.
    $nonceFile = Join-Path $state "src.nonce"
    $wantNonce = Get-Meta "angelo-src-nonce" "0"
    $haveNonce = if (Test-Path $nonceFile) { (Get-Content $nonceFile).Trim() } else { "0" }   # first boots wrote none
    if ($haveNonce -ne $wantNonce) {
        Write-Host "== stage: refresh source ($wantNonce)"
        $dl = Join-Path $root "download"
        $commitFile = Join-Path $state "src.commit"
        $haveCommit = if (Test-Path $commitFile) { (Get-Content $commitFile).Trim() } else { "" }
        $mode = Get-Meta "angelo-src-mode" "full"
        if ($mode -eq "delta" -and $haveCommit -and $haveCommit -eq (Get-Meta "angelo-src-base" "")) {
            # Only the files that changed since the commit this tree holds: unchanged files keep their times,
            # so the incremental build recompiles just what depends on the changed ones.
            & $gcloud storage cp --quiet "gs://$bucket/input/angelo-delta.tar.gz" "gs://$bucket/input/angelo-deleted.txt" $dl
            & tar -xzf (Join-Path $dl "angelo-delta.tar.gz") -C $src
            foreach ($gone in (Get-Content (Join-Path $dl "angelo-deleted.txt"))) {
                if ($gone.Trim()) { Remove-Item (Join-Path $src $gone.Trim()) -Force -ErrorAction SilentlyContinue }
            }
            Write-Host "refresh: delta from $haveCommit"
            # The large files the build needs travel separately; extract them again only when they changed.
            $lfsFile = Join-Path $state "lfs.digest"
            $haveLfs = if (Test-Path $lfsFile) { (Get-Content $lfsFile).Trim() } else { "" }
            $wantLfs = Get-Meta "angelo-lfs-digest" ""
            if ($wantLfs -and $wantLfs -ne $haveLfs) {
                & $gcloud storage cp --quiet "gs://$bucket/input/angelo-lfs.tar.gz" $dl
                & tar -xzf (Join-Path $dl "angelo-lfs.tar.gz") -C $src
                Write-Host "refresh: large files extracted again (digest $wantLfs, had '$haveLfs')"
            }
        } else {
            Write-Host "refresh: all of HEAD (mode '$mode', tree holds '$haveCommit', delta base '$(Get-Meta 'angelo-src-base' '')')"
            & $gcloud storage cp --quiet "gs://$bucket/input/angelo-src.tar.gz" "gs://$bucket/input/angelo-lfs.tar.gz" $dl
            & tar -xzf (Join-Path $dl "angelo-src.tar.gz") -C $src
            & tar -xzf (Join-Path $dl "angelo-lfs.tar.gz") -C $src
        }
        Set-Content $commitFile (Get-Meta "angelo-src-commit" "")
        Set-Content (Join-Path $state "lfs.digest") (Get-Meta "angelo-lfs-digest" "")
        Remove-Item (Join-Path $state "validate.done"), (Join-Path $state "gui.done"), (Join-Path $state "gui-prep.done"), (Join-Path $state "hold.done"), (Join-Path $state "bench.done"), (Join-Path $state "validate.exit"), (Join-Path $root "DONE"), (Join-Path $root "FAILED") -ErrorAction SilentlyContinue
        Set-Content $nonceFile $wantNonce
    }

    # --- 3a. The commit itself, for a release build's build info -----------------------------------------------
    # The source is an archive in an empty repository, where Blender's build info has no hash to read (and a
    # release build refuses to package without it). release-build also sends the commit object and its tree (no
    # history): installed as a shallow commit whose HEAD is exactly that commit, over the extracted files, it gives
    # buildinfo.cmake the true hash, branch and date.
    if ((Get-Meta "angelo-src-pack" "0") -eq "1") {
        $wantHead = Get-Meta "angelo-src-commit" ""
        $headFile = Join-Path $state "git.head"
        $haveHead = if (Test-Path $headFile) { (Get-Content $headFile).Trim() } else { "" }
        if ($wantHead -and $haveHead -ne $wantHead) {
            $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            try {
                $pack = Join-Path $root "download\angelo-commit.pack"
                & $gcloud storage cp --quiet "gs://$bucket/input/angelo-commit.pack" $pack 2>&1 | Out-Null
                & cmd.exe /d /c "git -C `"$src`" index-pack --stdin < `"$pack`"" 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "the commit pack could not be indexed" }
                [System.IO.File]::WriteAllText((Join-Path $src ".git\shallow"), "$wantHead`n")
                $branch = Get-Meta "angelo-src-branch" "HEAD"
                if ($branch -and $branch -ne "HEAD") {
                    git -C $src update-ref "refs/heads/$branch" $wantHead 2>&1 | Out-Null
                    git -C $src symbolic-ref HEAD "refs/heads/$branch" 2>&1 | Out-Null
                } else {
                    git -C $src update-ref --no-deref HEAD $wantHead 2>&1 | Out-Null
                }
                git -C $src read-tree $wantHead 2>&1 | Out-Null
                # Files the archive could not create here (tar.exe drops names outside the system code page, such as
                # Blender's Cyrillic and CJK test files) come from the commit itself; existing files are kept.
                # Otherwise the tree differs from its commit: About says "(modified)" and final_runs.py run refuses it.
                $env:GIT_LFS_SKIP_SMUDGE = "1"
                git -C $src checkout-index --all 2>&1 | Out-Null
                $differ = @(git -C $src status --porcelain --untracked-files=no 2>$null).Count
                $head = "$(git -C $src rev-parse HEAD 2>$null)".Trim()
                if ($head -ne $wantHead) { throw "the checkout's HEAD is '$head', not $wantHead" }
                Set-Content $headFile $wantHead
                Write-Host "source: git commit $wantHead on $branch (shallow: the commit and its tree); $differ files differ from it"
            } finally { $ErrorActionPreference = $old }
        }
    }

    # --- 3b. The full-feature build's GPU toolkits and pinned libraries (the 'release' stage only) -----------
    # Installers come from gs://<bucket>/toolkits/, put there by `gcp_windows_validate.sh toolkits` after someone
    # downloaded them and accepted their licenses: the CUDA Toolkit (cuda_<version>_..._windows.exe), the OptiX SDK
    # (a .zip holding include\optix.h, or NVIDIA's installer) and the AMD HIP SDK. A toolkit not supplied is not
    # installed; build_release_windows.ps1 then stops in seconds unless the run allows it to be missing.
    $releaseRun = (Get-Meta "angelo-stages" "identity+teams").Split("+") -contains "release"
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
        if ($kitExit -eq 3010) { Write-Host "a toolkit asked for a restart"; Sync-Logs; Restart-Computer -Force; exit 0 }
    }
    if ($releaseRun) {
        # The libraries at the commit this source pins (the source stage cloned them once, at the pin of that time).
        $libs = Join-Path $src "lib\windows_x64"
        $want = Get-Meta "angelo-libs-sha" ""
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $have = (git -C $libs rev-parse HEAD 2>$null)
        if ($want -and $have -ne $want) {
            Write-Host "== stage: libraries $have -> $want"
            $env:GIT_LFS_SKIP_SMUDGE = "1"
            git -C $libs fetch -q origin 2>&1 | Out-Host
            git -C $libs checkout -q $want 2>&1 | Out-Host
            $env:GIT_LFS_SKIP_SMUDGE = ""
            git -C $libs lfs pull 2>&1 | Out-Host
            if ($LASTEXITCODE -ne 0 -or (git -C $libs rev-parse HEAD 2>$null) -ne $want) {
                $ErrorActionPreference = $old
                throw "the Windows libraries could not be brought to $want"
            }
        }
        $ErrorActionPreference = $old
        foreach ($name in "CUDA_PATH", "HIP_PATH", "OPTIX_ROOT_DIR", "OCLOC_INSTALL_DIR") {
            $value = [Environment]::GetEnvironmentVariable($name, "Machine")
            if ($value) { Set-Item "env:$name" $value }
        }
        $env:ANGELO_LIBS_SHA = $want
        $env:ANGELO_SRC_COMMIT = Get-Meta "angelo-src-commit" ""   # the archive's commit, for the release manifest
        # The agent runtime (agent_runtime.json): fetched afresh for every release run, so the build bundles exactly
        # what the bucket holds now, each file checked against the uploaded SHA256SUMS. None uploaded: the build stops
        # at once unless angelo-release-allow-missing names agent-runtime.
        $runtimeDir = Join-Path $root "agent-runtime"
        Remove-Item $runtimeDir -Recurse -Force -ErrorAction SilentlyContinue
        $env:ANGELO_AGENT_RUNTIME_DIR = ""
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $runtimeObjects = @(& $gcloud storage ls "gs://$bucket/agent-runtime/" 2>$null | Where-Object { $_ -is [string] -and $_ -notmatch "/$" })
        if ($runtimeObjects) {
            New-Item -ItemType Directory -Force $runtimeDir | Out-Null
            & $gcloud storage cp --quiet "gs://$bucket/agent-runtime/*" $runtimeDir 2>&1 | Out-Null
        }
        $ErrorActionPreference = $old
        if ($runtimeObjects) {
            $sums = Join-Path $runtimeDir "SHA256SUMS"
            if (-not (Test-Path $sums)) { throw "gs://$bucket/agent-runtime/ has no SHA256SUMS" }
            foreach ($line in Get-Content $sums) {
                if (-not $line.Trim()) { continue }
                $digest, $file = $line.Trim() -split "\s+\*?", 2
                $path = Join-Path $runtimeDir $file
                $have = if (Test-Path $path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { "missing" }
                if ($have -ne $digest.ToLowerInvariant()) { throw "agent runtime: $file is $have, SHA256SUMS says $digest" }
            }
            $env:ANGELO_AGENT_RUNTIME_DIR = $runtimeDir
            Write-Host "agent runtime: $(@(Get-ChildItem $runtimeDir -Filter *.exe).Count) executables from gs://$bucket/agent-runtime/ (SHA256SUMS checked)"
        } else {
            Write-Host "agent runtime: none in gs://$bucket/agent-runtime/"
        }
    }

    # --- 4. Build, package, install, check -----------------------------------------------------------
    if (-not (Stage-Done "validate")) {
        Write-Host "== stage: validate"
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

    # --- 4b. Checks that need a signed-in desktop ------------------------------------------------------
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
            Sync-Logs
            Restart-Computer -Force
            exit 0
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
        Stage-Mark "gui"
      } catch {
        # The desktop-session checks are an extra: a failure here must not lose the results of the others.
        Write-Host "desktop session stage failed: $_"
        Write-Host $_.ScriptStackTrace
        Stage-Mark "gui"
      }
    }

    # --- 4c. Final measurement runs (scripts\angelo\windows_final_runs.ps1, doc/angelo/final_runs.rst) ------------
    # Mode from metadata angelo-bench-mode: prep (after the release stage: install, freeze, wait for the RDP sign-in), dry (unbilled)
    # or run (billed; only with `gcp_windows_validate.sh bench run --authorize-billed-runs`). Exit code 194: the
    # desktop session it needs starts after a reboot; the stage then waits for that session at the next boot.
    $stagesBench = (Get-Meta "angelo-stages" "identity+teams").Split("+")
    if ($stagesBench -contains "bench" -and -not (Stage-Done "bench")) {
        Write-Host "== stage: bench"
        # The periodic log upload would compete with the timed trials; the logs go up when the stage ends.
        Stop-Job $syncer -ErrorAction SilentlyContinue
        $benchExit = 1
        $ErrorActionPreference = "Continue"   # Its programs write to stderr.
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src "scripts\angelo\windows_final_runs.ps1") `
                -Stage -SourceDir $src -Bucket $bucket -Gcloud $gcloud *>&1 | Tee-Object -Append (Join-Path $logs "bench.log")
            $benchExit = $LASTEXITCODE
        } catch { Write-Host "bench stage failed: $_" }
        finally { $ErrorActionPreference = "Stop" }
        if ($benchExit -eq 194) { Sync-Logs; Restart-Computer -Force; exit 0 }
        if ($benchExit -ne 0) { Set-Content (Join-Path $state "validate.exit") $benchExit }   # Keeps an earlier stage's failure.
        Stage-Mark "bench"
    }

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
    Set-Content (Join-Path $root "FAILED") "$_"
    & $gcloud storage cp --quiet (Join-Path $root "FAILED") "gs://$bucket/result/FAILED"
} finally {
    Stop-Job $syncer -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
    Sync-Logs
}
# A finished (or failed) unattended run powers the machine off: a stopped VM costs only its disk.
if ((Get-Meta "angelo-stop-when-done" "0") -eq "1" -and ((Test-Path (Join-Path $root "DONE")) -or (Test-Path (Join-Path $root "FAILED")))) {
    Stop-Computer -Force
}
