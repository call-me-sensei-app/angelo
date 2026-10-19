# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The agent's Windows checks that need the signed-in desktop: CLI discovery and spawning, and the GUI native suites.

.DESCRIPTION
  Started by windows_gui_checks.ps1 in the automatically signed-in test user's session when the 'agent' stage is
  selected (windows_agent_validate.ps1 built the app and installed Python 3.13, Node.js LTS and Git before the restart).
  As that user it:
    1. installs the official CLIs the documented ways: Claude Code's native installer (claude.ai/install.ps1) and
       npm (@anthropic-ai/claude-code, @openai/codex), plus a tiny local npm package whose .cmd shim echoes its
       arguments (the quoting check);
    2. starts the built app through Angelo-launcher.exe, as a desktop launch does, with windows_agent_checks.py cli:
       discovery, --version probes, Codex's app-server, shim quoting, CREATE_NO_WINDOW, the Job Object tree kill,
       UTF-8 pipes, Git Bash and the default browser;
    3. runs the native suites with --gui against the built Angelo.exe.
  Nothing signs in to a provider and no credential is read or stored.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$ResultsDir = "C:\angelo\results\gui\agent",
    [int]$NativeJobs = 2
)
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    [void]$results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    Write-Host ("{0,-5} {1}  {2}" -f $Status, $Name, $Detail)
}
function Test-Check([string]$Name, [scriptblock]$Body) {
    try { $detail = & $Body; Add-Result $Name "PASS" ([string]($detail | Out-String).Trim()) }
    catch { Add-Result $Name "FAIL" $_.Exception.Message }
}

$python = "C:\Python313\python.exe"
$tests = Join-Path $SourceDir "tests\python\angelo_agent"
$build = Get-ChildItem (Split-Path $SourceDir -Parent) -Directory -Filter "build_windows_Lite*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$bin = Join-Path $build.FullName "bin\Release"
$exe = Join-Path $bin "Angelo.exe"
$launcher = Join-Path $bin "Angelo-launcher.exe"
$npm = Join-Path $env:ProgramFiles "nodejs\npm.cmd"
$npmLog = Join-Path $ResultsDir "cli-install.log"

