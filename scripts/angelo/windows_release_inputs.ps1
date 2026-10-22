# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The release stage's inputs on the Windows build machine: the libraries at the commit's pin and the agent runtime.

.DESCRIPTION
  Dot-sourced by windows_cloud_bootstrap.ps1 for a release run, after the GPU toolkits: it runs in the bootstrap's
  scope and uses its $gcloud, $bucket, $root, $src and Get-Meta. It brings lib\windows_x64 to angelo-libs-sha, reads
  the toolkit installers' machine-wide variables, fetches the agent runtime from gs://<bucket>/agent-runtime/ (each
  file checked against its SHA256SUMS) and sets ANGELO_LIBS_SHA and ANGELO_AGENT_RUNTIME_DIR for the build. A
  remote-signed release's inputs (gs://<bucket>/release-signing/, gcp_windows_validate.sh release-signing) are fetched
  into C:\angelo\release-signing, every file checked against its SHA256SUMS and none admitted without one, and named
  in ANGELO_RELEASE_SIGNING_DIR, only when the run's metadata angelo-release-sign is presigned.
#>
# The libraries at the commit this source pins (the source stage cloned them once, at the pin of that time).
$libs = Join-Path $src "lib\windows_x64"
$want = Get-Meta "angelo-libs-sha" ""
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$have = (git -C $libs rev-parse HEAD 2>$null)
if ($want -and $have -ne $want) {
    Write-Host "== stage: libraries $have -> $want"
    $env:GIT_LFS_SKIP_SMUDGE = "1"
    git -C $libs fetch -q origin 2>&1 | Out-Host
    git -C $libs checkout -q $want 2>&1 | Out-Host
    $env:GIT_LFS_SKIP_SMUDGE = ""
    git -C $libs lfs pull 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0 -or (git -C $libs rev-parse HEAD 2>$null) -ne $want) {
        $ErrorActionPreference = $old
        throw "the Windows libraries could not be brought to $want"
    }
}
$ErrorActionPreference = $old
foreach ($name in "CUDA_PATH", "HIP_PATH", "OPTIX_ROOT_DIR", "OCLOC_INSTALL_DIR") {
    $value = [Environment]::GetEnvironmentVariable($name, "Machine")
    if ($value) { Set-Item "env:$name" $value }
}
$env:ANGELO_LIBS_SHA = $want
# The agent runtime (agent_runtime.json): fetched afresh for every release run, so the build bundles exactly
# what the bucket holds now, each file checked against the uploaded SHA256SUMS. None uploaded: the build stops
# at once unless angelo-release-allow-missing names agent-runtime.
$runtimeDir = Join-Path $root "agent-runtime"
Remove-Item $runtimeDir -Recurse -Force -ErrorAction SilentlyContinue
$env:ANGELO_AGENT_RUNTIME_DIR = ""
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$runtimeObjects = @(& $gcloud storage ls "gs://$bucket/agent-runtime/" 2>$null | Where-Object { $_ -is [string] -and $_ -notmatch "/$" })
if ($runtimeObjects) {
    New-Item -ItemType Directory -Force $runtimeDir | Out-Null
    & $gcloud storage cp --quiet "gs://$bucket/agent-runtime/*" $runtimeDir 2>&1 | Out-Null
}
$ErrorActionPreference = $old
if ($runtimeObjects) {
    $sums = Join-Path $runtimeDir "SHA256SUMS"
    if (-not (Test-Path $sums)) { throw "gs://$bucket/agent-runtime/ has no SHA256SUMS" }
    foreach ($line in Get-Content $sums) {
        if (-not $line.Trim()) { continue }
        $digest, $file = $line.Trim() -split "\s+\*?", 2
        $path = Join-Path $runtimeDir $file
        $have = if (Test-Path $path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { "missing" }
        if ($have -ne $digest.ToLowerInvariant()) { throw "agent runtime: $file is $have, SHA256SUMS says $digest" }
    }
    $env:ANGELO_AGENT_RUNTIME_DIR = $runtimeDir
    Write-Host "agent runtime: $(@(Get-ChildItem $runtimeDir -Filter *.exe).Count) executables from gs://$bucket/agent-runtime/ (SHA256SUMS checked)"
} else {
    Write-Host "agent runtime: none in gs://$bucket/agent-runtime/"
}
# A remote-signed release (doc/angelo/releasing.rst, "Or SSL.com eSigner"): the signing VM's files, all of them summed.
$signingDir = Join-Path $root "release-signing"
Remove-Item $signingDir -Recurse -Force -ErrorAction SilentlyContinue
$env:ANGELO_RELEASE_SIGNING_DIR = ""
# Only when this run asks for it (metadata angelo-release-sign=presigned, set per run by the driver): inputs left in the
# bucket by an earlier release never switch a later run, a bench preparation included, to signing.
$signMode = Get-Meta "angelo-release-sign" ""
if ($signMode -and $signMode -cne "presigned") { throw "metadata angelo-release-sign is '$signMode', not presigned" }
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$signingSums = if ($signMode) { @(& $gcloud storage ls "gs://$bucket/release-signing/SHA256SUMS" 2>$null) } else { @() }
$ErrorActionPreference = $old
if ($signMode -and -not $signingSums) { throw "this run signs (angelo-release-sign=presigned), but gs://$bucket/release-signing/ has no SHA256SUMS" }
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
if ($signingSums) {
    New-Item -ItemType Directory -Force $signingDir | Out-Null
    & $gcloud storage cp --quiet --recursive "gs://$bucket/release-signing/*" $signingDir 2>&1 | Out-Null
}
$ErrorActionPreference = $old
if ($signingSums) {
    $named = @{}
    foreach ($line in Get-Content (Join-Path $signingDir "SHA256SUMS")) {
        if (-not $line.Trim()) { continue }
        $digest, $file = $line.Trim() -split "\s+\*?", 2
        if ($file -match "(^|/)\.\.(/|$)" -or $file.StartsWith("/")) { throw "release signing: SHA256SUMS names $file, outside its folder" }
        $path = Join-Path $signingDir ($file -replace "/", "\")
        $have = if (Test-Path -LiteralPath $path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { "missing" }
        if ($have -ne $digest.ToLowerInvariant()) { throw "release signing: $file is $have, SHA256SUMS says $digest" }
        $named[(Resolve-Path -LiteralPath $path).ProviderPath.ToLowerInvariant()] = $true
    }
    $unnamed = @(Get-ChildItem -LiteralPath $signingDir -Recurse -File | Where-Object { $_.FullName -ne (Join-Path $signingDir "SHA256SUMS") -and -not $named[$_.FullName.ToLowerInvariant()] })
    if ($unnamed) { throw "release signing: $($unnamed.Count) files SHA256SUMS does not name ($($unnamed[0].FullName))" }
    $env:ANGELO_RELEASE_SIGNING_DIR = $signingDir
    # The signed MSI the signing VM recorded (owner-set metadata): the second pass takes no other.
    $env:ANGELO_RELEASE_SIGNED_MSI_SHA256 = Get-Meta "angelo-release-signed-msi-sha256" ""
    Write-Host "release signing: $($named.Count) files from gs://$bucket/release-signing/ (SHA256SUMS checked); this release is signed (Presigned)"
}
