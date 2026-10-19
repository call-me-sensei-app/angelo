# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The footbridge's naming rule: an unambiguous brief, and the original brief kept as its own test (no Blender).

``oneshot-footbridge`` was written with two naming rules no railing part can
meet together: every part 'Footbridge ...' and every railing part
'Railing ...'. Its brief now excepts the railings from the first
rule. ``oneshot-footbridge-conflict`` keeps the original text, unchanged, with
the footbridge's checks and byte-identical pictures: a regression check that
an agent still resolves the collision as the more specific rule says.
"""

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import final_runs_plan  # noqa: E402
import oneshot_references  # noqa: E402
import scenario_checks  # noqa: E402
import scenario_oneshot_footbridge as footbridge  # noqa: E402
import scenario_oneshot_footbridge_conflict as conflict  # noqa: E402
import scenario_specs as specs  # noqa: E402

COLLIDING_RULE = "every part's name starts with 'Footbridge'."
UNAMBIGUOUS_RULE = ("every part's name starts with 'Footbridge', except the railings, whose part names start with "
                    "'Railing'.")
RAILING_RULE = "Every railing part's name starts with 'Railing'."
# SHA-256 of the brief as first written (the earlier billed footbridge runs were sent it).
ORIGINAL_BRIEF_SHA256 = "9d23c0cfc9b570917cad11d6658a3a17a35e433c16e023b64aa16c118dd26f93"
# The reference folder of those runs (test_native_oneshot_registry pins it too).
FOOTBRIDGE_DIGEST = "f676d5e14eeded19"
# The task sets registered before the conflict variant, in their order.
EARLIER = ("oneshot-bridge", "oneshot-footbridge", "oneshot-tree", "oneshot-cottage", "oneshot-castle",
           "oneshot-mini-footbridge", "oneshot-mini-footbridge-arched", "oneshot-mini-cube", "oneshot-mini-camera",
           "oneshot-mini-lighting")


def prompt_statement(source):
    """The lines of a module's top-level ``PROMPT = ...`` statement, as written."""
    lines = source.split("\n")
    start = lines.index("PROMPT = (")
    end = next(index for index in range(start, len(lines)) if lines[index].endswith('")'))
    return start, end + 1, lines


def ground_truth_part_names():
    """The object names the footbridge's ground truth gives its bridge and railing parts."""
    with mock.patch.object(footbridge, "kit", mock.MagicMock()) as kit:
        footbridge._footbridge(mock.MagicMock())
    return [call.args[1] for call in kit.mesh_object.call_args_list]


class BriefTests(unittest.TestCase):
    def test_the_footbridge_brief_excepts_the_railings(self):
        prompt = specs.tasks("oneshot_footbridge")[0]["prompt"]
        self.assertEqual(prompt, footbridge.PROMPT)
        self.assertIn("The footbridge. Weathered timber in a warm brown, rough to the touch; " + UNAMBIGUOUS_RULE,
                      prompt)
        self.assertIn(RAILING_RULE, prompt)                        # The railings paragraph keeps its rule.
        self.assertNotIn(COLLIDING_RULE, prompt)

    def test_only_the_naming_sentence_changed(self):
        self.assertEqual(footbridge.PROMPT, conflict.PROMPT.replace(COLLIDING_RULE, UNAMBIGUOUS_RULE, 1))
        self.assertEqual(conflict.PROMPT.count(COLLIDING_RULE), 1)

    def test_the_conflict_variant_sends_the_original_brief(self):
        prompt = specs.tasks("oneshot_footbridge_conflict")[0]["prompt"]
        self.assertEqual(prompt, conflict.PROMPT)
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), ORIGINAL_BRIEF_SHA256)
        self.assertIn(COLLIDING_RULE, prompt)
        self.assertIn(RAILING_RULE, prompt)
        self.assertNotIn("except the railings", prompt)

    def test_the_original_brief_is_copied_byte_for_byte(self):
        """The variant's statement is the footbridge brief its pictures were rendered with.

        This once put the statement back into the footbridge module and reproduced that folder's digest
        (FOOTBRIDGE_DIGEST); the footbridge's checks have changed since, so the statement it was proven with is
        pinned instead.
        """
        mine, mine_end, conflict_lines = prompt_statement((HERE / "scenario_oneshot_footbridge_conflict.py").read_text())
        statement = "\n".join(conflict_lines[mine:mine_end]).encode()
        self.assertEqual(hashlib.sha256(statement).hexdigest(),
                         "783307a1306918516a16898c388377a64c331d5104a8b05dda7cf0ba8f2b9c2d")


