# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial tests of the camera-relative ``orbit`` placement.

Host reproductions; test_blender_review_light_rig.py holds the native ones.

* ``Held``: claims these tests try to break and cannot (resolution once,
  before approval, on every controller path; numeric abuse refused; the
  resolved program canonical; a refusal stages nothing).
* ``Fixed``: faults and notes these tests found, each asserting the fixed
  behaviour: a far orbit is a VALIDATION refusal that names the orbit, claims
  no change and leaves the request free to correct it;
  a program that writes what the camera follows is refused; a camera at the
  point or with a non-finite position is refused in its own words; a location
  written beside an orbit is refused; resolving counts toward precondition_ms.
* ``Observations``: current behaviour behind the remaining notes, pinned so a
  change to it is a decision (each docstring says why it stands).
"""

import copy
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_camera_light_spec import orbit_location
from angelo_agent.action_catalog import validate_operation
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.program_checks import _encoded
from angelo_agent.program_orbits import followed_objects, has_orbit, resolve
from angelo_agent.program_spec import validate_program
from angelo_agent.refusal_codes import RefusalError
from angelo_agent.tool_specs import validate_call
# Data only: importing the module's TestCase classes would run them twice here.
from test_native_camera_orbits import CAMERA, CUBE, FACTORY_CAMERA, RIG, light

PARENT = "scn:" + "a" * 32 + ":7"
# A handle standing for the scene camera's parent in the dependency case.
# A rim behind a point far out in the scene: the default distance (the camera's, about 141 km) passes the bound.
FAR_RIM = light("Rim Light", math.pi, 0., 800.)
FAR_RIM["parameters"]["look_at"] = [-99999., 99999., 0.]
# Its correction: a short orbit on the camera's side of that point stays inside the bound.
NEAR_RIM = light("Rim Light", 0., 0., 800., 10.)
NEAR_RIM["parameters"]["look_at"] = [-99999., 99999., 0.]


def program(operations, **extra):
    return validate_program({"mode": "APPLY", "operations": copy.deepcopy(operations), **extra})


class CountingExecutor:
    """Records every resolution, precondition, rebind and stage; validates like BlenderExecutor.precondition."""

    def __init__(self, camera=CAMERA, validate=True):
        self.camera, self.validate = camera, validate
        self.resolutions, self.preconditions, self.started, self.rebinds = 0, [], [], []
        self.frame = 1
        self.window = "window"

    def supports_native_job(self, name):
        return name in {"angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"}

    def host_program(self, arguments, used=None):
        self.resolutions += 1
        return resolve(arguments, self.camera, used=used)

    def precondition(self, name, arguments):
        if self.validate:
            arguments = validate_call(name, arguments, host=True)
            if has_orbit(arguments):
                raise RefusalError("An orbit placement must be resolved before its program is bound; give location",
                                   code="VALIDATION")
        self.preconditions.append(copy.deepcopy(arguments))
        return ("bound", json.dumps(arguments, sort_keys=True), self.frame)

    def rebind_program(self, name, arguments, approved):
        self.rebinds.append(copy.deepcopy(arguments))
        rebound = copy.deepcopy(arguments)
        return {"arguments": rebound, "precondition": ("bound", json.dumps(rebound, sort_keys=True), self.frame),
                "reason": "The frame moved"}

    def start_native_job(self, name, arguments):
        self.started.append((name, copy.deepcopy(arguments)))
        raise RuntimeError("fixture stops at staging")


class ControllerCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("instance", "scratch.blend", "Scene"))
        self.c.busy = True
        self.c.state["options"]["approval"] = "ASK"  # Each test that wants automatic approval says so.

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def submit(self, executor, operations=RIG, identity="rig", ticks=4):
        answers = []
        self.c.request_tool(identity, "angelo_scene_program", {"mode": "APPLY", "operations": copy.deepcopy(operations)},
                            answers.append)
        for _tick in range(ticks):
            self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60),
                              apply_mutation=lambda call: None)
            if answers or executor.started or executor.preconditions:
                break
        return self.c.pending.get(self.c.state["id"] + "/" + identity), answers

    def pump(self, executor, ticks=4):
        for _tick in range(ticks):
            self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60),
                              apply_mutation=lambda call: None)


class Held(ControllerCase):
    def test_a_frame_rebind_after_approval_binds_the_resolved_program_and_never_resolves_again(self):
        executor = CountingExecutor()
        call, answers = self.submit(executor)
        self.assertTrue(call.approval and not answers)
        resolved = copy.deepcopy(call.program_arguments)
        self.c.approve_tool(call, executor, self.c.document)
        executor.frame = 2  # The approved binding no longer matches: the controller rebinds once (_rebind_once).
        self.pump(executor)
        self.assertEqual(executor.resolutions, 1)
        self.assertEqual(executor.rebinds, [resolved], "The rebind starts from the resolved program, not the model's")
        self.assertEqual([arguments for _name, arguments in executor.started], [resolved])
        self.assertFalse(any(has_orbit(arguments) for arguments in executor.preconditions))

    def test_automatic_approval_resolves_once_and_stages_the_resolved_program(self):
        self.c.state["options"]["approval"] = "AUTO_BOUNDED"
        executor = CountingExecutor()
        call, _answers = self.submit(executor)
        self.pump(executor)
        self.assertEqual(executor.resolutions, 1)
        self.assertEqual(len(executor.started), 1)
        self.assertFalse(has_orbit(executor.started[0][1]))
        self.assertTrue(call is None or call.program_arguments == executor.started[0][1])

    def test_a_duplicate_submission_joins_and_resolves_once(self):
        executor = CountingExecutor()
        answers = []
        arguments = {"mode": "APPLY", "operations": copy.deepcopy(RIG)}
        self.c.request_tool("dup", "angelo_scene_program", arguments, answers.append)
        self.c.request_tool("dup", "angelo_scene_program", copy.deepcopy(arguments), answers.append)
        self.pump(executor)
        self.assertEqual(executor.resolutions, 1)

    def test_a_program_without_orbit_is_untouched_and_reads_no_camera(self):
        executor = CountingExecutor()
        plain = [{"kind": "light_create", "target": None, "parameters": {"name": "Plain", "location": [4, -4, 5],
                                                                          "look_at": CUBE}}]
        call, _answers = self.submit(executor, plain, identity="plain")
        self.assertEqual(executor.resolutions, 0)
        self.assertIsNone(call.program_arguments)

    def test_numeric_abuse_is_refused_by_validation(self):
        for orbit in ({"azimuth": float("nan"), "elevation": 0}, {"azimuth": 0, "elevation": float("inf")},
                      {"azimuth": True, "elevation": 0}, {"azimuth": "1", "elevation": 0},
                      {"azimuth": 0, "elevation": 0, "distance": 0}, {"azimuth": 0, "elevation": 0, "distance": -1},
                      {"azimuth": 0, "elevation": math.pi / 2 + 1e-9}, {"azimuth": 7, "elevation": 0},
                      {"azimuth": 10 ** 400, "elevation": 0}, {"azimuth": 0, "elevation": 0, "roll": 1},
                      {"azimuth": 0, "elevation": 0, "distance": float("nan")}):
            with self.subTest(orbit=repr(orbit)), self.assertRaises(ValueError):
                validate_operation(light("L", 0, 0, 10.) | {"parameters": dict(light("L", 0, 0, 10.)["parameters"],
                                                                                  orbit=orbit)})

    def test_resolved_programs_are_canonical_so_bound_obligations_match_the_review(self):
        """approvals._review_obligations asserts the bound obligations' digest against program_arguments."""
        rng = random.Random(7)
        for _trial in range(1500):
            camera = {"handle": CAMERA["handle"], "location": [rng.uniform(-50, 50) for _ in range(3)]}
            operations = []
            for index in range(rng.randint(1, 4)):
                kind = rng.choice(["light_create", "camera_create"])
                orbit = {"azimuth": rng.uniform(-2 * math.pi, 2 * math.pi), "elevation": rng.uniform(-math.pi / 2, math.pi / 2)}
                if rng.random() < .5:
                    orbit["distance"] = rng.choice([.001, rng.uniform(.001, 50)])
                parameters = {"name": "N%d" % index, "alias": "a%d" % index, "orbit": orbit,
                              "look_at": [rng.choice([0, 1, rng.uniform(-20, 20)]) for _ in range(3)]}
                if kind == "light_create":
                    parameters["settings"] = {"light_type": rng.choice(["POINT", "SUN", "SPOT", "AREA"])}
                operations.append({"kind": kind, "target": None, "parameters": parameters})
            submitted = validate_call("angelo_scene_program", {"mode": rng.choice(["APPLY", "PREVIEW"]),
                                                               "operations": operations})
            try:
                resolved = resolve(submitted, camera)
            except RefusalError:
                continue
            self.assertEqual(_encoded(validate_call("angelo_scene_program", resolved, host=True)), _encoded(resolved))

    def test_a_refusal_after_resolution_stages_nothing(self):
        executor = CountingExecutor()
        call, answers = self.submit(executor, [FAR_RIM], identity="far")
        self.assertIsNone(call)
        self.assertTrue(answers and answers[0].get("error"))
        self.assertEqual((executor.started, executor.preconditions), ([], []))


