# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Authenticode signing of the Windows release (scripts/angelo/sign_windows.ps1 and its two callers).

Dependency-free. Real signing needs Windows, signtool, the Cloud KMS CNG provider and the CA's certificate, so
it is checked by contract here and run by whoever signs a release (doc/angelo/releasing.rst). Covered: the signtool
command (SHA-256, RFC 3161 timestamp, the KMS provider and key, never a key file or password), verification
with ``/pa``, the key-name rule, the refusals (a .pfx, a non-code-signing or expired certificate, a changed
file), the build script's order (preflight and update-key check before compiling; staged files signed inside
cpack; the MSI signed and verified file by file), and the CPack hook, run for real through cpack with a
stand-in signer: the packed files are the signed ones, and a failing signer or a staging folder without the
executable stops cpack. The agent runtime (``agent_runtime.json``, platforms.windows): OpenAI's unchanged
executables keep OpenAI's signature and are never signed, codex.exe always gets Angelo's, the MSI holds exactly
the pinned files, and the validation machine fetches, checks and runs the runtime it bundles
(``gcp_windows_validate.sh agent-runtime`` driven with a stand-in gcloud). The scripts are parsed by PowerShell
when it is on PATH (GitHub's Ubuntu runners have it, and then the dry run's plan is checked too), otherwise by a
structural check.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "scripts/angelo"
SIGN = TOOLS / "sign_windows.ps1"
BUILD = TOOLS / "build_release_windows.ps1"
HOOK = TOOLS / "windows_sign_staging.cmake"
RUNTIME_CHECK = TOOLS / "windows_agent_runtime_check.ps1"
RELEASE_STAGE = TOOLS / "windows_release_validate.ps1"
BOOTSTRAP = TOOLS / "windows_cloud_bootstrap.ps1"
TOOLCHAIN = TOOLS / "windows_release_toolchain.ps1"   # dot-sourced by BUILD
GCP = TOOLS / "gcp_windows_validate.sh"
GCP_SOURCE = TOOLS / "gcp_windows_source.sh"   # what the launcher sends
RELEASE_INPUTS = TOOLS / "windows_release_inputs.ps1"   # dot-sourced by the bootstrap for a release run
PIN = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))
WINDOWS = PIN["platforms"]["windows"]
KEY = "projects/example-project/locations/global/keyRings/example-keyring/cryptoKeys/authenticode/cryptoKeyVersions/1"


def read(path):
    return path.read_text(encoding="utf-8")


def block(text, start, end):
    """The text from ``start`` up to (not including) ``end``, both required."""
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def ps_structure_problems(text):
    """Unbalanced brackets outside strings, comments and here-strings: what a hand edit most often breaks."""
    stack, i, line = [], 0, 1
    pairs = {"(": ")", "{": "}", "[": "]"}
    while i < len(text):
        c, two = text[i], text[i:i + 2]
        if c == "\n":
            line += 1
        if two in ('@"', "@'") and (i + 2 < len(text) and text[i + 2] == "\n"):
            end = text.find("\n" + two[1] + "@", i + 2)
            if end < 0:
                return ["unterminated here-string at line %d" % line]
            line += text.count("\n", i, end + 3)
            i = end + 3
            continue
        if two == "<#":
            end = text.find("#>", i)
            line += text.count("\n", i, end)
            i = end + 2
            continue
        if c == "#":
            i = text.find("\n", i)
            continue
        if c in "'\"":
            j = i + 1
            while j < len(text):
                if c == '"' and text[j] == "`":
                    j += 2
                    continue
                if text[j] == c:
                    if text[j + 1:j + 2] == c:   # A doubled quote is an escaped quote.
                        j += 2
                        continue
                    break
                j += 1
            line += text.count("\n", i, j)
            i = j + 1
            continue
        if c == "`":
            i += 2
            continue
        if c in pairs:
            stack.append((pairs[c], line))
        elif c in pairs.values():
            if not stack or stack[-1][0] != c:
                return ["unexpected %r at line %d" % (c, line)]
            stack.pop()
        i += 1
    return ["unclosed bracket opened at line %d" % stack[-1][1]] if stack else []


