# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The macOS release signer (scripts/angelo/sign_notarize_macos.py, macho.py, dmg_macos.py) without a Mac.

Dependency-free: a synthetic Angelo.app made of tiny hand-built Mach-O files and a recording stand-in for
codesign, security, spctl, hdiutil and notarytool. Covered: Mach-O headers (thin, universal, object files,
look-alikes); the staging that drops caches and the bundled Python's build leftovers; every refusal (object
files, absolute dependencies and rpaths, links out of the bundle, unsigned nested bundle types, another
product or version, an empty update-key list, a missing, incomplete or joined agent runtime, an Apple
Development identity, an ad-hoc notarization); the inside-out order and each signature's flags and
entitlements, the agent runtime's own by path; the pinned entitlement sets; notarization
accepted and refused; the app notarized and stapled before the disk image is built, so a copy dragged out of
the image opens offline; and the disk image's stored window layout. The real tools run in the documented dry
run on the compiled app (doc/angelo/releasing.rst), which is what proves the signatures and the launch.
"""

import io
import json
import os
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import dmg_macos  # noqa: E402
import macho  # noqa: E402
import release_checks  # noqa: E402
import sign_notarize_macos as tool  # noqa: E402

IDENTITY = tool.IDENTITY
DEVELOPER_ID = "Developer ID Application: Hyperbond Studio PTE. LTD. (ABCDE12345)"


def mach_o(file_type=6, dylibs=(), rpaths=(), cpu=0x0100000C):
    """A minimal little-endian 64-bit Mach-O image with the given load commands."""
    commands = b""
    for command, names in ((0xC, dylibs), (0x8000001C, rpaths)):
        for name in names:
            text = name.encode() + b"\0"
            text += b"\0" * (-(len(text) + 12) % 8)
            offset = 24 if command == 0xC else 12
            body = struct.pack("<I", offset) + (b"\0" * 12 if command == 0xC else b"") + text
            commands += struct.pack("<II", command, 8 + len(body)) + body
    count = len(dylibs) + len(rpaths)
    return struct.pack("<IiiIIIII", macho.MH_MAGIC_64, cpu, 0, file_type, count, len(commands), 0, 0) + commands


def universal(*slices):
    header = struct.pack(">II", macho.FAT_MAGIC, len(slices))
    offset, table, body = 4096, b"", b""
    for cpu, data in slices:
        table += struct.pack(">iiIII", cpu, 0, offset + len(body), len(data), 12)
        body += data + b"\0" * (-len(data) % 4096)
    return header + table + b"\0" * (4096 - len(header) - len(table)) + body


def plist(**values):
    return plistlib.dumps(values)


def make_app(root, version=None, keys=True, agent_runtime=True):
    """A small Angelo.app with every kind of code the real bundle has (the agent runtime in Contents/Helpers)."""
    app = root / "Angelo.app"
    contents = app / "Contents"
    resources = contents / "Resources/5.2"
    files = {
        "Contents/Info.plist": plist(CFBundleIdentifier=IDENTITY["bundle"]["macos_id"], CFBundleExecutable="Angelo",
                                     CFBundleShortVersionString=version or IDENTITY["version"]["label"]),
        "Contents/MacOS/Angelo": mach_o(2, ["@rpath/libtbb.dylib", "/usr/lib/libSystem.B.dylib"],
                                        ["@executable_path/../Resources/lib"]),
        "Contents/Resources/lib/libtbb.dylib": mach_o(6, ["/usr/lib/libc++.1.dylib"]),
        "Contents/Resources/5.2/python/bin/python3.13": mach_o(2),
        "Contents/Resources/5.2/python/lib/python3.13/lib-dynload/_ctypes.cpython-313-darwin.so": universal(
            (0x0100000C, mach_o(8)), (0x01000007, mach_o(8, cpu=0x01000007))),
        "Contents/Resources/5.2/python/lib/python3.13/config-3.13-darwin/python.o": mach_o(1),
        "Contents/Resources/5.2/python/lib/python3.13/config-3.13-darwin/libpython3.13.a": b"!<arch>\n",
        "Contents/Resources/5.2/scripts/startup/__pycache__/x.cpython-313.pyc": b"cache",
        "Contents/Resources/5.2/scripts/startup/x.py": b"# script\n",
        "Contents/Resources/.DS_Store": b"finder",
        "Contents/Resources/Example.class": b"\xca\xfe\xba\xbe\x00\x00\x00\x34rest",
        "Contents/PlugIns/blender-thumbnailer.appex/Contents/Info.plist": plist(CFBundleExecutable="blender-thumbnailer"),
        "Contents/PlugIns/blender-thumbnailer.appex/Contents/MacOS/blender-thumbnailer": mach_o(2),
    }
    if agent_runtime:
        files.update({"Contents/Helpers/" + name: mach_o(2) for name in tool.AGENT_RUNTIME["required"]})
        files[tool.AGENT_RUNTIME["release_marker"]] = b'{"release": true}\n'  # ANGELO_REQUIRE_AGENT_RUNTIME
    for relative, data in files.items():
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if relative.startswith(("Contents/Helpers/", "Contents/MacOS/")):
            path.chmod(0o755)
    keys_file = resources / release_checks.KEYS_IN_INSTALL
    keys_file.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"id": "2026-10", "public_key": "A" * 43 + "=", "status": "active"}] if keys else []
    keys_file.write_text(json.dumps({"keys": entries}))
    return app


class FakeTools:
    """codesign, security, spctl, ditto, hdiutil and notarytool as a release machine would answer them."""

    def __init__(self, identity_lines=None, notary="Accepted"):
        self.log = []
        self.identity_lines = identity_lines if identity_lines is not None else [
            '  1) 0123456789ABCDEF0123456789ABCDEF01234567 "%s"' % DEVELOPER_ID,
            '  2) 89ABCDEF0123456789ABCDEF0123456789ABCDEF "Apple Development: Someone (ZZZZZ99999)"',
            "     2 valid identities found"]
        self.notary = notary

    def __call__(self, argv, check=True, timeout=None, env=None):
        self.log.append(list(argv))
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["ditto", "-c"]:
            Path(argv[-1]).write_bytes(b"zip")
        elif argv[0] == "ditto":
            shutil.copytree(argv[1], argv[2], symlinks=True)
        elif argv[:2] == ["security", "find-identity"]:
            ok.stdout = "\n".join(self.identity_lines) + "\n"
        elif argv[:2] == ["codesign", "-dv"]:
            ok.stderr = ("Identifier=x\nCodeDirectory v=20500 size=1 flags=0x10000(runtime) hashes=1+1\n"
                         "Authority=%s\nAuthority=Developer ID Certification Authority\nAuthority=Apple Root CA\n"
                         "Timestamp=13 Oct 2026 at 20:00:00\nTeamIdentifier=ABCDE12345\n" % DEVELOPER_ID)
        elif argv[:3] == ["codesign", "-d", "--entitlements"]:  # What the file's latest signature was given.
            signed = [arguments for arguments in self.log if arguments[:2] == ["codesign", "--force"]
                      and arguments[-1] == argv[-1]]
            entitlements = (signed[-1][signed[-1].index("--entitlements") + 1]
                            if signed and "--entitlements" in signed[-1] else None)
            ok.stdout = Path(entitlements).read_text() if entitlements else ""
        elif argv[0] == "spctl":
            ok.returncode, ok.stderr = 3, "%s: rejected\nsource=Unnotarized Developer ID\n" % argv[-1]
        elif argv[:3] == ["xcrun", "notarytool", "submit"]:
            ok.stdout = json.dumps({"id": "11111111-2222", "status": self.notary, "message": "done"})
        return ok


class AcceptingTools(FakeTools):
    """The same machine after Apple accepted the submissions: Gatekeeper sees notarized code."""

    def __call__(self, argv, check=True, timeout=None, env=None):
        result = super().__call__(argv, check, timeout, env)
        if argv[0] == "spctl":
            result.returncode, result.stderr = 0, "%s: accepted\nsource=Notarized Developer ID\n" % argv[-1]
        return result


class ToolCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = make_app(self.folder / "build")


class MachOTests(ToolCase):
    def test_headers_load_commands_and_look_alikes(self):
        binary = self.folder / "lib.dylib"
        binary.write_bytes(mach_o(6, ["@rpath/a.dylib", "/usr/lib/libSystem.B.dylib"], ["@loader_path/../lib"]))
        info = macho.read(binary)
        self.assertEqual((info.file_type, info.archs), ("dylib", ("arm64",)))
        self.assertEqual(info.dylibs, ("/usr/lib/libSystem.B.dylib", "@rpath/a.dylib"))
        self.assertEqual(info.rpaths, ("@loader_path/../lib",))
        fat = self.folder / "fat.so"
        fat.write_bytes(universal((0x0100000C, mach_o(8)), (0x01000007, mach_o(8, cpu=0x01000007))))
        self.assertEqual((macho.read(fat).file_type, macho.read(fat).archs), ("bundle", ("arm64", "x86_64")))
        for name, data in (("java.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34xx"), ("text", b"#!/bin/sh\n"),
                           ("empty", b"")):
            (self.folder / name).write_bytes(data)
            self.assertIsNone(macho.read(self.folder / name), name)
        truncated = self.folder / "truncated"
        truncated.write_bytes(mach_o(6, ["@rpath/a.dylib"])[:40])
        with self.assertRaises(ValueError):
            macho.read(truncated)
        self.assertTrue(macho.is_system_path("/System/Library/Frameworks/Metal.framework/Metal"))
        self.assertFalse(macho.is_relative_path("/opt/homebrew/lib/libz.dylib"))


class StageAndCheckTests(ToolCase):
    def test_staging_drops_caches_and_build_leftovers_and_leaves_a_clean_bundle(self):
        runner = FakeTools()
        staged, removed = tool.stage(self.app, self.folder / "out", runner)
        self.assertEqual(sorted(removed), sorted([
            "Contents/Resources/.DS_Store", "Contents/Resources/5.2/scripts/startup/__pycache__",
            "Contents/Resources/5.2/python/lib/python3.13/config-3.13-darwin/python.o",
            "Contents/Resources/5.2/python/lib/python3.13/config-3.13-darwin/libpython3.13.a"]))
        self.assertTrue((self.app / "Contents/Resources/.DS_Store").exists(), "the build itself is never changed")
        code, links = tool.inventory(staged)
        self.assertEqual(tool.bundle_problems(staged, code, links), [])
        self.assertEqual(len(code), 7)
        with self.assertRaises(tool.ReleaseError):
            tool.stage(self.app, self.folder / "out", runner)

    def test_staging_keeps_the_build_s_precompiled_bytecode(self):
        """Checked hash-based .pyc (angelo_python_bytecode.py) ship sealed; Python's own timestamp ones never do."""
        cache = self.app / "Contents/Resources/5.2/python/lib/python3.13/encodings/__pycache__"
        cache.mkdir(parents=True)
        (cache / "utf_8.cpython-313.pyc").write_bytes(b"\xf3\r\r\n" + (3).to_bytes(4, "little") + b"\0" * 16)
        (cache / "aliases.cpython-313.pyc").write_bytes(b"\xf3\r\r\n" + b"\0" * 20)
        staged, removed = tool.stage(self.app, self.folder / "out", FakeTools())
        kept = staged / cache.relative_to(self.app)
        self.assertEqual(sorted(p.name for p in kept.iterdir()), ["utf_8.cpython-313.pyc"])
        self.assertIn(str((cache / "aliases.cpython-313.pyc").relative_to(self.app)), removed)

    def test_what_cannot_be_signed_is_refused(self):
        lib = self.app / "Contents/Resources/lib"
        (lib / "libbad.dylib").write_bytes(mach_o(6, ["/Users/builder/lib/libz.dylib"], ["/Users/builder/lib"]))
        (lib / "leak.o").write_bytes(mach_o(1))
        os.symlink("/tmp", lib / "outside")
        (self.app / "Contents/Frameworks/Some.framework").mkdir(parents=True)
        problems = "\n".join(tool.bundle_problems(self.app, *tool.inventory(self.app)))
        for fragment in ("loads /Users/builder/lib/libz.dylib", "absolute LC_RPATH /Users/builder/lib",
                         "leak.o is a Mach-O object file", "links outside the bundle", "Some.framework is a nested"):
            self.assertIn(fragment, problems)
        (lib / "broken.dylib").write_bytes(mach_o(6, ["@rpath/a.dylib"])[:40])
        with self.assertRaises(tool.ReleaseError):
            tool.inventory(self.app)

    def test_another_product_or_version_and_an_empty_key_list_are_refused(self):
        other = make_app(self.folder / "old", version="0.9.0", keys=False)
        _main, info = tool.bundle_executable(other)
        self.assertIn("CFBundleShortVersionString", "".join(tool.identity_problems(other, info)))
        self.assertTrue(release_checks.update_key_problems(other))
        self.assertEqual(release_checks.update_key_problems(other, allow_none=True), [])
        self.assertEqual(release_checks.update_key_problems(self.app), [])

    def test_a_shipped_lfs_pointer_is_found(self):
        # The app templates' startup files once shipped as 130-byte LFS stubs; the signer refuses any.
        self.assertEqual(release_checks.lfs_pointer_files(self.app), [])
        stub = self.app / "Contents/Resources/5.2/scripts/startup/bl_app_templates_system/VFX/startup.blend"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_bytes(release_checks.LFS_POINTER + b"oid sha256:" + b"0" * 64 + b"\nsize 4096\n")
        self.assertEqual(release_checks.lfs_pointer_files(self.app),
                         ["Contents/Resources/5.2/scripts/startup/bl_app_templates_system/VFX/startup.blend"])
        self.assertEqual(release_checks.main(["lfs-pointers", str(self.app)]), 1)


