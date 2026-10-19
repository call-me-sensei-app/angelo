# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude Code and Codex discovery and spawning on real Windows (Windows only; run by windows_agent_checks.py).

Run inside the app started from the desktop (so its environment is a GUI
launch's, not a terminal's) after the official CLIs were installed for the
signed-in user. Nothing signs in, sends a prompt or reads a credential: the
CLIs only answer ``--version``, ``auth status`` and Codex's ``initialize``,
``config/read`` and ``account/read``. Each step lands in the report as PASS,
FAIL or NOTE (a fact recorded, not a pass condition). Contract:
doc/angelo/ai_providers.rst, "Windows".
"""

import ctypes
import json
import locale
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from angelo_agent import child_process, cli_runtime, cli_windows
from angelo_agent.protocol import JsonRpcProcess, RpcError

# Arguments cmd.exe would re-parse (quotes, %, ^, &, |, <, !) plus JSON, Unicode, an empty one and trailing backslashes.
TRICKY = ['{"model":"gpt-6","effort":"low"}', 'model_provider="openai"', "two words", 'say "hi"', "back\\slash\\",
          "100%", "%PATH%", "%%", "!x!", "^caret", "amp&er", "pipe|x", "<lt>", "", "h\u00e9llo \u2713 \u65e5\u672c",
          'end\\"quote']
UTF8_TEXT = "h\u00e9llo \u2713 \u65e5\u672c \U0001F3A8"
# A child that starts a long-lived grandchild, reports its pid (stdout, and a file when named), then sleeps.
_SLEEPER = ("import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
            "print(child.pid, flush=True)\n"
            "len(sys.argv) > 2 and open(sys.argv[2], 'w').write(str(child.pid))\n"
            "time.sleep(float(sys.argv[1]))\n")
# A sign-in CLI stand-in: a console helper of its own (pid written to argv[3]), then the default browser
# through ShellExecute (as Codex and Claude Code open their sign-in pages), then it waits.
_SIGN_IN_STAND_IN = ("import os, subprocess, sys, time\n"
                     "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                     "open(sys.argv[3], 'w').write(str(helper.pid))\n"
                     "os.startfile(sys.argv[1])\n"
                     "time.sleep(float(sys.argv[2]))\n")
_CONSOLE = ("import ctypes\n"
            "k, u = ctypes.windll.kernel32, ctypes.windll.user32\n"
            "h = k.GetConsoleWindow()\n"
            "print(int(h or 0), int(u.IsWindowVisible(h)) if h else 0)\n")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
for _name, _restype, _argtypes in (
        ("CreateToolhelp32Snapshot", wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
        ("Process32FirstW", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_void_p]),
        ("Process32NextW", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_void_p]),
        ("OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        ("GetExitCodeProcess", wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]),
        ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]), ("GetConsoleWindow", wintypes.HWND, []),
        ("GetACP", wintypes.UINT, [])):
    _function = getattr(_kernel32, _name)
    _function.restype, _function.argtypes = _restype, _argtypes
for _name, _restype, _argtypes in (
        ("GetClassNameW", ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
        ("GetWindowThreadProcessId", wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
        ("IsWindowVisible", wintypes.BOOL, [wintypes.HWND]),
        ("EnumWindows", wintypes.BOOL, [ctypes.c_void_p, wintypes.LPARAM])):
    _function = getattr(_user32, _name)
    _function.restype, _function.argtypes = _restype, _argtypes


def processes():
    """``{pid: (parent pid, image name)}`` from a Toolhelp snapshot."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(0x2, 0)
    table, entry = {}, PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
    try:
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return table


def descendants(pid):
    table, found, frontier = processes(), {}, [pid]
    while frontier:
        parent = frontier.pop()
        for child, (ppid, name) in table.items():
            if ppid == parent and child not in found and child != pid:
                found[child] = name
                frontier.append(child)
    return found


