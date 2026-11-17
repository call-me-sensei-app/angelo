# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Installs the full-feature build's GPU toolkits from a folder of installers, each only after its Authenticode
  signature checks out. Called by windows_cloud_bootstrap.ps1's toolkits stage (the 'release' stage only).

.DESCRIPTION
  Every installer must carry a Valid signature from its vendor, or it is refused and logged and nothing of it runs:
  NVIDIA Corporation for the CUDA Toolkit and the OptiX SDK, Advanced Micro Devices for the HIP SDK, Intel Corporation
  for ocloc. A .zip carries no signature, so an OptiX SDK .zip is refused too: supply NVIDIA's installer.

  * cuda_<version>_..._windows.exe: the compiler and runtime parts only (ANGELO_CUDA_COMPONENTS, or the default list), never
    Display.Driver, so the machine's GRID driver stays. A part this installer does not carry is dropped with a log
    line (crt and nvvm are inside nvcc before CUDA 13); the parts are read from the installer's own package files.
    Each CUDA is then checked for every program and header nvcc runs (Test-CudaToolchain), so a part the installer
    did not lay down fails this stage by name instead of the kernel build (build5 2026-11-15: CUDA 13.4 without its
    nvvm package, "The system cannot find the path specified" at kernel_sm_120.cubin). An install an earlier run
    marked done is installed again when that check fails.
  * The OptiX SDK installer (NSIS): /S /D=<toolkits>\optix (or wherever it installs), and OPTIX_ROOT_DIR names it.
  * Intel's ocloc (Graphics Offline Compiler for OpenCL): the oneAPI kernels' compiler, which Blender's Windows
    libraries lack. Intel's oneAPI offline installer (ocloc*.exe, signed by Intel Corporation) runs silently with
    --eula accept (whoever supplies the installer accepts Intel's license for it); a .zip is extracted instead. Either way each .exe
    and .dll beside ocloc.exe must carry Intel Corporation's signature before OCLOC_INSTALL_DIR names that folder.
  * NVIDIA's DLSS SDK as headers only (dlss-sdk-<version>-headers.zip: include\*.h and LICENSE.txt, from the
    github.com/NVIDIA/DLSS release tag Blender pins as DLSS_VERSION). A .zip carries no signature, so the archive is
    taken only at the SHA-256 recorded below when its licence was accepted; DLSS_SDK_ROOT names the extracted folder.
    The headers stay on the build machine: the licence forbids putting them under an open-source licence.
  * The AMD HIP SDK bundle (AMD-Software-PRO-Edition-...-For-HIP.exe) is a 7-Zip self-extractor holding the SDK's
    MSIs beside the Radeon display driver and Radeon software, and AMD documents its -install switch as installing
    "both driver and applications" with no component choice. So only the SDK's own MSIs are taken out of it (SDK
    core, runtime compiler, ray tracing), each signature checked again, and installed with msiexec; the driver and
    the other applications are never extracted. HIP_PATH names the SDK when its MSI did not. ANGELO_HIP_ARGS, when
    not empty, runs the bundle itself with those arguments instead (an explicit choice to take what it installs).

  The installed set is pinned (windows_toolkit_pins.json beside this script): an installer is taken only when its
  SHA-256 is one recorded there, so an older or newer SDK left in the toolkits folder is skipped with a log line, never
  installed. Every pinned installer must be installed (now or by an earlier run), and a pin whose sha256 is still TBD
  fails the stage, so a release build cannot start on an unpinned or partial set. OPTIX_ROOT_DIR names the SDK whose
  optix.h reports the pinned version and HIP_PATH the pinned HIP version's folder, even beside older installs.
  <Kits>\toolkits-installed.json records each pinned SDK (version, installer, SHA-256, signer, folder) for the build's
  record; gpu_sdk_pins.py then judges the versions against Blender's own pins before configuring.

  Exit code: 0 done, 3010 an installer asked for a restart, 1 an installer was refused or failed (the caller does
  not mark the stage done, so the next run tries again; the build then stops in seconds for the missing toolkit).
