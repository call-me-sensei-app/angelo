# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bridge's end posts: an unambiguous brief, and the original brief kept as its own test (no Blender).

``oneshot-bridge`` asked for railing posts "every 1.5 m from x = -12 to 12 m"
on a deck that ends at x = -12 and 12 m, so an end post centred there
reaches 5 cm past the deck, and the checks probe the end post at exactly
x = 12. The brief now says that the end posts are centred on
the deck's ends. ``oneshot-bridge-conflict`` keeps the original text,
unchanged, with the bridge's checks and byte-identical pictures.
"""

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import final_runs_plan  # noqa: E402
import oneshot_references  # noqa: E402
import scenario_bridge  # noqa: E402
import scenario_checks  # noqa: E402
import scenario_oneshot_bridge as bridge  # noqa: E402
import scenario_oneshot_bridge_conflict as conflict  # noqa: E402
import scenario_specs as specs  # noqa: E402
from test_native_oneshot_footbridge_naming import prompt_statement  # noqa: E402
from test_native_scenario_checks import FakeVector, mathutils  # noqa: E402

OLD_CLAUSE = "every 1.5 m from x = -12 to 12 m (17 a side), with a top rail"
NEW_CLAUSE = ("every 1.5 m from x = -12 to 12 m (17 a side; the end posts are centred on the deck's ends at x = -12 "
              "and 12 m, even though half of each then reaches past the deck end), with a top rail")
# SHA-256 of the brief as first written (the bridge's earlier billed runs were sent it).
ORIGINAL_BRIEF_SHA256 = "af3453bae8ba9fb7fee45fb7cd0b96fa080866ab2652f749f0fd16b94ff3f510"
# The reference folder of those runs (test_native_oneshot_registry pins it too).
BRIDGE_DIGEST = "f0d313c1cba67ef2"
EARLIER = ("oneshot-bridge", "oneshot-footbridge", "oneshot-tree", "oneshot-cottage", "oneshot-castle",
           "oneshot-mini-footbridge", "oneshot-mini-footbridge-arched", "oneshot-mini-cube", "oneshot-mini-camera",
           "oneshot-mini-lighting", "oneshot-footbridge-conflict")
SOURCES = ("scenario_oneshot_bridge.py",) + specs.SHARED_SOURCES + specs.ONESHOT_SHARED + ("scenario_bridge.py",)
POST, DECK_TOP, RAIL_Y = 0.1, scenario_bridge.DECK_TOP, scenario_bridge.RAIL_Y


def stated_railings():
    """The railings as the new brief states them, as (name, min, max) boxes: 17 posts a side every 1.5 m with the
    end posts centred on the deck's ends, a top rail 1 m and a middle rail 0.5 m above the deck, both 24 m long."""
    boxes = []
    for y in (-RAIL_Y, RAIL_Y):
        for x in (-12.0 + 1.5 * index for index in range(17)):
            boxes.append(("Railing Posts", (x - POST / 2, y - POST / 2, DECK_TOP), (x + POST / 2, y + POST / 2,
                                                                                      DECK_TOP + 1.0)))
        boxes.append(("Railing Rails", (-12.0, y - 0.04, DECK_TOP + 0.94), (12.0, y + 0.04, DECK_TOP + 1.0)))
        boxes.append(("Railing Rails", (-12.0, y - 0.03, DECK_TOP + 0.475), (12.0, y + 0.03, DECK_TOP + 0.525)))
    return boxes + [("Bridge Deck", (-12.0, -2.5, DECK_TOP - 0.6), (12.0, 2.5, DECK_TOP))]


def box_scene(boxes):
    """A stand-in for ``scene.ray_cast`` over axis-aligned boxes (the slab method), nearest hit first."""
    def ray_cast(depsgraph, start, direction, distance):
        best = None
        for name, low, high in boxes:
            near, far = 0.0, distance
            for axis in range(3):
                if abs(direction[axis]) < 1e-12:
                    if not low[axis] <= start[axis] <= high[axis]:
                        break
                    continue
                ends = sorted((bound - start[axis]) / direction[axis] for bound in (low[axis], high[axis]))
                near, far = max(near, ends[0]), min(far, ends[1])
            else:
                if near <= far and near > 0.0 and (best is None or near < best[0]):
                    best = (near, name)
        if best is None:
            return False, None, None, 0, None, None
        location = FakeVector(start[axis] + direction[axis] * best[0] for axis in range(3))
        return True, location, None, 0, NS(original=NS(name=best[1]), parent=None), None
    return NS(ray_cast=ray_cast)


