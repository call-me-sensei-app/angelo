# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Checks the agent runtime an installed Angelo, or its MSI, carries, and runs it from there: the Windows counterpart
  of the macOS launch check's agent-runtime step (sign_notarize_macos.py --launch-check).

.DESCRIPTION
  -Msi unpacks the MSI with an administrative install (msiexec /a) into a temporary folder and checks the runtime
  there; -InstallDir checks an installed copy (for example C:\Program Files\Angelo). Either way, against the pin
  (scripts\addons_core\angelo_agent\agent_runtime.json, platforms.windows):
    1. <install>\agent-runtime holds exactly the pinned files (the required ones, and only optional ones beside them),
       each byte for byte the file in -SourceRuntimeDir when that is given; -Release: the release marker is there too;
    2. OpenAI's unchanged release executables (upstream_signed) carry OpenAI's valid signature (upstream_signer), and
       codex.exe, Angelo's build, carries no OpenAI signature (it is unsigned until a release signs it with Angelo's
       certificate; -ExpectSigned: it must be validly signed);
    3. codex.exe --version reports the pinned version, run from that folder;
    4. codex.exe app-server generate-json-schema offers each pinned feature on thread/start, thread/resume and
       thread/fork;
    5. codex.exe app-server answers initialize over stdio with a user agent naming the pinned version, exactly what
       the app's agent_runtime.verify requires of every runtime process.
  Offline: no provider, account or network request; CODEX_HOME is a temporary folder, removed afterwards. Writes a
  JSON report (-Report) and exits 1 on the first problem. windows_release_validate.ps1 runs it on every release MSI.

