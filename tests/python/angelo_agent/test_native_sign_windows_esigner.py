# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""sign_windows.ps1's Esigner backend: SSL.com eSigner's CodeSignTool on the signing VM, secrets in memory only.

Read on any host (the script runs on Windows). The checks pin what the design rests on: the VM's own account is
checked before any secret is fetched; the secrets come from Secret Manager, are taken out of everything CodeSignTool
prints, are cleared when the run ends and are refused on a machine that logs command lines; CodeSignTool runs as its
pinned jar under its bundled java.exe, never through cmd.exe; a returned copy replaces its original only with an
equal Authenticode digest. The argument quoting's round trip is test_native_sign_windows_quoting.py.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_native_sign_windows import SIGN, TOOLS, block, ps_structure_problems, read

SIGN_BOOTSTRAP = TOOLS / "windows_sign_bootstrap.ps1"
CLOUD_BOOTSTRAP = TOOLS / "windows_cloud_bootstrap.ps1"
PE_DIGEST = TOOLS / "windows_pe_digest.ps1"
MSI_COMPARE = TOOLS / "windows_msi_compare.ps1"
BUILD = TOOLS / "build_release_windows.ps1"
REMOTE = TOOLS / "sign_remote.sh"
INPUTS = TOOLS / "windows_release_inputs.ps1"
RELEASE_STAGE = TOOLS / "windows_release_validate.ps1"
DRIVER = TOOLS / "gcp_windows_validate.sh"