class SignScriptTests(unittest.TestCase):
    def test_the_signtool_command_is_sha256_rfc3161_and_the_kms_provider(self):
        text = read(SIGN)
        lists = re.findall(r'\$arguments = @\((.*?)\)', block(text, "function Get-SignArguments", "\n}\n"), re.S)
        self.assertEqual(len(lists), 2, "one command per backend")
        artifact, arguments = lists
        for part in ('"sign"', '"/fd", "sha256"', '"/tr", $TimestampUrl', '"/td", "sha256"', '"/f", $CertificateFile',
                     '"/csp", $Provider', '"/kc", $KmsKey'):
            self.assertIn(part, arguments)
        for part in ('"sign"', '"/fd", "sha256"', '"/tr", $TimestampUrl', '"/td", "sha256"', '"/dlib", $ArtifactSigningDlib',
                     '"/dmdf", $ArtifactSigningMetadata'):
            self.assertIn(part, artifact)
        self.assertNotRegex(artifact, r'"/t"|"/p"|"/a"|"/sha1"|"/f"|"/csp"')
        self.assertIn('$Provider = "Google Cloud KMS Provider"', text)
        self.assertNotRegex(arguments, r'"/t"|"/p"|"/a"|"/sha1"', "no legacy timestamp, key password or store pick")
        self.assertIn('@("verify", "/pa", "/q", $File.FullName)', text)
        self.assertIn("TimeStamperCertificate", text, "every signature must carry a timestamp")

    def test_it_never_handles_a_secret(self):
        text = read(SIGN) + read(BUILD) + read(TOOLCHAIN) + read(HOOK)
        self.assertNotRegex(text, r"Read-Host|SecureString|Get-Credential|print-access-token|GOOGLE_APPLICATION_CREDENTIALS"
                                  r"|Import-PfxCertificate|-Password|/p ")
        self.assertIn('throw "$CertificateFile is a .pfx/.p12 key container', read(SIGN))
        self.assertIn("HasPrivateKey", read(SIGN))

    def test_artifact_signing_recognises_its_publisher_not_one_certificate(self):
        # Artifact Signing issues a new certificate every three days: a file signed on Monday is still ours on Friday.
        text = read(SIGN)
        ours = block(text, "function Test-Ours", "\n}\n")
        self.assertIn('if ($Signature.Status -ne "Valid"', ours)
        # The name alone would accept any trusted certificate with it: the identity validation's EKU too.
        self.assertIn("($ArtifactSigningIdentityEku -cin @(Get-IdentityEkus $Signature))", ours)
        self.assertIn('Where-Object { $_.StartsWith("1.3.6.1.4.1.311.97.") }', block(text, "function Get-IdentityEkus", "\n}\n"))
        self.assertIn("(Format-Name (Get-SignerName $Signature)) -ceq (Format-Name $Publisher)", ours,
                      "case-sensitive after ASCII folding: -eq would fold non-ASCII case too")
        name_rule = block(text, "function Format-Name", "\n}\n")
        self.assertIn('"[ \\t]+"', name_rule)
        self.assertIn('"[A-Z]"', name_rule, "ASCII letters only, as update_stage_win._fold")
        self.assertNotIn("\\s", name_rule)
        self.assertIn("Thumbprint -eq $Certificate.Thumbprint", ours, "a Cloud KMS certificate is still one certificate")
        self.assertIn("if (Test-Ours $signature $Certificate) { return \"ours\" }", text)
        self.assertIn("$ours = Test-Ours $signature $Certificate", text)
        self.assertNotIn("SignerCertificate.Thumbprint -eq", text.replace(ours, ""), "every 'ours' goes through Test-Ours")

    def test_artifact_signing_takes_names_and_never_a_credential(self):
        text = read(SIGN)
        for name in ("ENDPOINT", "ACCOUNT", "PROFILE"):
            self.assertIn("$env:ANGELO_SIGN_ARTIFACT_%s" % name, text)
        check = block(text, "function Assert-ArtifactSigningInputs", "\n}\n")
        self.assertIn("codesigning\\.azure\\.net", check)
        # The metadata file is the script's own, from the names: nothing else can be in it.
        self.assertIn("$metadata = [ordered]@{ Endpoint = $ArtifactSigningEndpoint; CodeSigningAccountName = "
                      "$ArtifactSigningAccount", check)
        self.assertIn("ExcludeCredentials = @($excluded)", check)
        self.assertIn('if ($AzureIdentity -eq "GceFederated") { $GceFederatedExcluded } else { $ArtifactSigningExcluded }', check)
        self.assertIn('"InteractiveBrowserCredential"', text, "an unattended build never waits for a browser")
        self.assertIn("Remove-Item -LiteralPath $ArtifactSigningMetadata", text)
        self.assertNotRegex(text, r"\$env:AZURE_CLIENT_SECRET|ClientSecret\s*=", "credentials stay in the environment")
        self.assertIn('$ArtifactSigningMinimumSignTool = [version]"10.0.22621.755"', text)
        self.assertIn('"http://timestamp.acs.microsoft.com"', text)
        build = read(BUILD)
        self.assertIn("[string]$SignTimestampUrl = $env:ANGELO_SIGN_TIMESTAMP_URL,", build,
                      "the build leaves the time-stamping service to the backend")
        # Azure's naming rules (quickstart): the patterns accept real names and refuse others.
        # .NET's \z (end of text, never before a final newline) is Python's \Z.
        account = re.search(r'\$ArtifactSigningAccount -notmatch "(.*?)"', check).group(1).replace("\\z", "\\Z")
        profile = re.search(r'\$ArtifactSigningProfile -notmatch "(.*?)"', check).group(1).replace("\\z", "\\Z")
        for name in ("angelosigning", "abc", "a" * 24, "my-account1"):
            self.assertRegex(name, account)
        for name in ("ab", "a" * 25, "1abc", "ab--c", "abc-", "a b c", "angelosigning\n"):
            self.assertNotRegex(name, account)
        for name in ("angelopublic", "abcde", "public-trust-1"):
            self.assertRegex(name, profile)
        for name in ("abcd", "a--bcd", "9abcde"):
            self.assertNotRegex(name, profile)

    def test_a_google_cloud_vm_signs_in_to_entra_by_federation_with_no_secret(self):
        text = read(SIGN)
        self.assertIn('[ValidateSet("Environment", "GceFederated")]', text)
        # Defaults from the environment are checked after binding: a typo never falls back to another mode.
        self.assertIn('if ($AzureIdentity -cnotin "Environment", "GceFederated") {', text)
        self.assertIn('if ($Backend -cnotin "KmsKey", "ArtifactSigning", "Esigner", "Presigned")', text)
        self.assertIn("the Azure sign-in settings belong to -Backend ArtifactSigning", text)
        self.assertIn('$FederatedAudience = "api://AzureADTokenExchange"', text)
        metadata = block(text, "function Get-GceMetadata", "\n}\n")
        self.assertIn("$request.Proxy = $null", metadata, "a credential never goes through a proxy")
        self.assertIn('$request.Headers.Add("Metadata-Flavor", "Google")', metadata)
        # The break-it review's HIGH: only the signer's own account signs, never the shared one.
        self.assertIn('$SharedValidationAccounts = @("win-build@")', text)
        account = block(text, "function Assert-SignerAccount", "\n}\n")
        self.assertIn('Get-GceMetadata "instance/service-accounts/default/email"', account)
        self.assertIn("$running -cne $GceServiceAccount", account)
        refresh = block(text, "function Update-FederatedToken", "\n}\n")
        self.assertIn("$claims.email -cne $GceServiceAccount -or $claims.aud -cne $FederatedAudience", refresh)
        self.assertIn("-ge 300", refresh, "a token about to expire is fetched again")
        self.assertNotRegex(refresh, r"Write-(Host|Output|Warning)[^\n]*\$token|\$env:AZURE", "never printed, never in our env")
        # The Azure variables live in signtool's environment for one call; every other AZURE_* is held back.
        call = block(text, "function Invoke-SignTool", "\n}\n")
        for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_FEDERATED_TOKEN_FILE"):
            self.assertIn("$env:%s =" % name, call)
        self.assertIn('Where-Object { $_.Name -like "AZURE_*" }', call)
        self.assertIn("} finally {", call)
        # Renewed before every signing attempt: an hour-long token never expires in a long run.
        loop = text[text.index("for ($attempt = 1; ; $attempt++) {   # A busy"):]
        self.assertLess(loop.index("Update-FederatedToken"), loop.index("Invoke-SignTool $tool (Get-SignArguments"))
        # Only the federated workload identity is left on.
        excluded = text[text.index("$GceFederatedExcluded = @("):]
        excluded = excluded[:excluded.index("\n")]
        for name in ("EnvironmentCredential", "ManagedIdentityCredential", "$ArtifactSigningExcluded"):
            self.assertIn(name, excluded)
        person = text[text.index("$ArtifactSigningExcluded = @("):]
        person = person[:person.index(")\n")]
        for name in ("InteractiveBrowserCredential", "AzureCliCredential", "AzurePowerShellCredential",
                     "AzureDeveloperCliCredential", "VisualStudioCredential"):
            self.assertIn(name, person, "a person's sign-in never signs, in either mode")
        self.assertNotIn("WorkloadIdentityCredential", excluded + person)
        # Preflight proves the sign-in with one real signature.
        self.assertIn('if ($Mode -eq "Preflight") { Assert-SigningWorks $tool', text)
        # The token file goes at the end, on any error and on Ctrl+C (finally), and old ones are swept.
        self.assertIn("Remove-Item -LiteralPath $ArtifactSigningMetadata, $FederatedTokenFile", text)
        self.assertLess(text.index("\ntry {\nif ($DryRun) {"), text.index("\n} finally {\n    Remove-Item -LiteralPath $ArtifactSigningMetadata"))
        self.assertIn('[DateTime]::UtcNow.AddHours(-2)', text)
        # \z, not $: PowerShell's $ also matches before a trailing newline. Every anchored pattern the script holds.
        anchored = re.findall(r'"(\^[^"]*)"', text)
        self.assertTrue(len(anchored) >= 6, anchored)
        # A scheme check and a properties file's key (CodeSignTool's TSA_URL = value): prefixes by design.
        prefixes = {"^https?://", "^\\s*TSA_URL\\s*="}
        self.assertEqual([p for p in anchored if not p.endswith("\\z") and p not in prefixes], [])
        # The preflight probe is a script of our own, never a copy of someone else's program.
        probe = block(text, "function Assert-SigningWorks", "\n}\n")
        self.assertIn('"angelo-sign-probe-{0}.ps1"', probe)
        self.assertNotIn("whoami", probe)
        self.assertNotRegex(probe, r"Copy-Item", "nothing but our own probe is signed")
        self.assertIn('$env:AZURE_TOKEN_CREDENTIALS = "WorkloadIdentityCredential"', text)

    def test_every_release_names_the_pinned_publisher(self):
        # Two CAs may write the legal name differently ("Pte Ltd" / "Pte. Ltd."); the updater would then refuse the
        # next release on every signed install. The release build compares the signed MSI and app with the pin first,
        # with the updater's own comparison (break-it review 2026-10-21).
        identity = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
        pin = identity["windows"]["authenticode_publisher"]
        good = "CN={0}, O={0}, L=Singapore, C={1}".format(pin["name"].upper(), pin["country"])
        other = "CN={0}, O={0}, C={1}".format(pin["name"].replace(".", ""), pin["country"])

        def check(*subjects):
            arguments = [part for subject in subjects for part in ("--subject", subject)]
            return subprocess.run([sys.executable, "-I", "-B", str(TOOLS / "release_checks.py"), "authenticode-publisher",
                                   *arguments], capture_output=True, text=True)
        self.assertEqual(check(good, good).returncode, 0)
        refused = check(good, other)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("refuse this release's updates", refused.stderr)
        self.assertEqual(check("").returncode, 1, "an unsigned file names no publisher")
        sys.path.insert(0, str(TOOLS))
        import release_checks  # noqa: E402
        signatures = {"Angelo.msi": {"status": "Valid", "subject": good, "issuer": ""},
                      "Angelo.exe": {"status": "NotSigned", "subject": "", "issuer": ""}}
        problems = release_checks.authenticode_publisher_problems(files=["Angelo.msi", "Angelo.exe"],
                                                                  signature=lambda path: signatures[path])
        self.assertEqual(len(problems), 1)
        self.assertIn("Angelo.exe: Authenticode status NotSigned", problems[0])
        person = "CN={0}, C={1}".format(pin["name"], pin["country"])
        self.assertEqual(check(person).returncode, 1, "a person's certificate is not the organization's")
        build = read(BUILD)
        gate = block(build, "if ($Sign) {\n        # Before the receipt", "\n    }\n")
        self.assertIn("authenticode-publisher --file $msi.FullName --file $payloadExe.FullName", gate,
                      "the updater's own reading of both signatures, never subjects passed as arguments")
        self.assertIn('$payloadExe = Get-ChildItem $payload -Recurse -Filter ($identity.windows.exe_stem + ".exe")', build,
                      "the installed app, not only the MSI")
        # The receipt publish_installers.py requires is written only after the gate passed (re-review 2026-10-21).
        self.assertLess(build.index("authenticode-publisher --file"), build.index('$receipt = "$($msi.FullName).builder-paths.json"'))
        self.assertIn("if (-not $env:ANGELO_SIGN_PUBLISHER) { $env:ANGELO_SIGN_PUBLISHER = $signPublisher.name }", build)

    def test_every_release_records_what_it_is_for_exactly_its_msi(self):
        # Declared by -Sign, never inferred: the build's last gate writes <msi>.signing.json, which publishing needs.
        build = read(BUILD)
        gate = block(build, "    # The last gate: what this release is", "Remove-Item $payload -Recurse -Force")
        self.assertIn('$signing = if ($Sign) { "signed" } else { "unsigned" }', gate)
        self.assertIn("msi-signing-record --installer $msi.FullName --signing $signing --file $payloadExe.FullName", gate)
        self.assertLess(build.index("tracked-scripts (Join-Path $payload"), build.index("msi-signing-record"),
                        "after every other gate on the payload")
        self.assertIn('"$($msi.FullName).signing.json"', build, "the record travels with the MSI")
        sys.path.insert(0, str(TOOLS))
        import msi_signing  # noqa: E402
        import release_checks  # noqa: E402
        with tempfile.TemporaryDirectory() as folder:
            msi = Path(folder, "Angelo-1.0.3-windows-x64.msi")
            msi.write_bytes(b"msi")
            identity = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
            pin = identity["windows"]["authenticode_publisher"]
            ours = {"status": "Valid", "subject": "CN={0}, O={0}, C={1}".format(pin["name"], pin["country"]), "issuer": ""}
            unsigned = {"status": "NotSigned", "subject": "", "issuer": ""}
            states = {"unsigned": unsigned, "signed": ours}
            for declared, actual, ok in (("unsigned", "unsigned", True), ("unsigned", "signed", False),
                                         ("signed", "signed", True), ("signed", "unsigned", False)):
                with self.subTest(declared=declared, actual=actual):
                    msi_signing.record_path(msi).unlink(missing_ok=True)
                    problems = release_checks.msi_signing_record_problems(
                        str(msi), declared, ["Angelo.exe"], signature=lambda path, a=actual: states[a])
                    self.assertEqual(not problems, ok, problems)
                    self.assertEqual(msi_signing.record_path(msi).exists(), ok, "a record only for what is true")
                    if ok:
                        self.assertIsNone(msi_signing.problem(msi, declared, declared == "signed"))
            # An app that is not as the MSI (a signed MSI around an unsigned app) is refused too.
            msi_signing.record_path(msi).unlink(missing_ok=True)
            mixed = lambda path: ours if str(path) == str(msi) else unsigned  # noqa: E731
            self.assertTrue(release_checks.msi_signing_record_problems(str(msi), "signed", ["Angelo.exe"], signature=mixed))
            self.assertFalse(msi_signing.record_path(msi).exists())

    def test_the_key_name_rule(self):
        pattern = re.search(r'\$KeyPattern = "(.*?)"', read(SIGN)).group(1).replace("\\z", "\\Z")   # .NET \z is Python's \Z
        self.assertRegex(KEY, pattern)
        for bad in ("projects/p/locations/l/keyRings/r/cryptoKeys/k", KEY + "/extra", KEY + "\n", "gcloud://x", ""):
            self.assertNotRegex(bad, pattern)

    def test_refusals_and_resigning_rules(self):
        text = read(SIGN)
        self.assertIn('"HashMismatch" { throw', text, "a file changed after signing is never signed over")
        self.assertIn('return "vendor:"', text, "a vendor's valid signature is kept")
        self.assertIn("$CodeSigningEku -notin $usages", text)
        self.assertIn("$certificate.NotAfter -lt $now", text)
        self.assertIn("certutil.exe -csplist", text)
        self.assertIn('Start-Process msiexec.exe -ArgumentList @("/a"', text, "the MSI's contents are checked unpacked")


