# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Windows checks for Angelo's application identity: build, run, package, install side by side.

.DESCRIPTION
  Runs steps 1-4 of "Windows validation" in doc/angelo/app_identity.rst on a Windows 10/11 or Windows
  Server x64 machine and prints PASS / FAIL / SKIP per check. It has NOT been run yet (it was written on
  macOS): expect to fix small things on first use, and read its output rather than trusting the summary.

  The checks that need a visible desktop and a GPU (splash and About text, taskbar grouping, Explorer
  thumbnails, "Open with") are not scripted: do them by hand over RDP afterwards.

  Prerequisites you install yourself (nothing is downloaded or installed here):
    - Visual Studio 2022 (or its Build Tools) with the C++ desktop workload and a Windows SDK
    - Git and Git LFS, CMake on PATH
    - WiX Toolset 3.x (candle.exe/light.exe) on PATH, for the MSI
    - The repository already cloned at the commit to validate, with working access (credentials are
      yours; this script never asks for or stores a token)
  Optional: a stock Blender 5.2 installer (MSI) for the side-by-side checks (-StockBlenderMsi).

.EXAMPLE
  .\scripts\angelo\windows_validate.ps1 -StockBlenderMsi C:\installers\blender-5.2.2-windows-x64.msi
#>
param(
    [string]$SourceDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$BuildTarget = "lite",
    # Extra make.bat options, e.g. 2022b to use the Visual Studio 2022 *Build Tools* instead of the IDE.
    [string]$MakeArgs = "",
    [string]$StockBlenderMsi = "",
    [switch]$SkipBuild,
    # The Windows libraries are already in lib\windows_x64 (a git clone, as make.bat expects).
    [switch]$SkipLibs,
    # Where to write results.json and copy the MSI (for unattended runs); empty = do not.
    [string]$ResultsDir = "",
    [switch]$KeepInstalled
)

$ErrorActionPreference = "Stop"
# A tree a send made must be exactly the commit it was sent (windows_source_send.ps1); elsewhere nothing is checked.
. (Join-Path $SourceDir "scripts\angelo\windows_source_send.ps1"); Assert-SentTree $SourceDir
$results = New-Object System.Collections.ArrayList

function Add-Result([string]$Name, [string]$Status, [string]$Detail = "") {
    [void]$results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Detail = $Detail })
    $color = @{ PASS = "Green"; FAIL = "Red"; SKIP = "Yellow" }[$Status]
    Write-Host ("{0,-5} {1}  {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Test-Check([string]$Name, [scriptblock]$Body) {
    try {
        $detail = & $Body
        Add-Result $Name "PASS" ([string]$detail)
    } catch {
        Add-Result $Name "FAIL" $_.Exception.Message
    }
}

function Get-Identity {
    Get-Content (Join-Path $SourceDir "scripts\modules\angelo_identity.json") -Raw | ConvertFrom-Json
}

$identity = Get-Identity
$exe = $identity.windows.exe_stem + ".exe"
$launcher = $identity.windows.exe_stem + $identity.windows.launcher_suffix + ".exe"
$version = $identity.version.label
$msiName = "{0}-{1}-windows-x64.msi" -f $identity.product.name, $version
$appData = $env:APPDATA
$blenderFoundation = Join-Path $appData "Blender Foundation"

Set-Location $SourceDir