class EsignerTests(unittest.TestCase):
    def setUp(self):
        self.text = read(SIGN)

    def test_the_vm_account_is_checked_before_any_secret_and_logging_is_refused(self):
        setup = block(self.text, 'if ($Backend -eq "Esigner" -and $Mode -in "Preflight", "Sign") {', "\n    }\n")
        main = self.text[self.text.index("Assert-SignToolFor $tool; Assert-SignerAccount"):]
        self.assertLess(main.index("Assert-SignerAccount"), main.index("Read-EsignerSecrets"))
        self.assertLess(setup.index("Assert-NoCommandLineLogging"), setup.index("Read-EsignerSecrets"))
        account = block(self.text, "function Assert-SignerAccount", "\n}\n")
        self.assertIn('$Backend -eq "Esigner"', account)
        logging = block(self.text, "function Assert-NoCommandLineLogging", "\n}\n")
        self.assertIn("ProcessCreationIncludeCmdLine_Enabled", logging)
        self.assertIn("EnableModuleLogging", logging)

    def test_secrets_come_from_secret_manager_never_through_a_proxy_and_never_stay(self):
        secret = block(self.text, "function Get-EsignerSecret(", "\n}\n")
        self.assertIn("https://secretmanager.googleapis.com/v1/projects/$EsignerSecretProject/secrets/$Name/versions/latest:access",
                      secret)
        self.assertIn("$request.Proxy = $null", secret)
        self.assertIn('$script:EsignerSecrets = $null', block(self.text, "} finally {\n    Remove-Item -LiteralPath $ArtifactSigningMetadata",
                                                                 "\n}\n"))
        invoke = block(self.text, "function Invoke-CodeSignTool", "\n}\n")
        self.assertIn("Output = (Hide-EsignerSecrets ($output + $errors.Result))", invoke)
        # Nothing reports or prints them: the report names the account, the credential id and the project only.
        for line in self.text.splitlines():
            if "EsignerSecrets" in line:
                self.assertNotRegex(line, r"Write-(Host|Output|Warning|Verbose)|\$result\.", line)

    def test_codesigntool_runs_as_its_pinned_jar_never_through_cmd(self):
        invoke = block(self.text, "function Invoke-CodeSignTool", "\n}\n")
        self.assertIn("$start.FileName = $script:EsignerJava", invoke)
        self.assertIn('@("-jar", $script:EsignerJar', invoke)
        self.assertIn("$start.UseShellExecute = $false", invoke)
        self.assertNotIn("CodeSignTool.bat", self.text.replace("never through\n  its .bat", ""))
        inputs = block(self.text, "function Assert-EsignerInputs", "\n}\n")
        self.assertIn("is $sha, not the pinned CodeSignTool jar $EsignerJarSha256", inputs)
        self.assertIn("names TSA_URL", inputs)
        self.assertIn('"Esigner" { "http://ts.ssl.com" }', self.text)

    def test_a_returned_copy_replaces_its_original_only_with_an_equal_digest(self):
        batch = block(self.text, "function Invoke-EsignerBatch", "\n}\n")
        order = [batch.index(marker) for marker in (
            "Get-AuthenticodePeDigest $Files[$i].FullName", 'Invoke-CodeSignTool "batch_sign"',
            "$after = Get-AuthenticodePeDigest $item.Signed", "if ($item.Digest -cne $after) { throw",
            "Copy-Item -LiteralPath $item.Signed -Destination $item.File.FullName -Force")]
        self.assertEqual(order, sorted(order), "every input is digested before anything is spent")
        self.assertIn('$pending = @($staged | Where-Object { -not $_.Signed })', batch, "a retry sends only the missing")
        self.assertIn("if ($attempt -ge 2) {", batch, "one retry at most: each may count against the quota")
        self.assertNotIn("$run.Output", batch, "CodeSignTool's output never leaves the process")
        self.assertIn('. (Join-Path $PSScriptRoot "windows_pe_digest.ps1")', self.text)
        self.assertIn('if ($entry.command -eq "batch_sign") { $result.esigner_signings += [int]$entry.files }', self.text)
        self.assertIn('"$Report.ledger.json"', self.text, "the ledger is written whether the run succeeds or not")
        invoke = block(self.text, "function Invoke-CodeSignTool", "\n}\n")
        start = invoke.index("[System.Diagnostics.Process]::Start(")
        self.assertLess(invoke.index("$script:EsignerLedger.Add($entry)"), start, "a call that throws after the start is still counted")
        self.assertLess(invoke.index("Save-EsignerLedger"), start, "and is on disk before it starts")
        self.assertIn("files = $Files", invoke, "the files a call may charge, recorded before it starts")
        # PowerShell 5.1: @() of a generic List throws "Argument types do not match" (both first probes, after spending).
        self.assertNotIn("@($script:EsignerLedger)", self.text)
        self.assertIn("ConvertTo-Json -InputObject $script:EsignerLedger.ToArray()", block(self.text, "function Save-EsignerLedger", "\n}\n"))
        self.assertIn("try { Save-EsignerLedger } catch {", self.text, "the final write never replaces the run's own error")

    def test_the_jvm_keeps_no_trace_of_the_arguments(self):
        invoke = block(self.text, "function Invoke-CodeSignTool", "\n}\n")
        for needed in ('"-XX:-UsePerfData"', "-XX:ErrorFile=", "-Djava.io.tmpdir=$script:EsignerPrivate",
                       '"JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "CLASSPATH"', "$script:EsignerLedger.Add("):
            self.assertIn(needed, invoke)
        self.assertIn('-cnotmatch "^[\\x20-\\x7E]+\\z"', block(self.text, "function Read-EsignerSecrets", "\n}\n"))
        self.assertIn("Remove-Item -LiteralPath $script:EsignerPrivate -Recurse -Force", self.text)
        quote = block(self.text, "function ConvertTo-ProcessArgument", "\n}\n")
        self.assertNotIn("return $Value", quote, "every argument is quoted (java expands an unquoted * or ?)")

    def test_signatures_are_ours_by_the_one_certificate(self):
        self.assertIn("$script:EsignerCertificate = $certificate", self.text)
        ours = block(self.text, "function Test-Ours", "\n}\n")
        self.assertIn("return $Signature.SignerCertificate.Thumbprint -eq $Certificate.Thumbprint", ours)
        self.assertIn("[ValidateRange(1, 100)][int]$BatchSize", self.text)


