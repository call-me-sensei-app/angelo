# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Authenticode-signs Angelo's Windows binaries and MSI with a key held in Google Cloud KMS, and verifies them.

.DESCRIPTION
  The private key never leaves Cloud KMS (an HSM key: the Cloud KMS CNG provider accepts only those). signtool
  reaches it through that provider ("Google Cloud KMS Provider", installed from Google's kms-integrations
  release MSI), which authenticates with Application Default Credentials: the signed-in gcloud user
  (gcloud auth application-default login) or the VM's service account. This script takes only the public
  certificate file and the key version's resource name; it never reads, prints or stores a key, password or
  token, and refuses a certificate file that carries a private key (.pfx).

  Modes:
    Preflight  signtool, the CNG provider, the certificate (code signing, in date, no private key) and the
               key name are checked; nothing is signed. build_release_windows.ps1 runs it before compiling.
    Sign       every .exe, .dll, .pyd and .msi under -Path that is not already validly signed is signed with
               SHA-256 and an RFC 3161 timestamp (/tr, /td sha256), then verified. A file a vendor already
               signed (Microsoft's runtime, GPU vendors' libraries) keeps its own signature; a file whose
               signature no longer matches it (HashMismatch) is refused rather than signed over, and any
               other invalid signature (expired without a timestamp, untrusted) is replaced by ours.
    Verify     every such file under -Path must pass signtool verify /pa, carry a timestamp, and be signed
               by this certificate unless a vendor signed it.
    VerifyMsi  the MSI must be signed by this certificate, and every binary inside it (extracted with an
               administrative install, msiexec /a, into a temporary folder) must be validly signed; its
               agent runtime folder must hold exactly the pinned files.
  The agent runtime (scripts\addons_core\angelo_agent\agent_runtime.json, platforms.windows) is held to its pin in
  every mode: OpenAI's unchanged release executables (upstream_signed) keep OpenAI's own signature (upstream_signer)
  and are never signed, re-signed or stripped here (any other state of theirs stops the script), and the runtime's
  other files (codex.exe, Angelo's build of the fork) must carry this certificate's signature, never a vendor's.
  -DryRun lists what Sign would do and the exact signtool command lines, without signtool or any signature
  check (every file is treated as unsigned, except the agent runtime's upstream_signed files, which are never
  signed), so the plan can be reviewed anywhere PowerShell runs.

  Runbook: doc/angelo/releasing.rst ("Windows").

.EXAMPLE
  .\scripts\angelo\sign_windows.ps1 -Mode Preflight -CertificateFile C:\angelo\codesign.crt `
    -KmsKey projects/<project>/locations/<location>/keyRings/<keyring>/cryptoKeys/<key>/cryptoKeyVersions/<n>
  (or set ANGELO_SIGN_KMS_KEY to that resource name; there is no default)
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("Preflight", "Sign", "Verify", "VerifyMsi")][string]$Mode,
    [string[]]$Path = @(),
    # The code-signing certificate the CA issued for the KMS key (.cer/.crt, DER or PEM). Never a .pfx.
    [string]$CertificateFile = $env:ANGELO_SIGN_CERTIFICATE,
    # projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N
    [string]$KmsKey = $env:ANGELO_SIGN_KMS_KEY,
    # The CA's RFC 3161 time-stamping service.
    [string]$TimestampUrl = $(if ($env:ANGELO_SIGN_TIMESTAMP_URL) { $env:ANGELO_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }),
    # Optional intermediate certificate(s) to embed (/ac), when the CA asks for it.
    [string]$ChainFile = $env:ANGELO_SIGN_CHAIN_FILE,
    [string]$SignTool = $env:ANGELO_SIGNTOOL,
    [string]$Description = "Angelo",
    [string]$DescriptionUrl = "https://angelo.studio/",
    [int]$BatchSize = 24,
    [string]$Report = "",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Provider = "Google Cloud KMS Provider"
$CodeSigningEku = "1.3.6.1.5.5.7.3.3"
$KeyPattern = "^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+/cryptoKeyVersions/\d+$"
$Extensions = @(".exe", ".dll", ".pyd", ".msi")
# The agent runtime's pin. OpenAI's unchanged release executables keep OpenAI's signature; the rest of the runtime
# (codex.exe, built from Angelo's fork) is Angelo's and carries this certificate's.
$RuntimePinFile = Join-Path $PSScriptRoot "..\addons_core\angelo_agent\agent_runtime.json"
if (-not (Test-Path $RuntimePinFile)) { throw "the agent runtime's pin is missing ($RuntimePinFile): run this script from the source tree" }
$RuntimePin = (Get-Content $RuntimePinFile -Raw -Encoding UTF8 | ConvertFrom-Json).platforms.windows
$UpstreamSigned = @($RuntimePin.upstream_signed)
$UpstreamSigner = [string]$RuntimePin.upstream_signer
$RuntimeOwn = @(@($RuntimePin.required) + @($RuntimePin.optional) | Where-Object { $_ -notin $UpstreamSigned })
if (-not $UpstreamSigner -or -not $RuntimeOwn) { throw "$RuntimePinFile names no upstream_signer or no file of Angelo's own" }

function Find-SignTool {
    if ($SignTool) {
        if (-not (Test-Path $SignTool)) { throw "signtool not found at $SignTool" }
        return $SignTool
    }
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    $found = Get-ChildItem $kits -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -eq "x64" } | Sort-Object { [version]($_.Directory.Parent.Name -replace "[^\d.]", "") } |
        Select-Object -Last 1
    if (-not $found) { throw "signtool.exe not found: install the Windows SDK's Signing Tools (or set ANGELO_SIGNTOOL)" }
    return $found.FullName
}

function Get-Certificate {
    if (-not $CertificateFile) { throw "give -CertificateFile (or ANGELO_SIGN_CERTIFICATE): the CA-issued code-signing certificate" }
    if (-not (Test-Path $CertificateFile)) { throw "certificate file not found: $CertificateFile" }
    if ([IO.Path]::GetExtension($CertificateFile) -in ".pfx", ".p12") {
        throw "$CertificateFile is a .pfx/.p12 key container; give the public certificate (.cer/.crt): the key stays in Cloud KMS"
    }
    $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertificateFile)
    if ($certificate.HasPrivateKey) { throw "$CertificateFile carries a private key; give the public certificate only" }
    $usages = @($certificate.Extensions | Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } |
        ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
    if ($CodeSigningEku -notin $usages) { throw "$($certificate.Subject) is not a code-signing certificate (no EKU $CodeSigningEku)" }
    $now = Get-Date
    if ($certificate.NotBefore -gt $now -or $certificate.NotAfter -lt $now) {
        throw "$($certificate.Subject) is valid from $($certificate.NotBefore) to $($certificate.NotAfter), not now"
    }
    if ($certificate.NotAfter -lt $now.AddDays(30)) { Write-Warning "$($certificate.Subject) expires on $($certificate.NotAfter)" }
    return $certificate
}

function Assert-Inputs([bool]$NeedsProvider) {
    if (-not $KmsKey -or $KmsKey -notmatch $KeyPattern) {
        throw "give -KmsKey (or ANGELO_SIGN_KMS_KEY) as projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N"
    }
    if ($TimestampUrl -notmatch "^https?://") { throw "-TimestampUrl must be the CA's RFC 3161 http(s) address" }
    if ($ChainFile -and -not (Test-Path $ChainFile)) { throw "chain file not found: $ChainFile" }
    if ($NeedsProvider) {
        $providers = (& certutil.exe -csplist) -join "`n"
        if ($providers -notmatch [regex]::Escape($Provider)) {
            throw "the '$Provider' CNG provider is not installed: install the Cloud KMS CNG provider MSI (github.com/GoogleCloudPlatform/kms-integrations releases)"
        }
    }
}

