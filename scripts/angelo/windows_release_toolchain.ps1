# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Functions of the Windows release build, dot-sourced by build_release_windows.ps1 (and, for the build folder's name,
  by windows_release_validate.ps1 and windows_final_runs.ps1). It defines functions only and runs nothing.

.DESCRIPTION
  The toolset (doc/angelo/native_editor_build.rst, "Toolset: clang-cl"): the Windows release builds with Visual
  Studio 2022's ClangCL platform toolset, so clang-cl compiles and lld-link links, as official Blender's Windows
  release does; Blender's CMake then gives it the same flags (platform_win32.cmake, MSVC_CLANG). Configuring needs
  the developer environment (vcvars64.bat), as make.bat's own call gives it: platform_win32.cmake reads the MSVC
  redistributables' folder from VCToolsRedistDir and finds cl.exe on PATH as CUDA's host compiler.
  CMake cannot change the toolset of an existing cache, so the build folder's name carries the toolset and a folder
  configured with another toolset is refused before anything is written to it.
#>

. (Join-Path $PSScriptRoot "windows_native_call.ps1")   # Invoke-NativeCapture: a program's error output under Stop.

# Visual Studio's MSBuild platform toolset the release builds with (CMake's -T) and the components that provide it.
$AngeloReleaseToolset = "ClangCL"
$AngeloClangComponents = @("Microsoft.VisualStudio.Component.VC.Llvm.Clang", "Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset")

# make.bat's name for a release build folder next to the source, with the toolset and the Blender series in it: a
# series has its own libraries and most of its sources differ, so a shared folder would rebuild everything each way.
function Get-AngeloReleaseBuildDir([string]$SourceDir) {
    Join-Path (Split-Path $SourceDir -Parent) ("build_windows_Release_x64_vc17_{0}_Release_{1}" -f $AngeloReleaseToolset.ToLowerInvariant(), (Get-AngeloBlenderSeries $SourceDir))
}

# The app's per-version folder ("5.3"), from the source's BKE_blender_version.h (as source_release.py reads it), so no
# script hard-codes the Blender series a build ships.
function Get-AngeloBlenderSeries([string]$SourceDir) {
    $header = Join-Path $SourceDir "source\blender\blenkernel\BKE_blender_version.h"
    $line = Select-String -Path $header -Pattern "^#define BLENDER_VERSION\s+(\d+)\s*$" | Select-Object -First 1
    if (-not $line) { throw "no BLENDER_VERSION in $header" }
    $full = [int]$line.Matches[0].Groups[1].Value
    "{0}.{1}" -f [math]::Floor($full / 100), ($full % 100)
}

# A build folder already configured with another toolset (an empty one is the default, MSVC's v143) is refused.
function Assert-BuildDirToolset([string]$BuildDir) {
    $cache = Join-Path $BuildDir "CMakeCache.txt"
    if (-not (Test-Path $cache)) { return }
    $line = Select-String -Path $cache -Pattern "^CMAKE_GENERATOR_TOOLSET:INTERNAL=(.*)$" | Select-Object -First 1
    $have = if ($line) { $line.Matches[0].Groups[1].Value.Trim() } else { "" }
    if ($have -ne $AngeloReleaseToolset) {
        throw ("$BuildDir was configured with the toolset '{0}', not ${AngeloReleaseToolset}: CMake cannot switch the toolset " +
            "of an existing cache. Build in a new folder (the default name carries the toolset).") -f $(if ($have) { $have } else { "v143 (MSVC)" })
    }
}