class ScopeTests(unittest.TestCase):
    """-Scope KeyFiles: only the app's, the launcher's and the agent runtime's own executables, and the MSI."""

    def test_key_files_are_named_by_the_identity_and_the_runtime_pin(self):
        text = read(SIGN)
        self.assertIn('$KeyFiles = @(($WindowsIdentity.exe_stem + ".exe"), ($WindowsIdentity.exe_stem + $WindowsIdentity.launcher_suffix + ".exe")) + $RuntimeOwn', text)
        self.assertIn('$File.Extension.ToLowerInvariant() -eq ".msi"', block(text, "function Test-KeyFile", "}\n"))
        loop = text[text.index("if (-not (Test-KeyFile $file)) {   # -Scope KeyFiles"):]
        order = [loop.index(marker) for marker in (
            'if ($Backend -eq "Esigner") { throw "$($file.FullName) is not a key file',
            'if ($decision.StartsWith("resign:")) { throw', 'if ($decision -eq "sign") { $result.unsigned_kept += $file.Name; continue }',
            'if ($decision -eq "sign") { $pending.Add($file) }')]
        self.assertEqual(order, sorted(order))
        # The scope follows the flow: a per-signature or placing backend never signs everything (review 2, N3).
        self.assertIn('if (-not $Scope) { $Scope = if ($Backend -in "Esigner", "Presigned") { "KeyFiles" } else { "All" } }', text)
        self.assertIn('if ($Backend -in "Esigner", "Presigned" -and $Scope -ne "KeyFiles") { throw', text)
        # Key files are always ours, whatever the scope (review 2, N6).
        self.assertIn("if ($File.Name -in $KeyFiles) {   # Key files are always Angelo's", text)
        self.assertIn("if ($File.Name -in $KeyFiles -and -not $ours) {", text)

    def test_verification_lets_only_other_files_stay_unsigned_and_requires_every_key_file(self):
        text = read(SIGN)
        kept = block(text, "function Test-UnsignedKept", "\n}\n")
        self.assertIn("if (Test-KeyFile $File) { return $false }", kept)
        self.assertIn('-eq "NotSigned"', kept)
        self.assertEqual(text.count("if (Test-UnsignedKept $file) { $result.unsigned_kept += $file.Name; continue }"), 2)
        self.assertIn('throw "the MSI holds no $($missing -join', text)
        self.assertIn('if ($Scope -cnotin "All", "KeyFiles") { throw', text)


class PresignedTests(unittest.TestCase):
    """The build machine places the signing VM's copies: only identical files with our timestamped signature."""

    def test_a_copy_is_placed_only_when_it_is_the_same_file_with_our_signature(self):
        text = read(SIGN)
        place = block(text, "function Invoke-PresignedPlacement", "\n}\n")
        order = [place.index(marker) for marker in (
            "the signing VM signs the MSI itself", 'throw "the signing VM returned no signed copy of $relative"',
            "Copy-Item -LiteralPath $copy -Destination $private",
            "$signature = Get-AuthenticodeSignature -LiteralPath $private",
            "$signed = Get-AuthenticodePeDigest $private", "if ($staged -cne $signed) { throw",
            "Move-Item -LiteralPath $private -Destination $file.FullName -Force", 'throw "$relative changed while it was placed"')]
        self.assertEqual(order, sorted(order), "the bytes checked are the bytes placed")
        self.assertIn("is outside $root, where the signed copies are laid out", place)
        root = block(text, "function Get-AppRoot", "\n}\n")
        self.assertIn("if ($found.Count -ne 1)", root)
        self.assertIn("foreach ($file in @($pending.ToArray())) { [void](Assert-Signed $file $certificate $tool $false)", text)
        self.assertIn('if ($Backend -ne "Presigned" -and $SignedFrom) { throw', text)
        self.assertIn('if ($Backend -in "KmsKey", "Presigned" -and $GceServiceAccount) {', text)

    def test_the_certificate_is_pinned_in_the_repository(self):
        text = read(SIGN)
        self.assertIn("if ($certificate.Thumbprint -notin @($WindowsIdentity.authenticode_certificates)) {", text)
        identity = json.loads((TOOLS.parent / "modules/angelo_identity.json").read_text(encoding="utf-8"))
        pins = identity["windows"]["authenticode_certificates"]
        self.assertTrue(pins and all(re.fullmatch(r"[0-9A-F]{40}", pin) for pin in pins), pins)


