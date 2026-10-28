# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera-relative placement (``orbit``) of new cameras and lights, resolved before approval.

Pure: the orbit math, the program resolver and its refusals, the derived checks
before and after resolution, and the real Controller resolving once with a
recording executor. The scene is described as the factory scene is (a cube at
the origin, the factory camera), the one the ``mini-lighting`` one-shot
runs on: its check (oneshot_mini_checks._check_light_rig) wants a
rim light behind the cube as the scene camera sees it and the key the
strongest of the other two at the cube. test_blender_camera_light_programs.py
stages and commits a resolved orbit inside the Angelo binary.
"""

import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_camera_light_spec import ORBIT_NOTE, ORBIT_REFERENCE_NOTE, look_at_euler, orbit_location
from angelo_agent.action_catalog import validate_operation
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.derived_checks import derive_checks
from angelo_agent.program_orbits import has_orbit, resolve
from angelo_agent.program_spec import validate_program
from angelo_agent.refusal_codes import RefusalError
import oneshot_mini_checks

# The factory scene's camera (Blender 5.2 startup file) and its cube's centre.
FACTORY_CAMERA = [7.358891, -6.925791, 4.958309]
CUBE = [0.0, 0.0, 0.0]
CAMERA = {"handle": "scn:" + "a" * 32 + ":2", "location": FACTORY_CAMERA}
SCENE = "scn:" + "a" * 32 + ":1"


def light(name, azimuth, elevation, energy, distance=None):
    orbit = {"azimuth": azimuth, "elevation": elevation}
    if distance is not None:
        orbit["distance"] = distance
    return {"kind": "light_create", "target": None, "parameters": {
        "name": name, "alias": name.split()[0].lower(), "look_at": CUBE, "orbit": orbit,
        "settings": {"light_type": "AREA", "energy": energy, "size": 2}}}


# A three-point rig as a plan writes it in the camera's frame: key camera-left, fill camera-right, rim behind.
RIG = [light("Key Light", -math.pi / 4, .6, 1000.), light("Fill Light", math.pi / 3, .3, 300.),
       light("Rim Light", math.pi, .7, 800.)]


def program(operations, **extra):
    return validate_program({"mode": "APPLY", "operations": copy.deepcopy(operations), **extra})


def behind_cosine(location):
    """The rig check's measure: the cosine at the subject between a light and the camera."""
    view = [c - s for c, s in zip(FACTORY_CAMERA, CUBE)]
    offset = [p - s for p, s in zip(location, CUBE)]
    return sum(a * b for a, b in zip(view, offset)) / (math.dist(FACTORY_CAMERA, CUBE) * math.dist(location, CUBE))


def reach(energy, location):
    return energy / math.dist(location, CUBE) ** 2


def camera_right():
    """The factory camera's right (local +X) when it looks at the cube without roll."""
    a, _b, c = look_at_euler(FACTORY_CAMERA, CUBE)
    del a
    return [math.cos(c), math.sin(c), 0.]