#>
param(
    [Parameter(Mandatory = $true)][string]$Download,
    [Parameter(Mandatory = $true)][string]$Kits
)
$ErrorActionPreference = "Stop"
# The overrides come from the environment (the bootstrap sets them from angelo-cuda-components and angelo-hip-args).
$CudaComponents, $HipArgs = $env:ANGELO_CUDA_COMPONENTS, $env:ANGELO_HIP_ARGS
$sevenZip = "C:\Program Files\7-Zip\7z.exe"
# CUDA 13 split crt (headers) and nvvm (cicc, the device front end nvcc runs for every kernel) out of nvcc.
$defaultCuda = "nvcc cudart cuobjdump nvdisasm nvprune nvrtc nvrtc_dev nvfatbin nvjitlink crt nvvm thrust visual_studio_integration"
# What nvcc runs or includes for a kernel, relative to the CUDA root: each must be there after an install.
$cudaToolchain = "bin\nvcc.exe", "bin\cudafe++.exe", "bin\ptxas.exe", "bin\fatbinary.exe", "bin\nvlink.exe",
    "nvvm\bin\cicc.exe", "include\cuda_runtime.h", "include\crt\host_config.h"
# The HIP SDK's own packages in the bundle; Packages\Drivers and the other Packages\Apps are never extracted.
$hipPackages = "SDKCore", "RTCRuntime", "RTCDevelopment", "RayTracingRuntime", "RayTracingDevelopment"
$NVIDIA, $AMD, $INTEL = "NVIDIA Corporation", "Advanced Micro Devices", "Intel Corporation"
# Header archives by DLSS SDK version: SHA-256 of the archive accepted with NVIDIA's RTX SDKs licence (2026-11-03,
# github.com/NVIDIA/DLSS tag v310.9.1, commit 374959484e7; doc/angelo/native_editor_build.rst, "DLSS").
$DlssHeaderArchives = @{ "310.9.1" = "08dfc3eb7a1e49242833442d76f30a2b8b6b5d0e2678d3ea2f288440204a7a6f" }
# The pinned installers (windows_toolkit_pins.json): what this script installs, by the SHA-256 of the vendor's file.
$PinFile = Join-Path $PSScriptRoot "windows_toolkit_pins.json"
$Pins = @((Get-Content $PinFile -Raw | ConvertFrom-Json).installers)
$unrecorded = @($Pins | Where-Object { $_.sha256 -notmatch "^[0-9a-f]{64}$" })
if ($unrecorded) {
    foreach ($pin in $unrecorded) { Write-Host ("toolkits: FAILED: the {0} {1} pin has no recorded sha256 ({2})" -f $pin.sdk, $pin.version, $pin.sha256) }
    exit 1
}
$Record = Join-Path $Kits "toolkits-installed.json"

function Test-Publisher([string]$Path, [string]$Publisher) {
    # Valid means the chain, the timestamp and the file's hash all check out; the signer must be the vendor itself.
    $signature = Get-AuthenticodeSignature -FilePath $Path
    $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { "" }
    # The vendor's name as a whole subject component (CN= or O=, quoted or not, "Inc." allowed), never a prefix of
    # some other name.
    $vendor = "(^|,\s*)(CN|O)=""?" + [regex]::Escape($Publisher) + "(,? Inc\.)?""?(,|$)"
    if ($signature.Status -ne "Valid" -or $subject -notmatch $vendor) {
        Write-Host ("toolkits: REFUSED {0}: signature {1}, signer '{2}' (expected {3})" -f (Split-Path $Path -Leaf),
                    $signature.Status, $subject, $Publisher)
        return $false
    }
    Write-Host ("toolkits: {0}: signature Valid, signer '{1}'" -f (Split-Path $Path -Leaf), $subject)
    return $true
}

function Invoke-Installer([string]$File, [string]$Arguments, [int[]]$Accept) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -notin $Accept) { throw "$(Split-Path $File -Leaf) exited with $($process.ExitCode)" }
    return $process.ExitCode
}

function Get-ArchivePaths([string]$Archive) {
    if (-not (Test-Path $sevenZip)) { throw "7-Zip is not installed ($sevenZip)" }
    $listing = & $sevenZip l -ba -slt $Archive
    if ($LASTEXITCODE -ne 0) { throw "7-Zip could not read $(Split-Path $Archive -Leaf)" }
    return @($listing | Where-Object { $_ -like "Path = *" } | ForEach-Object { $_.Substring(7) })
}

