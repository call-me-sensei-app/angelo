# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The full-feature Windows x64 build and its MSI: the Windows counterpart of build_release_macos.sh.

.DESCRIPTION
  Blender's blender_release.cmake (Cycles with the CUDA, OptiX, HIP, HIP RT and oneAPI devices and their
  precompiled kernels, as Blender's own Windows release; OpenVDB, USD, Alembic, FFmpeg, audio, i18n and the rest of
  the release feature set) plus the Explorer thumbnail handler BlendThumb.dll, with Angelo's identity, icons and
  installer art from scripts/modules/angelo_identity.json. make.bat lite stays the editor-validation build.

  Configures with the Visual Studio 2022 generator (as make.bat) and its ClangCL toolset: clang-cl and lld-link, the
  compiler and flags of official Blender's Windows release (windows_release_toolchain.ps1; refused when Visual Studio
  lacks them), in vcvars64's developer environment. With WITH_STRICT_BUILD_OPTIONS, a library or GPU
  toolkit that CMake cannot find fails the configure instead of quietly turning a release feature off. Then it
  checks the cache for every release feature before compiling, builds the INSTALL target and, with -Package, runs
  cpack -G WIX for Angelo-<version>-windows-x64.msi. The built app must report clang-cl's flags (release_checks.py
  windows-toolset). angelo-release-build.json in the build folder records the commit, libraries, toolset (compiler,
  linker, generator toolset), toolkits, features and the MSI's size and SHA-256.

  With -Sign (a release; needs -Package), every binary CPack stages is Authenticode-signed before WiX packs it,
  then the MSI is signed and checked file by file (scripts/angelo/sign_windows.ps1: a Cloud KMS key through the
  Cloud KMS CNG provider, or Azure Artifact Signing with ANGELO_SIGN_BACKEND=ArtifactSigning; RFC 3161 timestamps). The certificate, KMS key and signtool are checked before anything
  compiles, and so is the shipped update_keys.json (a release must be able to verify the update feed;
  -AllowNoUpdateKey is for rehearsals). Runbook: doc/angelo/releasing.rst.

  Never fetches dependencies or Git LFS objects, never signs unless -Sign is given, never installs or touches an
  installed Blender.
  GPU toolkits are found where Blender's CMake looks for them (doc/angelo/native_editor_build.rst):
    CUDA    CUDA_PATH (set by the CUDA Toolkit installer); 12.8 or newer for the sm_120 kernels
    ocloc   OCLOC_INSTALL_DIR, the folder with Intel's ocloc.exe (the oneAPI kernels; not in Blender's libraries)
    OptiX   OPTIX_ROOT_DIR, the SDK's root with include\optix.h; 8.0 or newer
    HIP     HIP_PATH (set by the AMD HIP SDK installer), with bin\hipcc.exe; 6.0 or newer
    HIP RT, oneAPI and Level Zero come with the pinned libraries (lib\windows_x64\hiprt, dpcpp, level-zero).
  -AllowMissing cuda,optix,hip,hiprt,oneapi builds without the named GPU parts: the manifest records them, and
  such a build is not equivalent to Blender's Windows release.

  The agent runtime (the patched OpenAI Codex build Angelo bundles; scripts\addons_core\angelo_agent\agent_runtime.json)
  comes from -AgentRuntimeDir (or ANGELO_AGENT_RUNTIME_DIR): codex.exe and codex-code-mode-host.exe, with the optional
  sandbox helpers, built for Windows x64: the same folder, by the same variable, as build_release_macos.sh. They are
  checked before anything compiles (x64 executables; OpenAI's unchanged helpers still validly signed by OpenAI; the
  pinned version from codex.exe --version, cacheAffinityKey in its app-server schema) and installed into
  <install>\agent-runtime, where the installed copies must be the checked ones. With -Sign, codex.exe is signed with
  Angelo's certificate and OpenAI's helpers keep OpenAI's signature (sign_windows.ps1). A release never ships
  without them: -AllowMissing agent-runtime builds a rehearsal without the runtime (Codex connections then use the
  person's own Codex), which the manifest records, and which -Sign refuses.

.EXAMPLE
  .\scripts\angelo\build_release_windows.ps1 -BuildDir C:\angelo\build_windows_Release_x64_vc17_clangcl_Release -Package

.EXAMPLE
  .\scripts\angelo\build_release_windows.ps1 -Package -Sign -SignCertificate C:\angelo\codesign.crt `
    -SignKmsKey projects/<project>/locations/<location>/keyRings/<keyring>/cryptoKeys/<key>/cryptoKeyVersions/<n>
  (or set ANGELO_SIGN_CERTIFICATE and ANGELO_SIGN_KMS_KEY; -Sign has no default key)
#>
param(
    [string]$SourceDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    # Default: build_windows_Release_x64_vc17_clangcl_Release next to the source: make.bat's name, with the toolset.
    [string]$BuildDir = "",
    # Default: <source>\lib\windows_x64, as Blender's CMake expects it (a git clone with its LFS objects).
    [string]$LibDir = "",
    # MSBuild projects built at once; MultiToolTask runs clang-cl on every core within a project and
    # EnforceProcessCountAcrossBuilds caps the compilers of all projects together at the core count.
    # 0 (default): Get-DefaultBuildJobs scales it with this machine's cores and memory (16 on a 176-core build VM).
    [int]$Jobs = 0,
    [string[]]$AllowMissing = @(),
    # The agent runtime's files for Windows x64 (codex.exe, codex-code-mode-host.exe, optional sandbox helpers).
    [string]$AgentRuntimeDir = $env:ANGELO_AGENT_RUNTIME_DIR,
    [switch]$ConfigureOnly,
    [switch]$Package,
    # Copies of the manifest, the logs and the MSI for an unattended run; empty = do not copy.
    [string]$ResultsDir = "",
    # Authenticode signing (see sign_windows.ps1). The certificate file and key name only, never a key.
    [switch]$Sign,
    [string]$SignCertificate = $env:ANGELO_SIGN_CERTIFICATE,
    [string]$SignKmsKey = $env:ANGELO_SIGN_KMS_KEY,
    # Empty: sign_windows.ps1 picks its backend's service (ANGELO_SIGN_BACKEND: KmsKey or ArtifactSigning, whose
    # inputs it reads from the environment: ANGELO_SIGN_PUBLISHER, ANGELO_SIGN_ARTIFACT_DLIB and
    # ANGELO_SIGN_ARTIFACT_ENDPOINT / _ACCOUNT / _PROFILE).
    [string]$SignTimestampUrl = $env:ANGELO_SIGN_TIMESTAMP_URL,
    [string]$SignChainFile = $env:ANGELO_SIGN_CHAIN_FILE,
    # Remote signing, second pass (-Sign, ANGELO_SIGN_BACKEND=Presigned): the signing VM's copy of the packed MSI.
    [string]$SignedMsi = "",
    [switch]$AllowNoUpdateKey
)
$ErrorActionPreference = "Stop"
$started = Get-Date

if ($env:OS -ne "Windows_NT") { throw "This preset is for Windows x64; on macOS use build_release_macos.sh." }
if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") { throw "This preset builds x64 on an x64 machine (found $env:PROCESSOR_ARCHITECTURE)." }
# Nothing below may smudge LFS content or reach the network.
$env:GIT_LFS_SKIP_SMUDGE = "1"
# Logged and quiet runs, the LFS pointer checks, the GPU toolkits and the clang-cl toolset.
. (Join-Path $PSScriptRoot "windows_release_toolchain.ps1")

$SourceDir = (Resolve-Path $SourceDir).Path
if (-not $BuildDir) { $BuildDir = Get-AngeloReleaseBuildDir $SourceDir }
# CMake cannot switch an existing cache's toolset: a folder configured with another one is never touched.
Assert-BuildDirToolset $BuildDir
if (-not $LibDir) { $LibDir = Join-Path $SourceDir "lib\windows_x64" }
if ($BuildDir -match "\s" -or $SourceDir -match "\s") { throw "Blender's Windows build needs paths without spaces: $SourceDir, $BuildDir" }
New-Item -ItemType Directory -Force $BuildDir | Out-Null
if ($ResultsDir) { New-Item -ItemType Directory -Force $ResultsDir | Out-Null }
$allowed = @($AllowMissing | ForEach-Object { $_ -split "[,+ ]" } | Where-Object { $_ } | ForEach-Object { $_.ToLowerInvariant() })
foreach ($name in $allowed) {
    if ($name -notin "cuda", "optix", "hip", "hiprt", "oneapi", "agent-runtime") { throw "-AllowMissing knows cuda, optix, hip, hiprt, oneapi and agent-runtime, not '$name'" }
}
$presigned = $Sign -and $env:ANGELO_SIGN_BACKEND -eq "Presigned"
if ($SignedMsi -and -not ($presigned -and (Test-Path -LiteralPath $SignedMsi -PathType Leaf))) { throw "-SignedMsi finishes a remote-signed release (-Sign, ANGELO_SIGN_BACKEND=Presigned) with the signing VM's MSI: $SignedMsi" }
if ($Sign -and "agent-runtime" -in $allowed) { throw "-Sign builds a release, and a release bundles the agent runtime: drop -AllowMissing agent-runtime and give -AgentRuntimeDir" }

# --- Release signing inputs (checked first: a missing provider must not cost a build) ------------------------
$signScript = Join-Path $SourceDir "scripts\angelo\sign_windows.ps1"
if ($Sign) {
    if (-not $Package) { throw "-Sign signs the MSI; give -Package too" }
    $env:ANGELO_SIGN_CERTIFICATE = $SignCertificate; $env:ANGELO_SIGN_KMS_KEY = $SignKmsKey
    $env:ANGELO_SIGN_TIMESTAMP_URL = $SignTimestampUrl; $env:ANGELO_SIGN_CHAIN_FILE = $SignChainFile
    # The publisher every release must name (the updater compares it): Artifact Signing's expected name by default.
    $signPublisher = (Get-Content (Join-Path $SourceDir "scripts\modules\angelo_identity.json") -Raw -Encoding UTF8 | ConvertFrom-Json).windows.authenticode_publisher
    if (-not $env:ANGELO_SIGN_PUBLISHER) { $env:ANGELO_SIGN_PUBLISHER = $signPublisher.name }
    & $signScript -Mode Preflight -Report (Join-Path $BuildDir "angelo-sign-preflight.json")
    # The shipped key list: a release that cannot verify the update feed never finds an update.
    $keys = Get-Content (Join-Path $SourceDir "scripts\addons_core\angelo_agent\update_keys.json") -Raw | ConvertFrom-Json
    $active = @($keys.keys | Where-Object { $_.status -ne "revoked" -and $_.public_key -and ([Convert]::FromBase64String($_.public_key)).Length -eq 32 })
    if (-not $active -and -not $AllowNoUpdateKey) {
        throw "update_keys.json has no active key: add the Cloud KMS update key (sign_update_feed.py add-key) or pass -AllowNoUpdateKey for a rehearsal"
    }
    $cmakeVersion = [version](((& cmake --version) | Select-Object -First 1) -replace "[^\d.]", "")
    if ($cmakeVersion -lt [version]"3.19") { throw "CMake ${cmakeVersion}: signing the staged files needs CPack pre-build scripts (3.19+)" }
}

# --- Libraries and source assets --------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $LibDir ".git"))) {
    throw "$LibDir is not a clone of Blender's lib-windows_x64; fetch the pinned libraries first (doc/angelo/native_editor_build.rst)."
}
$libsHead = Invoke-Quiet { git -C $LibDir rev-parse HEAD }
# The validation machine's source is an archive (git init, no commits): it reads the pin from ANGELO_LIBS_SHA.
$pinned = Invoke-Quiet { git -C $SourceDir ls-tree HEAD lib/windows_x64 } | ForEach-Object { ($_ -split "\s+")[2] }
if (-not $pinned) { $pinned = $env:ANGELO_LIBS_SHA }
if ($pinned -and $libsHead -and $pinned -ne $libsHead) { Write-Warning "$LibDir is at $libsHead, this source pins $pinned" }
$fetchHint = "the pinned libraries are not fully fetched (git -C lib\windows_x64 lfs pull)"
foreach ($folder in "openvdb", "usd", "ffmpeg", "OpenImageDenoise", "embree", "osl", "python", "tbb", "openal") {
    Assert-RealFiles (Join-Path $LibDir $folder) @("*.dll", "*.lib") $fetchHint
}
if ("hiprt" -notin $allowed) { Assert-RealFiles (Join-Path $LibDir "hiprt") @("*") $fetchHint }
if ("oneapi" -notin $allowed) { Assert-RealFiles (Join-Path $LibDir "dpcpp") @("*.dll", "*.exe") $fetchHint }
# Level Zero: level-zero, as the pinned libraries name it (platform_win32.cmake reads either spelling).
$levelZero = @("level-zero", "level_zero") | ForEach-Object { Join-Path $LibDir $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ("oneapi" -notin $allowed) { Assert-RealFiles $(if ($levelZero) { $levelZero } else { Join-Path $LibDir "level-zero" }) @("*.dll", "*.lib") $fetchHint }
foreach ($probe in "release\datafiles\startup.blend", "release\datafiles\splash.png", "release\datafiles\icons_blend\ui.blend") {
    $path = Join-Path $SourceDir $probe
    if ((Test-Path $path) -and (Test-Pointer $path)) { throw "$path is a Git LFS pointer; materialize the build's large files first." }
}
# The add-on ships Git LFS files too (its images, the Human Base Meshes): a pointer there fails here, not after install.
foreach ($folder in "assets", "release\windows", "release\branding", "scripts\addons_core\angelo_agent") {
    $path = Join-Path $SourceDir $folder
    if (-not (Test-Path $path)) { throw "$path is missing" }
    $pointer = Get-ChildItem $path -Recurse -File | Where-Object { Test-Pointer $_.FullName } | Select-Object -First 1
    if ($pointer) { throw "$($pointer.FullName) is a Git LFS pointer; materialize the build's large files first." }
}
Write-Host "Libraries: $LibDir ($libsHead)"

# --- Agent runtime (scripts\addons_core\angelo_agent\agent_runtime.json; checked before anything compiles) ----------
$runtimePin = Get-Content (Join-Path $SourceDir "scripts\addons_core\angelo_agent\agent_runtime.json") -Raw | ConvertFrom-Json
$runtimeFiles = $runtimePin.platforms.windows
$agentRuntime = [ordered]@{ version = $runtimePin.version; fork_commit = $runtimePin.fork.commit; folder = $null; files = @()
    sha256 = [ordered]@{} }
if (-not $AgentRuntimeDir) {
    if ("agent-runtime" -notin $allowed) {
        throw ("The Windows agent runtime is not supplied: build Codex $($runtimePin.version) with cacheAffinityKey (branch " +
            "$($runtimePin.fork.branch)) for x86_64-pc-windows-msvc and pass its folder ($($runtimeFiles.required -join ', ')) " +
            "as -AgentRuntimeDir or ANGELO_AGENT_RUNTIME_DIR (doc/angelo/releasing.rst, 'Agent runtime'). " +
            "-AllowMissing agent-runtime builds a rehearsal without it.")
    }
    $agentRuntime.folder = "omitted (-AllowMissing agent-runtime)"
} else {
    $AgentRuntimeDir = (Resolve-Path $AgentRuntimeDir).Path
    foreach ($name in $runtimeFiles.required) {
        $path = Join-Path $AgentRuntimeDir $name
        if (-not (Test-Path $path -PathType Leaf)) { throw "$AgentRuntimeDir lacks $name (the agent runtime is $($runtimeFiles.required -join ', '))" }
    }
    foreach ($name in @(@($runtimeFiles.required) + @($runtimeFiles.optional))) {
        $path = Join-Path $AgentRuntimeDir $name
        if (-not (Test-Path $path -PathType Leaf)) { continue }
        # An x64 executable: PE32+ (optional header magic 0x20B), machine 0x8664, not a DLL (characteristic 0x2000).
        $bytes = New-Object byte[] 4096
        $stream = [System.IO.File]::OpenRead($path)
        try { $read = $stream.Read($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
        $pe = if ($read -ge 64 -and $bytes[0] -eq 0x4D -and $bytes[1] -eq 0x5A) { [BitConverter]::ToInt32($bytes, 0x3C) } else { -1 }
        $x64 = $pe -gt 0 -and $pe + 26 -lt $read -and [BitConverter]::ToUInt32($bytes, $pe) -eq 0x4550 -and
            [BitConverter]::ToUInt16($bytes, $pe + 4) -eq 0x8664 -and [BitConverter]::ToUInt16($bytes, $pe + 24) -eq 0x20B -and
            -not ([BitConverter]::ToUInt16($bytes, $pe + 22) -band 0x2000)
        if (-not $x64) { throw "$path is not an x64 Windows executable" }
        # OpenAI's unchanged release files ship with OpenAI's own signature; never one re-signed, stripped or changed.
        if ($name -in @($runtimeFiles.upstream_signed)) {
            $signature = Get-AuthenticodeSignature -LiteralPath $path
            $signer = if ($signature.SignerCertificate) {
                $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { "" }
            if ($signature.Status -ne "Valid" -or $signer -ne $runtimeFiles.upstream_signer) {
                throw "$path must carry $($runtimeFiles.upstream_signer)'s valid signature (it is $($signature.Status), signer '$signer'): take it from Codex's release $($runtimePin.upstream.tag) again"
            }
        }
        $agentRuntime.sha256[$name] = (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $codexExe = Join-Path $AgentRuntimeDir $runtimeFiles.required[0]
    $runtimeHome = Join-Path $env:TEMP ("angelo-agent-runtime-{0}" -f [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force $runtimeHome | Out-Null
    $savedHome = $env:CODEX_HOME; $env:CODEX_HOME = $runtimeHome
    try {
        $reported = ((Invoke-Quiet { & $codexExe --version }) | Select-Object -First 1)
        if ("$reported".Trim() -notmatch ("\s" + [regex]::Escape($runtimePin.version) + "$")) {
            throw "$codexExe --version says '$reported'; this source pins Codex $($runtimePin.version)"
        }
        $schema = Join-Path $runtimeHome "schema"
        Invoke-Quiet { & $codexExe app-server generate-json-schema --experimental --out $schema } | Out-Null
        foreach ($request in "ThreadStartParams", "ThreadResumeParams", "ThreadForkParams") {
            $file = Join-Path $schema "v2\$request.json"
            $properties = if (Test-Path $file) { (Get-Content $file -Raw | ConvertFrom-Json).properties } else { $null }
            foreach ($feature in $runtimePin.features) {
                if (-not $properties -or -not ($properties.PSObject.Properties.Name -contains $feature)) {
                    throw "$codexExe's $request has no ${feature}: this is not the patched build (stock Codex reports the same version)"
                }
            }
        }
    } finally {
        $env:CODEX_HOME = $savedHome
        Remove-Item $runtimeHome -Recurse -Force -ErrorAction SilentlyContinue
    }
    $agentRuntime.folder = $AgentRuntimeDir
    $agentRuntime.files = @(@($runtimeFiles.required) + @($runtimeFiles.optional) | Where-Object { Test-Path (Join-Path $AgentRuntimeDir $_) })
}
Write-Host ("Agent runtime: Codex {0} from {1}" -f $runtimePin.version, $agentRuntime.folder)

# --- Toolchain --------------------------------------------------------------------------------------------
foreach ($tool in "git", "cmake") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not on PATH" }
}
if ($Package) {
    foreach ($tool in "candle", "light") {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool (WiX Toolset 3) is not on PATH" }
    }
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found: Visual Studio 2022 or its Build Tools are not installed" }
$vs = & $vswhere -products * -version "[17.0,18.0)" -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -latest -format json | ConvertFrom-Json
if (-not $vs) { throw "No Visual Studio 2022 instance with the x64 C++ tools" }
$vs = @($vs)[0]
Write-Host ("Visual Studio: {0} {1} at {2}" -f $vs.displayName, $vs.installationVersion, $vs.installationPath)
$toolset = Find-ClangClToolset $vs $vswhere
Write-Host ("Toolset: {0}, {1}, {2} -T {3}" -f $toolset.compiler, $toolset.linker, $toolset.generator, $toolset.generator_toolset)

# --- GPU toolkits (checked here: a missing one fails in seconds, not after hours of compiling) ------------------
$gpu = Get-GpuToolkits $allowed $LibDir
$toolkits = $gpu.toolkits; $cmakeGpu = $gpu.cmake
$toolkits.GetEnumerator() | ForEach-Object { Write-Host ("GPU {0}: {1}" -f $_.Key, $_.Value) }

# --- Configure --------------------------------------------------------------------------------------------
$release = Join-Path $SourceDir "build_files\cmake\config\blender_release.cmake"
$configureLog = Join-Path $BuildDir "angelo-configure.log"
# Blender's build info reads the source's git checkout: About, crash reports and support name the exact build by
# its hash. In a repository without that commit (an archive in an empty repository) buildinfo.cmake's git calls
# print "error: malformed object name HEAD" and MSBuild fails the build on that line; there the build info is off
# (the hash unknown) and the manifest records the archive's commit (ANGELO_SRC_COMMIT). That fallback is never a
# release: a package needs the commit checked out (release-build sends it to the validation machine).
$history = Invoke-Quiet { git -C $SourceDir rev-parse --verify -q "HEAD^{commit}" }
if ($Package -and -not $history) {
    throw "A release MSI needs Blender's build info (the commit's hash in About): build from a git checkout of the commit (gcp_windows_validate.sh release-build sends one)"
}
$arguments = @(
    "-S", "`"$SourceDir`"", "-B", "`"$BuildDir`"", "-G", "`"$($toolset.generator)`"", "-A", "x64", "-T", $toolset.generator_toolset,
    "`"-DCMAKE_GENERATOR_INSTANCE=$($vs.installationPath)`"", "-C", "`"$release`"",
    # Forward slashes: CMake writes LIBDIR into generated CMake code (cmake_install.cmake installs OSL's oslquery from
    # it), where C:\angelo\... reads as the invalid escape \a and INSTALL fails after the whole build.
    "`"-DLIBDIR=$($LibDir.Replace('\', '/'))`"",
    # A library or toolkit CMake cannot find is an error, never a release feature quietly turned off.
    "-DWITH_STRICT_BUILD_OPTIONS=ON",
    # Explorer thumbnails (BlendThumb.dll; blender_release.cmake switches the handler on only for macOS).
    "-DWITH_BLENDER_THUMBNAILER=ON",
    # As Blender's Windows build machines (build_files/buildbot/config/blender_windows.cmake).
    "-DHIPRT_COMPILER_PARALLEL_JOBS=6", "-DSYCL_OFFLINE_COMPILER_PARALLEL_JOBS=6",
    # The agent runtime, installed into <install>\agent-runtime (build_files\cmake\angelo_agent_runtime.cmake); a
    # release configure fails without it.
    "`"-DANGELO_AGENT_RUNTIME_DIR=$(if ($AgentRuntimeDir) { $AgentRuntimeDir.Replace('\', '/') } else { '' })`"",
    $(if ("agent-runtime" -in $allowed) { "-DANGELO_REQUIRE_AGENT_RUNTIME=OFF" } else { "-DANGELO_REQUIRE_AGENT_RUNTIME=ON" }),
    $(if ($history) { "-DWITH_BUILDINFO=ON" } else { "-DWITH_BUILDINFO=OFF" })
) + ($cmakeGpu | ForEach-Object { "`"$_`"" })
Write-Host ("cmake " + ($arguments -join " "))
Import-DeveloperEnvironment $toolset.vcvars
$code = Invoke-Logged $configureLog ("cmake " + ($arguments -join " "))
if ($code -ne 0) {
    # The errors themselves, in this script's output and the results (the configure log stays in the build folder).
    Select-String -Path $configureLog -Pattern "CMake Error" -Context 0, 12 | ForEach-Object { $_.Line; $_.Context.PostContext } | Out-Host
    if ($ResultsDir) { Copy-Item $configureLog $ResultsDir -Force -ErrorAction SilentlyContinue }
    throw "configure failed with $code ($configureLog)"
}
$disabled = Select-String -Path $configureLog -Pattern "not found, disabling (WITH_\w+)" | ForEach-Object { $_.Matches[0].Groups[1].Value }
if ($disabled) { throw "configure turned off $($disabled -join ', ') ($configureLog)" }
Assert-ConfiguredToolset $BuildDir $toolset
# nvcc's host cl.exe (and the other GPU kernel compilers) must not see clang's own headers.
Write-CustomCommandIncludePath $BuildDir
$toolset.custom_commands = "the MSVC toolset's include path (Directory.Build.targets)"

# Every release feature, read back from the cache: the options blender_release.cmake sets for Windows x64 and the
# thumbnailer, less the GPU parts -AllowMissing named.
$cache = @{}
Get-Content (Join-Path $BuildDir "CMakeCache.txt") | ForEach-Object {
    if ($_ -match "^(WITH_[A-Z0-9_]+):BOOL=(\w+)$") { $cache[$Matches[1]] = $Matches[2] }
}
$required = @(
    "WITH_ALEMBIC", "WITH_AUDASPACE", "WITH_BULLET", "WITH_CODEC_FFMPEG", "WITH_CODEC_SNDFILE", "WITH_CYCLES",
    "WITH_CYCLES_EMBREE", "WITH_CYCLES_OSL", "WITH_CYCLES_PATH_GUIDING", "WITH_DRACO", "WITH_FFTW3", "WITH_FREESTYLE",
    "WITH_HARU", "WITH_IMAGE_OPENJPEG", "WITH_IMAGE_WEBP", "WITH_INTERNATIONAL", "WITH_IO_FBX", "WITH_IO_PLY",
    "WITH_IO_STL", "WITH_IO_WAVEFRONT_OBJ", "WITH_MANIFOLD", "WITH_MATERIALX", "WITH_HYDRA", "WITH_MOD_FLUID",
    "WITH_MOD_OCEANSIM", "WITH_MOD_REMESH", "WITH_NANOVDB", "WITH_OPENAL", "WITH_OPENIMAGEDENOISE", "WITH_OPENSUBDIV",
    "WITH_OPENVDB", "WITH_OPENVDB_BLOSC", "WITH_POTRACE", "WITH_PYTHON_INSTALL", "WITH_QUADRIFLOW", "WITH_RUBBERBAND",
    "WITH_TBB", "WITH_TBB_MALLOC_PROXY", "WITH_USD", "WITH_WASAPI", "WITH_XR_OPENXR", "WITH_BLENDER_THUMBNAILER",
    "WITH_CYCLES_DEVICE_CUDA", "WITH_CYCLES_DEVICE_HIP"
)
if ("cuda" -notin $allowed) { $required += "WITH_CYCLES_CUDA_BINARIES" }
if ("optix" -notin $allowed) { $required += "WITH_CYCLES_DEVICE_OPTIX" }
if ("hip" -notin $allowed) { $required += "WITH_CYCLES_HIP_BINARIES" }
if ("hiprt" -notin $allowed) { $required += "WITH_CYCLES_DEVICE_HIPRT" }
if ("oneapi" -notin $allowed) { $required += "WITH_CYCLES_DEVICE_ONEAPI", "WITH_CYCLES_ONEAPI_BINARIES" }
$off = @($required | Where-Object { $cache[$_] -ne "ON" })
if ($off) { throw "release features off after configure: $($off -join ', ')" }
$features = [ordered]@{}
$required | Sort-Object | ForEach-Object { $features[$_] = $cache[$_] }

$identity = Get-Content (Join-Path $SourceDir "scripts\modules\angelo_identity.json") -Raw | ConvertFrom-Json
$msiName = "{0}-{1}-windows-x64.msi" -f $identity.product.name, $identity.version.label
$commit = if ($history) { $history } else { $env:ANGELO_SRC_COMMIT }
$manifest = [ordered]@{
    kind = "angelo-windows-release-build"; source_commit = $commit; libraries = $LibDir; libraries_commit = $libsHead
    build_info = $(if ($history) { "from the source's git history" } else { "off: the source is an archive without git history" })
    libraries_pinned = $pinned; visual_studio = "$($vs.displayName) $($vs.installationVersion)"; toolset = $toolset
    toolkits = $toolkits; allowed_missing = $allowed; release_equivalent = (-not $allowed); features = $features
    agent_runtime = $agentRuntime
    configured = (Get-Date -Format o); built = $null; msi = $null; signed = [bool]$Sign
}
function Save-Manifest {
    $json = $manifest | ConvertTo-Json -Depth 5
    $path = Join-Path $BuildDir "angelo-release-build.json"
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
    if ($ResultsDir) {
        Copy-Item $path $ResultsDir -Force
        Copy-Item (Join-Path $BuildDir "angelo-*.log") $ResultsDir -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $BuildDir "angelo-sign-*.json") $ResultsDir -Force -ErrorAction SilentlyContinue
    }
}
Save-Manifest
if ($ConfigureOnly) { Write-Host "Configured: $BuildDir"; exit 0 }

# --- Build and install --------------------------------------------------------------------------------------
$buildLog = Join-Path $BuildDir "angelo-build.log"
if ($Jobs -le 0) { $Jobs = Get-DefaultBuildJobs }
Write-Host "MSBuild projects at once: $Jobs"
$code = Invoke-Logged $buildLog "cmake --build `"$BuildDir`" --config Release --target INSTALL --parallel $Jobs -- /nologo /verbosity:minimal $($toolset.msbuild)"
if ($code -ne 0) { Save-Manifest; throw "build failed with $code ($buildLog)" }
$bin = Join-Path $BuildDir "bin\Release"
$exe = Join-Path $bin ($identity.windows.exe_stem + ".exe")
$installed = @($exe, (Join-Path $bin ($identity.windows.exe_stem + $identity.windows.launcher_suffix + ".exe")), (Join-Path $bin "BlendThumb.dll"))
if ($AgentRuntimeDir) { $installed += @($runtimeFiles.required | ForEach-Object { Join-Path $bin "$($runtimeFiles.folder)\$_" }) }
# A release build carries the marker that makes the app refuse another Codex in ANGELO_AGENT_RUNTIME.
if ("agent-runtime" -notin $allowed) { $installed += Join-Path $bin $runtimeFiles.release_marker }
foreach ($path in $installed) {
    if (-not (Test-Path $path)) { Save-Manifest; throw "$path is missing after the build" }
}
# The installed runtime is the checked one, byte for byte (an earlier configure's folder would install other files).
foreach ($name in $agentRuntime.sha256.Keys) {
    $copy = Join-Path $bin "$($runtimeFiles.folder)\$name"
    $hash = if (Test-Path $copy) { (Get-FileHash $copy -Algorithm SHA256).Hash.ToLowerInvariant() } else { "missing" }
    if ($hash -ne $agentRuntime.sha256[$name]) { Save-Manifest; throw "$copy is not the checked $name ($hash)" }
}
# No installed file may be a Git LFS pointer (release_checks.py lfs-pointers; the macOS build refuses one too): a file
# the checkout never materialized installs as its 130-byte stub. The files are the ones this INSTALL wrote.
$installManifest = Join-Path $BuildDir "install_manifest.txt"
$installedFiles = if (Test-Path $installManifest) { @(Get-Content $installManifest | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) }) }
    else { @(Get-ChildItem $bin -Recurse -File | ForEach-Object { $_.FullName }) }
$shippedPointers = @($installedFiles | Where-Object { (Get-Item -LiteralPath $_).Length -lt 1024 -and (Test-Pointer $_) })
if ($shippedPointers) {
    Save-Manifest
    throw "Git LFS pointers would ship ($($shippedPointers.Count)): $($shippedPointers -join ', '); materialize them from local objects (git lfs checkout) and rebuild"
}
# No builder path or credential may ship (doc/angelo/releasing.rst, "No builder paths"): every installed file,
# binaries included, scanned with the bundled Python (release_checks.py builder-paths; the macOS build runs it too).
$bundledPython = Get-ChildItem $bin -Recurse -Filter python.exe -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\python\\bin\\python\.exe$" } | Select-Object -First 1
if (-not $bundledPython) { Save-Manifest; throw "no bundled python.exe below $bin to run the builder-path scan" }
$releaseChecks = Join-Path $SourceDir "scripts\angelo\release_checks.py"
$pathsReport = Join-Path $BuildDir "angelo-builder-paths.json"
$builderFolders = @("--identity-path", $SourceDir, "--identity-path", $BuildDir)   # long and 8.3 forms
# What this INSTALL wrote (the LFS check above reads the same manifest); bin\Release also holds build-only output
# (makesdna/makesrna/msgfmt .pdb, .exp, .lib) that no installer carries. The MSI payload scan below is the final word.
$installedOnly = if (Test-Path $installManifest) { @("--files-from", $installManifest) } else { @() }
& $bundledPython.FullName -I -B $releaseChecks builder-paths $bin @builderFolders @installedOnly --report $pathsReport
if ($LASTEXITCODE -ne 0) { Save-Manifest; throw "builder paths or credentials would ship ($pathsReport)" }
$manifest.built = Get-Date -Format o
$manifest.binary = $exe
# The hash the build reports is the commit's: what About and crash reports will name.
$version = @(Invoke-Quiet { & $exe --factory-startup --version })
$reported = @($version | ForEach-Object { if ($_ -match "build hash:\s*(\S+)") { $Matches[1] } }) | Select-Object -First 1
$manifest.build_hash = $reported
if ($history -and (-not $reported -or -not $history.StartsWith($reported))) {
    Save-Manifest; throw "the build reports hash '$reported', not the source's $history"
}
# The app is clang-cl's build: the flags it reports and the images lld-link wrote (release_checks.py windows-toolset).
$toolsetReport = Join-Path $BuildDir "angelo-toolset.json"
$images = @($installed | Where-Object { $_ -match "\.(exe|dll)$" -and $_ -notmatch "\\$([regex]::Escape($runtimeFiles.folder))\\" })
& $bundledPython.FullName -I -B $releaseChecks windows-toolset --binary $exe @($images | ForEach-Object { "--pe"; $_ }) --report $toolsetReport
$toolset.reported = if (Test-Path $toolsetReport) { Get-Content $toolsetReport -Raw | ConvertFrom-Json } else { $null }
if ($LASTEXITCODE -ne 0) { Save-Manifest; throw "the build is not clang-cl's ($toolsetReport)" }

# --- Package ---------------------------------------------------------------------------------------------
if ($Package -and -not $SignedMsi) {
    Get-ChildItem $BuildDir -Filter "*.msi" -ErrorAction SilentlyContinue | Remove-Item -Force
    $packageLog = Join-Path $BuildDir "angelo-package.log"
    # A short packaging folder: WiX reads no staged path of 260 characters or more (windows_release_toolchain.ps1).
    $packageDir = Get-PackageStagingDir $BuildDir
    Get-ChildItem $packageDir -Filter "*.msi" -ErrorAction SilentlyContinue | Remove-Item -Force
    try { $longestStaged = Assert-StagedPathLengths $BuildDir $packageDir ([IO.Path]::GetFileNameWithoutExtension($msiName)) $bin $installedFiles }
    catch { Save-Manifest; throw }
    $manifest.package_staging = [ordered]@{ folder = $packageDir; longest_path = $longestStaged }
    $cpack = "cpack -G WIX -C Release -B `"$packageDir`""
    if ($Sign) {
        # CPack installs into its own staging folder (third-party DLLs come from lib\windows_x64 again), so the files
        # are signed there, just before WiX packs them (windows_sign_staging.cmake).
        $env:ANGELO_SIGN_SCRIPT = $signScript
        $env:ANGELO_SIGN_EXPECT = $identity.windows.exe_stem + ".exe"
        $env:ANGELO_SIGN_REPORT = Join-Path $BuildDir "angelo-sign-staging.json"
        $hook = (Join-Path $SourceDir "scripts\angelo\windows_sign_staging.cmake") -replace "\\", "/"
        $cpack += " -D `"CPACK_PRE_BUILD_SCRIPTS=$hook`""
    }
    $code = Invoke-Logged $packageLog "cd /d `"$BuildDir`" && $cpack"
    Get-ChildItem $packageDir -Filter "*.msi" -ErrorAction SilentlyContinue | Move-Item -Destination $BuildDir -Force
    $msi = Get-ChildItem $BuildDir -Filter "*.msi" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($code -ne 0 -or -not $msi) { Save-Manifest; throw "cpack -G WIX failed with $code ($packageLog)" }
    if ($msi.Name -ne $msiName) { Save-Manifest; throw "the MSI is named $($msi.Name), not $msiName" }
    if ($presigned) {   # A remote-signed release's first pass ends here: no receipt or record yet, so it cannot be published.
        . (Join-Path $SourceDir "scripts\angelo\windows_msi_compare.ps1")
        $manifest.msi = Save-PackedState $BuildDir $commit $msi.FullName $ResultsDir
        Save-Manifest
        Write-Host "Packed: $($msi.FullName). Sign it on the signing VM (sign_remote.sh msi), then run again with -SignedMsi."; exit 0
    }
    if ($Sign) {
        & $signScript -Mode Sign -Path $msi.FullName -Report (Join-Path $BuildDir "angelo-sign-msi.json")
        & $signScript -Mode VerifyMsi -Path $msi.FullName -Report (Join-Path $BuildDir "angelo-sign-verify-msi.json")
        $verified = Get-Content (Join-Path $BuildDir "angelo-sign-verify-msi.json") -Raw | ConvertFrom-Json
        $manifest.signing = [ordered]@{ certificate = $verified.certificate; kms_key = $SignKmsKey; timestamp_url = $SignTimestampUrl
            msi_binaries_verified = $verified.verified; vendor_signed = @($verified.vendor_signed).Count }
        $msi.Refresh()   # Signing changed its size and hash.
    }
}
if ($Package -and $SignedMsi) {   # Nothing is packed again (WiX writes new bytes): the copy must install the same.
    $msi = Get-Item -LiteralPath (Join-Path $BuildDir $msiName) -ErrorAction SilentlyContinue
    if (-not $msi) { Save-Manifest; throw "-SignedMsi: the first pass's $msiName is not in $BuildDir" }
    . (Join-Path $SourceDir "scripts\angelo\windows_msi_compare.ps1")
    try { $manifest.signing = Use-SignedMsi $msi.FullName (Resolve-Path -LiteralPath $SignedMsi).ProviderPath $signScript `
            (Join-Path $BuildDir "angelo-sign-verify-msi.json") $SignTimestampUrl $bundledPython.FullName $releaseChecks $BuildDir $commit; $msi.Refresh() } catch { Save-Manifest; throw }
}
if ($Package) {
    # The payload exactly as the MSI installs it, scanned again; the receipt ties the clean scan to these final bytes
    # (publish_installers.py refuses an MSI without a matching one).
    $payload = Join-Path $BuildDir "angelo-msi-payload"
    if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
    $payloadLog = Join-Path $BuildDir "angelo-msi-payload.log"
    $process = Start-Process msiexec.exe -ArgumentList @("/a", "`"$($msi.FullName)`"", "/qn", "TARGETDIR=`"$payload`"", "/l*v", "`"$payloadLog`"") -Wait -PassThru
    if ($process.ExitCode -ne 0) { Save-Manifest; throw "msiexec /a failed with $($process.ExitCode) ($payloadLog)" }
    $payloadExe = Get-ChildItem $payload -Recurse -Filter ($identity.windows.exe_stem + ".exe") -File | Select-Object -First 1
    if (-not $payloadExe) { Save-Manifest; throw "the MSI's payload holds no $($identity.windows.exe_stem).exe" }
    if ($Sign) {
        # Before the receipt (publish_installers.py takes only an MSI with one): the MSI and the app it installs name
        # angelo_identity.json's publisher, read and compared exactly as the updater reads and compares them, so a
        # certificate that writes the name otherwise fails this release, not every user's next update.
        & $bundledPython.FullName -I -B $releaseChecks authenticode-publisher --file $msi.FullName --file $payloadExe.FullName
        if ($LASTEXITCODE -ne 0) { Save-Manifest; throw "the signed MSI or app names another publisher than angelo_identity.json's" }
        $manifest.signing.publisher = "checked: MSI and $($payloadExe.Name)"
    }
    $receipt = "$($msi.FullName).builder-paths.json"
    & $bundledPython.FullName -I -B $releaseChecks builder-paths $payload @builderFolders --installer $msi.FullName --receipt $receipt `
        --report (Join-Path $BuildDir "angelo-builder-paths-msi.json")
    if ($LASTEXITCODE -ne 0) { Save-Manifest; throw "the MSI's payload carries builder paths or credentials" }
    # Only what the commit tracks (or builds from it) ships: an untracked file in the source tree would be in here.
    & $bundledPython.FullName -I -B $releaseChecks tracked-scripts (Join-Path $payload "Angelo\5.2\scripts") --source $SourceDir --libdir $LibDir
    if ($LASTEXITCODE -ne 0) { Save-Manifest; throw "the MSI's payload ships script files the commit does not track" }
    # The last gate: what this release is, declared by -Sign and never inferred, recorded for exactly these bytes
    # (msi_signing.py). Signed: the publisher check again; unsigned: neither the MSI nor the app carries a signature.
    # publish_installers.py publishes the MSI only as the kind its record names.
    $signing = if ($Sign) { "signed" } else { "unsigned" }
    & $bundledPython.FullName -I -B $releaseChecks msi-signing-record --installer $msi.FullName --signing $signing --file $payloadExe.FullName
    if ($LASTEXITCODE -ne 0) { Save-Manifest; throw "the MSI is not what this release declares it: $signing" }
    $manifest.msi_signing = $signing
    Remove-Item $payload -Recurse -Force
    $manifest.msi = [ordered]@{ name = $msi.Name; bytes = $msi.Length; sha256 = (Get-FileHash $msi.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    if ($ResultsDir) { Copy-Item $msi.FullName, $receipt, "$($msi.FullName).signing.json" $ResultsDir -Force }
    Write-Host ("MSI: {0} ({1:N0} MB)" -f $msi.FullName, ($msi.Length / 1MB))
}
$manifest.minutes = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
Save-Manifest
Write-Host "Native release build: $exe"
