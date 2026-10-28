# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live parallel trials' grade (``parallel_live_cases``): the job is the finished scene, every part assembled.

The workers model the parts and the lead assembles them in its own scene (the maintainer, 2026-10-25). A trial passes
only when every worker's part was merged by the lead and is in its scene at the end, each merge placed its parts as
built, and the lead itself built nothing. History: the first grade failed every merge (Sol's leads merged and were
graded 0/3); the second passed a lead that left finished parts outside its scene (Opus's, on e2e 878147790ac). The rows
below have the shape of those runs' lead chats.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import parallel_live_cases as cases  # noqa: E402

BEFORE = ["Camera", "Cube", "Light"]
AFTER = ["Camera", "Light", "Seat", "Leg", "Back", "Box", "Soil"]
SCENE = ["Collection", "Bench", "Planter"]


def row(tool, **result):
    return {"role": "activity", "tool": tool, "status": "completed", "result": result}


def merge(*entries, success=True):
    return row(cases.MERGE, success=success, merged=list(entries),
               changes={"added": ["Seat", "Leg"], "removed": [], "modified": []})


def entry(worker, collection, objects, placements=1, **more):
    return dict({"worker": worker, "collection": collection, "objects": objects, "placements": placements}, **more)


BOTH = merge(entry(1, "Bench", 3), entry(2, "Planter", 2))


def part(worker, state="merged", turn="completed"):
    return {"worker": worker, "state": state, "turn": turn, "meshes": 4, "materials": 1, "size": 1.0}


def grade(rows, after=AFTER, parts=None, checked=True, scene=SCENE):
    return cases.grade("bench_planter", parts or [part(1), part(2)], BEFORE, after, cases.lead_edits(rows), checked,
                       scene)


class LiveGradeTests(unittest.TestCase):
    def test_a_lead_that_assembled_every_part_passes(self):
        rows = [row("angelo_scene_program", changes={"added": [], "removed": ["Cube"]}), BOTH]   # Its own cleanup.
        checks = grade(rows)
        self.assertTrue(all(checks.values()), checks)

    def test_a_lead_that_left_finished_parts_outside_its_scene_fails(self):
        """Opus on e2e 878147790ac: both workers done, nothing merged; the second grade passed it."""
        checks = grade([row("angelo_workers_start", started=[1, 2])], BEFORE, [part(1, "done"), part(2, "done")])
        self.assertFalse(checks["every_part_assembled"])
        self.assertFalse(checks["merged_as_built"])
        self.assertTrue(checks["part1_worker_finished"] and checks["lead_built_no_part"])   # The rest held.

    def test_a_part_left_out_fails(self):
        rows = [merge(entry(1, "Bench", 3))]
        self.assertFalse(grade(rows, ["Camera", "Light", "Seat", "Leg", "Back"], [part(1), part(2, "done")])
                         ["every_part_assembled"])

    def test_a_merged_part_no_longer_in_the_scene_fails(self):
        self.assertFalse(grade([BOTH], scene=["Collection", "Bench"])["every_part_assembled"])   # Undone, deleted.

    def test_a_lead_that_built_an_object_itself_fails(self):
        rows = [BOTH, row("angelo_scene_program", changes={"added": ["Bench seat"], "removed": []})]
        self.assertFalse(grade(rows, AFTER + ["Bench seat"])["lead_built_no_part"])

    def test_objects_no_merge_brought_fail_even_unreported(self):
        self.assertFalse(grade([BOTH], AFTER + ["Something else"])["lead_built_no_part"])

    def test_a_refused_merge_brings_nothing(self):
        rows = [merge(entry(1, "Bench", 3), entry(2, "Planter", 2), success=False)]
        checks = grade(rows, BEFORE, [part(1, "done"), part(2, "done")])
        self.assertFalse(checks["every_part_assembled"])
        self.assertFalse(checks["merged_as_built"])

    def test_a_merge_that_reports_a_misplaced_part_fails(self):
        rows = [merge(entry(1, "Bench", 3, placement_warnings=[{"object": "Seat", "why": "off by 1.2 m"}]),
                      entry(2, "Planter", 2))]
        checks = grade(rows)
        self.assertFalse(checks["merged_as_built"])
        self.assertTrue(checks["every_part_assembled"])

    def test_a_merge_that_checks_no_placement_cannot_pass_as_built(self):
        """The phase 1 branch's merge placed from matrix_world (parts collapsed at the origin) and warned of nothing."""
        self.assertFalse(grade([BOTH], checked=False)["merged_as_built"])

    def test_a_merged_worker_is_finished_only_when_its_turn_completed(self):
        checks = grade([BOTH], parts=[part(1), part(2, turn="failed")])
        self.assertTrue(checks["part1_worker_finished"])
        self.assertFalse(checks["part2_worker_finished"])

    def test_a_light_or_camera_the_lead_adds_is_scene_setup_never_a_built_part(self):
        """The maintainer, 2026-10-25 (Sol's sailboat_dock on e2e 06092674722: a lead-added light failed it)."""
        for kind in ("LIGHT", "CAMERA", "EMPTY"):
            rows = [BOTH, row("angelo_scene_program", changes={"added": [{"name": "Key light", "type": kind}]})]
            self.assertTrue(grade(rows, AFTER + ["Key light"])["lead_built_no_part"], kind)
        for kind in ("MESH", "CURVE", "FONT", "VOLUME", "POINTCLOUD"):
            rows = [BOTH, row("angelo_scene_program", changes={"added": [{"name": "Lead's own", "type": kind}]})]
            self.assertFalse(grade(rows, AFTER + ["Lead's own"])["lead_built_no_part"], kind)

    def test_an_instancer_the_lead_adds_counts_as_geometry(self):
        rows = [BOTH, row("angelo_scene_program", changes={"added": [{"name": "Copy", "type": "EMPTY"}]})]
        checks = cases.grade("bench_planter", [part(1), part(2)], BEFORE, AFTER + ["Copy"], cases.lead_edits(rows),
                             True, SCENE, {"Copy": "INSTANCER"})
        self.assertFalse(checks["lead_built_no_part"])

    def test_an_addition_of_unknown_type_still_counts(self):
        rows = [BOTH, row("angelo_scene_program", changes={"added": ["Bare name"]})]
        self.assertFalse(grade(rows, AFTER + ["Bare name"])["lead_built_no_part"])

    def test_a_part_may_be_as_large_as_its_brief_asked(self):
        """The maintainer: the cap is the brief's ("16 x 16 m" of water), not the case's 15 m."""
        start = {"role": "activity", "tool": cases.START, "status": "completed", "result": {"started": [1, 2]},
                 "arguments": {"parts": [{"name": "Dock", "brief": "a dock with water covering about 16x16m"},
                                         {"name": "Boat", "brief": "a boat, no size given"}]}}
        lead = cases.lead_edits([start, BOTH])
        dock, boat = dict(part(1), name="Dock", size=16.0), dict(part(2), name="Boat", size=16.0)
        checks = cases.grade("sailboat_dock", [dock, boat], BEFORE, AFTER, lead, True, SCENE)
        self.assertTrue(checks["part1_size"])
        self.assertFalse(checks["part2_size"])   # No size in its brief: the case's 15 m.
        self.assertFalse(cases.grade("sailboat_dock", [dict(dock, size=19.0), boat], BEFORE, AFTER, lead, True,
                                     SCENE)["part1_size"])   # More than the brief asked, slack included.

    def test_only_explicit_size_forms_are_read_from_a_brief(self):
        self.assertEqual(cases.brief_size("water covering approximately 16x16m"), 16.0)
        self.assertEqual(cases.brief_size("Length 4.8m width 1.8m, x=1.35..3.35, mast to z=5.8"), 4.8)
        self.assertEqual(cases.brief_size("a 2 m tall lamp"), 2.0)
        self.assertEqual(cases.brief_size("water 16m x 16m around it"), 16.0)
        self.assertEqual(cases.brief_size("a dock 12 by 3 meters"), 12.0)
        self.assertEqual(cases.brief_size("Width ~1.2 m, length approximately 2.5m"), 2.5)
        self.assertIsNone(cases.brief_size("dock along x=1.35..3.35 and y=-3.5..3.5, plank top z=.55"))

    def test_the_message_asks_for_the_assembled_scene_alike_for_every_case(self):
        for case in cases.CASES:
            text = cases.prompt(case)
            self.assertIn("assemble their finished parts into this scene", text)
            self.assertNotIn("Do not build the parts in this scene", text)
        self.assertIn("Make sure the scene is assembled", cases.FOLLOW_UP)

    def test_the_harness_grades_with_the_leads_own_edits_and_the_scene_it_ends_with(self):
        source = (Path(__file__).resolve().parent / "parallel_live_session.py").read_text(encoding="utf-8")
        self.assertIn('cases.grade(CASE, parts, report["document_before"], after, edits, checked, in_scene, types)',
                      source)
        self.assertIn("scene.collection.children_recursive", source)
        self.assertIn('hasattr(worker_merge, "placement_check")', source)
        self.assertIn('screenshot("main-merged.png")', source)

    def test_every_card_is_answered_by_the_benchmarks_rule_workers_included(self):
        """Workers run Python since e2e ed0290ff332: declining their scripts failed every worker's turn (Opus, 08:56)."""
        source = (Path(__file__).resolve().parent / "parallel_live_session.py").read_text(encoding="utf-8")
        body = source[source.index("def answer_approvals("):source.index("def all_idle(")]
        self.assertIn("from oneshot_session import approval_decision", body)
        self.assertIn('lead_card or approval_decision(call.name) == "approve"', body)
        self.assertIn("key = (chat.state[\"id\"], call.id)", body)   # Two chats' call ids never collide.
        import oneshot_session
        self.assertEqual(oneshot_session.approval_decision("angelo_execute_python"), "approve")
        self.assertEqual(oneshot_session.approval_decision("angelo_write_file"), "decline")

    def test_a_diagnostic_run_shows_the_leads_thinking_and_asks_why_only_after_a_miss(self):
        """--debug-thinking and --ask-why-on-miss (eval_diagnostics), for a lead that, say, leaves parts unmerged: the
        question names the failed checks with what was measured, asked in Read only after grading."""
        here = Path(__file__).resolve().parent
        session = (here / "parallel_live_session.py").read_text(encoding="utf-8")
        self.assertIn("report.update(diagnostics.configure(lead))", session)
        self.assertIn('if failed and diagnostics.in_app_flags().get("ask_why_on_miss"):', session)
        self.assertIn("eval_ask_why.InAppAskWhy(trial, bpy,", session)
        driver = (here / "parallel_live_trial.py").read_text(encoding="utf-8")
        self.assertIn("diagnostics.add_arguments(parser)", driver)
        self.assertIn("**diagnostics.environment(args)", driver)


if __name__ == "__main__":
    unittest.main()
