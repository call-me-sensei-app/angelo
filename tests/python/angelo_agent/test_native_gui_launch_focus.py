# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every automated launch that opens a window gets its window flags from ``gui_launch.window_flags``.

A window an automated run opens must never cover or interrupt the person using the machine. Every launcher under
``tests/python/angelo_agent`` and ``scripts/angelo`` (the suite runner, suites' host drivers, live evals, benchmarks,
stress scripts, smokes) that builds a command line for the app, or for stock Blender beside it, without
``--background`` must call ``gui_launch.window_flags`` in the function that builds it (or an enclosing one), unless
``gui_launch.FOCUSED_LAUNCHES`` names the launch with its reason. PowerShell and shell scripts cannot import the
helper, so each of their app command lines must carry ``--no-window-focus`` itself.

A command line is a list holding a computed element (the executable) and a Blender-only argument
(``--window-geometry``, ``--enable-event-simulate``...), or any list holding an argument that only starts the app
(``--factory-startup``, ``--disable-autoexec``, ``--python-exit-code``), so a command built in pieces is found by its
first piece. ``--python`` alone is not one, since uv and pip take it too. A launcher may not ask for the ``front``
window state literally (only through a harness option, or with a ``FOCUSED_LAUNCHES`` entry). The known launchers
are listed below, so a scanner that stops recognising them fails instead of passing everything. Not seen: a command
in a shell string, one whose only app arguments are appended one by one or concatenated from a list holding none that
starts the app, one with no Blender-only argument at all, and a ``window_flags`` call whose result is not used.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import gui_launch  # noqa: E402

SCOPES = (ROOT / "tests/python/angelo_agent", ROOT / "scripts/angelo")
# Arguments only a Blender command line has. A list starting with a computed executable (not a string literal) and
# holding one of them is an app command; an all-literal list is an argument fragment spliced into one.
APP_ARGUMENTS = {"--factory-startup", "--disable-autoexec", "--python-exit-code", "--enable-event-simulate",
                 "--no-window-focus", "--window-geometry", "--python-expr", "--offline-mode", "--online-mode",
                 "--python-use-system-env", "--open-last", "--no-window-frame", "--window-fullscreen",
                 "--window-maximized"}
# Arguments that only start the app: any list holding one is (part of) an app command.
LAUNCH_ARGUMENTS = {"--factory-startup", "--disable-autoexec", "--python-exit-code"}
# Arguments of a command that opens no window.
NO_WINDOW_ARGUMENTS = {"--background", "-b", "--version", "-v", "--help", "-h", "--command", "-c"}
# Every launcher that opens windows today, as "file:function". The scanner must find each of them.
KNOWN_LAUNCHERS = {
    "tests/python/angelo_agent/run_native_suites.py:build_command",
    "tests/python/angelo_agent/eval_live_agent.py:run_trial",
    "tests/python/angelo_agent/bench_mcp_trial.py:launch_blender",
    "tests/python/angelo_agent/stress_blender_world_redo.py:run",
    "tests/python/angelo_agent/stress_blender_multires_undo.py:run",
    "tests/python/angelo_agent/test_blender_app_identity.py:phase",
    "tests/python/angelo_agent/test_blender_settings_import.py:make_profile",
    "tests/python/angelo_agent/test_blender_settings_import.py:phase",
    "tests/python/angelo_agent/test_blender_prefs_and_quit.py:phase",
    "tests/python/angelo_agent/test_blender_legacy_migration.py:phase",
    "tests/python/angelo_agent/test_blender_memory_project_resume.py:phase",
    "tests/python/angelo_agent/test_blender_external_cli.py:run_host",
    "tests/python/angelo_agent/test_blender_first_launch_frames.py:phase",
}
KNOWN_SCRIPT_LAUNCHES = {"scripts/angelo/windows_gui_checks.ps1", "scripts/angelo/windows_agent_gui.ps1"}