class SigningTests(ToolCase):
    def test_inside_out_order_and_flags(self):
        staged, _ = tool.stage(self.app, self.folder / "out", FakeTools())
        code, _links = tool.inventory(staged)
        plan = tool.signing_plan(staged, code)
        names = [(str(path.relative_to(staged.parent)), kind) for path, kind in plan]
        self.assertEqual(names[-2:], [("Angelo.app/Contents/PlugIns/blender-thumbnailer.appex", "appex"),
                                      ("Angelo.app", "app")])
        loose = dict(names[:-2])
        self.assertNotIn("Angelo.app/Contents/MacOS/Angelo", loose, "a bundle's executable is signed with it")
        self.assertEqual(loose["Angelo.app/Contents/Resources/5.2/python/bin/python3.13"], "executable")
        self.assertEqual(loose["Angelo.app/Contents/Helpers/codex"], "agent_runtime")
        self.assertEqual(loose["Angelo.app/Contents/Helpers/codex-code-mode-host"], "agent_runtime")
        self.assertEqual(loose["Angelo.app/Contents/Resources/lib/libtbb.dylib"], "library")
        depths = [len(Path(name).parts) for name, _kind in names[:-2]]
        self.assertEqual(depths, sorted(depths, reverse=True), "deepest first")
        commands = {kind: tool.codesign_command(path, kind, DEVELOPER_ID) for path, kind in plan}
        for kind, argv in commands.items():
            self.assertIn("--timestamp", argv)
            self.assertEqual(argv[argv.index("--options") + 1], "runtime")
        self.assertNotIn("--entitlements", commands["library"])
        for kind, entitlements in (("executable", tool.APP_ENTITLEMENTS), ("app", tool.APP_ENTITLEMENTS),
                                   ("appex", tool.APPEX_ENTITLEMENTS),
                                   ("agent_runtime", tool.AGENT_RUNTIME_ENTITLEMENTS)):
            self.assertEqual(commands[kind][commands[kind].index("--entitlements") + 1], str(entitlements))
        self.assertIn("--timestamp=none", tool.codesign_command(staged, "app", "-"), "ad hoc cannot be timestamped")

    def test_only_one_developer_id_application_identity_signs(self):
        tool.check_identity(DEVELOPER_ID, None, FakeTools())
        tool.check_identity("0123456789ABCDEF0123456789ABCDEF01234567", None, FakeTools())
        tool.check_identity("-", None, None)
        for identity in ("Apple Development: Someone", "Developer ID Application: Nobody", "Hyperbond"):
            runner = FakeTools(identity_lines=FakeTools().identity_lines + [
                '  3) 1111111111111111111111111111111111111111 "Developer ID Installer: Hyperbond Studio"'])
            with self.subTest(identity=identity), self.assertRaises(tool.ReleaseError):
                tool.check_identity(identity, None, runner)

    def test_the_entitlements_are_the_measured_minimum(self):
        app = plistlib.loads(tool.APP_ENTITLEMENTS.read_bytes())
        self.assertEqual(app, {"com.apple.security.cs.allow-unsigned-executable-memory": True,
                               "com.apple.security.cs.disable-library-validation": True,
                               "com.apple.security.device.audio-input": True,
                               "com.apple.security.device.camera": True})
        self.assertEqual(plistlib.loads(tool.APPEX_ENTITLEMENTS.read_bytes()),
                         {"com.apple.security.app-sandbox": True,
                          "com.apple.security.files.user-selected.read-only": True})
        # The agent runtime: OpenAI's own set for codex and codex-code-mode-host (V8's JIT), nothing more.
        self.assertEqual(plistlib.loads(tool.AGENT_RUNTIME_ENTITLEMENTS.read_bytes()),
                         {"com.apple.security.cs.allow-jit": True,
                          "com.apple.security.cs.allow-unsigned-executable-memory": True})

    def test_only_the_agent_runtimes_two_executables_get_its_entitlements(self):
        staged, _ = tool.stage(self.app, self.folder / "out", FakeTools())
        (staged / "Contents/Resources/lib/codex").write_bytes(mach_o(2))  # The same name elsewhere is no runtime.
        plan = dict(tool.signing_plan(staged, tool.inventory(staged)[0]))
        runtime = sorted(str(path.relative_to(staged)) for path, kind in plan.items() if kind == "agent_runtime")
        self.assertEqual(runtime, ["Contents/Helpers/codex", "Contents/Helpers/codex-code-mode-host"])
        self.assertEqual(plan[staged / "Contents/Resources/lib/codex"], "executable")

    def test_a_signature_with_other_entitlements_is_refused(self):
        staged, _ = tool.stage(self.app, self.folder / "out", FakeTools())
        plan = tool.signing_plan(staged, tool.inventory(staged)[0])
        runner = FakeTools()
        for path, kind in plan:
            runner(tool.codesign_command(path, kind, DEVELOPER_ID))
        tool.verify_signatures(staged, plan, DEVELOPER_ID, runner)
        host = staged / "Contents/Helpers/codex-code-mode-host"
        runner(tool.codesign_command(host, "executable", DEVELOPER_ID))  # The app's set instead of the runtime's.
        with self.assertRaises(tool.ReleaseError) as caught:
            tool.verify_signatures(staged, plan, DEVELOPER_ID, runner)
        self.assertIn("codex-code-mode-host's entitlements differ", str(caught.exception))


