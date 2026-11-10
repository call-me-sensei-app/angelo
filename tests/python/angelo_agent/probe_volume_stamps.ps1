# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  How Windows stamps file times on NTFS, FAT32 and exFAT: the facts angelo_change_clock's volume steps rest on.

.DESCRIPTION
  Runs probe_volume_stamps.py (its docstring says what it measures) on the unattended Windows validation machine with
  CI's host Python, as an administrator (the held VM runs as LocalSystem), no Blender and no desktop session:
    1. refuses a tree that is not exactly the commit sent (windows_source_send.ps1; elsewhere nothing is checked);
    2. runs the probe from the source tree with PYTHONUTF8=1, writing -OutDir\volume_stamps.json, its
       volume_stamps.summary.txt and the console log volume_stamps.log, under a hard timeout of -MaxSeconds plus
       -GraceSeconds (the probe stops itself at -MaxSeconds; the timeout only catches a hang);
    3. always runs the probe's --cleanup last, which detaches and deletes any test disk a killed run left in
       -WorkDir;
    4. prints the summary.
  Writes nothing into the source tree (no bytecode either). The test disks are small expandable VHDX files in
  -WorkDir, mounted at folders there (no drive letter). No provider, account or credential is involved.
#>
param(
    [string]$SourceDir = "C:\angelo\src",
    [string]$OutDir = "C:\angelo\cmd-out-probe",
    [string]$WorkDir = "C:\angelo\probe-work",
    [int]$Writes = 200,
    [int]$MaxSeconds = 600,
    [int]$GraceSeconds = 180
)
# Native programs' stderr is output here, never a terminating error: the probe is judged by its exit code and JSON.
$ErrorActionPreference = "Continue"
. (Join-Path $SourceDir "scripts\angelo\windows_source_send.ps1")
$commit = Get-SentCommit $SourceDir   # "" on a tree no send made; refuses a tree that is not the commit sent.
New-Item -ItemType Directory -Force $OutDir, $WorkDir | Out-Null
Set-Location $SourceDir
$env:PYTHONUTF8 = "1"                 # Readable logs; the add-on itself never relies on it.
$env:PYTHONDONTWRITEBYTECODE = "1"    # No __pycache__ left in the sent tree.
$python = "C:\Python313\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $python) { Write-Host "FAIL  no Python: install CI's host Python 3.13 (windows_agent_validate.ps1)"; exit 2 }

$out = Join-Path $OutDir "volume_stamps.json"
$log = Join-Path $OutDir "volume_stamps.log"
$summary = Join-Path $OutDir "volume_stamps.summary.txt"
Remove-Item $out, $summary -ErrorAction SilentlyContinue   # A stale result can never read as this run's.
$probe = "`"tests\python\angelo_agent\probe_volume_stamps.py`""
$arguments = "$probe --out `"$out`" --writes $Writes --max-seconds $MaxSeconds --work-dir `"$WorkDir`""
# cmd.exe writes the program's own bytes (UTF-8: PYTHONUTF8) to the log, where PowerShell 5 would re-encode them; the
# command goes through a batch file, as PowerShell 5 passes embedded quotes to cmd.exe unescaped.
$batch = Join-Path $env:TEMP ("angelo-probe-{0}.cmd" -f [guid]::NewGuid().ToString("N"))
[System.IO.File]::WriteAllText($batch, "@echo off`r`ncd /d `"$SourceDir`"`r`n`"$python`" $arguments > `"$log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("probe: {0} writes per volume, cap {1} s (+{2} s hard timeout), commit {3}" -f $Writes, $MaxSeconds, $GraceSeconds, $(if ($commit) { $commit } else { "(not a sent tree)" }))
try {
    $process = Start-Process -FilePath "cmd.exe" -ArgumentList @("/d", "/c", "`"$batch`"") -WorkingDirectory $SourceDir -NoNewWindow -PassThru
    $null = $process.Handle   # PowerShell 5 reports no ExitCode for a started process unless its handle was taken.
    if ($process.WaitForExit(($MaxSeconds + $GraceSeconds) * 1000)) {
        $code = $process.ExitCode
    } else {
        # By PID, the whole tree (cmd.exe and the probe): never by image name.
        & taskkill.exe /PID $process.Id /T /F | Out-Host
        $code = 124
        Write-Host ("FAIL  the probe was still running {0} s after its cap; killed" -f $GraceSeconds)
    }
} finally {
    # Always: a killed or failed run's test disks are detached and deleted (bounded by diskpart's own timeouts).
    & $python "tests\python\angelo_agent\probe_volume_stamps.py" --cleanup --work-dir $WorkDir 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  a test disk could not be removed; see the lines above"; $code = 1 }
    Remove-Item $batch -ErrorAction SilentlyContinue
}

if (Test-Path $summary) {
    Get-Content $summary -Encoding UTF8 | Out-Host
} else {
    Write-Host "no summary; the log's tail:"
    Get-Content $log -Tail 40 -Encoding UTF8 -ErrorAction SilentlyContinue | Out-Host
}
Write-Host ("probe exit {0}; results: {1}" -f $code, $out)
exit $code