class Fixed(ControllerCase):
    """Faults and notes these tests found, asserting the fixed behaviour."""

    def test_a_far_orbit_is_a_validation_refusal_that_names_the_orbit_and_claims_no_change(self):
        """Was: refused UNKNOWN for ``location[0]`` (a field the model never wrote) with partial_changes_possible,
        after the transcript had recorded the out-of-bounds program."""
        executor = CountingExecutor()
        call, answers = self.submit(executor, [FAR_RIM], identity="far")
        self.assertIsNone(call)
        self.assertEqual(answers[0]["error_code"], "VALIDATION")
        self.assertNotIn("partial_changes_possible", answers[0])
        self.assertIs(answers[0].get("scene_changed"), False)
        self.assertIn("orbit of 'Rim Light'", answers[0]["error"])
        self.assertIn("shorter distance", answers[0]["error"])
        self.assertNotIn("location[0]", answers[0]["error"])
        self.c.pump_events()
        rows = [m for m in self.c.state["messages"] if m.get("tool") == "angelo_scene_program"]
        self.assertFalse(any("program" in (row.get("arguments") or {}) for row in rows), "no out-of-bounds program recorded")
        self.assertEqual((executor.resolutions, executor.started, executor.preconditions), (1, [], []))

    def test_a_far_orbit_leaves_the_request_free_to_correct_it(self):
        """Was: the UNKNOWN refusal armed the request's recovery barrier, refusing the correction RECOVERY_REQUIRED."""
        executor = CountingExecutor()
        self.submit(executor, [FAR_RIM], identity="far")
        call, answers = self.submit(executor, [NEAR_RIM], identity="fixed")
        self.assertFalse(answers, answers)
        self.assertTrue(call is not None and call.approval)
        self.assertFalse(has_orbit(call.program_arguments))

    def test_the_bound_is_the_create_schemas(self):
        inside = light("Rim Light", math.pi, 0., 800., 1.)
        inside["parameters"]["look_at"] = [-99998., 99998., 0.]
        location = resolve(program([inside]), CAMERA)["operations"][0]["parameters"]["location"]
        self.assertTrue(all(abs(value) <= 100000 for value in location))
        with self.assertRaises(RefusalError) as caught:
            resolve(program([FAR_RIM]), CAMERA)
        self.assertEqual(caught.exception.code, "VALIDATION")

    def test_a_camera_at_the_point_or_without_a_finite_position_is_refused_in_its_own_words(self):
        """Was: both were called "straight above or below"."""
        at_camera = light("Rim Light", math.pi, 0., 800.)
        at_camera["parameters"]["look_at"] = FACTORY_CAMERA
        for location, words in ((FACTORY_CAMERA, "stands at look_at"), ([float("nan"), 0., 0.], "not finite"),
                                ([float("inf"), 0., 0.], "not finite"), ([7.358891, -6.925791, 9.], "straight above")):
            with self.subTest(location=location), self.assertRaisesRegex(RefusalError, words) as caught:
                resolve(program([at_camera]), {"handle": CAMERA["handle"], "location": location})
            self.assertEqual(caught.exception.code, "VALIDATION")

    def test_a_location_written_beside_an_orbit_is_refused_even_the_default(self):
        """Was: "Choose location or orbit, not both" compared location with its default, so [0, 0, 0] passed.
        No recorded plan wrote both."""
        for location in ([0, 0, 0], [0., 0., 0.], [1, 2, 3]):
            operation = light("Rim Light", math.pi, .5, 800.)
            operation["parameters"]["location"] = location
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, "location or orbit"):
                validate_operation(operation)
        canonical = validate_operation(light("Rim Light", math.pi, .5, 800.))
        self.assertNotIn("location", canonical["parameters"], "an orbit has no location until it is resolved")
        self.assertEqual(validate_operation(canonical), canonical)
        resolved = resolve(program([light("Rim Light", math.pi, .5, 800.)]), CAMERA)
        self.assertEqual(list(resolved["operations"][0]["parameters"])[:5], ["name", "alias", "location", "rotation", "look_at"])

    def test_a_program_writing_what_the_camera_follows_is_refused(self):
        """Was a fault: resolve knew only the camera's own handle, so a program turning its parent (or moving a
        constraint target it follows) committed the rig around where the camera used to be."""
        binding = [{"handle": PARENT, "revision": "e" * 64}]
        camera = dict(CAMERA, follows=[PARENT])
        for change in ({"kind": "transform", "target": PARENT, "parameters": {"rotation": [0, 0, math.pi]}},
                       {"kind": "object_delete", "target": PARENT, "parameters": {}}):
            with self.subTest(kind=change["kind"]), self.assertRaisesRegex(RefusalError, "scene camera follows") as caught:
                resolve(program([change, light("Rim Light", math.pi, .5, 800.)], read_set=binding, write_set=binding),
                        camera)
            self.assertEqual(caught.exception.code, "VALIDATION")
            self.assertIn("earlier program", str(caught.exception))
        self.assertFalse(has_orbit(resolve(program(RIG), camera)), "a program that leaves them alone resolves")
        self.assertFalse(has_orbit(resolve(program(RIG, read_set=binding), camera)), "reading one moves nothing")

    def test_a_camera_following_too_many_objects_refuses_every_orbit(self):
        with self.assertRaisesRegex(RefusalError, "cannot all be read"):
            resolve(program(RIG), dict(CAMERA, follows=None))

    def test_followed_objects_walks_parents_and_constraint_targets_once(self):
        """BlenderExecutor.host_program reports these (test_blender_review_light_rig.py drives the real ones).

        Every Object a constraint points at counts, read from its RNA (object_dependencies): a custom space too,
        and a vertex parent's modifiers' Objects."""
        def thing(name, parent=None, constraints=(), parent_type="OBJECT", modifiers=()):
            return NS(name=name, parent=parent, constraints=list(constraints), matrix_world=None, type="EMPTY",
                      parent_type=parent_type, modifiers=list(modifiers), animation_data=None, data=None)

        def struct(kind="COPY_LOCATION", **fields):  # Object pointers, collections and strings, as RNA lists them.
            kinds = {list: "COLLECTION", str: "STRING"}
            return NS(bl_rna=NS(properties=[NS(identifier=name, type=kinds.get(type(value), "POINTER"),
                                                fixed_type=NS(identifier="Object"))
                                             for name, value in fields.items()]), type=kind, **fields)
        rig = thing("rig")
        path = thing("path", parent=rig)
        hub = thing("hub", constraints=[struct(target=path)])
        rig.constraints = [struct(target=hub)]                                 # A cycle is walked once.
        armature = thing("armature")
        host = thing("host", modifiers=[struct("MIRROR", mirror_object=thing("mirror"))])
        camera = thing("camera", parent=thing("dolly", parent=rig),
                       constraints=[struct(target=thing("look")), struct(target=None),
                                    struct(target=None, targets=[struct(target=armature)]),
                                    struct(target=None, space_object=thing("space")),
                                    struct(target=host, subtarget="Tip")])
        handles = followed_objects(camera, lambda obj: obj.name)
        self.assertEqual(sorted(handles), ["armature", "dolly", "host", "hub", "look", "mirror", "path", "rig", "space"])
        self.assertEqual(len(handles), len(set(handles)))
        self.assertIsNone(followed_objects(camera, lambda obj: obj.name, limit=3))
        self.assertEqual(followed_objects(thing("pinned", parent=host), lambda obj: obj.name), ["host"])  # Origin only.
        self.assertEqual(followed_objects(thing("alone"), lambda obj: obj.name), [])

    def test_resolving_counts_toward_precondition_ms(self):
        """Was: staged_program ran before Controller._measure, leaving the camera read out of precondition_ms."""
        events, original = [], Controller._measure

        def measure(controller, metrics, phase, callback):
            def measured():
                events.append(phase + " start")
                try:
                    return callback()
                finally:
                    events.append(phase + " end")
            return original(controller, metrics, phase, measured)

        executor = CountingExecutor()
        resolve_with = executor.host_program
        executor.host_program = lambda arguments, used=None: (events.append("orbit"), resolve_with(arguments, used))[1]
        with patch.object(Controller, "_measure", measure):
            call, _answers = self.submit(executor)
        self.assertTrue(call is not None and call.approval)
        self.assertEqual(events[:3], ["precondition_ms start", "orbit", "precondition_ms end"])


