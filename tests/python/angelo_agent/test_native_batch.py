# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``batch``: the pure rules a plan is validated, counted and approved by, before anything is staged.

Dependency-free: the instances a batch makes (rows, positions, a path, a
scatter, mirroring, seeded variation), their names and aliases, how a batch
counts against the program's caps, its bindings and its derived checks.
Staging, the scene, Undo and recovery are covered by test_blender_batch.py in
the compiled app.
"""

import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_checks import required_checks
from angelo_agent import batch_spec  # noqa: E402
from angelo_agent.action_catalog import (affected_references, resource_dependencies, validate_operation,  # noqa: E402
                                         written_references)
from angelo_agent.action_schemas import CREATE_KINDS  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS, deferred_references, lookup  # noqa: E402
from angelo_agent.program_spec import MAX_CHECKS, MAX_OPERATIONS, validate_program  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
REV = "b" * 64
POST = {"kind": "create_primitive", "target": None,
        "parameters": {"primitive": "CUBE", "name": "Post 01", "alias": "post", "location": [-3, -0.7, 0.8]}}


def batch(sources=("$post",), **parameters):
    return {"kind": "batch", "target": None, "parameters": dict(sources=list(sources), **parameters)}


def params(**parameters):
    return validate_operation(batch(**parameters))["parameters"]


def program(*operations, **sets):
    return dict({"mode": "APPLY", "operations": list(operations)}, **sets)


class InstanceTests(unittest.TestCase):
    def test_rows_nest_like_stacked_arrays_and_start_with_the_unit(self):
        plan = batch_spec.instances(params(array=[{"step": [1.5, 0, 0], "count": 3}, {"step": [0, 0, 0.5], "count": 2}]))
        self.assertEqual([entry["offset"] for entry in plan],
                         [[0, 0, 0], [1.5, 0, 0], [3, 0, 0], [0, 0, 0.5], [1.5, 0, 0.5], [3, 0, 0.5]])
        self.assertEqual({(entry["rotation"], entry["scale"], entry["mirror"]) for entry in plan}, {(None, None, False)})

    def test_positions_carry_their_own_rotation_and_scale(self):
        plan = batch_spec.instances(params(at=[[0, 0, 0], [2, 0, 0]], rotations=[[0, 0, 0], [0, 0, 1]],
                                           scales=[[1, 1, 1], [2, 2, 2]]))
        self.assertEqual(plan[1], {"position": [2, 0, 0], "rotation": [0, 0, 1], "scale": [2, 2, 2], "mirror": False})

    def test_a_path_places_at_a_spacing_or_an_even_count(self):
        along = {"points": [[0, 0, 0], [4, 0, 0], [4, 3, 0]]}
        spaced = batch_spec.instances(params(along=dict(along, spacing=1.0, align=True)))
        self.assertEqual([[round(v, 6) for v in entry["position"]] for entry in spaced],
                         [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0], [4, 0, 0], [4, 1, 0], [4, 2, 0], [4, 3, 0]])
        self.assertEqual([round(entry["rotation"][2], 6) for entry in spaced], [0] * 5 + [round(math.pi / 2, 6)] * 3)
        counted = batch_spec.instances(params(along=dict(along, count=3)))
        self.assertEqual([[round(v, 6) for v in entry["position"]] for entry in counted], [[0, 0, 0], [3.5, 0, 0], [4, 3, 0]])
        self.assertIsNone(counted[0]["rotation"])
        # An end post exactly on the last point is included, as on a 24 m deck with a post every 1.5 m.
        deck = params(along={"points": [[-12, 0, 0], [12, 0, 0]], "spacing": 1.5})
        self.assertEqual(batch_spec.instance_count(deck), 17)

    def test_a_scatter_puts_one_instance_in_each_slice_of_the_longest_axis(self):
        scatter = params(scatter={"count": 8, "min": [4.5, -18, -3], "max": [8, 18, -2.4]})
        plan = batch_spec.instances(scatter)
        self.assertEqual([int((entry["position"][1] + 18) // 4.5) for entry in plan], list(range(8)))
        for entry in plan:
            self.assertTrue(4.5 <= entry["position"][0] <= 8 and -3 <= entry["position"][2] <= -2.4)

    def test_the_same_seed_is_the_same_scene_everywhere(self):
        # SplitMix64 of the seed, instance and channel: pinned, so no platform or Python version moves a rock.
        self.assertEqual([round(batch_spec.unit(17, index, channel), 12) for index, channel in ((0, 0), (0, 1), (7, 6), (15, 11))],
                         [0.764878694008, 0.021461119153, 0.981065268986, 0.825085098912])
        first = batch_spec.instances(params(scatter={"count": 4, "min": [0, 0, 0], "max": [1, 8, 0]},
                                            vary={"seed": 17, "scale": [[0.5, 0.5, 0.5], [2, 2, 2]]}))
        again = batch_spec.instances(params(scatter={"count": 4, "min": [0, 0, 0], "max": [1, 8, 0]},
                                            vary={"seed": 17, "scale": [[0.5, 0.5, 0.5], [2, 2, 2]]}))
        other = batch_spec.instances(params(scatter={"count": 4, "min": [0, 0, 0], "max": [1, 8, 0]},
                                            vary={"seed": 18, "scale": [[0.5, 0.5, 0.5], [2, 2, 2]]}))
        self.assertEqual(json.dumps(first), json.dumps(again))
        self.assertNotEqual(json.dumps(first), json.dumps(other))

    def test_mirror_follows_every_instance_and_varies_on_its_own(self):
        plan = batch_spec.instances(params(array=[{"step": [1, 0, 0], "count": 3}], mirror="Y",
                                           vary={"seed": 2, "rotation": [[0, 0, 0], [0, 0, 6]]}))
        self.assertEqual([entry["mirror"] for entry in plan], [False] * 3 + [True] * 3)
        self.assertEqual([entry["offset"] for entry in plan[3:]], [entry["offset"] for entry in plan[:3]])
        self.assertEqual(len({entry["rotation"][2] for entry in plan}), 6)

    def test_an_equal_scale_range_is_one_factor_and_any_other_is_per_axis(self):
        uniform = batch_spec.instances(params(at=[[0, 0, 0], [1, 0, 0]], vary={"seed": 5, "scale": [[0.5, 0.5, 0.5], [2, 2, 2]]}))
        self.assertTrue(all(len(set(entry["scale"])) == 1 and 0.5 <= entry["scale"][0] <= 2 for entry in uniform))
        free = batch_spec.instances(params(at=[[0, 0, 0], [1, 0, 0]], vary={"seed": 5, "scale": [[0.5, 0.5, 0.5], [2, 1, 3]]}))
        self.assertTrue(all(len(set(entry["scale"])) == 3 for entry in free))
        moved = batch_spec.instances(params(at=[[0, 0, 0], [1, 0, 0]], vary={"seed": 5, "offset": [0.25, 0, 0]}))
        self.assertTrue(all(abs(entry["position"][0] - index) <= 0.25 and entry["position"][1:] == [0, 0]
                            for index, entry in enumerate(moved)))


class NamingTests(unittest.TestCase):
    def test_copies_continue_a_numbered_name_or_take_blenders_copy_suffix(self):
        self.assertEqual(batch_spec.copy_names("Railing Post 01", 4), ["Railing Post 02", "Railing Post 03", "Railing Post 04"])
        self.assertEqual(batch_spec.copy_names("Lamp 9", 3), ["Lamp 10", "Lamp 11"])
        self.assertEqual(batch_spec.copy_names("Rock", 12)[:2] + batch_spec.copy_names("Rock", 12)[-1:],
                         ["Rock.001", "Rock.002", "Rock.011"])
        self.assertEqual(batch_spec.copy_names("Plank", 3), ["Plank.001", "Plank.002"])
        self.assertEqual(batch_spec.copy_names("Tile", 1002)[-1], "Tile.1001", "past .999, as Blender continues")

    def test_a_name_without_a_number_stays_that_name_as_blender_reads_it(self):
        """A brief that gives several parts one name ("'Bridge Abutments' are stone blocks under both deck ends")
        is met by a unit and its batch copies: Blender's own copies of 'Name' are 'Name.001', ..., the names an
        MCP script's Object.copy() or Shift+D give and that name lookups strip back to 'Name'. Copies named
        'Name 2' are another name than the one the brief asked for."""
        import re
        for name in ("Bridge Abutments", "Fence Post", "Window", "Pillar Left", "A"):
            for copy in batch_spec.copy_names(name, 5):
                self.assertEqual(re.sub(r"\.\d{3,}$", "", copy), name)
        # A name ending in Blender's suffix already continues it, sharing the numbering with its base.
        self.assertEqual(batch_spec.copy_names("Rock.004", 3), ["Rock.005", "Rock.006"])
        self.assertEqual(batch_spec.unit_names(["Rock", "Rock.001"], 2), [(["Rock.002"], 2), (["Rock.003"], 2)])

    def test_sources_of_one_stem_share_its_numbering(self):
        # Three piers mirrored: the copies take the next block, never a name another part of the unit has.
        piers = batch_spec.unit_names(["Pier 1", "Pier 2", "Pier 3", "Abutment"], 2)
        self.assertEqual(piers, [(["Pier 4"], 3), (["Pier 5"], 3), (["Pier 6"], 3), (["Abutment.001"], 1)])
        rows = batch_spec.unit_names(["Lamp Post 01", "Lamp Lantern 01", "Lamp Post 02"], 3)
        self.assertEqual(rows, [(["Lamp Post 03", "Lamp Post 05"], 2), (["Lamp Lantern 02", "Lamp Lantern 03"], 1),
                                (["Lamp Post 04", "Lamp Post 06"], 2)])
        self.assertEqual(batch_spec.unit_names(["Rock", "Rock 1"], 2), [(["Rock.001"], 1), (["Rock 2"], 1)])
        # A check outside the program finds a source's copies from the first one's name and the stride.
        self.assertEqual(batch_spec.copy_names("Lamp Post 02", 3, first="Lamp Post 04", stride=2),
                         ["Lamp Post 04", "Lamp Post 06"])

    def test_aliases_follow_the_source(self):
        operation = validate_operation(batch(["$post", OBJ], array=[{"step": [1, 0, 0], "count": 3}]))
        self.assertEqual(batch_spec.aliases(operation, 2), ["$post_2", "$post_3", "$copy2_1_2", "$copy2_1_3"])
        with self.assertRaisesRegex(ValueError, "shorten the alias"):
            validate_operation(batch(["$" + "p" * 31], array=[{"step": [1, 0, 0], "count": 3}]))


class RuleTests(unittest.TestCase):
    def test_exactly_one_placement_or_mirror_alone(self):
        self.assertEqual(batch_spec.instance_count(params(mirror="X")), 2)
        for refused in ({}, {"array": [{"step": [1, 0, 0], "count": 2}], "at": [[0, 0, 0], [1, 0, 0]]},
                        {"at": [[0, 0, 0]]}, {"at": [[0, 0, 0], [1, 0, 0]], "rotations": [[0, 0, 0]]},
                        {"rotations": [[0, 0, 0], [0, 0, 1]], "mirror": "X"},
                        {"at": [[0, 0, 0], [1, 0, 0]], "scales": [[1, 1, 1], [0, 1, 1]]},
                        {"along": {"points": [[0, 0, 0], [1, 0, 0]]}},
                        {"along": {"points": [[0, 0, 0], [1, 0, 0]], "spacing": 0.5, "count": 3}},
                        {"along": {"points": [[0, 0, 0], [0, 0, 0]], "count": 3}},
                        {"scatter": {"count": 3, "min": [1, 0, 0], "max": [0, 1, 1]}},
                        {"mirror": "X", "vary": {"seed": 1}},
                        {"mirror": "X", "vary": {"scale": [[2, 2, 2], [1, 1, 1]]}},
                        {"mirror": "X", "vary": {"scale": [[0, 1, 1], [1, 1, 1]]}},
                        {"mirror": "X", "vary": {"offset": [-1, 0, 0]}},
                        {"mirror": "W"}, {"mirror": "X", "expression": "x * 2"}, {"mirror": "X", "path": "/tmp/a.blend"}):
            with self.subTest(parameters=refused), self.assertRaises(ValueError):
                validate_operation(batch(**refused))
        with self.assertRaises(ValueError):
            validate_operation(batch([], mirror="X"))
        with self.assertRaises(ValueError):
            validate_operation(batch(["$a"] * 2, mirror="X"))
        with self.assertRaises(ValueError):
            validate_operation(batch(["$part%d" % index for index in range(9)], mirror="X"))
        with self.assertRaises(ValueError):
            validate_operation(dict(batch(mirror="X"), target=OBJ))

    def test_what_a_batch_reads_writes_and_creates(self):
        self.assertIn("batch", CREATE_KINDS)
        row = validate_operation(batch([OBJ], array=[{"step": [1, 0, 0], "count": 3}]))
        self.assertEqual((affected_references(row), written_references(row)), ((OBJ,), ()))
        placed = validate_operation(batch([OBJ], at=[[0, 0, 0], [1, 0, 0]]))
        self.assertEqual(written_references(placed), (OBJ,), "A batch that places its unit moves the source")
        varied = validate_operation(batch([OBJ], mirror="X", vary={"seed": 1, "rotation": [[0, 0, 0], [0, 0, 1]]}))
        self.assertEqual(written_references(varied), (OBJ,))
        self.assertEqual(resource_dependencies(row)["creates"], ["object", "data_if_full"])


class CountingTests(unittest.TestCase):
    """A batch is one operation; what it expands to has its own bounds."""

    def test_a_batch_is_one_of_the_programs_operations_whatever_it_makes(self):
        operations = [POST, batch(array=[{"step": [1, 0, 0], "count": 100}])] + [
            {"kind": "transform", "target": "$post_%d" % index, "parameters": {"rotation": [0, 0, 0.1]}}
            for index in range(2, MAX_OPERATIONS)]
        canonical = validate_program(program(*operations))
        self.assertEqual(len(canonical["operations"]), MAX_OPERATIONS)
        self.assertEqual(batch_spec.copies(canonical["operations"][1]["parameters"]), 99)
        with self.assertRaisesRegex(ValueError, "1-32 bounded operations"):
            validate_program(program(*operations, operations[-1]))

    def test_copies_are_bounded_per_batch_and_per_program(self):
        self.assertEqual((batch_spec.MAX_BATCH_COPIES, batch_spec.MAX_PROGRAM_COPIES, batch_spec.MAX_BATCH_PARTS), (1024, 1024, 8))
        validate_program(program(POST, batch(array=[{"step": [1, 0, 0], "count": 1025}])))
        with self.assertRaisesRegex(ValueError, "BATCH_TOO_LARGE: 1600 instances of 1 source"):
            validate_program(program(POST, batch(array=[{"step": [1, 0, 0], "count": 40}, {"step": [0, 1, 0], "count": 40}])))
        with self.assertRaisesRegex(ValueError, "BATCH_TOO_LARGE"):
            validate_program(program(POST, batch(along={"points": [[0, 0, 0], [5000, 0, 0]], "spacing": 1})))
        other = dict(POST, parameters=dict(POST["parameters"], name="Rail 01", alias="rail"))
        with self.assertRaisesRegex(ValueError, "PROGRAM_TOO_LARGE: this program's batches would create 1198 objects"):
            validate_program(program(POST, other, batch(array=[{"step": [1, 0, 0], "count": 600}]),
                                     batch(["$rail"], array=[{"step": [1, 0, 0], "count": 600}])))
        unit = [dict(POST, parameters=dict(POST["parameters"], name="Part %d" % index, alias="part%d" % index))
                for index in range(8)]
        with self.assertRaisesRegex(ValueError, "BATCH_TOO_LARGE: 130 instances of 8 source"):
            validate_program(program(*unit, batch(["$part%d" % index for index in range(8)],
                                                  array=[{"step": [1, 0, 0], "count": 130}])))

    def test_a_copys_alias_is_usable_later_and_never_redefined(self):
        later = {"kind": "modifier_add", "target": "$post_5", "parameters": {"type": "BEVEL", "settings": {}}}
        validate_program(program(POST, batch(array=[{"step": [1, 0, 0], "count": 5}]), later))
        with self.assertRaisesRegex(ValueError, "missing or forward-created alias"):
            validate_program(program(POST, batch(array=[{"step": [1, 0, 0], "count": 4}]), later))
        with self.assertRaisesRegex(ValueError, "missing or forward-created alias"):
            validate_program(program(POST, later, batch(array=[{"step": [1, 0, 0], "count": 5}])))
        clash = dict(POST, parameters=dict(POST["parameters"], name="Other", alias="post_2"))
        with self.assertRaisesRegex(ValueError, "cannot be redefined"):
            validate_program(program(POST, clash, batch(array=[{"step": [1, 0, 0], "count": 3}])))

    def test_a_declared_source_is_bound_like_any_dependency(self):
        row = batch([OBJ], array=[{"step": [1, 0, 0], "count": 3}])
        with self.assertRaisesRegex(ValueError, "must appear in the read or write set"):
            validate_program(program(row))
        validate_program(program(row, read_set=[{"handle": OBJ, "revision": REV}]))
        placed = batch([OBJ], at=[[0, 0, 0], [1, 0, 0]])
        with self.assertRaisesRegex(ValueError, "must appear in the write set"):
            validate_program(program(placed, read_set=[{"handle": OBJ, "revision": REV}]))
        validate_program(program(placed, write_set=[{"handle": OBJ, "revision": REV}]))


class DerivedCheckTests(unittest.TestCase):
    def test_one_copies_check_per_source(self):
        lantern = dict(POST, parameters=dict(POST["parameters"], name="Lantern 01", alias="lantern"))
        checks = derive_checks([validate_operation(operation) for operation in (
            POST, lantern, batch(["$post", "$lantern", OBJ], array=[{"step": [1, 0, 0], "count": 5}], mirror="Y", linked=False))])
        copies = [check for check in checks if check["kind"] == "copies"]
        self.assertEqual(copies, [{"kind": "copies", "target": "$post", "count": 9, "linked": False},
                                  {"kind": "copies", "target": "$lantern", "count": 9, "linked": False},
                                  {"kind": "copies", "target": OBJ, "count": 9, "linked": False, "alias": "$copy1_2"}])
        for check in copies:
            validate_check(check)
        self.assertIn("copies", DEFERRED_CHECKS)

    def test_a_row_keeps_the_sources_checks_and_a_placement_drops_its_transform(self):
        row = derive_checks([validate_operation(POST), validate_operation(batch(array=[{"step": [1, 0, 0], "count": 3}]))])
        self.assertEqual([check["kind"] for check in row], ["object_name", "transform", "dimensions", "copies"])
        placed = derive_checks([validate_operation(POST), validate_operation(batch(at=[[0, 0, 0], [1, 0, 0]]))])
        self.assertEqual([check["kind"] for check in placed], ["object_name", "copies"])

    def test_renaming_the_source_afterwards_drops_its_copies_check(self):
        rename = {"kind": "object_rename", "target": "$post", "parameters": {"name": "Pillar"}}
        checks = derive_checks([validate_operation(operation) for operation in (
            POST, batch(array=[{"step": [1, 0, 0], "count": 3}]), rename)])
        self.assertNotIn("copies", [check["kind"] for check in checks])

    def test_every_copy_and_source_check_survives_the_authored_cap(self):
        units = [dict(POST, parameters=dict(POST["parameters"], name="Part %d 01" % index, alias="p%d" % index,
                                            rotation=[0, 0, 0.1], scale=[2, 1, 1])) for index in range(12)]
        rows = [batch(["$p%d" % index], array=[{"step": [1, 0, 0], "count": 50}]) for index in range(12)]
        canonical = validate_call("angelo_scene_program", program(*units, *rows))
        self.assertEqual(len(required_checks(canonical)), 60)
        self.assertEqual(canonical["expected_checks"], [])
        self.assertEqual(sum(batch_spec.copies(operation["parameters"]) for operation in canonical["operations"][12:]), 588)
        # Every source and every copy group is checked, without priority or truncation.
        kinds = [check["kind"] for check in required_checks(canonical)]
        self.assertEqual(kinds.count("copies"), 12)
        self.assertEqual(validate_call("angelo_scene_program", canonical)["expected_checks"], canonical["expected_checks"],
                         "Validating a canonical program again changes nothing")


class EvaluatorTests(unittest.TestCase):
    """The copies check on stand-in objects: by the batch's aliases when staged, else by the continued names."""

    def setUp(self):
        self.evaluate = batch_spec.evaluate_copies
        self.data = object()
        self.source = NS(name="Post 01", type="MESH", data=self.data)
        self.copies = [NS(name="Post %02d" % index, type="MESH", data=self.data) for index in (2, 3, 4)]

    def reader(self, targets=None, scene=()):
        spent = []
        return NS(context={"targets": targets or {}, "scene_objects": list(scene)}, spend=spent.append,
                  scene_objects=lambda: list(scene))

    def check(self, **fields):
        return dict({"kind": "copies", "target": "$post", "count": 3, "linked": True}, **fields)

    def test_staged_copies_are_found_by_alias(self):
        targets = {"$post_%d" % index: copy for index, copy in zip((2, 3, 4), self.copies)}
        passed, observed = self.evaluate(self.reader(targets), self.source, self.check())
        self.assertTrue(passed)
        self.assertEqual(observed, {"copies": 3, "linked": True, "first": "Post 02", "last": "Post 04"})

    def test_live_copies_are_found_by_the_continued_names(self):
        passed, observed = self.evaluate(self.reader(scene=[self.source, *self.copies]), self.source, self.check())
        self.assertTrue(passed and observed["copies"] == 3)
        passed, observed = self.evaluate(self.reader(scene=[self.source, *self.copies[:2]]), self.source, self.check())
        self.assertFalse(passed)
        self.assertEqual(observed["copies"], 2)

    def test_a_stated_first_name_and_stride_find_interleaved_copies(self):
        others = [NS(name="Post %02d" % index, type="MESH", data=self.data) for index in (5, 6)]
        scene = [self.source, *self.copies, *others]
        check = self.check(count=2, first="Post 04", stride=2)
        validate_check(check)
        passed, observed = self.evaluate(self.reader(scene=scene), self.source, check)
        self.assertTrue(passed)
        self.assertEqual((observed["first"], observed["last"]), ("Post 04", "Post 06"))
        for refused in ({"stride": 0}, {"stride": 9}, {"first": ""}, {"first": "x" * 64}, {"names": ["Post 04"]}):
            with self.subTest(fields=refused), self.assertRaises(ValueError):
                validate_check(self.check(**refused))

    def test_linked_means_the_sources_own_data_and_type(self):
        self.copies[1].data = object()
        passed, observed = self.evaluate(self.reader(scene=[self.source, *self.copies]), self.source, self.check())
        self.assertFalse(passed)
        self.assertFalse(observed["linked"])
        self.assertTrue(self.evaluate(self.reader(scene=[self.source, *self.copies]), self.source, self.check(linked=False))[0])
        self.copies[1].type = "CURVE"
        self.assertFalse(self.evaluate(self.reader(scene=[self.source, *self.copies]), self.source, self.check(linked=False))[0])


