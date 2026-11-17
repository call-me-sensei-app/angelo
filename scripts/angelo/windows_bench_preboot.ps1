# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# The bench stage's pre-boot (windows_final_runs.ps1, doc/angelo/final_runs.rst "One boot for a signed-in machine"),
# dot-sourced by it: functions only. They read the stage's own variables ($python313, $finalRuns, $connections,
# $installDir, $bench, $state, $SourceDir) and functions (Invoke-Native, Get-Meta) when called.

# Pre-boot: what must hold before a candidate is worth waiting for. Each check refuses, saying what it found.
function Assert-PrebootReady([string]$StockUrl, [string[]]$Only = @()) {
    if (-not (Test-Path $python313)) { throw "pre-boot refused: no Python 3.13 at $python313 (not a prepared bench machine: run 'bench prep')" }
    # 1. The sign-in: the accounts the marker names, checked again as prep checks them (existence only).
    $marker = Join-Path $connections "SIGNED-IN"
    if (-not (Test-Path $marker)) { throw "pre-boot refused: not signed in (no $marker): run 'bench prep' and sign in over RDP" }
    $line = @(Get-Content $marker | Where-Object { $_ -match "^accounts:" })[0]
    $accounts = @(if ($line) { ($line -replace "^accounts:", "").Split(" ") | Where-Object { $_ -in "claude", "chatgpt" } })
    if ($accounts.Count -eq 0) { $accounts = @("claude", "chatgpt") }
    $check = @($finalRuns, "check-sign-in", "--connections-dir", $connections) + @($accounts | ForEach-Object { @("--account", $_) })
    if ((Invoke-Native $python313 $check) -ne 0) { throw "pre-boot refused: the sign-in ($($accounts -join ', ')) is incomplete: run 'bench prep' and sign in over RDP" }
    # 2. No stale install: a candidate is installed only while it is frozen, then removed (Freeze-MsiCandidate).
    # Bytecode the installed app's Python wrote while it was frozen stays after msiexec /x (it installed none of it):
    # that is no install. Any other file, or a registration, is.
    $left = @(Get-ChildItem $installDir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Extension -ne ".pyc" })
    $uninstall = @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*") |
        ForEach-Object { Get-ItemProperty $_ -ErrorAction SilentlyContinue } | Where-Object { $_.DisplayName -like "Angelo*" }
    if ($left.Count -or @($uninstall).Count) {
        throw "pre-boot refused: a stale Angelo install ($installDir holds $($left.Count) files, first $(@($left | Select-Object -First 1 | ForEach-Object { $_.FullName })); registered: $(@($uninstall | ForEach-Object { "$($_.DisplayName) $($_.DisplayVersion)" }) -join ', '))"
    }
    # 3. Stock Blender as the run expects: none when no selected step runs the MCP side; otherwise the URL's build,
    #    and a copy already unpacked from it reports that version.
    $needsArgs = @((Join-Path $SourceDir "tests\python\angelo_agent\final_runs_plan.py"), "needs", "--platform", "windows")
    foreach ($glob in $Only) { $needsArgs += @("--only", $glob) }
    $needs = (& $python313 @needsArgs | Out-String) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $null -eq $needs) { throw "pre-boot refused: final_runs_plan.py needs: no step matches '$($Only -join " ")'" }
    $stock = "not needed (no selected step runs the MCP side)"
    if ($needs.mcp_side) {
        if ($StockUrl -notmatch "blender-(\d+\.\d+\.\d+)-windows") { throw "pre-boot refused: the stock URL names no version: $StockUrl" }
        $want = $Matches[1]
        $exe = Join-Path $bench ("stock\" + [IO.Path]::GetFileNameWithoutExtension($StockUrl) + "\blender.exe")
        if (Test-Path $exe) {
            $ErrorActionPreference = "Continue"
            $found = "$(@(& $exe --factory-startup --version 2>$null | Where-Object { $_ -match "^Blender \d" })[0])".Trim()
            $ErrorActionPreference = "Stop"
            if ($found -notmatch "^Blender $([regex]::Escape($want))\b") { throw "pre-boot refused: stock Blender at $exe is '$found', not $want" }
            $stock = $found
        } else { $stock = "$want (unpacked by the preparation)" }
    }
    return [ordered]@{ accounts = $accounts; install = "none"; stock = $stock }
}

# Waits (pre-boot) until the candidate MSI is named in the metadata; the loop's own file keeps the heartbeat's
# progress moving, so the wait is never read as a hung stage. Returns the MSI's metadata value.
function Wait-PrebootCandidate([int]$Minutes) {
    $waiting = Join-Path $state "preboot-waiting"
    $until = (Get-Date).AddMinutes($Minutes)
    while ((Get-Date) -lt $until) {
        $named = Get-Meta "angelo-bench-candidate-msi" ""
        if ($named) { Remove-Item $waiting -ErrorAction SilentlyContinue; return $named }
        Set-Content $waiting "waiting for the candidate MSI until $($until.ToString('o')); $(Get-Date -Format o)"
        Start-Sleep -Seconds 20
    }
    throw "pre-boot: no candidate MSI named within $Minutes minutes (bench deliver)"
}