class _Launches(ast.NodeVisitor):
    """Window command lines in one module, each with its chain of enclosing functions."""

    def __init__(self):
        self.stack = ["<module>"]
        self.flags_in = set()   # Functions (as their chain) that call window_flags.
        self.commands = []      # (chain, line)
        self.literal_front = []  # (chain, line) of window_flags("front", ...)

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name == "window_flags":
            self.flags_in.add(tuple(self.stack))
            state = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "window_state"), None)
            if isinstance(state, ast.Constant) and state.value == "front":
                self.literal_front.append((tuple(self.stack), node.lineno))
        self.generic_visit(node)

    def _sequence(self, node):
        values = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        computed = any(not isinstance(e, ast.Constant) for e in node.elts)
        if ((computed and values & APP_ARGUMENTS) or values & LAUNCH_ARGUMENTS) and not values & NO_WINDOW_ARGUMENTS:
            self.commands.append((tuple(self.stack), node.lineno))
        self.generic_visit(node)

    visit_List = visit_Tuple = _sequence


def python_launchers(source, relative):
    """``{"file:function": (lines, has_flags)}`` for every function of ``source`` that builds a window command."""
    visitor = _Launches()
    visitor.visit(ast.parse(source))
    found = {}
    for chain, line in visitor.literal_front:   # A literal front state is a focused launch: it must be listed.
        found["%s:%s" % (relative, chain[-1])] = ((line,), False)
    for chain, line in visitor.commands:
        key = "%s:%s" % (relative, chain[-1])
        # Its own function or an enclosing one; a module-level call counts only for module-level commands.
        has_flags = any(chain[:depth] in visitor.flags_in for depth in range(min(2, len(chain)), len(chain) + 1))
        lines, flagged = found.get(key, ((), True))
        found[key] = (lines + (line,), flagged and has_flags)
    return found


def script_launches(text):
    """Line numbers of shell/PowerShell app command lines without ``--no-window-focus``, and of all of them."""
    missing, every = [], []
    for number, line in enumerate(text.splitlines(), 1):
        tokens = set(re.findall(r"(?<![\w-])--?[\w-]+", line))
        if tokens & APP_ARGUMENTS and not tokens & NO_WINDOW_ARGUMENTS:
            every.append(number)
            if gui_launch.NO_WINDOW_FOCUS not in tokens:
                missing.append(number)
    return missing, every


def launcher_files():
    for scope in SCOPES:
        for path in sorted(scope.rglob("*")):
            # Host unit tests (test_native_*) never start the app: CI runs them without one.
            if path.is_file() and not path.name.startswith("test_native_") and path.name != "gui_launch.py":
                yield path