Test-Check "agent gui: Claude Code native installer (claude.ai/install.ps1)" {
    # The documented "irm https://claude.ai/install.ps1 | iex", in a child PowerShell (the installer may exit).
    $installer = Join-Path $env:TEMP "claude-install.ps1"
    Invoke-WebRequest -UseBasicParsing "https://claude.ai/install.ps1" -OutFile $installer
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer 2>&1 | Out-String
    Add-Content $npmLog $output
    $installed = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
    if (-not (Test-Path $installed)) { throw "no $installed after the installer: $($output.Substring([Math]::Max(0, $output.Length - 400)))" }
    "$installed " + (& $installed --version 2>&1 | Out-String).Trim()
}
Test-Check "agent gui: npm install -g @anthropic-ai/claude-code @openai/codex" {
    if (-not (Test-Path $npm)) { throw "npm is not installed at $npm" }
    & $npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@latest" "@openai/codex@latest" *>&1 | Add-Content $npmLog
    if ($LASTEXITCODE -ne 0) { throw "npm exited with $LASTEXITCODE (cli-install.log)" }
    $folder = Join-Path $env:APPDATA "npm"
    foreach ($name in "claude.cmd", "codex.cmd") { if (-not (Test-Path (Join-Path $folder $name))) { throw "$name is not in $folder" } }
    (& $npm ls -g --depth=0 2>&1 | Out-String).Trim()
}
Test-Check "agent gui: a local npm package whose .cmd shim echoes its arguments" {
    $package = Join-Path $env:TEMP "angelo-argv-echo"
    Remove-Item $package -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $package | Out-Null
    Set-Content (Join-Path $package "package.json") '{"name":"angelo-argv-echo","version":"1.0.0","bin":{"angelo-argv-echo":"index.js"}}' -Encoding ASCII
    Set-Content (Join-Path $package "index.js") "#!/usr/bin/env node`nprocess.stdout.write(JSON.stringify(process.argv.slice(2)));" -Encoding ASCII
    Push-Location $package
    try { $tarball = (& $npm pack --silent 2>$null | Select-Object -Last 1).Trim() } finally { Pop-Location }
    & $npm install -g --no-fund --no-audit (Join-Path $package $tarball) *>&1 | Add-Content $npmLog
    $shim = Join-Path $env:APPDATA "npm\angelo-argv-echo.cmd"
    if (-not (Test-Path $shim)) { throw "npm made no $shim" }
    Get-Content $shim -Raw
}
Test-Check "agent gui: CLI checks inside the app started by its launcher" {
    $report = Join-Path $ResultsDir "cli.json"
    Remove-Item $report -ErrorAction SilentlyContinue
    $work = Join-Path $env:TEMP "angelo-cli-work"
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    $env:ANGELO_DISABLE_INTRO = "1"
    $arguments = "--factory-startup --no-window-focus --python `"$(Join-Path $tests 'windows_agent_checks.py')`" -- cli --report `"$report`" --work `"$work`" --desktop"
    Start-Process $launcher -ArgumentList $arguments | Out-Null
    $deadline = (Get-Date).AddMinutes(12)
    while (-not (Test-Path $report) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }
    Start-Sleep -Seconds 5
    Get-Process Angelo -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $report)) { throw "the app wrote no report within 12 minutes" }
    $data = Get-Content $report -Raw | ConvertFrom-Json
    if ($data.error) { throw "the checks raised: $($data.error)" }
    $steps = @($data.steps.PSObject.Properties)
    $failed = @($steps | Where-Object { $_.Value.ok -eq $false } | ForEach-Object { $_.Name })
    $line = "{0} steps: {1} PASS, {2} FAIL, {3} NOTE" -f $steps.Count, @($steps | Where-Object { $_.Value.ok -eq $true }).Count,
        $failed.Count, @($steps | Where-Object { $_.Value.ok -eq $null }).Count
    if ($failed) { throw "$line; failed: $($failed -join ', ')" }
    $line
}
Test-Check "agent gui: native suites with --gui on the built Angelo.exe" {
    # The runner puts its run folder under the temp folder, where its host drivers require their fixtures; a short
    # one keeps the suites' deepest snapshot paths short as well.
    $output = "C:\angelo\ng"
    Remove-Item $output -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $output | Out-Null
    $log = Join-Path $ResultsDir "native-gui.log"
    $env:PYTHONUTF8 = "1"
    # Through a batch file: cmd.exe writes the runner's UTF-8 bytes as they are (PowerShell 5 would re-encode them).
    $batch = Join-Path $env:TEMP "angelo-native-gui.cmd"
    [System.IO.File]::WriteAllText($batch, "@echo off`r`nset TEMP=$output`r`nset TMP=$output`r`n`"$python`" `"$(Join-Path $tests 'run_native_suites.py')`" --binary `"$exe`" --gui --jobs $NativeJobs > `"$log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n", (New-Object System.Text.UTF8Encoding($false)))
    & cmd.exe /d /c $batch
    $code = $LASTEXITCODE
    $summary = Get-ChildItem $output -Recurse -Filter "summary.txt" | Select-Object -First 1
    if ($summary) { Copy-Item $summary.FullName (Join-Path $ResultsDir "native-gui-summary.txt") -Force }
    $last = if ($summary) { (Get-Content $summary.FullName | Select-Object -Last 3) -join " " } else { "no summary" }
    if ($code -ne 0) { throw "exit ${code}: $last" }
    $last
}

$json = $results | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $ResultsDir "agent-gui-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
$failed = @($results | Where-Object Status -eq "FAIL").Count
Write-Host ("{0} agent desktop checks: {1} failed" -f $results.Count, $failed)
exit ([int]($failed -gt 0))
