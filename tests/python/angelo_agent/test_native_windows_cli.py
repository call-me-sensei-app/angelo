# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows CLI discovery, npm shim resolution, Git Bash and launching, on any host.

Folders are real temporary directories named like their Windows counterparts;
``shutil.which`` is replaced by a PATHEXT-style stand-in, because a POSIX
``which`` neither tries ``.cmd``/``.exe`` nor ignores the executable bit. The
Windows run (doc/angelo/ai_providers.rst, "Windows") proves the real behaviour
with installed CLIs.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import child_process, cli_runtime, cli_windows
from angelo_agent.protocol import RpcError

NPM_SHIM = r'''@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
'''
PNPM_SHIM = r'''@SETLOCAL
@IF NOT DEFINED NODE_PATH (
  @SET "NODE_PATH=C:\pnpm\global\5\node_modules"
) ELSE (
  @SET "NODE_PATH=%NODE_PATH%;C:\pnpm\global\5\node_modules"
)
@IF EXIST "%~dp0\node.exe" (
  "%~dp0\node.exe"  "%~dp0\global\5\node_modules\@anthropic-ai\claude-code\cli.js" %*
) ELSE (
  @SET PATHEXT=%PATHEXT:;.JS;=;%
  node  "%~dp0\global\5\node_modules\@anthropic-ai\claude-code\cli.js" %*
)
'''
EXE_SHIM = '@ECHO off\r\n"%~dp0\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n'


def which_stand_in(name, path=None):
    """``shutil.which`` as Windows answers it: PATHEXT extensions, no executable bit."""
    for directory in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        for suffix in ("", ".exe", ".cmd"):
            candidate = Path(directory, name + suffix) if directory else None
            if candidate and candidate.is_file() and (suffix or Path(name).suffix):
                return str(candidate)
    return None


class WindowsCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-windows-cli-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / "home"
        self.environ = {"APPDATA": str(self.home / "AppData/Roaming"), "LOCALAPPDATA": str(self.home / "AppData/Local"),
                        "ProgramFiles": str(self.root / "Program Files"), "USERPROFILE": str(self.home)}

    def file(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_known_folders_are_the_npm_winget_store_and_node_installs_that_exist(self):
        for relative in ("home/AppData/Roaming/npm", "home/AppData/Local/Microsoft/WindowsApps",
                         "home/AppData/Local/Microsoft/WinGet/Links", "Program Files/nodejs", "home/.local/bin",
                         *("home/AppData/Roaming/fnm/node-versions/v%d.0.0/installation" % n for n in range(16, 24))):
            (self.root / relative).mkdir(parents=True)
        directories = cli_windows.known_directories(self.environ, self.home)
        self.assertEqual(directories[:4], [str(self.home / "AppData/Roaming/npm"),
                                           str(self.home / "AppData/Local/Microsoft/WindowsApps"),
                                           str(self.home / "AppData/Local/Microsoft/WinGet/Links"),
                                           str(self.home / ".local/bin")])
        self.assertIn(str(self.root / "Program Files/nodejs"), directories)
        versions = [Path(d).parent.name for d in directories if "fnm" in d]
        self.assertEqual(versions, ["v%d.0.0" % n for n in range(23, 17, -1)])   # Newest six only.
        self.assertTrue(all(Path(d).is_dir() for d in directories))

    def test_configured_npm_prefix_holds_shims_directly(self):
        prefix = self.root / "npm-prefix"
        prefix.mkdir()
        directories = cli_windows.known_directories({"npm_config_prefix": str(prefix)}, self.home)
        self.assertEqual(directories, [str(prefix)])   # Not <prefix>\bin as on macOS; variable names ignore case.

    def test_chatgpt_bundled_codex_and_the_saved_path(self):
        codex = self.file("home/AppData/Local/Programs/ChatGPT/resources/codex-cli/codex.exe")
        self.assertEqual(cli_windows.chatgpt_codex(self.environ), [str(codex)])
        kept = self.root / "kept"
        kept.mkdir()
        saved = cli_windows.registry_path(lambda: ['"%s";relative;%s' % (kept, self.root / "missing"), str(kept)])
        self.assertEqual(saved, [str(kept)])

    def test_npm_shim_runs_node_and_its_script_directly(self):
        shim = self.file("npm/codex.cmd", NPM_SHIM)
        script = self.file("npm/node_modules/@openai/codex/bin/codex.js")
        command, environment = cli_windows.resolve_shim(shim, lambda: "C:\\node\\node.exe")
        self.assertEqual((command, environment), (["C:\\node\\node.exe", str(script)], {}))
        node = self.file("npm/node.exe")   # Node installed beside the shim (the nodejs folder).
        self.assertEqual(cli_windows.resolve_shim(shim, None)[0], [str(node), str(script)])
        for sibling in ("npm/codex", "npm/codex.ps1"):
            self.file(sibling, "#!/bin/sh")
            self.assertEqual(cli_windows.resolve_shim(self.root / sibling, None)[0], [str(node), str(script)])

    def test_pnpm_yarn_and_native_shims(self):
        script = self.file("pnpm/global/5/node_modules/@anthropic-ai/claude-code/cli.js")
        command, environment = cli_windows.resolve_shim(self.file("pnpm/claude.cmd", PNPM_SHIM), lambda: "node.exe")
        self.assertEqual(command, ["node.exe", str(script)])
        self.assertEqual(environment, {} if os.name != "nt" else {"NODE_PATH": "C:\\pnpm\\global\\5\\node_modules"})
        exe = self.file("npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe")
        self.assertEqual(cli_windows.resolve_shim(self.file("npm/claude.cmd", EXE_SHIM), None)[0], [str(exe)])
        outer = self.file("yarn/claude.cmd", '@"%~dp0\\..\\npm\\claude.cmd"   %*\r\n')
        self.assertEqual(cli_windows.resolve_shim(outer, None)[0], [str(exe)])
        self.assertIsNone(cli_windows.resolve_shim(self.file("other/tool.cmd", "@echo hello %*"), None))
        self.assertIsNone(cli_windows.resolve_shim(self.file("gone/codex.cmd", NPM_SHIM), None))  # Target missing.
        self.assertIsNone(cli_windows.resolve_shim(exe, None))

    def test_batch_files_never_receive_arguments_cmd_would_reparse(self):
        shim = self.file("npm/codex.cmd", NPM_SHIM)
        script = self.file("npm/node_modules/@openai/codex/bin/codex.js")
        json_argument = '{"fastMode":true,"hooks":{}}'
        args, _ = cli_windows.launch_command([str(shim), "--settings", json_argument], lambda: "node.exe", {})
        self.assertEqual(args, ["node.exe", str(script), "--settings", json_argument])
        exe = self.file("bin/codex.exe")
        self.assertEqual(cli_windows.launch_command([str(exe), json_argument], None, {})[0], [str(exe), json_argument])
        other = self.file("tools/claude.cmd", "@echo off\r\nC:\\tools\\real-claude.exe %*\r\n")
        line, _ = cli_windows.launch_command([str(other), "--version"], None, {"COMSPEC": "C:\\Windows\\System32\\cmd.exe"})
        self.assertEqual(line, '"C:\\Windows\\System32\\cmd.exe" /d /s /c ""%s" --version"' % other)
        for unsafe in (json_argument, "a&b", "%PATH%", "x y", 'q"', "a^b", "line\nbreak"):
            with self.subTest(argument=unsafe), self.assertRaises(cli_windows.UnsafeBatchArguments):
                cli_windows.launch_command([str(other), unsafe], None, {})

    def test_git_bash_is_found_the_way_claude_code_finds_it(self):
        bash = self.file("Program Files/Git/bin/bash.exe")
        git = self.file("Program Files/Git/cmd/git.exe")
        self.assertEqual(cli_windows.git_bash({}, lambda name: str(git)), str(bash))
        self.assertEqual(cli_windows.git_bash(self.environ, lambda name: None), str(bash))   # Standard folder.
        self.assertEqual(cli_windows.git_bash({"CLAUDE_CODE_GIT_BASH_PATH": str(bash)}, lambda name: None), str(bash))
        self.assertIsNone(cli_windows.git_bash({"CLAUDE_CODE_GIT_BASH_PATH": str(self.root / "no.exe")},
                                               lambda name: str(git)))
        self.assertIsNone(cli_windows.git_bash({"ProgramFiles": str(self.root / "empty")}, lambda name: None))


class WindowsDiscoveryTests(WindowsCliTests):
    """``cli_runtime`` on Windows: discovery order, messages, the child environment and launching."""

    def setUp(self):
        super().setUp()
        for patcher in (patch.object(cli_runtime.shutil, "which", side_effect=which_stand_in),
                        patch.object(cli_windows, "_read_registry_path", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def find(self, name, configured="", registry=None):
        with patch.object(cli_runtime.Path, "home", return_value=self.home), patch.dict(os.environ, {"PATH": ""}):
            return cli_runtime._find_windows(name, configured, self.environ, registry)

    def test_discovery_order_and_the_git_bash_requirement(self):
        npm = self.file("home/AppData/Roaming/npm/claude.cmd", NPM_SHIM)
        with self.assertRaisesRegex(RpcError, "Git Bash"):
            self.find("claude")
        self.file("Program Files/Git/bin/bash.exe")
        self.assertEqual(self.find("claude"), str(npm))
        configured = self.file("tools/claude.exe")
        self.assertEqual(self.find("claude", str(configured)), str(configured))
        self.assertEqual(self.find("claude", str(self.root / "typo.exe")), str(npm))   # A typo falls through, as on macOS.
        codex = self.file("home/AppData/Local/Programs/ChatGPT/resources/codex-cli/codex.exe")
        self.assertEqual(self.find("codex"), str(codex))
        saved = self.file("installed-later/codex.cmd", NPM_SHIM)
        codex.unlink()
        self.assertEqual(self.find("codex", registry=lambda: [str(saved.parent)]), str(saved))

    def test_not_found_says_what_to_do_on_windows(self):
        with self.assertRaises(RpcError) as raised:
            self.find("codex")
        message = str(raised.exception)
        self.assertIn("npm install -g @openai/codex", message)
        self.assertIn("`where.exe codex` in PowerShell", message)
        self.assertNotIn("brew", message)
        self.assertIn("npm install -g @anthropic-ai/claude-code", cli_runtime.too_old_message("claude", (2, 0, 0), "win32"))
        self.assertIn("`which codex` in Terminal", cli_runtime.not_found_message("codex", platform="darwin"))

    def test_child_environment_keeps_windows_path_casing_and_names_git_bash(self):
        bash = self.file("Program Files/Git/bin/bash.exe")
        npm = self.file("home/AppData/Roaming/npm/codex.cmd", NPM_SHIM)
        system = str(self.root / "Windows" / "system32")
        environ = dict(self.environ, Path=system)
        with patch.dict(os.environ, self.environ), patch.object(cli_runtime.Path, "home", return_value=self.home):
            env = cli_runtime.with_runtime_path(dict(environ), str(npm), windows=True)
        self.assertNotIn("PATH", env)
        self.assertEqual(env["Path"].split(os.pathsep)[:2], [system, str(npm.parent)])
        self.assertEqual(env["CLAUDE_CODE_GIT_BASH_PATH"], str(bash))
        on_path = dict(environ, Path=str(npm.parent).upper(), CLAUDE_CODE_GIT_BASH_PATH="C:\\chosen\\bash.exe")
        self.assertEqual(cli_runtime.with_runtime_path(dict(on_path), str(npm), windows=True), on_path)   # Any case.

    def test_launch_resolves_the_shim_or_says_what_is_missing(self):
        shim = self.file("npm/codex.cmd", NPM_SHIM)
        script = self.file("npm/node_modules/@openai/codex/bin/codex.js")
        node = self.file("nodejs/node.exe")
        env = {"PATH": str(node.parent)}
        self.assertEqual(cli_runtime.runtime_command([str(shim), "app-server"], env, windows=True),
                         [str(node), str(script), "app-server"])
        # Nothing of the machine's own may be searched (a Windows host has Node.js in Program Files).
        with patch.object(cli_runtime.Path, "home", return_value=self.home), patch.dict(os.environ, {"PATH": ""}), \
                patch.object(cli_windows, "known_directories", return_value=[]):
            with self.assertRaisesRegex(RpcError, "Node.js was not found"):
                cli_runtime.runtime_command([str(shim), "app-server"], {"PATH": ""}, windows=True)
        other = self.file("tools/claude.cmd", "@echo off\r\nreal.exe %*\r\n")
        with self.assertRaisesRegex(RpcError, "can't pass its arguments safely"):
            cli_runtime.runtime_command([str(other), '{"a":1}'], {}, windows=True)
        self.assertEqual(cli_runtime.runtime_command([str(shim), "app-server"], {}, windows=False),
                         [str(shim), "app-server"])


class ChildProcessTests(unittest.TestCase):
    def test_text_mode_defaults_to_utf8_with_replacement(self):
        self.assertEqual(child_process._utf8({"text": True}), {"text": True, "encoding": "utf-8", "errors": "replace"})
        self.assertEqual(child_process._utf8({"universal_newlines": True, "errors": "strict"}),
                         {"universal_newlines": True, "encoding": "utf-8", "errors": "strict"})
        self.assertEqual(child_process._utf8({"text": True, "encoding": "cp1252"}), {"text": True, "encoding": "cp1252"})
        self.assertEqual(child_process._utf8({"stdout": subprocess.PIPE}), {"stdout": subprocess.PIPE})

    @unittest.skipIf(os.name == "nt", "The POSIX route; Windows starts a ChildProcess (the real child below and "
                                      "windows_cli_checks.py on a Windows machine)")
    def test_text_pipes_are_utf8_and_everything_else_passes_through(self):
        with patch.object(child_process.subprocess, "run") as run, patch.object(child_process.subprocess, "Popen") as popen:
            child_process.run(["tool"], capture_output=True, text=True, timeout=5)
            child_process.run(["tool"], capture_output=True)
            child_process.popen(["tool"], text=True, encoding="cp1252", stdout=subprocess.PIPE)
        self.assertEqual(run.call_args_list[0].kwargs,
                         {"capture_output": True, "text": True, "timeout": 5, "encoding": "utf-8", "errors": "replace"})
        self.assertEqual(run.call_args_list[1].kwargs, {"capture_output": True})
        self.assertEqual(popen.call_args.kwargs, {"text": True, "encoding": "cp1252", "stdout": subprocess.PIPE})

    def test_a_real_child_round_trips_non_ascii_text(self):
        result = child_process.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write('caf\\u00e9 \\U0001f600'.encode())"],
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stdout, "caf\u00e9 \U0001f600")



def pe_header(subsystem):
    """The first bytes of a Windows program: MZ header, PE signature, COFF header, optional header to Subsystem."""
    data = bytearray(256)
    data[:2], data[0x3C:0x40] = b"MZ", (128).to_bytes(4, "little")
    data[128:132] = b"PE\0\0"
    data[128 + 24 + 68:128 + 24 + 70] = subsystem.to_bytes(2, "little")
    return bytes(data)


class FakeJobKernel:
    """The kernel32 calls a sign-in child's job makes when it ends."""

    def __init__(self, members):
        self.members, self.calls = list(members), []

    def QueryInformationJobObject(self, job, info_class, pointer, size, returned):  # noqa: N802
        listing = pointer._obj
        listing.listed = len(self.members)
        for index, pid in enumerate(self.members):
            listing.ids[index] = pid
        return 1

    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        return pid

    def TerminateProcess(self, process, code):  # noqa: N802
        self.calls.append(("TerminateProcess", process))
        self.members.remove(process)
        return 1

    def TerminateJobObject(self, job, code):  # noqa: N802
        self.calls.append(("TerminateJobObject", job))
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        return 1


class SignInChildTests(unittest.TestCase):
    """Cancelling a sign-in ends the CLI and its helpers, never the browser it opened (Windows: same Job Object)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.programs = {}
        for pid, name, subsystem in ((10, "codex.exe", 3), (11, "node.exe", 3), (20, "msedge.exe", 2)):
            path = self.root / name
            path.write_bytes(pe_header(subsystem))
            self.programs[pid] = str(path)

    def job(self, members):
        import ctypes
        job = object.__new__(child_process._WindowsJob)
        job.kernel32, job.ctypes, job.handle, job.closed = FakeJobKernel(members), ctypes, 99, False
        job.lock = threading.Lock()
        job._program = self.programs.get
        return job

    def test_the_program_kind_comes_from_its_pe_header(self):
        self.assertEqual(child_process.pe_subsystem(self.programs[10]), 3)
        self.assertEqual(child_process.pe_subsystem(self.programs[20]), child_process.IMAGE_SUBSYSTEM_WINDOWS_GUI)
        (self.root / "short.exe").write_bytes(b"MZ")
        (self.root / "text.exe").write_bytes(b"#!/bin/sh\n" + bytes(200))
        for name in ("short.exe", "text.exe", "missing.exe"):
            with self.subTest(program=name):
                self.assertIsNone(child_process.pe_subsystem(self.root / name))
        # An unreadable program counts as console: the CLI's own helpers always end.
        self.assertEqual(child_process.console_members([10, 20, 30], self.programs.get), [10, 30])

    def test_a_sign_in_child_ends_its_console_programs_and_leaves_the_browser(self):
        job = self.job([10, 11, 20])
        job.terminate(10, keep_gui=True)
        self.assertEqual(job.kernel32.calls, [("TerminateProcess", 10), ("TerminateProcess", 11)])
        self.assertEqual(job.kernel32.members, [20])

    def test_every_other_child_ends_its_whole_job(self):
        job = self.job([10, 11, 20])
        job.terminate(10)
        self.assertEqual(job.kernel32.calls, [("TerminateJobObject", 99)])

    @unittest.skipIf(os.name == "nt", "The POSIX route (Windows: the job tests above and windows_cli_checks.py)")
    def test_posix_ignores_the_option_and_ends_only_the_child(self):
        with patch.object(child_process.subprocess, "Popen") as popen:
            child_process.popen(["claude", "auth", "login"], keep_gui_programs=True)
        self.assertNotIn("keep_gui_programs", popen.call_args.kwargs)

    def test_native_sign_in_and_status_commands_keep_windowed_programs(self):
        from angelo_agent import authentication
        launched = []

        def launch(command, **kwargs):
            launched.append(kwargs)
            raise OSError("not started in this test")

        auth = authentication.NativeAuthentication()
        with patch.object(authentication, "native_auth_command", return_value=(["claude", "auth", "login"], {})), \
                patch.object(authentication, "launch", side_effect=launch):
            auth.start("claude_subscription", {}, str(self.root / "state"), login=True)
            for _ in range(200):
                if auth.pump():
                    break
                time.sleep(0.01)
        self.assertEqual([kwargs.get("keep_gui_programs") for kwargs in launched], [True])


if __name__ == "__main__":
    unittest.main()