function Get-Targets {
    $files = foreach ($item in $Path) {
        if (Test-Path $item -PathType Container) {
            Get-ChildItem $item -Recurse -File | Where-Object { $_.Extension.ToLowerInvariant() -in $Extensions }
        } elseif (Test-Path $item -PathType Leaf) {
            Get-Item $item
        } else {
            throw "$item does not exist"
        }
    }
    return @($files | Sort-Object FullName -Unique)
}

function Get-SignArguments([string[]]$Files) {
    $arguments = @("sign", "/v", "/fd", "sha256", "/tr", $TimestampUrl, "/td", "sha256", "/f", $CertificateFile,
        "/csp", $Provider, "/kc", $KmsKey, "/d", $Description, "/du", $DescriptionUrl)
    if ($ChainFile) { $arguments += @("/ac", $ChainFile) }
    return $arguments + $Files
}

function Invoke-SignTool([string]$Tool, [string[]]$Arguments) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $output = & $Tool @Arguments 2>&1 | Out-String; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
    return @{ Code = $code; Output = $output }
}

# The signer's common name ("OpenAI OpCo, LLC"), or "" for an unsigned file.
function Get-SignerName($Signature) {
    if (-not $Signature.SignerCertificate) { return "" }
    return $Signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
}

# A file needs our signature unless a vendor's valid signature is already on it. A signature that no longer
# matches its file means the file changed after it was signed: that is never signed over. Any other invalid
# signature (an expired certificate without a timestamp, an untrusted root) is replaced by ours. The agent
# runtime's upstream_signed files are kept only with the upstream signer's valid signature and never signed;
# its own files (codex.exe) are never left with another signer's.
function Get-Decision($File, $Certificate) {
    $signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
    if ($File.Name -in $UpstreamSigned) {
        if ($signature.Status -eq "Valid" -and (Get-SignerName $signature) -eq $UpstreamSigner) {
            return "upstream:" + $signature.SignerCertificate.Subject
        }
        throw ("$($File.FullName) is $UpstreamSigner's unchanged release file and ships with its own signature, but its " +
            "signature is $($signature.Status) (signer '$(Get-SignerName $signature)'): refusing to sign it; take it from Codex's release again")
    }
    switch ($signature.Status) {
        "NotSigned" { return "sign" }
        "Valid" {
            if ($signature.SignerCertificate.Thumbprint -eq $Certificate.Thumbprint) { return "ours" }
            if ($File.Name -in $RuntimeOwn) {
                throw "$($File.FullName) is Angelo's build of the agent runtime and must carry this certificate's signature, not $($signature.SignerCertificate.Subject)'s: use the unsigned build"
            }
            return "vendor:" + $signature.SignerCertificate.Subject
        }
        "HashMismatch" { throw "$($File.FullName) was changed after it was signed (HashMismatch): refusing to sign over it" }
        "NotSupportedFileFormat" { throw "$($File.FullName) cannot carry an Authenticode signature" }
        default { return "resign:" + $signature.Status }
    }
}