# The CUDA <major.minor> root the installer recorded for the machine (CUDA_PATH_V13_4), or $null.
function Get-CudaRoot([string]$Version) {
    return [Environment]::GetEnvironmentVariable("CUDA_PATH_V" + $Version.Replace(".", "_"), "Machine")
}

# The toolchain files missing from a CUDA <major.minor> install (every one when it has no recorded root).
function Get-CudaToolchainMissing([string]$Version) {
    $root = Get-CudaRoot $Version
    if (-not $root -or -not (Test-Path $root)) { return @("CUDA_PATH_V" + $Version.Replace(".", "_")) }
    return @($cudaToolchain | Where-Object { -not (Test-Path (Join-Path $root $_)) })
}

function Install-Cuda([System.IO.FileInfo]$File, [string]$Version) {
    $paths = Get-ArchivePaths $File.FullName
    $packages = @($paths | Where-Object { $_ -like "*.nvi" } | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension($_).ToLowerInvariant() })
    $folders = @($paths | ForEach-Object { ($_ -split "[\\/]")[0].ToLowerInvariant() } | Sort-Object -Unique)
    $wanted = @(($(if ($CudaComponents) { $CudaComponents } else { $defaultCuda })) -split " " | Where-Object { $_ })
    $parts = @()
    foreach ($part in $wanted) {
        if ($part -eq "Display.Driver") { Write-Host "toolkits: Display.Driver is never installed (the GRID driver stays)"; continue }
        $name = $part.ToLowerInvariant()
        # The package files name a part bare, cuda_-prefixed or lib-prefixed (CUDA 13: cuda_crt, libnvvm).
        if ($name -in $packages -or $name -in $folders -or ("cuda_" + $name) -in $folders -or ("lib" + $name) -in $folders) { $parts += "${part}_$Version" }
        else { Write-Host "toolkits: CUDA $Version carries no '$part' package; not requested" }
    }
    Write-Host "toolkits: CUDA $Version parts: $($parts -join ' ')"
    return Invoke-Installer $File.FullName ("-s " + ($parts -join " ")) @(0, 1, 3010)
}

