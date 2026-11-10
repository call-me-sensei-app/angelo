# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every GUI suite that runs Angelo across event-loop passes waits for its start-up first (1.0.8).

Enabling the add-on schedules the session's first tab build as soon as the layout is idle
(``studio_workspaces.ensure``), even with the default chat opted out; the build switches the window through the tabs
it makes. A suite that enabled Angelo and acted at a fixed first second raced it: a Help menu opened under the build
closed (test_blender_app_identity, 1.0.5 RC), an area converted mid-build belonged to a tab (flow_setup_states kept a
per-case workaround), and test_blender_compiled_gui's lookup found the window on another tab (1.0.7, 2 runs in 4).
``qa_boot.gated`` holds a suite's timer until the gate opens. Covered: the wrapper's behaviour, and a lint over every
GUI suite: one that starts Angelo (``addon_utils.enable("angelo_agent")``, or Angelo's default start-up) as its script
loads and registers a timer, or starts it inside a callback and keeps a timer running after it (a re-armed tick, a
wrapped or built callback), refers to the gate itself or through a helper module it imports. A one-shot callback
that enables Angelo, does its work and quits in the same call cannot interleave with the build, which runs on
later timers.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_native_qa_boot import load_gate

HERE = Path(__file__).resolve().parent


def _calls(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _constant(node, value):
    return isinstance(node, ast.Constant) and node.value == value


def _starts_angelo(call):
    name = ast.unparse(call.func)
    if name.rsplit(".", 1)[-1] == "enable" and call.args and _constant(call.args[0], "angelo_agent"):
        return True
    if name.endswith("addon_enable") and any(k.arg == "module" and _constant(k.value, "angelo_agent")
                                             for k in call.keywords):
        return True
    return name.endswith("product_env.clear") and bool(call.args) and _constant(call.args[0], "DISABLE_DEFAULT_CHAT")


def runs_angelo(tree):
    """Where the suite starts Angelo: "module" when it enables the host add-on (or opts back in to Angelo's default
    start-up, which enables it) as the script loads, before any of its timers runs; "callback" when only a function
    does; None when it never does."""
    inside = {id(node) for function in ast.walk(tree) if isinstance(function, (ast.FunctionDef, ast.Lambda))
              for node in ast.walk(function) if node is not function}
    starts = [call for call in _calls(tree) if _starts_angelo(call)]
    if any(id(call) not in inside for call in starts):
        return "module"
    return "callback" if starts else None


def _one_shot(function):
    """A function whose every return is None: registered as a timer, it runs once."""
    return not any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(function)) and all(
        node.value is None or _constant(node.value, None)
        for node in ast.walk(function) if isinstance(node, ast.Return))


def keeps_running(tree):
    """Whether a timer the suite registers runs again after its first call: any callback that is not a module
    function returning None every time (a re-armed tick, a wrapped or built callback, a generator driver)."""
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for call in _calls(tree):
        if not ast.unparse(call.func).endswith("timers.register") or not call.args:
            continue
        callback = call.args[0]
        if isinstance(callback, ast.Lambda):
            continue   # A one-line callback (a quit): nothing runs after it.
        if isinstance(callback, ast.Name) and callback.id in functions and _one_shot(functions[callback.id]):
            continue
        return True
    return False


def gated(source, folder=HERE):
    """The suite waits on the gate: it names ``qa_boot`` or imports a helper of this folder that does."""
    if "qa_boot." in source:
        return True
    for module in set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", source, re.M)):
        helper = folder / (module + ".py")
        if not module.startswith("test_blender_") and helper.is_file() and "qa_boot." in helper.read_text(
                encoding="utf-8"):
            return True
    return False


def registers_timers(tree):
    return any(ast.unparse(call.func).endswith("timers.register") for call in _calls(tree))


def needs_gate(tree, default_start_up=False):
    """Angelo started as the script loads, then any timer: the build runs before or under it. Started inside a
    callback: only a timer that runs again afterwards can meet the build (a one-shot callback ends first)."""
    where = "module" if default_start_up else runs_angelo(tree)
    return (where == "module" and registers_timers(tree)) or (where == "callback" and keeps_running(tree))


def ungated(source, folder=HERE, default_start_up=False):
    return needs_gate(ast.parse(source), default_start_up) and not gated(source, folder)


def window_suites():
    import native_suite_manifest as manifest
    return [suite for suite in manifest.SUITES if suite.kind != manifest.BACKGROUND]


class EveryWindowSuiteWaitsForStartUpTests(unittest.TestCase):
    def test_every_window_suite_running_angelo_waits_on_the_gate(self):
        found = []
        for suite in window_suites():
            path = HERE / (suite.script if suite.script.endswith(".py") else suite.script + ".py")
            source = path.read_text(encoding="utf-8")
            if ungated(source, default_start_up=getattr(suite, "default_chat", False)):
                found.append(path.name)
        self.assertEqual(found, [], "register the suite's timer through qa_boot.gated (or wait on qa_boot.started) "
                                    "before it acts: enabling Angelo schedules the first tab build")

    def test_the_lint_sees_a_suite_acting_without_the_gate(self):
        bare = ("import addon_utils, bpy\naddon_utils.enable('angelo_agent', default_set=False)\n"
                "def tick():\n    act()\n    return .2\nbpy.app.timers.register(tick, first_interval=1.0)\n")
        fixed = bare.replace("register(tick", "register(qa_boot.gated(tick)")
        default = ("product_env.clear('DISABLE_DEFAULT_CHAT')\ndef tick():\n    yield 1\n"
                   "bpy.app.timers.register(drive(tick))\n")
        one_shot = ("def run():\n    addon_utils.enable('angelo_agent')\n    work()\n    bpy.ops.wm.quit_blender()\n"
                    "    return None\nbpy.app.timers.register(run, first_interval=1)\n")
        enabled_then_one_tick = ("addon_utils.enable('angelo_agent')\ndef tick():\n    act()\n    return None\n"
                                 "bpy.app.timers.register(tick, first_interval=1)\n")
        no_angelo = "def tick():\n    return .2\nbpy.app.timers.register(tick)\n"
        self.assertTrue(ungated(bare))
        self.assertFalse(ungated(fixed))
        self.assertTrue(ungated(default))
        self.assertFalse(ungated(one_shot), "enables, works and quits in one call: the build runs on later timers")
        self.assertTrue(ungated(enabled_then_one_tick), "enabled as the script loads: the build may run by then")
        self.assertFalse(ungated(no_angelo))

    def test_a_helper_module_that_gates_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "suite_harness.py").write_text("def run(step):\n    return qa_boot.gated(step)\n")
            source = ("from suite_harness import run\naddon_utils.enable('angelo_agent')\n"
                      "bpy.app.timers.register(run(step))\n")
            self.assertFalse(ungated(source, Path(folder)))
            self.assertTrue(ungated(source.replace("suite_harness", "other_helper"), Path(folder)))


