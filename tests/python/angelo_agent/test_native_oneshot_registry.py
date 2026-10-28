# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-shot tests' registry, briefs, ground truths and references (no Blender, process or account).

Also pins that the scenario registry is unchanged: the one-shot tests are
listed apart, and a scenario's reference digest never depends on their files.
"""

import contextlib
import hashlib
import io
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

import oneshot_checks  # noqa: E402
import oneshot_mini_checks  # noqa: E402
import oneshot_minis  # noqa: E402
import oneshot_references  # noqa: E402
import scenario_bridge as bridge  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_oneshot_bridge as oneshot_bridge  # noqa: E402
import scenario_oneshot_footbridge as footbridge  # noqa: E402
import scenario_specs as specs  # noqa: E402

# A brief is written for any capable 3D assistant: it never says how to work or what the harness is.
FORBIDDEN = ("python", "script", "approv", "tool", "mcp", "angelo", "blender", "you cannot see", "you can't see",
             "geometry nodes", "modifier", "step 1", "stage 1", "\n1.")
# A mini is an everyday request and may name a Blender feature ("a Subdivision modifier"); the rest still holds.
FORBIDDEN_IN_MINIS = ("python", "script", "approv", "tool", "mcp", "angelo", "you cannot see", "you can't see")
MINI_NAMES = tuple(specs.scenario_id(task_set) for task_set in specs.ONESHOT_GROUPS["oneshot-minis"])
# The parallel-agents tests and the long candidates measure the finished build, not a render: like the minis, their
# briefs start no job.
NO_RENDER = MINI_NAMES + tuple(specs.scenario_id(task_set) for task_set in specs.ONESHOT_GROUPS["oneshot-parallel"]) \
    + tuple(specs.scenario_id(specs.ONESHOT_PREFIX + name) for name in specs.ONESHOT_LONG)


def modules(minis=None):
    """The one-shot test modules: all of them, only the scene tests (``minis=False``) or only the minis."""
    return [specs.module(name) for name in specs.ONESHOT_NAMES if minis is None or (name in MINI_NAMES) == minis]


def rendered():
    """The scene tests whose brief ends with a final still (every one but the minis and the parallel-agents tests)."""
    return [specs.module(name) for name in specs.ONESHOT_NAMES if name not in NO_RENDER]


class RegistryTests(unittest.TestCase):
    def test_task_set_names_and_the_scenario_sets_are_unchanged(self):
        self.assertEqual(specs.TASK_SETS, ("scenario-bridge", "scenario-island", "scenario-heavy", "scenario-shop",
                                           "scenario-workshop"))
        self.assertEqual(specs.SCENARIO_IDS, ("bridge", "island", "heavy", "shop", "workshop"))
        self.assertEqual(specs.ONESHOT_TASK_SETS[:2], ("oneshot-bridge", "oneshot-footbridge"))
        self.assertEqual(specs.scenario_id("oneshot-bridge"), "oneshot_bridge")
        self.assertEqual(specs.scenario_id("scenario-bridge"), "bridge")
        self.assertTrue(specs.is_scenario("oneshot-footbridge") and specs.is_oneshot("oneshot-footbridge"))
        self.assertTrue(specs.is_oneshot("oneshot_footbridge"))
        self.assertFalse(specs.is_oneshot("scenario-bridge") or specs.is_oneshot("bridge") or specs.is_oneshot("default"))
        for bad in ("oneshot-", "oneshot-room", "oneshot_bridge", "default", "oneshot-minis"):
            with self.subTest(task_set=bad), self.assertRaises(ValueError):
                specs.scenario_id(bad)
        with self.assertRaises(ValueError):
            specs.module("oneshot_room")
        self.assertEqual(specs.scenario_id("oneshot-mini-footbridge-arched"), "oneshot_mini_footbridge_arched")
        self.assertEqual(len(specs.ONESHOT_NAMES), len(set(specs.ONESHOT_NAMES)))
        self.assertEqual(specs.ONESHOT_GROUPS, {"oneshot-minis": (
            "oneshot-mini-footbridge", "oneshot-mini-footbridge-arched", "oneshot-mini-cube", "oneshot-mini-camera",
            "oneshot-mini-lighting"), "oneshot-face-hair": (
            "oneshot-head-stylised", "oneshot-head-portrait", "oneshot-hair-short", "oneshot-hair-long",
            "oneshot-head-anime"), "oneshot-parallel": ("oneshot-chess", "oneshot-parlour", "oneshot-roadster", "oneshot-outfit")})
        self.assertTrue(set(specs.ONESHOT_GROUPS["oneshot-minis"]) <= set(specs.ONESHOT_TASK_SETS))
        self.assertFalse(set(specs.ONESHOT_GROUPS) & set(specs.ONESHOT_TASK_SETS))   # A group is not a task set.

    def test_every_one_shot_test_is_complete(self):
        from angelo_agent import job_spec
        for module in modules():
            with self.subTest(test=module.ID):
                for attribute in ("ID", "TITLE", "FRAME", "TURNS", "CAMERAS", "SUBJECT", "LIMITS", "FINAL_STILL",
                                  "FINAL_VIEW", "GROUND_TRUTH", "build", "build_turn"):
                    self.assertTrue(hasattr(module, attribute), attribute)
                self.assertEqual(module.ID, specs.ONESHOT_NAMES[modules().index(module)])
                self.assertEqual(len(module.TURNS), 1)   # One message: one turn.
                self.assertIn(module.FINAL_VIEW, module.CAMERAS)
                self.assertTrue({"front", "side", "top", "hero"} <= set(module.CAMERAS))
                self.assertIn(module.GROUND_TRUTH, ("reference", "stand-in"))
                self.assertEqual(set(module.LIMITS), {"wall_s", "job_wait_s", "cost_usd"})
                self.assertTrue(module.LIMITS["wall_s"] > 0 and module.LIMITS["cost_usd"] > 0)
                self.assertEqual(specs.limits(module.ID), module.LIMITS)
                task, = specs.tasks(module.ID)
                self.assertEqual((task["kind"], task["requires_object"], task["checks"]), ("edit", False, []))
                self.assertTrue(task["scenario_checks"])
                known = set(module.CAMERAS) | set(getattr(module, "SOURCE_IMAGES", {}))
                self.assertTrue(set(task["references"]) <= known)
        for module in rendered():
            with self.subTest(test=module.ID):
                self.assertEqual(specs.job_wait(module.ID), module.LIMITS["job_wait_s"])
                self.assertGreater(module.LIMITS["job_wait_s"], 0)
                self.assertEqual(specs.approvable(module.ID), frozenset({"angelo_job_start"}))
                task, = specs.tasks(module.ID)
                self.assertTrue(1 <= len(task["references"]) <= specs.MAX_IMAGES_PER_TURN)
                still, = task["scenario_outputs"]
                self.assertEqual((still["name"], still["min_size"]), (module.FINAL_STILL["name"],
                                                                      list(module.FINAL_STILL["size"])))
                job = task["scenario_job"]
                self.assertEqual((job["kind"], job["resolution"]), ("RENDER_STILL", list(module.FINAL_STILL["size"])))
                self.assertTrue(job["output"].endswith("/" + module.FINAL_STILL["name"]))
                job_spec.validate_job("angelo_job_start", dict(job))   # What Angelo's job tool accepts.

    def test_a_test_with_more_than_one_turn_is_refused(self):
        with mock.patch.object(footbridge, "TURNS", footbridge.TURNS * 2), self.assertRaises(ValueError):
            specs.tasks("oneshot_footbridge")

    def test_every_check_is_a_known_kind_with_a_label(self):
        for module in modules():
            for check in module.TURNS[0]["checks"]:
                with self.subTest(test=module.ID, label=check.get("label")):
                    self.assertIn(check["kind"], checks.CHECKS)
                    self.assertTrue(check.get("label"))
        self.assertTrue(set(oneshot_checks.CHECKS) <= set(checks.CHECKS))   # Registered for every grader.
        self.assertTrue(set(oneshot_mini_checks.CHECKS) <= set(oneshot_checks.CHECKS))
        for kind in ("hygiene", "surface"):
            self.assertTrue(any(c["kind"] == kind for c in footbridge.CHECKS), kind)

    def test_one_shot_bridge_is_the_bridge_scenarios_scene_and_checks(self):
        mine = [{k: v for k, v in check.items() if k != "label"} for check in oneshot_bridge.TURNS[0]["checks"]]
        theirs = [{k: v for k, v in check.items() if k != "label"} for turn in bridge.TURNS for check in turn["checks"]]
        self.assertEqual(len(theirs), 46)
        self.assertEqual(mine[:len(theirs)], theirs)
        # Then the one-shot's own: the tidy-file check and the grader-only opacity of its stated solids.
        self.assertEqual([check["kind"] for check in mine[len(theirs):]], ["hygiene", "opaque", "opaque", "opaque"])
        self.assertEqual(oneshot_bridge.TURNS[0]["references"], ["front", "side", "top", "hero"])
        self.assertEqual({k: v for k, v in oneshot_bridge.CAMERAS.items() if k != "deck"}, bridge.CAMERAS)
        self.assertEqual(oneshot_bridge.CAMERAS["deck"]["location"], bridge.DECK_CAMERA["location"])
        self.assertEqual(oneshot_bridge.TURNS[0]["job"]["frame_start"], bridge.FRAME)


class BriefTests(unittest.TestCase):
    def test_a_brief_names_what_the_checks_look_for(self):
        for module in rendered():
            prompt = module.TURNS[0]["prompt"]
            for check in module.TURNS[0]["checks"]:
                target = check.get("target")
                if target and check.get("exists", True):
                    # A union (a wall and the windows set in it; a railing named under either of two rules) names
                    # at least one quoted part of the brief.
                    names = ["'%s" % (each.get("name") or each.get("prefix") or each.get("whole"))
                             for each in target.get("any") or [target]]
                    with self.subTest(test=module.ID, label=check["label"]):
                        self.assertTrue(any(name in prompt for name in names), names)
            with self.subTest(test=module.ID):
                self.assertIn("'%s'" % module.FINAL_STILL["name"], prompt)
                self.assertIn("%d x %d" % tuple(module.FINAL_STILL["size"]), prompt)
                self.assertIn("default scene", prompt)                   # The tidy-file demand the hygiene check grades.
                self.assertIn("fix what", prompt)                        # Look at the result and correct it.
                self.assertIn("Metres, Z up", prompt)

    def test_a_brief_never_says_how_to_work(self):
        for module in modules():
            prompt = module.TURNS[0]["prompt"].lower()
            for word in FORBIDDEN_IN_MINIS if module.ID in MINI_NAMES else FORBIDDEN:
                with self.subTest(test=module.ID, word=word):
                    self.assertNotIn(word, prompt)

    def test_briefs_are_distinct_for_the_scripted_models(self):
        # A scripted model matches a brief among its own test's turns only, so a test's original brief kept as its
        # own test (oneshot-footbridge-conflict, oneshot-bridge-conflict) may open as the test's does.
        heads = [module.TURNS[0]["prompt"][:120] for module in modules() if module.ID not in specs.SHARED_REFERENCES]
        heads += [turn["prompt"][:120] for name in specs.SCENARIO_IDS for turn in specs.module(name).TURNS]
        self.assertEqual(len(set(heads)), len(heads))

    def test_footbridge_ground_truth_agrees_with_its_brief_and_checks(self):
        prompt = footbridge.PROMPT
        self.assertEqual(footbridge.terrain_height(0.0, 3.0), footbridge.BED_Z)
        self.assertAlmostEqual(footbridge.terrain_height(-2.8, 0.0), 0.0, places=6)   # The sills rest on the bank.
        self.assertLess(abs(footbridge.terrain_height(-7.0, 3.0)), 0.45)
        self.assertIn("(4.2, -5.6, 3.6)", prompt)
        self.assertIn("(0, 0, 0.3)", prompt)
        camera = next(c for c in footbridge.CHECKS if c["kind"] == "camera")
        self.assertEqual((camera["location"], camera["look_at"]), (list(footbridge.HERO["location"]),
                                                                   list(footbridge.HERO["target"])))
        self.assertEqual(footbridge.CAMERAS["hero"]["location"], footbridge.HERO["location"])
        low, high = next(c for c in footbridge.CHECKS if c["kind"] == "light")["elevation_deg"]
        self.assertTrue(low <= footbridge.SUN_ELEVATION <= high)
        self.assertEqual(len(footbridge.POST_X) * 2, 10)
        self.assertGreaterEqual(len(footbridge.STONES), 5)
        self.assertTrue(all(0.3 <= 2 * radius <= 1.0 for _, _, radius in footbridge.STONES))
        self.assertLess(footbridge.WATER_Z, 0.0)
        self.assertGreater(footbridge.WATER_Z, footbridge.BED_Z)
        scene = next(c for c in footbridge.CHECKS if c["kind"] == "scene")["values"]
        self.assertEqual((scene["render.resolution_x"], scene["render.resolution_y"]), footbridge.FINAL_STILL["size"])


class MiniTests(unittest.TestCase):
    """The mini one-shot tests: one short everyday request on the factory scene, no image, no still."""

    MESSAGES = {
        "oneshot_mini_footbridge": "Add a stone footbridge at the origin: a deck 4 m long and 1.2 m wide, four square "
                                   "posts and two rails, in a grey stone material.",
        "oneshot_mini_footbridge_arched": "Add a slightly arched stone footbridge at the origin: a deck 4 m long and "
                                          "1.2 m wide, four square posts and two rails, in a grey stone material.",
        "oneshot_mini_cube": "Bevel the cube's edges by 2 cm with 3 segments, add a Subdivision modifier at level 2, "
                             "and give it a brushed-copper material.",
        "oneshot_mini_camera": "Add a camera at (6, -8, 4) aimed at the cube with a 50 mm lens, make it the scene "
                               "camera, then delete the old camera.",
        "oneshot_mini_lighting": "Light the cube with a key, a fill and a rim area light, set the world to a physical "
                                 "sky with the sun 5 degrees up, and switch to Cycles at 128 samples with denoising.",
    }

    def test_each_mini_is_its_message_and_nothing_else(self):
        self.assertEqual(set(MINI_NAMES), set(self.MESSAGES))
        for module in modules(minis=True):
            task, = specs.tasks(module.ID)
            with self.subTest(test=module.ID):
                self.assertEqual(task["prompt"], self.MESSAGES[module.ID])
                self.assertEqual(task["references"], [])                          # No reference image.
                self.assertFalse({"scenario_outputs", "scenario_job"} & set(task))  # No still is asked for.
                self.assertIsNone(module.FINAL_STILL["name"])
                self.assertIsNone(specs.final_still(module.ID))
                self.assertEqual(module.LIMITS, oneshot_minis.LIMITS)
                self.assertLessEqual(module.LIMITS["wall_s"], 300)
                self.assertEqual(module.LIMITS["job_wait_s"], 0)
                self.assertEqual(specs.job_wait(module.ID), 0)
                self.assertTrue(2 <= len(task["scenario_checks"]) <= 6)           # A few closed checks.
                self.assertEqual(specs.ONESHOT_SOURCES[module.ID], ("oneshot_minis.py",))
                self.assertEqual(specs.plan(module.ID)["oneshot"]["final_still"], None)

    def test_no_mini_asks_for_the_factory_scene_to_be_cleared(self):
        """The exception to the scene tests' tidy-file rule: said in each check set, and never checked against."""
        for module in modules(minis=True):
            checks_of = module.TURNS[0]["checks"]
            with self.subTest(test=module.ID):
                self.assertFalse(any(check["kind"] == "hygiene" for check in checks_of))
                self.assertFalse(any(check.get("exists") is False for check in checks_of))   # Nothing must be gone.
                self.assertIn("factory scene is kept", module.__doc__)
        labels = [check["label"] for check in specs.module("oneshot_mini_cube").TURNS[0]["checks"]]
        self.assertIn("the default cube is still there (the factory scene is kept)", labels)
        for name in ("oneshot_mini_camera", "oneshot_mini_lighting"):   # Their messages do not ask to keep it.
            labels = [check["label"] for check in specs.module(name).TURNS[0]["checks"]]
            self.assertNotIn("the default cube is still there (the factory scene is kept)", labels)
        for name in ("oneshot_mini_footbridge", "oneshot_mini_footbridge_arched"):
            self.assertFalse(any(check.get("target") == oneshot_minis.CUBE for check in specs.module(name).CHECKS))
            self.assertIn("neither is checked", specs.module(name).__doc__)

    def test_the_two_footbridges_differ_only_in_the_arch(self):
        flat, arched = specs.module("oneshot_mini_footbridge"), specs.module("oneshot_mini_footbridge_arched")
        self.assertEqual(arched.PROMPT.replace("slightly arched ", ""), flat.PROMPT)
        rise = [next(c for c in module.CHECKS if c["kind"] == "slab")["rise"] for module in (flat, arched)]
        self.assertTrue(rise[0][0] <= flat.RISE <= rise[0][1] and rise[1][0] <= arched.RISE <= rise[1][1])
        self.assertFalse(rise[1][0] <= flat.RISE <= rise[1][1])                # A flat deck fails the arched test,
        self.assertFalse(rise[0][0] <= arched.RISE <= rise[0][1])              # and an arched one the flat test.
        self.assertEqual([c["kind"] for c in flat.CHECKS], ["plan", "slab", "members", "members", "surface"])
        vertices, faces = oneshot_minis.arched_strip(4.0, 1.2, 0.15, 0.15, arched.RISE)
        xs, zs = [v[0] for v in vertices], [v[2] for v in vertices]
        self.assertEqual((min(xs), max(xs)), (-2.0, 2.0))
        self.assertAlmostEqual(max(zs) - 0.15, arched.RISE)
        self.assertEqual(len(faces), 4 * 16 + 2)

    def test_mini_ground_truths_agree_with_their_checks(self):
        cube = specs.module("oneshot_mini_cube")
        bevel = next(c for c in cube.CHECKS if c["kind"] == "bevel")
        self.assertEqual((bevel["width"], bevel["segments"]), (0.02, 3))
        self.assertEqual(next(c for c in cube.CHECKS if c["kind"] == "modifier_settings")["values"], {"levels": 2})
        self.assertEqual(checks.colour_family(cube.COPPER), "warm")
        camera = specs.module("oneshot_mini_camera")
        placed = next(c for c in camera.CHECKS if c["kind"] == "camera")
        self.assertEqual((placed["location"], placed["look_at"], placed["active"]), ([6.0, -8.0, 4.0], [0.0, 0.0, 0.0], True))
        self.assertTrue(placed["lens"][0] <= 50 <= placed["lens"][1])
        lighting = specs.module("oneshot_mini_lighting")
        behind = {name: oneshot_mini_checks.behind(location, oneshot_minis.FACTORY_CAMERA)
                  for name, location, _, _ in lighting.RIG}
        self.assertEqual(behind, {"Key": False, "Fill": False, "Rim": True})   # Behind the cube from the camera only.
        reach = {name: watts / sum(v * v for v in location) for name, location, watts, _ in lighting.RIG}
        self.assertGreater(reach["Key"], 1.2 * reach["Fill"])
        scene = next(c for c in lighting.CHECKS if c["kind"] == "scene")["values"]
        self.assertEqual(scene, {"render.engine": "CYCLES", "cycles.samples": 128, "cycles.use_denoising": True})
        sky = next(c for c in lighting.CHECKS if c["kind"] == "world")
        self.assertEqual(sky["sky_or_color"], [])                              # A sky texture, never a plain colour.
        self.assertTrue(sky["sun_elevation_deg"][0] <= lighting.SUN_ELEVATION <= sky["sun_elevation_deg"][1])


