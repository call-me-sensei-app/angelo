# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  The Windows bench's tools and candidate, installed once and checked every time (dot-sourced by
  windows_final_runs.ps1; doc/angelo/final_runs.rst, "On the Windows machine").

.DESCRIPTION
  Each function installs one thing under C:\angelo\bench from its own publisher, checks it, and returns what it is
  (path, version, SHA-256) for facts.json:
    Install-ClaudeCode   Claude Code from npm (@anthropic-ai/claude-code at the Mac comparator's version), whose
                         package carries the native claude.exe;
    Install-StockCodex   stock OpenAI Codex (unpatched: the MCP side's comparator) from the GitHub release of the
                         agent runtime's pinned version, checked against the SHA-256 GitHub publishes for the asset
                         and OpenAI's Authenticode signature;
    Install-StockBlender stock Blender from download.blender.org (a portable archive; nothing is installed);
    Install-McpRuntime   the pilot's MCP add-on and server (final_runs_mcp.py install);
    Install-OneshotSources the one-shot tests' source pictures the stage downloaded from the private bucket,
                         checked against the tests' SHA-256 pins (final_runs_sources.py check);
    Freeze-MsiCandidate  the candidate MSI installed, its install folder frozen (final_runs.py freeze), uninstalled.
  None of them reads, prints or copies a credential.
#>

function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

function Get-SignerName([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate) { return "" }
    return $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
}

function Get-Download([string]$Url, [string]$OutFile) {
    New-Item -ItemType Directory -Force (Split-Path $OutFile) | Out-Null
    if (-not (Test-Path $OutFile)) {
        $partial = "$OutFile.partial"
        Invoke-WebRequest $Url -OutFile $partial -UseBasicParsing
        Move-Item $partial $OutFile -Force
    }
    return $OutFile
}

function Install-ClaudeCode([string]$Bench, [string]$Version) {
    $ErrorActionPreference = "Continue"   # Native programs' stderr is output here; failures throw below.
    if (-not $Version) { throw "Claude Code: give the Mac comparator's version (claude --version there)" }
    $npmPrefix = Join-Path $Bench "npm-claude-$Version"
    $claude = Join-Path $Bench "cli\claude.exe"
    $current = if (Test-Path $claude) { "$(& $claude --version 2>$null)" } else { "" }
    if ($current -notmatch ("^" + [regex]::Escape($Version) + "\b")) {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw "npm is not installed (Node.js LTS)" }
        New-Item -ItemType Directory -Force $npmPrefix, (Split-Path $claude) | Out-Null
        & npm.cmd install -g --no-fund --no-audit --prefix $npmPrefix "@anthropic-ai/claude-code@$Version" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "npm could not install @anthropic-ai/claude-code@$Version" }
        # The package's own bin is the native claude.exe of its win32-x64 dependency; one copy outside npm's folder.
        $native = Get-ChildItem $npmPrefix -Recurse -Filter "claude.exe" -ErrorAction SilentlyContinue |
            Where-Object { "$(& $_.FullName --version 2>$null)" -match ("^" + [regex]::Escape($Version) + "\b") } | Select-Object -First 1
        if (-not $native) { throw "npm installed no claude.exe reporting $Version" }
        Copy-Item $native.FullName $claude -Force
    }
    $reported = "$(& $claude --version 2>$null)".Trim()
    if ($reported -notmatch ("^" + [regex]::Escape($Version) + "\b")) { throw "claude.exe reports '$reported', not $Version" }
    return [ordered]@{ path = $claude; version = $reported; sha256 = (Get-Sha256 $claude); source = "npm @anthropic-ai/claude-code@$Version" }
}

