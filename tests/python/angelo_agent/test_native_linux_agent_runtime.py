# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bundled agent runtime (patched Codex) on Linux (doc/angelo/releasing.rst, "Agent runtime").

Covered: the pin names a Linux layout (folder, release marker, Rust target, the same two programs as macOS and the
glibc ceiling); the add-on finds the bundled ``codex`` from Angelo's Linux executable and from the add-on's own place
in the extracted folder, and a release marker makes it a release build; the
release check reads ELF headers itself (``elf_info``): it refuses a file that is not x86-64 ELF code, a binary that
needs a glibc newer than the ceiling or glibc's private interface, and a dynamically linked binary whose section
headers are gone, and it accepts static and glibc-2.28 builds; ``--platform linux`` is a choice. On a Linux host the
reader also reads a real system binary. No Blender, no network, nothing runs.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "scripts/angelo"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(TOOLS))
from angelo_agent import agent_runtime  # noqa: E402
import elf_info  # noqa: E402
import release_checks  # noqa: E402
from linux_elf_fixtures import EM_AARCH64, ET_EXEC, elf_file  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

PIN = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))


def write(path, data, executable=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def linux_runtime(folder, data=None):
    """A Linux runtime folder of ELF stand-ins; codex carries each pinned setting's literal, as the build does."""
    keys = b"".join(b"\0" + key.encode() for key in PIN.get("config_keys", ()))
    for index, name in enumerate(PIN["platforms"]["linux"]["required"]):
        write(Path(folder, name), (elf_file() + (keys if index == 0 else b"")) if data is None else data)
    return Path(folder)


class PinTests(unittest.TestCase):
    def test_the_pin_names_a_linux_layout(self):
        files = PIN["platforms"]["linux"]
        self.assertEqual(files["folder"], "agent-runtime")
        self.assertEqual(files["release_marker"], "agent-runtime-release.json")
        self.assertEqual(files["required"], ["codex", "codex-code-mode-host"],
                         "the same programs as macOS: Angelo turns Codex's shell off, so its bubblewrap is never used")
        self.assertEqual(files["optional"], [])
        self.assertEqual(files["rust_target"], "x86_64-unknown-linux-gnu")
        self.assertEqual(files["glibc_ceiling"], "2.28", "Rocky Linux 8, upstream Blender's Linux build floor")
        self.assertNotIn("libssl.so.1.1", files["system_libraries"])
        self.assertEqual(files["interpreter"], "/lib64/ld-linux-x86-64.so.2")
        self.assertEqual(files["version_ceilings"], {"GLIBC": "2.28", "GCC": "7.0.0"})
        self.assertIn("libc.so.6", files["system_libraries"])

    def test_platform_keys(self):
        self.assertEqual(agent_runtime.platform_key("linux"), "linux")
        self.assertEqual(agent_runtime.layout("linux"), PIN["platforms"]["linux"])
        self.assertIsNone(agent_runtime.platform_key("freebsd14"))
        self.assertIsNone(agent_runtime.layout("freebsd14"))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(agent_runtime._bundled.cache_clear)
        self.addCleanup(agent_runtime._release_build.cache_clear)
        agent_runtime._bundled.cache_clear()
        agent_runtime._release_build.cache_clear()
        self.root = Path(temp.name, "Angelo-2.1.0-linux-x64")
        self.binary = str(write(self.root / "angelo", b"\x7fELF"))
        self.module = str(self.root / "5.3/scripts/addons_core/angelo_agent/agent_runtime.py")

    def test_the_bundled_runtime_is_found_beside_the_executable_and_from_the_add_on(self):
        self.assertIsNone(agent_runtime.bundled("linux", self.binary), "a development build bundles none")
        agent_runtime._bundled.cache_clear()
        codex = linux_runtime(self.root / "agent-runtime") / "codex"
        self.assertEqual(agent_runtime.install_root("linux", self.binary), self.root)
        self.assertEqual(agent_runtime.install_root("linux", "", self.module), self.root)
        self.assertEqual(agent_runtime.bundled("linux", self.binary), str(codex))
        self.assertEqual(agent_runtime.bundled("linux", "", self.module), str(codex))
        self.assertIsNone(agent_runtime.bundled("freebsd14", self.binary), "no other platform bundles one")

    def test_the_release_marker_makes_a_release_build(self):
        linux_runtime(self.root / "agent-runtime")
        self.assertFalse(agent_runtime.release_build("linux", self.binary))
        agent_runtime._release_build.cache_clear()
        write(self.root / PIN["platforms"]["linux"]["release_marker"], b"{}", executable=False)
        self.assertTrue(agent_runtime.release_build("linux", self.binary))
        self.assertEqual(release_checks.release_marker_problems(self.root, "linux"), [])


class InstallTests(unittest.TestCase):
    # The call in source/creator/CMakeLists.txt's portable branch is lane A's (linux packaging) and tested there;
    # the function reads any platform the pin names.
    def test_the_cmake_function_takes_linux(self):
        text = (ROOT / "build_files/cmake/angelo_agent_runtime.cmake").read_text(encoding="utf-8")
        self.assertIn("angelo_install_agent_runtime(<macos|windows|linux> <root>", text)

    def test_the_build_script_builds_the_pinned_programs_for_the_pinned_target(self):
        script = (TOOLS / "build_agent_runtime_linux.sh").read_text(encoding="utf-8")
        self.assertIn("agent_runtime.json", script, "reads the pin, never a second copy")
        self.assertIn("release_checks.py\" agent-runtime", script, "checks what it built")
        self.assertIn("strip --strip-unneeded", script, "keeps section headers so the glibc check can read them")
        self.assertIn("_OPENSSL_STATIC=1", script, "OpenSSL's soname differs between distributions")
        # No builder folder in what ships (break-it review of fd6e818a9e7 SHOULD-5; re-review NIT-2, NIT-4).
        for needle in ("--remap-path-prefix=$pair", "-ffile-prefix-map=$pair", 'DESTDIR="$SSL/root"',
                       "--prefix=/usr/local", 'release_checks.py" builder-paths "$OUT"'):
            self.assertIn(needle, script)
        pairs = script[script.index("for pair in "):].split("\n", 1)[0]
        self.assertTrue(pairs.startswith('for pair in "$HOME=~"'), "the last matching map wins: the home goes first")


class ElfTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def read(self, data):
        return elf_info.read(write(self.folder / "file", data))

    def test_it_reads_machine_interpreter_libraries_runpaths_and_versions(self):
        info = self.read(elf_file(runpath="$ORIGIN/lib:/home/builder/lib", needs=(
            ("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.17", "GLIBC_2.28")), ("libm.so.6", ("GLIBC_2.27",)))))
        self.assertEqual(info.machine, "x86_64")
        self.assertEqual(info.file_type, "shared")
        self.assertEqual(info.interpreter, "/lib64/ld-linux-x86-64.so.2")
        self.assertEqual(info.needed, ("libc.so.6", "libm.so.6"))
        self.assertEqual(info.runpaths, ("$ORIGIN/lib", "/home/builder/lib"))
        self.assertEqual(elf_info.glibc_versions(info), ("2.2.5", "2.17", "2.27", "2.28"))
        self.assertEqual(elf_info.glibc_problems(info, "2.28"), [])

    def test_a_newer_glibc_or_its_private_interface_is_refused(self):
        newer = self.read(elf_file(needs=(("libc.so.6", ("GLIBC_2.17", "GLIBC_2.34")),)))
        self.assertIn("needs glibc 2.34, newer than the 2.28", elf_info.glibc_problems(newer, "2.28", "codex")[0])
        self.assertEqual(elf_info.glibc_problems(newer, "2.34"), [], "the ceiling is the pin's, not a constant")
        private = self.read(elf_file(needs=(("libc.so.6", ("GLIBC_2.17", "GLIBC_PRIVATE")),)))
        self.assertIn("GLIBC_PRIVATE", elf_info.glibc_problems(private, "2.28")[0])

    def test_static_and_unreadable_files(self):
        static = self.read(elf_file(file_type=ET_EXEC, interpreter="", needs=()))
        self.assertEqual((static.interpreter, elf_info.glibc_versions(static)), ("", ()))
        self.assertEqual(elf_info.glibc_problems(static, "2.28"), [], "a static build has no glibc floor")
        stripped = self.read(elf_file(sections=False))
        self.assertIn("no section headers", elf_info.glibc_problems(stripped, "2.28")[0])
        self.assertIsNone(self.read(b"#!/bin/sh\n"))
        self.assertIsNone(self.read(elf_file(elf_class=1)), "32-bit ELF is not Angelo's")
        with self.assertRaises(ValueError):
            self.read(elf_file()[:200])

    def test_the_floor_check_bounds_interpreter_search_folders_and_every_version(self):
        """Break-it review SHOULD-2: each of these passed a glibc-only check."""
        floor = ("/lib64/ld-linux-x86-64.so.2", {"GLIBC": "2.28", "GCC": "7.0.0"})
        problems = lambda data: elf_info.floor_problems(self.read(data), *floor, name="codex")  # noqa: E731
        self.assertEqual(problems(elf_file(needs=(("libc.so.6", ("GLIBC_2.17", "GLIBC_2.28")),
                                                  ("libgcc_s.so.1", ("GCC_3.0", "GCC_7.0.0")),),
                                           runpath="$ORIGIN/lib:${ORIGIN}/../lib")), [])
        refused = {
            "builder runpath": (elf_file(runpath="/home/builder/openssl/lib"), "searches /home/builder/openssl/lib"),
            "relative runpath": (elf_file(runpath="lib"), "searches lib for libraries"),
            "tmp runpath": (elf_file(runpath="/tmp/x"), "searches /tmp/x"),
            "nix interpreter": (elf_file(interpreter="/nix/store/abc-glibc/lib/ld-linux-x86-64.so.2"),
                                "program interpreter is /nix/store"),
            "newer libgcc_s": (elf_file(needs=(("libgcc_s.so.1", ("GCC_14.0",)),)), "needs GCC 14.0 from libgcc_s"),
            "newer glibc": (elf_file(needs=(("libc.so.6", ("GLIBC_2.34",)),)), "needs GLIBC 2.34 from libc.so.6"),
            "unknown prefix": (elf_file(needs=(("libssl.so.3", ("OPENSSL_3.0.0",)),)), "ceilings don't cover"),
            "private": (elf_file(needs=(("libc.so.6", ("GLIBC_PRIVATE",)),)), "GLIBC_PRIVATE"),
        }
        for label, (data, words) in refused.items():
            with self.subTest(label):
                self.assertIn(words, "\n".join(problems(data)))

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("ls"),
                         environment_skip("a real Linux binary (Linux)"))
    def test_a_real_system_binary(self):
        info = elf_info.read(os.path.realpath(shutil.which("ls")))
        self.assertIsNotNone(info)
        self.assertTrue(info.interpreter or not info.needed)
        if info.interpreter:
            self.assertTrue(elf_info.glibc_versions(info), "a dynamically linked system binary needs glibc versions")