class RegistrationTests(unittest.TestCase):
    def test_appended_after_the_earlier_tests(self):
        self.assertEqual(specs.ONESHOT_TASK_SETS[:len(EARLIER) + 1], EARLIER + ("oneshot-footbridge-conflict",))
        self.assertEqual(specs.ONESHOT_IDS[len(EARLIER)], "footbridge-conflict")
        self.assertEqual(specs.scenario_id("oneshot-footbridge-conflict"), "oneshot_footbridge_conflict")
        self.assertIs(specs.module("oneshot_footbridge_conflict"), conflict)
        self.assertEqual(conflict.ID, "oneshot_footbridge_conflict")
        self.assertNotIn("oneshot-footbridge-conflict", specs.ONESHOT_GROUPS["oneshot-minis"])

    def test_the_launchers_accept_it(self):
        import bench_mcp_vs_angelo as bench
        import eval_live_tasks as live
        self.assertIn("oneshot-footbridge-conflict", live.TASK_SETS)
        args = bench.parse(["--task-set", "oneshot-footbridge-conflict"])
        self.assertEqual(args.task_timeout, footbridge.LIMITS["wall_s"])
        self.assertEqual(specs.wall_cap("oneshot-footbridge-conflict"), footbridge.LIMITS["wall_s"])

    def test_the_campaign_runs_the_unambiguous_footbridge_only(self):
        self.assertEqual(final_runs_plan.SCENES, ("oneshot-footbridge", "oneshot-bridge", "oneshot-tree",
                                                  "oneshot-cottage"))
        campaign = final_runs_plan.SCENES + final_runs_plan.MINIS + final_runs_plan.HEAVY
        self.assertNotIn("oneshot-footbridge-conflict", campaign)


