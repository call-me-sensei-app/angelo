# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Authenticode-signs Angelo's Windows binaries and MSI, with a key held in Google Cloud KMS or by Azure Artifact
  Signing, and verifies them.

.DESCRIPTION
  -Backend KmsKey (the default): the private key never leaves Cloud KMS (an HSM key: the Cloud KMS CNG provider
  accepts only those). signtool reaches it through that provider ("Google Cloud KMS Provider", installed from
  Google's kms-integrations release MSI), which authenticates with Application Default Credentials: the signed-in
  gcloud user (gcloud auth application-default login) or the VM's service account. This script takes only the
  public certificate file and the key version's resource name, and refuses a certificate file that carries a
  private key (.pfx).

  -Backend ArtifactSigning: Azure Artifact Signing holds the key and issues a certificate valid for three days at
  each signing, so a signature is ours when it is valid and its certificate names -Publisher (the validated legal
  name of the certificate profile), never by one certificate's thumbprint. signtool reaches the service through
  Azure.CodeSigning.Dlib.dll (Artifact Signing Client Tools: winget install -e --id
  Microsoft.Azure.ArtifactSigningClientTools; signtool 10.0.22621.755 or newer, .NET 8). The account's endpoint,
  name and certificate profile are parameters; this script writes the dlib's metadata file from them (names only,
  with the interactive sign-ins excluded, so an unattended build never waits for a browser) and removes it at the
  end. Credentials come from DefaultAzureCredential in the environment (a federated workload identity or a service
  principal, never a person's login), never from a file this script writes. Microsoft's time-stamping service is the
  default there.

  -Backend Esigner: SSL.com's eSigner cloud holds the key of an OV code-signing certificate, and SSL.com's
  CodeSignTool signs with it (-EsignerHome: its unpacked folder, run as its bundled java.exe and jar, never through
  its .bat, where cmd.exe would read a password's & or % as its own). A signature is ours when it is valid and made
  by -CertificateFile, the public certificate (one thumbprint for its year). The signer runs on its own Google
  Cloud VM as exactly -GceServiceAccount, and the account's password and TOTP secret come from Secret Manager in
  -EsignerSecretProject through that VM's own account, at signing time: they live only in this process's memory,
  are removed from everything CodeSignTool prints, and are never written, printed or reported. CodeSignTool takes
  them as arguments, so Preflight refuses a machine that records command lines (process-creation auditing with
  command lines, PowerShell module logging). Each file goes to CodeSignTool in a batch folder of copies (up to 100
  per OTP); a returned copy replaces its original only when its Authenticode digest (windows_pe_digest.ps1) equals
  the original's: only a signature was added. eSigner counts every file signed against the account's quota, which
  the report states. -EsignerJarSha256 pins CodeSignTool's jar, and SSL.com's time-stamping service (CodeSignTool's
  TSA_URL, which must equal -TimestampUrl) timestamps every signature.

  -Backend Presigned: nothing is signed here. Sign takes each file's signed copy from -SignedFrom, which the signing
  VM made with Esigner from the same files (the gated MSI's payload), by its path under the folder that holds
  -AppExe. A copy is placed only when its Authenticode digest equals the staged file's and it carries
  -CertificateFile's valid, timestamped signature; a file that needs signing and has no copy, or whose copy differs,
  stops the run, so WiX never packs an unsigned or a different file. A release build packs with it in the CPack
  hook, then the signing VM signs the MSI (the MSI itself is never placed here).

  -AzureIdentity GceFederated (ArtifactSigning on a Google Cloud VM): the VM's own Google service account signs in to
  Microsoft Entra through workload identity federation, so no Azure secret exists anywhere. The VM must run as exactly
  -GceServiceAccount, an account no other VM uses (whoever runs on a VM can obtain its account's token), and never the
  account the validation and bench VMs share. The script asks the VM's metadata server (never through a proxy) for
  that account's Google ID token for audience api://AzureADTokenExchange, checks whose and for whom it is, keeps it in
  a file in this user's temporary folder, renews it before every signing batch (it lasts an hour), and removes it at
  the end, on any error or on Ctrl+C. AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_FEDERATED_TOKEN_FILE are set only in
  signtool's environment, for each call, with every other AZURE_* setting held back; only Azure's
  WorkloadIdentityCredential is left on. -AzureTenantId and -AzureClientId (the Entra app whose federated credential
  trusts that service account) are names, not secrets. Preflight makes one real signature, so a wrong client id,
  federated subject or role fails before anything is built.

  This script never reads, prints or stores a key. It handles two credentials: GceFederated's Google ID token (an
  hour long, for Entra only), which it keeps in this user's temporary folder for signtool's dlib, never prints, and
  removes when it ends; and Esigner's account password and TOTP secret, held in memory for the run only (above).

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

.EXAMPLE
  .\scripts\angelo\sign_windows.ps1 -Mode Preflight -Backend ArtifactSigning -Publisher "Example Studio Ltd." `
    -ArtifactSigningDlib C:\angelo\artifact-signing\bin\x64\Azure.CodeSigning.Dlib.dll `
    -ArtifactSigningEndpoint https://eus.codesigning.azure.net -ArtifactSigningAccount examplesigning -ArtifactSigningProfile examplepublic
  (or set ANGELO_SIGN_BACKEND, ANGELO_SIGN_PUBLISHER, ANGELO_SIGN_ARTIFACT_DLIB, ANGELO_SIGN_ARTIFACT_ENDPOINT,
  ANGELO_SIGN_ARTIFACT_ACCOUNT and ANGELO_SIGN_ARTIFACT_PROFILE)
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("Preflight", "Sign", "Verify", "VerifyMsi")][string]$Mode,
    [string[]]$Path = @(),
    # The code-signing certificate the CA issued for the KMS key (.cer/.crt, DER or PEM). Never a .pfx.
    [string]$CertificateFile = $env:ANGELO_SIGN_CERTIFICATE,
    # projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N
    [string]$KmsKey = $env:ANGELO_SIGN_KMS_KEY,
    # Where the key lives: KmsKey (-CertificateFile + -KmsKey) or ArtifactSigning (-ArtifactSigningDlib,
    # -ArtifactSigningEndpoint/-Account/-Profile and -Publisher).
    [ValidateSet("KmsKey", "ArtifactSigning", "Esigner", "Presigned")]
    [string]$Backend = $(if ($env:ANGELO_SIGN_BACKEND) { $env:ANGELO_SIGN_BACKEND } else { "KmsKey" }),
    # Esigner: CodeSignTool's unpacked folder, its jar's pinned SHA-256, the eSigner user (a name, not a secret), the
    # certificate's credential id, and the Secret Manager project and names of the password and TOTP secret.
    [string]$EsignerHome = $env:ANGELO_SIGN_ESIGNER_HOME,
    [string]$EsignerJarSha256 = $env:ANGELO_SIGN_ESIGNER_JAR_SHA256,
    [string]$EsignerUsername = $env:ANGELO_SIGN_ESIGNER_USERNAME,
    [string]$EsignerCredentialId = $env:ANGELO_SIGN_ESIGNER_CREDENTIAL_ID,
    [string]$EsignerSecretProject = $env:ANGELO_SIGN_ESIGNER_SECRET_PROJECT,
    [string]$EsignerPasswordSecret = $(if ($env:ANGELO_SIGN_ESIGNER_PASSWORD_SECRET) { $env:ANGELO_SIGN_ESIGNER_PASSWORD_SECRET } else { "sslcom-password" }),
    [string]$EsignerTotpSecret = $(if ($env:ANGELO_SIGN_ESIGNER_TOTP_SECRET) { $env:ANGELO_SIGN_ESIGNER_TOTP_SECRET } else { "sslcom-esigner-totp" }),
    # Esigner Preflight: also make one real signature (of a probe script of ours). It costs one of the month's signings.
    [switch]$ProbeSignature,
    # Presigned: the files the signing VM signed (Esigner), laid out as under the folder that holds -AppExe.
    [string]$SignedFrom = $env:ANGELO_SIGN_SIGNED_FROM,
    [string]$AppExe = $(if ($env:ANGELO_SIGN_EXPECT) { $env:ANGELO_SIGN_EXPECT } else { "Angelo.exe" }),
    [string]$ArtifactSigningDlib = $env:ANGELO_SIGN_ARTIFACT_DLIB,
    # The Artifact Signing account's regional endpoint (https://<region>.codesigning.azure.net), name and profile.
    [string]$ArtifactSigningEndpoint = $env:ANGELO_SIGN_ARTIFACT_ENDPOINT,
    [string]$ArtifactSigningAccount = $env:ANGELO_SIGN_ARTIFACT_ACCOUNT,
    [string]$ArtifactSigningProfile = $env:ANGELO_SIGN_ARTIFACT_PROFILE,
    # ArtifactSigning: the validated legal name its certificates carry as their common name.
    [string]$Publisher = $env:ANGELO_SIGN_PUBLISHER,
    # ArtifactSigning: the extended key usage Microsoft gives this identity validation (1.3.6.1.4.1.311.97.*), the
    # same in every 3-day certificate and no one else's: with the name, what makes a signature ours. Preflight reports
    # it from a probe signature when it is not set yet.
    [string]$ArtifactSigningIdentityEku = $env:ANGELO_SIGN_ARTIFACT_EKU,
    # ArtifactSigning: how the signer signs in to Entra. Environment: DefaultAzureCredential as the environment has it
    # set up (a service principal); GceFederated: this Google Cloud VM's service account, federated (no secret).
    [ValidateSet("Environment", "GceFederated")]
    [string]$AzureIdentity = $(if ($env:ANGELO_SIGN_AZURE_IDENTITY) { $env:ANGELO_SIGN_AZURE_IDENTITY } else { "Environment" }),
    [string]$AzureTenantId = $env:ANGELO_SIGN_AZURE_TENANT_ID,
    [string]$AzureClientId = $env:ANGELO_SIGN_AZURE_CLIENT_ID,
    # GceFederated and Esigner: the signing VM's own service account (its e-mail). The VM must run as exactly this
    # account, and it must be one no other VM uses: whoever runs on a VM can obtain its account's token.
    [string]$GceServiceAccount = $env:ANGELO_SIGN_GCE_SERVICE_ACCOUNT,
    # The RFC 3161 time-stamping service; empty: DigiCert's for KmsKey, Microsoft's for ArtifactSigning, SSL.com's for
    # Esigner (whose CodeSignTool configuration must name the same).
    [string]$TimestampUrl = $env:ANGELO_SIGN_TIMESTAMP_URL,
    # Optional intermediate certificate(s) to embed (/ac), when the CA asks for it.
    [string]$ChainFile = $env:ANGELO_SIGN_CHAIN_FILE,
    [string]$SignTool = $env:ANGELO_SIGNTOOL,
    [string]$Description = "Angelo",
    [string]$DescriptionUrl = "https://angelo.studio/",
    [ValidateRange(1, 100)][int]$BatchSize = 24,
    # All: every binary that needs it gets Angelo's signature. KeyFiles: only the key files do (the app and launcher exes
    # angelo_identity.json names, the agent runtime's own files, the MSI); every other binary stays as it is, and
    # verification accepts it unsigned (a vendor's signature on it must still be valid).
    # Empty: KeyFiles for a backend that charges per signature or places the signing VM's copies (Esigner, Presigned),
    # All otherwise. Those two never take All.
    [string]$Scope = $env:ANGELO_SIGN_SCOPE,   # Esigner: CodeSignTool's batch_sign takes at most 100 files.
    [string]$Report = "",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
# Secret Manager and the time-stamping services need TLS 1.2; Windows PowerShell 5.1 may default to older protocols.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
# PowerShell does not check a default against its ValidateSet, and these defaults come from the environment: a
# misspelled value stops the script here instead of quietly running another mode (a typo once turned federation off).
if ($Backend -cnotin "KmsKey", "ArtifactSigning", "Esigner", "Presigned") {
    throw "-Backend (ANGELO_SIGN_BACKEND) is KmsKey, ArtifactSigning, Esigner or Presigned, not '$Backend'"
}
# The scope follows the flow, never an ambient setting it forgot (break-it review 2, N3): a remote-signed release signs and
# places the key files only.
if (-not $Scope) { $Scope = if ($Backend -in "Esigner", "Presigned") { "KeyFiles" } else { "All" } }
if ($Scope -cnotin "All", "KeyFiles") { throw "-Scope (ANGELO_SIGN_SCOPE) is All or KeyFiles, not '$Scope'" }
if ($Backend -in "Esigner", "Presigned" -and $Scope -ne "KeyFiles") { throw "-Backend $Backend signs the key files only (-Scope KeyFiles), not $Scope" }
if ($Backend -ne "Presigned" -and $SignedFrom) { throw "-SignedFrom (ANGELO_SIGN_SIGNED_FROM) belongs to -Backend Presigned: unset it" }
if ($AzureIdentity -cnotin "Environment", "GceFederated") {
    throw "-AzureIdentity (ANGELO_SIGN_AZURE_IDENTITY) is Environment or GceFederated, not '$AzureIdentity'"
}
if ($Backend -ne "ArtifactSigning" -and ($AzureIdentity -ne "Environment" -or $AzureTenantId -or $AzureClientId)) {
    throw "the Azure sign-in settings belong to -Backend ArtifactSigning; with $Backend they would be ignored: unset them"
}
if ($Backend -in "KmsKey", "Presigned" -and $GceServiceAccount) {
    throw "-GceServiceAccount belongs to a signing VM (ArtifactSigning's GceFederated, or Esigner); with KmsKey it would be ignored: unset it"
}
if ($Backend -ne "Esigner" -and ($EsignerHome -or $EsignerUsername -or $EsignerCredentialId -or $EsignerSecretProject -or $ProbeSignature)) {
    throw "the eSigner settings belong to -Backend Esigner; with $Backend they would be ignored: unset them"
}
# Service accounts other VMs share, never a signer: the Windows validation and bench VMs all run as this one, and run
# agents' scripts (gcp_windows_validate.sh's SA).
$SharedValidationAccounts = @("win-build@")
$Provider = "Google Cloud KMS Provider"
$CodeSigningEku = "1.3.6.1.5.5.7.3.3"
$KeyPattern = "^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+/cryptoKeyVersions/\d+\z"
$Extensions = @(".exe", ".dll", ".pyd", ".msi")
if (-not $TimestampUrl) {
    $TimestampUrl = switch ($Backend) {
        "ArtifactSigning" { "http://timestamp.acs.microsoft.com" }
        "Esigner" { "http://ts.ssl.com" }
        default { "http://timestamp.digicert.com" }
    }
}
# The dlib's metadata file, written from the parameters: names only, never a credential. Interactive and developer
# sign-ins are excluded so an unattended build fails instead of waiting for a browser.
$ArtifactSigningMetadata = Join-Path ([IO.Path]::GetTempPath()) ("angelo-artifact-signing-{0}.json" -f [guid]::NewGuid().ToString("N"))
# A person's sign-ins (browser, cached, Visual Studio, the Azure, PowerShell and developer command lines) are never
# what signs: Environment leaves a service principal (EnvironmentCredential), a workload identity or a managed identity.
$ArtifactSigningExcluded = @("InteractiveBrowserCredential", "SharedTokenCacheCredential", "VisualStudioCredential",
    "VisualStudioCodeCredential", "AzureCliCredential", "AzurePowerShellCredential", "AzureDeveloperCliCredential")
# GceFederated: the federated workload identity alone. ManagedIdentityCredential's probe would reach Google's metadata
# server (169.254.169.254 answers there too), and EnvironmentCredential would take an inherited secret.
$GceFederatedExcluded = @(@("EnvironmentCredential", "ManagedIdentityCredential") + $ArtifactSigningExcluded)
$FederatedAudience = "api://AzureADTokenExchange"
$FederatedTokenPrefix = "angelo-entra-federation-"
$FederatedTokenFile = Join-Path ([IO.Path]::GetTempPath()) ("{0}{1}.jwt" -f $FederatedTokenPrefix, [guid]::NewGuid().ToString("N"))
$ArtifactSigningMinimumSignTool = [version]"10.0.22621.755"
# The agent runtime's pin. OpenAI's unchanged release executables keep OpenAI's signature; the rest of the runtime
# (codex.exe, built from Angelo's fork) is Angelo's and carries this certificate's.
$RuntimePinFile = Join-Path $PSScriptRoot "..\addons_core\angelo_agent\agent_runtime.json"
if (-not (Test-Path $RuntimePinFile)) { throw "the agent runtime's pin is missing ($RuntimePinFile): run this script from the source tree" }
$RuntimePin = (Get-Content $RuntimePinFile -Raw -Encoding UTF8 | ConvertFrom-Json).platforms.windows
$UpstreamSigned = @($RuntimePin.upstream_signed)
$UpstreamSigner = [string]$RuntimePin.upstream_signer
$RuntimeOwn = @(@($RuntimePin.required) + @($RuntimePin.optional) | Where-Object { $_ -notin $UpstreamSigned })
if (-not $UpstreamSigner -or -not $RuntimeOwn) { throw "$RuntimePinFile names no upstream_signer or no file of Angelo's own" }
# The key files (-Scope KeyFiles): the executables people start and the updater checks, named by angelo_identity.json,
# and the agent runtime's own files; with every .msi.
$IdentityFile = Join-Path $PSScriptRoot "..\modules\angelo_identity.json"
if (-not (Test-Path $IdentityFile)) { throw "angelo_identity.json is missing ($IdentityFile): run this script from the source tree" }
$WindowsIdentity = (Get-Content $IdentityFile -Raw -Encoding UTF8 | ConvertFrom-Json).windows
$KeyFiles = @(($WindowsIdentity.exe_stem + ".exe"), ($WindowsIdentity.exe_stem + $WindowsIdentity.launcher_suffix + ".exe")) + $RuntimeOwn
function Test-KeyFile($File) { return $Scope -eq "All" -or $File.Name -in $KeyFiles -or $File.Extension.ToLowerInvariant() -eq ".msi" }

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
    # The certificate is pinned in the repository (angelo_identity.json, windows.authenticode_certificates, SHA-1
    # thumbprints): a certificate file that arrives with the inputs can never decide by itself which signature is ours.
    if ($certificate.Thumbprint -notin @($WindowsIdentity.authenticode_certificates)) {
        throw "$CertificateFile ($($certificate.Subject), $($certificate.Thumbprint)) is not a certificate angelo_identity.json pins (windows.authenticode_certificates)"
    }
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

function Assert-ArtifactSigningInputs([bool]$NeedsFiles) {
    if (-not $Publisher.Trim()) { throw "give -Publisher (or ANGELO_SIGN_PUBLISHER): the validated legal name the certificate profile carries" }
    if ($ArtifactSigningEndpoint -notmatch "^https://[a-z0-9]+\.codesigning\.azure\.net/?\z") {
        throw "give -ArtifactSigningEndpoint (or ANGELO_SIGN_ARTIFACT_ENDPOINT): the account region's https://<region>.codesigning.azure.net"
    }
    # Azure's naming rules: an account is 3-24 letters, digits or single hyphens; a profile 5-100 (quickstart).
    if ($ArtifactSigningAccount -notmatch "^[A-Za-z](?!.*--)[A-Za-z0-9-]{1,22}[A-Za-z0-9]\z") {
        throw "give -ArtifactSigningAccount (or ANGELO_SIGN_ARTIFACT_ACCOUNT): the Artifact Signing account's name"
    }
    if ($ArtifactSigningProfile -notmatch "^[A-Za-z](?!.*--)[A-Za-z0-9-]{3,98}[A-Za-z0-9]\z") {
        throw "give -ArtifactSigningProfile (or ANGELO_SIGN_ARTIFACT_PROFILE): the certificate profile's name"
    }
    if (-not $ArtifactSigningDlib) { throw "give -ArtifactSigningDlib (or ANGELO_SIGN_ARTIFACT_DLIB): Azure.CodeSigning.Dlib.dll (x64)" }
    if ($ArtifactSigningIdentityEku -and $ArtifactSigningIdentityEku -cnotmatch "^1\.3\.6\.1\.4\.1\.311\.97(\.[0-9]+)+\z") {
        throw "-ArtifactSigningIdentityEku is an Artifact Signing identity OID (1.3.6.1.4.1.311.97.*), not '$ArtifactSigningIdentityEku'"
    }
    if (-not $ArtifactSigningIdentityEku -and -not $DryRun -and $Mode -ne "Preflight") {
        throw "give -ArtifactSigningIdentityEku (or ANGELO_SIGN_ARTIFACT_EKU): Preflight reports it from a probe signature"
    }
    if (-not $NeedsFiles) { return }
    if (-not (Test-Path $ArtifactSigningDlib -PathType Leaf)) {
        throw "$ArtifactSigningDlib not found: install the Artifact Signing Client Tools (winget install -e --id Microsoft.Azure.ArtifactSigningClientTools)"
    }
    $excluded = if ($AzureIdentity -eq "GceFederated") { $GceFederatedExcluded } else { $ArtifactSigningExcluded }
    $metadata = [ordered]@{ Endpoint = $ArtifactSigningEndpoint; CodeSigningAccountName = $ArtifactSigningAccount
        CertificateProfileName = $ArtifactSigningProfile; ExcludeCredentials = @($excluded) }
    [IO.File]::WriteAllText($ArtifactSigningMetadata, ($metadata | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
}

function Assert-FederationInputs {
    if ($AzureIdentity -ne "GceFederated") { return }
    $guid = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z"
    if ($AzureTenantId -notmatch $guid) { throw "give -AzureTenantId (or ANGELO_SIGN_AZURE_TENANT_ID): the Entra tenant's id" }
    if ($AzureClientId -notmatch $guid) { throw "give -AzureClientId (or ANGELO_SIGN_AZURE_CLIENT_ID): the signer app's client id" }
    Assert-OwnSignerAccountName
}

# The signing VM's own account: a service account's e-mail, never the one the validation and bench VMs share.
function Assert-OwnSignerAccountName {
    if ($GceServiceAccount -cnotmatch "^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com\z") {
        throw "give -GceServiceAccount (or ANGELO_SIGN_GCE_SERVICE_ACCOUNT): the signing VM's own service account e-mail"
    }
    foreach ($shared in $SharedValidationAccounts) {
        if ($GceServiceAccount.StartsWith($shared)) {
            throw "$GceServiceAccount is the account the validation and bench VMs share: a signer has an account of its own"
        }
    }
}

# GET from this VM's metadata server, never through a proxy (its answers are credentials); throws for a path it lacks.
function Get-GceMetadata([string]$Path) {
    $request = [System.Net.HttpWebRequest]::Create("http://metadata.google.internal/computeMetadata/v1/" + $Path)
    $request.Proxy = $null
    $request.Headers.Add("Metadata-Flavor", "Google")
    $request.Timeout = 30000
    try { $response = $request.GetResponse() } catch {
        throw "this VM's metadata server did not answer for $($Path.Split('?')[0]) ($($_.Exception.Message)): the signer runs on a Google Cloud VM"
    }
    try { return (New-Object IO.StreamReader($response.GetResponseStream())).ReadToEnd().Trim() } finally { $response.Close() }
}

# The claims of a token (never printed): the part between its dots, base64url.
function Get-TokenClaims([string]$Token) {
    $parts = $Token.Split(".")
    if ($parts.Count -ne 3) { throw "the metadata server's answer is not an ID token" }
    $payload = $parts[1].Replace("-", "+").Replace("_", "/")
    $payload += "=" * ((4 - $payload.Length % 4) % 4)
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)) | ConvertFrom-Json
}

# GceFederated and Esigner: the VM runs as exactly -GceServiceAccount (not the account the validation VMs share), checked
# before any token or secret is asked for.
function Assert-SignerAccount {
    if (-not ($Backend -eq "Esigner" -or ($Backend -eq "ArtifactSigning" -and $AzureIdentity -eq "GceFederated"))) { return }
    $running = Get-GceMetadata "instance/service-accounts/default/email"
    if ($running -cne $GceServiceAccount) {
        throw "this VM runs as $running, not the signer account ${GceServiceAccount}: only the signing VM signs"
    }
}

# GceFederated: a fresh Google ID token for Entra in the token file signtool's dlib reads, renewed before every batch
# (and again when the server hands one with under five minutes left), so a long run never presents an expired one. The
# token is the signer account's, for Entra's audience; it is never printed.
function Update-FederatedToken {
    if ($Backend -ne "ArtifactSigning" -or $AzureIdentity -ne "GceFederated") { return }
    $path = "instance/service-accounts/default/identity?audience=$FederatedAudience&format=full"
    for ($attempt = 1; ; $attempt++) {
        $token = Get-GceMetadata $path
        $claims = Get-TokenClaims $token
        if ($claims.email -cne $GceServiceAccount -or $claims.aud -cne $FederatedAudience) {
            throw "the metadata server's token is not $GceServiceAccount's for $FederatedAudience"
        }
        if ([int64]$claims.exp - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge 300) { break }
        if ($attempt -ge 3) { throw "the metadata server keeps handing a token that expires within five minutes" }
        Start-Sleep -Seconds 5
    }
    [IO.File]::WriteAllText($FederatedTokenFile, $token, (New-Object System.Text.UTF8Encoding($false)))
}

# --- Esigner -------------------------------------------------------------------------------------------------------

function Assert-EsignerInputs([bool]$NeedsTool) {
    Assert-OwnSignerAccountName
    if ($EsignerUsername -cnotmatch "^[A-Za-z0-9._@-]{3,64}\z") { throw "give -EsignerUsername (or ANGELO_SIGN_ESIGNER_USERNAME): the eSigner account's user name" }
    if ($EsignerCredentialId -notmatch "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z") {
        throw "give -EsignerCredentialId (or ANGELO_SIGN_ESIGNER_CREDENTIAL_ID): the certificate's eSigner credential id"
    }
    if ($EsignerSecretProject -cnotmatch "^[a-z][a-z0-9-]{4,28}[a-z0-9]\z") {
        throw "give -EsignerSecretProject (or ANGELO_SIGN_ESIGNER_SECRET_PROJECT): the Secret Manager project holding the eSigner secrets"
    }
    foreach ($name in $EsignerPasswordSecret, $EsignerTotpSecret) {
        if ($name -cnotmatch "^[A-Za-z0-9_-]{1,255}\z") { throw "'$name' is not a Secret Manager secret name" }
    }
    if ($EsignerJarSha256 -cnotmatch "^[0-9a-f]{64}\z") {
        throw "give -EsignerJarSha256 (or ANGELO_SIGN_ESIGNER_JAR_SHA256): the pinned SHA-256 of CodeSignTool's jar, lowercase hex"
    }
    if (-not $NeedsTool) { return }
    if (-not $EsignerHome -or -not (Test-Path -LiteralPath $EsignerHome -PathType Container)) {
        throw "give -EsignerHome (or ANGELO_SIGN_ESIGNER_HOME): CodeSignTool's unpacked folder (windows_sign_bootstrap.ps1 installs it)"
    }
    $jars = @(Get-ChildItem -LiteralPath (Join-Path $EsignerHome "jar") -Filter "code_sign_tool-*.jar" -File -ErrorAction SilentlyContinue)
    $javas = @(Get-ChildItem -LiteralPath $EsignerHome -Recurse -Filter java.exe -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -eq "bin" })
    if ($jars.Count -ne 1 -or $javas.Count -ne 1) {
        throw "$EsignerHome holds $($jars.Count) CodeSignTool jars (jar\code_sign_tool-*.jar) and $($javas.Count) bundled java.exe; it needs one of each"
    }
    $sha = (Get-FileHash -LiteralPath $jars[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha -cne $EsignerJarSha256) { throw "$($jars[0].FullName) is $sha, not the pinned CodeSignTool jar $EsignerJarSha256" }
    $properties = Join-Path $EsignerHome "conf\code_sign_tool.properties"
    $tsa = @(Get-Content -LiteralPath $properties -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*TSA_URL\s*=" } |
        ForEach-Object { ($_ -split "=", 2)[1].Trim() })
    if ($tsa.Count -ne 1 -or $tsa[0].TrimEnd("/") -ne $TimestampUrl.TrimEnd("/")) {
        throw "CodeSignTool's $properties names TSA_URL '$($tsa -join ', ')', not -TimestampUrl $TimestampUrl"
    }
    $script:EsignerJava = $javas[0].FullName
    $script:EsignerJar = $jars[0].FullName
}

# CodeSignTool takes the password and TOTP secret as arguments: a machine that writes command lines or PowerShell
# invocations to a log would keep them there. Policy settings only (these registry values are how both are turned on).
function Assert-NoCommandLineLogging {
    $checks = @(
        @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit", "ProcessCreationIncludeCmdLine_Enabled",
          "process-creation auditing records command lines"),
        @("HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ModuleLogging", "EnableModuleLogging",
          "PowerShell module logging records commands' arguments"))
    foreach ($check in $checks) {
        $value = (Get-ItemProperty -LiteralPath $check[0] -Name $check[1] -ErrorAction SilentlyContinue).($check[1])
        if ($value -eq 1) { throw "this machine's policy turns on $($check[2]) ($($check[0])\$($check[1])): the eSigner secrets would be logged; sign elsewhere" }
    }
}

# One secret's latest version from Secret Manager, as the VM's own account (checked first): its value, never printed.
function Get-EsignerSecret([string]$AccessToken, [string]$Name) {
    $url = "https://secretmanager.googleapis.com/v1/projects/$EsignerSecretProject/secrets/$Name/versions/latest:access"
    $request = [System.Net.HttpWebRequest]::Create($url)
    $request.Proxy = $null
    $request.Headers.Add("Authorization", "Bearer " + $AccessToken)
    $request.Timeout = 30000
    try { $response = $request.GetResponse() } catch {
        throw "Secret Manager did not give $GceServiceAccount the secret $Name in $EsignerSecretProject ($($_.Exception.Message)); the VM needs the cloud-platform scope and the account secretAccessor on it"
    }
    try { $body = (New-Object IO.StreamReader($response.GetResponseStream())).ReadToEnd() } finally { $response.Close() }
    $data = ($body | ConvertFrom-Json).payload.data
    if (-not $data) { throw "the secret $Name in $EsignerSecretProject is empty" }
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($data)).TrimEnd([char[]]@("`r", "`n"))
}

