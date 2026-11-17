# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Loads the remote-command tool (windows_command_queue.ps1) at a machine's boot: the copy the sender published, when it
  checks out, else this tree's.

.DESCRIPTION
  gcp_windows_validate.sh sends this file as the angelo-queue-loader metadata with every up and redo, beside the boot
  script, and publishes its own windows_command_queue.ps1 with a SHA-256 manifest to gs://<bucket>/tools/
  (publish_tools; `gcp_windows_validate.sh tools` alone). windows_cloud_bootstrap.ps1 runs it in its own scope, where
  $gcloud, $bucket, $src and $state are set. A machine ran the tool from its source tree until 2026-11-04, so a fix to
  it never reached a machine whose tree was older (a lost-command fix and a weeks-old T4 tree); now a fix reaches it
  at its next boot once its sender has the fix, whatever the tree. The choice is kept in state\queue-tool.txt for the
  desktop session (windows_gui_checks.ps1).
#>

function Resolve-QueueTool([string]$Gcloud, [string]$Bucket, [string]$Src, [string]$State) {
    # The published copy when its SHA-256 is the manifest's and it parses; else this tree's, said loudly.
    $tree = Join-Path $Src "scripts\angelo\windows_command_queue.ps1"
    $dir = Join-Path $State "tools"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $local, $manifest = (Join-Path $dir "windows_command_queue.ps1"), (Join-Path $dir "manifest.json")
    Remove-Item $local, $manifest -Force -ErrorAction SilentlyContinue
    $why, $have = "", ""
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $said = @(& $Gcloud storage cp --quiet "gs://$Bucket/tools/windows_command_queue.ps1" "gs://$Bucket/tools/manifest.json" `
                ($dir + [IO.Path]::DirectorySeparatorChar) 2>&1 | ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0 -or -not (Test-Path $local) -or -not (Test-Path $manifest)) {
        $why = "none published, or not fetched (exit $code): " + (($said -join " ").Trim() -replace "\s+", " ")
    } else {
        $want = $null
        try { $want = [string](Get-Content $manifest -Raw | ConvertFrom-Json)."windows_command_queue.ps1".sha256 } catch { }
        $have = (Get-FileHash $local -Algorithm SHA256).Hash.ToLower()
        $tokens = $null; $errors = $null
        if (-not $want) { $why = "the manifest names no SHA-256" }
        elseif ($have -ne $want.ToLower()) { $why = "its SHA-256 $have is not the manifest's $want" }
        else {
            [void][System.Management.Automation.Language.Parser]::ParseFile($local, [ref]$tokens, [ref]$errors)
            if ($errors.Count -gt 0) { $why = "it does not parse: " + $errors[0].Message }
        }
    }
    $chosen = if ($why) { $tree } else { $local }
    if ($why) { Write-Host "== queue tool: FALLING BACK to this tree's copy ($tree): $why" }
    else { Write-Host "== queue tool: the published copy (SHA-256 $have)" }
    Set-Content (Join-Path $State "queue-tool.txt") $chosen
    return $chosen
}

. (Resolve-QueueTool $gcloud $bucket $src $state)