class RemoteReleaseTests(unittest.TestCase):
    """build_release_windows.ps1 with a remote signer: pack with the signed files, stop; then take the signed MSI."""

    def test_the_first_pass_stops_before_any_receipt_or_record(self):
        text = read(BUILD)
        packed = block(text, "if ($Package -and -not $SignedMsi) {", "\n}\n")
        stop = packed.index("if ($presigned) {")
        self.assertLess(packed.index("CPACK_PRE_BUILD_SCRIPTS=$hook"), stop)
        self.assertLess(stop, packed.index("& $signScript -Mode Sign -Path $msi.FullName"), "the MSI is never signed here")
        self.assertIn("exit 0", packed[stop:packed.index("& $signScript -Mode Sign -Path $msi.FullName")])
        self.assertNotIn("msi-signing-record", packed, "the record is written only after the signed MSI is checked")
        self.assertIn('$presigned = $Sign -and $env:ANGELO_SIGN_BACKEND -eq "Presigned"', text)
        self.assertIn("if ($SignedMsi -and -not ($presigned -and (Test-Path -LiteralPath $SignedMsi -PathType Leaf))) { throw", text)

    def test_the_second_pass_takes_the_signed_msi_only_if_it_installs_the_same(self):
        text = read(BUILD)
        second = block(text, "if ($Package -and $SignedMsi) {", "\n}\n")
        self.assertLess(second.index('. (Join-Path $SourceDir "scripts\\angelo\\windows_msi_compare.ps1")'),
                        second.index("$manifest.signing = Use-SignedMsi $msi.FullName"))

        self.assertNotIn("cpack", second, "nothing is packed again: WiX writes new bytes each time")
        # Then the same gates as every release: payload scan, publisher, builder paths, tracked scripts, the record.
        tail = text[text.index(second) + len(second):]
        for gate in ("authenticode-publisher --file $msi.FullName", "builder-paths $payload", "tracked-scripts",
                     "msi-signing-record --installer $msi.FullName --signing $signing"):
            self.assertIn(gate, tail)

    def test_the_second_pass_takes_only_the_first_pass_msi_from_the_same_commit(self):
        compare = read(MSI_COMPARE)
        save = block(compare, "function Save-PackedState", "\n}\n")
        self.assertIn('"angelo-presigned-pass1.json"', save)
        self.assertIn('".awaiting-signature.msi"', save, "never under the release's own name")
        check = block(compare, "function Assert-PackedState", "\n}\n")
        self.assertIn("if ($state.commit -cne $Commit -or $state.msi -cne (Split-Path $Msi -Leaf) -or $state.sha256 -cne $sha) {", check)
        use = block(compare, "function Use-SignedMsi", "\n}\n")
        self.assertLess(use.index("Assert-PackedState $BuildDir $Commit $Packed"), use.index("msi-same --packed"))
        self.assertIn("$releaseChecks $BuildDir $commit; $msi.Refresh()", read(BUILD))

    def test_signing_is_asked_for_per_run_never_inferred_from_the_bucket(self):
        inputs = read(INPUTS)
        part = inputs[inputs.index("# Only when this run asks for it"):]
        self.assertLess(part.index('$signMode = Get-Meta "angelo-release-sign" ""'), part.index("storage ls"))
        self.assertIn('$signingSums = if ($signMode) {', part)
        self.assertIn("but gs://$bucket/release-signing/ has no SHA256SUMS", part)
        driver = read(DRIVER)
        self.assertIn('""|presigned) RELEASE_SIGN="${ANGELO_GCP_RELEASE_SIGN:-}" ;;', driver)
        self.assertIn(",angelo-release-sign=$RELEASE_SIGN", driver, "written on every send, empty when unset (review 2, N4)")

    def test_the_signed_msi_is_read_as_data_before_anything_of_it_runs(self):
        use = block(read(MSI_COMPARE), "function Use-SignedMsi", "\n}\n")
        order = [use.index(marker) for marker in (
            "msi-same --packed $Packed --signed $Signed", "if ($LASTEXITCODE -ne 0) { throw",
            "Copy-Item -LiteralPath $Signed -Destination $Packed -Force", "& $SignScript -Mode VerifyMsi -Path $Packed")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("msiexec", read(MSI_COMPARE), "an MSI not yet proven ours is never run")
        self.assertIn("$bundledPython.FullName $releaseChecks", read(BUILD))


class ReleaseStageTests(unittest.TestCase):
    """build4's release stage gets the signing VM's files from its bucket, every one summed, and signs with them."""

    def test_every_signing_input_is_summed_and_nothing_else_admitted(self):
        text = read(INPUTS)
        part = text[text.index("# A remote-signed release"):]
        self.assertIn('if ($have -ne $digest.ToLowerInvariant()) { throw "release signing:', part)
        self.assertIn("outside its folder", part)
        self.assertIn("files SHA256SUMS does not name", part)
        self.assertLess(part.index("files SHA256SUMS does not name"), part.index("$env:ANGELO_RELEASE_SIGNING_DIR = $signingDir"))

    def test_the_release_stage_signs_presigned_with_them(self):
        text = read(RELEASE_STAGE)
        self.assertIn("[string]$ReleaseSigningDir = $env:ANGELO_RELEASE_SIGNING_DIR", text)
        signing = block(text, "if ($ReleaseSigningDir) {", "\n    }\n")
        for needed in ('$env:ANGELO_SIGN_BACKEND = "Presigned"', '$env:ANGELO_SIGN_SIGNED_FROM = Join-Path $ReleaseSigningDir "payload"',
                       '-Sign -SignCertificate', '-SignedMsi', 'the second pass takes one'):
            self.assertIn(needed, signing)

    def test_the_second_pass_takes_only_the_msi_the_signing_vm_recorded(self):
        stage = read(RELEASE_STAGE)
        self.assertIn("not the signing VM's recorded", stage)
        self.assertLess(stage.index("$have -cne $want"), stage.index('$allow += " -SignedMsi'))
        self.assertIn('Get-Meta "angelo-release-signed-msi-sha256"', read(INPUTS))
        self.assertIn(",angelo-release-signed-msi-sha256=$SIGNED_MSI_SHA256", read(DRIVER), "written on every send")

    def test_the_driver_uploads_them_with_sums(self):
        text = read(DRIVER)
        upload = block(text, "release_signing() {", "\n}\n")
        self.assertIn('shasum -a 256 -- "$f"', upload)
        self.assertIn('must hold exactly one .msi', upload)
        self.assertIn('release-signing) release_signing "$@" ;;', text)


class SignRemoteTests(unittest.TestCase):
    """sign_remote.sh, the release owner's side of a job: a dry run sends nothing and shows every step."""

    def run_remote(self, kind, job, with_files=True, with_msi=True, **env):
        with tempfile.TemporaryDirectory() as folder:
            files = Path(folder, "key")
            (files / "agent-runtime").mkdir(parents=True)
            (files / "Angelo.exe").write_bytes(b"MZ app")
            (files / "agent-runtime" / "codex.exe").write_bytes(b"MZ codex")
            msi = Path(folder, "Angelo-1.0.4-windows-x64.msi")
            msi.write_bytes(b"msi bytes")
            certificate = Path(folder, "codesign.crt")
            certificate.write_bytes(b"certificate")
            environment = dict(os.environ, ANGELO_SIGN_DRY_RUN="1", ANGELO_SIGN_ESIGNER_USERNAME="someone",
                               ANGELO_SIGN_ESIGNER_CREDENTIAL_ID="85e483cd-7516-4dde-a82e-953cde69fdf6", **env)
            args = ["bash", str(REMOTE), kind, "--job", job, "--certificate", str(certificate), "--out", str(Path(folder, "out"))]
            args += ["--files", str(files)] if with_files else []
            args += ["--msi", str(msi)] if with_msi else []
            return subprocess.run(args, capture_output=True, text=True, env=environment)

    def test_a_files_job_sends_the_key_files_and_attests_code_and_job_in_metadata(self):
        done = self.run_remote("files", "angelo-104-keys", with_msi=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        job = json.loads(lines[0])
        self.assertEqual((job["kind"], job["files"]), ("files", ["Angelo.exe", "agent-runtime/codex.exe"]))
        steps = [line for line in lines if line.startswith("DRY RUN:")]
        self.assertEqual(len(steps), 4)
        self.assertIn("gs://angelo-signing-win-sign/code/", steps[0])
        self.assertIn("gs://angelo-signing-win-sign/input/angelo-104-keys/", steps[1])
        self.assertIn("angelo-sign-job=angelo-104-keys", steps[2])
        self.assertRegex(steps[2], r"angelo-sign-job-sha256=[0-9a-f]{64}")
        self.assertIn("angelo-sign-code=", steps[2])
        self.assertIn("instances start win-sign", steps[3])

    def test_a_probe_and_an_msi_job(self):
        check = self.run_remote("check", "first-check", with_files=False, with_msi=False)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout.splitlines()[0])["kind"], "check")
        probe = self.run_remote("probe", "first-probe", with_files=False, with_msi=False)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(json.loads(probe.stdout.splitlines()[0])["kind"], "probe")
        msi = self.run_remote("msi", "angelo-104-msi", with_files=False)
        self.assertEqual(msi.returncode, 0, msi.stderr)
        self.assertEqual(json.loads(msi.stdout.splitlines()[0])["msi"], "Angelo-1.0.4-windows-x64.msi")

    def test_a_files_job_takes_only_key_files_and_a_fresh_out(self):
        with tempfile.TemporaryDirectory() as folder:
            files = Path(folder, "key")
            files.mkdir()
            (files / "python313.dll").write_bytes(b"MZ not a key file")
            certificate = Path(folder, "c.crt")
            certificate.write_bytes(b"c")
            environment = dict(os.environ, ANGELO_SIGN_DRY_RUN="1", ANGELO_SIGN_ESIGNER_USERNAME="u",
                               ANGELO_SIGN_ESIGNER_CREDENTIAL_ID="85e483cd-7516-4dde-a82e-953cde69fdf6")
            done = subprocess.run(["bash", str(REMOTE), "files", "--job", "j", "--certificate", str(certificate),
                                   "--out", str(Path(folder, "out")), "--files", str(files)], capture_output=True, text=True, env=environment)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("is not a key file", done.stderr)
            used = Path(folder, "used")
            used.mkdir()
            (used / "old").write_text("x")
            done = subprocess.run(["bash", str(REMOTE), "probe", "--job", "j", "--certificate", str(certificate),
                                   "--out", str(used)], capture_output=True, text=True, env=environment)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("must be new or empty", done.stderr)

    def test_refusals(self):
        for kind, job, files, msi in (("sign", "x", True, True), ("msi", "../escape", False, True), ("files", "", True, False),
                                      ("files", "no-files", False, False), ("msi", "no-msi", False, False)):
            with self.subTest(kind=kind, job=job):
                self.assertNotEqual(self.run_remote(kind, job, files, msi).returncode, 0)

    def test_the_job_never_runs_twice_and_the_vm_is_waited_for(self):
        text = read(REMOTE)
        self.assertIn("one job at a time", text)
        self.assertIn("was used before: name a new one", text)
        self.assertIn("--keys=angelo-sign-job,angelo-sign-job-sha256", text)
        self.assertLess(text.index('until [[ "$(status)" == TERMINATED ]]'), text.index("remove-metadata"))


