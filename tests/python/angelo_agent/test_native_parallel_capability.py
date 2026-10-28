# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parallel edit is for a capable machine, and a worker shows its own plan's progress (1.0.6, the maintainer's calls).

Without Blender: the one capability check (``parallel_capability``) decides the feature, the worker count and the
Settings line; below the bar there is no feature at all. A worker's progress tool reaches only workers, voice and apps
never get the family, and a tile counts the worker's own steps. The in-Blender proof of the tile is
``test_blender_live_view.py``.
"""

import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import (parallel_access, parallel_capability as cap, session_rail as rail,  # noqa: E402
                          worker_spec)
from angelo_agent.tool_specs import TOOL_MAP  # noqa: E402

ENV = "ANGELO_ENABLE_PARALLEL_AGENTS"


def machine(gpu=True, memory=16.0, cores=10):
    return cap.assess(gpu, "Apple M4 Pro" if gpu else "", memory, cores)


class CapabilityTests(unittest.TestCase):
    def test_a_capable_machine_runs_workers_by_its_memory_and_cores(self):
        """The count follows the machine (maintainer, 2026-10-25): memory beside the lead, a core each beyond the
        lead's, never more than the most one request starts. At the bar it is the 4 every earlier build allowed."""
        self.assertEqual(machine(memory=17.2, cores=8).workers, 4)
        self.assertEqual(machine(memory=17.2).workers, 6)              # 10 cores: 6; memory holds 7.
        self.assertEqual(machine(memory=12.0).workers, 3)              # Memory holds 3.
        self.assertEqual(machine(memory=10.4).workers, 2)
        self.assertEqual(machine(memory=51.5, cores=14).workers, 10)   # Mac 1.
        self.assertEqual(machine(memory=64.0, cores=8).workers, 4)     # Cores bound it.
        self.assertEqual(machine(memory=352.0, cores=88).workers, cap.MAX_WORKERS)   # The ceiling.
        self.assertTrue(machine().capable)

    def test_the_start_tool_says_this_computers_own_count(self):
        from angelo_agent import worker_spec
        start = next(t for t in worker_spec.for_launch(10) if t["name"] == "angelo_workers_start")
        self.assertIn("At most 10 workers run at once on this computer; the parts past that wait their turn, "
                      "each starting once a running worker is done.", start["description"])   # Its own count (E2E-16 M-4).
        from angelo_agent.worker_sessions import MAX_AUTO_STARTS   # Past the limit a part waits (worker_queue).
        self.assertEqual(start["inputSchema"]["properties"]["parts"]["maxItems"], MAX_AUTO_STARTS)
        self.assertEqual([t["name"] for t in worker_spec.for_launch(3)], [t["name"] for t in worker_spec.WORKER_TOOLS])
        specs = (ADDON / "tool_specs.py").read_text(encoding="utf-8")
        self.assertIn("TOOLS.extend(worker_tools_for_launch(parallel_worker_limit(), parallel_limit_where()) + "
                      "PART_TOOLS)", specs)

    def test_below_the_bar_there_is_no_feature(self):
        for below, missing in ((machine(gpu=False), ("gpu",)), (machine(memory=8.6), ("memory",)),
                               (machine(cores=4), ("cores",)), (machine(False, 8.0, 4), ("gpu", "memory", "cores"))):
            self.assertFalse(below.capable)
            self.assertEqual(below.workers, 0)
            self.assertEqual(below.missing, missing)
        self.assertEqual(cap.required_memory_gb(), 11)   # The lead's 8 GB and two workers' 1.2 GB peaks.

    def test_a_background_angelo_and_a_software_renderer_never_qualify(self):
        background = types.ModuleType("bpy")
        background.app = SimpleNamespace(background=True)
        with patch.dict(sys.modules, {"bpy": background}):
            self.assertEqual(cap._gpu(), (False, ""))
        window = types.ModuleType("bpy")
        window.app = SimpleNamespace(background=False)
        for renderer, hardware in (("Apple M4 Pro", True), ("llvmpipe (LLVM 17.0.6, 256 bits)", False),
                                   ("Microsoft Basic Render Driver", False), ("NVIDIA Tesla T4/PCIe/SSE2", True)):
            gpu = types.ModuleType("gpu")
            gpu.platform = SimpleNamespace(backend_type_get=lambda: "OPENGL", renderer_get=lambda r=renderer: r)
            with patch.dict(sys.modules, {"bpy": window, "gpu": gpu}):
                self.assertEqual(cap._gpu()[0], hardware, renderer)

    def test_the_feature_and_admission_follow_the_one_check(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV, None)
            with patch.object(cap, "current", return_value=machine(memory=12.0)):
                self.assertTrue(parallel_access.enabled())
                self.assertEqual(parallel_access.worker_limit(), 3)
            with patch.object(cap, "current", return_value=machine(gpu=False)):
                self.assertFalse(parallel_access.enabled())
                self.assertEqual(parallel_access.worker_limit(), 0)
        with patch.dict(os.environ, {ENV: "0"}), patch.object(cap, "current", return_value=machine()):
            self.assertFalse(parallel_access.enabled())
        with patch.dict(os.environ, {ENV: "1"}), patch.object(cap, "current", return_value=machine(gpu=False)):
            self.assertTrue(parallel_access.enabled())   # Test fixtures and headless suites only.
            self.assertEqual(parallel_access.worker_limit(), 6)   # Forced, it still follows the machine (P1-20 H2).
        small = machine(gpu=False, memory=9.0, cores=4)
        with patch.dict(os.environ, {ENV: "1"}), patch.object(cap, "current", return_value=small):
            self.assertEqual(parallel_access.worker_limit(), cap.MIN_WORKERS)   # A fixture still runs.
        source = (ADDON / "worker_places.py").read_text(encoding="utf-8")   # Places: the one count (worker_places),
        self.assertIn("parallel_access.worker_limit()", source[source.index("def free("):])   # the queue's and pool's.
        for module in ("worker_queue.py", "worker_pool.py"):
            self.assertIn("worker_places.free()", (ADDON / module).read_text(encoding="utf-8"), module)

    def test_settings_say_what_a_machine_below_the_bar_needs(self):
        source = (ADDON / "providers_page_model.py").read_text(encoding="utf-8")
        body = source[source.index("def _parallel_edit("):source.index("def _enhance(")]
        self.assertIn("Parallel edit needs {what}, which this computer doesn't have.", body)
        self.assertIn("for name in machine.missing", body)   # Only what this computer lacks.
        self.assertIn("parallel_capability.required_memory_gb()", body)
        self.assertIn("Up to {count} worker agents at once on this computer.", body)
        self.assertIn("sections.append(_parallel_edit(prefs))", source)


