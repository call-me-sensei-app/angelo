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
GCP = TOOLS / "gcp_windows_validate.sh"
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
        arguments = re.search(r'function Get-SignArguments.*?\$arguments = @\((.*?)\)', text, re.S).group(1)
        for part in ('"sign"', '"/fd", "sha256"', '"/tr", $TimestampUrl', '"/td", "sha256"', '"/f", $CertificateFile',
                     '"/csp", $Provider', '"/kc", $KmsKey'):
            self.assertIn(part, arguments)
        self.assertIn('$Provider = "Google Cloud KMS Provider"', text)
        self.assertNotRegex(arguments, r'"/t"|"/p"|"/a"|"/sha1"', "no legacy timestamp, key password or store pick")
        self.assertIn('@("verify", "/pa", "/q", $File.FullName)', text)
        self.assertIn("TimeStamperCertificate", text, "every signature must carry a timestamp")

    def test_it_never_handles_a_secret(self):
        text = read(SIGN) + read(BUILD) + read(HOOK)
        self.assertNotRegex(text, r"Read-Host|SecureString|Get-Credential|print-access-token|GOOGLE_APPLICATION_CREDENTIALS"
                                  r"|Import-PfxCertificate|-Password|/p ")
        self.assertIn('throw "$CertificateFile is a .pfx/.p12 key container', read(SIGN))
        self.assertIn("HasPrivateKey", read(SIGN))

    def test_the_key_name_rule(self):
        pattern = re.search(r'\$KeyPattern = "(.*?)"', read(SIGN)).group(1)
        self.assertRegex(KEY, pattern)
        for bad in ("projects/p/locations/l/keyRings/r/cryptoKeys/k", KEY + "/extra", "gcloud://x", ""):
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
        self.assertIn("if ($File.Name -in $RuntimeOwn) {", own)
        self.assertLess(own.index("return \"ours\""), own.index("$RuntimeOwn"))
        loop = block(text, "foreach ($file in @(Get-Targets)) {", "$toSign = $pending.ToArray()")
        upstream_branch = re.search(r'elseif \(\$decision\.StartsWith\("upstream:"\)\) \{(.*?)\}\n', loop).group(1)
        self.assertNotIn("$pending.Add", upstream_branch)
        self.assertIn("$result.upstream_kept +=", upstream_branch)

    def test_verification_holds_each_runtime_file_to_its_signer(self):
        text = read(SIGN)
        check = block(text, "function Assert-Signed", "function Assert-RuntimeFolder")
        self.assertIn("if ($ours -or (Get-SignerName $signature) -ne $UpstreamSigner) {", check)
        self.assertIn("if ($File.Name -in $RuntimeOwn -and -not $ours) {", check)
        self.assertLess(check.index("$UpstreamSigned"), check.index("-not $AllowVendor"), "no vendor pass for codex.exe")
        folder = block(text, "function Assert-RuntimeFolder", "$result = [ordered]@{")
        self.assertIn("$_.Directory.Name -eq $RuntimePin.folder", folder)
        self.assertIn("(@($RuntimePin.required) + @($RuntimePin.optional))", folder)
        self.assertIn("$result.agent_runtime = Assert-RuntimeFolder $inner", text, "VerifyMsi checks the MSI's runtime")
        dry = block(text, "if ($DryRun) {", "} else {")
        self.assertIn("Where-Object { $_.Name -notin $UpstreamSigned }", dry, "the plan never lists OpenAI's files")

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
        text = read(BOOTSTRAP)
        release = block(text, "    if ($releaseRun) {\n        # The libraries", "    # --- 4. Build, package")
        order = [release.index(marker) for marker in (
            'Remove-Item $runtimeDir -Recurse', '$env:ANGELO_AGENT_RUNTIME_DIR = ""', '"gs://$bucket/agent-runtime/*"',
            '$sums = Join-Path $runtimeDir "SHA256SUMS"', "Get-FileHash $path -Algorithm SHA256", "$env:ANGELO_AGENT_RUNTIME_DIR = $runtimeDir")]
        self.assertEqual(order, sorted(order), "fetched afresh and checked before the build may use it")

    def test_every_shipped_git_lfs_file_travels_real_and_a_pointer_is_never_sent(self):
        """The archive of HEAD carries LFS pointers; the files the build installs travel real beside it. The agent
        add-on's images were outside the folders sent and shipped in the MSI as pointers."""
        text = read(GCP)
        folders = re.search(r"^LFS_DIRS='(.*)'$", text, re.M).group(1)
        self.assertNotIn("bl_app_templates_system|", text, "one list, not a copy per command")
        self.assertEqual(text.count('"^($LFS_DIRS)/"'), 5)
        self.assertIn('git lfs ls-files | grep -E " - ($LFS_DIRS)/"', block(text, "need_real_files() {", "\n}\n"))
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
        for script in (SIGN, BUILD, RUNTIME_CHECK, RELEASE_STAGE, BOOTSTRAP):
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