def bounds_of(boxes, target):
    chosen = [(low, high) for name, low, high in boxes if scenario_checks.matches(name, target)]
    return ([min(low[axis] for low, _ in chosen) for axis in range(3)],
            [max(high[axis] for _, high in chosen) for axis in range(3)])


class BriefTests(unittest.TestCase):
    def test_the_bridge_brief_centres_the_end_posts_on_the_deck_ends(self):
        prompt = specs.tasks("oneshot_bridge")[0]["prompt"]
        self.assertEqual(prompt, bridge.PROMPT)
        self.assertIn(NEW_CLAUSE, prompt)
        self.assertNotIn(OLD_CLAUSE, prompt)
        self.assertIn("'Bridge Deck' is a slab 24 m long (x = -12 to 12)", prompt)   # The deck is unchanged.

    def test_only_the_end_post_clause_changed(self):
        self.assertEqual(conflict.PROMPT.count(OLD_CLAUSE), 1)
        self.assertEqual(bridge.PROMPT, conflict.PROMPT.replace(OLD_CLAUSE, NEW_CLAUSE, 1))

    def test_the_conflict_variant_sends_the_original_brief(self):
        prompt = specs.tasks("oneshot_bridge_conflict")[0]["prompt"]
        self.assertEqual(prompt, conflict.PROMPT)
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), ORIGINAL_BRIEF_SHA256)
        self.assertIn(OLD_CLAUSE, prompt)
        self.assertNotIn("end posts", prompt)

    def test_the_original_brief_is_copied_byte_for_byte(self):
        """The variant's statement is the bridge brief its pictures were rendered with.

        This once put the statement back into the bridge module and reproduced that folder's digest
        (BRIDGE_DIGEST); the bridge's checks have changed since, so the statement it was proven with is pinned
        instead.
        """
        mine, mine_end, variant = prompt_statement((HERE / "scenario_oneshot_bridge_conflict.py").read_text())
        self.assertEqual(hashlib.sha256("\n".join(variant[mine:mine_end]).encode()).hexdigest(),
                         "c4a4394c84bf5af918082781fa65b719858b61f68f026fd7babc259b7d47bc96")


class RegistrationTests(unittest.TestCase):
    def test_appended_after_the_earlier_tests(self):
        self.assertEqual(specs.ONESHOT_TASK_SETS[:len(EARLIER) + 1], EARLIER + ("oneshot-bridge-conflict",))
        self.assertEqual(specs.scenario_id("oneshot-bridge-conflict"), "oneshot_bridge_conflict")
        self.assertIs(specs.module("oneshot_bridge_conflict"), conflict)
        self.assertEqual(conflict.ID, "oneshot_bridge_conflict")

    def test_the_launchers_accept_it_and_the_campaign_leaves_it_out(self):
        import bench_mcp_vs_angelo as bench
        import eval_live_tasks as live
        self.assertIn("oneshot-bridge-conflict", live.TASK_SETS)
        self.assertEqual(bench.parse(["--task-set", "oneshot-bridge-conflict"]).task_timeout, bridge.LIMITS["wall_s"])
        self.assertIn("oneshot-bridge", final_runs_plan.SCENES)
        campaign = final_runs_plan.SCENES + final_runs_plan.MINIS + final_runs_plan.HEAVY
        self.assertNotIn("oneshot-bridge-conflict", campaign)


