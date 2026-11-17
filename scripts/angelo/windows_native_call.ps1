# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# The one way the Windows build machine's release path runs a program whose error output it redirects: the cloud
# bootstrap, the release stage, the release build and signing (test_native_windows_native_call.py lists the path).
# Scripts in the checkout dot-source this file. The two startup scripts that run before a checkout exists
# (windows_cloud_bootstrap.ps1, windows_sign_bootstrap.ps1) carry the function below byte for byte in a here-string
# they load (and hand to their background jobs), as the test checks.
#
# Those scripts run under Windows PowerShell 5.1, mostly with $ErrorActionPreference = "Stop". There every line a
# program writes to a redirected stderr (2>&1, 2>$null, 2>file) becomes an ErrorRecord, and Stop turns the first one
# into a terminating NativeCommandError: a Python warning fails the step, and a real refusal surfaces as that error
# instead of the program's own message (the identity stage's add-on prune had it, #207). Invoke-NativeCapture runs the
# program in its own Continue scope, with -InputText piped to it when given, and returns its exit code, its standard
# output and its error output as text, kept apart (a caller that parses JSON reads Out alone), and all of them in
# order (Lines, and Text joined). -Log replaces
# "*>&1 | Tee-Object <file>" for a long stage: each line goes to the host and to the file as it arrives (Tee-Object's
# UTF-16 with a byte-order mark, shared so the heartbeat can read the tail meanwhile). The caller judges the exit code;
# a program that cannot be found is exit code -1 with that said, never a stale code from an earlier one.

function Invoke-NativeCapture([string]$Exe, [string[]]$Arguments = @(), [string[]]$InputText = $null, [string]$Log = "") {
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) {
        $said = "$Exe was not found"
        return [pscustomobject]@{ Code = -1; Out = @(); Err = @($said); Lines = @($said); Text = $said }
    }
    $writer = $null
    if ($Log) {
        $stream = New-Object IO.FileStream($Log, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        $writer = New-Object IO.StreamWriter($stream, [Text.Encoding]::Unicode); $writer.AutoFlush = $true
    }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $each = { if ($writer) { $line = "$_"; $writer.WriteLine($line); Write-Host $line }; $_ }
        if ($null -ne $InputText) { $records = @($InputText | & $Exe @Arguments 2>&1 | ForEach-Object $each) }
        else { $records = @(& $Exe @Arguments 2>&1 | ForEach-Object $each) }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old; if ($writer) { $writer.Dispose() } }
    $out = @($records | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    $err = @($records | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    $lines = @($records | ForEach-Object { "$_" })   # Both, in the order the program wrote them.
    return [pscustomobject]@{ Code = $code; Out = $out; Err = $err; Lines = $lines; Text = ($lines -join "`r`n") }
}