class NumberingTests(unittest.TestCase):
    """One numbering: given at launch, never reused; status, merge, its card and the tiles all use it (P1-6 H2)."""

    def test_numbers_are_given_at_launch_and_never_reused(self):
        source = (ADDON / "worker_sessions.py").read_text(encoding="utf-8")
        execute = source[source.index("def execute("):source.index("def _launch(")]
        self.assertIn("first = _numbers.get(lead_id, 0) + 1", execute)
        self.assertNotIn("len(_started", execute)
        status = source[source.index("def status("):source.index("def cancel_starts(")]
        self.assertIn("_parts[session_id][0]", status)
        self.assertNotIn("enumerate(_started", status)

    def test_a_lead_plan_never_carries_a_worker_report(self):
        source = (ADDON / "codex_plan.py").read_text(encoding="utf-8")
        self.assertIn("(PART_NAMES & frozenset(TOOL_MAP))", source)   # A worker's plan may (P1-6 M1)…
        self.assertIn("return PLAN_TOOLS - PART_NAMES if allowed is None", source)   # …a lead's never does.
        direct = (ADDON / "claude_direct_schema.py").read_text(encoding="utf-8")
        self.assertIn("for tool in declared(allowed):", direct)

    def test_a_windowless_executor_reads_no_scene(self):
        source = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("scene = self.window.scene if self.window is not None else None"), 2)
        self.assertNotIn("        scene = self.window.scene\n", source)


class HarnessTests(unittest.TestCase):
    """The parallel suites and live-trial drivers name only what the add-on has (a renamed queue ended three billed
    live trials at their first tick, 2026-10-24)."""

    MODULES = ("worker_sessions", "parallel_sessions", "live_view", "live_view_draw", "session_rail_input",
               "rail_pictures", "worker_routing", "parallel_access", "parallel_capability")
    HARNESSES = ("parallel_live_session.py", "parallel_live_trial.py", "parallel_workers_qa.py",
                 "test_blender_session_rail.py", "test_blender_live_view.py", "test_blender_parallel_below_bar.py")

    def test_every_add_on_name_a_harness_uses_exists(self):
        import ast
        here = Path(__file__).resolve().parent
        for harness in self.HARNESSES:
            tree = ast.parse((here / harness).read_text(encoding="utf-8"))
            used = {(node.value.id, node.attr) for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name) and node.value.id in self.MODULES}
            for module, name in used:
                defined = set()
                for node in ast.walk(ast.parse((ADDON / (module + ".py")).read_text(encoding="utf-8"))):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        defined.add(node.name)
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        defined |= {t.id for t in targets if isinstance(t, ast.Name)}
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        defined |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
                self.assertIn(name, defined, "%s uses %s.%s, which the add-on does not define" % (harness, module, name))


