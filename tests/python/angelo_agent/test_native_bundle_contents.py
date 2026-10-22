# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the installed app carries from scripts/angelo, the bundled Python's build leftovers, and the agent runtime.

Dependency-free. ``source/creator/CMakeLists.txt`` installs all of ``scripts/`` except ``scripts/angelo``,
then exactly the commands in ``ANGELO_RUNTIME_SCRIPTS``: the files a person runs with the bundled Python.
Release, signing, cloud and Windows validation scripts must never ship. The rule is checked by reading it
and, with CMake on PATH (CI has it), by installing a copy of ``scripts/angelo``'s real file names through the
rule's own text. The runtime commands must only need the add-on and the identity module, which ship.

The agent runtime (the patched Codex build, ``agent_runtime.json``) comes from ``ANGELO_AGENT_RUNTIME_DIR``:
``build_files/cmake/angelo_agent_runtime.cmake`` installs exactly the pinned files under their upstream names
into ``Angelo.app/Contents/Helpers`` or ``<install>/agent-runtime``, refuses a folder missing one, and with
``ANGELO_REQUIRE_AGENT_RUNTIME`` (the release scripts) refuses a configure without the folder.
``release_checks.agent_runtime_problems`` is the release check of such a folder or an installed app's: arm64
Mach-O executables on macOS; on Windows x64 PE executables, with OpenAI's own signature still embedded in its
unchanged release files (the pin's ``upstream_signed``).
"""

import ast
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
CMAKE = ROOT / "source/creator/CMakeLists.txt"
TOOLS = ROOT / "scripts/angelo"
RUNTIME_MODULE = ROOT / "build_files/cmake/angelo_agent_runtime.cmake"
PIN_FILE = ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json"
PIN = json.loads(PIN_FILE.read_text(encoding="utf-8"))
sys.path.insert(0, str(TOOLS))
import release_checks  # noqa: E402
RUNTIME = {"angelo_cli.py", "angelo_mcp.py", "migrate_user_folder.py", "recover_connections.py"}
# Shipped Python that may name a scripts/angelo file only in documentation, never in code.
SHIPPED = ("scripts/addons_core/angelo_agent", "scripts/modules", "scripts/startup")


def cmake_text():
    return CMAKE.read_text(encoding="utf-8")


def scripts_install_block(text):
    """The install(DIRECTORY ${CMAKE_SOURCE_DIR}/scripts ...) call and the runtime-scripts block after it."""
    start = text.index("  install(\n    DIRECTORY ${CMAKE_SOURCE_DIR}/scripts\n")
    end = text.index("  unset(ANGELO_RUNTIME_SCRIPTS)\n", start)
    return text[start:end]


def runtime_list(text):
    match = re.search(r"set\(ANGELO_RUNTIME_SCRIPTS\n(.*?)\n  \)", text, re.S)
    return {line.strip() for line in match.group(1).splitlines() if line.strip()}


class RuleTests(unittest.TestCase):
    def test_scripts_angelo_is_excluded_and_only_the_runtime_commands_are_installed(self):
        block = scripts_install_block(cmake_text())
        self.assertIn('REGEX "/scripts/angelo$" EXCLUDE', block)
        self.assertEqual(runtime_list(block), RUNTIME)
        self.assertIn("DESTINATION ${TARGETDIR_VER}/scripts/angelo", block)
        for name in RUNTIME:
            self.assertTrue((TOOLS / name).is_file(), name)

    def test_release_tools_exist_and_are_not_runtime(self):
        tools = {p.name for p in TOOLS.iterdir()}
        for name in ("build_release_macos.sh", "build_release_windows.ps1", "sign_notarize_macos.py",
                     "sign_windows.ps1", "sign_update_feed.py", "source_release.py", "gcp_windows_validate.sh",
                     "dev_codesign.sh", "changelog.py", "i18n"):
            self.assertIn(name, tools)
            self.assertNotIn(name, RUNTIME)

    def test_runtime_commands_import_only_what_ships(self):
        for name in RUNTIME:
            tree = ast.parse((TOOLS / name).read_text(encoding="utf-8"))
            modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names}
            modules |= {node.module.split(".")[0] for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
            local = {p.stem for p in TOOLS.glob("*.py")}
            self.assertFalse(modules & local, f"{name} imports a scripts/angelo module that does not ship")

    def test_shipped_code_never_loads_a_development_tool(self):
        """Docstrings may point people at scripts/angelo; code may not open, import or run anything there."""
        for root in SHIPPED:
            for path in (ROOT / root).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                              if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                              and node.body and isinstance(node.body[0], ast.Expr)
                              and isinstance(node.body[0].value, ast.Constant)}
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                            and "scripts/angelo/" in node.value):
                        name = node.value.split("scripts/angelo/", 1)[1].split()[0]
                        self.assertIn(name, RUNTIME, f"{path.relative_to(ROOT)} uses scripts/angelo/{name}")

    def test_the_macos_python_install_drops_its_build_only_files(self):
        text = cmake_text()
        function = text[text.index("function(install_dir from to)"):text.index("endfunction()")]
        self.assertIn('PATTERN "config-${PYTHON_VERSION}-darwin/*.a" EXCLUDE', function)
        self.assertIn('PATTERN "config-${PYTHON_VERSION}-darwin/python.o" EXCLUDE', function)


@unittest.skipUnless(shutil.which("cmake"), "CMake is required to run the install rule")
class InstallTests(unittest.TestCase):
    def test_installing_through_the_rule_ships_only_the_runtime_commands(self):
        block = scripts_install_block(cmake_text()).replace("${FREESTYLE_EXCLUDE_CONDITIONAL}", "_freestyle/*")
        with tempfile.TemporaryDirectory() as folder:
            project, prefix = Path(folder, "src"), Path(folder, "prefix")
            for path in TOOLS.rglob("*"):  # Every real name in scripts/angelo, empty.
                if path.is_file() and "__pycache__" not in path.parts:
                    target = project / "scripts/angelo" / path.relative_to(TOOLS)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("")
            for other in ("addons_core/angelo_agent/update_keys.json", "modules/angelo_identity.py",
                          "startup/angelo_default_layout.py", "addons_core/angelo_agent/angelo/keep.txt"):
                (project / "scripts" / other).parent.mkdir(parents=True, exist_ok=True)
                (project / "scripts" / other).write_text("")
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\nproject(rule NONE)\nset(TARGETDIR_VER 5.2)\n" + block
                + "\n", encoding="utf-8")
            for argv in (["cmake", "-S", str(project), "-B", str(Path(folder, "build"))],
                         ["cmake", "--install", str(Path(folder, "build")), "--prefix", str(prefix)]):
                done = subprocess.run(argv, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            installed = prefix / "5.2/scripts"
            self.assertEqual({p.name for p in (installed / "angelo").iterdir()}, RUNTIME)
            for other in ("addons_core/angelo_agent/update_keys.json", "modules/angelo_identity.py",
                          "startup/angelo_default_layout.py", "addons_core/angelo_agent/angelo/keep.txt"):
                self.assertTrue((installed / other).is_file(), f"{other} must still ship")



def arm64_executable():
    """A minimal arm64 Mach-O executable header (what macho.read needs)."""
    return struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0)


def pe_executable(machine=0x8664, dll=False, signer=None):
    """A minimal PE32+ file (what release_checks.pe_info reads): the DOS stub's pointer, the COFF header, the
    optional header's magic and, with ``signer``, a certificate table (data directory 4) whose stand-in signature
    names that signer, as an Authenticode signature's certificates do."""
    offset, optional_size = 0x80, 240
    head = bytearray(offset + 24 + optional_size)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, offset)
    head[offset:offset + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, offset + 4, machine, 1, 0, 0, 0, optional_size, 0x22 | (0x2000 if dll else 0))
    struct.pack_into("<H", head, offset + 24, 0x20B)
    if signer is not None:
        table = struct.pack("<IHH", 8 + 64, 0x0200, 0x0002) + (b"0\x82\x00\x40" + signer.encode()).ljust(64, b"\0")
        struct.pack_into("<II", head, offset + 24 + 112 + 8 * 4, len(head), len(table))
        head += table
    return bytes(head)


