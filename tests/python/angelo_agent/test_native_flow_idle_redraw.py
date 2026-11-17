# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Generation Flows pump asks for a redraw only when what its surfaces show has changed (``flow_host.pump``).

Evidence (an-blender53 on Mac 1, 2026-11-16, 3becd249274 and back to the 1.1.0 measurement build 08e2b0f7842): with
the app left alone on the Director tab, the chat area was redrawn every 2 s for as long as the app ran. Each redraw
took about 130 MB of graphics memory that Metal released a second later, so the app's footprint stepped up and down
without end (0.80 <> 0.93 GB on the start-up file) and an idle app kept the GPU awake. Every one of those redraws was
asked for here: the pump re-evaluates every flow each ``IDLE_REFRESH_SECONDS`` (a change nobody announced: a library
another Angelo wrote to) and then tagged every Generation Flows editor and every chat or Media Studio area, whether or
not anything it had read differed, even in a file with no flow at all.

The rule: a surface is redrawn when its inputs changed (a job's event, a run's step, an edit, a split collected, an
evaluation or a generation catalog that differs from the last, a flow gone), and at the refresh's pace only while
work of the person's is in progress (a job or a run not finished: its row shows time passing). An idle app asks for
nothing.

The real ``flow_host.pump`` runs here on a stand-in ``bpy`` (``test_native_flow_run_approval``'s) with the library,
the job queue, the trees and the clock stood in. The running app's proof, on every tab: ``test_blender_idle_redraw``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from test_native_flow_run_approval import MODULES, flow_ui
from test_native_flows import catalog, hero_flow, library

flow_host = flow_ui.flow_host
PUMP = flow_host.pump.__wrapped__   # Under ``edit_tool_hold.guarded``: the pump itself.


class Host(unittest.TestCase):
    """The pump with everything it reads stood in: ``trees`` (name -> what an evaluation finds), ``job_events`` (what
    each coming call of the job queue's pump returns), ``active`` (jobs in progress), ``collected`` (trees whose split
    finished), ``catalog`` (kind -> the generation accounts and models of that kind)."""

    def setUp(self):
        self.now, self.redraws = 1000.0, []
        self.trees, self.job_events, self.active, self.collected = {}, [], {}, set()
        self.recipes, self.catalog = set(), {}
        jobs = SimpleNamespace(pump=lambda: self.job_events.pop(0) if self.job_events else False, active=self.active)
        tree = flow_host.flow_tree
        stand_ins = {
            "jobs": lambda: jobs, "library": lambda: SimpleNamespace(assets={}),
            "flow_trees": lambda: [self.tree(name) for name in self.trees],
            "recipe_trees": lambda: [], "LibraryIndex": lambda assets: None,
            "evaluate_tree": lambda item, index=None, definition=None: (
                {"flow": item.name}, copy.deepcopy(self.trees[item.name])),   # Equal by value, never the same object.
            "subflow_summaries": lambda flow, states: {}, "part_summaries": lambda flow, states: {},
            "connection_needs": lambda *args, **kwargs: {}, "connected_kinds": lambda: set(),
            "catalog_entries": lambda kind: copy.deepcopy(self.catalog.get(kind, [])),   # Equal by value.
            "_prepare_previews": lambda identities: None, "_visible_assets": lambda entry, item: [],
            "_redraw": lambda: self.redraws.append(round(self.now - 1000.0, 2)),
            "time": SimpleNamespace(monotonic=lambda: self.now),
        }
        patches = [mock.patch.dict("sys.modules", MODULES)]
        patches += [mock.patch.object(flow_host, name, value) for name, value in stand_ins.items()]
        patches += [mock.patch.object(tree, "tree_flow", lambda item: {"name": item.name}),
                    mock.patch.object(tree, "recipe_problem", lambda item: None),
                    mock.patch.object(tree, "flow_id", lambda item: item.name),
                    mock.patch.object(tree, "sync_subflows", lambda item: None),
                    mock.patch.object(flow_host.flow_split_host, "collect",
                                      lambda item, store, identity: item.name in self.collected
                                      and not self.collected.discard(item.name)),
                    mock.patch.object(flow_host.flow_split_host, "start", lambda item, states, store: None),
                    mock.patch.object(flow_host, "_cache", {}), mock.patch.object(flow_host, "_dirty", set()),
                    mock.patch.object(flow_host, "_runs", {}), mock.patch.object(flow_host, "_last_refresh", 0.0),
                    mock.patch.object(flow_host, "_catalog_seen", None, create=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tree(self, name):
        nodes = [SimpleNamespace(bl_idname=flow_host.flow_tree.SUBFLOW_NODE, recipe=None)] if name in self.recipes else []
        return SimpleNamespace(name=name, nodes=nodes)

    def idle(self, seconds):
        """The pump at its own pace for ``seconds``; the redraws it asked for meanwhile."""
        before = len(self.redraws)
        for _ in range(round(seconds / flow_host.PUMP_SECONDS)):
            self.now += flow_host.PUMP_SECONDS
            self.assertEqual(PUMP(), flow_host.PUMP_SECONDS)
        return self.redraws[before:]


class IdleTests(Host):
    def test_a_file_with_no_flow_asks_for_no_redraw(self):   # The start-up file: 0.80 <> 0.93 GB, every 2 s.
        self.assertEqual(len(self.idle(1)), 1)   # Its first look at the generation catalog, as at every start.
        self.assertEqual(self.idle(30), [])

    def test_a_flow_that_evaluates_the_same_asks_once_then_never(self):
        self.trees["Hero"] = {"Sheet": {"status": "READY", "outputs": ["a"]}}
        self.assertEqual(len(self.idle(1)), 1)   # Its first evaluation: the editor shows states it did not have.
        self.assertEqual(self.idle(30), [])

    def test_a_flow_that_uses_a_recipe_and_evaluates_the_same_asks_for_none(self):
        # Its sockets are synced with the recipe at every refresh, which marks it for evaluation: not a change.
        self.trees["Hero"] = {"Use": {"status": "READY"}}
        self.recipes.add("Hero")
        self.idle(1)
        self.assertEqual(self.idle(30), [])

    def test_the_refresh_still_happens_while_nothing_is_redrawn(self):
        self.trees["Hero"] = {"Sheet": {"status": "MISSING"}}
        self.idle(1)
        made = flow_host._cache["Hero"]["at"]
        self.idle(3 * flow_host.IDLE_REFRESH_SECONDS)
        self.assertGreater(flow_host._cache["Hero"]["at"], made)


class ChangeTests(Host):
    def test_a_change_nobody_announced_is_shown_at_the_next_refresh(self):   # Another Angelo wrote the library.
        self.trees["Hero"] = {"Sheet": {"status": "MISSING"}}
        self.idle(5)
        self.trees["Hero"] = {"Sheet": {"status": "READY", "outputs": ["a"]}}
        asked = self.idle(flow_host.IDLE_REFRESH_SECONDS + 1)
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.idle(30), [])

    def test_a_jobs_event_is_shown_at_once_with_or_without_a_flow(self):
        # Media Studio and the chat follow their jobs through this pump: with no flow in the file the event used to
        # be shown only by the refresh's unconditional redraw, up to two seconds late.
        for with_flow in (False, True):
            with self.subTest(with_flow=with_flow):
                if with_flow:
                    self.trees["Hero"] = {"Sheet": {"status": "MISSING"}}
                self.idle(5)
                self.job_events.append(True)
                self.assertEqual(len(self.idle(flow_host.PUMP_SECONDS)), 1)
                self.assertEqual(self.idle(30), [])

    def test_a_generation_account_that_changed_is_shown_at_the_next_refresh_with_or_without_a_flow(self):
        # Nobody announces it (a key checked, an account connected): Media Studio's and a step's "connect" row.
        for with_flow in (False, True):
            with self.subTest(with_flow=with_flow):
                if with_flow:
                    self.trees["Hero"] = {"Sheet": {"status": "BLOCKED"}}
                self.catalog["IMAGE"] = [{"id": "image-model", "connection": "", "ready": False}]
                self.idle(5)
                self.catalog["IMAGE"] = [{"id": "image-model", "connection": "account", "ready": True}]
                self.assertEqual(len(self.idle(flow_host.IDLE_REFRESH_SECONDS + 1)), 1)
                self.assertEqual(self.idle(30), [])

    def test_an_edit_is_shown_at_the_next_pump(self):
        self.trees["Hero"] = {"Sheet": {"status": "MISSING"}}
        self.idle(5)
        flow_host.tree_changed(self.tree("Hero"))
        self.assertEqual(len(self.idle(flow_host.PUMP_SECONDS)), 1)
        self.assertEqual(self.idle(30), [])

    def test_a_finished_split_is_shown_at_once(self):
        self.trees["Hero"] = {"Sheet": {"status": "READY"}}
        self.idle(5)
        self.collected.add("Hero")
        self.assertEqual(len(self.idle(flow_host.PUMP_SECONDS)), 1)
        self.assertEqual(self.idle(30), [])

    def test_a_flow_that_went_away_is_cleared_once(self):
        self.trees["Hero"] = {"Sheet": {"status": "READY"}}
        self.idle(5)
        del self.trees["Hero"]
        self.assertEqual(len(self.idle(flow_host.IDLE_REFRESH_SECONDS + 1)), 1)
        self.assertNotIn("Hero", flow_host._cache)
        self.assertEqual(self.idle(30), [])

    def test_a_runs_step_is_shown_at_once(self):
        self.trees["Hero"] = {"Sheet": {"status": "RUNNING"}}
        self.idle(5)
        steps = [True]
        run = SimpleNamespace(finished=False, tick=lambda: bool(steps) and steps.pop(), flow_name="Hero", steps={})
        flow_host._runs["r"] = {"run": run, "tree": "Hero", "owner": "test", "flow_id": "Hero"}
        with mock.patch.object(flow_host, "_after_tick", lambda item: None):
            self.assertEqual(len(self.idle(flow_host.PUMP_SECONDS)), 1)


class WorkTests(Host):
    """While a job or a run of the person's is in progress its row shows time passing: the refresh's pace, no more,
    and nothing once it has ended."""

    def test_a_job_in_progress_keeps_the_refreshs_pace_and_stops_with_it(self):
        self.idle(5)
        self.active["job"] = object()
        asked = self.idle(10)
        self.assertEqual(len(asked), round(10 / flow_host.IDLE_REFRESH_SECONDS))
        self.active.clear()
        self.job_events.append(True)   # Its end is an event.
        self.assertEqual(len(self.idle(flow_host.PUMP_SECONDS)), 1)
        self.assertEqual(self.idle(30), [])

    def test_a_run_in_progress_keeps_the_refreshs_pace_and_stops_with_it(self):
        self.trees["Hero"] = {"Sheet": {"status": "RUNNING"}}
        self.idle(5)
        run = SimpleNamespace(finished=False, tick=lambda: False, flow_name="Hero", steps={})
        flow_host._runs["r"] = {"run": run, "tree": "Hero", "owner": "test", "flow_id": "Hero"}
        self.assertEqual(len(self.idle(10)), round(10 / flow_host.IDLE_REFRESH_SECONDS))
        run.finished = True
        self.assertEqual(self.idle(30), [])


class SameEvaluationTests(unittest.TestCase):
    """What the comparison rests on: a flow evaluated twice against the same library compares equal, by value."""

    def test_two_evaluations_of_the_same_flow_and_library_are_equal(self):
        entries = []   # The pump's own functions (flow_graph, flow_fanout, flow_steps), on real flow data.
        for _ in range(2):
            flow, assets = hero_flow(), library()
            states = flow_host.evaluate(flow, flow_host.LibraryIndex(assets), catalog=catalog)
            entries.append({"definition": flow, "flow": flow, "states": states,
                            "subflows": flow_host.subflow_summaries(flow, states),
                            "parts": flow_host.part_summaries(flow, states),
                            "error": None, "at": len(entries), "recipe_problem": None,
                            "needs": flow_host.connection_needs(flow, catalog, connected={"IMAGE", "3D"})})
        self.assertIsNot(entries[0]["states"], entries[1]["states"])
        self.assertEqual(flow_host._shown(entries[0]), flow_host._shown(entries[1]))
        self.assertNotIn("at", flow_host._shown(entries[0]))
        changed = dict(entries[1], states=dict(entries[1]["states"], Sheet={"status": "READY"}))
        self.assertNotEqual(flow_host._shown(entries[0]), flow_host._shown(changed))
        self.assertIsNone(flow_host._shown(None))


class TheWindowSuiteRunsAloneTests(unittest.TestCase):
    """``test_blender_idle_redraw`` counts every draw of its window, and the window system draws a window too: on a
    display without a compositor (Xvfb) a window that covers this one and leaves makes all of it draw again.

    Linux, 2026-11-16 (gcp-kit on angelo-lane-latency, the 3e36393 build): beside seven other suites on one Xvfb 0
    of 5 runs passed, each with the whole Director window drawn once or twice about 9 s into the count and no redraw
    asked from Python; alone on an Xvfb of its own 10 of 10 passed, and 5 of 5 under eight busy loops."""

    def test_the_suite_is_declared_to_run_alone_and_says_why(self):
        import native_suite_manifest
        suite = native_suite_manifest.by_name()["test_blender_idle_redraw"]
        self.assertTrue(suite.serial)
        self.assertIn("display to itself", suite.notes)

    def test_the_runner_runs_such_a_suite_after_the_others_and_beside_none(self):
        import threading
        import time
        import native_suite_process
        lock, running, beside, done_before = threading.Lock(), set(), {}, {}

        def run_plan(plan, _launch, _reports):
            name = plan["suite"].name
            with lock:
                for other in running:
                    beside[other].add(name)
                beside[name], done_before[name] = set(running), set(beside) - running
                running.add(name)
            time.sleep(0.05)
            with lock:
                running.discard(name)
            return {"name": name, "status": "passed", "duration_seconds": 0.05}

        plans = [{"suite": SimpleNamespace(name=name, serial=name.startswith("alone"))}
                 for name in ("alone_first", "a", "b", "c", "d", "alone_second")]
        with mock.patch.object(native_suite_process, "run_plan", run_plan):
            results = native_suite_process.run_all(plans, 3, progress=lambda line: None)
        self.assertEqual([plan["suite"].name for plan in plans], [result["name"] for result in results])
        self.assertTrue(any(beside[name] for name in "abcd"), "the others do run beside one another")
        self.assertEqual((set(), set()), (beside["alone_first"], beside["alone_second"]))
        self.assertEqual(set("abcd"), done_before["alone_first"])
        self.assertEqual(set("abcd") | {"alone_first"}, done_before["alone_second"])


if __name__ == "__main__":
    unittest.main()