class FlowTests(ToolCase):
    def run_tool(self, *extra, runner=None):
        runner = runner or FakeTools()
        argv = ["--app", str(self.app), "--out", str(self.folder / "out")] + list(extra)
        self.errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(self.errors):
            code = tool.main(argv, runner=runner, platform="darwin")
        return code, runner

    def test_developer_id_signing_without_a_disk_image(self):
        code, runner = self.run_tool("--identity", DEVELOPER_ID, "--no-dmg")
        self.assertEqual(code, 0)
        receipt = json.loads((self.folder / "out" / tool.RECEIPT).read_text())
        self.assertEqual((receipt["signing"]["files"], receipt["signing"]["team"]), (7, "ABCDE12345"))
        self.assertEqual(receipt["signing"]["entitlements"], sorted(plistlib.loads(tool.APP_ENTITLEMENTS.read_bytes())))
        self.assertEqual(receipt["signing"]["agent_runtime"], {
            "files": ["Contents/Helpers/codex", "Contents/Helpers/codex-code-mode-host"],
            "entitlements": ["com.apple.security.cs.allow-jit", "com.apple.security.cs.allow-unsigned-executable-memory"]})
        signs = [argv for argv in runner.log if argv[:2] == ["codesign", "--force"]]
        self.assertEqual(len(signs), 7)
        for argv in signs:
            if "/Contents/Helpers/" in argv[-1]:
                self.assertEqual(argv[argv.index("--entitlements") + 1], str(tool.AGENT_RUNTIME_ENTITLEMENTS))
        self.assertTrue(signs[-1][-1].endswith("/Angelo.app"))
        self.assertIn(["codesign", "--verify", "--deep", "--strict", "--verbose=2", signs[-1][-1]], runner.log)
        arguments = {argument for argv in runner.log for argument in argv}
        for secret_flag in ("--password", "--apple-id", "--key", "--key-id", "--issuer"):
            self.assertNotIn(secret_flag, arguments, "no credential ever reaches a command line")

    def test_refusals_before_anything_is_signed(self):
        cases = {
            ("--identity", "-"): "cannot be notarized",
            ("--identity", DEVELOPER_ID): "--notary-profile",
            ("--identity", DEVELOPER_ID, "--notary-profile", "p", "--allow-no-update-key"): "must trust the update key",
            ("--identity", "Apple Development: Someone", "--no-dmg"): "Developer ID Application",
        }
        for extra, fragment in cases.items():
            with self.subTest(extra=extra):
                code, runner = self.run_tool(*extra)
                self.assertEqual(code, 1)
                self.assertIn(fragment, self.errors.getvalue())
                self.assertFalse([argv for argv in runner.log if argv[:2] == ["codesign", "--force"]])
        self.app = make_app(self.folder / "unkeyed", keys=False)
        self.assertEqual(self.run_tool("--identity", DEVELOPER_ID, "--no-dmg")[0], 1)
        self.assertIn("no active update-signing key", self.errors.getvalue())

    def test_a_release_without_the_agent_runtime_is_refused_and_a_rehearsal_says_so(self):
        self.app = make_app(self.folder / "bare", agent_runtime=False)
        code, runner = self.run_tool("--identity", DEVELOPER_ID, "--no-dmg")
        self.assertEqual(code, 1)
        self.assertIn("a release bundles Angelo's agent runtime", self.errors.getvalue())
        self.assertFalse([argv for argv in runner.log if argv[:2] == ["codesign", "--force"]])
        code, _runner = self.run_tool("--identity", DEVELOPER_ID, "--notary-profile", "p", "--allow-no-agent-runtime")
        self.assertEqual(code, 1)
        self.assertIn("bundles the agent runtime", self.errors.getvalue())
        self.assertEqual(self.run_tool("--identity", "-", "--no-dmg", "--allow-no-agent-runtime")[0], 0)

    def test_a_release_without_its_release_marker_is_refused(self):
        (self.app / tool.AGENT_RUNTIME["release_marker"]).unlink()
        self.assertEqual(self.run_tool("--identity", DEVELOPER_ID, "--no-dmg")[0], 1)
        self.assertIn("agent-runtime-release.json is missing: build with ANGELO_REQUIRE_AGENT_RUNTIME=ON",
                      self.errors.getvalue())

    def test_an_incomplete_or_joined_agent_runtime_is_refused(self):
        (self.app / "Contents/Helpers/codex-code-mode-host").unlink()
        self.assertEqual(self.run_tool("--identity", DEVELOPER_ID, "--no-dmg")[0], 1)
        self.assertIn("lacks codex-code-mode-host", self.errors.getvalue())
        self.app = make_app(self.folder / "joined")
        (self.app / "Contents/Helpers/other-tool").write_bytes(mach_o(2))
        self.assertEqual(self.run_tool("--identity", DEVELOPER_ID, "--no-dmg", "--allow-no-agent-runtime")[0], 1)
        self.assertIn("other-tool, which is not part of the pinned agent runtime", self.errors.getvalue())


