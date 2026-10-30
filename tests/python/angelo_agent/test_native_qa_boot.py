# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every GUI suite waits for Angelo's start-up through one gate, ``qa_boot.started`` (1.0.5).

A suite that waited on Director's and the layout's timers alone acted in the quiet second before Manual's Studio and
Flows tabs were built and found no 3D view (test_blender_director_navigation, a1c3a0dc6cf drag-class line). Covered:
the gate's rule (timers, a build running or about to start, a build held back by the suite's own card) and that no
GUI suite or QA helper waits on those timers itself; a timer's value recorded as a diagnostic or a check is not a wait.
"""

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
TIMERS = {("director", "_timer"), ("ui", "_layout_timer")}


def load_gate(director_timer=None, layout_timer=None, tabs=True, building=False, missing=(), quiet=True,
              holding=None, owed=False, startup_timer=None, cover_since=None):
    """``qa_boot`` loaded from its file against stand-ins; nothing is left in ``sys.modules``."""
    package = types.ModuleType("angelo_agent")
    package.director = SimpleNamespace(_timer=director_timer)
    package.ui = SimpleNamespace(_layout_timer=layout_timer)
    modules = {"angelo_agent": package, "angelo_agent.director": package.director, "angelo_agent.ui": package.ui,
               "bpy": SimpleNamespace(
                   context=SimpleNamespace(window_manager=SimpleNamespace(windows=["window"])),
                   app=SimpleNamespace(timers=SimpleNamespace(is_registered=lambda timer: timer is not None))),
               "angelo_default_layout": SimpleNamespace(_timer=startup_timer, _cover={"since": cover_since,
                                                                                       "handlers": []})}
    if tabs:
        package.studio_workspaces = SimpleNamespace(_building=building, _holding=holding, missing=lambda: list(missing),
                                                    startup_quiet=lambda window: quiet,
                                                    startup_build_pending=lambda: owed)
        modules["angelo_agent.studio_workspaces"] = package.studio_workspaces
    else:
        modules["angelo_agent.studio_workspaces"] = None   # ImportError on import, as on a build without the tabs.
    spec = importlib.util.spec_from_file_location("_qa_boot_under_test", HERE / "qa_boot.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    patch = mock.patch.dict(sys.modules, modules)
    return gate, patch


def started(**state):
    gate, patch = load_gate(**state)
    with patch:
        return gate.started()


class GateTests(unittest.TestCase):
    def test_waits_for_both_layout_timers(self):
        self.assertFalse(started(director_timer=object()))
        self.assertFalse(started(layout_timer=object()))
        self.assertTrue(started())

    def test_waits_while_the_tabs_build_runs_or_is_about_to(self):
        self.assertFalse(started(building=True))
        self.assertFalse(started(missing=["studio"], quiet=True), "quiet with tabs missing: the build is next")

    def test_waits_for_angelos_start_up_script_that_builds_director(self):
        self.assertFalse(started(startup_timer=object()), "a Manual request before it runs is undone by it")
        gate, patch = load_gate(startup_timer=object(), building=True)
        with patch:
            self.assertEqual(gate.waiting_on(), ["Angelo's start-up layout timer", "Manual's tab build running"])

    def test_waits_for_the_start_up_build_and_a_build_holding_the_layout(self):
        self.assertFalse(started(owed=True, missing=["studio"], quiet=False),
                         "the session's first build runs under the start-up cover: owed until it ran")
        self.assertFalse(started(holding=object()))

    def test_waits_until_the_start_up_cover_is_off_the_window(self):
        """The cover goes on a 0.25 s poll after the layout settles: a frame captured before shows only the cover."""
        self.assertFalse(started(cover_since=1.0))
        gate, patch = load_gate(cover_since=1.0)
        with patch:
            self.assertEqual(gate.waiting_on(), ["Angelo's start-up cover"])
        self.assertTrue(started(cover_since=None))

    def test_a_build_held_back_by_the_suites_own_card_is_not_waited_for(self):
        self.assertTrue(started(missing=["studio"], quiet=False), "a card or the welcome holds it: the suite lets go")

    def test_a_build_without_the_tabs_module_is_started_once_the_timers_are_idle(self):
        self.assertTrue(started(tabs=False))


def hand_rolled_waits(source):
    """(line, text) of each comparison of Director's or the layout's timer that is not a recorded value: one inside
    a dict literal or assigned to a subscript (``state["checks"][...] = ...``) records; anything else waits."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def recorded(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.Dict):
                return True
            if isinstance(node, ast.Assign):
                return all(isinstance(target, ast.Subscript) for target in node.targets)
        return False
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(
                isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name)
                and (part.value.id, part.attr) in TIMERS for part in [node.left] + node.comparators):
            if not recorded(node):
                found.append((node.lineno, ast.unparse(node)))
    return found