function Install-StockCodex([string]$Bench, [string]$Version) {
    $ErrorActionPreference = "Continue"   # Native programs' stderr is output here; failures throw below.
    if (-not $Version) { throw "stock Codex: give the version (the agent runtime's pin)" }
    $tag = "rust-v$Version"
    $asset = "codex-package-x86_64-pc-windows-msvc.tar.gz"
    $root = Join-Path $Bench "codex-stock-$Version"
    $receipt = Join-Path $root "PROVENANCE.json"
    if (-not (Test-Path $receipt)) {
        $release = Invoke-RestMethod "https://api.github.com/repos/openai/codex/releases/tags/$tag" -Headers @{ "User-Agent" = "angelo-bench" }
        $entry = @($release.assets | Where-Object { $_.name -eq $asset })[0]
        if (-not $entry -or $entry.digest -notmatch "^sha256:([0-9a-f]{64})$") { throw "$tag has no $asset with a published SHA-256" }
        $published = $Matches[1]
        $archive = Get-Download $entry.browser_download_url (Join-Path $Bench "download\$tag-$asset")
        $actual = Get-Sha256 $archive
        if ($actual -ne $published) { Remove-Item $archive -Force; throw "$asset is $actual, GitHub publishes $published" }
        Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $root | Out-Null
        & tar.exe -xzf $archive -C $root
        if ($LASTEXITCODE -ne 0) { throw "tar could not extract $asset" }
        [ordered]@{ release = $tag; asset = $asset; url = $entry.browser_download_url; sha256 = $actual } |
            ConvertTo-Json | Set-Content $receipt
    }
    $codex = Get-ChildItem $root -Recurse -Filter "codex.exe" | Select-Object -First 1
    if (-not $codex) { throw "$root holds no codex.exe" }
    if (-not (Test-Path (Join-Path $codex.DirectoryName "codex-code-mode-host.exe"))) {
        throw "codex-code-mode-host.exe is not beside $($codex.FullName): Codex finds it there"
    }
    $signer = Get-SignerName $codex.FullName
    if ($signer -ne "OpenAI OpCo, LLC") { throw "$($codex.FullName) is not validly signed by OpenAI (signer '$signer')" }
    $reported = "$(& $codex.FullName --version 2>$null)".Trim()
    if ($reported -notmatch ("\s" + [regex]::Escape($Version) + "$")) { throw "stock codex.exe reports '$reported', not $Version" }
    $ripgrep = Get-ChildItem $root -Recurse -Filter "rg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    $provenance = Get-Content $receipt -Raw | ConvertFrom-Json
    return [ordered]@{ path = $codex.FullName; version = $reported; sha256 = (Get-Sha256 $codex.FullName); signer = $signer
        package = $provenance.asset; package_sha256 = $provenance.sha256; release = $provenance.release
        files = @(Get-ChildItem $codex.DirectoryName -File | ForEach-Object { $_.Name })
        ripgrep = $(if ($ripgrep) { $ripgrep.FullName } else { "" }) }
}

