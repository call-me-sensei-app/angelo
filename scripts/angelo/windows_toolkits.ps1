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
    line (crt is inside nvcc before CUDA 13); the parts are read from the installer's own package files.
  * The OptiX SDK installer (NSIS): /S /D=<toolkits>\optix (or wherever it installs), and OPTIX_ROOT_DIR names it.
  * Intel's ocloc (Graphics Offline Compiler for OpenCL): the oneAPI kernels' compiler, which Blender's Windows
    libraries lack. Intel's oneAPI offline installer (ocloc*.exe, signed by Intel Corporation) runs silently with
    --eula accept (whoever supplies the installer accepts Intel's license for it); a .zip is extracted instead. Either way each .exe
    and .dll beside ocloc.exe must carry Intel Corporation's signature before OCLOC_INSTALL_DIR names that folder.
  * The AMD HIP SDK bundle (AMD-Software-PRO-Edition-...-For-HIP.exe) is a 7-Zip self-extractor holding the SDK's
    MSIs beside the Radeon display driver and Radeon software, and AMD documents its -install switch as installing
    "both driver and applications" with no component choice. So only the SDK's own MSIs are taken out of it (SDK
    core, runtime compiler, ray tracing), each signature checked again, and installed with msiexec; the driver and
    the other applications are never extracted. HIP_PATH names the SDK when its MSI did not. ANGELO_HIP_ARGS, when
    not empty, runs the bundle itself with those arguments instead (an explicit choice to take what it installs).

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
$defaultCuda = "nvcc cudart cuobjdump nvdisasm nvprune nvrtc nvrtc_dev nvfatbin nvjitlink crt thrust visual_studio_integration"
# The HIP SDK's own packages in the bundle; Packages\Drivers and the other Packages\Apps are never extracted.
$hipPackages = "SDKCore", "RTCRuntime", "RTCDevelopment", "RayTracingRuntime", "RayTracingDevelopment"
$NVIDIA, $AMD, $INTEL = "NVIDIA Corporation", "Advanced Micro Devices", "Intel Corporation"

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

function Install-Cuda([System.IO.FileInfo]$File, [string]$Version) {
    $paths = Get-ArchivePaths $File.FullName
    $packages = @($paths | Where-Object { $_ -like "*.nvi" } | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension($_).ToLowerInvariant() })
    $folders = @($paths | ForEach-Object { ($_ -split "[\\/]")[0].ToLowerInvariant() } | Sort-Object -Unique)
    $wanted = @(($(if ($CudaComponents) { $CudaComponents } else { $defaultCuda })) -split " " | Where-Object { $_ })
    $parts = @()
    foreach ($part in $wanted) {
        if ($part -eq "Display.Driver") { Write-Host "toolkits: Display.Driver is never installed (the GRID driver stays)"; continue }
        $name = $part.ToLowerInvariant()
        if ($name -in $packages -or $name -in $folders -or ("cuda_" + $name) -in $folders) { $parts += "${part}_$Version" }
        else { Write-Host "toolkits: CUDA $Version carries no '$part' package; not requested" }
    }
    Write-Host "toolkits: CUDA $Version parts: $($parts -join ' ')"
    return Invoke-Installer $File.FullName ("-s " + ($parts -join " ")) @(0, 1, 3010)
}