def windows_file(name):
    """A stand-in for a pinned Windows file: OpenAI's unchanged ones carry OpenAI's signature, codex.exe none."""
    files = PIN["platforms"]["windows"]
    return pe_executable(signer=files["upstream_signer"] if name in files["upstream_signed"] else None)


def runtime_folder(root, platform, names=None, data=None):
    folder = Path(root, "runtime-" + platform)
    folder.mkdir(parents=True, exist_ok=True)
    for name in names if names is not None else PIN["platforms"][platform]["required"]:
        path = folder / name
        path.write_bytes(data if data is not None else (arm64_executable() if platform == "macos" else windows_file(name)))
        path.chmod(0o755)
    return folder


class AgentRuntimeRuleTests(unittest.TestCase):
    def test_the_app_installs_the_runtime_where_the_pin_says(self):
        text = cmake_text()
        self.assertIn('angelo_install_agent_runtime(macos "${ANGELO_MACOS_EXECUTABLE}.app")', text)
        self.assertIn('angelo_install_agent_runtime(windows "." COMPONENT ${ANGELO_WIN_INSTALL_COMPONENT})', text)
        root = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertLess(root.index("include(build_files/cmake/angelo_identity.cmake)"),
                        root.index("include(build_files/cmake/angelo_agent_runtime.cmake)"))
        self.assertEqual(PIN["platforms"]["macos"]["folder"], "Contents/Helpers", "Apple's place for helper tools")
        self.assertEqual(PIN["platforms"]["windows"]["folder"], "agent-runtime")

    def test_release_builds_require_it_and_development_builds_may_omit_it(self):
        release = (TOOLS / "build_release_macos.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ -z "$AGENT_RUNTIME" ]]; then', release)
        self.assertIn('release_checks.py" agent-runtime "$AGENT_RUNTIME" --platform macos', release)
        self.assertIn('-DANGELO_AGENT_RUNTIME_DIR="$AGENT_RUNTIME" -DANGELO_REQUIRE_AGENT_RUNTIME=ON', release)
        self.assertLess(release.index("agent-runtime \"$AGENT_RUNTIME\""), release.index("cmake -S"),
                        "checked before configuring")
        native = (TOOLS / "build_native.sh").read_text(encoding="utf-8")
        self.assertIn('-DANGELO_AGENT_RUNTIME_DIR="$AGENT_RUNTIME"', native)
        self.assertNotIn("ANGELO_REQUIRE_AGENT_RUNTIME", native)


class AgentRuntimeCheckTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def runner(self, version=PIN["version"], features=tuple(PIN["features"])):
        calls = []

        def run(argv, check=False, timeout=None, env=None):
            calls.append((argv, env))
            if argv[1] == "--version":
                return SimpleNamespace(returncode=0, stdout="codex-cli %s\n" % version, stderr="")
            out = Path(argv[argv.index("--out") + 1])
            for name in release_checks.AGENT_RUNTIME_PARAMS:
                (out / "v2").mkdir(parents=True, exist_ok=True)
                (out / "v2" / (name + ".json")).write_text(json.dumps({"properties": {f: {} for f in features}}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        run.calls = calls
        return run

    def test_a_complete_pinned_patched_runtime_passes(self):
        for platform in ("macos", "windows"):
            with self.subTest(platform=platform):
                folder = runtime_folder(self.root, platform)
                run = self.runner()
                self.assertEqual(release_checks.agent_runtime_problems(folder, platform, run), [])
                (version, env), (schema, _env) = run.calls
                self.assertEqual(version[1:], ["--version"])
                self.assertEqual(schema[1:4], ["app-server", "generate-json-schema", "--experimental"])
                self.assertNotEqual(Path(env["CODEX_HOME"]), Path.home() / ".codex", "never the person's profile")

    def test_missing_wrong_or_joined_files_are_refused(self):
        self.assertIn("is missing: a release bundles", release_checks.agent_runtime_problems(self.root / "none", "macos")[0])
        folder = runtime_folder(self.root, "macos", ["codex"])
        self.assertIn("lacks codex-code-mode-host", "\n".join(release_checks.agent_runtime_problems(folder, "macos")))
        folder = runtime_folder(self.root, "macos", data=b"#!/bin/sh\n")
        self.assertIn("not an arm64 Mach-O executable", release_checks.agent_runtime_problems(folder, "macos")[0])
        folder = runtime_folder(self.root, "windows", data=b"not a PE")
        self.assertIn("not an x64 Windows executable", release_checks.agent_runtime_problems(folder, "windows")[0])
        folder = runtime_folder(self.root / "joined", "macos")
        (folder / "notes.txt").write_text("x")
        self.assertEqual(release_checks.agent_runtime_problems(folder, "macos"), [], "a source folder may hold more")
        self.assertIn("notes.txt, which is not part of the pinned agent runtime",
                      release_checks.agent_runtime_problems(folder, "macos", exclusive=True)[0])

    def test_windows_files_must_be_x64_executables(self):
        """MZ alone was the old check: an arm64 build, a DLL or a 32-bit image under a pinned name is refused."""
        for label, data in (("arm64", pe_executable(machine=0xAA64)), ("dll", pe_executable(dll=True)),
                            ("pe32", pe_executable()[:0x80 + 24] + struct.pack("<H", 0x10B) + pe_executable()[0x80 + 26:]),
                            ("stub", b"MZ" + bytes(200))):
            with self.subTest(label):
                folder = runtime_folder(self.root / label, "windows", ["codex.exe"], data=data)
                self.assertIn("codex.exe is not an x64 Windows executable",
                              release_checks.agent_runtime_problems(folder, "windows"))
        folder = runtime_folder(self.root / "good", "windows")
        self.assertEqual(release_checks.pe_info(folder / "codex.exe"), (0x8664, False, b""))
        self.assertEqual(release_checks.agent_runtime_problems(folder, "windows"), [])

    def test_openais_unchanged_windows_files_keep_openais_signature(self):
        """Angelo signs codex.exe only: the helpers ship as OpenAI released and signed them (doc/angelo/releasing.rst)."""
        files = PIN["platforms"]["windows"]
        self.assertEqual(files["upstream_signer"], "OpenAI OpCo, LLC")
        self.assertEqual(sorted(files["upstream_signed"]),
                         ["codex-code-mode-host.exe", "codex-command-runner.exe", "codex-windows-sandbox-setup.exe"])
        self.assertNotIn(files["required"][0], files["upstream_signed"], "codex.exe is Angelo's build")
        self.assertLessEqual(set(files["upstream_signed"]), set(files["required"]) | set(files["optional"]))
        everything = files["required"] + files["optional"]
        folder = runtime_folder(self.root / "all", "windows", everything)
        self.assertEqual(release_checks.agent_runtime_problems(folder, "windows", exclusive=True), [])
        for label, data in (("unsigned", pe_executable()), ("re-signed", pe_executable(signer="Someone Else Ltd"))):
            with self.subTest(label):
                folder = runtime_folder(self.root / label, "windows", everything)
                (folder / "codex-command-runner.exe").write_bytes(data)
                problems = release_checks.agent_runtime_problems(folder, "windows")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("codex-command-runner.exe must carry OpenAI OpCo, LLC's own signature", problems[0])
        folder = runtime_folder(self.root / "absent", "windows")
        self.assertEqual(release_checks.agent_runtime_problems(folder, "windows"), [], "an absent optional file is fine")

    def test_another_version_or_a_stock_build_is_refused(self):
        folder = runtime_folder(self.root, "macos")
        self.assertIn("this source pins Codex", release_checks.agent_runtime_problems(
            folder, "macos", self.runner(version="0.162.0-alpha.2"))[0])
        stock = release_checks.agent_runtime_problems(folder, "macos", self.runner(features=()))
        self.assertEqual(len(stock), 3)
        self.assertIn("not the patched build", stock[0])

    def test_the_command_line_exits_with_the_verdict(self):
        folder = runtime_folder(self.root, "windows")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(release_checks.main(["agent-runtime", str(folder), "--platform", "windows", "--no-run"]), 0)
            self.assertEqual(release_checks.main(["agent-runtime", str(self.root / "none"), "--no-run"]), 1)
        self.assertIn("is missing", errors.getvalue())


@unittest.skipUnless(shutil.which("cmake"), "CMake is required to run the install rule")
class AgentRuntimeInstallTests(unittest.TestCase):
    """build_files/cmake/angelo_agent_runtime.cmake in a real configure and install."""

    def configure(self, platform, root, *definitions):
        project = Path(self.folder, "src")
        (project / "build_files/cmake").mkdir(parents=True, exist_ok=True)
        (project / "scripts/addons_core/angelo_agent").mkdir(parents=True, exist_ok=True)
        shutil.copy(RUNTIME_MODULE, project / "build_files/cmake")
        shutil.copy(PIN_FILE, project / "scripts/addons_core/angelo_agent")
        (project / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.19)\nproject(runtime NONE)\n"
            "include(build_files/cmake/angelo_agent_runtime.cmake)\n"
            'angelo_install_agent_runtime(%s "%s")\n' % (platform, root), encoding="utf-8")
        build = Path(self.folder, "build")
        shutil.rmtree(build, ignore_errors=True)
        return subprocess.run(["cmake", "-S", str(project), "-B", str(build)] + list(definitions),
                              capture_output=True, text=True), build

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def install(self, build):
        prefix = Path(self.folder, "prefix")
        shutil.rmtree(prefix, ignore_errors=True)
        done = subprocess.run(["cmake", "--install", str(build), "--prefix", str(prefix)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        return prefix

    def test_the_pinned_files_install_under_their_upstream_names(self):
        source = runtime_folder(self.folder, "macos")
        (source / "unrelated.txt").write_text("not part of the runtime")
        done, build = self.configure("macos", "Angelo.app", "-DANGELO_AGENT_RUNTIME_DIR=" + str(source))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("Agent runtime: Codex %s" % PIN["version"], done.stdout)
        installed = self.install(build)
        helpers = installed / "Angelo.app/Contents/Helpers"
        self.assertEqual(sorted(p.name for p in helpers.iterdir()), ["codex", "codex-code-mode-host"])
        self.assertTrue(all(p.stat().st_mode & 0o111 for p in helpers.iterdir()), "installed as programs")
        self.assertTrue(release_checks.release_marker_problems(installed / "Angelo.app", "macos"),
                        "A development build carries no release marker: it keeps path overrides")
        done, build = self.configure("macos", "Angelo.app", "-DANGELO_AGENT_RUNTIME_DIR=" + str(source),
                                     "-DANGELO_REQUIRE_AGENT_RUNTIME=ON")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        app = self.install(build) / "Angelo.app"
        self.assertEqual(release_checks.release_marker_problems(app, "macos"), [])
        marker = json.loads((app / PIN["platforms"]["macos"]["release_marker"]).read_text())
        self.assertEqual(marker, {"release": True, "agent_runtime": PIN["version"]})
        source = runtime_folder(self.folder, "windows", PIN["platforms"]["windows"]["required"])
        done, build = self.configure("windows", ".", "-DANGELO_AGENT_RUNTIME_DIR=" + str(source))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(sorted(p.name for p in (self.install(build) / "agent-runtime").iterdir()),
                         ["codex-code-mode-host.exe", "codex-command-runner.exe", "codex-windows-sandbox-setup.exe",
                          "codex.exe"], "both sandbox helpers are required on Windows")

    def test_a_windows_release_installs_all_four_files_and_its_marker(self):
        files = PIN["platforms"]["windows"]
        source = runtime_folder(self.folder, "windows", files["required"] + files["optional"])
        (source / "SHA256SUMS").write_text("not part of the runtime")
        (source / "PROVENANCE.txt").write_text("not part of the runtime")
        done, build = self.configure("windows", ".", "-DANGELO_AGENT_RUNTIME_DIR=" + str(source),
                                     "-DANGELO_REQUIRE_AGENT_RUNTIME=ON")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        installed = self.install(build)
        runtime = release_checks.agent_runtime_folder(installed, "windows")
        self.assertEqual(sorted(p.name for p in runtime.iterdir()), sorted(files["required"] + files["optional"]))
        for path in runtime.iterdir():
            self.assertEqual(path.read_bytes(), (source / path.name).read_bytes(), "installed unchanged: signatures intact")
        self.assertEqual(release_checks.agent_runtime_problems(runtime, "windows", exclusive=True), [])
        self.assertEqual(release_checks.release_marker_problems(installed, "windows"), [])
        self.assertEqual(json.loads((installed / files["release_marker"]).read_text()),
                         {"release": True, "agent_runtime": PIN["version"]})

    def test_a_folder_missing_a_file_and_a_release_without_a_folder_stop_the_configure(self):
        source = runtime_folder(self.folder, "macos", ["codex"])
        done, _build = self.configure("macos", "Angelo.app", "-DANGELO_AGENT_RUNTIME_DIR=" + str(source))
        self.assertNotEqual(done.returncode, 0)
        # CMake wraps a message at about 80 columns, where the folder's length puts the breaks (a short Linux /tmp
        # path broke "lacks" from the file name): read the words, not the lines.
        self.assertIn("lacks codex-code-mode-host", " ".join(done.stderr.split()))
        done, _build = self.configure("macos", "Angelo.app", "-DANGELO_REQUIRE_AGENT_RUNTIME=ON")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("A release bundles Angelo's agent runtime", " ".join(done.stderr.split()))
        done, build = self.configure("macos", "Angelo.app")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Agent runtime: none bundled", done.stdout)
        self.assertFalse((self.install(build) / "Angelo.app").exists(), "a development build bundles nothing")


if __name__ == "__main__":
    unittest.main()
