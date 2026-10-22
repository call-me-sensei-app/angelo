# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The MSI an install check may use: only the build of the commit under test (dot-sourced; defines one function).

.DESCRIPTION
  Get-CommitMsi asks msi_commit.py (with the host Python the bootstrap installs) for the MSI proven by its build
  manifest to be the build of -Commit: the one given in -Msi, or the newest under C:\angelo whose name matches
  -Pattern. It throws, naming each MSI it refused and why, when there is none: an install check that took the newest
  MSI on the disk once installed and passed on an earlier run's build of another commit.
#>

function Get-CommitMsi([string]$SourceDir, [string]$Commit, [string]$Msi = "", [string]$Pattern = "*.msi") {
    $hostPython = "C:\Python313\python.exe"
    if (-not $Commit) { throw "the commit under test is unknown (no proven send, no -Commit): no MSI can be proven" }
    if (-not (Test-Path $hostPython)) { throw "no host Python at $hostPython to check which commit an MSI was built from" }
    $arguments = @("-I", "-B", (Join-Path $SourceDir "scripts\angelo\msi_commit.py"), "--commit", $Commit, "--pattern", $Pattern)
    $arguments += $(if ($Msi) { @("--msi", $Msi) } else { @("--search", "C:\angelo") })
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = @(& $hostPython @arguments 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
    $out | ForEach-Object { Write-Host $_ }
    if ($code -ne 0 -or -not $out) { throw ("no MSI of commit {0}: {1}" -f $Commit, ($out -join " / ")) }
    $out[-1].Trim()
}