class LoaderVariableTests(unittest.TestCase):
    """Break-it review SHOULD-4: Angelo's own runtime starts with no dynamic-loader variables."""

    INJECTED = {"PATH": "/usr/bin", "HOME": "/home/a", "LD_PRELOAD": "/tmp/x.so", "LD_LIBRARY_PATH": "/tmp",
                "LD_AUDIT": "/tmp/a.so", "GCONV_PATH": "/tmp/g", "GLIBC_TUNABLES": "x", "DYLD_INSERT_LIBRARIES": "y"}

    def test_without_loader(self):
        from angelo_agent import runtime_environment
        self.assertEqual(runtime_environment.without_loader(dict(self.INJECTED)), {"PATH": "/usr/bin", "HOME": "/home/a"})

    def test_the_bundled_runtime_gets_none_and_a_persons_own_codex_keeps_its_environment(self):
        from unittest import mock
        from angelo_agent import runtime_launch
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        with mock.patch.dict(agent_runtime.PIN, {"app_server_flags": [agent_runtime.IGNORE_USER_CONFIG]}), \
                mock.patch.object(runtime_launch, "child_environment", side_effect=lambda *a, **k: dict(self.INJECTED)), \
                mock.patch.object(runtime_launch, "profile_environment", side_effect=lambda env, *a, **k: env), \
                mock.patch.object(runtime_launch, "with_runtime_path", side_effect=lambda env, executable: env), \
                mock.patch.object(runtime_launch.sys, "platform", "darwin"):   # Loader variables, every platform.
            for bundled in (True, False):
                with mock.patch.object(agent_runtime, "is_agent_runtime", return_value=bundled):
                    _command, env = runtime_launch.codex_command("/x/codex", "codex_subscription", state.name)
                self.assertEqual("LD_PRELOAD" in env, not bundled)
                self.assertEqual("GCONV_PATH" in env, not bundled)
                self.assertEqual(env["PATH"], "/usr/bin")
        # On Linux the bundled runtime finds programs in root-owned folders only (a user-writable bwrap earlier in
        # PATH would otherwise run at every app-server start, measured on Ubuntu 24.04).
        injected = dict(self.INJECTED, PATH="/home/a/.local/bin:/usr/bin")
        with mock.patch.object(runtime_launch, "child_environment", side_effect=lambda *a, **k: dict(injected)), \
                mock.patch.object(runtime_launch, "profile_environment", side_effect=lambda env, *a, **k: env), \
                mock.patch.object(runtime_launch.sys, "platform", "linux"):
            with mock.patch.object(agent_runtime, "is_agent_runtime", return_value=True), \
                    mock.patch.dict(agent_runtime.PIN, {"app_server_flags": [agent_runtime.IGNORE_USER_CONFIG]}):
                _command, env = runtime_launch.codex_command("/x/codex", "codex_subscription", state.name)
            self.assertEqual(env["PATH"], "/usr/sbin:/usr/bin:/sbin:/bin")


