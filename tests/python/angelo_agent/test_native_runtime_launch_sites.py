# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own runtime is always started under its launch rules (re-reviews of d67b4e25575 and 1c31d63dead,
SHOULD-1).

The rules (``runtime_environment.for_agent_runtime``: no dynamic-loader variables; on Linux the system's program
folders, except for the browser sign-in) are applied in ``child_process.popen``/``run``, keyed on
``agent_runtime.is_agent_runtime(argv[0])``: every Angelo child that goes through that module gets them, whoever
built its environment. The structural scan below finds every other process start in the add-on (every file, every
subpackage; ``subprocess``, ``os`` and ``asyncio`` starters, also under a module alias, a ``from`` import or
``getattr``) and requires each to be classified with why it never starts the agent runtime. The behaviour tests run
each start with ``LD_PRELOAD`` and a user-writable PATH. No Blender, nothing really started.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import (agent_runtime, authentication, child_process, endpoint_catalog,  # noqa: E402
                          runtime_environment, runtime_launch)
from angelo_agent.protocol import RpcError  # noqa: E402

STARTERS = {
    "subprocess": {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "os": {"system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
           "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "posix_spawn",
           "posix_spawnp", "startfile"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    # A direct construction applies the rules too (ChildProcess.__init__), but is listed: re-review NIT-5.
    "child_process": {"ChildProcess"},
}
NOT_THE_RUNTIME = "never the agent runtime: "
# Every process start that bypasses child_process, and why it never starts Angelo's runtime.
CLASSIFIED = {
    "cli_runtime.py:_run_login_shell": NOT_THE_RUNTIME + "the person's login shell, read for its PATH",
    "crash_gpu.py:relaunch": NOT_THE_RUNTIME + "Angelo itself (bpy.app.binary_path), restarted",
    "crash_gpu.py:restart_with_opengl": NOT_THE_RUNTIME + "Angelo itself, restarted (a default argument)",
    "download_names.py:_read": NOT_THE_RUNTIME + "macOS /usr/sbin/scutil --dns",
    "endpoint_keys.py:run_command": NOT_THE_RUNTIME + "the managed policy's key program (one a script made is refused)",
    "engine_link_writeback.py:_run": NOT_THE_RUNTIME + "a background Blender (Angelo's binary) exporting for an engine",
    "engine_preflight_link.py:_run": NOT_THE_RUNTIME + "a background Blender re-importing an export",
    "engine_status.py:_chaos_restart": NOT_THE_RUNTIME + "macOS launchctl",
    "external_setup.py:connect": NOT_THE_RUNTIME + "the person's own Claude Code CLI (claude mcp add)",
    "external_setup.py:disconnect": NOT_THE_RUNTIME + "the person's own Claude Code CLI (claude mcp remove)",
    "fab_browser_ownership.py:_run": NOT_THE_RUNTIME + "the system's process listing for Fab's browser",
    "fab_browser_process.py:__init__": NOT_THE_RUNTIME + "Chrome for Fab's sign-in",
    "fab_browser_process_windows.py:__init__": NOT_THE_RUNTIME + "Chrome for Fab's sign-in on Windows (a ChildProcess)",
    "fab_browser_windows_proof.py:_borrow": NOT_THE_RUNTIME + "names ChildProcess only to check a child's type",
    "fab_browser_profile_session_windows.py:finish_drained": NOT_THE_RUNTIME + "names ChildProcess to check a type",
    "fab_convert.py:run": NOT_THE_RUNTIME + "a background Blender converting a Fab download",
    "headless_pipe.py:__init__": NOT_THE_RUNTIME + "the headless Angelo (Angelo's own binary)",
    "headless_window.py:__init__": NOT_THE_RUNTIME + "Angelo's own binary in its hidden window",
    "headless_window_lock.py:ask": NOT_THE_RUNTIME + "macOS osascript asking the person",
    "pdf_process_output.py:run": NOT_THE_RUNTIME + "the bundled Python reading a PDF",
    "pdf_process_limits.py:<module>": NOT_THE_RUNTIME + "the PDF reader's Windows Popen subclass (the bundled Python)",
    "studio_assets_host.py:convert_batch": NOT_THE_RUNTIME + "a background Blender converting assets",
    "studio_video.py:play": NOT_THE_RUNTIME + "Angelo's own animation player",
    "studio_video.py:reveal": NOT_THE_RUNTIME + "Finder (open -R) or Explorer",
    "unreal_plugin/AngeloLink/Content/Python/angelo_link/editor.py:_bring_angelo_forward":
        NOT_THE_RUNTIME + "runs inside Unreal's editor, opening Angelo's app",
    "update_install.py:start": NOT_THE_RUNTIME + "the updater's install helper",
    "update_stage_mac.py:run": NOT_THE_RUNTIME + "macOS codesign/ditto/hdiutil for an update",
    "update_stage_win.py:authenticode": NOT_THE_RUNTIME + "PowerShell reading an update's signature",
    "web_environment.py:_wait_for_bake": NOT_THE_RUNTIME + "a background Blender baking a web environment",
    "web_export.py:_run_gltfpack": NOT_THE_RUNTIME + "the bundled gltfpack",
    "web_readback.py:_run": NOT_THE_RUNTIME + "a background Blender reading an export back",
    "worker_host.py:_run_group": NOT_THE_RUNTIME + "a worker Blender",
    "worker_launch.py:start_process": NOT_THE_RUNTIME + "a worker Blender",
}
INJECTED = {"PATH": "/home/a/.local/bin:/usr/bin", "HOME": "/home/a", "LD_PRELOAD": "/tmp/x.so",
            "LD_LIBRARY_PATH": "/tmp", "GCONV_PATH": "/tmp/g"}


def start_sites(source, label="module"):
    """``["label:function"]`` of every function (``<module>`` at the top level) that names a process starter: a
    ``subprocess``/``os``/``asyncio`` attribute (under any import alias), a starter ``from``-imported (under any
    name) or ``getattr(<module>, "<starter>")``. Naming counts, not only calling: ``run = run or subprocess.run``."""
    tree = ast.parse(source)
    modules, direct = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in STARTERS:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in STARTERS:
            for alias in node.names:
                if alias.name in STARTERS[node.module]:
                    direct.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.module:   # from . import child_process (as cp)
            for alias in node.names:
                if alias.name in STARTERS:
                    modules[alias.asname or alias.name] = alias.name

    def starts(node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = modules.get(node.value.id)
            return module is not None and node.attr in STARTERS[module]
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return node.id in direct
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and isinstance(node.args[1], ast.Constant)):
            module = modules.get(node.args[0].id)
            return module is not None and node.args[1].value in STARTERS[module]
        return False

    found = set()

    def visit(scope, name):
        for child in ast.iter_child_nodes(scope):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                for default in child.args.defaults + [d for d in child.args.kw_defaults if d is not None]:
                    if any(starts(part) for part in ast.walk(default)):
                        found.add("%s:%s" % (label, child.name))
                continue
            if isinstance(child, ast.ClassDef):
                visit(child, name)
                continue
            if any(starts(part) for part in ast.walk(child)):
                found.add("%s:%s" % (label, name))
    visit(tree, "<module>")
    return sorted(found)


class InventoryTests(unittest.TestCase):
    def test_the_scan_sees_every_shape_of_a_start(self):
        source = ("import subprocess, os\nimport subprocess as sp\nfrom subprocess import Popen as P\n"
                  "from .agent_runtime import executable as runtime\n"
                  "def a():\n    subprocess.run([runtime(), 'x'])\n"
                  "def b(run=None):\n    run = run or sp.run\n    run(['x'])\n"
                  "def c():\n    P(['x'])\n"
                  "def d():\n    getattr(os, 'posix_spawn')('x', ['x'], {})\n"
                  "def e(spawn=subprocess.Popen):\n    spawn(['x'])\n"
                  "class K:\n    def f(self):\n        os.execv('x', ['x'])\n"
                  "def fine():\n    import json\n    return json.dumps({})\n")
        self.assertEqual(start_sites(source), ["module:a", "module:b", "module:c", "module:d", "module:e",
                                               "module:f"])

    def test_every_start_outside_child_process_is_classified(self):
        found = []
        for path in sorted(ADDON.rglob("*.py")):
            if path == ADDON / "child_process.py":
                continue   # The one place the rules are applied (only the add-on's own module of that name).
            found += start_sites(path.read_text(encoding="utf-8"), path.relative_to(ADDON).as_posix())
        for site in found:
            with self.subTest(site=site):
                self.assertIn(site, CLASSIFIED, "start it through child_process (which applies Angelo's runtime "
                                                "rules), or classify it here with why it never starts the runtime")
        for site in CLASSIFIED:
            with self.subTest(stale=site):
                self.assertIn(site, found, "no longer a start: remove it")


FLAG = agent_runtime.IGNORE_USER_CONFIG
PINNED_FLAG = {"app_server_flags": [FLAG]}   # A pin naming a build with the flag (agent_runtime.check_start).


class ChokePointTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(runtime_environment.sys, "platform", "linux"),
                      mock.patch.dict(agent_runtime.PIN, PINNED_FLAG)):
            patch.start()
            self.addCleanup(patch.stop)

    def assertOwnRules(self, env, path):
        for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "GCONV_PATH"):
            self.assertNotIn(name, env)
        self.assertEqual(env["PATH"], path)

    def captured(self, start, argv, runtime=True, **kwargs):
        seen = {}

        def record(args, **given):
            seen.update(given.get("env") or {})
            return None
        with mock.patch.object(agent_runtime, "is_agent_runtime", return_value=runtime), \
                mock.patch.object(child_process.os, "name", "posix"), \
                mock.patch.object(child_process.subprocess, "Popen", side_effect=record), \
                mock.patch.object(child_process.subprocess, "run", side_effect=record):
            start(argv, env=dict(INJECTED), **kwargs)
        return seen

    def test_every_start_of_the_runtime_gets_the_rules(self):
        self.assertOwnRules(self.captured(child_process.popen, ["/x/codex", "app-server", FLAG]),
                            runtime_environment.SYSTEM_PATH)
        self.assertOwnRules(self.captured(child_process.run, ["/x/codex", "login", "status"]),
                            runtime_environment.SYSTEM_PATH)
        self.assertOwnRules(self.captured(child_process.popen, ["/x/codex", "login"], keep_gui_programs=True),
                            INJECTED["PATH"])

    def test_the_runtimes_app_server_never_starts_with_the_persons_config(self):
        """Coordinator ruling, 2026-11-20: every start of the agent runtime's app-server carries --ignore-user-config,
        and a pin naming a build without it refuses to start one at all (fail closed, never the person's config)."""
        for start in (child_process.popen, child_process.run):
            with self.subTest(start.__name__), self.assertRaises(RpcError):
                self.captured(start, ["/x/codex", "app-server", "--listen", "stdio://"])
        with mock.patch.dict(agent_runtime.PIN, {"app_server_flags": []}), self.assertRaises(RpcError):
            self.captured(child_process.popen, ["/x/codex", "app-server", FLAG])
        self.assertEqual(self.captured(child_process.popen, ["/usr/local/bin/codex", "app-server"], runtime=False),
                         INJECTED, "the person's own Codex has no such flag (a build without the runtime)")

    def test_only_the_command_lines_angelo_builds_start_the_runtime(self):
        """Re-review of ffa4cb56717, SHOULD-1: Codex accepts root options before a subcommand, and exec, review, resume
        and the terminal UI also load the person's config. Only the shapes Angelo builds pass; every other is refused
        with a translated sentence, before a process exists."""
        from angelo_agent.display_text import Text
        allowed = (["/x/codex", "--version"], ["/x/codex", "debug", "models", "--bundled"],
                   ["/x/codex", "login"], ["/x/codex", "login", "status"],
                   ["/x/codex", "-c", 'cli_auth_credentials_store="keyring"', "login", "--with-api-key"],
                   ["/x/codex", "app-server", FLAG, "--listen", "stdio://", "-c", 'web_search="disabled"'])
        for argv in allowed:
            with self.subTest(allowed=argv):
                self.captured(child_process.run, argv)
        refused = (["/x/codex", "-c", "k=v", "app-server", FLAG], ["/x/codex", "--enable", "x", "app-server", FLAG],
                   ["/x/codex", "exec", "hi"], ["/x/codex", "e", "hi"], ["/x/codex", "review"], ["/x/codex", "resume"],
                   ["/x/codex", "hello"], ["/x/codex"], ["/x/codex", "app-server", FLAG, "--bogus", "1"],
                   ["/x/codex", "app-server", "--listen", "stdio://", FLAG], ["/x/codex", "login", "--device-auth"],
                   ["/x/codex", "-c", "model=x", "login"])
        for argv in refused:
            for start in (child_process.popen, child_process.run):
                with self.subTest(refused=argv, start=start.__name__), self.assertRaises(RpcError) as caught:
                    self.captured(start, argv)
                self.assertIsInstance(caught.exception.args[0], Text, "translated (display_text.Text)")
        with mock.patch.object(agent_runtime, "executable", return_value="/x/codex"), self.assertRaises(RpcError):
            self.captured(child_process.popen, "/x/codex exec hi", runtime=False)

    def test_a_shims_rewrite_never_hides_the_runtime(self):
        """The Windows npm-shim rewrite (node.exe and its script) happens before child_process: the command line is
        checked first, in cli_runtime.runtime_command."""
        from angelo_agent import cli_runtime
        with mock.patch.object(agent_runtime, "is_agent_runtime", return_value=True), \
                mock.patch.object(cli_runtime.cli_windows, "launch_command",
                                  side_effect=AssertionError("rewritten before the check")), \
                self.assertRaises(RpcError):
            cli_runtime.runtime_command(["C:/x/codex.cmd", "exec", "hi"], {}, windows=True)

    def test_another_program_is_left_as_built(self):
        self.assertEqual(self.captured(child_process.popen, ["/usr/bin/git", "status"], runtime=False), INJECTED)

    def test_no_environment_given_means_the_process_environment_under_the_rules(self):
        seen = {}
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "/tmp/x.so"}), \
                mock.patch.object(agent_runtime, "is_agent_runtime", return_value=True), \
                mock.patch.object(child_process.os, "name", "posix"), \
                mock.patch.object(child_process.subprocess, "Popen",
                                  side_effect=lambda args, **given: seen.update(given["env"])):
            child_process.popen(["/x/codex", "app-server", FLAG])
        self.assertNotIn("LD_PRELOAD", seen)


