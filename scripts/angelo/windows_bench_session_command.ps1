# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  A request's session command (dot-sourced by windows_final_runs.ps1; functions only).

.DESCRIPTION
  A bench request may name a folder in this machine's bucket holding session.ps1 (metadata angelo-bench-session-input,
  gcp_windows_validate.sh ANGELO_BENCH_SESSION_INPUT). The bench's desktop session then runs that script in place of
  final_runs.py, on the gates' own path: the preparation, the kept sign-in's check, the throwaway bench user and its
  cleanup. It is for a harness that needs a signed-in person's desktop, such as update_carry_over_gate.py's state
  receipt on a gate machine (no sign-in, no user of its own, nothing else changed).
    Receive-BenchSessionCommand  the stage (SYSTEM), before the session: the folder into <bench>\session-<nonce>\in,
                                 then its optional prepare.ps1 -In <in> -Out <out> as SYSTEM (what the session's
                                 non-administrator user cannot do itself, such as unpacking an MSI)
    Invoke-BenchSessionCommand   the session: session.ps1 -In <in> -Out <out> -Facts <facts.json> -Connections <folder>,
                                 its exit code returned
    Publish-BenchSessionCommand  the stage, after the session's DONE: <out> to gs://<bucket>/bench/session-<nonce>/out
#>

function Get-BenchSessionInput { "$(Get-Meta 'angelo-bench-session-input' '')".Trim() }

function Receive-BenchSessionCommand([string]$Source, [string]$Bucket, [string]$Dir) {
    if ($Source -notlike "gs://$Bucket/*") { throw "angelo-bench-session-input must be a folder in gs://$Bucket/ (got $Source)" }
    if (Test-Path (Join-Path $Dir "in\session.ps1")) { return }   # A later boot of the same request.
    New-Item -ItemType Directory -Force (Join-Path $Dir "in"), (Join-Path $Dir "out") | Out-Null
    Invoke-Gcloud @("storage", "cp", "--quiet", "--recursive", ($Source.TrimEnd("/") + "/*"), (Join-Path $Dir "in"))
    if (-not (Test-Path (Join-Path $Dir "in\session.ps1"))) { throw "$Source holds no session.ps1" }
    Write-Host "== session command: $Source -> $Dir\in"
    $prepare = Join-Path $Dir "in\prepare.ps1"
    if (Test-Path $prepare) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $prepare -In (Join-Path $Dir "in") -Out (Join-Path $Dir "out")
        if ($LASTEXITCODE -ne 0) { throw "the session command's prepare.ps1 exited with $LASTEXITCODE" }
    }
}

function Invoke-BenchSessionCommand([string]$Dir, [string]$Facts, [string]$Connections) {
    # Its output to the host (the session's transcript), never into this function's result: a PowerShell function
    # returns everything it writes, and the session records exactly "<mode> exit code: <n>" (the first run's DONE
    # held the whole output and failed the request though the command exited 0).
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "in\session.ps1") -In (Join-Path $Dir "in") `
        -Out (Join-Path $Dir "out") -Facts $Facts -Connections $Connections | Out-Host
    return $LASTEXITCODE
}

function Publish-BenchSessionCommand([string]$Dir, [string]$Bucket, [string]$Nonce) {
    if (Test-Path (Join-Path $Dir "out")) {
        Invoke-Gcloud @("storage", "rsync", "--recursive", (Join-Path $Dir "out"), "gs://$Bucket/bench/session-$Nonce/out") -Optional
    }
}