class BootstrapTests(unittest.TestCase):
    def test_the_signing_vm_runs_only_as_the_signer_and_installs_only_pinned_tools(self):
        text = read(SIGN_BOOTSTRAP)
        order = [text.index(marker) for marker in (
            '$account -cne $expected -or $account -notmatch "^[a-z][a-z0-9-]*-signer@"',
            '$account.StartsWith("win-build@")', 'function Get-Pinned', '$zip = Get-Pinned "codesigntool"',
            '$sdk = Get-Pinned "winsdk"', "# --- A signing job", "Stop-Transcript | Out-Null\n$job = ")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(text.rstrip().endswith("Stop-Computer -Force"), "the VM powers off after every boot")
        pinned = block(text, "function Get-Pinned", "\n}\n")
        self.assertIn("if ($actual -cne $sha) {", pinned)
        self.assertIn('$url -notmatch "^(https|gs)://"', pinned)
        self.assertIn('-notmatch "^CN=Microsoft Corporation,"', text, "the SDK installer must carry Microsoft's signature")
        self.assertIn("JAR-SHA256.txt", text)
        self.assertIn('$receipt.jar_sha256 = [string](Get-Content', text, "the hash, not a PowerShell object (368 KB)")
        self.assertNotRegex(text, r"secretmanager|sslcom-|password|totp", "the bootstrap never touches a secret")
        self.assertIn('"gs://$bucket/signed/bootstrap/', text, "the one prefix the signer may write")

    def test_a_job_is_claimed_once_runs_only_attested_code_and_signs_no_msi_it_runs(self):
        text = read(SIGN_BOOTSTRAP)
        job = text[text.index("# --- A signing job"):]
        order = [job.index(marker) for marker in (
            "Stop-Transcript | Out-Null", '"--if-generation-match=0", $claim', "if ($claimed) {",
            'Get-Metadata "instance/attributes/angelo-sign-code"', 'Get-Metadata "instance/attributes/angelo-sign-job-sha256"',
            "& $signScript @signArguments")]
        self.assertEqual(order, sorted(order), "claimed before anything is fetched or spent; code from metadata")
        attested = block(job, "function Get-Attested", "\n        }\n")
        self.assertIn('if ($actual -cne $Sha256) { throw', attested)
        self.assertNotIn("msiexec", job, "no MSI is run on the signing VM")
        self.assertIn("is not a plain relative path", job)
        self.assertIn('if ($spec.kind -cnotin "check", "probe", "files", "msi") { throw', job)
        check = job[job.index('if ($spec.kind -eq "check") {'):job.index('} elseif ($spec.kind -eq "probe") {')]
        self.assertNotIn("ProbeSignature", check, "a check never signs")
        self.assertIn('$signArguments.Mode = "Preflight"', check)
        self.assertIn("GceServiceAccount = $expected", job, "the signer is the metadata's account, not whatever runs")
        self.assertIn('"gs://$bucket/signed/$job/ledger.json"', job)
        self.assertIn('("where: " + $where.Substring(', job, "a failure says where it was thrown")
        self.assertIn("$_.ScriptStackTrace", job)
        for line in job.splitlines():   # Inputs and code are only read; every copy to the bucket goes below signed/.
            if '"gs://$bucket/' in line and ' cp ' in line and "input/" not in line and "code/" not in line:
                self.assertIn("/signed/", line)

    def test_review_2_the_vm_always_powers_off_reads_metadata_raw_and_signs_key_files_only(self):
        text = read(SIGN_BOOTSTRAP)
        trap = text[text.index("trap {"):]
        self.assertLess(trap.index("Stop-Computer -Force"), trap.index("break"), "N1: any failure powers off")
        self.assertLess(text.index("trap {"), text.index('Get-Metadata "instance/attributes/angelo-sign-account"'))
        meta = block(text, "function Get-Metadata", "\n}\n")
        self.assertIn("Invoke-WebRequest -UseBasicParsing", meta)
        self.assertNotIn("Invoke-RestMethod", meta, "N2: never a content-sniffing reader")
        self.assertIn("[Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())", meta,
                      "the body's bytes as UTF-8: PowerShell 5.1's .Content is a byte array for application/text")
        self.assertNotIn(".Content", meta.replace("RawContentStream", ""))
        self.assertIn('"--if-generation-match=0", $file, "gs://$bucket/signed/bootstrap/$(Split-Path $file -Leaf)"', text,
                      "N1: a new receipt per boot, never an overwrite")
        files = text[text.index('} elseif ($spec.kind -eq "files") {'):]
        self.assertIn("a key-files job lists 1 to 8", files)
        self.assertIn("is not a key file", files)
        self.assertIn('Scope = "KeyFiles"', text)
        self.assertIn('Join-Path $output "payload"', text, "N12: laid out as release-signing takes it")
        self.assertIn('"signed-msi.sha256"', text)
        probe = text[text.index('if ($spec.kind -eq "probe") {'):]
        self.assertLess(probe.index("a second claim of job $job succeeded"), probe.index('$signArguments.Mode = "Preflight"'),
                        "N15: the claim is proven exclusive before anything is signed")

    def test_every_gcloud_call_survives_windows_powershell_and_a_failure_leaves_its_reason(self):
        # The startup-script runner is Windows PowerShell 5.1: under "Stop", a redirected stderr line (gcloud's progress)
        # throws. The first bare call (Get-Pinned's) powered the VM off at every boot with no receipt (2026-10-22).
        text = read(SIGN_BOOTSTRAP)
        helper = block(text, "function Invoke-Gcloud", "\n}\n")
        self.assertLess(helper.index('$ErrorActionPreference = "Continue"'), helper.index("& $gcloud @Arguments 2>&1"))
        self.assertIn("finally { $ErrorActionPreference = $old }", helper)
        calls = [line for line in text.splitlines() if re.search(r"& \$gcloud\b", line)]
        self.assertEqual(len(calls), 1, "every gcloud call goes through Invoke-Gcloud: %s" % calls)
        self.assertLess(text.index("function Invoke-Gcloud"), text.index("function Get-Pinned"))
        trap = block(text, "trap {", "\n}\n")
        self.assertLess(trap.index('"gs://$bucket/signed/bootstrap/$(Split-Path $failed -Leaf)"'), trap.index("Stop-Computer -Force"),
                        "the reason goes up before the VM powers off")
        self.assertIn('"--if-generation-match=0", $failed', trap, "a new object, never an overwrite")
        self.assertIn("function Stop-Here([string]$Why) { throw $Why }", text, "every refusal leaves through the trap")

    def test_a_validation_vm_refuses_a_signing_account(self):
        text = read(CLOUD_BOOTSTRAP)
        refusal = text.index('if ($account -match "^[^@]*-signer@") {')
        self.assertLess(refusal, text.index('$bucket = Get-Meta "angelo-bucket"'))
        self.assertLess(refusal, text.index("function Run("), "before anything is installed or run")

    def test_the_new_scripts_parse(self):
        pwsh = shutil.which("pwsh")
        for script in (SIGN_BOOTSTRAP, PE_DIGEST, CLOUD_BOOTSTRAP, MSI_COMPARE, BUILD, SIGN):
            with self.subTest(script=script.name):
                self.assertEqual(ps_structure_problems(read(script)), [])
                if pwsh:
                    command = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('%s', "
                               "[ref]$null, [ref]$e); $e | ForEach-Object { $_.ToString() }" % script)
                    done = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True)
                    self.assertEqual((done.returncode, done.stdout.strip()), (0, ""), done.stderr)


if __name__ == "__main__":
    unittest.main()