function Assert-Signed($File, $Certificate, [string]$Tool, [bool]$AllowVendor) {
    $verified = Invoke-SignTool $Tool @("verify", "/pa", "/q", $File.FullName)
    if ($verified.Code -ne 0) { throw "signtool verify /pa failed for $($File.FullName): $($verified.Output)" }
    $signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
    if ($signature.Status -ne "Valid") { throw "$($File.FullName): $($signature.Status) $($signature.StatusMessage)" }
    if (-not $signature.TimeStamperCertificate) { throw "$($File.FullName) has no timestamp" }
    $ours = $signature.SignerCertificate.Thumbprint -eq $Certificate.Thumbprint
    if ($File.Name -in $UpstreamSigned) {
        if ($ours -or (Get-SignerName $signature) -ne $UpstreamSigner) {
            throw "$($File.FullName) must carry $UpstreamSigner's own signature, not $($signature.SignerCertificate.Subject)'s"
        }
        return $false
    }
    if ($File.Name -in $RuntimeOwn -and -not $ours) {
        throw "$($File.FullName) is Angelo's build of the agent runtime and must be signed by $($Certificate.Subject), not $($signature.SignerCertificate.Subject)"
    }
    if (-not $ours -and -not $AllowVendor) { throw "$($File.FullName) is signed by $($signature.SignerCertificate.Subject), not $($Certificate.Subject)" }
    return $ours
}

# An unpacked MSI's agent runtime: exactly the pinned files in the pin's folder (their signatures are checked with
# every other binary). A signed MSI is a release, and a release bundles the runtime.
function Assert-RuntimeFolder($Files) {
    $runtime = @($Files | Where-Object { $_.Name -eq $RuntimePin.required[0] -and $_.Directory.Name -eq $RuntimePin.folder })
    if ($runtime.Count -ne 1) { throw "the MSI holds $($runtime.Count) agent runtimes ($($RuntimePin.folder)\$($RuntimePin.required[0])); a release holds one" }
    $held = @(Get-ChildItem $runtime[0].Directory.FullName -File | ForEach-Object { $_.Name })
    $missing = @($RuntimePin.required | Where-Object { $_ -notin $held })
    $extra = @($held | Where-Object { $_ -notin (@($RuntimePin.required) + @($RuntimePin.optional)) })
    if ($missing -or $extra) { throw "the MSI's agent runtime lacks [$($missing -join ', ')] or holds unpinned [$($extra -join ', ')]" }
    return $held
}

$result = [ordered]@{ mode = $Mode; dry_run = [bool]$DryRun; kms_key = $KmsKey; timestamp_url = $TimestampUrl;
    signed = @(); vendor_signed = @(); upstream_kept = @(); already_ours = 0; verified = 0 }