class LongCandidateTests(unittest.TestCase):
    """The long candidates (LONG-TEST-SELECTION): registered apart, briefs that say nothing about how to work."""

    def test_the_long_candidates_are_appended_apart_from_the_parallel_set_and_the_groups(self):
        self.assertEqual(specs.ONESHOT_LONG, ("apartment", "high-street", "town-centre", "grand-apartment", "tempest"))
        self.assertEqual(specs.ONESHOT_IDS[-len(specs.ONESHOT_LONG):], specs.ONESHOT_LONG)
        self.assertEqual(specs.ONESHOT_PARALLEL, ("chess", "parlour", "roadster", "outfit"))
        grouped = {task_set for group in specs.ONESHOT_GROUPS.values() for task_set in group}
        for name in specs.ONESHOT_LONG:
            task_set = specs.ONESHOT_PREFIX + name
            with self.subTest(test=task_set):
                self.assertNotIn(task_set, grouped)
                module = specs.module(specs.scenario_id(task_set))
                self.assertEqual(specs.ONESHOT_SOURCES[module.ID][0], "long_kits.py")
                task, = specs.tasks(module.ID)
                self.assertTrue(1 <= len(task["references"]) <= specs.MAX_IMAGES_PER_TURN)
                self.assertFalse({"scenario_outputs", "scenario_job"} & set(task))   # No still is asked for.

    def test_each_long_brief_names_what_its_checks_look_for_and_never_the_split(self):
        for name in specs.ONESHOT_LONG:
            module = specs.module(specs.scenario_id(specs.ONESHOT_PREFIX + name))
            prompt = module.PROMPT
            for word in ("parallel", "worker", "split", "agent", "at the same time"):
                self.assertNotIn(word, prompt.lower(), (module.ID, word))
            for phrase in ("default scene", "fix what", "Metres, Z up", "no unused leftovers"):
                self.assertIn(phrase, prompt, (module.ID, phrase))
            for check in module.CHECKS:
                target = check.get("target") or {}
                quoted = target.get("name") or target.get("prefix") or target.get("whole")
                if quoted:
                    with self.subTest(test=module.ID, label=check["label"]):
                        self.assertIn("'%s" % quoted, prompt)
            for part, spec in module.GRADE_CLASSES.items():
                with self.subTest(test=module.ID, part=part):
                    self.assertTrue(set(spec["views"]) <= set(module.CAMERAS))
                    self.assertTrue(spec["what"])


    def test_probe_margins_move_each_ray_sideways_by_the_margin(self):
        import math
        import long_probe_margins as margins
        for direction in ((0, 0, -1), (0, 1, 0), (1, 0, 0), (0.3, -0.2, -1.0)):
            moves = margins.offsets(direction)
            self.assertEqual(len(moves), 8)
            norm = math.sqrt(sum(c * c for c in direction))
            for move in moves:
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in move)), margins.MARGIN, places=5)
                self.assertAlmostEqual(sum(m * d for m, d in zip(move, direction)) / norm, 0.0, places=5)
        for name in specs.ONESHOT_LONG:   # The margin self-check is part of each candidate's frozen sources.
            self.assertIn("long_probe_margins.py", specs.ONESHOT_SOURCES[specs.scenario_id(specs.ONESHOT_PREFIX + name)])
        # Candidate 3 (rule 3: candidate 2 enlarged) proves its probes with the generic runner, on candidate 2's builder.
        self.assertEqual(specs.ONESHOT_SOURCES["oneshot_town_centre"][-1], "long_probe_check.py")