# --- 1. Libraries and build -------------------------------------------------------------------------
if (-not $SkipBuild) {
    Test-Check "tools: git, git-lfs, cmake, WiX" {
        foreach ($tool in "git", "git-lfs", "cmake", "candle", "light") {
            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not on PATH" }
        }
        "found"
    }
    if ($SkipLibs) {
        Test-Check "windows libraries (lib/windows_x64) present" {
            if (-not (Test-Path (Join-Path $SourceDir "lib\windows_x64\.git"))) { throw "lib\windows_x64\.git is missing" }
            "present"
        }
    } else {
        Test-Check "windows libraries (lib/windows_x64)" {
            # Blender's own server supplies these; the subset of this repo's LFS files is small.
            $env:GIT_LFS_SKIP_SMUDGE = "1"
            git -c submodule.lib/windows_x64.update=checkout submodule update --init lib/windows_x64 | Out-Null
            git -C lib/windows_x64 lfs install --local --skip-repo | Out-Null
            git -C lib/windows_x64 lfs pull | Out-Null
            git lfs pull --include="assets/**,release/datafiles/**,scripts/startup/bl_app_templates_system/**,release/windows/**,release/branding/**" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "git lfs pull failed" }
            "ok"
        }
    }
    Test-Check "toolchain diagnostics" {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
        if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found: Visual Studio / Build Tools are not installed" }
        $found = & $vswhere -all -prerelease -products * -format json | ConvertFrom-Json
        $text = $found | ForEach-Object { "{0} {1} at {2}" -f $_.displayName, $_.installationVersion, $_.installationPath }
        Write-Host ($text -join "`n")
        if (-not $found) { throw "vswhere lists no Visual Studio installation" }
        $text -join "; "
    }
    Test-Check "make.bat $BuildTarget $MakeArgs" {
        # Every MSI an install check accepts must name its commit (msi_commit.py: the hash the program reports), so
        # the build carries Blender's build info whatever the target: blender_lite.cmake forces WITH_BUILDINFO off.
        # make.bat applies its target's cache script only when it configures (nobuild); the option set after it
        # stays, and the build that follows does not configure again.
        cmd /c "make.bat $BuildTarget $MakeArgs nobuild verbose" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "make.bat (configure) exited with $LASTEXITCODE" }
        $configured = Get-ChildItem (Join-Path $SourceDir "..") -Directory -Filter "build_windows_*" |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        cmake $configured.FullName -DWITH_BUILDINFO=ON | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "cmake -DWITH_BUILDINFO=ON exited with $LASTEXITCODE" }
        cmd /c "make.bat $BuildTarget $MakeArgs verbose" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "make.bat exited with $LASTEXITCODE" }
        "built with build info"
    }
}

$buildDir = Get-ChildItem (Join-Path $SourceDir "..") -Directory -Filter "build_windows_*" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $buildDir) { Add-Result "build directory" "FAIL" "no ..\build_windows_* folder next to the source"; $buildDir = $null }
$bin = if ($buildDir) { Join-Path $buildDir.FullName "bin\Release" } else { "" }