class NoSandboxedProcessTests(unittest.TestCase):
    """Break-it review SHOULD-6: Codex's Linux sandbox (bubblewrap, which needs the unprivileged user namespaces
    Ubuntu 24.04 restricts) starts only for a sandboxed command. Every way the pinned fork has to run one is off or
    answered no in Angelo's launch: the shell tools (shell_tool also gates unified_exec), hooks, browser and computer
    use, skill dependency installs; the sandbox is read-only, so a patch or command that writes needs approval, which
    Angelo always declines. Default-on features of the pinned fork that can start a process, read from its
    codex-rs/features/src/lib.rs at the pin."""

    OFF = ("features.shell_tool", "features.shell_snapshot", "features.hooks", "features.browser_use",
           "features.computer_use", "features.in_app_browser", "features.skill_mcp_dependency_install", "features.code_mode",
           "features.apps", "features.plugins", "features.remote_plugin")

    def test_every_process_starting_feature_is_off_and_writes_are_declined(self):
        from unittest import mock
        from angelo_agent import runtime_launch, runtime_requests
        with mock.patch.object(runtime_launch, "profile_environment", side_effect=lambda env, *a, **k: env), \
                mock.patch.object(runtime_launch, "with_runtime_path", side_effect=lambda env, executable: env):
            command, _env = runtime_launch.codex_command("/x/codex", "codex_subscription", "/tmp/state")
        settings = dict(value.split("=", 1) for flag, value in zip(command, command[1:]) if flag == "-c")
        for key in self.OFF:
            self.assertEqual(settings.get(key), "false", key)
        self.assertEqual(settings.get("sandbox_mode"), '"read-only"')
        self.assertEqual(settings.get("approval_policy"), '"on-request"')
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            self.assertEqual(runtime_requests.CODEX_DECLINED[method], {"decision": "decline"}, method)

    def test_no_program_from_the_persons_codex_configuration_runs(self):
        """Config keys that start a process: ``notify`` (after every turn, with its text; built whatever
        features.hooks says), every MCP server and every plugin the person's config.toml lists."""
        import tempfile
        from unittest import mock
        from angelo_agent import runtime_launch
        with tempfile.TemporaryDirectory() as home:
            Path(home, "config.toml").write_text('notify = ["codex-notify"]\n[mcp_servers.tracker]\ncommand = "t"\n'
                                                 '[plugins.helper]\nenabled = true\n', encoding="utf-8")
            with mock.patch.object(runtime_launch, "profile_environment",
                                   side_effect=lambda env, *a, **k: dict(env, CODEX_HOME=home)), \
                    mock.patch.object(runtime_launch, "with_runtime_path", side_effect=lambda env, executable: env):
                command, _env = runtime_launch.codex_command("/x/codex", "codex_subscription", "/tmp/state")
        settings = dict(value.split("=", 1) for flag, value in zip(command, command[1:]) if flag == "-c")
        self.assertEqual(settings.get("notify"), "[]")
        self.assertEqual(settings.get("mcp_servers.tracker.enabled"), "false")
        self.assertEqual(settings.get("plugins.helper.enabled"), "false")


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name, "runtime-linux")

    def test_a_complete_linux_runtime_passes(self):
        self.assertEqual(release_checks.agent_runtime_problems(linux_runtime(self.folder), "linux"), [])
        self.assertEqual(release_checks.agent_runtime_problems(self.folder, "linux", exclusive=True), [])

    def test_wrong_code_is_refused(self):
        cases = {"#!/bin/sh": (b"#!/bin/sh\n", "is not x86-64 Linux ELF code"),
                 "arm64": (elf_file(machine=EM_AARCH64), "is not x86-64 Linux ELF code"),
                 "glibc": (elf_file(needs=(("libc.so.6", ("GLIBC_2.38",)),)), "needs GLIBC 2.38 from libc.so.6"),
                 "libssl": (elf_file(needs=(("libc.so.6", ("GLIBC_2.17",)), ("libssl.so.1.1", ("OPENSSL_1_1_0",)))),
                            "needs libssl.so.1.1, which distributions ship differently"),
                 "runpath": (elf_file(runpath="/home/builder/lib"), "only $ORIGIN-relative folders"),
                 "truncated": (elf_file()[:100], "is a malformed ELF file")}
        for label, (data, words) in cases.items():
            with self.subTest(label):
                folder = linux_runtime(Path(self.folder.parent, label), data)
                self.assertIn(words, "\n".join(release_checks.agent_runtime_problems(folder, "linux")))
        if os.name != "nt":   # Windows keeps no execute bit: os.access(X_OK) is true for every file there.
            folder = linux_runtime(Path(self.folder.parent, "mode"))
            (folder / "codex").chmod(0o644)
            self.assertIn("is not marked executable",
                          "\n".join(release_checks.agent_runtime_problems(folder, "linux")))

    def test_the_command_line_takes_linux(self):
        linux_runtime(self.folder)
        self.assertEqual(release_checks.main(["agent-runtime", str(self.folder), "--platform", "linux", "--no-run"]), 0)


if __name__ == "__main__":
    unittest.main()