class DigestAndReferenceTests(unittest.TestCase):
    def test_a_one_shot_digest_covers_its_ground_truth_and_job_sources(self):
        names = ("scenario_oneshot_bridge.py", "scenario_bridge.py") + specs.SHARED_SOURCES + specs.ONESHOT_SHARED
        with tempfile.TemporaryDirectory() as folder:
            for name in names:
                shutil.copy(HERE / name, folder)
            with mock.patch.object(specs, "HERE", Path(folder)):
                first = specs.digest("oneshot_bridge")
                for name in ("scenario_bridge.py", "oneshot_job.py"):
                    Path(folder, name).write_text(Path(folder, name).read_text() + "\n# edit\n")
                    changed = specs.digest("oneshot_bridge")
                    self.assertNotEqual(changed, first, name)
                    first = changed
        self.assertNotEqual(specs.digest("oneshot_bridge"), specs.digest("bridge"))

    def test_the_built_scene_tests_keep_the_reference_folders_of_their_first_billed_runs(self):
        """Rendered references are not bit-reproducible (EEVEE), so a changed digest means new image bytes.

        The first billed baselines were given the images of these two folders. A failure here says
        that later runs will be given re-rendered images of the same ground truth: change the test module,
        ``oneshot_job.py``, ``oneshot_images.py`` or the shared builder sources only on purpose, and then
        update the pin (and say so where the runs are compared).
        """
        self.assertEqual({name: specs.digest(name) for name in ("oneshot_bridge", "oneshot_footbridge")},
                         {"oneshot_bridge": "f0d313c1cba67ef2", "oneshot_footbridge": "f676d5e14eeded19"})

    def test_scenario_digests_do_not_depend_on_the_one_shot_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("scenario_bridge.py",) + specs.SHARED_SOURCES:
                shutil.copy(HERE / name, folder)
            with mock.patch.object(specs, "HERE", Path(folder)):   # No one-shot file is there to read.
                self.assertEqual(specs.digest("bridge"), specs.digest("bridge"))
                self.assertEqual(len(specs.digest("bridge")), 16)

    def test_source_pictures_are_refused_unless_they_are_the_named_file(self):
        picture = {"file": "concept.png", "sha256": hashlib.sha256(b"the picture").hexdigest(), "label": "x"}
        scenario = mock.Mock(SOURCE_IMAGES={"concept": picture})
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(
                "os.environ", {oneshot_references.SOURCES_ENV: folder}):
            with self.assertRaises(FileNotFoundError):
                oneshot_references.source_files(scenario)
            Path(folder, "concept.png").write_bytes(b"another picture")
            with self.assertRaises(ValueError):
                oneshot_references.source_files(scenario)
            Path(folder, "concept.png").write_bytes(b"the picture")
            self.assertEqual(oneshot_references.source_files(scenario), {"concept.png": str(Path(folder, "concept.png"))})
        self.assertEqual(oneshot_references.source_files(footbridge), {})   # Rendered references only.

    def test_reference_cli_and_job_accept_the_one_shot_names(self):
        import scenario_references
        with mock.patch.object(scenario_references, "make", return_value={"selfcheck": {}}) as make, \
                contextlib.redirect_stdout(io.StringIO()):
            scenario_references.main(["--scenario", "oneshot_footbridge"])
        self.assertEqual(make.call_args.args[0], "oneshot_footbridge")
        with mock.patch.object(oneshot_references, "make", return_value={"digest": "d"}) as mine:
            self.assertEqual(scenario_references.make("oneshot_bridge", "/blender"), {"digest": "d"})
        self.assertEqual(mine.call_args.args[:2], ("oneshot_bridge", "/blender"))
        source = (HERE / "oneshot_job.py").read_text()
        self.assertIn("choices=specs.ONESHOT_NAMES", source)


if __name__ == "__main__":
    unittest.main()