function Install-Hip([System.IO.FileInfo]$File, [string]$Version) {
    if ($HipArgs) {
        Write-Host "toolkits: running the HIP bundle itself with '$HipArgs' (angelo-hip-args)"
        return Invoke-Installer $File.FullName $HipArgs @(0, 1, 3010)
    }
    $paths = Get-ArchivePaths $File.FullName
    $target = Join-Path $Kits "hip-sdk"
    Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
    $restart = $false
    foreach ($package in $hipPackages) {
        $prefix = "Packages\Apps\ROCmSDKPackages\$package\"
        if (-not ($paths | Where-Object { $_.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })) {
            throw "the HIP bundle has no $prefix"
        }
        & $sevenZip x -y "-o$target" $File.FullName ($prefix + "*") -r | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7-Zip could not extract $prefix" }
        foreach ($msi in Get-ChildItem (Join-Path $target $prefix) -Filter "*.msi") {
            if (-not (Test-Publisher $msi.FullName $AMD)) { throw "$($msi.Name) is not signed by $AMD" }
            $log = Join-Path $Kits ("hip-" + $msi.BaseName + ".log")
            # 1638: this product is installed already (a retried stage after another toolkit failed).
            $code = Invoke-Installer "msiexec.exe" "/i `"$($msi.FullName)`" /qn /norestart /l*v `"$log`"" @(0, 1638, 1641, 3010)
            Write-Host "toolkits: $($msi.Name) exit code $code"
            if ($code -in 1641, 3010) { $restart = $true }
        }
    }
    # ROCm's folders are named major.minor (C:\Program Files\AMD\ROCm\7.1): HIP_PATH names the pinned version's folder,
    # also when an older HIP SDK is installed beside it and HIP_PATH still names that one.
    $series = ($Version -split "\.")[0..1] -join "."
    $hipcc = @(Get-ChildItem "C:\Program Files\AMD\ROCm\$series\bin\hipcc.exe" -ErrorAction SilentlyContinue)
    if (-not $hipcc) { throw "the HIP SDK MSIs installed no bin\hipcc.exe under C:\Program Files\AMD\ROCm\$series" }
    [Environment]::SetEnvironmentVariable("HIP_PATH", $hipcc[0].Directory.Parent.FullName + "\", "Machine")
    Write-Host "toolkits: HIP SDK at $([Environment]::GetEnvironmentVariable('HIP_PATH', 'Machine'))"
    return $(if ($restart) { 3010 } else { 0 })
}

function Get-Pin([System.IO.FileInfo]$File) {
    # The pin this file is, by its SHA-256 (never by its name: a renamed file is still the vendor's or it is not).
    $hash = (Get-FileHash $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    return $Pins | Where-Object { $_.sha256 -eq $hash } | Select-Object -First 1
}

function Get-OptixVersion([string]$Root) {
    # optix.h's OPTIX_VERSION (90000 for 9.0.0) as "major.minor.micro".
    $header = Join-Path $Root "include\optix.h"
    if (-not (Test-Path $header)) { return "" }
    $line = Select-String -Path $header -Pattern "^#define OPTIX_VERSION\s+(\d+)" | Select-Object -First 1
    if (-not $line) { return "" }
    $number = [int]$line.Matches[0].Groups[1].Value
    return "{0}.{1}.{2}" -f [math]::Floor($number / 10000), [math]::Floor(($number % 10000) / 100), ($number % 100)
}

function Get-OclocVersion([System.IO.FileInfo]$Exe) {
    # ocloc's driver build (e.g. 32.0.101.8424). Some ocloc.exe builds carry no version resource (oneAPI 2025.3's is
    # blank); the Graphics Compiler shipped beside it (igc64.dll) always carries the same driver build, so it answers then.
    $version = [string]$Exe.VersionInfo.FileVersion
    # A blank version and 0.0.0.0 both say nothing (gpu_sdk_pins' reader, windows_release_toolchain.ps1, agrees).
    if ($version -notmatch "[1-9]") {
        $igc = Join-Path $Exe.Directory.FullName "igc64.dll"
        if (Test-Path $igc) { $version = [string](Get-Item $igc).VersionInfo.FileVersion }
    }
    return $version.Trim()
}

function Register-Ocloc([string[]]$Roots, [string]$Package, [string]$Version = "") {
    # The newest ocloc.exe under the roots, or with -Version (the pin, e.g. 101.8424) the one whose driver build ends in
    # it (Get-OclocVersion; Intel numbers ocloc like its graphics driver, 32.0.101.8424), so an older or newer oneAPI ocloc
    # installed beside it is never the one named. Every .exe and .dll beside it must be signed Valid by Intel Corporation
    # before OCLOC_INSTALL_DIR names the folder (on Windows Cycles also loads IGC from it: IGC_INSTALL_DIR).
    $exe = $Roots | Where-Object { $_ -and (Test-Path $_) } |
        ForEach-Object { Get-ChildItem $_ -Recurse -Filter "ocloc.exe" -ErrorAction SilentlyContinue } |
        Where-Object { -not $Version -or (Get-OclocVersion $_).EndsWith("." + $Version) } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $exe) { Write-Host "toolkits: FAILED ${Package}: no ocloc.exe$(if ($Version) { " of version $Version" }) under $($Roots -join ', ')"; return $false }
    $binaries = @(Get-ChildItem $exe.Directory.FullName -File | Where-Object { $_.Extension -in ".exe", ".dll" })
    $unsigned = @($binaries | Where-Object { -not (Test-Publisher $_.FullName $INTEL) })
    if ($unsigned) {
        Write-Host "toolkits: REFUSED ${Package}: $($unsigned.Count) of the binaries beside ocloc.exe are not signed by $INTEL"
        return $false
    }
    [Environment]::SetEnvironmentVariable("OCLOC_INSTALL_DIR", $exe.Directory.FullName, "Machine")
    Write-Host "toolkits: ocloc at $($exe.Directory.FullName) ($($binaries.Count) signed binaries)"
    return $true
}

$failed, $restart = $false, $false
foreach ($file in Get-ChildItem $Download -File) {
    # An installer that already finished is not run again when another one failed and the stage is retried.
    $installed = Join-Path $Kits ("installed-{0}-{1}.done" -f $file.Name, $file.Length)
    if ((Test-Path $installed) -and $file.Name -match "^cuda_(\d+)\.(\d+)[\d.]*_.*windows.*\.exe$") {
        $missing = Get-CudaToolchainMissing ("{0}.{1}" -f $Matches[1], $Matches[2])
        if ($missing) {
            Write-Host "toolkits: $($file.Name) was installed by an earlier run without $($missing -join ', '); installing it again"
            Remove-Item $installed -Force
        }
    }
    if (Test-Path $installed) {
        Write-Host "toolkits: $($file.Name) was installed by an earlier run"
        Remove-Item $file.FullName -Force -ErrorAction SilentlyContinue
        continue
    }
    # Only a pinned installer runs (the DLSS headers carry their own pin below).
    $pin = $null
    if ($file.Name -notlike "dlss-sdk-*") {
        $pin = Get-Pin $file
        if (-not $pin) {
            # Not pinned: never installed here, and its copy only takes the build's disk (the bucket keeps it).
            Write-Host "toolkits: $($file.Name) is not a pinned installer (windows_toolkit_pins.json); skipped and removed"
            Remove-Item $file.FullName -Force -ErrorAction SilentlyContinue
            continue
        }
        Write-Host "toolkits: $($file.Name) is the pinned $($pin.sdk) $($pin.version)"
    }
    try {
        if ($file.Name -match "^cuda_(\d+)\.(\d+)[\d.]*_.*windows.*\.exe$") {
            $version = "{0}.{1}" -f $Matches[1], $Matches[2]
            if ($pin.sdk -ne "cuda" -or -not $pin.version.StartsWith("$version.")) { throw "$($file.Name) is pinned as $($pin.sdk) $($pin.version), not CUDA $version" }
            if (-not (Test-Publisher $file.FullName $NVIDIA)) { $failed = $true; continue }
            $code = Install-Cuda $file $version
            Write-Host "toolkits: CUDA $version installer exit code $code"
            $missing = Get-CudaToolchainMissing $version
            if ($missing) { throw "CUDA $version is installed without $($missing -join ', ') (the parts requested are in the line above)" }
            Write-Host "toolkits: CUDA $version toolchain complete at $(Get-CudaRoot $version)"
        } elseif ($file.Name -match "optix.*\.zip$") {
            Write-Host "toolkits: REFUSED $($file.Name): a .zip carries no signature; supply NVIDIA's OptiX SDK installer"
            $failed = $true; continue
        } elseif ($file.Name -match "optix.*\.exe$") {
            if (-not (Test-Publisher $file.FullName $NVIDIA)) { $failed = $true; continue }
            $optix = Join-Path $Kits ("optix-" + $pin.version)
            $code = Invoke-Installer $file.FullName "/S /D=$optix" @(0)
            # The installer may keep its own folder (NVIDIA's default is under ProgramData) rather than /D=, so look
            # for the SDK's header where either puts it.
            $roots = @($optix, (Join-Path $env:ProgramData "NVIDIA Corporation"), (Join-Path $env:ProgramFiles "NVIDIA Corporation"))
            $header = $roots | Where-Object { Test-Path $_ } |
                ForEach-Object { Get-ChildItem $_ -Recurse -Filter "optix.h" -ErrorAction SilentlyContinue } |
                Where-Object { $_.Directory.Name -eq "include" -and $_.Directory.Parent.Name -notmatch "internal" } |
                Where-Object { (Get-OptixVersion $_.Directory.Parent.FullName) -eq $pin.version } |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if (-not $header) { throw "the OptiX SDK installer (exit code $code) left no include\optix.h of version $($pin.version) under $($roots -join ', ')" }
            $root = $header.Directory.Parent.FullName
            [Environment]::SetEnvironmentVariable("OPTIX_ROOT_DIR", $root, "Machine")
            Write-Host "toolkits: OptiX SDK at $root (installer exit code $code)"
        } elseif ($file.Name -match "^dlss-sdk-(\d+\.\d+\.\d+)-headers\.zip$") {
            $version = $Matches[1]
            $hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($DlssHeaderArchives[$version] -ne $hash) {
                Write-Host "toolkits: REFUSED $($file.Name): SHA-256 $hash is not the one accepted for DLSS SDK $version"
                $failed = $true; continue
            }
            $target = Join-Path $Kits "dlss-sdk-$version"
            Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
            Expand-Archive $file.FullName -DestinationPath $target -Force
            if (-not (Test-Path (Join-Path $target "include\nvsdk_ngx.h"))) { throw "$($file.Name) holds no include\nvsdk_ngx.h" }
            [Environment]::SetEnvironmentVariable("DLSS_SDK_ROOT", $target, "Machine")
            Write-Host "toolkits: DLSS SDK $version headers at $target"
            $code = 0
        } elseif ($file.Name -match "ocloc.*\.zip$") {
            # Intel's Graphics Offline Compiler for OpenCL as a .zip: the archive has no signature, so every program
            # and library beside ocloc.exe must carry Intel's before the folder is named for the build.
            $target = Join-Path $Kits "ocloc"
            Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
            Expand-Archive $file.FullName -DestinationPath $target -Force
            if (-not (Register-Ocloc @($target) $file.Name)) {
                Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
                $failed = $true; continue
            }
            $code = 0
        } elseif ($file.Name -match "ocloc.*\.exe$") {
            # Intel's oneAPI offline installer for ocloc. Supplying it accepts Intel's EULA for this package, which is
            # what --eula accept records; -s -a runs the bootstrapper and the installer silently.
            if (-not (Test-Publisher $file.FullName $INTEL)) { $failed = $true; continue }
            # 1001: Intel's installer found this product already installed (a retried stage after another toolkit
            # failed, build5 2026-11-15); the installed binaries are then checked and named exactly as after a fresh install.
            $code = Invoke-Installer $file.FullName "-s -a --silent --eula accept" @(0, 3010, 1001)
            if ($code -eq 1001) { $code = 0 }
            Write-Host "toolkits: Intel ocloc installer exit code $code"
            $oneapi = @((Join-Path ${env:ProgramFiles(x86)} "Intel\oneAPI"), (Join-Path $env:ProgramFiles "Intel\oneAPI"))
            if (-not (Register-Ocloc $oneapi $file.Name $pin.version)) { $failed = $true; continue }
        } elseif ($file.Name -match "hip.*\.exe$") {
            if (-not (Test-Publisher $file.FullName $AMD)) { $failed = $true; continue }
            $code = Install-Hip $file $pin.version
        } else {
            Write-Host "toolkits: $($file.Name) is not a known installer; skipped"
            continue
        }
        if ($code -eq 3010) { $restart = $true }
        Set-Content $installed (Get-Date -Format o)
        if ($pin) { Set-Content (Join-Path $Kits ("pinned-{0}.json" -f $pin.sha256)) ($pin | ConvertTo-Json -Compress) }
        # Installed: the copy goes (the bucket keeps it), so the installers never eat the full build's free disk (build5,
        # 2026-11-15: 13.5 GB of installers left 21 GB of the 30 GB the build checks for).
        Remove-Item $file.FullName -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "toolkits: FAILED $($file.Name): $_"
        $failed = $true
    }
}
# Every pin installed (now or earlier), and the record of what is: the build's record takes it from here.
$done = @()
foreach ($pin in $Pins) {
    if (Test-Path (Join-Path $Kits ("pinned-{0}.json" -f $pin.sha256))) { $done += $pin; continue }
    Write-Host ("toolkits: FAILED: the pinned {0} {1} installer ({2}, sha256 {3}) is not installed (missing from the toolkits folder, or its install failed above)" -f $pin.sdk, $pin.version, $pin.file, $pin.sha256)
    $failed = $true
}
$installedRecord = [ordered]@{
    pins = $PinFile
    optix_root = [Environment]::GetEnvironmentVariable("OPTIX_ROOT_DIR", "Machine")
    hip_path = [Environment]::GetEnvironmentVariable("HIP_PATH", "Machine")
    # The installers set these for the machine, not for this process: read them there.
    cuda_paths = @([Environment]::GetEnvironmentVariables("Machine").GetEnumerator() | Where-Object { $_.Key -like "CUDA_PATH_V*" } |
                   Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" })
    installers = @($done | ForEach-Object { [ordered]@{ sdk = $_.sdk; version = $_.version; sha256 = $_.sha256; signer = $_.signer } })
}
Set-Content $Record ($installedRecord | ConvertTo-Json -Depth 4)
Write-Host "toolkits: record $Record ($($done.Count) of $($Pins.Count) pinned installers)"
if ($failed) { exit 1 }
if ($restart) { exit 3010 }
exit 0