class AgentRuntimeSigningTests(unittest.TestCase):
    """OpenAI's unchanged executables keep OpenAI's signature; codex.exe, Angelo's build, gets Angelo's."""

    def test_the_signer_reads_the_pin_and_never_signs_openais_files(self):
        text = read(SIGN)
        self.assertIn('Join-Path $PSScriptRoot "..\\addons_core\\angelo_agent\\agent_runtime.json"', text)
        self.assertIn("$UpstreamSigned = @($RuntimePin.upstream_signed)", text)
        self.assertIn("$UpstreamSigner = [string]$RuntimePin.upstream_signer", text)
        decision = block(text, "function Get-Decision", "function Assert-Signed")
        upstream = block(decision, "if ($File.Name -in $UpstreamSigned) {", "switch ($signature.Status)")
        self.assertIn('$signature.Status -eq "Valid" -and (Get-SignerName $signature) -eq $UpstreamSigner', upstream)
        self.assertIn('return "upstream:"', upstream)
        self.assertIn("refusing to sign it", upstream, "any other state of OpenAI's file stops signing")
        self.assertNotRegex(upstream, r'return "(sign|resign|vendor)', "OpenAI's file is never queued for our signature")
        self.assertLess(decision.index("$UpstreamSigned"), decision.index('"NotSigned" { return "sign" }'),
                        "checked before an unsigned or invalid signature could queue it")
        own = block(decision, '"Valid" {', '"HashMismatch"')
        self.assertIn("if ($File.Name -in $KeyFiles) {", own)
        self.assertLess(own.index("return \"ours\""), own.index("$KeyFiles"))
        loop = block(text, "foreach ($file in @(Get-Targets)) {", "$toSign = $pending.ToArray()")
        upstream_branch = re.search(r'elseif \(\$decision\.StartsWith\("upstream:"\)\) \{(.*?)\}\n', loop).group(1)
        self.assertNotIn("$pending.Add", upstream_branch)
        self.assertIn("$result.upstream_kept +=", upstream_branch)

    def test_verification_holds_each_runtime_file_to_its_signer(self):
        text = read(SIGN)
        check = block(text, "function Assert-Signed", "function Assert-RuntimeFolder")
        self.assertIn("if ($ours -or (Get-SignerName $signature) -ne $UpstreamSigner) {", check)
        self.assertIn("if ($File.Name -in $KeyFiles -and -not $ours) {", check)
        self.assertLess(check.index("$UpstreamSigned"), check.index("-not $AllowVendor"), "no vendor pass for codex.exe")
        folder = block(text, "function Assert-RuntimeFolder", "$result = [ordered]@{")
        self.assertIn("$_.Directory.Name -eq $RuntimePin.folder", folder)
        self.assertIn("(@($RuntimePin.required) + @($RuntimePin.optional))", folder)
        self.assertIn("$result.agent_runtime = Assert-RuntimeFolder $inner", text, "VerifyMsi checks the MSI's runtime")
        dry = block(text, "if ($DryRun) {", "} else {")
        self.assertIn("Where-Object { $_.Name -notin $UpstreamSigned -and (Test-KeyFile $_) }", dry, "the plan never lists OpenAI's files")

    def test_the_pin_names_codex_exe_as_angelos_and_the_rest_as_openais(self):
        self.assertEqual(WINDOWS["upstream_signer"], "OpenAI OpCo, LLC")
        own = [name for name in WINDOWS["required"] + WINDOWS["optional"] if name not in WINDOWS["upstream_signed"]]
        self.assertEqual(own, ["codex.exe"])
        self.assertNotIn("windows", PIN.get("_pending", {}), "the Windows binaries exist")

    def test_the_build_checks_the_runtime_before_compiling_and_its_installed_copy_after(self):
        text = read(BUILD)
        order = [text.index(marker) for marker in (
            "# --- Agent runtime", "is not an x64 Windows executable", "$name -in @($runtimeFiles.upstream_signed)",
            "Get-AuthenticodeSignature -LiteralPath $path", "$agentRuntime.sha256[$name] =", "& $codexExe --version",
            "# --- Configure", "--target INSTALL", "foreach ($name in $agentRuntime.sha256.Keys) {", "# --- Package")]
        self.assertEqual(order, sorted(order))
        self.assertIn("-eq 0x8664", text)
        self.assertIn("$signer -ne $runtimeFiles.upstream_signer", text)
        self.assertIn('is not the checked $name', text)