function Install-Hip([System.IO.FileInfo]$File) {
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
    $hipcc = @(Get-ChildItem "C:\Program Files\AMD\ROCm\*\bin\hipcc.exe" -ErrorAction SilentlyContinue |
               Sort-Object { $_.Directory.Parent.Name } -Descending)
    if (-not $hipcc) { throw "the HIP SDK MSIs installed no bin\hipcc.exe under C:\Program Files\AMD\ROCm" }
    $current = [Environment]::GetEnvironmentVariable("HIP_PATH", "Machine")
    if (-not $current -or -not (Test-Path (Join-Path $current "bin\hipcc.exe"))) {
        [Environment]::SetEnvironmentVariable("HIP_PATH", $hipcc[0].Directory.Parent.FullName + "\", "Machine")
    }
    Write-Host "toolkits: HIP SDK at $([Environment]::GetEnvironmentVariable('HIP_PATH', 'Machine'))"
    return $(if ($restart) { 3010 } else { 0 })
}

function Register-Ocloc([string[]]$Roots, [string]$Package) {
    # The newest ocloc.exe under the roots; every .exe and .dll beside it must be signed Valid by Intel Corporation
    # before OCLOC_INSTALL_DIR names the folder (on Windows Cycles also loads IGC from it: IGC_INSTALL_DIR).
    $exe = $Roots | Where-Object { $_ -and (Test-Path $_) } |
        ForEach-Object { Get-ChildItem $_ -Recurse -Filter "ocloc.exe" -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $exe) { Write-Host "toolkits: FAILED ${Package}: no ocloc.exe under $($Roots -join ', ')"; return $false }
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
    if (Test-Path $installed) { Write-Host "toolkits: $($file.Name) was installed by an earlier run"; continue }
    try {
        if ($file.Name -match "^cuda_(\d+)\.(\d+)[\d.]*_.*windows.*\.exe$") {
            $version = "{0}.{1}" -f $Matches[1], $Matches[2]
            if (-not (Test-Publisher $file.FullName $NVIDIA)) { $failed = $true; continue }
            $code = Install-Cuda $file $version
            Write-Host "toolkits: CUDA $version installer exit code $code"
        } elseif ($file.Name -match "optix.*\.zip$") {
            Write-Host "toolkits: REFUSED $($file.Name): a .zip carries no signature; supply NVIDIA's OptiX SDK installer"
            $failed = $true; continue
        } elseif ($file.Name -match "optix.*\.exe$") {
            if (-not (Test-Publisher $file.FullName $NVIDIA)) { $failed = $true; continue }
            $optix = Join-Path $Kits "optix"
            $code = Invoke-Installer $file.FullName "/S /D=$optix" @(0)
            # The installer may keep its own folder (NVIDIA's default is under ProgramData) rather than /D=, so look
            # for the SDK's header where either puts it.
            $roots = @($optix, (Join-Path $env:ProgramData "NVIDIA Corporation"), (Join-Path $env:ProgramFiles "NVIDIA Corporation"))
            $header = $roots | Where-Object { Test-Path $_ } |
                ForEach-Object { Get-ChildItem $_ -Recurse -Filter "optix.h" -ErrorAction SilentlyContinue } |
                Where-Object { $_.Directory.Name -eq "include" -and $_.Directory.Parent.Name -notmatch "internal" } |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if (-not $header) { throw "the OptiX SDK installer (exit code $code) left no include\optix.h under $($roots -join ', ')" }
            $root = $header.Directory.Parent.FullName
            [Environment]::SetEnvironmentVariable("OPTIX_ROOT_DIR", $root, "Machine")
            Write-Host "toolkits: OptiX SDK at $root (installer exit code $code)"
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
            $code = Invoke-Installer $file.FullName "-s -a --silent --eula accept" @(0, 3010)
            Write-Host "toolkits: Intel ocloc installer exit code $code"
            $oneapi = @((Join-Path ${env:ProgramFiles(x86)} "Intel\oneAPI"), (Join-Path $env:ProgramFiles "Intel\oneAPI"))
            if (-not (Register-Ocloc $oneapi $file.Name)) { $failed = $true; continue }
        } elseif ($file.Name -match "hip.*\.exe$") {
            if (-not (Test-Publisher $file.FullName $AMD)) { $failed = $true; continue }
            $code = Install-Hip $file
        } else {
            Write-Host "toolkits: $($file.Name) is not a known installer; skipped"
            continue
        }
        if ($code -eq 3010) { $restart = $true }
        Set-Content $installed (Get-Date -Format o)
    } catch {
        Write-Host "toolkits: FAILED $($file.Name): $_"
        $failed = $true
    }
}
if ($failed) { exit 1 }
if ($restart) { exit 3010 }
exit 0