# A build folder configured against other libraries keeps what CMake found in them and never looks again when LIBDIR
# changes, so after a library update (a Blender bump) the build would compile and link against the old ones (macOS's
# 1.1 rehearsal: an executable dyld refused). When the folder or the commit of the libraries differs from the last
# configure's (angelo-libdir.txt, written by Confirm-BuildDirLibraries), the cache moves aside; compiled objects stay. A
# folder from before that record compares the folder alone.
function Reset-BuildDirForLibraries([string]$BuildDir, [string]$LibDir, [string]$LibCommit) {
    $cache = Join-Path $BuildDir "CMakeCache.txt"
    if (-not (Test-Path $cache)) { return }
    $line = Select-String -Path $cache -Pattern "^LIBDIR:[A-Z]*=(.*)$" | Select-Object -First 1
    $cachedLib = if ($line) { $line.Matches[0].Groups[1].Value.Trim() } else { "" }
    $record = Join-Path $BuildDir "angelo-libdir.txt"
    $recorded = if (Test-Path $record) { @(Get-Content $record) } else { @() }
    $normal = { param($path) $path.Replace('\', '/').TrimEnd('/').ToLowerInvariant() }
    $stale = ""
    if ($cachedLib -and (& $normal $cachedLib) -ne (& $normal $LibDir)) { $stale = "configured with $cachedLib" }
    elseif ($recorded.Count -ge 2 -and $recorded[1] -and $recorded[1] -ne $LibCommit) {
        $stale = "configured with the libraries at {0}, now {1}" -f $recorded[1].Substring(0, [Math]::Min(12, $recorded[1].Length)), $LibCommit
    }
    if ($stale) {
        Move-Item -Force $cache (Join-Path $BuildDir "CMakeCache.txt.libdir-changed")
        Write-Host "The build folder was ${stale}: its CMake cache moved to CMakeCache.txt.libdir-changed; configuring anew."
    }
}

# After the configure: refuses when the libraries are not at the source's pin or the cache still names another library
# checkout (a folder named like LibDir, such as another windows_x64), naming them; otherwise records them.
function Confirm-BuildDirLibraries([string]$BuildDir, [string]$LibDir, [string]$LibCommit, [string]$Pinned) {
    if ($Pinned -and $LibCommit -ne $Pinned) { throw "The libraries in $LibDir are at $LibCommit; this source pins $Pinned. Check out the pinned libraries." }
    $normal = { param($path) $path.Replace('\', '/').TrimEnd('/').ToLowerInvariant() }
    $root = & $normal (Resolve-Path $LibDir).Path
    $name = (Split-Path $LibDir -Leaf).ToLowerInvariant()
    $others = @(Get-Content (Join-Path $BuildDir "CMakeCache.txt") | Where-Object { $_ -match "^[^#/][^:=]*:[A-Z]*=" } | ForEach-Object {
        $key, $value = $_ -split "=", 2
        foreach ($item in ($value -split '[;\[\]"]')) {   # lists, and FIND_PACKAGE_MESSAGE_DETAILS' [path][path]
            $path = & $normal $item
            $at = $path.IndexOf("/$name/"); if ($at -lt 0 -and $path.EndsWith("/$name")) { $at = $path.Length - $name.Length - 1 }
            if ($at -ge 0 -and $path.Substring(0, $at + $name.Length + 1) -ne $root) { "  $($key.Split(':')[0]) = $item" }
        }
    })
    if ($others) { throw ("This configure still names libraries outside ${LibDir}:`n" + (($others | Select-Object -First 20) -join "`n")) }
    [System.IO.File]::WriteAllText((Join-Path $BuildDir "angelo-libdir.txt"), "$LibDir`n$LibCommit`n", (New-Object System.Text.UTF8Encoding($false)))
}

# cmd.exe writes a program's own bytes to the log (PowerShell 5 would re-encode them), and a program's stderr cannot
# end this script: each step is judged by its exit code. The tail is echoed for the transcript.
# MSBuild projects built at once when -Jobs is not given: one per 4 cores and per 8 GB of memory, 1 to 16, so a
# smaller VM does not run out of memory (each project runs clang-cl on every core; links and nvcc are the peaks).
# Cores are the machine's logical processors in every processor group: .NET's ProcessorCount sees only this process's
# group (44 of build4's 176), which gave 11 jobs there instead of 16.
function Get-DefaultBuildJobs {
    $system = Get-CimInstance Win32_ComputerSystem
    $cores = [int]$system.NumberOfLogicalProcessors
    if ($cores -lt 1) { $cores = [Environment]::ProcessorCount }
    $memoryGb = [math]::Floor($system.TotalPhysicalMemory / 1GB)
    return [int][math]::Max(1, [math]::Min(16, [math]::Min([math]::Floor($cores / 4), [math]::Floor($memoryGb / 8))))
}

function Invoke-Logged([string]$Log, [string]$Command) {
    $batch = Join-Path $env:TEMP ("angelo-release-{0}.cmd" -f [guid]::NewGuid().ToString("N"))
    [System.IO.File]::WriteAllText($batch, "@echo off`r`n$Command > `"$Log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n", (New-Object System.Text.UTF8Encoding($false)))
    & cmd.exe /d /c $batch
    $code = $LASTEXITCODE
    Remove-Item $batch -ErrorAction SilentlyContinue
    Get-Content $Log -Tail 25 -Encoding UTF8 -ErrorAction SilentlyContinue | Out-Host
    return $code
}

# Each pinned model-provider wire of the agent runtime (agent_runtime.json wire_apis), offline: `codex debug models` with
# a provider on it (no request, no key); a build without the wire refuses the setting. Bare values: Codex reads a -c
# value that is not TOML as a string, and the call operator would mangle embedded quotes. release_checks.py
# (runtime_wires.py) and windows_agent_runtime_check.ps1 run the same check.
function Assert-RuntimeWires([string]$CodexExe, [string[]]$Wires) {
    foreach ($wire in @($Wires | Where-Object { $_ })) {
        $listed = Invoke-NativeCapture $CodexExe @("-c", "model_provider=angelo_wire_check", "-c", "model_providers.angelo_wire_check.name=angelo_wire_check",
            "-c", "model_providers.angelo_wire_check.wire_api=$wire", "debug", "models")
        $models = $null
        try { $models = @((($listed.Out -join "`n") | ConvertFrom-Json).models) } catch { }
        if ($listed.Code -ne 0 -or -not $models -or $models.Count -eq 0) {
            throw "$CodexExe does not speak the pinned model-provider wire ${wire}: this is not the patched build"
        }
    }
}

# Each pinned provider capability of the agent runtime (agent_runtime.json provider_capabilities), offline: `codex
# features list` with a throwaway provider (never selected; its URL is a closed local port) declaring each pinned value
# must load, and with an unpinned value must be refused as an unknown variant, because Codex ignores a capability it
# does not read. Bare values, as Assert-RuntimeWires passes them. release_checks.py (runtime_capabilities.py) and
# windows_agent_runtime_check.ps1 run the same check.
function Assert-RuntimeCapabilities([string]$CodexExe, $Capabilities) {
    if (-not $Capabilities) { return }
    $provider = "model_providers.angelo_capability_check"
    foreach ($capability in @($Capabilities.PSObject.Properties | Where-Object { -not $_.Name.StartsWith("_") })) {
        foreach ($value in @($capability.Value) + "angelo-unpinned-value") {
            $probe = Invoke-NativeCapture $CodexExe @("-c", "$provider.name=angelo_capability_check", "-c", "$provider.base_url=http://127.0.0.1:9/v1",
                "-c", "$provider.capabilities.$($capability.Name)=$value", "features", "list")
            if ($value -eq "angelo-unpinned-value") {
                if ($probe.Code -eq 0 -or -not $probe.Text.Contains("unknown variant")) {
                    throw "$CodexExe does not read the pinned provider capability $($capability.Name): this is not the build the pin names"
                }
            } elseif ($probe.Code -ne 0) {
                throw "$CodexExe refused the pinned provider capability $($capability.Name) = '${value}': $($probe.Text)"
            }
        }
    }
}

# A Git LFS pointer is a small text file that starts with this line; a library or asset still in that form fails late.
function Test-Pointer([string]$Path) {
    $info = Get-Item $Path
    if ($info.Length -gt 1024) { return $false }
    return ([System.IO.File]::ReadAllText($Path)).StartsWith("version https://git-lfs")
}
function Assert-RealFiles([string]$Folder, [string[]]$Patterns, [string]$What) {
    if (-not (Test-Path $Folder)) { throw "$Folder is missing: $What" }
    $files = @(Get-ChildItem $Folder -Recurse -File -Include $Patterns -ErrorAction SilentlyContinue | Select-Object -First 400)
    if (-not $files) { throw "$Folder has no $($Patterns -join ', '): $What" }
    $pointers = @($files | Where-Object { Test-Pointer $_.FullName })
    if ($pointers) { throw "$($pointers[0].FullName) is a Git LFS pointer ($($pointers.Count) of the first $($files.Count) checked): $What" }
}

function Get-Version([string]$Text, [string]$Pattern) {
    $match = [regex]::Match($Text, $Pattern)
    if ($match.Success) { return [version]("{0}.{1}" -f $match.Groups[1].Value, $match.Groups[2].Value) }
    return $null
}

# clang-cl and lld-link as the given Visual Studio installs them (VC\Tools\Llvm\x64\bin), and its vcvars64.bat;
# refused, saying what to install, when the instance lacks them.
function Find-ClangClToolset($Vs, [string]$Vswhere) {
    $with = @(& $Vswhere -products * -version "[17.0,18.0)" -requires @AngeloClangComponents -format json | ConvertFrom-Json) |
        Where-Object { $_.installationPath -eq $Vs.installationPath }
    $add = ($AngeloClangComponents | ForEach-Object { "--add $_" }) -join " "
    if (-not $with) {
        throw ("$($Vs.installationPath) has no clang-cl toolset: the Windows release builds with clang-cl, as official Blender's " +
            "(native_editor_build.rst, 'Toolset: clang-cl'). Add $($AngeloClangComponents -join ' and ') with Visual Studio's " +
            "installer: setup.exe modify --installPath `"$($Vs.installationPath)`" $add --quiet --norestart")
    }
    $llvm = Join-Path $Vs.installationPath "VC\Tools\Llvm\x64\bin"
    $found = [ordered]@{ generator = "Visual Studio 17 2022"; generator_toolset = $AngeloReleaseToolset
        compiler = $null; compiler_path = (Join-Path $llvm "clang-cl.exe"); linker = $null; linker_path = (Join-Path $llvm "lld-link.exe")
        vcvars = (Join-Path $Vs.installationPath "VC\Auxiliary\Build\vcvars64.bat")
        msbuild = "/p:UseMultiToolTask=true /p:EnforceProcessCountAcrossBuilds=true" }
    foreach ($path in $found.compiler_path, $found.linker_path, $found.vcvars) {
        if (-not (Test-Path $path)) { throw "$path is missing: repair Visual Studio's $($AngeloClangComponents -join ' and ')" }
    }
    $found.compiler = "clang-cl " + [regex]::Match(((Invoke-NativeCapture $found.compiler_path @("--version")).Out -join " "), "\d+\.\d+\.\d+").Value
    $found.linker = "lld-link " + [regex]::Match(((Invoke-NativeCapture $found.linker_path @("--version")).Out -join " "), "\d+\.\d+\.\d+").Value
    $found.cmake = "$((Invoke-NativeCapture cmake @("--version")).Out | Select-Object -First 1)"
    return $found
}

# The developer environment of vcvars64.bat, imported into this process so that every later step (configure, build,
# cpack) runs in it, as make.bat's builds do; judged by what it must provide, not by vcvars' exit code. The CMake found
# before stays first on PATH (vcvars puts Visual Studio's own CMake ahead of it).
function Import-DeveloperEnvironment([string]$Vcvars) {
    $cmakeDir = Split-Path (Get-Command cmake -ErrorAction Stop).Source
    $batch = Join-Path $env:TEMP ("angelo-vcvars-{0}.cmd" -f [guid]::NewGuid().ToString("N"))
    [System.IO.File]::WriteAllText($batch, "@echo off`r`ncall `"$Vcvars`" > nul 2>&1`r`nset`r`n", (New-Object System.Text.UTF8Encoding($false)))
    $lines = @(& cmd.exe /d /c $batch)
    Remove-Item $batch -ErrorAction SilentlyContinue
    foreach ($line in $lines) { if ($line -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process") } }
    $env:Path = "$cmakeDir;$env:Path"
    if (-not $env:VCToolsRedistDir -or -not (Test-Path $env:VCToolsRedistDir)) { throw "$Vcvars gave no VCToolsRedistDir (the MSVC redistributables)" }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw "$Vcvars put no cl.exe on PATH (CUDA's host compiler)" }
}

# The ClangCL toolset puts clang's own headers first in every project's IncludePath (Microsoft.Cpp.ClangCl.Common.props),
# and MSBuild hands IncludePath to custom build steps as INCLUDE. The custom-command projects run host tools that are not
# clang-cl (nvcc with cl.exe as its host compiler, hipcc, the SYCL compiler), and cl.exe stops at clang's vadefs.h
# ("C1021: invalid preprocessor command 'include_next'"). Directory.Build.targets in the build folder gives those
# projects (ConfigurationType Utility; they compile nothing with clang-cl) the MSVC toolset's own IncludePath.
function Write-CustomCommandIncludePath([string]$BuildDir) {
    $xml = "<Project>`r`n  <!-- Written by build_release_windows.ps1 (windows_release_toolchain.ps1): custom-command projects run host tools`r`n" +
        "       that are not clang-cl; they get the MSVC toolset's include path, without clang's own headers. -->`r`n" +
        "  <PropertyGroup Condition=`"'`$(ConfigurationType)' == 'Utility' and '`$(PlatformToolset)' == '$AngeloReleaseToolset'`">`r`n" +
        "    <IncludePath>`$(VC_IncludePath);`$(WindowsSDK_IncludePath)</IncludePath>`r`n  </PropertyGroup>`r`n</Project>`r`n"
    [System.IO.File]::WriteAllText((Join-Path $BuildDir "Directory.Build.targets"), $xml, (New-Object System.Text.UTF8Encoding($false)))
}

# WiX 3's light.exe cannot open a path of 260 characters or more (MAX_PATH), and CPack stages each installed file at
# <package folder>\_CPack_Packages\Windows\WIX\<package>\<component>\<its path below the install folder>. CPack packs in
# a short folder beside the build folder (one per build folder), so the build folder's name never counts; the longest
# staged path is checked before WiX runs, and its length returned.
function Get-PackageStagingDir([string]$BuildDir) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($BuildDir.TrimEnd('\').ToLowerInvariant())
    $hash = -join ([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)[0..3] | ForEach-Object { $_.ToString("x2") })
    Join-Path (Split-Path $BuildDir -Parent) "cpk-$hash"
}
function Assert-StagedPathLengths([string]$BuildDir, [string]$PackageDir, [string]$PackageName, [string]$InstallDir, [string[]]$Files) {
    $config = Join-Path $BuildDir "CPackConfig.cmake"
    $line = if (Test-Path $config) { Select-String -Path $config -Pattern '^set\(CPACK_COMPONENTS_ALL "(.*)"\)' | Select-Object -First 1 }
    $components = if ($line) { @($line.Matches[0].Groups[1].Value -split ";" | Where-Object { $_ }) } else { @() }
    $component = @(@($components) + "Unspecified" | Sort-Object Length -Descending)[0]
    $root = Join-Path $PackageDir "_CPack_Packages\Windows\WIX\$PackageName\$component"
    $prefix = $InstallDir.TrimEnd('\') + '\'
    $longest = $Files | ForEach-Object { $_ -replace '[\\/]+', '\' } | Where-Object { $_.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Join-Path $root $_.Substring($prefix.Length) } | Sort-Object Length -Descending | Select-Object -First 1
    if ($longest -and $longest.Length -ge 260) {
        throw "WiX cannot read a staged path of $($longest.Length) characters (260 or more): $longest; package in a shorter folder"
    }
    return $(if ($longest) { $longest.Length } else { 0 })
}

# What CMake configured (its compiler detection and the cache), refused unless it is clang-cl with lld-link.
function Assert-ConfiguredToolset([string]$BuildDir, $Toolset) {
    # CMakeFiles\<CMake version>\CMakeCXXCompiler.cmake: what CMake's compiler detection found.
    $detected = Get-ChildItem (Join-Path $BuildDir "CMakeFiles") -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Item (Join-Path $_.FullName "CMakeCXXCompiler.cmake") -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $values = @{}
    if ($detected) {
        Get-Content $detected.FullName | ForEach-Object { if ($_ -match '^set\((CMAKE_CXX_COMPILER\w*) "(.*)"\)$') { $values[$Matches[1]] = $Matches[2] } }
    }
    Get-Content (Join-Path $BuildDir "CMakeCache.txt") | ForEach-Object {
        if ($_ -match "^(CMAKE_LINKER|CMAKE_GENERATOR_TOOLSET):\w+=(.*)$") { $values[$Matches[1]] = $Matches[2] }
    }
    # clang-cl is Clang with the MSVC-compatible front end (the command-line variant CMake records, or its file name).
    $clangCl = $values["CMAKE_CXX_COMPILER_ID"] -eq "Clang" -and
        ($values["CMAKE_CXX_COMPILER_FRONTEND_VARIANT"] -eq "MSVC" -or "$($values['CMAKE_CXX_COMPILER'])" -match "clang-cl\.exe$")
    if (-not $clangCl) {
        throw "CMake configured the compiler '$($values['CMAKE_CXX_COMPILER_ID'])' ($($values['CMAKE_CXX_COMPILER'])), not clang-cl ($BuildDir)"
    }
    if ($values["CMAKE_GENERATOR_TOOLSET"] -ne $Toolset.generator_toolset) { throw "CMake configured the toolset '$($values['CMAKE_GENERATOR_TOOLSET'])', not $($Toolset.generator_toolset)" }
    if ("$($values['CMAKE_LINKER'])" -notmatch "lld-link\.exe$") { throw "CMake configured the linker '$($values['CMAKE_LINKER'])', not lld-link" }
    $Toolset.compiler = "clang-cl $($values['CMAKE_CXX_COMPILER_VERSION'])"
    $Toolset.compiler_path = $values["CMAKE_CXX_COMPILER"]; $Toolset.linker_path = $values["CMAKE_LINKER"]
}

# The Python of Blender's Windows libraries: it runs the checks that must pass before anything is configured.
function Get-LibraryPython([string]$LibDir) {
    $python = Get-ChildItem (Join-Path $LibDir "python") -Recurse -Filter python.exe -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "\\bin\\python\.exe$" } | Select-Object -First 1
    if (-not $python) { throw "no python.exe below $LibDir\python to read Blender's GPU SDK pins with" }
    return $python.FullName
}

# The installed CUDA toolkit of one major version: NVIDIA's installer names each in CUDA_PATH_V<major>_<minor>, and
# CUDA_PATH (the toolkit installed last) counts when its compiler reports that major version.
function Get-CudaToolkitRoot([string]$Major) {
    $named = Get-ChildItem Env: | Where-Object { $_.Name -match "^CUDA_PATH_V${Major}_\d+$" } |
        Sort-Object { [int]($_.Name -replace "^CUDA_PATH_V\d+_", "") } -Descending | ForEach-Object { $_.Value }
    foreach ($root in @($named) + @($env:CUDA_PATH)) {
        if (-not $root) { continue }
        $nvcc = Join-Path $root "bin\nvcc.exe"
        if (-not (Test-Path $nvcc)) { continue }
        $reported = Get-Version ((Invoke-NativeCapture $nvcc @("--version")).Out -join "`n") "release (\d+)\.(\d+)"
        if ($reported -and "$($reported.Major)" -eq $Major) { return $root.TrimEnd('\') }
    }
    return $null
}

# A CUDA toolkit's exact version: version.json at its root names it (cuda.version). Without that file only the
# compiler's release (major.minor) is known, which gpu_sdk_pins.py does not take for a pinned patch version.
function Get-CudaToolkitVersion([string]$Root) {
    $file = Join-Path $Root "version.json"
    if (Test-Path $file) {
        $version = (Get-Content $file -Raw | ConvertFrom-Json).cuda.version
        if ($version) { return "$version" }
    }
    $nvcc = Join-Path $Root "bin\nvcc.exe"
    $reported = Get-Version ((Invoke-NativeCapture $nvcc @("--version")).Out -join "`n") "release (\d+)\.(\d+)"
    return "$reported"
}

# ocloc's version as Blender names it: the last two fields of the four-field file version (the graphics driver
# release it belongs to), or the whole file version when it has another shape. It is read from ocloc.exe and, when
# that carries no version (oneAPI 2025.3's has none: every field is 0), from the Graphics Compiler beside it,
# igc64.dll, which carries the same driver build. Empty when neither names one.
function Get-OclocVersion([string]$Folder) {
    foreach ($name in @("ocloc.exe", "igc64.dll")) {
        $file = Join-Path $Folder $name
        if (-not (Test-Path $file)) { continue }
        $fields = @("$((Get-Item $file).VersionInfo.FileVersion)" -split "[^0-9]+" | Where-Object { $_ })
        if (-not ($fields | Where-Object { [int64]$_ -ne 0 })) { continue }
        if ($fields.Count -ge 4) { return "{0}.{1}" -f $fields[2], $fields[3] }
        return ($fields -join ".")
    }
    return ""
}

# A path as CMake takes it anywhere: forward slashes, no trailing one; nothing for no path. A backslash survives the
# command line but not a second reading: a macro that parses its arguments again (FindCUDA.cmake's
# cuda_find_host_program read "C:\Program Files" as an invalid escape and stopped the configure), generated CMake code
# (LIBDIR in cmake_install.cmake), a script CPack runs. Every path the release build gives cmake or cpack goes through
# here (test_native_gpu_sdk_pins.py reads that none is left out).
function ConvertTo-CMakePath([string]$Path) { return "$Path".Replace('\', '/').TrimEnd('/') }

# Every GPU SDK is the version Blender pins for its own release, or an approved exception: another version can change
# what a person's machine needs, as a newer OptiX SDK raises the oldest NVIDIA driver OptiX runs on
# (gpu_sdk_pins.py; doc\angelo\native_editor_build.rst, "GPU toolkits").
function Test-GpuSdkPins($Versions, [string[]]$Allowed, [string]$Python, [string]$SourceDir) {
    $file = Join-Path ([IO.Path]::GetTempPath()) ("angelo-gpu-sdks-{0}.json" -f [guid]::NewGuid().ToString("N"))
    try {
        [IO.File]::WriteAllText($file, (@{ versions = $Versions; left_out = @($Allowed) } | ConvertTo-Json -Depth 4))
        $ran = Invoke-NativeCapture $Python @("-I", "-B", (Join-Path $SourceDir "scripts\angelo\gpu_sdk_pins.py"), "check", "--source", $SourceDir, "--used", $file)
        if ($ran.Code -ne 0) { throw ("the GPU SDKs are not the versions Blender pins: " + ($ran.Text -replace "`r`n", " / ")) }
        Write-Host $ran.Text
    } finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
}

# A build folder keeps what CMake found under a toolkit's folder (OptiX's headers, HIP's compiler, DLSS's headers) and
# never looks again when another folder is given: the pinned folders passed the check above while the build compiled
# with the earlier ones. "before" the configure, a CUDA toolkit without nvvm\bin\cicc stops the build, and a folder
# last configured with other toolkits (or with no record of them) has its CMake cache moved aside; "after" it, the
# cache CMake wrote must hold every SDK inside the folder given for it and at the version judged, or the build stops
# with the entry's name; a clean cache becomes the folder's record (gpu_sdk_cache.py).
function Invoke-ToolkitCacheCheck([string]$Stage, [string]$BuildDir, $Gpu, [string]$SourceDir) {
    $file = Join-Path ([IO.Path]::GetTempPath()) ("angelo-gpu-cache-{0}.json" -f [guid]::NewGuid().ToString("N"))
    try {
        [IO.File]::WriteAllText($file, (@{ options = @($Gpu.cmake); versions = $Gpu.versions } | ConvertTo-Json -Depth 4))
        $python = $Gpu.python
        $ran = Invoke-NativeCapture $python @("-I", "-B", (Join-Path $SourceDir "scripts\angelo\gpu_sdk_cache.py"), "--build", $BuildDir, "--used", $file, "--stage", $Stage)
        Write-Host $ran.Text
        if ($ran.Code -ne 0) { throw ("the build folder's GPU toolkits ($Stage the configure): " + ($ran.Text -replace "`r`n", " / ")) }
    } finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
}

# The pinned installers this machine's toolkits came from: windows_toolkits.ps1 writes toolkits-installed.json in its
# toolkits folder (each SDK's version, the installer's SHA-256 and signer), which ANGELO_TOOLKITS_RECORD names or which
# lies beside the SDK folders. It goes into the build's record as it is; a machine set up by hand has none.
function Get-ToolkitInstallRecord {
    $beside = @($env:OPTIX_ROOT_DIR, $env:DLSS_SDK_ROOT, $env:OCLOC_INSTALL_DIR) | Where-Object { $_ } |
        ForEach-Object { Join-Path (Split-Path $_.TrimEnd('\') -Parent) "toolkits-installed.json" }
    foreach ($file in @($env:ANGELO_TOOLKITS_RECORD) + @($beside)) {
        if ($file -and (Test-Path $file)) { return (Get-Content $file -Raw -Encoding UTF8 | ConvertFrom-Json) }
    }
    return $null
}

# The GPU toolkits (checked before configuring: a missing one fails in seconds, not after hours of compiling) and the
# CMake options for them: every GPU option is stated on each configure, so a part allowed missing in an earlier run of
# a build folder comes back on as soon as it is no longer allowed (blender_release.cmake, passed first, switches them
# all on).
function Get-GpuToolkits([string[]]$Allowed, [string]$LibDir, [string]$SourceDir) {
    $toolkits = [ordered]@{}
    $versions = [ordered]@{}   # each SDK's exact version, judged against Blender's pins once all are read
    $cmakeGpu = @()
    $python = Get-LibraryPython $LibDir
    $read = Invoke-NativeCapture $python @("-I", "-B", (Join-Path $SourceDir "scripts\angelo\gpu_sdk_pins.py"), "pins", "--source", $SourceDir)
    if ($read.Code -ne 0) { throw ("Blender's GPU SDK pins cannot be read: " + ($read.Text -replace "`r`n", " / ")) }
    $pins = ($read.Out -join "`n") | ConvertFrom-Json   # Standard output only: a warning on stderr is never parsed.
    if ("cuda" -in $Allowed) {
        $toolkits.cuda = "omitted (-AllowMissing)"
    } else {
        # One toolkit for each CUDA major version Blender pins, passed to CMake as Blender's own build passes them:
        # Cycles then picks a toolkit per kernel architecture (intern\cycles\kernel\device\cuda\CMakeLists.txt).
        $found = [ordered]@{}
        foreach ($pin in @($pins.sdks.cuda)) {
            $major = "$pin".Split(".")[0]
            $root = Get-CudaToolkitRoot $major
            if (-not $root) { continue }
            $found[$major] = @{ root = $root; version = (Get-CudaToolkitVersion $root) }
            $cmakeGpu += "-DCUDA${major}_TOOLKIT_ROOT_DIR=$(ConvertTo-CMakePath $root)"
            $cmakeGpu += "-DCUDA${major}_NVCC_EXECUTABLE=$(ConvertTo-CMakePath (Join-Path $root 'bin\nvcc.exe'))"
        }
        if (-not $found.Count) { throw "CUDA Toolkit not found (CUDA_PATH or CUDA_PATH_V<major>_<minor>, with bin\nvcc.exe); install it or pass -AllowMissing cuda" }
        $default = "$($pins.cuda_default_major)"
        $first = if ($found.Contains($default)) { $found[$default] } else { @($found.Values)[0] }
        $cmakeGpu += "-DCUDA_TOOLKIT_ROOT_DIR=$(ConvertTo-CMakePath $first.root)"
        $cmakeGpu += "-DCUDA_NVCC_EXECUTABLE=$(ConvertTo-CMakePath (Join-Path $first.root 'bin\nvcc.exe'))"
        $versions.cuda = @($found.Values | ForEach-Object { $_.version })
        $toolkits.cuda = (@($found.Values | ForEach-Object { "{0} at {1}" -f $_.version, $_.root }) -join "; ")
    }
    if ("optix" -in $Allowed) {
        $toolkits.optix = "omitted (-AllowMissing)"
    } else {
        $header = if ($env:OPTIX_ROOT_DIR) { Join-Path $env:OPTIX_ROOT_DIR "include\optix.h" } else { "" }
        if (-not $header -or -not (Test-Path $header)) { throw "OptiX SDK not found (OPTIX_ROOT_DIR\include\optix.h); install it or pass -AllowMissing optix" }
        $line = Select-String -Path $header -Pattern "^#define OPTIX_VERSION\s+(\d+)" | Select-Object -First 1
        $number = if ($line) { [int]$line.Matches[0].Groups[1].Value } else { 0 }
        if ($number -lt 80000) { throw "OptiX $number at $header; Cycles needs 8.0 or newer" }
        $versions.optix = "{0}.{1}.{2}" -f [math]::Floor($number / 10000), [math]::Floor(($number % 10000) / 100), ($number % 100)
        $toolkits.optix = "{0} at {1}" -f $versions.optix, $env:OPTIX_ROOT_DIR
        $cmakeGpu += "-DOPTIX_ROOT_DIR=$(ConvertTo-CMakePath $env:OPTIX_ROOT_DIR)"
    }
    if ("hip" -in $Allowed) {
        $toolkits.hip = "omitted (-AllowMissing)"
    } else {
        $hipcc = if ($env:HIP_PATH) { Join-Path $env:HIP_PATH "bin\hipcc.exe" } else { "" }
        if (-not $hipcc -or -not (Test-Path $hipcc)) { throw "AMD HIP SDK not found (HIP_PATH\bin\hipcc.exe); install it or pass -AllowMissing hip" }
        $versionFile = Join-Path $env:HIP_PATH "include\hip\hip_version.h"
        $hipMajor = Select-String -Path $versionFile -Pattern "^#define HIP_VERSION_MAJOR\s+(\d+)" | Select-Object -First 1
        $hipMinor = Select-String -Path $versionFile -Pattern "^#define HIP_VERSION_MINOR\s+(\d+)" | Select-Object -First 1
        $hipVersion = if ($hipMajor -and $hipMinor) { [version]("{0}.{1}" -f $hipMajor.Matches[0].Groups[1].Value, $hipMinor.Matches[0].Groups[1].Value) } else { $null }
        if (-not $hipVersion -or $hipVersion -lt [version]"6.0") { throw "HIP $hipVersion at $env:HIP_PATH; Cycles needs 6.0 or newer" }
        $hipPatch = Select-String -Path $versionFile -Pattern "^#define HIP_VERSION_PATCH\s+(\d+)" | Select-Object -First 1
        $versions.hip = if ($hipPatch) { "$hipVersion.$($hipPatch.Matches[0].Groups[1].Value)" } else { "$hipVersion" }
        $toolkits.hip = "$($versions.hip) at $env:HIP_PATH"
        $cmakeGpu += "-DHIP_ROOT_DIR=$(ConvertTo-CMakePath $env:HIP_PATH)"
    }
    if ("hiprt" -in $Allowed) { $toolkits.hiprt = "omitted (-AllowMissing)" }
    else { $toolkits.hiprt = "lib\windows_x64\hiprt" }
    if ("oneapi" -in $Allowed) { $toolkits.oneapi = "omitted (-AllowMissing)" }
    else {
        # The oneAPI kernels are compiled ahead of time with Intel's ocloc, which Blender's Windows libraries do not
        # carry (its dependency builder makes ocloc for Linux only): Intel's Graphics Offline Compiler for OpenCL,
        # downloaded separately (OCLOC_INSTALL_DIR, set by windows_toolkits.ps1), or a dpcpp\lib\ocloc folder.
        $ocloc = @($env:OCLOC_INSTALL_DIR, (Join-Path $LibDir "dpcpp\lib\ocloc")) | Where-Object { $_ -and (Test-Path (Join-Path $_ "ocloc.exe")) } | Select-Object -First 1
        if (-not $ocloc) { throw "Intel's ocloc not found (OCLOC_INSTALL_DIR\ocloc.exe or lib\windows_x64\dpcpp\lib\ocloc); supply Intel's Graphics Offline Compiler for OpenCL or pass -AllowMissing oneapi" }
        $versions.ocloc = Get-OclocVersion $ocloc
        $toolkits.oneapi = "lib\windows_x64\dpcpp, ocloc $($versions.ocloc) at $ocloc"
        $cmakeGpu += "-DOCLOC_INSTALL_DIR=$(ConvertTo-CMakePath $ocloc)"
    }
    # A release may leave DLSS out on purpose (ANGELO_RELEASE_DLSS=off, from the VM's angelo-release-dlss): NVIDIA's
    # licence needs its written approval of the marks a DLSS build shows, so without it the release ships without DLSS.
    # Unlike -AllowMissing that build is still release-equivalent; the manifest records the choice.
    if ($env:ANGELO_RELEASE_DLSS -and $env:ANGELO_RELEASE_DLSS -notin "on", "off") { throw "ANGELO_RELEASE_DLSS is '$env:ANGELO_RELEASE_DLSS', not on or off" }
    $dlssOff = $env:ANGELO_RELEASE_DLSS -eq "off"
    if ("dlss" -in $Allowed) { $toolkits.dlss = "omitted (-AllowMissing)" }
    elseif ($dlssOff) { $toolkits.dlss = "off (ANGELO_RELEASE_DLSS=off: a release without DLSS)"; $versions.dlss = "off" }
    else {
        # NVIDIA's DLSS SDK, headers only (windows_toolkits.ps1 installs them at their pinned SHA-256): the version is
        # Blender's own pin, so a release builds against exactly what official Blender does.
        $pin = Get-Content (Join-Path $SourceDir "build_files\buildbot\config\blender_version.cmake") -Raw
        $want = [regex]::Match($pin, "set\(DLSS_VERSION\s+(\d+\.\d+\.\d+)\)").Groups[1].Value
        if (-not $want) { throw "no DLSS_VERSION in build_files\buildbot\config\blender_version.cmake" }
        $root = $env:DLSS_SDK_ROOT
        if (-not $root -or -not (Test-Path (Join-Path $root "include\nvsdk_ngx.h"))) { throw "DLSS SDK headers not found (DLSS_SDK_ROOT\include\nvsdk_ngx.h); supply dlss-sdk-$want-headers.zip or pass -AllowMissing dlss" }
        if ((Split-Path $root.TrimEnd('\') -Leaf) -ne "dlss-sdk-$want") { throw "DLSS SDK at $root, but Blender pins $want (dlss-sdk-$want)" }
        $versions.dlss = $want
        $toolkits.dlss = "$want headers at $root"
        $cmakeGpu += "-DDLSS_SDK_ROOT=$(ConvertTo-CMakePath $root)"
    }
    Test-GpuSdkPins $versions $Allowed $python $SourceDir
    # The release options each part turns on: build_release_windows.ps1 requires them after configure.
    $switches = [ordered]@{
        cuda = @("WITH_CYCLES_CUDA_BINARIES"); optix = @("WITH_CYCLES_DEVICE_OPTIX"); hip = @("WITH_CYCLES_HIP_BINARIES")
        hiprt = @("WITH_CYCLES_DEVICE_HIPRT"); oneapi = @("WITH_CYCLES_DEVICE_ONEAPI", "WITH_CYCLES_ONEAPI_BINARIES")
        dlss = @("WITH_DLSS")
    }
    foreach ($part in $switches.Keys) {
        $off = $part -in $Allowed -or ($part -eq "dlss" -and $dlssOff)
        foreach ($option in $switches[$part]) { $cmakeGpu += "-D{0}={1}" -f $option, $(if ($off) { "OFF" } else { "ON" }) }
    }
    if ($dlssOff) { $switches.Remove("dlss") }   # Not required after configure: this release leaves it off.
    return @{ toolkits = $toolkits; versions = $versions; installers = (Get-ToolkitInstallRecord); cmake = @($cmakeGpu | Select-Object -Unique); options = $switches; python = $python }
}