class OrbitMathTests(unittest.TestCase):
    def test_azimuth_turns_from_the_camera_side_to_its_right_and_behind(self):
        front = orbit_location(FACTORY_CAMERA, CUBE, {"azimuth": 0., "elevation": 0.})
        right = orbit_location(FACTORY_CAMERA, CUBE, {"azimuth": math.pi / 2, "elevation": 0.})
        back = orbit_location(FACTORY_CAMERA, CUBE, {"azimuth": math.pi, "elevation": 0.})
        horizontal = math.hypot(FACTORY_CAMERA[0], FACTORY_CAMERA[1])
        self.assertAlmostEqual(math.hypot(front[0], front[1]), math.dist(FACTORY_CAMERA, CUBE), places=5)
        self.assertGreater(behind_cosine(front), horizontal / math.dist(FACTORY_CAMERA, CUBE) - 1e-6)
        self.assertGreater(sum(a * b for a, b in zip(right, camera_right())), .999 * math.dist(FACTORY_CAMERA, CUBE))
        self.assertLess(behind_cosine(back), -.75)                     # Straight across the cube from the camera.
        self.assertEqual(back[2], 0.)                                    # Elevation 0 is the point's own height.

    def test_elevation_and_distance(self):
        above = orbit_location(FACTORY_CAMERA, [1., 2., 3.], {"azimuth": 1., "elevation": math.pi / 2, "distance": 4.})
        self.assertEqual(above, [1., 2., 7.])                            # Straight up from the point.
        raised = orbit_location(FACTORY_CAMERA, CUBE, {"azimuth": 2., "elevation": .5, "distance": 6.})
        self.assertAlmostEqual(math.dist(raised, CUBE), 6., places=5)
        self.assertAlmostEqual(raised[2], 6. * math.sin(.5), places=5)
        default = orbit_location(FACTORY_CAMERA, CUBE, {"azimuth": 2., "elevation": .5})
        self.assertAlmostEqual(math.dist(default, CUBE), math.dist(FACTORY_CAMERA, CUBE), places=5)

    def test_a_point_away_from_the_origin_moves_the_frame_with_it(self):
        point = [10., 4., 1.]
        back = orbit_location(FACTORY_CAMERA, point, {"azimuth": math.pi, "elevation": 0., "distance": 3.})
        toward = [FACTORY_CAMERA[0] - point[0], FACTORY_CAMERA[1] - point[1]]
        away = [back[0] - point[0], back[1] - point[1]]
        self.assertAlmostEqual((toward[0] * away[0] + toward[1] * away[1]) / (math.hypot(*toward) * math.hypot(*away)),
                               -1., places=6)

    def test_a_camera_straight_above_has_no_side_and_is_refused(self):
        with self.assertRaisesRegex(ValueError, "straight above or below"):
            orbit_location([0., 0., 9.], CUBE, {"azimuth": math.pi, "elevation": .5})

    def test_rounded_like_the_observation_and_never_negative_zero(self):
        # A camera on -Y looks along +Y: its right is +X, its left -X (a y of about -6e-17 rounds to 0.0, not -0.0).
        self.assertEqual(orbit_location([0., -10., 0.], CUBE, {"azimuth": math.pi / 2, "elevation": 0., "distance": 1.}),
                         [1., 0., 0.])
        value = orbit_location([0., -10., 0.], CUBE, {"azimuth": -math.pi / 2, "elevation": 0., "distance": 1.})
        self.assertEqual(json.dumps(value), "[-1.0, 0.0, 0.0]")
        exact = orbit_location(FACTORY_CAMERA, CUBE, {"azimuth": 2.5, "elevation": .3})
        self.assertEqual(exact, [round(value, 6) for value in exact])