class SchemaTests(unittest.TestCase):
    def test_batch_is_named_in_every_plan_and_read_on_demand(self):
        self.assertIn("batch", DEFERRED_ACTIONS)
        (exact,) = lookup("ACTIONS", ["batch"])["actions"]
        self.assertEqual(exact["target"], "null")
        self.assertEqual(exact["parameters"]["required"], ["sources"])
        used = deferred_references([{"tool": "angelo_scene_program", "arguments": program(POST, batch(mirror="X"))}])
        self.assertEqual(used, {"ACTIONS": ["batch"]})
        self.assertLess(len(json.dumps(lookup("ACTIONS", ["batch"]), separators=(",", ":"))), 3400)

    def test_every_model_reads_what_the_placements_mean_and_it_is_what_they_do(self):
        """Regression: in a one-shot footbridge GPT-6.1-Sol read at?:Vec3[] as offsets.

        It placed a post at (-1.85, -0.5, 0.75) and wrote at [[3.7, 0, 0], [0, 1, 0], [3.7, 1, 0]] for three
        more: the post moved to (3.7, 0, 0) and two copies followed, three posts, two of them off the deck.
        """
        from angelo_agent.codex_reference import program_reference
        note = batch_spec.PLACEMENT_NOTE
        for text in (program_reference(), program_reference(plan=True)):
            self.assertIn(note, text)
        (exact,) = lookup("ACTIONS", ["batch"])["actions"]
        self.assertEqual(exact["note"], note, "a lookup (a Codex thread's correction) carries the same note")
        self.assertNotIn("note", lookup("ACTIONS", ["transform"])["actions"][0])
        # The note's claims, against the rules the host runs.
        self.assertIn("world positions", note)
        self.assertIn("n positions make n-1 more instances (2n-1 with mirror), each copying every source", note)
        mirrored = params(at=[[0, 0, 0], [1, 0, 0], [2, 0, 0]], mirror="X")
        self.assertEqual(batch_spec.copies(mirrored), 2 * 3 - 1)
        self.assertEqual(batch_spec.copies(params(sources=("$post", "$cap"), at=[[0, 0, 0], [1, 0, 0]])), 2)
        recorded = params(at=[[3.7, 0, 0], [0, 1, 0], [3.7, 1, 0]])
        placed = batch_spec.instances(recorded)
        self.assertEqual([item["position"] for item in placed], [[3.7, 0, 0], [0, 1, 0], [3.7, 1, 0]])
        self.assertEqual(batch_spec.copies(recorded), 2, "the unit itself is the first of the three")
        self.assertIn("array steps are offsets from the unit, and each count includes it", note)
        row = params(array=[{"step": [0, 1, 0], "count": 2}])
        self.assertEqual([item["offset"] for item in batch_spec.instances(row)], [[0, 0, 0], [0, 1, 0]])
        self.assertEqual(batch_spec.copies(row), 1)

    def test_claudes_plan_states_batch_exactly_with_its_note(self):
        """Opus footbridge plans wrote one create per post and rail.

        batch was named in Claude's plan but not specified, so using it cost a lookup round; the plans spent about
        220 output tokens on the copies instead (doc/angelo/performance.rst, "Plan output tokens").
        """
        from angelo_agent.plan_schema import CLAUDE_CORE_ACTIONS, CLAUDE_REPEAT_ACTIONS, core_operation
        from angelo_agent.tool_specs import native_plan_schema
        self.assertEqual(CLAUDE_REPEAT_ACTIONS, ("batch",))
        self.assertIn("batch", CLAUDE_CORE_ACTIONS)
        plan = [{"tool": "angelo_scene_program", "arguments": program(POST, batch(mirror="X"))}]
        self.assertEqual(deferred_references(plan, CLAUDE_CORE_ACTIONS), {}, "no lookup before a batch")
        self.assertEqual(core_operation("batch")["properties"]["parameters"]["description"], batch_spec.PLACEMENT_NOTE)
        self.assertIn(json.dumps(batch_spec.PLACEMENT_NOTE), json.dumps(native_plan_schema()))
        # Codex declarations and the strict plan keep it deferred: their reference states it as TypeScript.
        self.assertIn("batch", DEFERRED_ACTIONS)

    def test_the_notes_order_and_names_are_the_rules_the_host_runs(self):
        note = batch_spec.PLACEMENT_NOTE
        self.assertIn("Copies continue the unit's name (Post 1, Post 2; Pier, Pier.001) and alias ($post_2, $post_3)",
                      note)
        self.assertEqual(batch_spec.copy_names("Post 1", 3), ["Post 2", "Post 3"])
        self.assertEqual(batch_spec.copy_names("Pier", 2), ["Pier.001"])
        self.assertEqual(batch_spec.aliases(validate_operation(batch(mirror="Y")), 1), ["$post_2"])
        self.assertIn("give the unit its material and modifiers before the batch", note)
        paint = {"kind": "material_principled", "target": "$post", "parameters": {"name": "Stone"}}
        bevel = {"kind": "modifier_add", "target": "$post", "parameters": {"type": "BEVEL", "settings": {}}}
        validate_program(program(POST, paint, bevel, batch(mirror="X")))
        with self.assertRaisesRegex(ValueError, "LINKED_EDIT"):  # A Sol arched-footbridge refusal.
            validate_program(program(POST, batch(mirror="X"), paint))
        validate_program(program(POST, batch(mirror="X", linked=False), paint))  # Own data: allowed, copies plain.
        # The same material on every linked copy is the shared data's own state: admitted (program_rules).
        everyone = {"kind": "material_principled", "target": "$post", "parameters": {"name": "Stone", "also": ["$post_2"]}}
        validate_program(program(POST, batch(mirror="X"), everyone))

    def test_no_field_takes_an_expression_a_path_or_a_name_lookup(self):
        schema = json.dumps(batch_spec.BATCH_SCHEMA)
        for word in ("expression", "path", "filepath", "script", "code", "name"):
            self.assertNotIn('"%s"' % word, schema)
        self.assertIs(batch_spec.BATCH_SCHEMA["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