def alive(pid):
    handle = _kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def wait_gone(pids, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(alive(pid) for pid in pids):
        time.sleep(0.1)
    return [pid for pid in pids if alive(pid)]


def kill(pids):
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, creationflags=child_process.CREATE_NO_WINDOW)


def visible_console_windows(pids):
    """Visible console windows (conhost's ConsoleWindowClass) owned by any of ``pids``."""
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, name, 64)
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if name.value in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"} and _user32.IsWindowVisible(hwnd) \
                and (owner.value in pids or not pids):
            found.append((owner.value, name.value))
        return True

    callback = callback_type(visit)
    _user32.EnumWindows(ctypes.cast(callback, ctypes.c_void_p), 0)
    return found


class Checks:
    def __init__(self, work, tools=None, desktop=False):
        self.work, self.tools, self.desktop = Path(work), Path(tools) if tools else None, desktop
        self.steps = {}
        self.python = sys.executable if Path(sys.executable).name.lower().startswith("python") else shutil.which("python")

    def step(self, name, ok, **detail):
        self.steps[name] = {"ok": ok, **detail}
        print("%s  %s  %s" % ({True: "PASS", False: "FAIL", None: "NOTE"}[ok], name,
                               json.dumps(detail, ensure_ascii=False)[:400]), flush=True)

    def guarded(self, name, function):
        try:
            function()
        except Exception as error:   # One failing step must not hide the others.
            self.step(name, False, error="%s: %s" % (type(error).__name__, error))

    def run(self):
        for name, function in (("environment", self.environment), ("discovery", self.discovery),
                               ("not_found_message", self.not_found_message), ("git_bash", self.git_bash),
                               ("claude_runtime", self.claude_runtime), ("codex_app_server", self.codex_app_server),
                               ("shim_quoting", self.shim_quoting), ("plain_batch", self.plain_batch),
                               ("no_console_window", self.no_console_window), ("utf8_pipes", self.utf8_pipes),
                               ("job_tree", self.job_tree), ("browser", self.browser)):
            self.guarded(name, function)
        return self.steps

    # -- facts ---------------------------------------------------------------------------------------------------
    def environment(self):
        path = os.environ.get("PATH", "").split(os.pathsep)
        appdata, home = os.environ.get("APPDATA", ""), str(Path.home())
        ntdll, long_paths = ctypes.WinDLL("ntdll"), None
        if hasattr(ntdll, "RtlAreLongPathsEnabled"):   # BOOLEAN: one byte
            ntdll.RtlAreLongPathsEnabled.restype = ctypes.c_ubyte
            long_paths = bool(ntdll.RtlAreLongPathsEnabled())
        self.step("environment", None, executable=sys.executable, in_blender="bpy" in sys.modules,
                  ansi_code_page=_kernel32.GetACP(), preferred_encoding=locale.getpreferredencoding(False),
                  own_console=int(_kernel32.GetConsoleWindow() or 0), long_paths_enabled=long_paths,
                  npm_dir_on_path=any(entry.rstrip("\\").lower() == str(Path(appdata, "npm")).lower() for entry in path),
                  local_bin_on_path=any(entry.rstrip("\\").lower() == str(Path(home, ".local", "bin")).lower() for entry in path),
                  git_on_path=bool(shutil.which("git")), registry_path=cli_windows.registry_path()[:12])

    def discovery(self):
        for name in ("claude", "codex"):
            on_path = shutil.which(name)
            try:
                found = cli_runtime.find_executable(name)
            except RpcError as error:
                self.step("discovery_" + name, False, error=str(error), on_path=on_path)
                continue
            source = ("PATH" if on_path and Path(on_path) == Path(found) else
                      "known folders" if any(Path(found).parent == Path(directory) for directory in
                                             cli_windows.known_directories(os.environ, Path.home())) else "registry PATH")
            self.step("discovery_" + name, True, found=found, via=source,
                      launch=cli_runtime.runtime_command([found, "--version"], dict(os.environ))[:2])
        # Claude's native installer alone (~\.local\bin\claude.exe): npm's folder hidden from the search.
        native = Path.home() / ".local" / "bin" / "claude.exe"
        empty = self.work / "no-npm"
        empty.mkdir(exist_ok=True)
        npm = str(Path(os.environ.get("APPDATA", ""), "npm")).lower()
        path = os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                               if entry.rstrip("\\").lower() != npm)
        with patch.dict(os.environ, {"PATH": path}):
            try:
                found = cli_runtime._find_windows("claude", "", dict(os.environ, APPDATA=str(empty)), registry=lambda: [])
            except RpcError as error:
                found = "not found: " + str(error)
        self.step("discovery_claude_native_installer", Path(found) == native if native.is_file() else None,
                  found=found, expected=str(native))

    def not_found_message(self):
        empty = self.work / "empty"
        empty.mkdir(exist_ok=True)
        environ = {key: str(empty) for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "ProgramFiles", "ProgramData")}
        with patch.dict(os.environ, {"PATH": str(empty)}), patch.object(Path, "home", return_value=empty):
            try:
                cli_runtime._find_windows("codex", "", environ, registry=lambda: [])
            except RpcError as error:
                message = str(error)
                self.step("not_found_message", "where.exe codex" in message and "npm install -g @openai/codex" in message,
                          message=message)
                return
        self.step("not_found_message", False, error="a CLI was found in an empty environment")

    def git_bash(self):
        bash = cli_windows.git_bash(os.environ, shutil.which)
        self.step("git_bash_found", bool(bash and Path(bash).is_file()), bash=bash)
        claude = self._claude_candidates()
        if not claude:
            self.step("git_bash_missing_message", False, error="no Claude Code install to check against")
            return
        empty = self.work / "no-git"
        empty.mkdir(exist_ok=True)
        environ = {key: str(empty) for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "ProgramFiles",
                                               "ProgramW6432", "ProgramFiles(x86)", "ProgramData")}
        system = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32")
        with patch.dict(os.environ, {"PATH": system}):
            try:
                cli_runtime._find_windows("claude", claude[0], environ, registry=lambda: [])
            except RpcError as error:
                self.step("git_bash_missing_message", str(error) == cli_windows.GIT_BASH_MISSING, message=str(error))
            else:
                self.step("git_bash_missing_message", False, error="Claude was accepted without Git Bash")
            # What Claude Code itself does without Git Bash (a fact for the docs, not Angelo's behaviour).
            env = {"PATH": system, "SystemRoot": os.environ.get("SystemRoot", "C:\\Windows"), "USERPROFILE": str(empty),
                   "APPDATA": str(empty), "LOCALAPPDATA": str(empty), "TEMP": str(empty), "TMP": str(empty)}
            try:
                probe = cli_runtime.run_cli([claude[0], "--version"], env=env, capture_output=True, text=True, timeout=60)
                self.step("claude_without_git_bash", None, exit=probe.returncode,
                          output=(probe.stdout + probe.stderr).strip()[:300])
            except (OSError, RpcError, subprocess.TimeoutExpired) as error:
                self.step("claude_without_git_bash", None, error=str(error)[:300])

    # -- Claude Code -------------------------------------------------------------------------------------------
    def _claude_candidates(self):
        home, appdata = Path.home(), Path(os.environ.get("APPDATA", ""))
        found = []
        for path in (home / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd"):
            if path.is_file():
                found.append(str(path))
        try:
            discovered = cli_runtime.find_executable("claude")
            if discovered.lower() not in {path.lower() for path in found}:
                found.append(discovered)
        except RpcError:
            pass
        return found

    def claude_runtime(self):
        from angelo_agent import claude_transport
        candidates = self._claude_candidates()
        if not candidates:
            self.step("claude_version", False, error="no Claude Code install found")
        for executable in candidates:
            label = "native" if executable.lower().endswith(".exe") else "npm"
            env = cli_runtime.with_runtime_path(dict(os.environ), executable)
            started = time.monotonic()
            try:
                version = claude_transport.require_supported_runtime(executable, env)
            except RpcError as error:
                probe = cli_runtime.run_cli([executable, "--version"], env=env, capture_output=True, text=True, timeout=60)
                self.step("claude_version_" + label, False, executable=executable, error=str(error),
                          printed=(probe.stdout + probe.stderr).strip()[:300], exit=probe.returncode)
                continue
            self.step("claude_version_" + label, True, executable=executable, version=".".join(map(str, version)),
                      seconds=round(time.monotonic() - started, 2), tested_range="%s-%s" % tuple(
                          ".".join(map(str, cli_runtime.RUNTIMES["claude"][key])) for key in ("floor", "ceiling")),
                      drift=cli_runtime.newer_than_gated("claude", version))
            config = self.work / ("claude-config-" + label)
            config.mkdir(exist_ok=True)
            status = cli_runtime.run_cli([executable, "auth", "status"], env=dict(env, CLAUDE_CONFIG_DIR=str(config)),
                                         capture_output=True, text=True, timeout=60)
            try:
                logged_in = json.loads(status.stdout).get("loggedIn")
            except ValueError:
                logged_in = "unparsed: " + (status.stdout + status.stderr).strip()[:200]
            self.step("claude_auth_status_" + label, logged_in is False, exit=status.returncode, logged_in=logged_in)

    # -- Codex -------------------------------------------------------------------------------------------------
    def codex_app_server(self):
        from angelo_agent.providers import codex_command
        executable = cli_runtime.find_executable("codex")
        state = self.work / "codex-state"
        state.mkdir(exist_ok=True)
        command, env = codex_command(executable, "codex_subscription", str(state), reuse_login=False)
        started = time.monotonic()
        rpc = JsonRpcProcess(command, env=env, cwd=str(self.work), spawn=cli_runtime.launch)
        try:
            initialized = rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo",
                                                                    "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            rpc.notify("initialized")
            version = cli_runtime.codex_version(initialized)
            self.step("codex_initialize", True, executable=executable, version=".".join(map(str, version)),
                      seconds=round(time.monotonic() - started, 2), launched=rpc.process.args[:2],
                      drift=cli_runtime.newer_than_gated("codex", version))
            configuration = rpc.request("config/read", {}).get("config", {})
            # The -c values carry quotes and dots: they arrive intact only when no cmd.exe re-parses them.
            expected = {"sandbox_mode": "read-only", "approval_policy": "on-request", "model_reasoning_effort": "low"}
            seen = {key: configuration.get(key) for key in expected}
            self.step("codex_overrides_intact", seen == expected, seen=seen)
            account = rpc.request("account/read", {"refreshToken": False}).get("account")
            self.step("codex_not_signed_in", account is None, account_type=(account or {}).get("type"))
            tree = descendants(rpc.process.pid)
            windows = visible_console_windows({rpc.process.pid, *tree})
            self.step("codex_no_console_window", not windows, tree=tree, windows=windows)
            rpc.process.terminate()   # Job Object: the whole tree, node.exe and codex.exe alike
            survivors = wait_gone([rpc.process.pid, *tree])
            self.step("codex_terminate_ends_tree", not survivors, tree=tree, survivors=survivors)
            kill(survivors)
        finally:
            rpc.close()
        # A normal close (stdin closed, the app-server exits by itself) leaves nothing behind either.
        rpc = JsonRpcProcess(command, env=env, cwd=str(self.work), spawn=cli_runtime.launch)
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        tree = descendants(rpc.process.pid)
        rpc.close()
        survivors = wait_gone([rpc.process.pid, *tree])
        self.step("codex_close_leaves_nothing", not survivors, tree=tree, survivors=survivors,
                  exit=rpc.process.returncode)
        kill(survivors)

    # -- shims -------------------------------------------------------------------------------------------------
    def shim_quoting(self):
        shim = Path(os.environ.get("APPDATA", ""), "npm", "angelo-argv-echo.cmd")
        if not shim.is_file():
            self.step("shim_quoting", False, error="%s is missing (the .ps1 installs it with npm)" % shim)
            return
        env = cli_runtime.with_runtime_path(dict(os.environ), str(shim))
        resolved = cli_runtime.runtime_command([str(shim), "x"], dict(env))
        completed = cli_runtime.run_cli([str(shim), *TRICKY], env=env, capture_output=True, text=True, timeout=60)
        received = json.loads(completed.stdout) if completed.stdout.strip() else None
        self.step("shim_quoting", received == TRICKY, resolved=resolved[:2], received=received)
        # Why it matters: the same call through cmd.exe (what CreateProcess does with a .cmd).
        direct = subprocess.run([str(shim), *TRICKY], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=env, creationflags=child_process.CREATE_NO_WINDOW)
        try:
            through_cmd = json.loads(direct.stdout)
        except ValueError:
            through_cmd = direct.stdout.strip()[:300]
        if isinstance(through_cmd, list):
            changed = sum(1 for index, original in enumerate(TRICKY)
                          if index >= len(through_cmd) or through_cmd[index] != original)
        else:
            changed = "all (unparseable output)"
        self.step("shim_through_cmd_exe_would_mangle", None, changed=changed, of=len(TRICKY), through_cmd=through_cmd)

    def plain_batch(self):
        batch = self.work / "plain.cmd"
        batch.write_text("@echo off\r\necho %*\r\n", encoding="ascii")
        plain = cli_runtime.run_cli([str(batch), "alpha", "beta=1"], env=dict(os.environ), capture_output=True,
                                    text=True, timeout=30)
        try:
            cli_runtime.run_cli([str(batch), '{"a":1}'], env=dict(os.environ), capture_output=True, text=True, timeout=30)
        except RpcError as error:
            refused = str(error)
        else:
            refused = None
        self.step("plain_batch", plain.stdout.strip() == "alpha beta=1" and bool(refused),
                  plain=plain.stdout.strip(), refused=refused)

    # -- process behaviour -------------------------------------------------------------------------------------
    def no_console_window(self):
        ours = child_process.run([self.python, "-c", _CONSOLE], capture_output=True, text=True, timeout=60)
        plain = subprocess.run([self.python, "-c", _CONSOLE], capture_output=True, text=True, timeout=60)
        window, _ = map(int, ours.stdout.split())
        self.step("no_console_window", window == 0, child_console_window=window,
                  without_create_no_window=plain.stdout.strip(), parent_console=int(_kernel32.GetConsoleWindow() or 0))

    def utf8_pipes(self):
        node = shutil.which("node") or str(Path(os.environ.get("ProgramFiles", ""), "nodejs", "node.exe"))
        script = "process.stdin.setEncoding('utf8');let s='';process.stdin.on('data',d=>s+=d);" \
                 "process.stdin.on('end',()=>process.stdout.write(%s+'|'+s))" % json.dumps(UTF8_TEXT)
        ours = child_process.run([node, "-e", script], input="\u00fcn\u00ef \u2713", capture_output=True, text=True, timeout=60)
        plain = subprocess.run([node, "-e", script], input="plain", capture_output=True, text=True, timeout=60,
                               creationflags=child_process.CREATE_NO_WINDOW)
        self.step("utf8_pipes", ours.stdout == UTF8_TEXT + "|\u00fcn\u00ef \u2713", received=ours.stdout,
                  default_text_mode_decodes_as=plain.stdout[:40], default_encoding=locale.getpreferredencoding(False))

    def job_tree(self):
        process = child_process.popen([self.python, "-c", _SLEEPER, "300"], stdout=subprocess.PIPE, text=True)
        grandchild = int(process.stdout.readline())
        process.terminate()
        process.wait(10)
        survivors = wait_gone([process.pid, grandchild])
        self.step("terminate_ends_tree", not survivors, child=process.pid, grandchild=grandchild, survivors=survivors)
        kill(survivors)
        marker = self.work / "timeout-grandchild.txt"
        try:
            child_process.run([self.python, "-c", _SLEEPER, "300", str(marker)], capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            grandchild = int(marker.read_text()) if marker.is_file() else None
            survivors = wait_gone([grandchild]) if grandchild else []
            self.step("run_timeout_ends_tree", grandchild is not None and not survivors, grandchild=grandchild,
                      survivors=survivors)
            kill(survivors)
        else:
            self.step("run_timeout_ends_tree", False, error="no timeout")
        process = child_process.popen([self.python, "-c", _SLEEPER, "0.5"], stdout=subprocess.PIPE, text=True)
        grandchild = int(process.stdout.readline())
        process.wait(30)
        time.sleep(1)
        survived = alive(grandchild)
        self.step("normal_exit_leaves_descendants", survived, grandchild=grandchild, exit=process.returncode)
        kill([grandchild])

    def browser(self):
        """A CLI opening the sign-in page (ShellExecute, as Codex and Claude Code do) and exiting normally."""
        if not self.desktop:
            self.step("browser_survives_normal_exit", None, skipped="needs the desktop session")
            return
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<title>Angelo check</title><p>Angelo browser check</p>")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        before = {pid for pid, (_, name) in processes().items() if name.lower() == "msedge.exe"}
        url = "http://127.0.0.1:%d/angelo-browser-check" % server.server_address[1]
        opener = "import os, sys, time; os.startfile(sys.argv[1]); time.sleep(float(sys.argv[2]))"
        try:
            process = child_process.popen([self.python, "-c", opener, url, "3"])
            process.wait(60)
            deadline = time.monotonic() + 30
            while not hits and time.monotonic() < deadline:
                time.sleep(0.25)
            time.sleep(2)
            browsers = {pid for pid, (_, name) in processes().items() if name.lower() == "msedge.exe"} - before
            self.step("browser_survives_normal_exit", bool(hits) and any(alive(pid) for pid in browsers),
                      requests=list(hits), new_browser_processes=len(browsers), opener_exit=process.returncode)
            # A cancelled sign-in: the CLI (with a console helper of its own) is ended while the browser it started
            # is open. A fresh browser joins the CLI's Job Object; the sign-in launch must leave it running.
            def edges():
                return {pid for pid, (_, name) in processes().items() if name.lower() == "msedge.exe"}

            for label, keep in (("browser_survives_cancelled_sign_in", True), ("browser_after_terminate_otherwise", False)):
                kill(list(edges() - before))
                wait_gone(list(edges() - before), 15)
                hits.clear()
                marker = self.work / ("helper-%s.txt" % label)
                process = child_process.popen([self.python, "-c", _SIGN_IN_STAND_IN, url + "?" + label, "120", str(marker)],
                                              keep_gui_programs=keep)
                deadline = time.monotonic() + 30
                while not (hits and marker.is_file()) and time.monotonic() < deadline:
                    time.sleep(0.25)
                time.sleep(2)
                started = edges() - before
                process.terminate()
                process.wait(10)
                helper = int(marker.read_text()) if marker.is_file() else None
                survivors = wait_gone([pid for pid in (helper,) if pid], 10)
                time.sleep(2)
                browsers = [pid for pid in started if alive(pid)]
                detail = dict(requests=list(hits), browser_processes_started=len(started),
                              browser_processes_alive=len(browsers), helper_survived=bool(survivors),
                              browser_was_running_before=bool(before))
                if keep:
                    self.step(label, bool(hits) and bool(started) and bool(browsers) and not survivors, **detail)
                else:   # Why sign-in launches keep windowed programs: a fact, not a pass condition.
                    self.step(label, None, **detail)
                kill(survivors)
        finally:
            server.shutdown()
            kill([pid for pid, (_, name) in processes().items() if name.lower() == "msedge.exe" and pid not in before])


def run(work=None, tools=None, desktop=False):
    work = Path(work or tempfile.mkdtemp(prefix="angelo-cli-checks-"))
    work.mkdir(parents=True, exist_ok=True)
    return Checks(work, tools, desktop).run()