class FactoryRigTests(unittest.TestCase):
    """The mini-lighting brief's rig, written in the camera's frame and resolved against the factory camera."""

    def test_the_resolved_rim_is_behind_and_the_key_is_strongest_at_the_cube(self):
        resolved = resolve(program(RIG), CAMERA)
        located = {op["parameters"]["name"]: op["parameters"]["location"] for op in resolved["operations"]}
        energy = {op["parameters"]["name"]: op["parameters"]["settings"]["energy"] for op in resolved["operations"]}
        cosines = {name: behind_cosine(location) for name, location in located.items()}
        behind = {name: oneshot_mini_checks.behind(location, FACTORY_CAMERA, CUBE) for name, location in located.items()}
        self.assertEqual(behind, {"Key Light": False, "Fill Light": False, "Rim Light": True})
        self.assertLess(cosines["Rim Light"], min(cosines["Key Light"], cosines["Fill Light"]))
        # Left out, the distance is the camera's for all three: the energies alone rank them at the cube.
        self.assertGreater(reach(energy["Key Light"], located["Key Light"]),
                           1.2 * reach(energy["Fill Light"], located["Fill Light"]))
        self.assertTrue(all(abs(math.dist(location, CUBE) - math.dist(FACTORY_CAMERA, CUBE)) < 1e-5
                            for location in located.values()))

    def test_a_stated_distance_is_kept_so_reach_is_the_plans_own(self):
        rig = [light("Key Light", -math.pi / 4, .6, 1000., 6.), light("Fill Light", math.pi / 3, .3, 300., 7.),
               light("Rim Light", math.pi, .7, 800., 5.)]
        located = {op["parameters"]["name"]: op["parameters"]["location"] for op in resolve(program(rig), CAMERA)["operations"]}
        self.assertEqual({name: round(math.dist(value, CUBE), 5) for name, value in located.items()},
                         {"Key Light": 6., "Fill Light": 7., "Rim Light": 5.})
        self.assertGreater(reach(1000., located["Key Light"]), 1.2 * reach(300., located["Fill Light"]))
        self.assertTrue(oneshot_mini_checks.behind(located["Rim Light"], FACTORY_CAMERA, CUBE))

    def test_the_recorded_world_plan_and_its_camera_frame_twin(self):
        # A recorded plan put the rim at (0, 5, 4): world +Y, about 102 degrees from the factory camera, 133 round it
        # in plan and 39 up. The 3D angle alone read that three-quarter back light as no rim (before 2026-10-21); it
        # is one. Its camera-frame twin, straight across the cube from the camera, is further behind still.
        world = [0., 5., 4.]
        twin = resolve(program([light("Rim Light", math.pi, math.atan2(4., 5.), 800., math.hypot(5., 4.))]), CAMERA)
        twin = twin["operations"][0]["parameters"]["location"]
        self.assertTrue(oneshot_mini_checks.behind(world, FACTORY_CAMERA, CUBE))
        self.assertTrue(oneshot_mini_checks.behind(twin, FACTORY_CAMERA, CUBE))
        self.assertLess(behind_cosine(twin), behind_cosine(world))

    def test_the_resolved_program_is_an_ordinary_create_with_exact_checks(self):
        source = program(RIG)
        before = copy.deepcopy(source)
        resolved = resolve(source, CAMERA)
        self.assertEqual(source, before, "The submitted program is never changed")
        self.assertFalse(has_orbit(resolved))
        self.assertTrue(has_orbit(source))
        self.assertEqual(validate_program(resolved), resolved)                      # Canonical and idempotent.
        checks = derive_checks(resolved["operations"])
        locations = [check for check in checks if check["kind"] == "transform"]
        self.assertEqual([check["expected"] for check in locations],
                         [op["parameters"]["location"] for op in resolved["operations"]])
        self.assertEqual(sum(check["kind"] == "aim" for check in checks), 3)
        self.assertEqual(sum(check["kind"] == "light_settings" for check in checks), 3)

    def test_unresolved_orbits_derive_no_location(self):
        checks = derive_checks(program(RIG)["operations"])
        self.assertFalse([check for check in checks if check["kind"] == "transform"])
        self.assertEqual(sum(check["kind"] == "aim" for check in checks), 3)

    def test_a_camera_can_orbit_too(self):
        camera = {"kind": "camera_create", "target": None, "parameters": {
            "name": "Reverse", "look_at": CUBE, "orbit": {"azimuth": math.pi, "elevation": .4}}}
        resolved = resolve(program([camera]), CAMERA)["operations"][0]["parameters"]
        self.assertNotIn("orbit", resolved)
        self.assertTrue(oneshot_mini_checks.behind(resolved["location"], FACTORY_CAMERA, CUBE))