function Read-EsignerSecrets {
    $token = (Get-GceMetadata "instance/service-accounts/default/token" | ConvertFrom-Json).access_token
    if (-not $token) { throw "the metadata server gave $GceServiceAccount no access token" }
    $script:EsignerSecrets = @{ Password = (Get-EsignerSecret $token $EsignerPasswordSecret)
        Totp = (Get-EsignerSecret $token $EsignerTotpSecret) }
    try { [void][Convert]::FromBase64String($script:EsignerSecrets.Totp) } catch {
        throw "the secret $EsignerTotpSecret is not a base64 TOTP secret (eSigner's)"
    }
    # java.exe reads an ANSI command line: a character outside printable ASCII could be best-fit mapped to a quote or a
    # space and split the argument. Such a secret is refused rather than passed.
    foreach ($name in "Password", "Totp") {
        if ($script:EsignerSecrets[$name] -cnotmatch "^[\x20-\x7E]+\z") {
            throw "the eSigner $($name.ToLowerInvariant()) secret holds characters outside printable ASCII: CodeSignTool cannot take it safely"
        }
    }
    # A private folder for the JVM's temporary and crash files, removed when the run ends.
    $script:EsignerPrivate = Join-Path ([IO.Path]::GetTempPath()) ("angelo-esigner-jvm-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $script:EsignerPrivate)
}

# Whatever CodeSignTool prints, with every secret's value taken out first.
function Hide-EsignerSecrets([string]$Text) {
    foreach ($value in @($script:EsignerSecrets.Values)) {
        if ($value) { $Text = $Text.Replace($value, "<secret>") }
    }
    return $Text
}

# One argument quoted as Windows programs split their command line (CommandLineToArgvW and the MSVC runtime, which
# java.exe's launcher follows): any printable ASCII character is safe.
# Always quoted: java's launcher expands an unquoted * or ? as a file wildcard.
function ConvertTo-ProcessArgument([string]$Value) {
    $quoted = [regex]::Replace($Value, '(\\*)"', [System.Text.RegularExpressions.MatchEvaluator] { param($m) $m.Groups[1].Value * 2 + '\"' })
    return '"' + [regex]::Replace($quoted, '(\\+)\z', [System.Text.RegularExpressions.MatchEvaluator] { param($m) $m.Groups[1].Value * 2 }) + '"'
}

# CodeSignTool, as its bundled java.exe and pinned jar, from its own folder (it reads conf\ there).
# The ledger, on disk at every change (beside the report): a run that dies keeps it. ToArray(): under Windows
# PowerShell 5.1, @() of a generic List throws "Argument types do not match" (it did so at the end of both first
# probes, after their eSigner calls, hiding whether they signed).
function Save-EsignerLedger {
    if (-not $Report) { return }
    $json = ConvertTo-Json -InputObject $script:EsignerLedger.ToArray() -Depth 3
    [IO.File]::WriteAllText("$Report.ledger.json", $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-CodeSignTool([string]$Command, [string[]]$Arguments, [bool]$WithTotp, [int]$Files = 0) {
    # No perf data file (it records the command line), crash logs and temporary files only in the private folder.
    $jvm = @("-XX:-UsePerfData", "-XX:ErrorFile=$(Join-Path $script:EsignerPrivate 'hs_err_pid%p.log')",
        "-Djava.io.tmpdir=$script:EsignerPrivate")
    $all = $jvm + @("-jar", $script:EsignerJar, $Command, "-username=$EsignerUsername", "-password=$($script:EsignerSecrets.Password)") + $Arguments
    if ($WithTotp) { $all += "-totp_secret=$($script:EsignerSecrets.Totp)" }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $script:EsignerJava
    $start.Arguments = (@($all | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
    $start.WorkingDirectory = $EsignerHome
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    # Options the JVM would take from the environment never reach it (they could log or alter the run).
    foreach ($name in "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "CLASSPATH") {
        if ($start.EnvironmentVariables.ContainsKey($name)) { $start.EnvironmentVariables.Remove($name) }
    }
    # The ledger counts every call that may have spent signings, failed ones included (written even when the run fails):
    # the entry exists before the process starts, so a call that throws after the start is still counted.
    $entry = [ordered]@{ at = (Get-Date -Format o); command = $Command; files = $Files; exit = $null }
    $script:EsignerLedger.Add($entry)
    Save-EsignerLedger
    $process = [System.Diagnostics.Process]::Start($start)
    $errors = $process.StandardError.ReadToEndAsync()
    $output = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    $entry.exit = $process.ExitCode
    Save-EsignerLedger
    # The output stays in memory, for this script's own parsing: it is never thrown, printed or reported.
    return @{ Code = $process.ExitCode; Output = (Hide-EsignerSecrets ($output + $errors.Result)) }
}

# One batch: copies under unique names in a folder of their own, batch_sign to another folder, then each returned
# copy replaces its original only if it differs by a signature alone (an equal Authenticode digest; an .msi has no PE
# digest and is checked by VerifyMsi). Every PE input is digested before anything is spent, so a file that cannot be
# checked costs nothing; a retry sends only the files that did not come back. Messages are fixed: CodeSignTool's own
# output never leaves this process.
function Invoke-EsignerBatch([object[]]$Files) {
    $work = Join-Path ([IO.Path]::GetTempPath()) ("angelo-esigner-" + [guid]::NewGuid().ToString("N"))
    try {
        $staged = for ($i = 0; $i -lt $Files.Count; $i++) {
            $digest = if ($Files[$i].Extension.ToLowerInvariant() -ne ".msi") { Get-AuthenticodePeDigest $Files[$i].FullName } else { $null }
            [pscustomobject]@{ File = $Files[$i]; Name = ("{0:d3}-{1}" -f $i, $Files[$i].Name); Digest = $digest; Signed = $null }
        }
        for ($attempt = 1; ; $attempt++) {
            $pending = @($staged | Where-Object { -not $_.Signed })
            $in = Join-Path $work "in-$attempt"; $out = Join-Path $work "out-$attempt"
            [void](New-Item -ItemType Directory -Path $in, $out)
            foreach ($item in $pending) { Copy-Item -LiteralPath $item.File.FullName -Destination (Join-Path $in $item.Name) }
            $run = Invoke-CodeSignTool "batch_sign" @("-credential_id=$EsignerCredentialId", "-input_dir_path=$in", "-output_dir_path=$out") $true $pending.Count
            foreach ($item in $pending) {
                $returned = Join-Path $out $item.Name
                if (Test-Path -LiteralPath $returned) { $item.Signed = $returned }
            }
            $missing = @($staged | Where-Object { -not $_.Signed })
            if (-not $missing) { break }
            if ($attempt -ge 2) {
                throw "CodeSignTool batch_sign returned $($staged.Count - $missing.Count) of $($staged.Count) files (last exit $($run.Code)); see the run's ledger"
            }
            Write-Warning "CodeSignTool batch_sign returned $($staged.Count - $missing.Count) of $($staged.Count) files (exit $($run.Code)); retrying the $($missing.Count) missing"
            Start-Sleep -Seconds 30
        }
        foreach ($item in $staged) {
            if ($item.Digest) {
                $after = Get-AuthenticodePeDigest $item.Signed
                if ($item.Digest -cne $after) { throw "$($item.File.FullName) came back changed beyond its signature (digest $($item.Digest) -> $after)" }
            }
            Copy-Item -LiteralPath $item.Signed -Destination $item.File.FullName -Force
        }
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Esigner Preflight: the secrets are readable and eSigner lists this certificate's credential for the account (no
# signing). -ProbeSignature also signs a probe script of ours (one of the month's signings).
function Assert-EsignerWorks {
    $listed = Invoke-CodeSignTool "get_credential_ids" @() $false
    if ($listed.Code -ne 0 -or $listed.Output -notmatch [regex]::Escape($EsignerCredentialId)) {
        throw "eSigner does not list the credential $EsignerCredentialId for $EsignerUsername (exit $($listed.Code))"
    }
    if (-not $ProbeSignature) { return "credential listed" }
    $work = Join-Path ([IO.Path]::GetTempPath()) ("angelo-sign-probe-" + [guid]::NewGuid().ToString("N"))
    $probe = Join-Path $work "probe.ps1"; $out = Join-Path $work "out"
    try {
        [void](New-Item -ItemType Directory -Path $work, $out)
        [IO.File]::WriteAllText($probe, "# Angelo signing preflight probe; never distributed.`r`n", (New-Object System.Text.UTF8Encoding($true)))
        $run = Invoke-CodeSignTool "sign" @("-credential_id=$EsignerCredentialId", "-input_file_path=$probe", "-output_dir_path=$out") $true 1
        $signedProbe = Join-Path $out "probe.ps1"
        $signature = if (Test-Path -LiteralPath $signedProbe) { Get-AuthenticodeSignature -LiteralPath $signedProbe } else { $null }
        if ($run.Code -ne 0 -or -not $signature -or $signature.Status -ne "Valid" -or
            $signature.SignerCertificate.Thumbprint -ne $script:EsignerCertificate.Thumbprint) {
            throw "a probe signature failed, so the release would too ($(if ($signature) { $signature.Status } else { 'nothing returned' }), exit $($run.Code))"
        }
        if (-not $signature.TimeStamperCertificate) { throw "the probe signature has no timestamp" }
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    return "credential listed, probe signed"
}

# --- Presigned -----------------------------------------------------------------------------------------------------

# The folder that holds the app's executable under -Path: signed copies are found by their path below it.
function Get-AppRoot {
    $found = @(foreach ($item in $Path) { Get-ChildItem -LiteralPath $item -Recurse -Filter $AppExe -File -ErrorAction SilentlyContinue })
    if ($found.Count -ne 1) { throw "-Path holds $($found.Count) $AppExe; Presigned places copies below the one folder that holds it" }
    return $found[0].Directory.FullName
}

# Each file that needs our signature gets the signing VM's copy, if it differs by a signature alone and the signature is
# ours, valid and timestamped. Returns the placed files' paths.
function Invoke-PresignedPlacement([object[]]$Files, $Certificate) {
    . (Join-Path $PSScriptRoot "windows_pe_digest.ps1")
    $root = Get-AppRoot
    $placed = @()
    $placing = Join-Path ([IO.Path]::GetTempPath()) ("angelo-placing-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $placing)
    try {
    foreach ($file in $Files) {
        if ($file.Extension.ToLowerInvariant() -eq ".msi") { throw "$($file.FullName): the signing VM signs the MSI itself; Presigned places only its contents" }
        if (-not $file.FullName.StartsWith($root + "\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "$($file.FullName) is outside $root, where the signed copies are laid out"
        }
        $relative = $file.FullName.Substring($root.Length + 1)
        $copy = Join-Path $SignedFrom $relative
        if (-not (Test-Path -LiteralPath $copy -PathType Leaf)) { throw "the signing VM returned no signed copy of $relative" }
        # The exact bytes placed are the ones checked: a private copy is verified, then moved in, then checked again.
        $private = Join-Path $placing ([guid]::NewGuid().ToString("N") + $file.Extension)
        Copy-Item -LiteralPath $copy -Destination $private
        $signature = Get-AuthenticodeSignature -LiteralPath $private
        if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate -or
            $signature.SignerCertificate.Thumbprint -ne $Certificate.Thumbprint -or -not $signature.TimeStamperCertificate) {
            throw "the signed copy of $relative is $($signature.Status), not $($Certificate.Subject)'s timestamped signature"
        }
        $staged = Get-AuthenticodePeDigest $file.FullName
        $signed = Get-AuthenticodePeDigest $private
        if ($staged -cne $signed) { throw "the signed copy of $relative is another file (digest $signed, staged $staged): rebuild from the gated inputs" }
        Move-Item -LiteralPath $private -Destination $file.FullName -Force
        if ((Get-AuthenticodePeDigest $file.FullName) -cne $staged -or
            (Get-AuthenticodeSignature -LiteralPath $file.FullName).SignerCertificate.Thumbprint -ne $Certificate.Thumbprint) {
            throw "$relative changed while it was placed"
        }
        $placed += $file.FullName
    }
    } finally {
        Remove-Item -LiteralPath $placing -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $placed
}

function Assert-SignToolFor([string]$Tool) {
    if ($Backend -ne "ArtifactSigning") { return }
    $version = [version](((Get-Item $Tool).VersionInfo.FileVersion -split " ")[0])
    if ($version -lt $ArtifactSigningMinimumSignTool) {
        throw "$Tool is $version; Artifact Signing's dlib needs signtool $ArtifactSigningMinimumSignTool or newer (set ANGELO_SIGNTOOL)"
    }
}

function Assert-Inputs([bool]$NeedsProvider) {
    if ($TimestampUrl -notmatch "^https?://") { throw "-TimestampUrl must be an RFC 3161 http(s) address" }
    if ($Backend -eq "ArtifactSigning") { Assert-ArtifactSigningInputs $NeedsProvider; Assert-FederationInputs; return }
    if ($Backend -eq "Presigned") {
        if ($NeedsProvider -and -not ($SignedFrom -and (Test-Path -LiteralPath $SignedFrom -PathType Container))) {
            throw "give -SignedFrom (or ANGELO_SIGN_SIGNED_FROM): the folder of files the signing VM signed"
        }
        return
    }
    if ($Backend -eq "Esigner") {   # Verify needs the certificate only; a dry run checks the names.
        if ($NeedsProvider -or $DryRun) { Assert-EsignerInputs $NeedsProvider }
        return
    }
    if (-not $KmsKey -or $KmsKey -notmatch $KeyPattern) {
        throw "give -KmsKey (or ANGELO_SIGN_KMS_KEY) as projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N"
    }
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
    if ($Backend -eq "ArtifactSigning") {
        $arguments = @("sign", "/v", "/fd", "sha256", "/tr", $TimestampUrl, "/td", "sha256", "/dlib", $ArtifactSigningDlib,
            "/dmdf", $ArtifactSigningMetadata, "/d", $Description, "/du", $DescriptionUrl)
        return $arguments + $Files
    }
    $arguments = @("sign", "/v", "/fd", "sha256", "/tr", $TimestampUrl, "/td", "sha256", "/f", $CertificateFile,
        "/csp", $Provider, "/kc", $KmsKey, "/d", $Description, "/du", $DescriptionUrl)
    if ($ChainFile) { $arguments += @("/ac", $ChainFile) }
    return $arguments + $Files
}

# signtool, with GceFederated's sign-in in its environment only for this call: no AZURE_* setting lingers in this
# process (the build goes on for hours after signing), and none inherited (a secret, another authority) can change
# which credential signs or where the token goes.
function Invoke-SignTool([string]$Tool, [string[]]$Arguments) {
    $federated = $Backend -eq "ArtifactSigning" -and $AzureIdentity -eq "GceFederated"
    $saved = @{}
    if ($federated) {
        foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like "AZURE_*" })) {
            $saved[$item.Name] = $item.Value; Remove-Item -LiteralPath ("Env:" + $item.Name)
        }
        $env:AZURE_TENANT_ID = $AzureTenantId; $env:AZURE_CLIENT_ID = $AzureClientId; $env:AZURE_FEDERATED_TOKEN_FILE = $FederatedTokenFile
        $env:AZURE_TOKEN_CREDENTIALS = "WorkloadIdentityCredential"   # an allow-list where Azure.Identity reads it
    }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $output = & $Tool @Arguments 2>&1 | Out-String; $code = $LASTEXITCODE } finally {
        $ErrorActionPreference = $old
        if ($federated) {
            foreach ($name in "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_FEDERATED_TOKEN_FILE", "AZURE_TOKEN_CREDENTIALS") {
                Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
            }
            foreach ($name in $saved.Keys) { Set-Item -LiteralPath ("Env:" + $name) $saved[$name] }
        }
    }
    return @{ Code = $code; Output = $output }
}

# Preflight with Artifact Signing: one real signature, through the same sign-in and profile as the release (a wrong
# client id, federated subject or role otherwise fails hours later inside cpack), on a script of our own (never
# someone else's program: a system tool signed as Angelo's would be ours to answer for).
function Assert-SigningWorks([string]$Tool) {
    if ($Backend -ne "ArtifactSigning") { return }
    $probe = Join-Path ([IO.Path]::GetTempPath()) ("angelo-sign-probe-{0}.ps1" -f [guid]::NewGuid().ToString("N"))
    [IO.File]::WriteAllText($probe, "# Angelo signing preflight probe; never distributed.`r`n", (New-Object System.Text.UTF8Encoding($true)))
    try {
        Update-FederatedToken
        $signed = Invoke-SignTool $Tool (Get-SignArguments @($probe))
        if ($signed.Code -ne 0) { throw "a probe signature failed, so the release would too: $($signed.Output)" }
        $signature = Get-AuthenticodeSignature -LiteralPath $probe
        if (-not $ArtifactSigningIdentityEku) {
            $found = @(Get-IdentityEkus $signature) -join ", "
            throw "set -ArtifactSigningIdentityEku (ANGELO_SIGN_ARTIFACT_EKU) to this identity validation's EKU; the probe's certificate carries: $found"
        }
        if (-not (Test-Ours $signature $null)) { throw "the probe's signature is $($signature.Status), not $Publisher's with $ArtifactSigningIdentityEku" }
    } finally {
        Remove-Item -LiteralPath $probe -ErrorAction SilentlyContinue
    }
}

# The certificate's Artifact Signing identity EKUs (1.3.6.1.4.1.311.97.*).
function Get-IdentityEkus($Signature) {
    if (-not $Signature.SignerCertificate) { return @() }
    return @($Signature.SignerCertificate.Extensions |
        Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } |
        ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value } | Where-Object { $_.StartsWith("1.3.6.1.4.1.311.97.") })
}

# The signer's common name ("OpenAI OpCo, LLC"), or "" for an unsigned file.
function Get-SignerName($Signature) {
    if (-not $Signature.SignerCertificate) { return "" }
    return $Signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
}

# ASCII letter case and runs of spaces and tabs only, as the updater folds a publisher's name (update_stage_win._fold):
# any other character, a non-ASCII look-alike included, stays itself.
function Format-Name([string]$Name) {
    $spaced = [regex]::Replace($Name.Trim([char[]]@(" ", "`t")), "[ \t]+", " ")
    return [regex]::Replace($spaced, "[A-Z]", [System.Text.RegularExpressions.MatchEvaluator] { param($m) $m.Value.ToLowerInvariant() })
}

# Whether a valid signature is Angelo's: this certificate's (KmsKey), or any certificate Artifact Signing issued to
# -Publisher (a new one every three days, so never one thumbprint).
function Test-Ours($Signature, $Certificate) {
    if ($Signature.Status -ne "Valid" -or -not $Signature.SignerCertificate) { return $false }
    if ($Backend -eq "ArtifactSigning") {
        # The name alone would accept any trusted certificate with it, from any CA: this identity's EKU too.
        return (Format-Name (Get-SignerName $Signature)) -ceq (Format-Name $Publisher) -and
            $ArtifactSigningIdentityEku -and ($ArtifactSigningIdentityEku -cin @(Get-IdentityEkus $Signature))
    }
    return $Signature.SignerCertificate.Thumbprint -eq $Certificate.Thumbprint
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
            if (Test-Ours $signature $Certificate) { return "ours" }
            if ($File.Name -in $KeyFiles) {   # Key files are always Angelo's (break-it review 2, N6), whatever the scope.
                throw "$($File.FullName) is one of Angelo's key files and must carry this certificate's signature, not $($signature.SignerCertificate.Subject)'s: use the unsigned build"
            }
            return "vendor:" + $signature.SignerCertificate.Subject
        }
        "HashMismatch" { throw "$($File.FullName) was changed after it was signed (HashMismatch): refusing to sign over it" }
        "NotSupportedFileFormat" { throw "$($File.FullName) cannot carry an Authenticode signature" }
        default { return "resign:" + $signature.Status }
    }
}

# -Scope KeyFiles: a binary that is not a key file may stay unsigned. One that carries a signature is checked like any
# other (a vendor's must be valid), and a key file never stays unsigned.
function Test-UnsignedKept($File) {
    if (Test-KeyFile $File) { return $false }
    return (Get-AuthenticodeSignature -LiteralPath $File.FullName).Status -eq "NotSigned"
}

function Assert-Signed($File, $Certificate, [string]$Tool, [bool]$AllowVendor) {
    $verified = Invoke-SignTool $Tool @("verify", "/pa", "/q", $File.FullName)
    if ($verified.Code -ne 0) { throw "signtool verify /pa failed for $($File.FullName): $($verified.Output)" }
    $signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
    if ($signature.Status -ne "Valid") { throw "$($File.FullName): $($signature.Status) $($signature.StatusMessage)" }
    if (-not $signature.TimeStamperCertificate) { throw "$($File.FullName) has no timestamp" }
    $ours = Test-Ours $signature $Certificate
    if ($File.Name -in $UpstreamSigned) {
        if ($ours -or (Get-SignerName $signature) -ne $UpstreamSigner) {
            throw "$($File.FullName) must carry $UpstreamSigner's own signature, not $($signature.SignerCertificate.Subject)'s"
        }
        return $false
    }
    if ($File.Name -in $KeyFiles -and -not $ours) {
        throw "$($File.FullName) is one of Angelo's key files and must be signed by $SignerLabel, not $($signature.SignerCertificate.Subject)"
    }
    if (-not $ours -and -not $AllowVendor) { throw "$($File.FullName) is signed by $($signature.SignerCertificate.Subject), not $SignerLabel" }
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

$script:EsignerLedger = New-Object System.Collections.Generic.List[object]
$result = [ordered]@{ mode = $Mode; dry_run = [bool]$DryRun; backend = $Backend; kms_key = $KmsKey; timestamp_url = $TimestampUrl;
    signed = @(); vendor_signed = @(); upstream_kept = @(); unsigned_kept = @(); already_ours = 0; verified = 0; scope = $Scope }

# Token files an interrupted run left (over two hours old: none still valid, none another run is using).
Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Filter "$FederatedTokenPrefix*.jwt" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddHours(-2) } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -ErrorAction SilentlyContinue }
# Whatever ends the run (an error, Ctrl+C), its temporary files go: the dlib's names and, above all, a token.
try {
if ($DryRun) {
    if ($Mode -ne "Sign") { throw "-DryRun applies to -Mode Sign" }
    Assert-Inputs $false
    $all = @(Get-Targets)
    $targets = @($all | Where-Object { $_.Name -notin $UpstreamSigned -and (Test-KeyFile $_) } | ForEach-Object { $_.FullName })
    $result.upstream_kept = @($all | Where-Object { $_.Name -in $UpstreamSigned } |
        ForEach-Object { "$($_.Name) (kept: $UpstreamSigner's signature, checked when signing)" })
    $batches = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $targets.Count; $i += $BatchSize) {
        $batches.Add([string[]](Get-SignArguments $targets[$i..([math]::Min($i + $BatchSize, $targets.Count) - 1)]))
    }
    if ($Backend -eq "Presigned") {
        $batches.Clear()
        foreach ($target in $targets) { Write-Host "place the signing VM's copy (digest- and signature-checked) over $target" }
    } elseif ($Backend -eq "Esigner") {   # Never a secret: CodeSignTool's own arguments are shown without them.
        $batches.Clear()
        for ($i = 0; $i -lt $targets.Count; $i += $BatchSize) { $batches.Add([string[]]$targets[$i..([math]::Min($i + $BatchSize, $targets.Count) - 1)]) }
        foreach ($batch in $batches) {
            Write-Host ("CodeSignTool batch_sign -credential_id=$EsignerCredentialId ({0} copies, digest-checked): {1}" -f $batch.Count, ($batch -join " "))
        }
    } else {
        foreach ($batch in $batches) { Write-Host ("signtool " + ($batch -join " ")) }
    }
    $result.signed = $targets
    $result.batches = $batches.Count
} else {
    if ($env:OS -ne "Windows_NT") { throw "Authenticode signing runs on Windows (use -DryRun elsewhere)" }
    if ($Backend -eq "ArtifactSigning") {
        $certificate = $null
        $SignerLabel = "Artifact Signing's certificate for $Publisher"
        $result.certificate = [ordered]@{ publisher = $Publisher; endpoint = $ArtifactSigningEndpoint
            account = $ArtifactSigningAccount; profile = $ArtifactSigningProfile; identity = $AzureIdentity
            client_id = $AzureClientId }
    } else {   # KmsKey, Esigner and Presigned: one certificate, so one thumbprint.
        $certificate = Get-Certificate
        $script:EsignerCertificate = $certificate
        $SignerLabel = $certificate.Subject
        $result.certificate = [ordered]@{ subject = $certificate.Subject; issuer = $certificate.Issuer; thumbprint = $certificate.Thumbprint;
            not_after = $certificate.NotAfter.ToString("o") }
    }
    $tool = Find-SignTool
    $result.signtool = $tool
    Assert-Inputs ($Mode -in "Preflight", "Sign")
    if ($Mode -in "Preflight", "Sign") { Assert-SignToolFor $tool; Assert-SignerAccount; Update-FederatedToken }
    if ($Backend -eq "Esigner" -and $Mode -in "Preflight", "Sign") {
        . (Join-Path $PSScriptRoot "windows_pe_digest.ps1")
        Assert-NoCommandLineLogging
        Read-EsignerSecrets
        $result.esigner = [ordered]@{ username = $EsignerUsername; credential_id = $EsignerCredentialId
            secret_project = $EsignerSecretProject; signer_account = $GceServiceAccount; jar_sha256 = $EsignerJarSha256 }
    }
    if ($Mode -eq "Preflight" -and $Backend -eq "Esigner") { $result.preflight_signature = Assert-EsignerWorks }
    elseif ($Mode -eq "Preflight") { Assert-SigningWorks $tool; $result.preflight_signature = "ok" }
    if ($Mode -eq "Sign") {
        $pending = New-Object System.Collections.Generic.List[object]
        $result.resigned = @()
        foreach ($file in @(Get-Targets)) {
            $decision = Get-Decision $file $certificate
            if (-not (Test-KeyFile $file)) {   # -Scope KeyFiles
                # The signing VM is sent key files only: anything else is a mistake that would spend signings (N8).
                if ($Backend -eq "Esigner") { throw "$($file.FullName) is not a key file: an eSigner run signs the key files only" }
                # A signature that is there must be valid: it would fail VerifyMsi after the MSI was signed (N13).
                if ($decision.StartsWith("resign:")) { throw "$($file.FullName) carries an invalid signature ($($decision.Substring(7))): replace the file" }
                if ($decision -eq "sign") { $result.unsigned_kept += $file.Name; continue }
            }
            if ($decision -eq "sign") { $pending.Add($file) }
            elseif ($decision -eq "ours") { $result.already_ours++ }
            elseif ($decision.StartsWith("resign:")) { $pending.Add($file); $result.resigned += "$($file.Name) ($($decision.Substring(7)))" }
            elseif ($decision.StartsWith("upstream:")) { $result.upstream_kept += "$($file.Name) ($($decision.Substring(9)))" }
            else { $result.vendor_signed += "$($file.Name) ($($decision.Substring(7)))" }
        }
        $toSign = $pending.ToArray()
        if ($Backend -eq "Presigned") {   # Placed, not signed: the copies' signatures are checked below like any other.
            $result.signed = @(Invoke-PresignedPlacement $toSign $certificate)
            $result.placed_from = $SignedFrom
            $toSign = @()
        }
        for ($i = 0; $i -lt $toSign.Count; $i += $BatchSize) {
            $batch = @($toSign[$i..([math]::Min($i + $BatchSize, $toSign.Count) - 1)] | ForEach-Object { $_.FullName })
            if ($Backend -eq "Esigner") {   # Each file counts against the eSigner quota (reported as esigner_signings).
                Invoke-EsignerBatch @($toSign[$i..([math]::Min($i + $BatchSize, $toSign.Count) - 1)])
                $result.signed += $batch
                $result.esigner_signings = 0   # Files sent to batch_sign, retries included: what eSigner may have charged.
                foreach ($entry in $script:EsignerLedger) { if ($entry.command -eq "batch_sign") { $result.esigner_signings += [int]$entry.files } }
                continue
            }
            for ($attempt = 1; ; $attempt++) {   # A busy time-stamping service is the usual failure.
                Update-FederatedToken
                $signed = Invoke-SignTool $tool (Get-SignArguments $batch)
                if ($signed.Code -eq 0) { break }
                if ($attempt -ge 4) { throw "signtool sign failed after $attempt attempts: $($signed.Output)" }
                Write-Warning "signtool sign failed (attempt $attempt), retrying: $(($signed.Output -split "`n" | Select-Object -Last 3) -join ' ')"
                Start-Sleep -Seconds (15 * $attempt)
            }
            $result.signed += $batch
        }
        foreach ($file in @($pending.ToArray())) { [void](Assert-Signed $file $certificate $tool $false); $result.verified++ }
    } elseif ($Mode -eq "Verify") {
        foreach ($file in @(Get-Targets)) {
            if (Test-UnsignedKept $file) { $result.unsigned_kept += $file.Name; continue }
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
                if (Test-UnsignedKept $file) { $result.unsigned_kept += $file.Name; continue }
                if (Assert-Signed $file $certificate $tool $true) { $result.verified++ } else { $result.vendor_signed += $file.Name }
            }
            # Every key file the MSI holds must be there and ours (Assert-Signed above held them to it).
            $missing = @($KeyFiles | Where-Object { $_ -notin @($inner | ForEach-Object { $_.Name }) })
            if ($missing) { throw "the MSI holds no $($missing -join ', ')" }
            $result.agent_runtime = Assert-RuntimeFolder $inner
            $result.msi = [ordered]@{ name = $msi[0].Name; binaries = $inner.Count }
        } finally {
            Remove-Item $folder -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

} finally {
    Remove-Item -LiteralPath $ArtifactSigningMetadata, $FederatedTokenFile -ErrorAction SilentlyContinue
    $script:EsignerSecrets = $null   # The eSigner password and TOTP secret live no longer than the run.
    if ($script:EsignerPrivate) { Remove-Item -LiteralPath $script:EsignerPrivate -Recurse -Force -ErrorAction SilentlyContinue }
    # Every eSigner call that may have spent signings, written whether the run succeeded or not.
    # Never in place of the run's own error: an exception here would replace it (and hide whether anything signed).
    if ($script:EsignerLedger.Count) {
        try { Save-EsignerLedger } catch { Write-Warning "the eSigner ledger could not be written: $($_.Exception.Message)" }
    }
}
$json = $result | ConvertTo-Json -Depth 4
if ($Report) { [IO.File]::WriteAllText($Report, $json, (New-Object System.Text.UTF8Encoding($false))) }
Write-Host ("{0}: signed {1}, verified {2}, already ours {3}, vendor-signed kept {4}, agent runtime's upstream signatures kept {5}" -f
    $Mode, @($result.signed).Count, $result.verified, $result.already_ours, @($result.vendor_signed).Count, @($result.upstream_kept).Count)
