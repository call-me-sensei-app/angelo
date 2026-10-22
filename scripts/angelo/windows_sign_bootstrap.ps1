# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Google Compute Engine startup script of the Windows signing VM: installs the pinned signing tools, then powers off.

.DESCRIPTION
  The signing VM (doc/angelo/releasing.rst, "Windows signing VM") lives in its own project and runs as its own
  service account, named *-signer@: the only account that can read the eSigner secrets in Secret Manager. This
  script refuses to run as any other account (metadata angelo-sign-account names the expected one, and it must be a
  *-signer@ account, never the account the validation VMs share). The validation VMs' bootstrap
  (windows_cloud_bootstrap.ps1) refuses the reverse.

  At every boot it is idempotent: each install leaves a marker in C:\angelo\state.
  - SSL.com's CodeSignTool for Windows (its own Java runtime inside): metadata angelo-codesigntool-url (https, or the
    reviewed copy in the signing bucket, gs://) and angelo-codesigntool-sha256 pin the zip; it is unpacked into C:\angelo\codesigntool, and its jar's SHA-256 is
    recorded in C:\angelo\codesigntool\JAR-SHA256.txt (sign_windows.ps1's -EsignerJarSha256). SSL.com publishes no
    checksum, so the pin is the one recorded when the zip was first fetched and reviewed.
  - signtool (Windows SDK Signing Tools only), from metadata angelo-winsdk-url and angelo-winsdk-sha256 (the SDK
    installer, winsdksetup.exe), which sign_windows.ps1 uses to verify every signature.
  A receipt (account, pins, the installed jar's SHA-256 and CodeSignTool's TSA_URL; never a secret) goes to
  gs://<angelo-bucket>/signed/bootstrap/, the one prefix the signer may write.

  Then, when metadata angelo-sign-job names a job, it signs that job once and the VM powers off (sign_remote.sh
  starts it so). Everything that decides what runs and what is signed comes from metadata, which only the release
  owner can set (the signer account cannot):
  - angelo-sign-code: the signing code's SHA-256s ({"scripts/angelo/sign_windows.ps1": "<sha256>", ...: also
    windows_pe_digest.ps1, agent_runtime.json and angelo_identity.json}). Each file is gs://<bucket>/code/<sha256>,
    kept only with that hash, and laid out as in the repository. A job never brings code.
  - angelo-sign-job-sha256: the job description's SHA-256, gs://<bucket>/input/<job>/job.json, data only:
      {"kind": "check" | "probe" | "files" | "msi", "certificate": "<sha256>", "files": {"<relative path>": "<sha256>", ...},
       "msi": {"name": "<file name>", "sha256": "<sha256>"}, "esigner": {"username", "credential_id", "secret_project"}}
    Its inputs are gs://<bucket>/input/<job>/<sha256>, each kept only with its hash.
  Before anything is spent the VM claims the job: it creates signed/<job>/CLAIM only if no such object exists (a
  generation match of 0), so a job is signed at most once, whatever restarts it. No MSI is ever run here.
  - files: the key files (sign_remote.sh: the app's and launcher's executables, the agent runtime's own) are signed
    at their relative paths (sign_windows.ps1 -Backend Esigner, each digest-checked) and go to signed/<job>/files/.
  - msi: the MSI a build packed is signed (as a file: nothing in it runs) and goes to signed/<job>/<name>.
  - probe: Preflight with -ProbeSignature (one signing, of sign_windows.ps1's own probe script; valid, ours,
    timestamped).
  - check: Preflight only (no signing): the secrets are read and eSigner lists the credential. The free way to prove
    a change to the signing code or the VM before a job that spends.
  The report and the ledger of every eSigner call (sign_windows.ps1's; never a secret) go to signed/<job>/, with DONE,
  or FAILED and the reason's first line.

  It never reads a secret: sign_windows.ps1 -Backend Esigner fetches them at signing time.
#>
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = "C:\angelo"
$state = Join-Path $root "state"
$logs = Join-Path $root "logs"
$codeSignTool = Join-Path $root "codesigntool"
New-Item -ItemType Directory -Force $root, $state, $logs | Out-Null
Start-Transcript -Path (Join-Path $logs ("sign-bootstrap-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))) -Append | Out-Null
# Whatever fails, the signing VM powers off: it never stays up holding the signer's account (break-it review 2, N1).
# First, best effort, the reason goes to signed/bootstrap/failed-*.txt: a VM that only powered off says nothing (the
# serial port shows the startup-script runner's lines, not this script's). No secret is in scope here: a job's own
# failures are caught inside the job and go to signed/<job>/FAILED.
trap {
    Write-Warning "the signing bootstrap failed: $_"
    $reason = ([string]$_ -split "`n")[0]
    try { Stop-Transcript | Out-Null } catch { }
    try {
        if ($gcloud -and $bucket -and (Get-Command Invoke-Gcloud -ErrorAction SilentlyContinue)) {
            $failed = Join-Path $logs ("failed-{0}-{1}.txt" -f (Get-Date -Format "yyyyMMddTHHmmss"), [guid]::NewGuid().ToString("N").Substring(0, 8))
            Set-Content -LiteralPath $failed ($reason.Substring(0, [Math]::Min(400, $reason.Length)))
            Invoke-Gcloud @("storage", "cp", "--quiet", "--if-generation-match=0", $failed, "gs://$bucket/signed/bootstrap/$(Split-Path $failed -Leaf)") | Out-Null
        }
    } catch { }
    Stop-Computer -Force
    break
}

# Raw text, never a content-sniffing reader: Invoke-RestMethod would parse a JSON or XML-looking value (the signing code's
# pins are JSON) into an object (break-it review 2, N2). The body's bytes are decoded as UTF-8 here: Windows PowerShell
# 5.1 gives .Content as a byte array for a type it does not count as text, and the metadata server answers
# application/text, so [string] of .Content read "97 110 ..." and the account check refused every boot (2026-10-22).
# A key that is not set reads as the default at once; any other failure (the server busy after a start) is tried again.
function Get-Metadata([string]$Path, [string]$Default = "") {
    for ($try = 1; $try -le 3; $try++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Headers @{ "Metadata-Flavor" = "Google" } -TimeoutSec 10 `
                -Uri "http://metadata.google.internal/computeMetadata/v1/$Path"
            return [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
        } catch {
            $status = $_.Exception.Response
            if ($status -and [int]$status.StatusCode -eq 404) { return $Default }
            Start-Sleep -Seconds 2
        }
    }
    $Default
}
# The trap above records the reason and powers off.
function Stop-Here([string]$Why) { throw $Why }

# Only the signer: the expected account, named *-signer@, and never the account the validation VMs share.
$expected = [string](Get-Metadata "instance/attributes/angelo-sign-account")
$account = [string](Get-Metadata "instance/service-accounts/default/email")
if (-not $expected -or $account -cne $expected -or $account -notmatch "^[a-z][a-z0-9-]*-signer@" -or $account.StartsWith("win-build@")) {
    Stop-Here "this VM runs as '$account', not the signing account '$expected' (*-signer@): the signing bootstrap refuses to run"
}
$bucket = [string](Get-Metadata "instance/attributes/angelo-bucket")
if (-not $bucket) { Stop-Here "metadata angelo-bucket is missing" }
$gcloud = @("C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
            "C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $gcloud) { Stop-Here "the Cloud SDK is not installed on this image" }
# Every gcloud call goes through here. gcloud writes progress to stderr, and Windows PowerShell 5.1 (the startup-script
# runner's) turns each redirected stderr line into an error record that $ErrorActionPreference = "Stop" throws: a bare
# call under Stop fails on gcloud's first line, whatever its exit code. Returns the exit code; -Check throws on nonzero.
function Invoke-Gcloud([string[]]$Arguments, [switch]$Check) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $gcloud @Arguments 2>&1 | Out-Null; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
    if ($Check -and $code -ne 0) { throw "gcloud $($Arguments[0..1] -join ' ') failed ($code)" }
    return $code
}

# A download, kept only when its SHA-256 is the pinned one.
function Get-Pinned([string]$Name) {
    $url = [string](Get-Metadata "instance/attributes/angelo-$Name-url")
    $sha = ([string](Get-Metadata "instance/attributes/angelo-$Name-sha256")).ToLowerInvariant()
    if ($url -notmatch "^(https|gs)://" -or $sha -notmatch "^[0-9a-f]{64}\z") { Stop-Here "metadata angelo-$Name-url (https or gs) and angelo-$Name-sha256 must pin $Name" }
    $file = Join-Path $root ("download-" + $Name + [IO.Path]::GetExtension($url.Split("?")[0]))
    if ($url.StartsWith("gs://")) {   # The reviewed copy kept in the signing project's own bucket.
        if ((Invoke-Gcloud @("storage", "cp", "--quiet", $url, $file)) -ne 0) { Stop-Here "could not fetch $url" }
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file
    }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $sha) { Remove-Item -LiteralPath $file -Force; Stop-Here "$url is $actual, not the pinned $sha" }
    return @{ File = $file; Url = $url; Sha256 = $sha }
}

$receipt = [ordered]@{ account = $account; started = (Get-Date -Format o) }

if (-not (Test-Path (Join-Path $state "codesigntool.done"))) {
    $zip = Get-Pinned "codesigntool"
    Remove-Item -LiteralPath $codeSignTool -Recurse -Force -ErrorAction SilentlyContinue
    $unpacked = Join-Path $root "codesigntool-unpacked"
    Remove-Item -LiteralPath $unpacked -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -LiteralPath $zip.File -DestinationPath $unpacked
    # The zip may hold the tool at its top or in one folder: the folder with jar\ is CodeSignTool's home.
    $toolHome = Get-ChildItem -LiteralPath $unpacked -Recurse -Directory -Filter jar | Select-Object -First 1
    if (-not $toolHome) { Stop-Here "the CodeSignTool zip has no jar folder" }
    Move-Item -LiteralPath $toolHome.Parent.FullName -Destination $codeSignTool
    Remove-Item -LiteralPath $unpacked, $zip.File -Recurse -Force -ErrorAction SilentlyContinue
    $jars = @(Get-ChildItem -LiteralPath (Join-Path $codeSignTool "jar") -Filter "code_sign_tool-*.jar" -File)
    if ($jars.Count -ne 1) { Stop-Here "CodeSignTool holds $($jars.Count) code_sign_tool-*.jar, not one" }
    (Get-FileHash -LiteralPath $jars[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() |
        Set-Content -LiteralPath (Join-Path $codeSignTool "JAR-SHA256.txt") -Encoding ascii
    $receipt.codesigntool = [ordered]@{ url = $zip.Url; zip_sha256 = $zip.Sha256; jar = $jars[0].Name }
    Set-Content (Join-Path $state "codesigntool.done") (Get-Date -Format o)
}

if (-not (Test-Path (Join-Path $state "signtool.done"))) {
    $sdk = Get-Pinned "winsdk"
    # Microsoft's own signature as well as the pin: the installer fetches its components from Microsoft and checks them.
    $sdkSignature = Get-AuthenticodeSignature -LiteralPath $sdk.File
    if ($sdkSignature.Status -ne "Valid" -or $sdkSignature.SignerCertificate.Subject -notmatch "^CN=Microsoft Corporation,") {
        Stop-Here "the Windows SDK installer's signature is $($sdkSignature.Status) ($($sdkSignature.SignerCertificate.Subject)), not Microsoft's"
    }
    $process = Start-Process -FilePath $sdk.File -ArgumentList "/features OptionId.SigningTools /quiet /norestart /ceip off" -Wait -PassThru
    if ($process.ExitCode -notin 0, 3010) { Stop-Here "the Windows SDK installer exited with $($process.ExitCode)" }
    Remove-Item -LiteralPath $sdk.File -Force -ErrorAction SilentlyContinue
    $receipt.winsdk = [ordered]@{ url = $sdk.Url; sha256 = $sdk.Sha256 }
    Set-Content (Join-Path $state "signtool.done") (Get-Date -Format o)
}

$signtool = Get-ChildItem (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin") -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Directory.Name -eq "x64" } | Select-Object -Last 1
$receipt.signtool = if ($signtool) { $signtool.FullName } else { $null }
# [string]: a line Get-Content returns carries its provider properties, which ConvertTo-Json writes out in full.
$receipt.jar_sha256 = [string](Get-Content -LiteralPath (Join-Path $codeSignTool "JAR-SHA256.txt") -ErrorAction SilentlyContinue | Select-Object -First 1)
$receipt.tsa_url = @(Get-Content -LiteralPath (Join-Path $codeSignTool "conf\code_sign_tool.properties") -ErrorAction SilentlyContinue |
    Where-Object { $_ -match "^\s*TSA_URL\s*=" } | ForEach-Object { ($_ -split "=", 2)[1].Trim() })
$receipt.finished = (Get-Date -Format o)
$file = Join-Path $logs ("sign-bootstrap-receipt-{0}-{1}.json" -f (Get-Date -Format "yyyyMMddTHHmmssZ").Replace(":", ""), [guid]::NewGuid().ToString("N").Substring(0, 8))
$receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $file -Encoding utf8
# A new object per boot: the signer may create objects below signed/, never replace one.
Invoke-Gcloud @("storage", "cp", "--quiet", "--if-generation-match=0", $file, "gs://$bucket/signed/bootstrap/$(Split-Path $file -Leaf)") -Check | Out-Null

# --- A signing job ---------------------------------------------------------------------------------------------
# The transcript ends before any signing: the job runs with the eSigner secrets in its process (the startup-script
# runner keeps what is printed: nothing here prints a secret, and sign_windows.ps1 prints none of CodeSignTool's output).
Stop-Transcript | Out-Null
$job = [string](Get-Metadata "instance/attributes/angelo-sign-job")
if ($job -and $job -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\z") { Write-Warning "metadata angelo-sign-job '$job' is not a job name"; $job = "" }
$claimed = $false
if ($job) {
    # The claim: created only if absent (generation 0), so a job is signed at most once, whatever restarts the VM.
    $claim = Join-Path $logs "claim.txt"
    Set-Content -LiteralPath $claim "claimed $(Get-Date -Format o)"
    $claimed = (Invoke-Gcloud @("storage", "cp", "--quiet", "--if-generation-match=0", $claim, "gs://$bucket/signed/$job/CLAIM")) -eq 0
    if (-not $claimed) { Write-Warning "job $job is claimed already (or the claim failed): not signing it" }
}
if ($claimed) {
    $work = Join-Path $root "job-$job"
    $report = Join-Path $work "sign-report.json"
    try {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $work | Out-Null
        # A file from the bucket, kept only with the SHA-256 that owner-set metadata (or the attested job) names.
        function Get-Attested([string]$Object, [string]$Sha256, [string]$Destination) {
            if ($Sha256 -cnotmatch "^[0-9a-f]{64}\z") { throw "'$Sha256' is not a SHA-256" }
            New-Item -ItemType Directory -Force (Split-Path $Destination) | Out-Null
            Invoke-Gcloud @("storage", "cp", "--quiet", "gs://$bucket/$Object", $Destination) -Check | Out-Null
            $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -cne $Sha256) { throw "gs://$bucket/$Object is $actual, not the attested $Sha256" }
        }
        # The signing code, pinned by metadata only.
        $code = [string](Get-Metadata "instance/attributes/angelo-sign-code") | ConvertFrom-Json
        $layout = Join-Path $work "src"
        $codeFiles = @("scripts/angelo/sign_windows.ps1", "scripts/angelo/windows_pe_digest.ps1",
            "scripts/addons_core/angelo_agent/agent_runtime.json", "scripts/modules/angelo_identity.json")
        foreach ($path in $codeFiles) {
            $sha = [string]$code.$path
            Get-Attested "code/$sha" $sha (Join-Path $layout ($path -replace "/", "\"))
        }
        # The job: data only, attested by metadata.
        Get-Attested "input/$job/job.json" ([string](Get-Metadata "instance/attributes/angelo-sign-job-sha256")) (Join-Path $work "job.json")
        $spec = Get-Content -LiteralPath (Join-Path $work "job.json") -Raw | ConvertFrom-Json
        if ($spec.kind -cnotin "check", "probe", "files", "msi") { throw "job $job is of kind '$($spec.kind)', not check, probe, files or msi" }
        $certificate = Join-Path $work "certificate.crt"
        Get-Attested "input/$job/$($spec.certificate)" ([string]$spec.certificate) $certificate
        $signArguments = @{ Mode = "Sign"; Backend = "Esigner"; Scope = "KeyFiles"; CertificateFile = $certificate
            EsignerHome = $codeSignTool; EsignerJarSha256 = (Get-Content -LiteralPath (Join-Path $codeSignTool "JAR-SHA256.txt") | Select-Object -First 1).Trim()
            EsignerUsername = [string]$spec.esigner.username; EsignerCredentialId = [string]$spec.esigner.credential_id
            EsignerSecretProject = [string]$spec.esigner.secret_project; GceServiceAccount = $expected; Report = $report }
        if ($signtool) { $signArguments.SignTool = $signtool.FullName }
        $signScript = Join-Path $layout "scripts\angelo\sign_windows.ps1"
        $output = Join-Path $work "output"
        New-Item -ItemType Directory -Force $output | Out-Null
        if ($spec.kind -eq "check") {
            $signArguments.Mode = "Preflight"
            & $signScript @signArguments
        } elseif ($spec.kind -eq "probe") {
            # A second claim of the same job must be refused (generation match 0, create-only rights): checked before any
            # signing, so a store that would allow it costs nothing (break-it review 2, N15).
            $second = Invoke-Gcloud @("storage", "cp", "--quiet", "--if-generation-match=0", $claim, "gs://$bucket/signed/$job/CLAIM")
            if ($second -eq 0) { throw "a second claim of job $job succeeded: claims are not exclusive here, so nothing may be signed" }
            $signArguments.Mode = "Preflight"
            & $signScript @signArguments -ProbeSignature
        } elseif ($spec.kind -eq "files") {
            # Only the key files, named by the attested angelo_identity.json and agent_runtime.json, and at most 8: a job can
            # never spend signings on anything else (break-it review 2, N8). sign_windows.ps1 also runs KeyFiles.
            $identity = (Get-Content -LiteralPath (Join-Path $layout "scripts\modules\angelo_identity.json") -Raw | ConvertFrom-Json).windows
            $pin = (Get-Content -LiteralPath (Join-Path $layout "scripts\addons_core\angelo_agent\agent_runtime.json") -Raw | ConvertFrom-Json).platforms.windows
            $allowed = @(($identity.exe_stem + ".exe"), ($identity.exe_stem + $identity.launcher_suffix + ".exe")) +
                @(@($pin.required) + @($pin.optional) | Where-Object { $_ -notin @($pin.upstream_signed) } | ForEach-Object { "$($pin.folder)/$_" })
            $names = @($spec.files.PSObject.Properties | ForEach-Object { $_.Name })
            if ($names.Count -lt 1 -or $names.Count -gt 8) { throw "job $job lists $($names.Count) files; a key-files job lists 1 to 8" }
            foreach ($name in $names) { if ($name -cnotin $allowed) { throw "job file '$name' is not a key file ($($allowed -join ', '))" } }

            $files = Join-Path $work "payload"
            foreach ($entry in $spec.files.PSObject.Properties) {
                if ($entry.Name -cnotmatch "^[A-Za-z0-9._ -]+(/[A-Za-z0-9._ -]+)*\z" -or $entry.Name -match "(^|/)\.\.?(/|$)") {
                    throw "job file '$($entry.Name)' is not a plain relative path"
                }
                Get-Attested "input/$job/$($entry.Value)" ([string]$entry.Value) (Join-Path $files ($entry.Name -replace "/", "\"))
            }
            & $signScript @signArguments -Path $files
            # Laid out as gcp_windows_validate.sh release-signing takes it: payload/<path under the app's folder>.
            Copy-Item -LiteralPath $files -Destination (Join-Path $output "payload") -Recurse
        } else {
            if ([string]$spec.msi.name -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.msi\z") { throw "job msi name '$($spec.msi.name)' is not a plain MSI file name" }
            New-Item -ItemType Directory -Force (Join-Path $output "signed-msi") | Out-Null
            $msi = Join-Path $output ("signed-msi\" + [string]$spec.msi.name)
            Get-Attested "input/$job/$($spec.msi.sha256)" ([string]$spec.msi.sha256) $msi
            & $signScript @signArguments -Path $msi
            # The signed MSI's SHA-256, from a bucket the build machine's pool cannot write: pass 2 requires it.
            (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -LiteralPath (Join-Path $output "signed-msi.sha256") -Encoding ascii
        }
        Copy-Item -LiteralPath $report -Destination $output
        Invoke-Gcloud @("storage", "cp", "--quiet", "--recursive", "$output\*", "gs://$bucket/signed/$job/") -Check | Out-Null
        Set-Content -LiteralPath (Join-Path $work "DONE") (Get-Date -Format o)
        Invoke-Gcloud @("storage", "cp", "--quiet", (Join-Path $work "DONE"), "gs://$bucket/signed/$job/DONE") -Check | Out-Null
    } catch {
        # The reason's first line only (sign_windows.ps1's messages never carry CodeSignTool's output or a secret), then
        # where it was thrown: the exception's type and the script stack (function names, files and line numbers, never
        # a value). A message alone ("Argument types do not match", the first probe) did not say where.
        $failure = Join-Path $logs "sign-job-failed.txt"
        $reason = ([string]$_.Exception.Message -split "`n")[0]
        $where = @(([string]$_.ScriptStackTrace -split "`n") | Select-Object -First 6 | ForEach-Object { $_.Trim() }) -join " < "
        Set-Content -LiteralPath $failure @(("job ${job}: " + $reason.Substring(0, [Math]::Min(400, $reason.Length))),
            ("type: " + $_.Exception.GetType().FullName), ("where: " + $where.Substring(0, [Math]::Min(800, $where.Length))))
        Invoke-Gcloud @("storage", "cp", "--quiet", $failure, "gs://$bucket/signed/$job/FAILED") | Out-Null
    } finally {
        # The ledger of every eSigner call, failures included: what the job may have cost.
        if (Test-Path -LiteralPath "$report.ledger.json") {
            Invoke-Gcloud @("storage", "cp", "--quiet", "$report.ledger.json", "gs://$bucket/signed/$job/ledger.json") | Out-Null
        }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Stop-Computer -Force
