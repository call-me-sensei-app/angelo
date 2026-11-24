# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# session.ps1 (L5 2.0.1, a bench session command: windows_bench_session_command.ps1; runs in the bench user's desktop session): the update
# gate's state receipt, 2.0.0 -> the signed 2.0.1, with this machine's signed-in bench account (--profile account: the
# bench user is new on every send and has no Angelo folders). The gate's input is a signed-in account folder made of the
# bench's records whose own profile exists and whose own status command exits 0, each naming that profile by its
# absolute path, as the shipped recovery (recover_connections.py) names it: metadata only, no credential is read,
# copied or printed (exit codes only). Only the receipt and the logs go to <Out> (uploaded); the account folder and
# the gate's own working folder stay on the machine in <session>\work (orchestrator: nothing but the receipt leaves).
param([string]$In, [string]$Out, [string]$Facts, [string]$Connections)
$ErrorActionPreference = "Continue"; $ProgressPreference = "SilentlyContinue"
$log = Join-Path $Out "session.log"
function Say([string]$Text) { $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Text; Write-Output $line; Add-Content $log $line }
$src = "C:\angelo\src"
# The commit the bench session proved before it started this (windows_final_runs.ps1 -Session: Read-SentCommit).
$expectSrc = [string]$env:ANGELO_SOURCE_COMMIT
$env:PYTHONUTF8 = "1"
Say ("user {0}, session {1}" -f $env:USERNAME, (Get-Process -Id $PID).SessionId)
$commit = ""; try { . (Join-Path $src "scripts\angelo\windows_source_send.ps1"); $commit = [string](Get-SentCommit $src) } catch { Say "FAIL: the tree verifier refused the source: $_"; exit 1 }
if (-not $expectSrc -or $commit -ne $expectSrc) { Say "FAIL: the tree is $commit, not the session's $expectSrc"; exit 1 }
# This command's own files, by SHA-256, into the receipt (update_carry_over_gate.tools): what ran is on record.
$toolFiles = [ordered]@{ "session.ps1" = $PSCommandPath; "prepare.ps1" = (Join-Path $In "prepare.ps1") }
$toolShas = [ordered]@{}; foreach ($k in $toolFiles.Keys) { $toolShas[$k] = if (Test-Path -LiteralPath $toolFiles[$k]) { (Get-FileHash -LiteralPath $toolFiles[$k] -Algorithm SHA256).Hash.ToLower() } else { "missing" } }
$env:ANGELO_GATE_SESSION_TOOLS = ($toolShas | ConvertTo-Json -Compress)
Say "session command files: $($env:ANGELO_GATE_SESSION_TOOLS)"
$f = Get-Content $Facts -Raw | ConvertFrom-Json
$py = [string]$f.harness_python
$apps = Get-Content (Join-Path $In "apps.json") -Raw | ConvertFrom-Json
Say "tree $commit; python $py; old $($apps.old); new $($apps.new)"
$records = @((Get-Content (Join-Path $Connections "connections.json") -Raw | ConvertFrom-Json).connections)
$signed = @()
foreach ($r in $records) { if (-not $r) { continue }
    if (@("claude_subscription", "codex_subscription", "codex_api") -notcontains [string]$r.mode) { continue }
    $fam = if ([string]$r.mode -like "claude_*") { "claude-profile" } else { "codex-profile" }
    $dir = Join-Path (Join-Path $Connections $fam) ([string]$r.id)
    $code = "no profile"
    if (Test-Path -LiteralPath $dir) {
        $tool = if ($fam -eq "claude-profile") { @([string]$f.claude, "auth", "status") } else { @([string]$f.codex, "login", "status") }
        $var = if ($fam -eq "claude-profile") { "CLAUDE_CONFIG_DIR" } else { "CODEX_HOME" }
        $prior = [Environment]::GetEnvironmentVariable($var); [Environment]::SetEnvironmentVariable($var, $dir)
        try { & $tool[0] $tool[1] $tool[2] *> $null; $code = $LASTEXITCODE } catch { $code = "failed to start" }
        [Environment]::SetEnvironmentVariable($var, $prior)
    }
    Say ("bench record {0} {1}: own profile exists {2}; status exit {3}" -f $r.mode, ([string]$r.id).Substring(0, 8), (Test-Path -LiteralPath $dir), $code)
    if ("$code" -eq "0") { $r | Add-Member -NotePropertyName native_profile -NotePropertyValue $dir -Force; $signed += $r }
}
if (-not $signed) { Say "BLOCKED: no signed-in coding account in $Connections"; exit 1 }
$work = Join-Path (Split-Path $In -Parent) "work"
$account = Join-Path $work "account"; New-Item -ItemType Directory -Force $account | Out-Null
$doc = [ordered]@{ version = 3; active = [string]$signed[0].id; connections = $signed; ignored_native_modes = @() }
[IO.File]::WriteAllText((Join-Path $account "connections.json"), ($doc | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
Say ("the gate's account folder: {0} ({1} signed-in coding account(s))" -f $account, $signed.Count)
$cli = @(); foreach ($n in @("claude", "codex")) { $p = [string]$f.$n; if ($p -and (Test-Path -LiteralPath $p)) { $cli += @("--$n", $p) } }
$gate = Join-Path $src "tests\python\angelo_agent\update_carry_over_gate.py"
$gateOut = Join-Path $work "gate"
Say "== update_carry_over_gate.py --profile account --connections-from $account $($cli -join ' ')"
& $py -s -B $gate --from-app $apps.old --to-app $apps.new --out $gateOut --profile account --connections-from $account @cli 2>&1 |
    ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $Out "gate.log")
$code = $LASTEXITCODE
Say "gate exit $code"
$receipt = Join-Path $gateOut "update-gate.json"
New-Item -ItemType Directory -Force (Join-Path $Out "gate") | Out-Null
Copy-Item $receipt (Join-Path $Out "gate\update-gate.json") -ErrorAction SilentlyContinue
if (Test-Path $receipt) { $g = Get-Content $receipt -Raw | ConvertFrom-Json
    Say ("gate receipt: success {0}; failed {1}; error {2}" -f $g.success, ($g.failed_checks -join ","), $g.error)
    foreach ($e in @($g.sign_in)) { if ($e.mode) { Say ("  sign-in {0} {1}: {2} (exit {3})" -f $e.mode, ([string]$e.id).Substring(0, 8), $e.status, $e.exit) } } }
# A failed gate keeps its evidence: the phases' logs and screenshots (no preference value: a reading holds strings as
# digests) are copied into out\ before anything is removed, so a miss can be diagnosed (break-it review r5 S2).
if ($code -ne 0 -and (Test-Path -LiteralPath $gateOut)) {
    $keep = Join-Path $Out "failed-gate"
    Get-ChildItem -LiteralPath $gateOut -Recurse -File -Include *.out, *.log, *.png -ErrorAction SilentlyContinue | ForEach-Object {
        $to = Join-Path $keep $_.FullName.Substring($gateOut.Length).TrimStart("\")
        New-Item -ItemType Directory -Force (Split-Path $to -Parent) | Out-Null
        Copy-Item -LiteralPath $_.FullName $to }
    Say "the failed gate's logs and screenshots kept in $keep"
}
# This run's own large folders go once the receipt is copied: its unpacked apps, its MSIs and the gate's work. Only
# these three, and only inside this session's own folder: anything else is refused, never removed.
$session = [IO.Path]::GetFullPath((Split-Path $In -Parent)).TrimEnd("\")
foreach ($own in @((Join-Path $In "apps"), (Join-Path $In "msi"), $work)) {
    $full = [IO.Path]::GetFullPath($own).TrimEnd("\")
    if (-not $full.StartsWith($session + "\", [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $full -Leaf) -notin @("apps", "msi", "work")) { Say "REFUSED cleanup of $full (outside $session)"; continue }
    if (-not (Test-Path -LiteralPath $full)) { continue }
    # Neither the folder nor any folder between it and the session may be a link (an in\ swapped for a junction).
    $linked = $false; $step = $full
    while ($step.Length -gt $session.Length) {
        if ((Get-Item -LiteralPath $step -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { $linked = $true; break }
        $step = Split-Path $step -Parent }
    if ($linked) { Say "REFUSED cleanup of $full (a link at $step)"; continue }
    [IO.Directory]::Delete("\\?\" + $full, $true)
    Say ("removed this run's {0}: {1}" -f $full, -not (Test-Path -LiteralPath $full))
}
Say "DONE session"
exit $code