class NotarizationTests(ToolCase):
    def test_accepted_is_stapled_and_assessed_and_refused_fetches_the_log(self):
        image = self.folder / "Angelo.dmg"
        image.write_bytes(b"image")
        runner = AcceptingTools()
        result = tool.notarize(image, "angelo-notary", None, self.folder, runner)
        self.assertEqual(result["status"], "Accepted")
        submitted = runner.log[0]
        self.assertEqual(submitted[:3], ["xcrun", "notarytool", "submit"])
        self.assertEqual(submitted[submitted.index("--keychain-profile") + 1], "angelo-notary")
        self.assertIn(["xcrun", "stapler", "staple", str(image)], runner.log)
        self.assertIn(["xcrun", "stapler", "validate", str(image)], runner.log)
        refused = FakeTools(notary="Invalid")
        with self.assertRaises(tool.ReleaseError) as caught:
            tool.notarize(image, "angelo-notary", None, self.folder, refused)
        self.assertIn("notary-log.json", str(caught.exception))
        self.assertEqual(refused.log[-1][:4], ["xcrun", "notarytool", "log", "11111111-2222"])
        self.assertFalse([argv for argv in refused.log if "stapler" in argv])

    def test_the_app_is_submitted_as_a_zip_and_the_ticket_stapled_to_the_app(self):
        app = self.folder / "Angelo.app"
        runner = AcceptingTools()
        result = tool.notarize_app(app, "angelo-notary", None, self.folder, runner)
        self.assertEqual((result["status"], result["stapled"]), ("Accepted", True))
        zipped, submitted = runner.log[0], runner.log[1]
        self.assertEqual(zipped[:3], ["ditto", "-c", "-k"])
        self.assertIn("--keepParent", zipped)
        self.assertEqual(submitted[:4], ["xcrun", "notarytool", "submit", zipped[-1]])
        self.assertFalse(Path(zipped[-1]).exists(), "the submitted zip is not left behind")
        self.assertEqual(runner.log[2:4], [["xcrun", "stapler", "staple", str(app)],
                                           ["xcrun", "stapler", "validate", str(app)]])
        self.assertEqual(runner.log[4][:4], ["spctl", "--assess", "--type", "execute"])
        refused = FakeTools(notary="Invalid")
        with self.assertRaises(tool.ReleaseError):
            tool.notarize_app(app, "angelo-notary", None, self.folder, refused)
        self.assertFalse([argv for argv in refused.log if "stapler" in argv])
        with self.assertRaises(tool.ReleaseError) as caught:
            tool.notarize_app(app, "angelo-notary", None, self.folder, FakeTools())
        self.assertIn("notarized app", str(caught.exception))

    def test_the_release_staples_the_app_before_the_image_and_checks_the_ticket_inside(self):
        built = []

        def build(staged, out, work, identity, styled, runner):
            built.append([argv for argv in runner.log if argv[:3] == ["xcrun", "stapler", "staple"]])
            image = out / "Angelo-1.0.0-macos-arm64.dmg"
            image.write_bytes(b"image")
            return image, None

        runner = AcceptingTools()
        argv = ["--app", str(self.app), "--out", str(self.folder / "out"), "--identity", DEVELOPER_ID,
                "--notary-profile", "angelo-notary", "--unstyled-dmg"]
        with mock.patch.object(tool.dmg_macos, "build", build), \
                mock.patch.object(tool.dmg_macos, "check_contents", return_value=["accepted"]) as contents, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(argv, runner=runner, platform="darwin"), 0)
        staged = str((self.folder / "out" / "Angelo.app").resolve())
        self.assertEqual(built, [[["xcrun", "stapler", "staple", staged]]], "the image holds the stapled app")
        submitted = [argv[3] for argv in runner.log if argv[:3] == ["xcrun", "notarytool", "submit"]]
        self.assertEqual(len(submitted), 2)
        self.assertTrue(submitted[0].endswith(".zip"))
        self.assertTrue(submitted[1].endswith(".dmg"))
        self.assertTrue(contents.call_args.kwargs["gatekeeper"])
        receipt = json.loads((self.folder / "out" / tool.RECEIPT).read_text())
        self.assertTrue(receipt["notarized"])
        self.assertEqual((receipt["app_notarization"]["status"], receipt["app_notarization"]["stapled"]),
                         ("Accepted", True))