class ValidationMachineTests(unittest.TestCase):
    """The validation machine bundles the runtime it was given and runs it from the MSI."""

    def test_the_release_stage_builds_with_the_runtime_and_runs_it_from_the_msi(self):
        text = read(RELEASE_STAGE)
        self.assertIn("[string]$AgentRuntimeDir = $env:ANGELO_AGENT_RUNTIME_DIR", text)
        self.assertIn('if ($AgentRuntimeDir) { $allow += " -AgentRuntimeDir', text)
        order = [text.index(marker) for marker in (
            "build_release_windows.ps1 -Package", 'windows_agent_runtime_check.ps1"', "-Msi `\"$msi`\"",
            "BACKGROUND native suites on the full build")]
        self.assertEqual(order, sorted(order))
        self.assertIn('" -Release" }', text, "a runtime-required build must carry the release marker")

    def test_the_bootstrap_fetches_and_checks_the_runtime_for_a_release_run(self):
        stage = block(read(BOOTSTRAP), "    if ($releaseRun) {\n        # The libraries", "    # --- 4. Build, package")
        self.assertIn('. (Join-Path $src "scripts\\angelo\\windows_release_inputs.ps1")', stage, "run in the bootstrap's scope")
        release = read(RELEASE_INPUTS)
        order = [release.index(marker) for marker in (
            'Remove-Item $runtimeDir -Recurse', '$env:ANGELO_AGENT_RUNTIME_DIR = ""', '"gs://$bucket/agent-runtime/*"',
            '$sums = Join-Path $runtimeDir "SHA256SUMS"', "Get-FileHash $path -Algorithm SHA256", "$env:ANGELO_AGENT_RUNTIME_DIR = $runtimeDir")]
        self.assertEqual(order, sorted(order), "fetched afresh and checked before the build may use it")

    def test_every_shipped_git_lfs_file_travels_real_and_a_pointer_is_never_sent(self):
        """The archive of HEAD carries LFS pointers; the files the build installs travel real beside it. The agent
        add-on's images were outside the folders sent and shipped in the MSI as pointers."""
        text = read(GCP) + read(GCP_SOURCE)
        folders = re.search(r"^LFS_DIRS='(.*)'$", text, re.M).group(1)
        self.assertNotIn("bl_app_templates_system|", text, "one list, not a copy per command")
        self.assertEqual(text.count('"^($LFS_DIRS)/"'), 1, "pack_lfs, shared by every send")
        self.assertIn('git lfs ls-files | grep -E " - ($LFS_DIRS)/"', block(text, "need_real_files() {", "\n}\n"))
        # Every send (up, redo for a full or delta send, release-build, bench, sync) refuses a pointer before packing,
        # and the machine's proof fails while any file of the real list is one (the LFS clean filter hides it).
        send = block(text, "send_source() {", "\n}\n")
        self.assertLess(send.index("need_real_files"), send.index("pack_head") if "pack_head" in send else len(send))
        self.assertLess(send.index("need_real_files"), send.index("pack_delta"))
        self.assertIn('send_source "$work" "$nonce" "$base"', block(text, "sync() {", "\n}\n"))
        self.assertIn('> "$1/angelo-lfs-real.txt"', block(text, "pack_lfs() {", "\n}\n"))
        self.assertIn('"$work/angelo-lfs-real.txt"', send)
        self.assertIn("shipped_sources.pointer_problems(tree, real)", read(TOOLS / "source_tree.py"))
        listed = subprocess.run(["git", "-C", str(ROOT), "-c", "core.quotePath=off", "ls-files", "-z", ":(attr:filter=lfs)"],
                                capture_output=True, text=True)
        if listed.returncode == 0:   # A source archive without Git has nothing to list; CI checks out the repository.
            shipped = [path for path in listed.stdout.split("\0")
                       if path and not re.match(r"(tests|doc|lib|release/darwin)/", path)]
            self.assertTrue(shipped)
            self.assertEqual([path for path in shipped if not re.match(r"(%s)/" % folders, path)], [])

    def test_no_file_the_install_wrote_may_be_a_git_lfs_pointer(self):
        text = read(BUILD)
        order = [text.index(marker) for marker in (
            'throw "$copy is not the checked $name', 'Join-Path $BuildDir "install_manifest.txt"', "(Test-Pointer $_)",
            "Git LFS pointers would ship", "# --- Package")]
        self.assertEqual(order, sorted(order))

    def test_the_runtime_check_runs_what_the_app_requires(self):
        text = read(RUNTIME_CHECK)
        self.assertIn('Join-Path $PSScriptRoot "..\\addons_core\\angelo_agent\\agent_runtime.json"', text)
        for marker in ('Start-Process msiexec.exe -ArgumentList @("/a"', "$_.Directory.Name -eq $files.folder",
                       "(@($files.required) + @($files.optional))", "-SourceRuntimeDir", "$files.release_marker",
                       "$name -in @($files.upstream_signed)", "$signer -eq $files.upstream_signer",
                       '@("--version")', '@("app-server", "generate-json-schema", "--experimental", "--out", $schema)',
                       '"app-server --listen stdio://"', 'method = "initialize"', "CODEX_HOME"):
            self.assertIn(marker, text)
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        from angelo_agent import agent_runtime
        pattern = re.search(r'\$result\.user_agent -match "(.*?)"\)', text).group(1)
        self.assertEqual(pattern, agent_runtime._VERSION.pattern, "the version the app reads from initialize")
        self.assertNotRegex(text, r"api\.openai\.com|chatgpt\.com|codex login", "offline: no account or provider")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("shasum") and os.name != "nt", "needs bash and shasum")
    def test_the_upload_command_checks_the_folder_and_sends_only_the_pinned_files(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_native_bundle_contents import windows_file
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            runtime = folder / "runtime"
            runtime.mkdir()
            for name in WINDOWS["required"] + WINDOWS["optional"]:
                (runtime / name).write_bytes(windows_file(name))
            (runtime / "codex.pdb").write_bytes(b"symbols")
            (runtime / "PROVENANCE.txt").write_text("built from the fork")
            subprocess.run("shasum -a 256 *.exe > SHA256SUMS", shell=True, cwd=runtime, check=True)
            captured = folder / "bucket"
            bin_dir = folder / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "gcloud"
            fake.write_text("#!/usr/bin/env python3\nimport os, shutil, sys\nfrom pathlib import Path\n"
                            "args = sys.argv[1:]\nopen(os.environ['GCLOUD_LOG'], 'a').write(' '.join(args) + '\\n')\n"
                            "if args[1:3] == ['storage', 'cp']:\n"
                            "    out = Path(os.environ['GCLOUD_CAPTURE']); out.mkdir(exist_ok=True)\n"
                            "    [shutil.copy(a, out) for a in args[3:-1] if os.path.isfile(a)]\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ["PATH"]), ANGELO_GCP_PROJECT="example-project",
                       ANGELO_GCP_ZONE="us-central1-a", ANGELO_GCP_NAME="example-vm", GCLOUD_LOG=str(folder / "log"),
                       GCLOUD_CAPTURE=str(captured))

            def upload():
                return subprocess.run(["bash", str(GCP), "agent-runtime", str(runtime)], capture_output=True, text=True,
                                      env=env, cwd=folder)
            done = upload()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(sorted(p.name for p in captured.iterdir()),
                             sorted(WINDOWS["required"] + WINDOWS["optional"] + ["PROVENANCE.txt", "SHA256SUMS"]))
            sums = {line.split()[1]: line.split()[0] for line in (captured / "SHA256SUMS").read_text().splitlines()}
            self.assertEqual(sorted(sums), sorted(WINDOWS["required"] + WINDOWS["optional"]))
            log = (folder / "log").read_text()
            self.assertIn("storage rm --quiet gs://example-project-example-vm/agent-runtime/**", log)
            self.assertIn("gs://example-project-example-vm/agent-runtime/", log.splitlines()[-2])
            shutil.rmtree(captured)
            (runtime / "codex-code-mode-host.exe").write_bytes(windows_file("codex.exe"))   # OpenAI's signature gone
            subprocess.run("shasum -a 256 *.exe > SHA256SUMS", shell=True, cwd=runtime, check=True)
            done = upload()
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("must carry OpenAI OpCo, LLC's own signature", done.stderr)
            self.assertFalse(captured.exists(), "nothing is uploaded from a refused folder")


class BuildScriptTests(unittest.TestCase):
    def test_signing_is_checked_before_compiling_and_runs_inside_cpack(self):
        text = read(BUILD)
        order = [text.index(marker) for marker in (
            "& $signScript -Mode Preflight", "update_keys.json has no active key", "# --- Libraries and source assets",
            "--target INSTALL", "CPACK_PRE_BUILD_SCRIPTS=$hook", "& $signScript -Mode Sign -Path $msi.FullName",
            "& $signScript -Mode VerifyMsi", "$msi.Refresh()", "sha256 = (Get-FileHash $msi.FullName")]
        self.assertEqual(order, sorted(order))
        self.assertIn('if (-not $Package) { throw "-Sign signs the MSI; give -Package too" }', text)
        self.assertIn('$env:ANGELO_SIGN_EXPECT = $identity.windows.exe_stem + ".exe"', text)

    def test_a_release_bundles_the_checked_agent_runtime_and_a_rehearsal_says_it_has_none(self):
        """The runtime is built apart from the app: without its folder the build stops at once, saying what to do."""
        text = read(BUILD)
        order = [text.index(marker) for marker in (
            "# --- Agent runtime", "The Windows agent runtime is not supplied", "& $codexExe --version",
            "& $codexExe app-server generate-json-schema",
            "-DANGELO_REQUIRE_AGENT_RUNTIME=ON", "--target INSTALL", 'Join-Path $bin "$($runtimeFiles.folder)\\$_"')]
        self.assertEqual(order, sorted(order))
        self.assertIn('"oneapi", "agent-runtime") { throw "-AllowMissing knows', text)
        self.assertIn('if ($Sign -and "agent-runtime" -in $allowed) { throw', text, "a signed MSI is a release")
        self.assertIn("agent_runtime = $agentRuntime", text, "the manifest says which runtime shipped")
        self.assertIn('scripts\\addons_core\\angelo_agent\\agent_runtime.json', text, "the one pin")
        self.assertIn("[string]$AgentRuntimeDir = $env:ANGELO_AGENT_RUNTIME_DIR", text)


@unittest.skipUnless(shutil.which("cmake") and shutil.which("cpack") and os.name != "nt",
                     "CMake and CPack are required, and the stand-in signer is a POSIX script")
class CpackHookTests(unittest.TestCase):
    """windows_sign_staging.cmake through a real cpack run (TGZ instead of WiX: the hook is generator-neutral)."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        source = self.folder / "src"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.19)\nproject(hook NONE)\n"
            'file(WRITE "${CMAKE_BINARY_DIR}/Angelo.exe" "exe")\nfile(WRITE "${CMAKE_BINARY_DIR}/tbb.dll" "dll")\n'
            'install(FILES "${CMAKE_BINARY_DIR}/Angelo.exe" "${CMAKE_BINARY_DIR}/tbb.dll" DESTINATION .)\n'
            "set(CPACK_GENERATOR TGZ)\nset(CPACK_PACKAGE_NAME Angelo)\ninclude(CPack)\n")
        self.build = self.folder / "build"
        subprocess.run(["cmake", "-S", str(source), "-B", str(self.build)], check=True, capture_output=True)
        self.signer = self.folder / "signer"
        self.signer.write_text("#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
                               "args = sys.argv[1:]\nPath(os.environ['HOOK_ARGS']).write_text(json.dumps(args))\n"
                               "for path in Path(args[args.index('-Path') + 1]).rglob('*'):\n"
                               "    path.is_file() and path.write_bytes(path.read_bytes() + b' SIGNED')\n"
                               "sys.exit(int(os.environ.get('HOOK_EXIT', '0')))\n")
        self.signer.chmod(self.signer.stat().st_mode | stat.S_IEXEC)

    def cpack(self, expect="Angelo.exe", exit_code="0"):
        env = dict(os.environ, ANGELO_SIGN_SCRIPT=str(SIGN), ANGELO_SIGN_EXPECT=expect, ANGELO_POWERSHELL=str(self.signer),
                   HOOK_ARGS=str(self.folder / "args.json"), HOOK_EXIT=exit_code, ANGELO_SIGN_REPORT=str(self.folder / "r.json"))
        for package in self.build.glob("*.tar.gz"):
            package.unlink()
        done = subprocess.run(["cpack", "-D", "CPACK_PRE_BUILD_SCRIPTS=%s" % HOOK.as_posix()], cwd=self.build,
                              capture_output=True, text=True, env=env)
        return done, list(self.build.glob("*.tar.gz"))

    def test_the_packed_files_are_the_signed_ones(self):
        done, packages = self.cpack()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        arguments = json.loads((self.folder / "args.json").read_text())
        self.assertEqual(arguments[arguments.index("-Mode") + 1], "Sign")
        self.assertEqual(arguments[arguments.index("-Report") + 1], str(self.folder / "r.json"))
        self.assertIn("-NonInteractive", arguments)
        with tarfile.open(packages[0]) as archive:
            contents = {Path(m.name).name: archive.extractfile(m).read() for m in archive.getmembers()
                        if m.isfile() and not Path(m.name).name.startswith("._")}   # macOS adds AppleDouble files.
        self.assertEqual(contents, {"Angelo.exe": b"exe SIGNED", "tbb.dll": b"dll SIGNED"})

    def test_a_failing_signer_or_a_wrong_staging_folder_stops_cpack(self):
        for expect, exit_code in (("Angelo.exe", "1"), ("Nothing.exe", "0")):
            with self.subTest(expect=expect, exit_code=exit_code):
                done, packages = self.cpack(expect, exit_code)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(packages, [], "no package is produced from unsigned files")


class ParseTests(unittest.TestCase):
    def test_the_scripts_parse(self):
        pwsh = shutil.which("pwsh")
        for script in (SIGN, BUILD, TOOLCHAIN, RUNTIME_CHECK, RELEASE_STAGE, BOOTSTRAP, RELEASE_INPUTS):
            with self.subTest(script=script.name):
                self.assertEqual(ps_structure_problems(read(script)), [])
                if pwsh:
                    command = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('%s', "
                               "[ref]$null, [ref]$e); $e | ForEach-Object { $_.ToString() }" % script)
                    done = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True)
                    self.assertEqual((done.returncode, done.stdout.strip()), (0, ""), done.stderr)
        if pwsh:   # Not a separate, skippable test: the host suite allows no skips where PowerShell is absent.
            self.check_dry_run(pwsh)

    def test_the_structure_check_finds_what_it_is_for(self):
        self.assertEqual(ps_structure_problems('if ($a) { "}" ; $b = @("x") } # } \n'), [])
        self.assertTrue(ps_structure_problems("if ($a) { $b = @(1, 2 }\n"))
        self.assertTrue(ps_structure_problems("function f {\n"))

    def check_dry_run(self, pwsh):
        """-DryRun plans every binary in batches and prints the exact signtool command (CI, where pwsh exists)."""
        with tempfile.TemporaryDirectory() as folder:
            runtime = ["agent-runtime/" + name for name in WINDOWS["required"] + WINDOWS["optional"]]
            for name in ["Angelo.exe", "Angelo-launcher.exe", "5.2/python/DLLs/_ctypes.pyd", "tbb.dll", "readme.txt"] + runtime:
                path = Path(folder, "stage", name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
            done = subprocess.run([pwsh, "-NoProfile", "-File", str(SIGN), "-Mode", "Sign", "-DryRun",
                                   "-Path", str(Path(folder, "stage")), "-KmsKey", KEY, "-CertificateFile", "codesign.crt",
                                   "-BatchSize", "3", "-Report", str(Path(folder, "plan.json"))],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            plan = json.loads(Path(folder, "plan.json").read_text())
            self.assertEqual(sorted(Path(p).name for p in plan["signed"]),
                             ["Angelo-launcher.exe", "Angelo.exe", "_ctypes.pyd", "codex.exe", "tbb.dll"])
            self.assertEqual(sorted(entry.split(" ")[0] for entry in plan["upstream_kept"]), sorted(WINDOWS["upstream_signed"]))
            self.assertEqual(plan["batches"], 2)
            self.assertIn('/csp Google Cloud KMS Provider /kc %s' % KEY, done.stdout)


if __name__ == "__main__":
    unittest.main()