class GuiLaunchFocus(unittest.TestCase):
    def test_every_window_launch_uses_the_shared_flags(self):
        found, scripts, unflagged = {}, set(), []
        for path in launcher_files():
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix == ".py":
                for key, (lines, has_flags) in python_launchers(path.read_text(encoding="utf-8"), relative).items():
                    found[key] = lines
                    if not has_flags and key not in gui_launch.FOCUSED_LAUNCHES:
                        unflagged.append("%s (lines %s)" % (key, ", ".join(map(str, lines))))
            elif path.suffix in (".ps1", ".psm1", ".sh", ".bat", ".cmd"):
                missing, every = script_launches(path.read_text(encoding="utf-8", errors="replace"))
                if every:
                    scripts.add(relative)
                unflagged += ["%s:%d" % (relative, number) for number in missing]
        self.assertEqual(unflagged, [], "a launch opens a window without gui_launch.window_flags; use it, or list "
                                        "a deliberately focused launch in gui_launch.FOCUSED_LAUNCHES with its reason")
        # Not blind: every launcher known today is still recognised.
        self.assertLessEqual(KNOWN_LAUNCHERS, set(found), "the scanner no longer recognises a known launcher")
        self.assertLessEqual(KNOWN_SCRIPT_LAUNCHES, scripts)

    def test_the_scanner_flags_an_unguarded_window_launch(self):
        def scan(body):
            return python_launchers("import subprocess\n" + body, "x.py")
        bare = scan("def f(b, s):\n    subprocess.run([b, '--factory-startup', '--python', s])\n")
        helped = scan("def f(b, s):\n    subprocess.run([b, '--factory-startup', *gui_launch.window_flags(), s])\n")
        nested = scan("def f(b):\n    flags = gui_launch.window_flags()\n    def g(s):\n"
                      "        return [b, '--disable-autoexec', *flags, '--python', s]\n    return g\n")
        background = scan("def f(b, s):\n    subprocess.run([b, '--background', '--factory-startup', s])\n")
        tooling = scan("def f(uv, v):\n    subprocess.run([uv, 'venv', '--python', v])\n")
        fragment = scan("WINDOW = ('--window-geometry', '60', '60', '1400', '900')\n")
        niced = scan("def f(b, s):\n    subprocess.run(['nice', '-n', '19', b, '--window-geometry', '1', '1', '9', '9'])\n")
        pieces = scan("def f(b):\n    command = [b]\n    command += ['--factory-startup', '--python', 's']\n")
        literal = scan("def f(b):\n    return [b, '--factory-startup', *gui_launch.window_flags('front')]\n")
        chosen = scan("def f(b, a):\n    return [b, '--factory-startup', *gui_launch.window_flags(a.window_state)]\n")
        module_call = scan("FLAGS = gui_launch.window_flags()\ndef f(b):\n    return [b, '--factory-startup']\n")
        self.assertEqual(module_call, {"x.py:f": ((4,), False)})
        self.assertEqual((niced, pieces), ({"x.py:f": ((3,), False)}, {"x.py:f": ((4,), False)}))
        self.assertEqual((literal, chosen), ({"x.py:f": ((3, 3), False)}, {"x.py:f": ((3,), True)}))
        self.assertEqual(bare, {"x.py:f": ((3,), False)})
        self.assertEqual(helped, {"x.py:f": ((3,), True)})
        self.assertEqual(nested, {"x.py:g": ((5,), True)})
        self.assertEqual((background, tooling, fragment), ({}, {}, {}))
        self.assertEqual(script_launches('Start-Process $exe "--factory-startup --python x"\n'
                                         '& $exe --background --factory-startup --python y\n'
                                         'Start-Process $exe "--factory-startup --no-window-focus --python z"'),
                         ([1], [1, 3]))

    def test_only_a_commands_own_list_opens_no_window(self):
        """Counterexamples of the 2026-10-28 reviews (tests c14d0ed, fc20afc818b, 81382641a4f): each a window launch.
        A command is judged by its own list, so a background command elsewhere never exempts one."""
        def scan(body):
            return python_launchers("import subprocess, sys\nTASKPOLICY = '/usr/sbin/taskpolicy'\n" + body, "x.py")
        head = "def f(b, s, x, gui, lock):\n"
        add = "    cmd += ['--factory-startup', '--python', s]\n"
        cases = {
            "rebound": "    cmd = [b, '--background', '--factory-startup']\n    subprocess.run(cmd)\n    cmd = [b]\n" + add,
            "rebound_extend": "    cmd = [b, '-b', '--factory-startup']\n    subprocess.run(cmd)\n    cmd = [b]\n"
                              "    cmd.extend(['--factory-startup', '--python', s])\n",
            "python_c": "    cmd = [sys.executable, '-c', 'print(1)']\n    subprocess.run(cmd)\n    cmd = [b]\n" + add,
            "other_tool_flag": "    extra = ['-v']\n    subprocess.run([b, '--factory-startup', '--python', s] + extra)\n",
            "one_branch": "    cmd = [b]\n    if x:\n        cmd = [b, '-b']\n" + add,
            "try_remove": "    cmd = [b, '--background']\n    try:\n        cmd.remove('--background')\n"
                          "    except ValueError:\n        pass\n" + add,
            "helper": "    cmd = [b, '--background']\n    strip_background(cmd)\n" + add,
            "walrus": "    cmd = [b]\n    x and (cmd := [b, '-b'])\n" + add,
            "taskpolicy_plus": "    subprocess.run([TASKPOLICY, '-b'] + [b, '--factory-startup', '--python', s])\n",
            "taskpolicy_pieces": "    cmd = [TASKPOLICY, '-b']\n" + add,
            "derived_removed": "    base = [b, '--background']\n    cmd = base + ['--factory-startup', '--python', s]\n"
                               "    cmd.remove('--background')\n    subprocess.run(cmd)\n",
            "derived_splice": "    base = [b, '--background']\n    cmd = [*base, '--factory-startup', '--python', s]\n"
                              "    make_gui(cmd)\n    subprocess.run(cmd)\n",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                found = scan(head + body)
                self.assertEqual([flagged for _lines, flagged in found.values()], [False], found)
        enclosing = scan("def f(b):\n    cmd = [b, '--background']\n    def g(s):\n        cmd = [b]\n"
                         "        cmd += ['--factory-startup', s]\n    return g\n")
        module = scan("command = ['blender', '--background']\ndef f(b, s):\n    command = [b]\n"
                      "    command += ['--factory-startup', '--python', s]\n")
        self.assertEqual((list(enclosing), list(module)), (["x.py:g"], ["x.py:f"]))

    def test_window_flags(self):
        self.assertEqual(gui_launch.window_flags(), ["--no-window-focus", "--enable-event-simulate"])
        self.assertEqual(gui_launch.window_flags(input_isolation=False), ["--no-window-focus"])
        self.assertEqual(gui_launch.window_flags("front"), ["--enable-event-simulate"])
        self.assertEqual(gui_launch.window_flags("front", input_isolation=False), [])
        with self.assertRaises(ValueError):
            gui_launch.window_flags("hidden")
        # Only the update end-to-end: its driver presses Restart to Update with real window
        # events and the updater relaunches the new release with no flag. Each entry states its reason.
        self.assertEqual(set(gui_launch.FOCUSED_LAUNCHES), {"tests/python/angelo_agent/update_e2e_macos.py:run",
                                                            "tests/python/angelo_agent/update_e2e_windows.py:run"})
        self.assertTrue(all(len(reason) > 40 for reason in gui_launch.FOCUSED_LAUNCHES.values()))

    def test_a_comparison_never_hides_differing_window_states(self):
        # macOS: stock Blender orders its windows in front despite --no-window-focus; Angelo stays behind.
        behind = {side: gui_launch.side_window_state(side, "behind", "darwin") for side in ("mcp", "angelo")}
        self.assertTrue(behind["mcp"].startswith("front"))
        self.assertTrue(behind["angelo"].startswith("behind"))
        self.assertIn("not comparable", gui_launch.window_symmetry_warning("behind", platform="darwin"))
        # The same state for both: front everywhere, and behind where stock Blender honours the flag.
        self.assertEqual(gui_launch.window_symmetry_warning("front", platform="darwin"), "")
        self.assertEqual(gui_launch.window_symmetry_warning("behind", platform="win32"), "")
        self.assertEqual(gui_launch.window_symmetry_warning("behind", sides=("angelo",), platform="darwin"), "")
        self.assertEqual(gui_launch.window_symmetry_warning("behind", platform="linux"), "")
        # The timed campaign gives a two-sided step the state both sides share; one side alone stays behind.
        self.assertEqual([gui_launch.matched_window_state(p) for p in ("darwin", "win32", "linux")],
                         ["front", "behind", "behind"])
        for platform in ("darwin", "win32", "linux"):
            self.assertEqual(gui_launch.window_symmetry_warning(gui_launch.matched_window_state(platform),
                                                                platform=platform), "")
        import final_runs_plan
        self.assertEqual(final_runs_plan.window_state({"sides": ("mcp", "angelo")}), gui_launch.matched_window_state())
        self.assertEqual(final_runs_plan.window_state({"sides": ("angelo",)}), "behind")


if __name__ == "__main__":
    unittest.main()