# --- 2. The built program ---------------------------------------------------------------------------
Test-Check "executables are Angelo's, not Blender's" {
    foreach ($name in $exe, $launcher) {
        if (-not (Test-Path (Join-Path $bin $name))) { throw "$name missing in $bin" }
    }
    foreach ($name in "blender.exe", "blender-launcher.exe") {
        if (Test-Path (Join-Path $bin $name)) { throw "$name must not exist" }
    }
    "$exe + $launcher"
}
Test-Check "version resource" {
    $info = (Get-Item (Join-Path $bin $exe)).VersionInfo
    if ($info.CompanyName -ne $identity.vendor.name) { throw "CompanyName is '$($info.CompanyName)'" }
    if ($info.ProductName -ne $identity.product.name) { throw "ProductName is '$($info.ProductName)'" }
    if ($info.FileVersion -notlike "$version*") { throw "FileVersion is '$($info.FileVersion)'" }
    if ($info.Comments -notlike "Based on Blender*") { throw "Comments is '$($info.Comments)'" }
    "{0} / {1} / {2}" -f $info.CompanyName, $info.ProductName, $info.FileVersion
}
Test-Check "default folders (background run)" {
    $script = Join-Path $env:TEMP "angelo_identity_probe.py"
    @"
import bpy, json
print("ANGELO_PROBE" + json.dumps({"config": bpy.utils.user_resource("CONFIG", create=True),
    "cache": bpy.app.cachedir, "tmp": bpy.app.tempdir, "version": list(bpy.app.version)}))
"@ | Set-Content $script -Encoding ASCII
    $before = Test-Path $blenderFoundation
    $output = & (Join-Path $bin $exe) --background --factory-startup --python $script 2>&1 | Out-String
    $line = ($output -split "`n" | Where-Object { $_ -match "^ANGELO_PROBE" } | Select-Object -First 1)
    if (-not $line) { throw "the probe printed nothing: $output" }
    $probe = ($line -replace "^ANGELO_PROBE", "") | ConvertFrom-Json
    $name = $identity.paths.user_dir_name
    if ($probe.config -notlike (Join-Path $appData "$name\5.2\config")) { throw "config is $($probe.config)" }
    if ($probe.cache -notlike (Join-Path $env:LOCALAPPDATA "$name\Cache\*")) { throw "cache is $($probe.cache)" }
    if ((Split-Path $probe.tmp.TrimEnd("\") -Leaf) -notlike ($identity.paths.temp_prefix + "_*")) { throw "temp is $($probe.tmp)" }
    if ($probe.version[0] -ne 5 -or $probe.version[1] -ne 2) { throw "bpy.app.version is $($probe.version -join '.')" }
    if (-not $before -and (Test-Path $blenderFoundation)) { throw "Angelo created $blenderFoundation" }
    $probe.config
}

# --- 3. The installer -------------------------------------------------------------------------------
$msi = $null
Test-Check "cpack -G WIX -> $msiName" {
    if (-not $buildDir) { throw "no build directory" }
    # No MSI from a build without build info: nothing could tie it to a commit (msi_commit.py refuses it anyway).
    if (-not (Select-String -Path (Join-Path $buildDir.FullName "CMakeCache.txt") -Pattern "^WITH_BUILDINFO:BOOL=ON$" -Quiet)) {
        throw "the build has no build info (WITH_BUILDINFO is not ON in its CMakeCache.txt): its MSI could not name its commit"
    }
    # An MSI left by an earlier run (another commit's build) must never stand in for this one: removed first, and
    # cpack's own exit code decides.
    Get-ChildItem $buildDir.FullName -Filter "*.msi" -ErrorAction SilentlyContinue | Remove-Item -Force
    Push-Location $buildDir.FullName
    try { cpack -G WIX | Out-Host; $packed = $LASTEXITCODE } finally { Pop-Location }
    $msi = Get-ChildItem $buildDir.FullName -Filter "*.msi" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($packed -ne 0 -or -not $msi) {
        throw "no MSI produced: cpack exited with $packed (if light.exe reports an unresolved file id, see 'Windows validation' step 3)"
    }
    if ($msi.Name -ne $msiName) { throw "MSI is named $($msi.Name)" }
    # The proof install checks read (msi_commit.py): this file, its bytes, the commit sent (proven) and the hash the
    # built program reports, which must be that commit's.
    $sent = Get-SentCommit $SourceDir
    $reported = @(& (Join-Path $bin $exe) --factory-startup --version) | ForEach-Object { if ($_ -match "build hash:\s*(\S+)") { $Matches[1] } } | Select-Object -First 1
    if ($sent -and (-not $reported -or -not $sent.StartsWith("$reported"))) { throw "the build reports hash '$reported', not the commit sent ($sent)" }
    $manifest = [ordered]@{ kind = "angelo-windows-identity-build"; source_commit = "$sent"; build_hash = "$reported"
        msi = [ordered]@{ name = $msi.Name; bytes = $msi.Length; sha256 = (Get-FileHash $msi.FullName -Algorithm SHA256).Hash.ToLowerInvariant() } }
    $script:msiManifest = Join-Path $buildDir.FullName "angelo-identity-build.json"
    [System.IO.File]::WriteAllText($script:msiManifest, ($manifest | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
    $script:msiPath = $msi.FullName
    $msi.FullName
}

# --- 4. Installing: Angelo alone, then side by side with a stock Blender ---------------------------------
# This machine is a test machine: each scenario starts from a clean slate (both products removed and the
# .blend registrations they leave behind cleared), so one run never depends on the last one.
$prog = $identity.windows.app_user_model_id
$installDir = Join-Path $env:ProgramFiles $identity.windows.install_folder
$probeScript = Join-Path $env:TEMP "angelo_identity_probe.py"
$angeloLog = Join-Path $env:TEMP "angelo-msi.log"

function Get-ClassState([string]$Label) {
    # Registry views of the .blend type: machine and user separately, and what Windows resolves.
    $read = {
        param($hive)
        $key = "Registry::$hive\SOFTWARE\Classes\.blend"
        $default = (Get-ItemProperty $key -ErrorAction SilentlyContinue).'(default)'
        $with = Get-Item "$key\OpenWithProgids" -ErrorAction SilentlyContinue
        [pscustomobject]@{ Default = [string]$default; With = @(if ($with) { $with.GetValueNames() | Where-Object { $_ } }) }
    }
    $machine = & $read "HKEY_LOCAL_MACHINE"
    $user = & $read "HKEY_CURRENT_USER"
    $resolved = if ($user.Default) { $user.Default } else { $machine.Default }
    Write-Host ("registry [{0}] machine default='{1}' with=[{2}]; user default='{3}' with=[{4}]" -f
        $Label, $machine.Default, ($machine.With -join ","), $user.Default, ($user.With -join ","))
    [pscustomobject]@{ Machine = $machine; User = $user; Resolved = $resolved; With = @($machine.With + $user.With) }
}
function Invoke-Msi([string]$Verb, [string]$Package, [string]$Log = "") {
    # ALLUSERS=1 installs for every user, in Program Files, as these checks expect (Angelo's MSI installs for the
    # current user by default; a stock Blender MSI is per machine anyway).
    $arguments = "/$Verb `"$Package`"" + $(if ($Verb -eq "i") { " ALLUSERS=1" } else { "" }) + " /qn /norestart" +
        $(if ($Log) { " /l*v `"$Log`"" } else { "" })
    $p = Start-Process msiexec.exe -ArgumentList $arguments -Wait -PassThru
    if ($p.ExitCode -notin 0, 1605, 3010) { throw "msiexec /$Verb exited with $($p.ExitCode)" }   # 1605: not installed
    $p.ExitCode
}
function Reset-TestMachine {
    if ($script:msiPath) { [void](Invoke-Msi "x" $script:msiPath) }
    if ($StockBlenderMsi) { [void](Invoke-Msi "x" $StockBlenderMsi) }
    foreach ($hive in "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER") {
        $key = "Registry::$hive\SOFTWARE\Classes\.blend"
        if (Test-Path $key) {
            Set-ItemProperty $key -Name "(default)" -Value "" -ErrorAction SilentlyContinue
            foreach ($name in $prog, "blender.5.2") { Remove-ItemProperty "$key\OpenWithProgids" -Name $name -ErrorAction SilentlyContinue }
        }
    }
}
function Save-Evidence([string]$Name, [string[]]$Lines) {
    if ($ResultsDir) {
        New-Item -ItemType Directory -Force $ResultsDir | Out-Null
        $Lines | Set-Content (Join-Path $ResultsDir $Name) -Encoding UTF8
    }
}
function Assert-AngeloUnregistered([string]$Log) {
    $state = Get-ClassState "after uninstall"
    $problems = @()
    if (Test-Path "Registry::HKEY_CLASSES_ROOT\$prog") { $problems += "ProgID $prog is still registered" }
    if ($state.With -contains $prog) { $problems += "$prog is still in .blend OpenWithProgids" }
    if ($state.Resolved -eq $prog) { $problems += ".blend default is still $prog" }
    if (Test-Path $Log) {
        Copy-Item $Log (Join-Path $(if ($ResultsDir) { $ResultsDir } else { $env:TEMP }) (Split-Path $Log -Leaf)) -Force -ErrorAction SilentlyContinue
        $lines = Select-String -Path $Log -Pattern "UnRegisterAngelo|RegisterAngelo|unregister-allusers|Custom action|CustomAction|RemoveFiles" |
            Select-Object -First 12 | ForEach-Object { $_.Line.Trim() }
        Write-Host ("MSI uninstall log excerpt:`n" + ($lines -join "`n"))
        if ($problems) { $problems += "MSI log: " + (($lines | Select-Object -First 4) -join " / ") }
    }
    if ($problems) { throw ($problems -join "; ") }
}
function Get-BlenderSnapshot {
    # Under the folders a stock Blender install owns: files by path, size and time; folders by path only. A folder's own
    # time moves whenever Windows finishes an install (it changed on 771 folders, none of them a file, within 18 s of the
    # baseline in a real run), and an added or removed entry already shows up as a path. Equal before and after = untouched.
    foreach ($folder in $blenderFoundation, (Join-Path $env:ProgramFiles "Blender Foundation")) {
        if (Test-Path $folder) {
            Get-ChildItem $folder -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
                if ($_.PSIsContainer) { "{0}|dir" -f $_.FullName }
                else { "{0}|{1}|{2}" -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.Ticks }
            }
        }
    }
}
function Wait-BlenderSettle([int]$MaxSeconds = 180) {
    # A fresh install may still be finishing; take the baseline only once two reads 15 s apart agree.
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    $previous = @(Get-BlenderSnapshot | Sort-Object)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 15
        $current = @(Get-BlenderSnapshot | Sort-Object)
        if (-not (Compare-Object $previous $current)) { return $current }
        $previous = $current
    }
    return $previous
}
function Get-InstallSnapshot {
    # The install folder by path, size and time (folders by path): a launch must leave it exactly as installed, or MSI
    # repair and updates meet files they do not own (doc/angelo/releasing.rst, "Nothing is written inside the app").
    Get-ChildItem $installDir -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.PSIsContainer) { "{0}|dir" -f $_.FullName }
        else { "{0}|{1}|{2}" -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.Ticks }
    }
}
function Invoke-InstalledAddonProbe {
    # The add-on's startup and a child Python started as Blender's extension system starts one. The launch must
    # write bytecode, to the per-user cache: a launch that writes none anywhere would prove nothing.
    $script = Join-Path $env:TEMP "angelo_seal_probe.py"
    @"
import os, subprocess, sys
import addon_utils, bpy
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
child = subprocess.run([sys.executable, *bpy.app.python_args, "-c", "import json, email.mime.text, urllib.request"])
prefix = sys.pycache_prefix or ""
written = sum(name.endswith(".pyc") for _f, _d, names in os.walk(prefix) for name in names) if os.path.isabs(prefix) else 0
print("ANGELO_SEAL_PROBE", addon_utils.check("angelo_agent")[1], child.returncode, written, prefix)
"@ | Set-Content $script -Encoding ASCII
    $output = & (Join-Path $installDir $exe) --background --python $script 2>&1 | Out-String
    $line = ($output -split "`n" | Where-Object { $_ -match "^ANGELO_SEAL_PROBE" } | Select-Object -First 1)
    if ($line -notmatch "^ANGELO_SEAL_PROBE True 0 (\d+) (.+)$") { throw "the add-on probe failed: $output" }
    if ([int]$Matches[1] -lt 1) { throw "the launch wrote no bytecode to its cache ($($Matches[2].Trim())): it proves nothing" }
    if ($Matches[2].Trim().StartsWith($installDir, [System.StringComparison]::OrdinalIgnoreCase)) { throw "the bytecode cache is inside the install folder" }
    $line.Trim()
}
function Invoke-InstalledProbe {
    $output = & (Join-Path $installDir $exe) --background --factory-startup --python $probeScript 2>&1 | Out-String
    if ($output -notmatch "ANGELO_PROBE") { throw "the installed program printed no probe: $output" }
}

if ($script:msiPath) {
    Test-Check "scenario A: Angelo alone, install" {
        Reset-TestMachine
        $null = Get-ClassState "clean slate"
        [void](Invoke-Msi "i" $script:msiPath $angeloLog)
        if (-not (Test-Path (Join-Path $installDir $launcher))) { throw "$launcher not found in $installDir" }
        $state = Get-ClassState "Angelo installed"
        if (-not (Test-Path "Registry::HKEY_CLASSES_ROOT\$prog\shell\open\command")) { throw "ProgID $prog is not registered" }
        if ($state.With -notcontains $prog) { throw "$prog missing from .blend OpenWithProgids" }
        if ($state.Resolved -ne $prog) { throw "nothing owned .blend and Angelo did not take it ('$($state.Resolved)')" }
        "default: '$($state.Resolved)'"
    }
    Test-Check "scenario A: the installed program runs" { Invoke-InstalledProbe; "ran" }
    Test-Check "scenario A: launches write nothing in the install folder" {
        $before = @(Get-InstallSnapshot | Sort-Object)
        Invoke-InstalledProbe
        $probe = Invoke-InstalledAddonProbe
        $after = @(Get-InstallSnapshot | Sort-Object)
        $changed = @(Compare-Object $before $after)
        Save-Evidence "install-folder-launch-diff.txt" ($changed | ForEach-Object { $_.SideIndicator + " " + $_.InputObject })
        if ($changed) { throw "a launch changed $($changed.Count) entries, e.g. $(($changed | Select-Object -First 3 | ForEach-Object { $_.SideIndicator + ' ' + $_.InputObject }) -join ' // ')" }
        "$($after.Count) entries unchanged; $probe"
    }
    Test-Check "scenario A: uninstall removes Angelo's .blend registration" {
        $log = Join-Path $env:TEMP "angelo-msi-uninstall-A.log"
        [void](Invoke-Msi "x" $script:msiPath $log)
        Assert-AngeloUnregistered $log
        "ProgID, Open With entry and default are gone"
    }

    if ($StockBlenderMsi -and (Test-Path $StockBlenderMsi)) {
        $script:stockInstalled = $false
        Test-Check "scenario B: stock Blender installed first" {
            Reset-TestMachine
            [void](Invoke-Msi "i" $StockBlenderMsi)
            $script:stockInstalled = $true
            $script:blenderBefore = @(Wait-BlenderSettle)     # its own install may still be finishing
            $script:stockState = Get-ClassState "stock Blender installed"
            "default: '$($script:stockState.Resolved)', $($script:blenderBefore.Count) entries"
        }
        Test-Check "scenario B: stock Blender's folders are quiet by themselves" {
            # Control: nothing of Angelo's has run yet. If the stock install keeps writing, the next check cannot blame Angelo.
            Start-Sleep -Seconds 45
            $quiet = @(Get-BlenderSnapshot | Sort-Object)
            $drift = @(Compare-Object $script:blenderBefore $quiet)
            Save-Evidence "stock-quiescence-diff.txt" ($drift | ForEach-Object { $_.SideIndicator + " " + $_.InputObject })
            if ($drift) { throw "the stock install changed $($drift.Count) entries on its own, e.g. $(($drift | Select-Object -First 3 | ForEach-Object { $_.InputObject }) -join ' // ')" }
            "$($quiet.Count) entries, unchanged for 45 s"
        }
        Test-Check "scenario B: Angelo installed beside it" {
            [void](Invoke-Msi "i" $script:msiPath $angeloLog)
            $state = Get-ClassState "both installed"
            if ($state.Resolved -ne $script:stockState.Resolved) { throw ".blend default changed from '$($script:stockState.Resolved)' to '$($state.Resolved)'" }
            if ($state.With -notcontains $prog) { throw "$prog missing from .blend OpenWithProgids" }
            "default stays '$($state.Resolved)'; Angelo is an Open With choice"
        }
        Test-Check "scenario B: Angelo's installer left the Blender folders untouched" {
            $now = @(Get-BlenderSnapshot | Sort-Object)
            $changed = @(Compare-Object $script:blenderBefore $now)
            Save-Evidence "after-angelo-install-diff.txt" ($changed | ForEach-Object { $_.SideIndicator + " " + $_.InputObject })
            if ($changed) { throw "changed by the installer ($($changed.Count)): $(($changed | Select-Object -First 3 | ForEach-Object { $_.SideIndicator + ' ' + $_.InputObject }) -join ' // ')" }
            "unchanged"
        }
        Test-Check "scenario B: Angelo runs and the Blender folders stay untouched" {
            Invoke-InstalledProbe
            $after = @(Get-BlenderSnapshot | Sort-Object)
            $changed = @(Compare-Object $script:blenderBefore $after)
            Save-Evidence "after-angelo-run-diff.txt" ($changed | ForEach-Object { $_.SideIndicator + " " + $_.InputObject })
            if ($changed) { throw "changed by the run ($($changed.Count)): $(($changed | Select-Object -First 4 | ForEach-Object { $_.SideIndicator + ' ' + $_.InputObject }) -join ' // ')" }
            "$($after.Count) entries unchanged"
        }
        Test-Check "scenario B: uninstalling Angelo leaves stock Blender working with its default" {
            $log = Join-Path $env:TEMP "angelo-msi-uninstall-B.log"
            [void](Invoke-Msi "x" $script:msiPath $log)
            $state = Get-ClassState "Angelo removed, stock remains"
            if ($state.Resolved -ne $script:stockState.Resolved) { throw ".blend default is '$($state.Resolved)', was '$($script:stockState.Resolved)'" }
            Assert-AngeloUnregistered $log
            $blender = Get-ChildItem "$env:ProgramFiles\Blender Foundation" -Recurse -Filter "blender.exe" | Select-Object -First 1
            if (-not $blender) { throw "stock blender.exe not found" }
            $version = & $blender.FullName --version | Select-Object -First 1
            if ($version -notlike "Blender*") { throw "unexpected --version: $version" }
            $version
        }
        if (-not $KeepInstalled) { Reset-TestMachine }
    } else {
        Add-Result "scenario B (side by side with stock Blender)" "SKIP" "pass -StockBlenderMsi to run it"
    }
} else {
    Add-Result "installer scenarios" "SKIP" "no MSI was produced"
}

# --- Summary ----------------------------------------------------------------------------------------
Write-Host ""
$results | Format-Table -AutoSize | Out-String | Write-Host
$failed = @($results | Where-Object { $_.Status -eq "FAIL" }).Count
if ($ResultsDir) {
    New-Item -ItemType Directory -Force $ResultsDir | Out-Null
    $results | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $ResultsDir "results.json") -Encoding UTF8
    if ($script:msiPath) { Copy-Item $script:msiPath, $script:msiManifest $ResultsDir -Force }
}
Write-Host ("{0} checks: {1} failed" -f $results.Count, $failed)
Write-Host "By hand next (needs the desktop and a GPU): splash/About text, taskbar group, Explorer thumbnails, 'Open with'."
exit ([int]($failed -gt 0))