class SameSceneTests(unittest.TestCase):
    def test_everything_but_the_brief_is_the_bridges(self):
        for attribute in ("CAMERAS", "SUBJECT", "LIMITS", "FINAL_STILL", "FINAL_VIEW", "FRAME", "GROUND_TRUTH",
                          "APPROVABLE", "JOB_WAIT_S", "HYGIENE", "build", "build_turn"):
            with self.subTest(attribute=attribute):
                self.assertIs(getattr(conflict, attribute), getattr(bridge, attribute))
        mine, theirs = specs.tasks("oneshot_bridge_conflict")[0], specs.tasks("oneshot_bridge")[0]
        differs = {"prompt", "scenario", "source"}
        self.assertEqual({k: v for k, v in mine.items() if k not in differs},
                         {k: v for k, v in theirs.items() if k not in differs})
        self.assertIs(conflict.TURNS[0]["checks"], bridge.TURNS[0]["checks"])

    def test_both_read_the_bridges_reference_folder(self):
        folder = specs.references_dir("oneshot_bridge")
        self.assertEqual((specs.digest("oneshot_bridge"), specs.digest("oneshot_bridge_conflict")),
                         (BRIDGE_DIGEST, BRIDGE_DIGEST))
        self.assertEqual(folder.parent.name, "oneshot_bridge")
        self.assertEqual(specs.references_dir("oneshot_bridge_conflict"), folder)
        with tempfile.TemporaryDirectory() as root, mock.patch.dict("os.environ", {specs.REFERENCES_ENV: root}), \
                mock.patch.object(oneshot_references.references, "run_job", return_value=1) as job:
            with self.assertRaises(RuntimeError):
                oneshot_references.make("oneshot_bridge_conflict", "/blender")
        arguments = job.call_args.args[1]
        self.assertEqual(arguments[arguments.index("--scenario") + 1], "oneshot_bridge")

    def test_a_brief_edit_keeps_the_folder_and_any_other_edit_does_not(self):
        def digest_after(name, old, new):
            with tempfile.TemporaryDirectory() as folder:
                for source in SOURCES:
                    shutil.copy(HERE / source, folder)
                text = Path(folder, name).read_text()
                self.assertEqual(text.count(old), 1, old)
                Path(folder, name).write_text(text.replace(old, new))
                with mock.patch.object(specs, "HERE", Path(folder)):
                    return specs.digest("oneshot_bridge")
        self.assertEqual(digest_after("scenario_oneshot_bridge.py", "a small red car", "a small blue car"),
                         BRIDGE_DIGEST)
        self.assertNotEqual(digest_after("scenario_oneshot_bridge.py", '"size": (1920, 1080)', '"size": (1920, 1081)'),
                            BRIDGE_DIGEST)
        self.assertNotEqual(digest_after("scenario_bridge.py", "POST_SPACING = 2.4, 1.0, 1.5",
                                         "POST_SPACING = 2.4, 1.0, 1.6"), BRIDGE_DIGEST)


class EndPostCheckTests(unittest.TestCase):
    """The railing checks, run by the grader's own code on the railings exactly as the new brief states them."""

    def railing_checks(self):
        return [check for check in bridge.TURNS[0]["checks"] if check["label"].startswith("railings: ")]

    def test_a_build_that_follows_the_brief_passes_every_railing_check(self):
        boxes, scene = stated_railings(), box_scene(stated_railings())
        checks = self.railing_checks()
        self.assertEqual(len(checks), 7)
        with mathutils():
            for check in checks:
                with self.subTest(label=check["label"]):
                    if check["kind"] == "probe":
                        passed, details = scenario_checks._check_probe(None, scene, None, check)
                    else:
                        with mock.patch.object(scenario_checks, "evaluated_bounds",
                                               return_value=bounds_of(boxes, check["target"])):
                            passed, details = scenario_checks._check_bbox(None, None, None, check)
                    self.assertTrue(passed, details)

    def test_the_end_post_probe_runs_through_the_stated_posts_middle(self):
        """Centred on the deck's end, the end post has 5 cm on either side of the ray, not an edge."""
        probe = next(c for c in self.railing_checks() if c["label"] == "railings: end post at x=12")
        self.assertEqual(probe["origin"][0], 12.0)
        self.assertEqual(scenario_bridge.DECK_LENGTH / 2, 12.0)
        posts = [low for name, low, _ in stated_railings() if name == "Railing Posts" and low[1] > 0]
        self.assertAlmostEqual(max(low[0] for low in posts) + POST / 2, probe["origin"][0])
        bbox = next(c for c in self.railing_checks() if c["kind"] == "bbox")
        low, high = bounds_of(stated_railings(), bbox["target"])
        self.assertAlmostEqual(high[0], 12.0 + POST / 2)          # Half an end post past the deck, inside the check.
        self.assertLessEqual(abs(high[0] - bbox["max"][0]), bbox["tolerance"])
        self.assertLessEqual(abs(low[0] - bbox["min"][0]), bbox["tolerance"])


if __name__ == "__main__":
    unittest.main()
