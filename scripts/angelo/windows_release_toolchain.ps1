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

# Visual Studio's MSBuild platform toolset the release builds with (CMake's -T) and the components that provide it.
$AngeloReleaseToolset = "ClangCL"
$AngeloClangComponents = @("Microsoft.VisualStudio.Component.VC.Llvm.Clang", "Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset")

# make.bat's name for a release build folder next to the source, with the toolset in it.
function Get-AngeloReleaseBuildDir([string]$SourceDir) {
    Join-Path (Split-Path $SourceDir -Parent) ("build_windows_Release_x64_vc17_{0}_Release" -f $AngeloReleaseToolset.ToLowerInvariant())
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

# Windows PowerShell 5 turns a native program's redirected stderr into errors, which Stop would make fatal.
function Invoke-Quiet([scriptblock]$Body) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Body 2>$null } finally { $ErrorActionPreference = $old }
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
    $found.compiler = "clang-cl " + [regex]::Match(((Invoke-Quiet { & $found.compiler_path --version }) -join " "), "\d+\.\d+\.\d+").Value
    $found.linker = "lld-link " + [regex]::Match(((Invoke-Quiet { & $found.linker_path --version }) -join " "), "\d+\.\d+\.\d+").Value
    $found.cmake = "$((Invoke-Quiet { & cmake --version }) | Select-Object -First 1)"
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

# The GPU toolkits (checked before configuring: a missing one fails in seconds, not after hours of compiling) and the
# CMake options for them: every GPU option is stated on each configure, so a part allowed missing in an earlier run of
# a build folder comes back on as soon as it is no longer allowed (blender_release.cmake, passed first, switches them
# all on).
function Get-GpuToolkits([string[]]$Allowed, [string]$LibDir) {
    $toolkits = [ordered]@{}
    $cmakeGpu = @()
    if ("cuda" -in $Allowed) {
        $toolkits.cuda = "omitted (-AllowMissing)"
    } else {
        $nvcc = if ($env:CUDA_PATH) { Join-Path $env:CUDA_PATH "bin\nvcc.exe" } else { "" }
        if (-not $nvcc -or -not (Test-Path $nvcc)) { throw "CUDA Toolkit not found (CUDA_PATH\bin\nvcc.exe); install it or pass -AllowMissing cuda" }
        $cudaVersion = Get-Version ((Invoke-Quiet { & $nvcc --version }) -join "`n") "release (\d+)\.(\d+)"
        if (-not $cudaVersion -or $cudaVersion -lt [version]"12.8") { throw "CUDA $cudaVersion at $nvcc; the sm_120 kernels need 12.8 or newer" }
        $toolkits.cuda = "$cudaVersion at $env:CUDA_PATH"
    }
    if ("optix" -in $Allowed) {
        $toolkits.optix = "omitted (-AllowMissing)"
    } else {
        $header = if ($env:OPTIX_ROOT_DIR) { Join-Path $env:OPTIX_ROOT_DIR "include\optix.h" } else { "" }
        if (-not $header -or -not (Test-Path $header)) { throw "OptiX SDK not found (OPTIX_ROOT_DIR\include\optix.h); install it or pass -AllowMissing optix" }
        $line = Select-String -Path $header -Pattern "^#define OPTIX_VERSION\s+(\d+)" | Select-Object -First 1
        $number = if ($line) { [int]$line.Matches[0].Groups[1].Value } else { 0 }
        if ($number -lt 80000) { throw "OptiX $number at $header; Cycles needs 8.0 or newer" }
        $toolkits.optix = "{0}.{1} at {2}" -f [math]::Floor($number / 10000), [math]::Floor(($number % 10000) / 100), $env:OPTIX_ROOT_DIR
        $cmakeGpu += "-DOPTIX_ROOT_DIR=$($env:OPTIX_ROOT_DIR.TrimEnd('\'))"
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
        $toolkits.hip = "$hipVersion at $env:HIP_PATH"
        $cmakeGpu += "-DHIP_ROOT_DIR=$($env:HIP_PATH.TrimEnd('\'))"
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
        $toolkits.oneapi = "lib\windows_x64\dpcpp, ocloc at $ocloc"
        $cmakeGpu += "-DOCLOC_INSTALL_DIR=$($ocloc.TrimEnd('\'))"
    }
    $switches = [ordered]@{
        cuda = @("WITH_CYCLES_CUDA_BINARIES"); optix = @("WITH_CYCLES_DEVICE_OPTIX"); hip = @("WITH_CYCLES_HIP_BINARIES")
        hiprt = @("WITH_CYCLES_DEVICE_HIPRT"); oneapi = @("WITH_CYCLES_DEVICE_ONEAPI", "WITH_CYCLES_ONEAPI_BINARIES")
    }
    foreach ($part in $switches.Keys) {
        foreach ($option in $switches[$part]) { $cmakeGpu += "-D{0}={1}" -f $option, $(if ($part -in $Allowed) { "OFF" } else { "ON" }) }
    }
    return @{ toolkits = $toolkits; cmake = @($cmakeGpu | Select-Object -Unique) }
}