class DescribeTests(unittest.TestCase):
    def test_the_launch_line_says_what_was_measured(self):
        capable = cap.assess(True, "Tesla T4/PCIe/SSE2", 64.4, 16)
        self.assertEqual(cap.describe(capable), "GPU Tesla T4/PCIe/SSE2, 64.4 GB, 16 cores, 12 workers")
        below = cap.assess(False, "GDI Generic", 64.4, 16)
        self.assertEqual(cap.describe(below), "GPU GDI Generic, 64.4 GB, 16 cores, 0 workers; below the bar: gpu")


class BatchTests(unittest.TestCase):
    def test_a_report_never_costs_a_cube_batch(self):
        from angelo_agent import parallel_sessions
        cube = {"tool": "angelo_create_cube", "arguments": {"name": "a", "size": 1, "location": [0, 0, 0]}}
        report = {"tool": worker_spec.PROGRESS, "arguments": {"done": 1, "steps": ["Cubes"]}}
        controller = SimpleNamespace(state={"id": "w1"})
        with patch.object(parallel_sessions, "note_progress") as noted:
            self.assertEqual(worker_spec.cube_batch([report, cube, cube]), [cube, cube])
            noted.assert_not_called()   # Never ahead of the work: shown once the batch has committed (P1-8 P5).
            worker_spec.note_reports([report, cube, cube], controller)
        noted.assert_called_once_with("w1", 1, ["Cubes"])
        with self.assertRaises(ValueError):   # A bad report is still refused before anything runs.
            worker_spec.cube_batch([{"tool": worker_spec.PROGRESS, "arguments": {"done": -1}}, cube])
        self.assertEqual(worker_spec.cube_batch([cube]), [cube])   # Without reports: the plain cube rule.
        self.assertIsNone(worker_spec.cube_batch([report]))
        self.assertIsNone(worker_spec.cube_batch([cube, {"tool": "angelo_scene_program", "arguments": {}}]))
        for name in ("providers.py", "codex_plan.py"):
            source = (ADDON / name).read_text(encoding="utf-8")
            self.assertIn("cube_batch(", source, name)
            self.assertIn("note_reports(", source[source.index("cube_batch("):], name)   # After the batch.

    def test_a_windowless_executor_runs_only_a_chats_own_tools(self):
        source = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        body = source[source.index("def _windowless("):source.index("def _windowless_precondition(")]
        self.assertIn("if name not in local_tools():", body)
        self.assertIn("raise RefusalError", body)
        execute = source[source.index("    def execute(self, name, arguments):"):]
        self.assertLess(execute.index("if not self._windowless(name):"), execute.index("job_host.guard(name, arguments)"))