class SameSceneTests(unittest.TestCase):
    def test_everything_but_the_brief_is_the_footbridges(self):
        for attribute in ("CAMERAS", "SUBJECT", "LIMITS", "FINAL_STILL", "FINAL_VIEW", "FRAME",
                          "GROUND_TRUTH", "APPROVABLE", "JOB_WAIT_S", "build", "build_turn"):
            with self.subTest(attribute=attribute):
                self.assertIs(getattr(conflict, attribute), getattr(footbridge, attribute))
        # The same checks, with railing parts found under either of the conflicting brief's two naming rules.
        self.assertEqual(conflict.CHECKS, footbridge.checks(conflict.RAILING))
        self.assertEqual(footbridge.CHECKS, footbridge.checks(footbridge.RAILING))
        mine, theirs = specs.tasks("oneshot_footbridge_conflict")[0], specs.tasks("oneshot_footbridge")[0]
        differs = {"prompt", "scenario", "source", "scenario_checks"}
        self.assertEqual({k: v for k, v in mine.items() if k not in differs},
                         {k: v for k, v in theirs.items() if k not in differs})
        self.assertNotEqual(conflict.TITLE, footbridge.TITLE)

    def test_both_read_the_footbridges_reference_folder(self):
        self.assertEqual(specs.digest("oneshot_footbridge"), FOOTBRIDGE_DIGEST)
        self.assertEqual(specs.digest("oneshot_footbridge_conflict"), FOOTBRIDGE_DIGEST)
        folder = specs.references_dir("oneshot_footbridge")
        self.assertEqual(specs.references_dir("oneshot_footbridge_conflict"), folder)
        self.assertEqual(folder.parent.name, "oneshot_footbridge")
        plans = [specs.plan(name) for name in ("oneshot_footbridge", "oneshot_footbridge_conflict")]
        self.assertEqual({(plan["reference_digest"], plan["references"]) for plan in plans},
                         {(FOOTBRIDGE_DIGEST, str(folder))})

    def test_its_references_are_made_by_the_footbridge(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict("os.environ", {specs.REFERENCES_ENV: root}), \
                mock.patch.object(oneshot_references.references, "run_job", return_value=1) as job:
            with self.assertRaises(RuntimeError):
                oneshot_references.make("oneshot_footbridge_conflict", "/blender")
            arguments = job.call_args.args[1]
            self.assertEqual(arguments[arguments.index("--scenario") + 1], "oneshot_footbridge")
            folder = specs.references_dir("oneshot_footbridge")
            folder.mkdir(parents=True)
            (folder / specs.MANIFEST).write_text('{"scenario": "oneshot_footbridge"}')
            self.assertEqual(oneshot_references.make("oneshot_footbridge_conflict"), {"scenario": "oneshot_footbridge"})


class KeptDigestTests(unittest.TestCase):
    """The footbridge keeps its folder through a brief-only edit, and only through one."""

    def digest_after(self, edit):
        names = ("scenario_oneshot_footbridge.py",) + specs.SHARED_SOURCES + specs.ONESHOT_SHARED
        with tempfile.TemporaryDirectory() as folder:
            for name in names:
                shutil.copy(HERE / name, folder)
            edit(Path(folder))
            with mock.patch.object(specs, "HERE", Path(folder)):
                return specs.digest("oneshot_footbridge")

    @staticmethod
    def replace(name, old, new):
        def edit(folder):
            text = (folder / name).read_text()
            assert text.count(old) == 1, old
            (folder / name).write_text(text.replace(old, new))
        return edit

    def test_a_brief_edit_keeps_the_folder(self):
        self.assertEqual(self.digest_after(lambda folder: None), FOOTBRIDGE_DIGEST)
        self.assertEqual(self.digest_after(self.replace("scenario_oneshot_footbridge.py", "small gaps", "narrow gaps")),
                         FOOTBRIDGE_DIGEST)
        self.assertEqual(specs.KEPT_DIGESTS["oneshot_footbridge"][0], FOOTBRIDGE_DIGEST)

    def test_any_other_edit_makes_new_references(self):
        edits = {"a constant": self.replace("scenario_oneshot_footbridge.py", "PLANKS = 6.0, 1.4, 0.3, 40",
                                            "PLANKS = 6.0, 1.4, 0.3, 41"),
                 "a check": self.replace("scenario_oneshot_footbridge.py", '"count": [12, 400]', '"count": [13, 400]'),
                 "the job": lambda folder: (folder / "oneshot_job.py").write_text(
                     (folder / "oneshot_job.py").read_text() + "\n# edit\n")}
        for label, edit in edits.items():
            with self.subTest(edit=label):
                self.assertNotEqual(self.digest_after(edit), FOOTBRIDGE_DIGEST)

    def test_without_brief_leaves_a_module_without_one_alone(self):
        source = b'"""Doc."""\nX = 1\n'
        self.assertEqual(specs.without_brief(source), source)
        self.assertEqual(specs.without_brief(b'X = 1\nPROMPT = (\n    "a "\n    "b")\nY = 2\n'), b"X = 1\nY = 2\n")


class GroundTruthTests(unittest.TestCase):
    def test_the_ground_truth_names_follow_the_unambiguous_brief(self):
        names = ground_truth_part_names()
        railing = [name for name in names if name.startswith("Railing")]
        self.assertEqual(railing, ["Railing Posts", "Railing Handrails", "Railing Midrails"])
        self.assertTrue(all(name.startswith("Footbridge ") for name in names if name not in railing), names)

    def test_footbridge_checks_select_no_railing_part(self):
        """Railing parts named 'Railing ...' are outside every check that selects by the 'Footbridge' prefix.

        The reference job's self-check grades all 25 checks on this ground truth in stock Blender (manifest
        ``selfcheck``); what the host can pin is which objects each check reads.
        """
        names = ground_truth_part_names()
        prefixed = [check for check in footbridge.CHECKS if (check.get("target") or {}).get("prefix", "")
                    .startswith("Footbridge")]
        self.assertEqual(len(prefixed), 4)   # The bridge as a whole (the deck is its exact name now).
        for check in prefixed:
            with self.subTest(label=check["label"]):
                selected = [name for name in names if scenario_checks.matches(name, check["target"])]
                self.assertTrue(selected)
                self.assertFalse([name for name in selected if name.startswith("Railing")])
        railing = [check for check in footbridge.CHECKS if check.get("target") == footbridge.RAILING]
        self.assertEqual(len(railing), 6)    # Railings, a handrail and posts on each side, the walkway.
        for check in railing:
            self.assertEqual([name for name in names if scenario_checks.matches(name, check["target"])],
                             ["Railing Posts", "Railing Handrails", "Railing Midrails"])


if __name__ == "__main__":
    unittest.main()
