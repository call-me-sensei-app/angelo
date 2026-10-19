# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The final measurement runs on the Windows validation machine (doc/angelo/final_runs.rst).

.DESCRIPTION
  Four entry points:

  -Stage    run by windows_cloud_bootstrap.ps1 (stage "bench", as SYSTEM) at every boot. It prepares the machine
            (Prepare-Bench: Node, Python 3.13 and uv; Claude Code from npm and stock Codex from its GitHub release
            at the Mac's versions; stock Blender 5.2.2, the current release; the pilot's MCP add-on and server from
            gs://<bucket>/bench/mcp-runtime.tar.gz; the candidate: the MSI named by angelo-bench-candidate-msi
            (installed, its install folder frozen) or else this commit's full-feature build from the 'release'
            stage, frozen), then by metadata angelo-bench-mode:
              prep  keeps an earlier sign-in whose accounts check-sign-in still finds (result/SIGNED-IN-KEPT), or
                    waits (angelo-bench-signin-minutes, default 60) for the operator's sign-in over RDP (-SignIn);
              dry   runs final_runs.py dry-run, headroom and status in a signed-in desktop session (unbilled);
              run   runs final_runs.py run --authorize-billed-runs there, resuming the campaign, until it ends or
                    angelo-bench-hours pass (the machine's own limit is ANGELO_GCP_MAX_HOURS).
            The desktop session is a throwaway user's automatic sign-in after one reboot (as the GUI stage does);
            exit code 194 asks the bootstrap for that reboot. Results go to gs://<bucket>/bench/.
  -Session  started in that desktop session by a Run-key entry; writes C:\angelo\bench\out\DONE-<nonce>.
  -Setup    Prepare-Bench alone (a held machine's remote command), from parameters instead of metadata:
            -ClaudeVersion, -CodexVersion (default: the agent runtime's pin), -StockUrl, -McpBundle,
            -OneshotSources (a folder of the one-shot tests' source pictures; checked, then named in facts.json) and
            -CandidateMsi (a path) with -CandidateSha256. Prints facts.json.
  -SignIn   run by the operator over RDP, once: creates the bench's two connections in
            C:\angelo\bench\connections (final_runs.py prepare-connections: a Claude and a ChatGPT subscription, each
            with its own profile there, never the person's own) and signs each one's CLI in: Claude Code
            (claude auth login: a URL to open in any browser, a code to paste back) and Codex (codex login
            --device-auth: a one-time code to enter in the Mac's browser); then checks them
            (check-sign-in) and writes the SIGNED-IN marker naming the accounts; -Accounts chatgpt on a machine
            that runs ChatGPT steps only.

  It never reads, prints or copies a credential: sign-ins stay in C:\angelo\bench\connections, in the files the
  CLIs keep there, and only their existence is checked.
#>
param(
    [switch]$Stage, [switch]$Session, [switch]$SignIn, [switch]$Setup,
    [string]$SourceDir = "C:\angelo\src", [string]$Bucket = "", [string]$Gcloud = "",
    [string]$Mode = "", [string]$Nonce = "",
    [string]$ClaudeVersion = "", [string]$CodexVersion = "", [string]$McpBundle = "", [string]$OneshotSources = "",
    [string]$StockUrl = "https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip",
    [string]$CandidateMsi = "", [string]$CandidateSha256 = "",
    # -SignIn: the accounts this machine's steps use (the Astra and Sol clones run ChatGPT steps only).
    [ValidateSet("claude", "chatgpt")][string[]]$Accounts = @("claude", "chatgpt")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$bench = "C:\angelo\bench"
$state = Join-Path $bench "state"
$out = Join-Path $bench "out"
$logs = "C:\angelo\logs"
$benchUser = "angelobench"
New-Item -ItemType Directory -Force $bench, $state, $out, $logs | Out-Null

function Get-Meta([string]$Name, [string]$Default = "") {
    try {
        Invoke-RestMethod -Headers @{ "Metadata-Flavor" = "Google" } `
            -Uri "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$Name"
    } catch { $Default }
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Add-MachinePath([string]$Folder) {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    if (($machine.Split(";") | Where-Object { $_ -eq $Folder }).Count -eq 0) {
        [Environment]::SetEnvironmentVariable("Path", "$Folder;$machine", "Machine")
    }
    Refresh-Path
}
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File $($Arguments -join ' ') exited with $LASTEXITCODE" }
}
# Windows PowerShell 5.1 turns each stderr line of a native program whose stderr is redirected into an error record,
# and under this script's Stop preference the first one ends the script (gcloud's "Copying ..." progress line on
# stderr would end bench prep right after SIGN-IN-READY). A program whose output is captured or hidden runs here:
# Continue for this call only, every line to the host as text, and its exit code returned for the caller to check.
function Invoke-Native([string]$File, [string[]]$Arguments) {
    $ErrorActionPreference = "Continue"   # This function's scope only.
    & $File @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host
    return $LASTEXITCODE
}
# Every gcloud call: a failed copy stops the stage (-Optional: a warning), never its progress output.
function Invoke-Gcloud([string[]]$Arguments, [switch]$Optional) {
    $code = Invoke-Native $Gcloud $Arguments
    if ($code -ne 0) {
        $failed = "gcloud $($Arguments -join ' ') exited with $code"
        if ($Optional) { Write-Warning $failed } else { throw $failed }
    }
}
function Read-Facts { if (Test-Path (Join-Path $state "facts.json")) { Get-Content (Join-Path $state "facts.json") -Raw | ConvertFrom-Json } else { $null } }
. (Join-Path $PSScriptRoot "windows_bench_tools.ps1")

$commit = (Get-Meta "angelo-src-commit" "")
$short = if ($commit) { $commit.Substring(0, [Math]::Min(12, $commit.Length)) } else { "unknown" }
$connections = Join-Path $bench "connections"
$python313 = "C:\Python313\python.exe"
$finalRuns = Join-Path $SourceDir "tests\python\angelo_agent\final_runs.py"
$installDir = "C:\Program Files\Angelo"

# Everything a campaign needs on this machine, installed once and checked every time; facts.json says what it is.
function Prepare-Bench([string]$ClaudeVersion, [string]$CodexVersion, [string]$StockUrl, [string]$Bundle,
                       [string]$CandidateMsi, [string]$CandidateSha256, [string]$Sources) {
    # Only facts.json's object is returned: everything a step prints goes to the host.
    # 1. Tools: Node (npm for Claude Code), Python 3.13 (uv, the MCP install), uv.
    Refresh-Path
    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { & choco install -y --no-progress nodejs-lts | Out-Host; Refresh-Path }
    if (-not (Test-Path $python313)) { & choco install -y --no-progress python313 --params "/InstallDir:C:\Python313" | Out-Host }
    $uv = "C:\Python313\Scripts\uv.exe"
    if (-not (Test-Path $uv)) { Invoke-Checked $python313 @("-m", "pip", "install", "--disable-pip-version-check", "-q", "uv==0.9.5") | Out-Host }
    # 2. The two CLIs: Claude Code at the Mac comparator's version, stock (unpatched) Codex at the runtime's.
    if (-not $CodexVersion) {
        $CodexVersion = (Get-Content (Join-Path $SourceDir "scripts\addons_core\angelo_agent\agent_runtime.json") -Raw | ConvertFrom-Json).version
    }
    $claude = Install-ClaudeCode $bench $ClaudeVersion
    $codex = Install-StockCodex $bench $CodexVersion
    # Both sides use these two executables: the harness by path, Angelo through PATH (its own discovery; a release
    # build runs its own agent runtime for Codex).
    Add-MachinePath (Split-Path $claude.path)
    Add-MachinePath (Split-Path $codex.path)
    if ($codex.ripgrep) { Add-MachinePath (Split-Path $codex.ripgrep) }
    # 3. Stock Blender, the current stock release (a portable archive; nothing is installed).
    $stock = Install-StockBlender $bench $StockUrl
    # 4. The pilot's MCP add-on and server, byte-identical, with the frozen dependency versions.
    $mcp = Install-McpRuntime $bench $Bundle $python313 $uv $SourceDir
    # 4b. The one-shot tests' source pictures (the tree and the cottage cut their references from one), checked
    #     against the tests' SHA-256 pins; the session points ANGELO_ONESHOT_SOURCES at them.
    $oneshot = if ($Sources) { Install-OneshotSources $Sources $python313 $SourceDir } else { $null }
    # 5. The candidate: an MSI installed and frozen, or this commit's full-feature build ('release' stage), frozen.
    if ($CandidateMsi) {
        $frozen = Freeze-MsiCandidate $bench $CandidateMsi $CandidateSha256 $installDir $SourceDir
        $candidate = $frozen.folder; $harnessPython = $frozen.harness_python
    } else {
        $candidate = Join-Path $bench "candidate-$short"
        if (-not (Test-Path (Join-Path $candidate "RECEIPT.json"))) {
            $buildDir = Join-Path (Split-Path $SourceDir) "build_windows_Release_x64_vc17_Release"
            $manifestPath = Join-Path $buildDir "angelo-release-build.json"
            if (-not (Test-Path $manifestPath)) { throw "no full-feature build: run 'gcp_windows_validate.sh bench prep' first" }
            $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
            if ($manifest.source_commit -ne $commit -or -not $manifest.built) {
                throw "the full-feature build is of '$($manifest.source_commit)' (built: '$($manifest.built)'), not ${commit}: run 'bench prep'"
            }
            $release = Join-Path $buildDir "bin\Release"
            Invoke-Checked (Join-Path $release "5.2\python\bin\python.exe") @($finalRuns, "freeze", "--source", $release, "--into", $candidate) | Out-Host
            Copy-Item $manifestPath (Join-Path $candidate "angelo-release-build.json")
        }
        $harnessPython = Join-Path $candidate "Release\5.2\python\bin\python.exe"
        $frozen = [ordered]@{ folder = $candidate; build = "angelo-release-build.json" }
    }
    $receipt = Get-Content (Join-Path $candidate "RECEIPT.json") -Raw | ConvertFrom-Json
    $facts = [ordered]@{ commit = $commit; candidate = $candidate; campaign = (Join-Path $bench ("campaign-" + (Split-Path $candidate -Leaf).Replace("candidate-", "")))
        harness_python = $harnessPython; candidate_version = @($receipt.version)[0]; candidate_executable_sha256 = $receipt.executable_sha256
        candidate_source = $frozen
        claude = $claude.path; claude_version = $claude.version; codex = $codex.path; codex_version = $codex.version
        stock_blender = $stock.path; mcp_server_dir = $mcp.server_dir; mcp_addon_dir = $mcp.addon_dir
        oneshot_sources = $(if ($oneshot) { $oneshot.folder } else { "" })
        tools = [ordered]@{ claude = $claude; codex = $codex; stock_blender = $stock; mcp = $mcp; oneshot_sources = $oneshot
            node = "$(& node.exe --version)"; npm = "$(& npm.cmd --version)"; python = "$(& $python313 --version)"; uv = "$(& $uv --version)" } }
    $facts | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $state "facts.json")
    New-Item -ItemType Directory -Force $connections | Out-Null
    return $facts
}

if ($Setup) {
    if (-not $McpBundle) { throw "-Setup needs -McpBundle (final_runs_mcp.py bundle on the Mac)" }
    $facts = Prepare-Bench $ClaudeVersion $CodexVersion $StockUrl $McpBundle $CandidateMsi $CandidateSha256 $OneshotSources
    $facts | ConvertTo-Json -Depth 5
    exit 0
}

# ------------------------------------------------------------------ the operator's sign-in (over RDP)
if ($SignIn) {
    $facts = Read-Facts
    if (-not $facts) { throw "The machine is not prepared (windows_final_runs.ps1 -Setup, or 'gcp_windows_validate.sh bench prep')" }
    $ErrorActionPreference = "Continue"   # The CLIs write prompts to stderr.
    $prepared = (& $facts.harness_python $finalRuns prepare-connections --connections-dir $connections) -join "`n" | ConvertFrom-Json
    if (-not $prepared) { throw "final_runs.py prepare-connections failed" }
    if ($Accounts -contains "claude") {
        Write-Host ""
        Write-Host "Claude Code, with your Claude subscription. A sign-in URL appears below: open it in the browser on"
        Write-Host "your Mac (copy it across), approve, and paste the code it shows back here."
        $env:CLAUDE_CONFIG_DIR = $prepared.claude_profile
        & $facts.claude auth login
        Remove-Item env:CLAUDE_CONFIG_DIR
    }
    if ($Accounts -contains "chatgpt") {
        # Codex's own sign-in for a remote machine (its hint on this machine; not in its --help): a URL and a one-time
        # code to enter in any browser, so the Mac's browser does it and no password is typed here.
        Write-Host ""
        Write-Host "The ChatGPT account the benchmark uses (never your main one). Codex prints a URL and a one-time code:"
        Write-Host "open the URL in your Mac's browser signed in to that account and enter the code; this window then finishes."
        $env:CODEX_HOME = $prepared.codex_profile
        & $facts.codex login --device-auth
        Remove-Item env:CODEX_HOME
    }
    $check = @("check-sign-in", "--connections-dir", $connections) + @($Accounts | ForEach-Object { @("--account", $_) })
    & $facts.harness_python $finalRuns @check
    if ($LASTEXITCODE -ne 0) { throw "Not signed in yet ($($Accounts -join ', ')): run this again (finished sign-ins are kept)" }
    # When, and which accounts were checked: a later bench prep checks those again and keeps the sign-in.
    Set-Content (Join-Path $connections "SIGNED-IN") @((Get-Date -Format o), ("accounts: " + ($Accounts -join " ")))
    Write-Host "Signed in. Sign out of this session; the bench continues by itself."
    exit 0
}

# ------------------------------------------------------------------ the measurement session (desktop)
if ($Session) {
    # The Run key starts this at every sign-in: only the bench user's, once per request (a reboot mid-run resumes).
    if ($env:USERNAME -ne $benchUser -or (Test-Path (Join-Path $out "DONE-$Nonce"))) { exit 0 }
    $log = Join-Path $logs "bench-session-$Nonce.log"
    $code = 1
    try {
        Start-Transcript -Path $log -Append | Out-Null
        $facts = Read-Facts
        $campaign = $facts.campaign; $candidate = $facts.candidate
        $env:ANGELO_SOURCE_COMMIT = $commit
        $env:PYTHONUTF8 = "1"
        $env:PYTHONDONTWRITEBYTECODE = "1"
        # The one-shot tests' source pictures, checked by Prepare-Bench (the bench user's own profile has none).
        if ($facts.oneshot_sources) { $env:ANGELO_ONESHOT_SOURCES = $facts.oneshot_sources }
        $py = $facts.harness_python
        if (-not (Test-Path (Join-Path $campaign "CONFIG.json"))) {
            Invoke-Checked $py @($finalRuns, "init", "--output", $campaign, "--candidate", $candidate, "--platform", "windows",
                "--connections-dir", $connections, "--discover-connections", "--stock-blender", $facts.stock_blender,
                "--mcp-server-dir", $facts.mcp_server_dir, "--mcp-addon-dir", $facts.mcp_addon_dir,
                "--codex", $facts.codex, "--claude", $facts.claude, "--python", $py)
        }
        $select = @()
        foreach ($glob in ((Get-Meta "angelo-bench-only" "").Split(" ") | Where-Object { $_ })) { $select += @("--only", $glob) }
        if ($Mode -eq "run") {
            $started = [datetime]::Parse((Get-Content (Join-Path $state "started-$Nonce")))
            $hours = [double](Get-Meta "angelo-bench-hours" "7") - ((Get-Date) - $started).TotalHours - 0.25
            if ($hours -le 0.5) { throw "less than half an hour of this session left; start another" }
            & $py $finalRuns run --output $campaign --authorize-billed-runs --stop-after-hours ([Math]::Round($hours, 2)) @select
            $code = $LASTEXITCODE
        } else {
            & $py $finalRuns dry-run --output $campaign --depth full --wait-quiet-minutes 30 @select
            $code = $LASTEXITCODE
            & $py $finalRuns headroom --output $campaign
        }
        & $py $finalRuns status --output $campaign
        & $py $finalRuns report --output $campaign | Set-Content (Join-Path $campaign "REPORT.json")
    } catch {
        Write-Host "bench session failed: $_"
        Write-Host $_.ScriptStackTrace
    } finally {
        Set-Content (Join-Path $out "DONE-$Nonce") "$Mode exit code: $code"
        Stop-Transcript | Out-Null
    }
    exit 0
}

if (-not $Stage) { throw "Pass -Stage, -Session or -SignIn" }

# ------------------------------------------------------------------ the stage (SYSTEM, every boot)
$mode = Get-Meta "angelo-bench-mode" ""
$nonce = Get-Meta "angelo-src-nonce" "0"
if ($mode -notin "prep", "dry", "run") { throw "metadata angelo-bench-mode must be prep, dry or run (got '$mode')" }
$startedFile = Join-Path $state "started-$nonce"
if (-not (Test-Path $startedFile)) { Set-Content $startedFile (Get-Date -Format o) }
Write-Host "== bench stage: $mode, commit $short, request $nonce"

# 1-5. Tools, CLIs, stock Blender, the MCP side and the candidate (Prepare-Bench).
$claudeVersion = Get-Meta "angelo-bench-claude-version" ""
if (-not $claudeVersion) { throw "metadata angelo-bench-claude-version is required (the Mac comparator's Claude Code)" }
$bundle = Join-Path $bench "mcp-runtime.tar.gz"
Invoke-Gcloud @("storage", "cp", "--quiet", "gs://$Bucket/bench/mcp-runtime.tar.gz", $bundle)
# The one-shot tests' source pictures, from the private bucket only (gcp_windows_validate.sh oneshot-sources);
# Prepare-Bench checks them. A folder that already holds the pinned pictures stays: the later boot of a request
# runs while its session may be reading them.
$sources = Join-Path $bench "oneshot-sources"
$sourcesCheck = @((Join-Path $SourceDir "tests\python\angelo_agent\final_runs_sources.py"), "check", "--dir", $sources)
if (-not ((Test-Path $python313) -and (Invoke-Native $python313 $sourcesCheck) -eq 0)) {
    Remove-Item $sources -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $sources | Out-Null
    if ((Invoke-Native $Gcloud @("storage", "cp", "--quiet", "gs://$Bucket/bench/oneshot-sources/*", $sources)) -ne 0) {
        throw "no one-shot source pictures in gs://$Bucket/bench/oneshot-sources/: upload them with 'gcp_windows_validate.sh oneshot-sources DIR'"
    }
}
$candidateMsi = Get-Meta "angelo-bench-candidate-msi" ""
$candidateSha = Get-Meta "angelo-bench-candidate-sha256" ""
$frozenAlready = $candidateSha -and (Test-Path (Join-Path $bench ("candidate-msi-" + $candidateSha.Substring(0, 12) + "\RECEIPT.json")))
if ($candidateMsi -like "gs://*" -and -not $frozenAlready) {
    $local = Join-Path $bench ("download\" + (Split-Path $candidateMsi -Leaf))
    New-Item -ItemType Directory -Force (Split-Path $local) | Out-Null
    Invoke-Gcloud @("storage", "cp", "--quiet", $candidateMsi, $local)
    $candidateMsi = $local
}
$facts = Prepare-Bench $claudeVersion (Get-Meta "angelo-bench-codex-version" "") `
    (Get-Meta "angelo-bench-stock-url" "https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip") `
    $bundle $candidateMsi $candidateSha $sources
$campaign = $facts.campaign
$short = Split-Path $campaign -Leaf

if ($mode -eq "prep") {
    # A machine signed in before keeps its sign-ins: the accounts its marker names are checked again as -SignIn
    # checks them (check-sign-in: the CLIs' files exist; nothing is read), and the marker stays when they pass.
    $marker = Join-Path $connections "SIGNED-IN"
    if (Test-Path $marker) {
        $line = @(Get-Content $marker | Where-Object { $_ -match "^accounts:" })[0]
        $accounts = @(if ($line) { ($line -replace "^accounts:", "").Split(" ") | Where-Object { $_ -in "claude", "chatgpt" } })
        if ($accounts.Count -eq 0) { $accounts = @("claude", "chatgpt") }   # An older marker names none: both.
        $check = @($finalRuns, "check-sign-in", "--connections-dir", $connections) + @($accounts | ForEach-Object { @("--account", $_) })
        if ((Invoke-Native $facts.harness_python $check) -eq 0) {
            $kept = Join-Path $state "SIGNED-IN-KEPT"
            Set-Content $kept "signed in $(@(Get-Content $marker)[0]); checked again $(Get-Date -Format o): $($accounts -join ' ')"
            Write-Host "== signed in already ($($accounts -join ', ')): the sign-ins are there and kept; no sign-in needed"
            Invoke-Gcloud @("storage", "cp", "--quiet", $kept, "gs://$Bucket/result/SIGNED-IN-KEPT") -Optional
            exit 0
        }
        Write-Host "== the earlier sign-in ($($accounts -join ', ')) is incomplete: waiting for -SignIn again"
        Remove-Item $marker
    }
    $minutes = [int](Get-Meta "angelo-bench-signin-minutes" "60")
    if ($minutes -le 0) { $minutes = 60 }
    Write-Host "== prepared; waiting up to $minutes minutes for the sign-in (RDP, then: powershell -File $PSCommandPath -SignIn)"
    $ready = Join-Path $state "SIGN-IN-READY"
    Set-Content $ready "waiting until $((Get-Date).AddMinutes($minutes).ToString('o'))"
    Invoke-Gcloud @("storage", "cp", "--quiet", $ready, "gs://$Bucket/result/SIGN-IN-READY")   # `bench prep` says so.
    $deadline = (Get-Date).AddMinutes($minutes)
    while (-not (Test-Path (Join-Path $connections "SIGNED-IN")) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 20 }
    if (-not (Test-Path (Join-Path $connections "SIGNED-IN"))) { throw "no sign-in within $minutes minutes" }
    Write-Host "== signed in"
    exit 0
}
if (-not (Test-Path (Join-Path $connections "SIGNED-IN"))) { throw "not signed in: run 'bench prep' and sign in over RDP first" }

# 6. A desktop session for the GUI apps: a throwaway user signs in automatically after one reboot and a Run-key
#    entry starts -Session there (the GUI stage's mechanism). Taken away again when the session has finished.
$winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$run = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
$prepared = Join-Path $state "session-$nonce"
if (-not (Test-Path $prepared)) {
    Add-Type -AssemblyName System.Web
    $pw = [System.Web.Security.Membership]::GeneratePassword(20, 4) + "aA1!"
    Remove-LocalUser -Name $benchUser -ErrorAction SilentlyContinue
    # An earlier session user's profile goes too (removing an account keeps it).
    Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "C:\Users\$benchUser*" -and -not $_.Loaded } |
        Remove-CimInstance -ErrorAction SilentlyContinue
    New-LocalUser -Name $benchUser -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null
    Add-LocalGroupMember -Group "Users" -Member $benchUser
    Set-ItemProperty $winlogon AutoAdminLogon "1"
    Set-ItemProperty $winlogon DefaultUserName $benchUser
    Set-ItemProperty $winlogon DefaultDomainName $env:COMPUTERNAME
    Set-ItemProperty $winlogon DefaultPassword $pw
    Remove-Variable pw
    # The bench (with the sign-ins the operator left, file by file), the source tree and the logs.
    & icacls.exe $bench /grant "${benchUser}:(OI)(CI)M" /T /C /Q | Out-Null
    foreach ($folder in $SourceDir, $logs) { & icacls.exe $folder /grant "${benchUser}:(OI)(CI)M" /C /Q | Out-Null }
    if (-not (Test-Path "HKLM:\SOFTWARE\Microsoft\ServerManager")) { New-Item "HKLM:\SOFTWARE\Microsoft\ServerManager" | Out-Null }
    Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\ServerManager" DoNotOpenServerManagerAtLogon 1 -Type DWord
    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -Session -SourceDir "{1}" -Mode {2} -Nonce {3}' -f $PSCommandPath, $SourceDir, $mode, $nonce
    Set-ItemProperty $run AngeloFinalRuns $command
    Set-Content $prepared (Get-Date -Format o)
    Write-Host "== restarting so the bench user signs in"
    exit 194
}
$hours = [double](Get-Meta "angelo-bench-hours" "7")
$deadline = [datetime]::Parse((Get-Content $startedFile)).AddHours($hours + 0.25)
Write-Host "== waiting for the desktop session (until $deadline)"
while (-not (Test-Path (Join-Path $out "DONE-$nonce")) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 30 }
$result = if (Test-Path (Join-Path $out "DONE-$nonce")) { Get-Content (Join-Path $out "DONE-$nonce") } else { "TIMED OUT" }
Write-Host "== session: $result"
Set-ItemProperty $winlogon AutoAdminLogon "0"
Remove-ItemProperty $winlogon DefaultPassword -ErrorAction SilentlyContinue
Remove-ItemProperty $run AngeloFinalRuns -ErrorAction SilentlyContinue
Remove-LocalUser -Name $benchUser -ErrorAction SilentlyContinue
if (Test-Path $campaign) { Invoke-Gcloud @("storage", "rsync", "--recursive", $campaign, "gs://$Bucket/bench/$short") -Optional }
Invoke-Gcloud @("storage", "cp", "--quiet", (Join-Path $state "facts.json"), "gs://$Bucket/bench/facts-$short.json") -Optional
if ($result -notmatch "exit code: 0$") { exit 1 }
exit 0
