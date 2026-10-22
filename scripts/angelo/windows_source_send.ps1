# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  How the Windows build machine receives a source send and proves its tree is that commit (dot-sourced).

.DESCRIPTION
  source_send.py and source_tree.py (beside this file: in a send folder, the machine's source-tools folder or the
  tree's scripts\angelo) do the work; this file runs them with the host Python and keeps the bucket's
  input/confirmed-commit, the launcher's only delta base, equal to what the machine has proven:
    Receive-Send       apply a downloaded send folder; publish the commit once proven, clear the marker when the tree
                       is not provably it, keep it when the send was cut off or does not apply.
    Publish-Confirmed  prove the recorded confirmed commit again and publish it, or clear the marker.
    Assert-SentTree    refuse to go on unless the tree is exactly the commit sent (release_checks.py source-clean's
                       rule and words); on a machine no send made, nothing is checked.
    Get-SentCommit     the commit under test, for every check that names one (MSIs, manifests, the bench): the commit
                       sent, returned only once the tree is proven to be it. Never instance metadata or an environment
                       variable set at boot: a queued sync changes the tree while those keep the old value.
    Report-Leftovers   name the untracked files a stage wrote into the tree (they force the next send to be all of
                       HEAD); it removes nothing.
#>

$sendTools = $PSScriptRoot
$sendState = "C:\angelo\state"
$sendPython = "C:\Python313\python.exe"

function Invoke-SendTool([string[]]$Arguments) {
    if (-not (Test-Path $sendPython)) { & choco install -y --no-progress python313 --params "/InstallDir:C:\Python313" | Out-Host }
    if (-not (Test-Path $sendPython)) { throw "no Python at $sendPython to check the source tree with" }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = @(& $sendPython -I -B (Join-Path $sendTools "source_send.py") @Arguments 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
    $out | ForEach-Object { Write-Host $_ }
    [pscustomobject]@{ Code = $code; Text = ($out -join " / ") }
}

function Set-ConfirmedMarker([string]$Gcloud, [string]$Bucket, [string]$Commit) {
    $marker = "gs://$Bucket/input/confirmed-commit"
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        if ($Commit) {
            $file = Join-Path $sendState "confirmed-commit.upload"
            [System.IO.File]::WriteAllText($file, $Commit)
            $out = @(& $Gcloud storage cp --quiet $file $marker 2>&1 | ForEach-Object { "$_" })
        } else {
            $out = @(& $Gcloud storage rm --quiet $marker 2>&1 | ForEach-Object { "$_" })
        }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    $what = if ($Commit) { "= $Commit" } else { "cleared (no commit is confirmed)" }
    # A marker left stale is caught by the machine (a delta from another base does not apply), but never silently.
    if ($code -ne 0 -and ($Commit -or ($out -join " ") -notmatch "matched no objects|No URLs matched|not found")) {
        Write-Host ("source: FAILED to set {0} {1} (gcloud exit {2}): {3}" -f $marker, $what, $code, ($out -join " / "))
    } else {
        Write-Host ("source: {0} {1}" -f $marker, $what)
    }
}

function Publish-Confirmed([string]$Tree, [string]$Gcloud, [string]$Bucket) {
    $done = Invoke-SendTool @("confirmed", "--tree", $Tree, "--state", $sendState)
    $commit = if ($done.Code -eq 0) { (Get-Content (Join-Path $sendState "confirmed.commit")).Trim() } else { "" }
    Set-ConfirmedMarker $Gcloud $Bucket $commit
}

function Receive-Send([string]$Folder, [string]$Tree, [string]$Gcloud, [string]$Bucket, [string]$Commit) {
    $done = Invoke-SendTool @("receive", "--folder", $Folder, "--tree", $Tree, "--state", $sendState, "--commit", $Commit)
    switch ($done.Code) {
        0 { Set-ConfirmedMarker $Gcloud $Bucket $Commit; return }
        3 { Set-ConfirmedMarker $Gcloud $Bucket ""; throw "the tree is not provably $Commit after its send: $($done.Text)" }
        4 { Publish-Confirmed $Tree $Gcloud $Bucket; throw "the send does not apply to this tree; nothing changed, send again: $($done.Text)" }
        default { throw "receiving $Folder failed (exit $($done.Code)); the confirmed commit is unchanged: $($done.Text)" }
    }
}

function Assert-SentTree([string]$SourceDir) {
    if (-not (Test-Path (Join-Path $sendState "sent.commit"))) { return }   # not a tree a send made
    $done = Invoke-SendTool @("verify", "--tree", $SourceDir, "--state", $sendState)
    if ($done.Code -ne 0) { throw "refused: $SourceDir is not the commit it was sent: $($done.Text)" }
}

function Get-SentCommit([string]$SourceDir) {
    $sent = Join-Path $sendState "sent.commit"
    if (-not (Test-Path $sent)) { return "" }   # a tree no send made has no commit under test
    Assert-SentTree $SourceDir
    (Get-Content $sent).Trim()
}

function Report-Leftovers([string]$SourceDir, [string]$Stage) {
    $done = Invoke-SendTool @("leftovers", "--tree", $SourceDir)
    if ($done.Text -notmatch "leftovers: 0 untracked") { Write-Host "source: the $Stage stage left untracked files in $SourceDir" }
}