if ($DryRun) {
    if ($Mode -ne "Sign") { throw "-DryRun applies to -Mode Sign" }
    Assert-Inputs $false
    $all = @(Get-Targets)
    $targets = @($all | Where-Object { $_.Name -notin $UpstreamSigned } | ForEach-Object { $_.FullName })
    $result.upstream_kept = @($all | Where-Object { $_.Name -in $UpstreamSigned } |
        ForEach-Object { "$($_.Name) (kept: $UpstreamSigner's signature, checked when signing)" })
    $batches = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $targets.Count; $i += $BatchSize) {
        $batches.Add([string[]](Get-SignArguments $targets[$i..([math]::Min($i + $BatchSize, $targets.Count) - 1)]))
    }
    foreach ($batch in $batches) { Write-Host ("signtool " + ($batch -join " ")) }
    $result.signed = $targets
    $result.batches = $batches.Count
} else {
    if ($env:OS -ne "Windows_NT") { throw "Authenticode signing runs on Windows (use -DryRun elsewhere)" }
    $certificate = Get-Certificate
    $result.certificate = [ordered]@{ subject = $certificate.Subject; issuer = $certificate.Issuer; thumbprint = $certificate.Thumbprint;
        not_after = $certificate.NotAfter.ToString("o") }
    $tool = Find-SignTool
    $result.signtool = $tool
    Assert-Inputs ($Mode -in "Preflight", "Sign")
    if ($Mode -eq "Sign") {
        $pending = New-Object System.Collections.Generic.List[object]
        $result.resigned = @()
        foreach ($file in @(Get-Targets)) {
            $decision = Get-Decision $file $certificate
            if ($decision -eq "sign") { $pending.Add($file) }
            elseif ($decision -eq "ours") { $result.already_ours++ }
            elseif ($decision.StartsWith("resign:")) { $pending.Add($file); $result.resigned += "$($file.Name) ($($decision.Substring(7)))" }
            elseif ($decision.StartsWith("upstream:")) { $result.upstream_kept += "$($file.Name) ($($decision.Substring(9)))" }
            else { $result.vendor_signed += "$($file.Name) ($($decision.Substring(7)))" }
        }
        $toSign = $pending.ToArray()
        for ($i = 0; $i -lt $toSign.Count; $i += $BatchSize) {
            $batch = @($toSign[$i..([math]::Min($i + $BatchSize, $toSign.Count) - 1)] | ForEach-Object { $_.FullName })
            for ($attempt = 1; ; $attempt++) {   # A busy time-stamping service is the usual failure.
                $signed = Invoke-SignTool $tool (Get-SignArguments $batch)
                if ($signed.Code -eq 0) { break }
                if ($attempt -ge 4) { throw "signtool sign failed after $attempt attempts: $($signed.Output)" }
                Write-Warning "signtool sign failed (attempt $attempt), retrying: $(($signed.Output -split "`n" | Select-Object -Last 3) -join ' ')"
                Start-Sleep -Seconds (15 * $attempt)
            }
            $result.signed += $batch
        }
        foreach ($file in $toSign) { [void](Assert-Signed $file $certificate $tool $false); $result.verified++ }
    } elseif ($Mode -eq "Verify") {
        foreach ($file in @(Get-Targets)) {
            if (Assert-Signed $file $certificate $tool $true) { $result.verified++ } else { $result.vendor_signed += $file.Name }
        }
    } elseif ($Mode -eq "VerifyMsi") {
        $msi = @(Get-Targets | Where-Object { $_.Extension.ToLowerInvariant() -eq ".msi" })
        if ($msi.Count -ne 1) { throw "VerifyMsi needs exactly one .msi in -Path" }
        [void](Assert-Signed $msi[0] $certificate $tool $false)
        $folder = Join-Path ([IO.Path]::GetTempPath()) ("angelo-msi-" + [guid]::NewGuid().ToString("N"))
        $log = "$folder.log"
        try {
            $process = Start-Process msiexec.exe -ArgumentList @("/a", "`"$($msi[0].FullName)`"", "/qn", "TARGETDIR=`"$folder`"", "/l*v", "`"$log`"") -Wait -PassThru
            if ($process.ExitCode -ne 0) { throw "msiexec /a failed with $($process.ExitCode) (log: $log)" }
            $inner = @(Get-ChildItem $folder -Recurse -File | Where-Object { $_.Extension.ToLowerInvariant() -in ".exe", ".dll", ".pyd" })
            if (-not $inner) { throw "the MSI holds no binaries" }
            foreach ($file in $inner) {
                if (Assert-Signed $file $certificate $tool $true) { $result.verified++ } else { $result.vendor_signed += $file.Name }
            }
            $result.agent_runtime = Assert-RuntimeFolder $inner
            $result.msi = [ordered]@{ name = $msi[0].Name; binaries = $inner.Count }
        } finally {
            Remove-Item $folder -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

$json = $result | ConvertTo-Json -Depth 4
if ($Report) { [IO.File]::WriteAllText($Report, $json, (New-Object System.Text.UTF8Encoding($false))) }
Write-Host ("{0}: signed {1}, verified {2}, already ours {3}, vendor-signed kept {4}, agent runtime's upstream signatures kept {5}" -f
    $Mode, @($result.signed).Count, $result.verified, $result.already_ours, @($result.vendor_signed).Count, @($result.upstream_kept).Count)