class ResolvedProgramTests(unittest.TestCase):
    """Re-read of 0b6b952594, SHOULD-1: the allowlist decides on the program the OS will run. A real runtime file in a
    temporary folder; ``agent_runtime.executable`` names it, ``is_agent_runtime`` compares the files themselves."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = os.path.realpath(folder.name)
        # A program file the host could run: codex.exe on Windows, which CreateProcess finds for "codex" too.
        self.runtime = os.path.join(self.folder, "codex.exe" if sys.platform == "win32" else "codex")
        with open(self.runtime, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(self.runtime, 0o755)
        self.started = []
        for patch in (mock.patch.object(agent_runtime, "executable", return_value=self.runtime),
                      mock.patch.object(agent_runtime, "override", return_value=None),   # The bundled runtime.
                      mock.patch.dict(agent_runtime.PIN, PINNED_FLAG),
                      mock.patch.object(child_process.os, "name", "posix"),
                      mock.patch.object(child_process.subprocess, "Popen",
                                        side_effect=lambda args, **k: self.started.append(args)),
                      mock.patch.object(child_process.subprocess, "run",
                                        side_effect=lambda args, **k: self.started.append(args))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_every_spelling_of_a_start_is_decided_on_what_runs(self):
        env = {"PATH": self.folder + os.pathsep + "/usr/bin"}
        refused = {
            "executable= keyword": (["x", "exec", "hi"], {"executable": self.runtime}),
            "relative program and cwd": (["./codex", "exec", "hi"], {"cwd": self.folder}),
            "bare name on the child's PATH": (["codex", "exec", "hi"], {"env": env}),
            "bytes program": ([os.fsencode(self.runtime), "exec", "hi"], {}),
            "shell=True with a list": ([self.runtime + " exec hi"], {"shell": True}),
            "a string through its folder": ("cd %s && ./codex exec hi" % self.folder, {"shell": True}),
            "env wrapper": (["/usr/bin/env", self.runtime, "exec", "hi"], {}),
            "sh -c wrapper": (["/bin/sh", "-c", self.runtime + " exec hi"], {}),
            "a network listener": ([self.runtime, "app-server", FLAG, "--listen", "ws://0.0.0.0:4500"], {}),
        }
        for label, (args, kwargs) in refused.items():
            for start in (child_process.popen, child_process.run):
                with self.subTest(label, start=start.__name__), self.assertRaises(RpcError):
                    start(args, **kwargs)
        self.assertEqual(self.started, [], "nothing started")
        allowed = ([self.runtime, "app-server", FLAG, "--listen", "stdio://"],
                   ["./codex", "app-server", FLAG, "--listen", "stdio://"])
        for args in allowed:
            child_process.popen(args, cwd=self.folder, env=dict(env))
        self.assertEqual(len(self.started), 2)

    def test_a_windows_path_without_its_extension_is_the_file_createprocess_runs(self):
        """CreateProcess appends .exe to a path without an extension (any host: Windows rules asked for by name)."""
        tool = os.path.join(self.folder, "tool.exe")
        with open(tool, "w", encoding="utf-8") as handle:
            handle.write("MZ")
        found = agent_runtime.started_programs([os.path.join(self.folder, "tool")], {}, windows=True)
        self.assertTrue(any(os.path.isfile(path) and os.path.samefile(path, tool) for path in found), found)
        self.assertTrue(os.path.samefile(agent_runtime.started_program([os.path.join(self.folder, "tool")], {},
                                                                        windows=True), tool))
        self.assertFalse(any(os.path.isfile(path) for path in agent_runtime.started_programs(
            [os.path.join(self.folder, "tool")], {}, windows=False)), "POSIX runs no tool.exe for 'tool'")
        missing = os.path.join(self.folder, "none")
        self.assertEqual(agent_runtime.started_program([missing], {}, windows=True), missing, "as written")

    def test_a_direct_childprocess_is_refused_before_anything_starts(self):
        """``ChildProcess.__init__`` applies the rules itself when constructed directly (Fab's Windows browser)."""
        def reached(*args, **kwargs):
            raise AssertionError("Popen reached")
        base = child_process.ChildProcess.__bases__[0]   # The real Popen (setUp stands in for the module's name).
        with mock.patch.object(base, "__init__", new=reached), self.assertRaises(RpcError):
            child_process.ChildProcess([self.runtime, "exec", "hi"])


class BuilderTests(unittest.TestCase):
    """What each builder of a runtime environment hands to child_process (the start applies the rules too)."""

    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.state = state.name
        for patch in (mock.patch.object(agent_runtime, "is_agent_runtime", return_value=True),
                      mock.patch.object(runtime_environment.sys, "platform", "linux"),
                      mock.patch.dict(agent_runtime.PIN, PINNED_FLAG)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_app_server(self):
        with mock.patch.object(runtime_launch, "child_environment", side_effect=lambda *a, **k: dict(INJECTED)), \
                mock.patch.object(runtime_launch, "profile_environment", side_effect=lambda env, *a, **k: env), \
                mock.patch.object(runtime_launch.sys, "platform", "linux"):
            command, env = runtime_launch.codex_command("/x/codex", "codex_subscription", self.state)
        self.assertEqual(command[1:3], ["app-server", FLAG])
        self.assertNotIn("LD_PRELOAD", env)
        self.assertEqual(env["PATH"], runtime_environment.SYSTEM_PATH)

    def test_only_the_browser_sign_in_keeps_the_persons_path(self):
        with mock.patch.object(authentication, "find_executable", return_value="/x/codex"), \
                mock.patch.object(authentication, "child_environment", side_effect=lambda *a, **k: dict(INJECTED)), \
                mock.patch.object(authentication, "profile_environment", side_effect=lambda env, *a, **k: env), \
                mock.patch.object(authentication, "with_runtime_path", side_effect=lambda env, executable: env):
            system = runtime_environment.SYSTEM_PATH
            for login, api_key, path in ((False, False, system), (True, False, INJECTED["PATH"]), (True, True, system)):
                mode = "codex_api" if api_key else "codex_subscription"
                command, env = authentication.native_auth_command(mode, {}, "/tmp/state", login, api_key)
                with self.subTest(command=command[1:]):
                    self.assertNotIn("LD_PRELOAD", env)
                    self.assertEqual(env["PATH"], path)
                    self.assertEqual(authentication.browser_sign_in(login, api_key), path == INJECTED["PATH"])

    def test_the_catalog_listing_starts_through_child_process(self):
        seen = {}
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(endpoint_catalog, "_signature", return_value=("x",)), \
                mock.patch.object(endpoint_catalog.owned_paths, "require_runnable", side_effect=lambda path: path), \
                mock.patch.object(endpoint_catalog.essential_environment, "environment",
                                  side_effect=lambda *a, **k: dict(INJECTED, **k)), \
                mock.patch.object(child_process, "run",
                                  side_effect=lambda argv, **k: seen.update(k["env"]) or SimpleNamespace(returncode=1,
                                                                                                          stdout="")):
            endpoint_catalog._cache.clear()
            endpoint_catalog.bundled("/x/codex", home)
        self.assertNotIn("LD_PRELOAD", seen)
        self.assertEqual(seen["PATH"], runtime_environment.SYSTEM_PATH)


if __name__ == "__main__":
    unittest.main()