class OrbitRefusalTests(unittest.TestCase):
    def test_orbit_needs_look_at_and_replaces_location(self):
        with self.assertRaisesRegex(ValueError, "give look_at"):
            validate_operation({"kind": "light_create", "target": None,
                                "parameters": {"orbit": {"azimuth": 1., "elevation": 0.}}})
        with self.assertRaisesRegex(ValueError, "location or orbit"):
            validate_operation({"kind": "light_create", "target": None, "parameters": {
                "location": [1, 2, 3], "look_at": CUBE, "orbit": {"azimuth": 1., "elevation": 0.}}})
        with self.assertRaises(ValueError):                              # Azimuth and elevation are both stated.
            validate_operation({"kind": "light_create", "target": None,
                                "parameters": {"look_at": CUBE, "orbit": {"azimuth": 1.}}})
        with self.assertRaises(ValueError):                              # Only creations orbit.
            validate_operation({"kind": "light_configure", "target": SCENE,
                                "parameters": {"look_at": CUBE, "orbit": {"azimuth": 1., "elevation": 0.}}})
        aimed_at_origin = validate_operation(RIG[2])                     # look_at [0,0,0] with the default location.
        self.assertEqual(aimed_at_origin["parameters"]["orbit"], {"azimuth": math.pi, "elevation": .7})

    def test_no_scene_camera(self):
        with self.assertRaises(RefusalError) as caught:
            resolve(program(RIG), None)
        self.assertEqual(caught.exception.code, "VALIDATION")
        self.assertIn("has none", str(caught.exception))

    def test_a_program_that_edits_the_scene_camera(self):
        moves = {"kind": "transform", "target": CAMERA["handle"], "parameters": {"location": [1, 2, 3]}}
        binding = [{"handle": SCENE, "revision": "c" * 64}, {"handle": CAMERA["handle"], "revision": "d" * 64}]
        for operations in ([moves] + RIG, RIG + [moves]):  # Before the orbits or after them.
            with self.subTest(first=operations[0]["kind"]), self.assertRaises(RefusalError) as caught:
                resolve(program(operations, read_set=binding, write_set=binding), CAMERA)
            self.assertIn("edits the scene camera", str(caught.exception))
            self.assertIn("earlier program", str(caught.exception))

    def test_setting_the_camera_the_scene_already_has_moves_nothing(self):
        """Was refused ("sets the scene camera"); in program order the orbits are measured from that same camera."""
        sets = {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": CAMERA["handle"]}}}
        binding = [{"handle": SCENE, "revision": "c" * 64}, {"handle": CAMERA["handle"], "revision": "d" * 64}]
        resolved = resolve(program([sets] + RIG, read_set=binding[1:], write_set=binding[:1]), CAMERA)
        self.assertEqual([operation["parameters"]["location"] for operation in resolved["operations"][1:]],
                         [operation["parameters"]["location"] for operation in resolve(program(RIG), CAMERA)["operations"]])

    def test_a_camera_above_the_point(self):
        with self.assertRaisesRegex(RefusalError, "straight above"):
            resolve(program(RIG), {"handle": CAMERA["handle"], "location": [0., 0., 20.]})

    def test_a_program_without_orbits_is_returned_as_it_is(self):
        plain = program([{"kind": "light_create", "target": None, "parameters": {"location": [4, -4, 5]}}])
        self.assertIs(resolve(plain, None), plain)


class OrbitExecutor:
    """Resolves against the factory camera, records preconditions and stops at staging."""

    def __init__(self, camera=CAMERA):
        self.camera, self.resolutions, self.started, self.preconditions, self.window = camera, 0, [], [], "window"

    def supports_native_job(self, name):
        return name in {"angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"}

    def host_program(self, arguments, used=None):
        self.resolutions += 1
        return resolve(arguments, self.camera, used=used)

    def precondition(self, name, arguments):
        self.preconditions.append(copy.deepcopy(arguments))
        return ("bound", json.dumps(arguments, sort_keys=True))

    def start_native_job(self, name, arguments):
        self.started.append((name, copy.deepcopy(arguments)))
        raise RuntimeError("fixture stops at staging")


class ControllerResolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("instance", "scratch.blend", "Scene"))
        self.c.busy = True

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def request(self, executor, operations=RIG, identity="rig"):
        answers = []
        self.c.request_tool(identity, "angelo_scene_program", {"mode": "APPLY", "operations": copy.deepcopy(operations)},
                            answers.append)
        for _tick in range(4):
            self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
            if answers or executor.started or executor.preconditions:
                break
        return self.c.pending.get(self.c.state["id"] + "/" + identity), answers

    def test_resolved_once_shown_and_staged_exactly(self):
        from angelo_agent.approvals import review_arguments
        executor = OrbitExecutor()
        call, answers = self.request(executor)
        self.assertTrue(call.approval and not answers)
        self.assertEqual(executor.resolutions, 1)
        self.assertEqual(executor.preconditions, [call.program_arguments])
        self.assertFalse(has_orbit(call.program_arguments))
        self.assertTrue(has_orbit(call.arguments), "The submitted call keeps what the model wrote")
        shown = review_arguments(call)
        self.assertEqual([op["parameters"]["location"] for op in shown["operations"]],
                         [op["parameters"]["location"] for op in call.program_arguments["operations"]])
        self.assertIn([op["parameters"]["location"] for op in call.program_arguments["operations"]][0],
                      [check["expected"] for check in shown["expected_checks"] if check["kind"] == "transform"])
        self.c.approve_tool(call, executor, self.c.document)
        self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(executor.started, [("angelo_scene_program", call.program_arguments)])
        self.assertEqual(executor.resolutions, 1, "Later preconditions bind the resolved program, never a new one")
        self.c.pump_events()
        activity = next(m for m in self.c.state["messages"] if m.get("tool") == "angelo_scene_program")
        self.assertEqual(activity["arguments"]["program"], call.program_arguments)

    def test_a_refused_resolution_never_stages(self):
        executor = OrbitExecutor(camera=None)
        call, answers = self.request(executor)
        self.assertIsNone(call)
        self.assertEqual(answers[0]["error_code"], "VALIDATION")
        self.assertIn("has none", answers[0]["error"])
        self.assertEqual((executor.started, executor.preconditions), ([], []))

    def test_an_executor_that_cannot_read_the_camera_refuses(self):
        executor = OrbitExecutor()
        executor.host_program = None
        call, answers = self.request(executor)
        self.assertIsNone(call)
        self.assertEqual(answers[0]["error_code"], "UNSUPPORTED")
        self.assertEqual(executor.started, [])