class NoHandRolledGateTests(unittest.TestCase):
    def test_every_suite_waits_through_qa_boot(self):
        helpers = sorted(p for p in HERE.glob("qa_*.py") if p.name != "qa_boot.py")
        files = sorted(HERE.glob("test_blender_*.py")) + helpers
        found = {p.name: waits for p in files if (waits := hand_rolled_waits(p.read_text(encoding="utf-8")))}
        self.assertEqual(found, {}, "wait with qa_boot.started(): it also waits for Manual's tab build")

    def test_the_check_sees_waits_and_spares_recorded_values(self):
        waits = ("def tick():\n    if director._timer is not None or ui._layout_timer is not None:\n        return .2\n"
                 "def settled():\n    return ui._layout_timer is None\n"
                 "until(lambda: director._timer is None)\n")
        recorded = ("state['checks']['no_relayout'] = ui._layout_timer is None\n"
                    "state['debug'] = {'layout_timer': ui._layout_timer is not None}\n")
        self.assertEqual(len(hand_rolled_waits(waits)), 4)
        self.assertEqual(hand_rolled_waits(recorded), [])


LAYOUT_ACTIONS = ("open_chat", "request_mode", "request_chat_dock", "workspace_mode")


def acts_before_the_gate(source):
    """(function, line) of the first layout change (a chat dock, a mode switch) a function makes before it has waited
    on the gate (``qa_boot.started`` or a ``settled`` helper on it): a dock asked for while the start-up still moved
    the layout was refused ("The chat dock is already moving", agent_workspace_ui on efa6897cd0b)."""
    found = []
    for function in (n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        calls = sorted((n for n in ast.walk(function) if isinstance(n, ast.Call)),
                       key=lambda n: (n.lineno, n.col_offset))
        for call in calls:
            name = ast.unparse(call.func)
            if name in ("qa_boot.started", "settled"):
                break
            if name.rsplit(".", 1)[-1] in LAYOUT_ACTIONS:
                found.append((function.name, call.lineno))
                break
    return found


class GateFirstTests(unittest.TestCase):
    def test_no_suite_docks_or_switches_mode_before_the_gate(self):
        found = {p.name: acts for p in sorted(HERE.glob("test_blender_*.py"))
                 if (acts := acts_before_the_gate(p.read_text(encoding="utf-8")))}
        self.assertEqual(found, {}, "wait on qa_boot.started() before the first open_chat / request_mode")

    def test_the_check_sees_a_dock_before_the_gate(self):
        early = "def tick():\n    if phase == 'boot':\n        bpy.ops.angelo.open_chat(side='RIGHT')\n" \
                "    if not qa_boot.started():\n        return .1\n"
        late = "def tick():\n    if phase == 'boot':\n        if not qa_boot.started():\n            return .1\n" \
               "        bpy.ops.angelo.open_chat(side='RIGHT')\n"
        helper = "def tick():\n    if not settled():\n        return .3\n    director.request_mode(w, 'MANUAL')\n"
        self.assertEqual(acts_before_the_gate(early), [("tick", 3)])
        self.assertEqual(acts_before_the_gate(late), [])
        self.assertEqual(acts_before_the_gate(helper), [])


def enable_waits_without_the_gate(source):
    """Line of each wait for the host to become enabled (``"angelo_agent" not in ...addons`` ending in a ``return``)
    whose condition does not also wait on the gate. Enabling the host mid-run (the native Coding Agent choice enables
    it lazily) schedules the session's first Studio/Flows tab build, which switches the window through other
    workspaces for a few frames: the suite's saved area is in none of their screens (test_blender_compiled_gui on the
    1.0.7 cut 744f498d0a6: StopIteration finding it, 2 runs in 4)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.If) or not node.body or not isinstance(node.body[0], ast.Return):
            continue
        waits_for_host = any(
            isinstance(part, ast.Compare) and isinstance(part.left, ast.Constant) and part.left.value == "angelo_agent"
            and isinstance(part.ops[0], ast.NotIn) and ast.unparse(part.comparators[0]).endswith("preferences.addons")
            for part in ast.walk(node.test))
        if waits_for_host and "qa_boot.started()" not in ast.unparse(node.test):
            found.append(node.lineno)
    return found


class HostEnableGateTests(unittest.TestCase):
    def test_a_suite_waiting_for_the_host_to_enable_also_waits_on_the_gate(self):
        found = {p.name: lines for p in sorted(HERE.glob("test_blender_*.py"))
                 if (lines := enable_waits_without_the_gate(p.read_text(encoding="utf-8")))}
        self.assertEqual(found, {}, "an enabled host builds Manual's tabs next: wait on qa_boot.started() too")

    def test_the_check_sees_an_enable_wait_without_the_gate(self):
        bare = ("def tick():\n    if 'angelo_agent' not in bpy.context.preferences.addons or not area().history:\n"
                "        return .1\n")
        gated = ("def tick():\n    if ('angelo_agent' not in bpy.context.preferences.addons or not qa_boot.started()\n"
                 "            or not area().history):\n        return .1\n")
        recorded = "state['checks']['host_not_pre_enabled'] = 'angelo_agent' not in bpy.context.preferences.addons\n"
        self.assertEqual(enable_waits_without_the_gate(bare), [2])
        self.assertEqual(enable_waits_without_the_gate(gated), [])
        self.assertEqual(enable_waits_without_the_gate(recorded), [])


if __name__ == "__main__":
    unittest.main()
