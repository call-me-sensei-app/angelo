# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Use-SignedMsi: the second pass of a remote-signed release takes the signing VM's copy of the MSI it packed only when
  the copy holds exactly that MSI.

.DESCRIPTION
  Signing an MSI adds its two signature streams and rewrites the compound file, so its bytes always change. The copy
  is first read as data, never run: release_checks.py msi-same (msi_compound.py) compares every stream and storage
  (every table, the summary information, every cabinet and Binary-table entry), all but the signature streams, with
  the packed MSI's. Running it (an administrative install) would execute its AdminExecuteSequence custom actions
  before it is proven ours. Only then does the copy replace the packed MSI. sign_windows.ps1 -Mode VerifyMsi then checks
  its signature and the files it installs. Used by build_release_windows.ps1 -SignedMsi.

  The two passes are bound: the first (Save-PackedState) records the commit, the MSI's SHA-256 and the folder in
  angelo-presigned-pass1.json beside the build, and leaves the MSI in the results under a name that says it awaits
  its signature; the second takes a signed copy only for that same MSI from that same commit (Assert-PackedState).
  Dot-source this file; it defines functions and nothing else.
#>

function Save-PackedState([string]$BuildDir, [string]$Commit, [string]$Msi, [string]$ResultsDir) {
    if (-not $Commit) { throw "a remote-signed release needs its source commit (the build names none)" }
    $sha = (Get-FileHash -LiteralPath $Msi -Algorithm SHA256).Hash.ToLowerInvariant()
    $state = [ordered]@{ kind = "angelo-presigned-pass1"; commit = $Commit; msi = (Split-Path $Msi -Leaf); sha256 = $sha
        packed = (Get-Date -Format o) }
    [IO.File]::WriteAllText((Join-Path $BuildDir "angelo-presigned-pass1.json"), ($state | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    if ($ResultsDir) {   # Never under the release's own name: this MSI is not a release until its signature is checked.
        Copy-Item -LiteralPath $Msi -Destination (Join-Path $ResultsDir ([IO.Path]::GetFileNameWithoutExtension($Msi) + ".awaiting-signature.msi")) -Force
    }
    return [ordered]@{ name = $state.msi; sha256 = $sha; state = "packed with the signing VM's files; awaiting its MSI signature (-SignedMsi)" }
}

function Assert-PackedState([string]$BuildDir, [string]$Commit, [string]$Msi) {
    if (-not $Commit) { throw "-SignedMsi: this build names no source commit, so its first pass cannot be matched" }
    $path = Join-Path $BuildDir "angelo-presigned-pass1.json"
    if (-not (Test-Path -LiteralPath $path)) { throw "-SignedMsi: no first pass recorded in $BuildDir (angelo-presigned-pass1.json)" }
    $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $sha = (Get-FileHash -LiteralPath $Msi -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($state.commit -cne $Commit -or $state.msi -cne (Split-Path $Msi -Leaf) -or $state.sha256 -cne $sha) {
        throw "-SignedMsi: the first pass packed $($state.msi) $($state.sha256) at $($state.commit), not $(Split-Path $Msi -Leaf) $sha at $Commit"
    }
}

function Use-SignedMsi([string]$Packed, [string]$Signed, [string]$SignScript, [string]$VerifyReport, [string]$TimestampUrl,
                       [string]$Python, [string]$ReleaseChecks, [string]$BuildDir, [string]$Commit) {
    Assert-PackedState $BuildDir $Commit $Packed
    & $Python -I -B $ReleaseChecks msi-same --packed $Packed --signed $Signed
    if ($LASTEXITCODE -ne 0) { throw "the signed MSI is not the MSI this build packed (release_checks.py msi-same)" }
    Copy-Item -LiteralPath $Signed -Destination $Packed -Force
    & $SignScript -Mode VerifyMsi -Path $Packed -Report $VerifyReport
    $verified = Get-Content -LiteralPath $VerifyReport -Raw | ConvertFrom-Json
    return [ordered]@{ certificate = $verified.certificate; backend = "Presigned (signed on the signing VM)"; timestamp_url = $TimestampUrl
        msi_binaries_verified = $verified.verified; vendor_signed = @($verified.vendor_signed).Count; same_as_packed = "msi-same: every stream" }
}