.EXAMPLE
  .\scripts\angelo\windows_agent_runtime_check.ps1 -Msi C:\angelo\results\release\Angelo-1.0.0-windows-x64.msi `
    -SourceRuntimeDir C:\angelo\agent-runtime -Release -Report runtime.json
#>
param(
    [string]$Msi = "",
    [string]$InstallDir = "",
    [string]$SourceRuntimeDir = "",
    [switch]$Release,
    [switch]$ExpectSigned,
    [string]$Report = "",
    [int]$TimeoutSeconds = 60
)
$ErrorActionPreference = "Stop"
if ([bool]$Msi -eq [bool]$InstallDir) { throw "give either -Msi or -InstallDir" }
$pin = Get-Content (Join-Path $PSScriptRoot "..\addons_core\angelo_agent\agent_runtime.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$files = $pin.platforms.windows
$result = [ordered]@{ kind = "angelo-windows-agent-runtime-check"; pinned = $pin.version; source = $(if ($Msi) { $Msi } else { $InstallDir })
    folder = $null; files = [ordered]@{}; signatures = [ordered]@{}; version = $null; schema = @(); user_agent = $null; passed = $false }

function Get-SignerName($Signature) {
    if (-not $Signature.SignerCertificate) { return "" }
    return $Signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
}

# A native program's output, never its stderr as a PowerShell error.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [hashtable]$Environment) {
    $info = New-Object System.Diagnostics.ProcessStartInfo $Exe
    $info.Arguments = ($Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join " "
    $info.UseShellExecute = $false; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true; $info.CreateNoWindow = $true
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = $Environment[$key] }
    $process = [System.Diagnostics.Process]::Start($info)
    $process.StandardInput.Close()
    $out = $process.StandardOutput.ReadToEndAsync(); $err = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { $process.Kill(); throw "$Exe $($info.Arguments) did not finish within $TimeoutSeconds s" }
    $process.WaitForExit()
    return @{ Code = $process.ExitCode; Out = $out.Result; Err = $err.Result }
}

$unpacked = $null
$work = Join-Path ([IO.Path]::GetTempPath()) ("angelo-runtime-check-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $work | Out-Null
$exitCode = 1
try {
    if ($Msi) {
        $unpacked = Join-Path $work "msi"
        $log = Join-Path $work "msiexec.log"
        $process = Start-Process msiexec.exe -ArgumentList @("/a", "`"$((Resolve-Path $Msi).Path)`"", "/qn", "TARGETDIR=`"$unpacked`"", "/l*v", "`"$log`"") -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "msiexec /a failed with $($process.ExitCode) ($log)" }
        $found = @(Get-ChildItem $unpacked -Recurse -File -Filter $files.required[0] | Where-Object { $_.Directory.Name -eq $files.folder })
        if ($found.Count -ne 1) { throw "the MSI holds $($found.Count) agent runtimes ($($files.folder)\$($files.required[0])); a release holds one" }
        $folder = $found[0].Directory.FullName
    } else {
        $folder = Join-Path $InstallDir $files.folder
    }
    $result.folder = $folder
    if (-not (Test-Path $folder -PathType Container)) { throw "$folder is missing: this install carries no agent runtime" }

    # 1. Exactly the pinned files, the checked bytes, and a release's marker.
    $held = @(Get-ChildItem $folder -File | ForEach-Object { $_.Name })
    $missing = @($files.required | Where-Object { $_ -notin $held })
    $extra = @($held | Where-Object { $_ -notin (@($files.required) + @($files.optional)) })
    if ($missing -or $extra) { throw "$folder lacks [$($missing -join ', ')] or holds unpinned [$($extra -join ', ')]" }
    foreach ($name in $held) {
        $hash = (Get-FileHash (Join-Path $folder $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        $result.files[$name] = $hash
        if ($SourceRuntimeDir) {
            $source = Join-Path $SourceRuntimeDir $name
            $want = if (Test-Path $source) { (Get-FileHash $source -Algorithm SHA256).Hash.ToLowerInvariant() } else { "absent" }
            if ($hash -ne $want) { throw "$name in $folder is not the source's ($hash, source $want)" }
        }
    }
    if ($SourceRuntimeDir) {
        $notShipped = @(@($files.optional) | Where-Object { (Test-Path (Join-Path $SourceRuntimeDir $_)) -and $_ -notin $held })
        if ($notShipped) { throw "$folder lacks $($notShipped -join ', '), which the source runtime has" }
    }
    $marker = Join-Path (Split-Path $folder -Parent) $files.release_marker
    $result.release_marker = Test-Path $marker
    if ($Release -and -not $result.release_marker) { throw "$marker is missing: a release build makes the app run only its own runtime" }

    # 2. OpenAI's signatures kept; codex.exe never signed by OpenAI.
    foreach ($name in $held) {
        $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $folder $name)
        $signer = Get-SignerName $signature
        $result.signatures[$name] = "$($signature.Status) $signer".Trim()
        if ($name -in @($files.upstream_signed)) {
            if ($signature.Status -ne "Valid" -or $signer -ne $files.upstream_signer) {
                throw "$name must carry $($files.upstream_signer)'s valid signature, but it is $($signature.Status) (signer '$signer')"
            }
        } elseif ($signer -eq $files.upstream_signer) {
            throw "$name is Angelo's build and cannot carry $($files.upstream_signer)'s signature"
        } elseif ($ExpectSigned -and $signature.Status -ne "Valid") {
            throw "$name is $($signature.Status); a signed release carries Angelo's valid signature on it"
        }
    }

    # 3-5. The runtime itself, from where it is installed.
    $codex = Join-Path $folder $files.required[0]
    $codexHome = Join-Path $work "codex-home"
    New-Item -ItemType Directory -Force $codexHome | Out-Null
    $environment = @{ CODEX_HOME = $codexHome }
    $version = Invoke-Native $codex @("--version") $environment
    $result.version = "$($version.Out)".Trim()
    if ($version.Code -ne 0 -or $result.version -notmatch ("\s" + [regex]::Escape($pin.version) + "$")) {
        throw "codex.exe --version exited $($version.Code) saying '$($result.version)'; this source pins Codex $($pin.version)"
    }
    $schema = Join-Path $work "schema"
    $made = Invoke-Native $codex @("app-server", "generate-json-schema", "--experimental", "--out", $schema) $environment
    if ($made.Code -ne 0) { throw "codex.exe could not write its app-server schema: $($made.Err)" }
    foreach ($request in "ThreadStartParams", "ThreadResumeParams", "ThreadForkParams") {
        $file = Join-Path $schema "v2\$request.json"
        $properties = if (Test-Path $file) { (Get-Content $file -Raw | ConvertFrom-Json).properties } else { $null }
        foreach ($feature in $pin.features) {
            if (-not $properties -or -not ($properties.PSObject.Properties.Name -contains $feature)) {
                throw "codex.exe's $request has no ${feature}: not the patched build"
            }
            $result.schema += "$request.$feature"
        }
    }
    $info = New-Object System.Diagnostics.ProcessStartInfo $codex, "app-server --listen stdio://"
    $info.UseShellExecute = $false; $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true; $info.CreateNoWindow = $true; $info.WorkingDirectory = $codexHome
    $info.EnvironmentVariables["CODEX_HOME"] = $codexHome
    $server = [System.Diagnostics.Process]::Start($info)
    $errors = $server.StandardError.ReadToEndAsync()
    try {
        $request = @{ id = 1; method = "initialize"; params = @{ clientInfo = @{ name = "angelo_runtime_check"; title = "Angelo runtime check"; version = "0.0.0" }
            capabilities = @{ experimentalApi = $true } } } | ConvertTo-Json -Compress -Depth 6
        $server.StandardInput.WriteLine($request); $server.StandardInput.Flush()
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        $answer = $null
        while (-not $answer -and (Get-Date) -lt $deadline) {
            $line = $server.StandardOutput.ReadLineAsync()
            if (-not $line.Wait([int][math]::Max(1, ($deadline - (Get-Date)).TotalMilliseconds))) { break }
            if ($null -eq $line.Result) { break }
            $message = $null
            try { $message = $line.Result | ConvertFrom-Json } catch { }
            if ($message -and $message.id -eq 1) { $answer = $message }
        }
        if (-not $answer) { throw "codex.exe app-server did not answer initialize within $TimeoutSeconds s: $(if ($errors.IsCompleted) { $errors.Result })" }
        if ($answer.error) { throw "codex.exe app-server refused initialize: $($answer.error | ConvertTo-Json -Compress)" }
        $result.user_agent = [string]$answer.result.userAgent
        # The app's agent_runtime.reported_version: the version after the first "/".
        $reported = if ($result.user_agent -match "/(\d+\.\d+\.\d+[^\s(;]*)") { $Matches[1] } else { $null }
        if ($reported -ne $pin.version) { throw "initialize reports '$($result.user_agent)'; the app requires Codex $($pin.version)" }
        if (-not (Test-Path (Join-Path $folder $files.required[1]))) { throw "$($files.required[1]) is not beside codex.exe" }
    } finally {
        $server.StandardInput.Close()
        if (-not $server.WaitForExit(15000)) { $server.Kill() }
    }
    $result.passed = $true
    $exitCode = 0
    Write-Host ("agent runtime: {0} files in {1}, {2}, initialize: {3}" -f $held.Count, $folder, $result.version, $result.user_agent)
} catch {
    $result.problem = $_.Exception.Message
    Write-Host "agent runtime check failed: $($result.problem)"
} finally {
    if ($Report) { [IO.File]::WriteAllText($Report, ($result | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false))) }
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
exit $exitCode
