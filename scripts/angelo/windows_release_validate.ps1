# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The 'release' stage of windows_cloud_bootstrap.ps1: the full-feature build, its MSI and the BACKGROUND native suites.

.DESCRIPTION
  As the machine's administrator, without a signed-in desktop, it:
    1. parses every scripts\angelo\*.ps1 with PowerShell's own parser (a typo fails here, not hours later);
    2. installs CI's host Python (3.13 with numpy 2.3.4 and jsonschema 4.23.0) when missing, as the agent stage;
    3. runs build_release_windows.ps1 -Package: blender_release.cmake with every GPU device and its kernels,
       BlendThumb.dll, Angelo's identity and the agent runtime from -AgentRuntimeDir (ANGELO_AGENT_RUNTIME_DIR; the
       machine's bootstrap fetches it from gs://<bucket>/agent-runtime/), then cpack -G WIX;
    4. checks the MSI's agent runtime and runs it from the unpacked MSI (windows_agent_runtime_check.ps1: the pinned
       files byte for byte, OpenAI's signatures kept, --version, the app-server schema and initialize);
     then the seal check on the build's install folder (release_launch_checks.py seal --install): a headless
     launch with the add-on and both kinds of child Python must leave it unchanged, before any suite runs it;
    5. runs the BACKGROUND native suites against that build's Angelo.exe, and reads test_blender_release_features'
       receipt: full_feature_build must be true (it also runs the bundled runtime the app finds);
  and writes release-results.json (PASS/FAIL per step), the build manifest, the logs and the MSI to -ResultsDir.
  No provider, account or credential is involved, and nothing is installed from the MSI.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$ResultsDir = "C:\angelo\results\release",
    [string]$BuildDir = "C:\angelo\build_windows_Release_x64_vc17_Release",
    [string]$AllowMissing = "",
    [string]$AgentRuntimeDir = $env:ANGELO_AGENT_RUNTIME_DIR,
    [int]$NativeJobs = 4
)
# Native programs' stderr is output here, never a terminating error: each step is judged by exit codes and reports.
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
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    # The toolkit installers set these machine-wide; a running process does not see them until it reads them again.
    foreach ($name in "CUDA_PATH", "HIP_PATH", "OPTIX_ROOT_DIR", "OCLOC_INSTALL_DIR") {
        $value = [Environment]::GetEnvironmentVariable($name, "Machine")
        if ($value) { Set-Item "env:$name" $value }
    }
}
function Invoke-Logged([string]$Log, [string]$Command) {
    $batch = Join-Path $env:TEMP ("angelo-step-{0}.cmd" -f [guid]::NewGuid().ToString("N"))
    [System.IO.File]::WriteAllText($batch, "@echo off`r`n$Command > `"$Log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n", (New-Object System.Text.UTF8Encoding($false)))
    & cmd.exe /d /c $batch
    $code = $LASTEXITCODE
    Remove-Item $batch -ErrorAction SilentlyContinue
    Get-Content $Log -Tail 15 -Encoding UTF8 -ErrorAction SilentlyContinue | Out-Host
    return $code
}

$python = "C:\Python313\python.exe"
$tests = Join-Path $SourceDir "tests\python\angelo_agent"
$env:PYTHONUTF8 = "1"
Refresh-Path

Test-Check "release: every scripts\angelo\*.ps1 parses" {
    $bad = @()
    foreach ($file in Get-ChildItem (Join-Path $SourceDir "scripts\angelo") -Filter "*.ps1") {
        $tokens = $null; $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
        if ($errors) { $bad += "{0}: {1} (line {2})" -f $file.Name, $errors[0].Message, $errors[0].Extent.StartLineNumber }
    }
    if ($bad) { throw ($bad -join "; ") }
    "parsed"
}
Test-Check "release: host Python 3.13 with numpy and jsonschema (as CI)" {
    if (-not (Test-Path $python)) {
        & choco install -y --no-progress python313 --params "/InstallDir:C:\Python313" | Out-Host
        Refresh-Path
    }
    if (-not (Test-Path $python)) { throw "Python 3.13 did not install at $python" }
    & $python -m pip install --disable-pip-version-check -q numpy==2.3.4 jsonschema==4.23.0 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "pip exited with $LASTEXITCODE" }
    (& $python -c "import sys; print(sys.version.split()[0])")
}
Test-Check "release: free disk for the full build" {
    $free = [math]::Floor((Get-PSDrive ((Split-Path $BuildDir -Qualifier).TrimEnd(":"))).Free / 1GB)
    $built = Test-Path (Join-Path $BuildDir "CMakeCache.txt")
    # A first full build with every GPU kernel needs room for its objects; an incremental one far less.
    $need = if ($built) { 30 } else { 120 }
    if ($free -lt $need) { throw "$free GB free on the build drive, $need GB needed" }
    "$free GB free"
}