class ProgressTests(unittest.TestCase):
    def test_a_worker_reports_its_own_plan_checked(self):
        good = worker_spec.validate_worker(worker_spec.PROGRESS, {"done": 0, "steps": ["  Base ", "Legs", "Top"]})
        self.assertEqual(good, {"done": 0, "steps": ["Base", "Legs", "Top"]})
        self.assertEqual(worker_spec.validate_worker(worker_spec.PROGRESS, {"done": 2}), {"done": 2})
        for bad in ({"done": -1}, {"done": "2"}, {}):
            with self.assertRaises(ValueError):
                worker_spec.validate_worker(worker_spec.PROGRESS, bad)
        for steps in ([], [3, None], "Base", ["  ", ""]):   # No usable title: the count still shows (P1-11 S8).
            self.assertEqual(worker_spec.validate_worker(worker_spec.PROGRESS, {"done": 1, "steps": steps}), {"done": 1})

    def test_a_long_or_odd_step_title_is_cut_never_refused(self):
        """A report is display only: it never costs the plan of real edits it rides in."""
        long = "Carve the backrest slats with rounded tops and a slight lean so the bench reads as hand made oak"
        joined = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"   # Persian with a zero-width non-joiner.
        clean = worker_spec.validate_worker(worker_spec.PROGRESS, {"done": 99, "steps": [long, "a\x00b", "  ", joined]
                                                                   + ["Step"] * 50})
        self.assertEqual(clean["done"], worker_spec.STEPS_LIMIT)
        self.assertEqual(len(clean["steps"]), worker_spec.STEPS_LIMIT)   # Every place kept: done counts the same steps.
        self.assertTrue(clean["steps"][0].endswith("…") and len(clean["steps"][0]) <= worker_spec.STEP_LIMIT)
        self.assertTrue(long.startswith(clean["steps"][0][:-1]) and not clean["steps"][0][:-1].endswith(" "))
        self.assertEqual(clean["steps"][1:4], ["a b", "…", joined])   # A joiner is kept, a blank holds its place.
        self.assertEqual(worker_spec.clean_steps(["a\ud800b"]), ["a\ufffdb"])   # A lone surrogate could not be drawn.
        self.assertEqual(worker_spec.clean_steps(["…", "Legs"]), ["…", "Legs"])   # "…" itself is a title.

    def test_only_workers_get_it_and_only_leads_start_workers(self):
        names = {"angelo_scene_program"} | worker_spec.FAMILY_NAMES
        with patch.dict(os.environ, {ENV: "1"}):
            self.assertIsNone(parallel_access.restrict(None, names))   # A lead's "every tool"…
            lead = parallel_access.restrict(frozenset(names), names)
            worker = parallel_access.restrict(None, names, worker=True)
        self.assertFalse(lead & worker_spec.PART_NAMES)
        self.assertTrue(worker_spec.PART_NAMES <= worker)
        self.assertFalse(worker & worker_spec.WORKER_NAMES)

    def test_a_worker_chat_is_offered_only_what_runs_for_it(self):
        """Its worker Blender's reads and typed edits and the chat's own tools; never a tool the worker refuses."""
        from angelo_agent import remote_executor
        reads, edits = remote_executor.worker_tools()
        runs = reads | edits | remote_executor.local_tools()
        with patch.dict(os.environ, {ENV: "1"}):
            names = set(TOOL_MAP) | worker_spec.FAMILY_NAMES   # As a build with the family declares them.
            worker = parallel_access.restrict(None, names, worker=True)
            lead = parallel_access.restrict(None, names)
        self.assertTrue(worker <= runs, sorted(worker - runs))
        self.assertIn("angelo_scene_program", worker)
        self.assertTrue(worker_spec.PART_NAMES <= worker)
        self.assertIn("angelo_execute_python", worker)   # Approved in Angelo as any chat's (worker_script).
        self.assertIsNone(lead)   # The lead keeps every tool.
        source = (ADDON / "tool_specs.py").read_text(encoding="utf-8")
        declared = source[source.index("def declared("):source.index("def native_plan_schema(")]
        self.assertIn('tool["name"] not in PART_NAMES', declared)   # …declares no worker's tool.
        self.assertIn("for tool in declared(allowed):", source)

    def test_voice_and_apps_never_get_the_family(self):
        source = (ADDON / "tool_specs.py").read_text(encoding="utf-8")
        voice = source[source.index("def dynamic_tools("):source.index("# Codex threads on OpenAI")]
        self.assertIn('t["name"] not in FAMILY_NAMES', voice)
        self.assertTrue(worker_spec.PROGRESS.startswith(worker_spec.PREFIX))   # external_clients.NEVER_FOR_APPS.
        from angelo_agent import remote_executor   # A worker's progress runs as its chat's own (worker_refusals).
        self.assertLessEqual(worker_spec.PART_NAMES, remote_executor.local_tools())

    def test_a_tile_counts_the_workers_own_steps(self):
        self.assertEqual(rail.progress_text((3, 7)), "3 of 7 steps · 43%")
        self.assertEqual(rail.progress_text((0, 1)), "0 of 1 step · 0%")
        self.assertEqual(rail.progress_text(None), "")
        line = rail.status(rail.WORKING, "Working in Angelo", "1m 20s", "Carve the legs", progress=(3, 7))
        self.assertTrue(line.startswith("Carve the legs · 3 of 7 steps · 43%"), line)   # The step first, as it starts.
        self.assertIn("Carve the legs", line)
        self.assertEqual(rail.status(rail.DONE), "Done, waiting for merge")
        plan = rail.layout([rail.Tile("w1", "W", "W", "s", rail.WORKING, progress=(3, 7)),
                            rail.Tile("w2", "W", "W", "s", rail.WORKING)], (1600, 900), 1.0)
        self.assertIsNotNone(plan.tiles[0].bar)
        self.assertIsNone(plan.tiles[1].bar)
        self.assertEqual(rail.tile_kind(False, False, False, False, done=True), rail.DONE)
        self.assertEqual(rail.tile_kind(True, False, False, False, done=True), rail.WORKING)

    def test_progress_comes_from_the_report_never_a_task_list(self):
        from angelo_agent import parallel_sessions
        with patch.object(parallel_sessions, "_order", ["w1"]), patch.dict(parallel_sessions.progress, {}, clear=True), \
                patch.object(parallel_sessions, "_bump", lambda *a: None):
            self.assertFalse(parallel_sessions.note_progress("w1", 0))   # No plan yet: nothing to count.
            self.assertTrue(parallel_sessions.note_progress("w1", 1, ["Hull", "Mast", "Sail"]))
            self.assertEqual(parallel_sessions.progress_of("w1"), (1, 3))
            self.assertEqual(parallel_sessions.current_step("w1"), "Mast")
            parallel_sessions.note_progress("w1", 9)
            self.assertEqual(parallel_sessions.progress_of("w1"), (3, 3))   # Never more than the plan.
            self.assertEqual(parallel_sessions.current_step("w1"), "")


if __name__ == "__main__":
    unittest.main()