class Observations(ControllerCase):
    def test_the_frame_is_the_camera_position_not_what_it_sees(self):
        """Documented (agent_execution.rst, "Cameras and lights"): the frame is built from the camera's position
        alone, so azimuth pi/2 is "its right" only for an unrolled camera looking at the point.

        A camera 1 mm off vertical above the point is accepted (the floor is 0.0001), and that millimetre picks
        the frame: from (0.001, 0, 10) with Blender's default orientation (rotation 0: looking down -Z, screen
        right +X, screen up +Y) azimuth pi/2 lands at +Y, the top of its picture, and pi at -X, its left. A rolled
        camera (a Dutch angle, a portrait roll) is the same: the orbit never reads the camera's rotation.
        """
        right = orbit_location([0.001, 0., 10.], CUBE, {"azimuth": math.pi / 2, "elevation": 0., "distance": 5.})
        behind = orbit_location([0.001, 0., 10.], CUBE, {"azimuth": math.pi, "elevation": 0., "distance": 5.})
        self.assertEqual((right, behind), ([0., 5., 0.], [-5., 0., 0.]))

    def test_any_edit_of_the_camera_object_refuses_the_orbit(self):
        """Stands: a lens change or a rename moves no camera, yet refuses the whole program (one more round).
        Telling the edits that move it from those that do not is left for when a recorded plan needs both."""
        binding = [{"handle": CAMERA["handle"], "revision": "d" * 64}]
        for change in ({"kind": "camera_configure", "target": CAMERA["handle"], "parameters": {"settings": {"lens": 85}}},
                       {"kind": "object_rename", "target": CAMERA["handle"], "parameters": {"name": "Shot"}}):
            with self.subTest(kind=change["kind"]), self.assertRaisesRegex(RefusalError, "edits the scene camera"):
                resolve(program([change] + RIG, read_set=binding, write_set=binding), CAMERA)

if __name__ == "__main__":
    unittest.main()