function Install-StockBlender([string]$Bench, [string]$Url) {
    $ErrorActionPreference = "Continue"   # Native programs' stderr is output here; failures throw below.
    $name = [IO.Path]::GetFileNameWithoutExtension($Url)
    $zip = Get-Download $Url (Join-Path $Bench ("stock\" + (Split-Path $Url -Leaf)))
    $root = Join-Path $Bench "stock\$name"
    if (-not (Test-Path (Join-Path $root "blender.exe"))) { Expand-Archive $zip -DestinationPath (Split-Path $zip) -Force }
    $exe = Join-Path $root "blender.exe"
    if (-not (Test-Path $exe)) { throw "no blender.exe in $root" }
    $version = @(& $exe --factory-startup --version 2>$null | Where-Object { $_ -match "^Blender \d" })[0]
    return [ordered]@{ path = $exe; version = "$version".Trim(); url = $Url; zip_sha256 = (Get-Sha256 $zip); sha256 = (Get-Sha256 $exe)
        signer = (Get-SignerName $exe) }
}

function Install-McpRuntime([string]$Bench, [string]$Bundle, [string]$Python, [string]$Uv, [string]$SourceDir) {
    $ErrorActionPreference = "Continue"   # Native programs' stderr is output here; failures throw below.
    $digest = (Get-Sha256 $Bundle).Substring(0, 12)
    $mcp = Join-Path $Bench "mcp-$digest"
    # An install that names no interpreter_dir ran on uv's interpreter in the installing account's (SYSTEM's)
    # profile, which the bench user cannot read: the server never started in the bench session. Install it again,
    # self-contained (final_runs_mcp.uv_environment).
    $installed = Join-Path $mcp "INSTALLED.json"
    $selfContained = (Test-Path $installed) -and [bool]((Get-Content $installed -Raw | ConvertFrom-Json).interpreter_dir)
    if (-not $selfContained) {
        Remove-Item $mcp -Recurse -Force -ErrorAction SilentlyContinue
        & $Python (Join-Path $SourceDir "tests\python\angelo_agent\final_runs_mcp.py") install --bundle $Bundle --into $mcp --uv $Uv | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "final_runs_mcp.py install failed" }
    }
    return [ordered]@{ root = $mcp; bundle_sha256 = (Get-Sha256 $Bundle); server_dir = (Join-Path $mcp "mcp_server")
        addon_dir = (Join-Path $mcp "mcp_addon") }
}

function Install-OneshotSources([string]$Folder, [string]$Python, [string]$SourceDir) {
    $ErrorActionPreference = "Continue"   # Native programs' stderr is output here; failures throw below.
    $checked = @(& $Python (Join-Path $SourceDir "tests\python\angelo_agent\final_runs_sources.py") check --dir $Folder 2>&1 |
        ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) { throw "$Folder does not hold the one-shot tests' pinned source pictures: $($checked -join ' ')" }
    $result = ($checked -join "`n") | ConvertFrom-Json
    return [ordered]@{ folder = $Folder; files = $result.files; unexpected = @($result.unexpected) }
}

function Freeze-MsiCandidate([string]$Bench, [string]$Msi, [string]$Sha256, [string]$InstallDir, [string]$SourceDir) {
    $ErrorActionPreference = "Continue"   # Native programs' stderr is output here; failures throw below.
    $known = if ($Sha256) { Join-Path $Bench ("candidate-msi-" + $Sha256.ToLowerInvariant().Substring(0, 12)) } else { "" }
    if ($known -and (Test-Path (Join-Path $known "RECEIPT.json"))) {
        $actual = $Sha256.ToLowerInvariant()   # Frozen before: the MSI is not needed again.
    } else {
        $actual = Get-Sha256 $Msi
        if ($Sha256 -and $actual -ne $Sha256.ToLowerInvariant()) { throw "$Msi is $actual, not the candidate $Sha256" }
    }
    $candidate = Join-Path $Bench ("candidate-msi-" + $actual.Substring(0, 12))
    if (-not (Test-Path (Join-Path $candidate "RECEIPT.json"))) {
        $log = Join-Path $Bench "logs\candidate-install-$($actual.Substring(0, 12)).log"
        New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
        # ALLUSERS=1: for every user, in Program Files (the MSI's own default is a per-user install).
        $run = Start-Process msiexec.exe -ArgumentList "/i `"$Msi`" ALLUSERS=1 /qn /norestart /l*v `"$log`"" -Wait -PassThru
        if ($run.ExitCode -notin 0, 3010) { throw "msiexec /i $Msi exited with $($run.ExitCode) ($log)" }
        try {
            # Frozen with the installed app's own Python, as the harness will run it.
            $python = Join-Path $InstallDir "5.2\python\bin\python.exe"
            & $python (Join-Path $SourceDir "tests\python\angelo_agent\final_runs.py") freeze --source $InstallDir --into $candidate | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "final_runs.py freeze failed" }
        } finally {
            # The frozen copy is the candidate: the installed one (its shell extension, its file types) goes again.
            $run = Start-Process msiexec.exe -ArgumentList "/x `"$Msi`" /qn /norestart" -Wait -PassThru
            if ($run.ExitCode -notin 0, 1605, 3010) { Write-Warning "msiexec /x exited with $($run.ExitCode)" }
        }
        [ordered]@{ msi = (Split-Path $Msi -Leaf); msi_sha256 = $actual } | ConvertTo-Json | Set-Content (Join-Path $candidate "MSI.json")
    }
    $receipt = Get-Content (Join-Path $candidate "RECEIPT.json") -Raw | ConvertFrom-Json
    return [ordered]@{ folder = $candidate; msi_sha256 = $actual; executable = $receipt.executable
        executable_sha256 = $receipt.executable_sha256; version = @($receipt.version)[0..1] -join " | "
        harness_python = (Join-Path $receipt.app "5.2\python\bin\python.exe") }
}