Test-Check "release: the source tree is exactly the sent commit (nothing untracked)" {
    # A folder install ships every file in the tree, so a file git does not track would ship (shipped_sources.py).
    $out = & $python (Join-Path $SourceDir "scripts\angelo\release_checks.py") source-clean $SourceDir --commit "$env:ANGELO_SRC_COMMIT" 2>&1
    if ($LASTEXITCODE -ne 0) { throw (($out | Select-Object -First 4) -join " / ") }
    if (-not $env:ANGELO_SRC_COMMIT) { throw "ANGELO_SRC_COMMIT is not set: the sent commit is unknown" }
    "$env:ANGELO_SRC_COMMIT"
}

Test-Check "release: the release's own notes are dated (CHANGELOG.md and release_notes.json)" {
    # What's New draws an undated entry as "Unreleased" (release_dated.py); refuse before the build, not at the feed.
    $out = & $python (Join-Path $SourceDir "scripts\angelo\release_checks.py") release-dated $SourceDir 2>&1
    if ($LASTEXITCODE -ne 0) { throw (($out | Select-Object -First 4) -join " / ") }
    ($out | Select-Object -Last 1)
}

$build = Join-Path $SourceDir "scripts\angelo\build_release_windows.ps1"
$exe = Join-Path $BuildDir "bin\Release\Angelo.exe"
$buildOk = $false
Test-Check "release: build_release_windows.ps1 -Package (blender_release, every GPU device, MSI)" {
    if (@($results | Where-Object Status -eq "FAIL").Count) { throw "skipped: an earlier check failed" }
    $allow = if ($AllowMissing) { " -AllowMissing `"$AllowMissing`"" } else { "" }
    if ($AgentRuntimeDir) { $allow += " -AgentRuntimeDir `"$AgentRuntimeDir`"" }
    $code = Invoke-Logged (Join-Path $ResultsDir "release-build.log") ("powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$build`" " +
        "-SourceDir `"$SourceDir`" -BuildDir `"$BuildDir`" -Package -ResultsDir `"$ResultsDir`"$allow")
    if ($code -ne 0) { throw "exit $code (release-build.log, angelo-configure.log, angelo-build.log)" }
    $script:buildOk = $true
    $manifest = Get-Content (Join-Path $ResultsDir "angelo-release-build.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    "{0}, {1} MB, {2} min, release equivalent: {3}" -f $manifest.msi.name, [math]::Round($manifest.msi.bytes / 1MB), $manifest.minutes, $manifest.release_equivalent
}
Test-Check "release: the MSI's agent runtime, run from the unpacked MSI (windows_agent_runtime_check.ps1)" {
    if (-not $buildOk) { throw "no full build" }
    $manifest = Get-Content (Join-Path $ResultsDir "angelo-release-build.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $manifest.agent_runtime.files) { return "none bundled: $($manifest.agent_runtime.folder)" }
    $msi = Join-Path $BuildDir $manifest.msi.name
    $report = Join-Path $ResultsDir "agent-runtime-check.json"
    $check = Join-Path $SourceDir "scripts\angelo\windows_agent_runtime_check.ps1"
    # A release build (the runtime required) carries the marker that makes the app refuse any other Codex.
    $release = if (@($manifest.allowed_missing) -contains "agent-runtime") { "" } else { " -Release" }
    $code = Invoke-Logged (Join-Path $ResultsDir "agent-runtime-check.log") ("powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$check`" " +
        "-Msi `"$msi`" -SourceRuntimeDir `"$($manifest.agent_runtime.folder)`"$release -Report `"$report`"")
    if ($code -ne 0) { throw "exit $code (agent-runtime-check.log)" }
    $data = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    "{0}; {1} files; initialize: {2}" -f $data.version, @($data.files.PSObject.Properties).Count, $data.user_agent
}
Test-Check "release: a launch writes nothing in the install folder (release_launch_checks.py seal)" {
    if (-not $buildOk -or -not (Test-Path $exe)) { throw "no full build" }
    $check = Join-Path $SourceDir "scripts\angelo\release_launch_checks.py"
    $report = Join-Path $ResultsDir "seal-check.json"
    $code = Invoke-Logged (Join-Path $ResultsDir "seal-check.log") "`"$python`" `"$check`" seal --install `"$(Split-Path $exe)`" --report `"$report`""
    if ($code -ne 0) { throw "exit $code (seal-check.log, seal-check.json)" }
    $data = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    "{0} launch(es), nothing changed; bytecode in {1}" -f @($data.launches).Count, $data.launches[0].pycache_prefix
}
Test-Check "release: BACKGROUND native suites on the full build" {
    if (-not $buildOk -or -not (Test-Path $exe)) { throw "no full build" }
    # A machine with no display adapter but Windows' own has no working OpenGL: the suites that need one are skipped
    # (never passed) and must pass on a GPU machine instead (native_suite_manifest needs_opengl).
    $gpus = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "Microsoft (Basic Display|Basic Render|Remote Display|Hyper-V)" })
    if (-not $gpus) { $env:ANGELO_QA_NO_OPENGL = "1" } else { Remove-Item env:ANGELO_QA_NO_OPENGL -ErrorAction SilentlyContinue }
    $output = "C:\angelo\native-release"
    Remove-Item $output -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $output | Out-Null
    $code = Invoke-Logged (Join-Path $ResultsDir "native-background.log") "`"$python`" `"$(Join-Path $tests 'run_native_suites.py')`" --binary `"$exe`" --jobs $NativeJobs --timeout-scale 2 --output `"$output`""
    $summary = Get-ChildItem $output -Recurse -Filter "summary.txt" | Select-Object -First 1
    if ($summary) { Copy-Item $summary.FullName (Join-Path $ResultsDir "native-background-summary.txt") -Force }
    # Each failed suite's reports and logs come back with the results, to diagnose without the machine.
    if ($summary) {
        foreach ($line in Get-Content $summary.FullName) {
            if ($line -match "^FAILED\s+\S+\s+\S+\s+(test_blender_\w+)") {
                $into = Join-Path $ResultsDir ("native-failed\" + $Matches[1])
                New-Item -ItemType Directory -Force $into | Out-Null
                $suite = Join-Path $summary.Directory.FullName $Matches[1]
                Copy-Item (Join-Path $suite "suite.log") $into -Force -ErrorAction SilentlyContinue
                Get-ChildItem (Join-Path $suite "qa") -Recurse -File -Include *.json, *.log, *.txt -ErrorAction SilentlyContinue |
                    Where-Object Length -lt 5MB | Copy-Item -Destination $into -Force
            }
        }
    }
    $receipt = Get-ChildItem $output -Recurse -Filter "release-features.json" | Select-Object -First 1
    if ($receipt) { Copy-Item $receipt.FullName (Join-Path $ResultsDir "release-features.json") -Force }
    $last = if ($summary) { (Get-Content $summary.FullName | Select-Object -Last 3) -join " " } else { "no summary" }
    if ($code -ne 0) { throw "exit ${code}: $last" }
    $last
}
Test-Check "release: the build reports every release feature (test_blender_release_features)" {
    $receipt = Join-Path $ResultsDir "release-features.json"
    if (-not (Test-Path $receipt)) { throw "no release-features.json from the native suites" }
    $data = Get-Content $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
    $missing = @($data.options.PSObject.Properties | Where-Object { -not $_.Value } | ForEach-Object Name)
    if (-not $data.full_feature_build) { throw "full_feature_build is false: $($missing -join ', ')" }
    "full_feature_build: true ($($data.version))"
}

$json = $results | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $ResultsDir "release-results.json"), $json, (New-Object System.Text.UTF8Encoding($true)))
$failed = @($results | Where-Object Status -eq "FAIL").Count
Write-Host ("{0} release checks: {1} failed" -f $results.Count, $failed)
exit ([int]($failed -gt 0))