class DiskImageLayoutTests(unittest.TestCase):
    def ds_store(self, bounds, icons, icon_size=128.0):
        """The records the window script makes Finder write, as a .DS_Store holds them."""
        def record(name, code, blob):
            utf16 = name.encode("utf-16-be")
            return struct.pack(">I", len(name)) + utf16 + code + b"blob" + struct.pack(">I", len(blob)) + blob
        data = record(".", b"bwsp", plistlib.dumps({"WindowBounds": bounds}, fmt=plistlib.FMT_BINARY))
        data += record(".", b"icvp", plistlib.dumps({"iconSize": icon_size, "backgroundType": 2,
                                                     "backgroundImageAlias": b"alias"}, fmt=plistlib.FMT_BINARY))
        for name, (x, y) in icons.items():
            data += record(name, b"Iloc", struct.pack(">II", x, y) + b"\xff\xff\xff\xff\xff\xff\x00\x00")
        return b"\x00\x00\x00\x01Bud1" + data

    def test_the_stored_window_must_be_the_one_the_script_lays_out(self):
        expected = dmg_macos.expected_layout(dmg_macos.window_script(IDENTITY["bundle"]["macos_executable"]))
        self.assertEqual(expected["size"], (540, 372))
        self.assertEqual(expected["icons"], {"Angelo.app": (135, 190), "Applications": (400, 190)})
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder, ".DS_Store")
            store.write_bytes(self.ds_store("{{100, 1018}, {540, 372}}", expected["icons"]))
            self.assertEqual(dmg_macos.layout_problems(dmg_macos.stored_layout(store), expected), [])
            # What Finder once kept on the full image: the icons placed, the window at its default size.
            store.write_bytes(self.ds_store("{{1010, 417}, {920, 464}}", expected["icons"]))
            self.assertEqual(dmg_macos.layout_problems(dmg_macos.stored_layout(store), expected),
                             ["window (920, 464), expected (540, 372)"])
            store.write_bytes(self.ds_store("{{100, 1018}, {540, 372}}", {"Angelo.app": (10, 10)}))
            self.assertEqual(len(dmg_macos.layout_problems(dmg_macos.stored_layout(store), expected)), 2)
        self.assertEqual(dmg_macos.layout_problems(None, expected), ["Finder stored no window layout"])


if __name__ == "__main__":
    unittest.main()

    def test_the_writable_image_has_room_for_many_files_and_hfs_overhead(self):
        # 1.1 GB in ~6,000 files no longer fit in the byte total plus 64 MiB once the agent runtime was bundled.
        with tempfile.TemporaryDirectory() as folder:
            content = Path(folder)
            (content / "big.bin").write_bytes(b"\0" * (3 * 2 ** 20))
            for index in range(600):
                (content / ("small-%d.txt" % index)).write_bytes(b"x")
            os.symlink("/Applications", content / "Applications")
            bytes_mb = (3 * 2 ** 20 + 600) // 2 ** 20
            allocated_mb = (3 * 2 ** 20 + 600 * 4096 + 601 * 4096) // 2 ** 20
            size = dmg_macos.image_megabytes(content)
            self.assertGreaterEqual(size, allocated_mb + 128)
            self.assertGreater(size, bytes_mb + 64)