class GatedTests(unittest.TestCase):
    """``qa_boot.gated``: the callback runs only once the gate opens, and a gate that never opens fails loudly."""

    def gate(self, enabled=True, **state):
        gate, patch = load_gate(**state)
        quits = []
        patch.start()
        self.addCleanup(patch.stop)
        sys.modules["bpy"].ops = SimpleNamespace(wm=SimpleNamespace(quit_blender=lambda: quits.append(True)))
        extra = mock.patch.dict(sys.modules, {"addon_utils": SimpleNamespace(check=lambda name: (False, enabled)),
                                              "qa_waits": SimpleNamespace(timeout_scale=lambda: 1.0)})
        extra.start()
        self.addCleanup(extra.stop)
        return gate, quits

    def test_the_callback_waits_while_the_build_runs_then_runs(self):
        gate, _quits = self.gate(building=True)
        calls = []
        tick = gate.gated(lambda: calls.append(1) or .5)
        self.assertEqual(tick(), 0.1)
        self.assertEqual(calls, [])
        sys.modules["angelo_agent.studio_workspaces"]._building = False
        self.assertEqual(tick(), .5)
        self.assertEqual(tick(), .5, "once open, every call is the callback's own")
        self.assertEqual(calls, [1, 1])

    def test_nothing_to_wait_for_without_angelo(self):
        gate, _quits = self.gate(enabled=False, building=True)
        self.assertEqual(gate.gated(lambda: 7)(), 7)

    def test_a_gate_that_never_opens_quits_and_names_what_it_waited_for(self):
        gate, quits = self.gate(building=True)
        now = [0.0]
        tick = gate.gated(lambda: 1, limit=5, clock=lambda: now[0])
        self.assertEqual(tick(), 0.1)
        now[0] = 6.0
        with self.assertRaisesRegex(RuntimeError, "Manual's tab build running"):
            tick()
        self.assertEqual(quits, [True])


if __name__ == "__main__":
    unittest.main()