class ModelFacingTests(unittest.TestCase):
    def test_every_model_reads_what_orbit_means(self):
        from angelo_agent.codex_reference import program_reference
        from angelo_agent.tool_specs import native_plan_schema
        self.assertIn(json.dumps(ORBIT_NOTE), json.dumps(native_plan_schema()))
        for text in (program_reference(), program_reference(plan=True)):
            self.assertIn("orbit?:{azimuth:number;elevation:number;distance?:number;}", text)
            self.assertIn("\n" + ORBIT_REFERENCE_NOTE + "\n", text)
        for words in ("azimuth 0 = camera side", "pi = behind look_at", "rim light", "radians"):
            self.assertIn(words, ORBIT_NOTE)
            self.assertIn(words, ORBIT_REFERENCE_NOTE)

    def test_the_reference_line_says_what_orbit_and_location_mean_first(self):
        # Codex reads the field far from its line; the line leads with the request words that need it.
        # Facts-only text: what orbit and location mean, and the
        # camera orbit is measured from (program_orbits), replace "with orbit and look_at, not location".
        lead = ORBIT_REFERENCE_NOTE.split(" orbit: ")[0]
        for words in ("orbit with look_at places one relative to the view", "key, fill or rim light", "behind",
                      "camera-left", "location is world coordinates, not camera-relative",
                      "the camera the scene looks through once the program has run",
                      "a program that edits that camera, or an object it follows, cannot also use orbit"):
            self.assertIn(words, lead)
        self.assertTrue(ORBIT_REFERENCE_NOTE.startswith("camera_create, light_create: "))


if __name__ == "__main__":
    unittest.main()
