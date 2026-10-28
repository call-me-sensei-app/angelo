# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script's Booleans, measured (script_booleans): the measure, the facts, the rows, the hook and the live reading.

Host stand-ins only. The real operator, solvers, evaluated meshes and the executor's hook are in
test_blender_script_booleans.py.
"""

import importlib.util
import itertools
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_scene_queries import queries  # noqa: E402,F401  (scene_queries on a stand-in bpy, for script_after)
from angelo_agent import model_results, script_after, script_booleans as booleans  # noqa: E402

HAS_NUMPY = importlib.util.find_spec("numpy") is not None
IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
CUBE_FACES = [(0, 2, 6, 4), (1, 5, 7, 3), (0, 4, 5, 1), (2, 3, 7, 6), (0, 1, 3, 2), (4, 6, 7, 5)]   # Outward.


def matrix(scale=(1.0, 1.0, 1.0), offset=(0.0, 0.0, 0.0)):
    return [[scale[0], 0.0, 0.0, offset[0]], [0.0, scale[1], 0.0, offset[1]], [0.0, 0.0, scale[2], offset[2]],
            [0.0, 0.0, 0.0, 1.0]]


class Elements(list):
    """A mesh element collection: ``len`` and ``foreach_get`` of one flat property."""

    def __init__(self, values, prop):
        super().__init__(values)
        self._prop = prop

    def foreach_get(self, prop, out):
        assert prop == self._prop, prop
        flat = [value for item in self for value in (item if isinstance(item, tuple) else (item,))]
        out[:] = flat


class Attribute:
    def __init__(self, values, key):
        self.data = SimpleNamespace(foreach_get=lambda name, out: Elements(values, key).foreach_get(name, out))


class FakeMesh:
    """A mesh from vertices and faces, read through attributes (as Blender 5.2's) or, without them, through RNA."""

    def __init__(self, vertices, faces, attributes=True):
        edges, corner_edge, corner_vert, starts = {}, [], [], []
        for face in faces:
            starts.append(len(corner_vert))
            for index, vertex in enumerate(face):
                key = tuple(sorted((vertex, face[(index + 1) % len(face)])))
                corner_edge.append(edges.setdefault(key, len(edges)))
                corner_vert.append(vertex)
        ends = sorted(edges, key=edges.get)
        self.vertices = Elements([tuple(map(float, v)) for v in vertices], "co")
        self.polygons = Elements(starts, "loop_start")
        self.edges = Elements(ends, "vertices")
        self.loops = Elements(corner_edge, "edge_index")
        self.attributes = {}
        if attributes:
            self.attributes = {"position": Attribute(self.vertices, "vector"),
                               ".corner_edge": Attribute(corner_edge, "value"),
                               ".corner_vert": Attribute(corner_vert, "value"), ".edge_verts": Attribute(ends, "value")}
        else:   # RNA reads: loops give edge_index or vertex_index.
            loops = self.loops

            def foreach_get(prop, out):
                out[:] = corner_edge if prop == "edge_index" else corner_vert
            loops.foreach_get = foreach_get


def cube(lo=(-1, -1, -1), hi=(1, 1, 1), faces=CUBE_FACES, **kwargs):
    vertices = [(x, y, z) for x, y, z in itertools.product((lo[0], hi[0]), (lo[1], hi[1]), (lo[2], hi[2]))]
    return FakeMesh(vertices, faces, **kwargs)


@unittest.skipUnless(HAS_NUMPY, "NumPy is not installed")
class MeasureTests(unittest.TestCase):
    def test_a_closed_outward_cube_has_its_volume_faces_and_world_bounds(self):
        reading = booleans.measure(cube(), matrix((2.0, 1.0, 1.0), (10.0, 0.0, 0.0)))
        self.assertEqual(reading["faces"], 6)
        self.assertAlmostEqual(reading["volume"], 16.0)
        self.assertEqual(reading["bounds"], [[8.0, -1.0, -1.0], [12.0, 1.0, 1.0]])

    def test_rna_reads_give_the_same_reading(self):
        self.assertEqual(booleans.measure(cube(attributes=False), IDENTITY), booleans.measure(cube(), IDENTITY))

    def test_inward_winding_is_a_negative_volume_and_a_mirror_is_not(self):
        inward = [tuple(reversed(face)) for face in CUBE_FACES]
        self.assertAlmostEqual(booleans.measure(cube(faces=inward), IDENTITY)["volume"], -8.0)
        self.assertAlmostEqual(booleans.measure(cube(), matrix((-1.0, 1.0, 1.0)))["volume"], 8.0,
                               msg="Blender's Boolean flips a negative transform's faces: not inside out")

    def test_far_from_its_origin_a_small_part_keeps_its_volume(self):
        reading = booleans.measure(cube((1000.0,) * 3, (1000.5,) * 3), matrix(offset=(-1000.0, 0.0, 0.0)))
        self.assertAlmostEqual(reading["volume"], 0.125, delta=1e-12)   # Exact in float32, summed about the mean.

    def test_an_open_mesh_a_mixed_winding_and_no_faces_say_why(self):
        self.assertEqual(booleans.measure(cube(faces=CUBE_FACES[:5]), IDENTITY)["why"], "open mesh")
        mixed = CUBE_FACES[:5] + [tuple(reversed(CUBE_FACES[5]))]
        self.assertEqual(booleans.measure(cube(faces=mixed), IDENTITY)["why"], "faces wound both ways")
        empty = booleans.measure(cube(faces=[]), IDENTITY)
        self.assertEqual((empty["faces"], empty["why"]), (0, "no faces"))

    def test_past_the_triangle_cap_only_faces_are_read(self):
        with patch.object(booleans, "MAX_TRIANGLES", 11):
            reading = booleans.measure(cube(), IDENTITY)
        self.assertEqual(reading, {"faces": 6, "why": "over 11 triangles"})


class FactTests(unittest.TestCase):
    def reading(self, faces, volume=None, bounds=((0, 0, 0), (1, 1, 1)), why=None):
        out = {"faces": faces, "bounds": [list(map(float, corner)) for corner in bounds]}
        out.update({"volume": volume} if volume is not None else {"why": why or "open mesh"})
        return out

    def test_the_extremes_and_nothing_in_between(self):
        cases = [((6, 64.0), (6, 64.0), ["DIFFERENCE"], "volume and faces unchanged (within 0.0064 m³)"),
                 ((6, 64.0), (12, 16.0), ["DIFFERENCE"], "kept 25% of its volume"),
                 ((6, 64.0), (12, 80.0), ["DIFFERENCE"], "volume grew by 25%"),
                 ((6, 64.0), (12, 80.0), ["INTERSECT"], "volume grew by 25%"),
                 ((6, 64.0), (12, 48.0), ["UNION"], "volume shrank by 25%"),
                 ((6, -64.0), (12, 48.0), ["DIFFERENCE"], booleans.NEGATIVE),
                 ((6, 64.0), (12, 48.0), ["DIFFERENCE"], None),
                 ((6, 64.0), (12, 120.0), ["UNION"], None),
                 ((6, 64.0), (12, 80.0), ["DIFFERENCE", "UNION"], None),
                 ((6, 64.0), (10, 64.000001), ["DIFFERENCE"], None),   # Faces changed, no operand size: no call.
                 ((6, 64.0), (10, 64.0000001), ["DIFFERENCE"], None),   # Not "grew": within rounding.
                 ((6, 64.0), (10, 63.9999999), ["UNION"], None)]
        for before, after, operations, fact in cases:
            with self.subTest(before=before, after=after, operations=operations):
                row = booleans.facts(self.reading(*before), self.reading(*after), operations)
                self.assertEqual(row.get("fact"), fact)
                self.assertEqual(row["faces"], [before[0], after[0]])
                self.assertNotIn("bounds", row, "Bounds only when they changed")

    def test_unchanged_is_judged_against_the_smaller_of_the_object_and_its_operands(self):
        """A review: a 4 cm bolt hole through a 40 m³ wall changed it by 0.00126 m³, under 0.01% of the wall, and
        read "volume unchanged" like a cutter wound inward. Against the cutter's 0.0025 m³ it is a real cut."""
        bolt = booleans.facts(self.reading(6, 40.0), self.reading(24, 39.99874), ["DIFFERENCE"], operand=0.0025)
        self.assertNotIn("fact", bolt)
        inward = booleans.facts(self.reading(6, 64.0), self.reading(10, 64.0000004), ["DIFFERENCE"], operand=24.0)
        self.assertEqual(inward["fact"], "volume unchanged (within 0.0024 m³)")
        large = booleans.facts(self.reading(6, 1.0), self.reading(7, 0.99995), ["DIFFERENCE"], operand=1000.0)
        self.assertEqual(large["fact"], "volume unchanged (within 0.0001 m³)", "The smaller volume bounds it")
        missed = booleans.facts(self.reading(6, 40.0), self.reading(6, 40.0), ["DIFFERENCE"], operand=0.0025)
        self.assertEqual(missed["fact"], "volume and faces unchanged (within 2.5e-07 m³)")

    def test_an_unevaluated_reading_claims_no_measure_or_fact(self):
        row = booleans.facts({"faces": 6, "why": booleans.NOT_EVALUATED}, {"faces": 6, "why": booleans.NOT_EVALUATED},
                             ["DIFFERENCE"], operand=8.0)
        self.assertEqual(row, {"faces": [6, 6], "volume": booleans.NOT_EVALUATED})

    def test_no_faces_left_and_the_side_without_a_volume(self):
        row = booleans.facts(self.reading(6, 64.0), {"faces": 0, "why": "no faces"}, ["DIFFERENCE"])
        self.assertEqual(row, {"faces": [6, 0], "volume_m3": [64.0, None], "volume": "after: no faces",
                               "bounds": [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], None], "fact": "no faces left"})

    def test_an_open_mesh_states_faces_and_bounds_unchanged_or_nothing(self):
        same = booleans.facts(self.reading(1), self.reading(1), ["DIFFERENCE"])
        self.assertEqual(same, {"faces": [1, 1], "volume": "open mesh", "fact": "faces and bounds unchanged"})
        cut = booleans.facts(self.reading(1), self.reading(2), ["DIFFERENCE"])
        self.assertEqual(cut, {"faces": [1, 2], "volume": "open mesh"})

    def test_volumes_keep_four_significant_digits_and_at_least_three_places(self):
        self.assertEqual(booleans._significant([12.0, 11.21468]), [12.0, 11.215])
        self.assertEqual(booleans._significant([2520.0, 2488.4]), [2520.0, 2488.4])
        self.assertEqual(booleans._significant([1.2345e-6, 1e-6]), [1.234e-6, 1e-6])
        self.assertEqual(booleans._significant([0.0, 0.0]), [0.0, 0.0])

    def test_a_both_sided_reason_reads_once(self):
        row = booleans.facts({"faces": 6, "why": "over 9 triangles"}, {"faces": 6, "why": "over 9 triangles"},
                             ["UNION"])
        self.assertEqual(row, {"faces": [6, 6], "volume": "over 9 triangles"})


class RowTests(unittest.TestCase):
    def test_rows_past_the_cap_are_counted(self):
        rows = booleans.Rows(clock=lambda: 0.0)
        for index in range(booleans.MAX_ROWS + 3):
            rows.add({"object": str(index)})
        self.assertEqual(rows.outcome(), {"booleans": [{"object": str(i)} for i in range(booleans.MAX_ROWS)],
                                          "booleans_more": 3})
        self.assertEqual(booleans.Rows(clock=lambda: 0.0).outcome(), {})

    def test_the_budget_closes_measuring_but_not_the_rows(self):
        readings = iter([0.0, booleans.BUDGET_SECONDS])
        rows = booleans.Rows(clock=lambda: next(readings))
        self.assertTrue(rows.open())
        rows.measured(lambda: None)
        self.assertFalse(rows.open())
        self.assertTrue(rows.room())


class Modifier(SimpleNamespace):
    def __init__(self, name, cutter, kind="BOOLEAN", **fields):
        super().__init__(**dict(dict(name=name, type=kind, object=cutter, collection=None, operand_type="OBJECT",
                                     operation="DIFFERENCE", solver="EXACT", show_viewport=True), **fields))


class Obj(SimpleNamespace):
    """A mesh object; ``evaluated_get`` returns itself, as Blender does, when the view layer does not evaluate it."""

    def __init__(self, name, data, modifiers=(), evaluated=None, **fields):
        super().__init__(**dict(dict(name=name, type="MESH", data=data, modifiers=list(modifiers),
                                     matrix_world=IDENTITY, mode="OBJECT", parent=None, parent_type="OBJECT",
                                     in_view_layer=True), **fields))
        data.shape_keys = None
        self._evaluated = evaluated

    def as_pointer(self):
        return id(self)

    def evaluated_get(self, depsgraph):
        if not self.in_view_layer:
            return self
        return SimpleNamespace(matrix_world=self.matrix_world, data=self._evaluated or self.data,
                               as_pointer=lambda: id(self) + 1)


class FakeOps:
    """``bpy.ops`` as far as the hook reads it: the factory global, and modifier_apply as a stand-in."""

    def __init__(self, context, results):
        self.context, self.results, self.calls = context, results, []
        self._op_create_function = self.create

    def create(self, module, function):
        def operator(*args, **kwargs):
            self.calls.append((module, function, args, kwargs))
            if kwargs.get("raise_"):
                raise RuntimeError("poll failed")
            for obj in (self.context.selected_editable_objects if kwargs.get("use_selected_objects")
                        else [self.context.object or self.context.active_object]):
                named = [md for md in obj.modifiers if md.name == kwargs.get("modifier")]
                if named:
                    obj.modifiers.remove(named[0])
                    obj.data = self.results.get(obj.name, obj.data)
            return {"FINISHED"}
        operator.poll = lambda: True
        return operator


def stand_in(objects, results, active=None, override=None, selected=()):
    context = SimpleNamespace(object=override or active, active_object=active, selected_editable_objects=list(selected),
                              evaluated_depsgraph_get=lambda: "depsgraph")
    ops = FakeOps(context, results)
    return SimpleNamespace(context=context, ops=ops), ops


@unittest.skipUnless(HAS_NUMPY, "NumPy is not installed")
class AppliedTests(unittest.TestCase):
    def setUp(self):
        self.cutter = Obj("Hole", cube())
        self.block = Obj("Block", cube((0, 0, 0), (4, 4, 4)), [Modifier("Cut", self.cutter)])
        self.cut = cube((0, 0, 0), (4, 4, 1))   # What the stand-in operator leaves: a quarter of the block.

    def test_only_modifier_apply_is_observed_and_bpy_ops_is_put_back_however_the_script_ends(self):
        for ending in (None, ValueError, SystemExit, KeyboardInterrupt):
            bpy, ops = stand_in([self.block], {}, active=self.block)
            real = ops._op_create_function
            with self.subTest(ending=ending):
                try:
                    with booleans.Applied(bpy):
                        self.assertIsNot(ops._op_create_function, real)
                        other = ops._op_create_function("object", "select_all")
                        self.assertNotIsInstance(other, booleans._Observed)
                        observed = ops._op_create_function("object", "modifier_apply")
                        self.assertIsInstance(observed, booleans._Observed)
                        self.assertTrue(observed.poll(), "Everything else is the operator's own")
                        if ending is not None:
                            raise ending()
                except (ValueError, SystemExit, KeyboardInterrupt):
                    pass
                self.assertIs(ops._op_create_function, real)

    def test_a_callable_kept_past_the_script_is_the_operator_alone(self):
        bpy, ops = stand_in([self.block], {"Block": self.cut}, active=self.block)
        with booleans.Applied(bpy) as applied:
            kept = ops._op_create_function("object", "modifier_apply")
        kept(modifier="Cut")
        self.assertEqual((applied.outcome(), len(self.block.modifiers)), ({}, 0))

    def test_a_stand_in_bpy_without_ops_runs_unobserved(self):
        with booleans.Applied(SimpleNamespace(context=None)) as applied:
            pass
        self.assertEqual(applied.outcome(), {})

    def apply(self, bpy, ops, **kwargs):
        with booleans.Applied(bpy) as applied:
            ops._op_create_function("object", "modifier_apply")(**kwargs)
        return applied.outcome()

    def test_an_applied_boolean_is_measured_before_and_after(self):
        bpy, ops = stand_in([self.block], {"Block": self.cut}, active=self.block)
        out = self.apply(bpy, ops, modifier="Cut")
        self.assertEqual(out, {"booleans": [{
            "object": "Block", "with": "Hole", "operation": "DIFFERENCE", "solver": "EXACT", "applied": True,
            "faces": [6, 6], "volume_m3": [64.0, 16.0],
            "bounds": [[[0.0, 0.0, 0.0], [4.0, 4.0, 4.0]], [[0.0, 0.0, 0.0], [4.0, 4.0, 1.0]]],
            "fact": "kept 25% of its volume"}]})

    def test_the_context_object_comes_before_the_active_one(self):
        other = Obj("Other", cube(), [Modifier("Cut", self.cutter)])
        bpy, ops = stand_in([self.block, other], {"Block": self.cut}, active=other, override=self.block)
        self.assertEqual(self.apply(bpy, ops, modifier="Cut")["booleans"][0]["object"], "Block")

    def test_selected_objects_each_get_a_row(self):
        other = Obj("Other", cube(), [Modifier("Cut", self.cutter)])
        bpy, ops = stand_in([self.block, other], {"Block": self.cut}, active=self.block, selected=[self.block, other])
        rows = self.apply(bpy, ops, modifier="Cut", use_selected_objects=True)["booleans"]
        self.assertEqual([(row["object"], row.get("fact")) for row in rows],
                         [("Block", "kept 25% of its volume"),
                          ("Other", "volume and faces unchanged (within 0.0008 m³)")])

    def test_a_call_that_applied_no_boolean_says_so_without_measures(self):
        bpy, ops = stand_in([self.block], {}, active=self.block)
        self.assertEqual(self.apply(bpy, ops)["booleans"], [{   # modifier omitted: Blender's exec applies nothing.
            "object": "Block", "with": "Hole", "operation": "DIFFERENCE", "solver": "EXACT", "applied": False}])
        self.assertEqual(self.apply(bpy, ops, modifier="Missing"), {}, "Not a Boolean of this object: no row")

    def test_an_unevaluated_target_keeps_its_face_counts_and_claims_no_world_measure(self):
        self.block.in_view_layer = False   # Disabled in viewports: its matrix may be stale, and the operator cut in it.
        bpy, ops = stand_in([self.block], {"Block": self.cut}, active=self.block)
        self.assertEqual(self.apply(bpy, ops, modifier="Cut")["booleans"], [{
            "object": "Block", "with": "Hole", "operation": "DIFFERENCE", "solver": "EXACT", "applied": True,
            "faces": [6, 6], "volume": booleans.NOT_EVALUATED}])

    def test_the_operands_volume_bounds_unchanged(self):
        hole = Obj("Hole", cube((1, 1, -1), (1.02, 1.02, 5)))   # A thin cutter: 0.0024 m³.
        self.block.modifiers = [Modifier("Cut", hole)]
        nicked = cube((0, 0, 0), (4, 4, 4))
        bpy, ops = stand_in([self.block], {"Block": nicked}, active=self.block)
        with patch.object(booleans, "measure", wraps=booleans.measure) as measured:
            out = self.apply(bpy, ops, modifier="Cut")
        self.assertEqual(measured.call_count, 3, "Before, the operand, after")
        self.assertEqual(out["booleans"][0]["fact"], "volume and faces unchanged (within 2.4e-07 m³)")

    def test_a_raising_call_records_its_row_and_still_raises(self):
        bpy, ops = stand_in([self.block], {}, active=self.block)
        with booleans.Applied(bpy) as applied:
            with self.assertRaisesRegex(RuntimeError, "poll failed"):
                ops._op_create_function("object", "modifier_apply")(modifier="Cut", raise_=True)
        self.assertIs(applied.outcome()["booleans"][0]["applied"], False)

    def test_a_measuring_error_drops_the_row_never_the_call(self):
        bpy, ops = stand_in([self.block], {"Block": self.cut}, active=self.block)
        with patch.object(booleans, "measure", side_effect=TypeError("unreadable")):
            out = self.apply(bpy, ops, modifier="Cut")
        self.assertEqual(out, {"booleans_more": 1}, "The apply happened; its measures did not")
        self.assertEqual(len(self.block.modifiers), 0)

    def test_past_the_row_cap_applies_are_counted_without_measuring(self):
        bpy, ops = stand_in([self.block], {"Block": self.cut}, active=self.block)
        with booleans.Applied(bpy) as applied:
            applied.rows.rows = [{}] * booleans.MAX_ROWS
            with patch.object(booleans, "measure", side_effect=AssertionError("measured past the cap")):
                ops._op_create_function("object", "modifier_apply")(modifier="Cut")
        self.assertEqual(applied.rows.more, 1)


@unittest.skipUnless(HAS_NUMPY, "NumPy is not installed")
class LiveTests(unittest.TestCase):
    def window(self, *objects):
        scene = SimpleNamespace(objects=SceneObjects(objects))
        return SimpleNamespace(scene=scene, view_layer=SimpleNamespace(depsgraph="depsgraph"))

    def test_an_all_boolean_stack_is_base_against_evaluated(self):
        cutter = Obj("Hole", cube())
        block = Obj("Block", cube((0, 0, 0), (4, 4, 4)), [Modifier("Cut", cutter)],
                    evaluated=cube((0, 0, 0), (4, 4, 3)))
        result = booleans.live(self.window(block, cutter), {"success": True}, ["Block", "Hole"])
        self.assertEqual(result["booleans"], [{"object": "Block", "with": "Hole", "operation": "DIFFERENCE",
                                               "solver": "EXACT", "live": True, "faces": [6, 6],
                                               "volume_m3": [64.0, 48.0],
                                               "bounds": [[[0.0, 0.0, 0.0], [4.0, 4.0, 4.0]],
                                                          [[0.0, 0.0, 0.0], [4.0, 4.0, 3.0]]]}])

    def test_another_enabled_modifier_shape_keys_or_edit_mode_leave_it_out(self):
        cutter = Obj("Hole", cube())
        shaped = Obj("Shaped", cube(), [Modifier("Cut", cutter), Modifier("Bevel", None, "BEVEL")])
        hidden = Obj("Hidden", cube(), [Modifier("Cut", cutter), Modifier("Bevel", None, "BEVEL")])
        hidden.modifiers[1].show_viewport = False
        keyed = Obj("Keyed", cube(), [Modifier("Cut", cutter)])
        editing = Obj("Editing", cube(), [Modifier("Cut", cutter)])
        keyed.data.shape_keys, editing.mode = object(), "EDIT"
        result = booleans.live(self.window(shaped, hidden, keyed, editing, cutter), {"success": True},
                               ["Shaped", "Hidden", "Keyed", "Editing"])
        self.assertEqual([row["object"] for row in result["booleans"]], ["Hidden"])

    def test_an_unevaluated_stack_is_named_without_measures(self):
        """A review: a live cut of half a block, disabled in viewports, read "volume and faces unchanged" from its
        unevaluated base mesh while a render cut half of it."""
        cutter = Obj("Hole", cube())
        block = Obj("Block", cube((0, 0, 0), (4, 4, 4)), [Modifier("Cut", cutter)], in_view_layer=False)
        row = booleans.live(self.window(block, cutter), {"success": True}, ["Block"])["booleans"][0]
        self.assertEqual(row, {"object": "Block", "with": "Hole", "operation": "DIFFERENCE", "solver": "EXACT",
                               "live": True, "volume": booleans.NOT_EVALUATED})

    def test_operand_volumes_are_summed_or_unknown(self):
        def boolean(**fields):
            return Modifier("Cut", None, **fields)
        closed, open_ = Obj("Closed", cube()), Obj("Open", cube(faces=CUBE_FACES[:5]))
        hidden = Obj("Hidden", cube(), in_view_layer=False)
        many = SimpleNamespace(name="Many", all_objects=[Obj("C%d" % i, cube()) for i in range(
            booleans.MAX_OPERAND_OBJECTS + 1)])
        pair = SimpleNamespace(name="Pair", all_objects=[closed, Obj("Lamp", cube(), type="LIGHT"), Obj("B", cube())])
        cases = [([boolean(object=closed)], 8.0), ([boolean(object=closed), boolean(object=closed)], 16.0),
                 ([boolean(object=open_)], None), ([boolean(object=hidden)], None), ([boolean()], None),
                 ([boolean(operand_type="COLLECTION", collection=pair)], 16.0),
                 ([boolean(operand_type="COLLECTION", collection=many)], None)]
        for modifiers, volume in cases:
            with self.subTest(volume=volume):
                found = booleans.operand_volume(modifiers, "depsgraph")
                self.assertEqual(found if found is None else round(found, 9), volume)

    def test_a_target_whose_operand_changed_is_read_and_stacks_are_capped(self):
        cutters = [Obj("Hole %d" % i, cube()) for i in range(booleans.MAX_STACK + 2)]
        block = Obj("Block", cube(), [Modifier("Cut %d" % i, cutter) for i, cutter in enumerate(cutters)])
        result = booleans.live(self.window(block, *cutters), {"success": True}, ["Hole 1"])
        row = result["booleans"][0]
        self.assertEqual((row["object"], len(row["stack"]), row["stack_more"]), ("Block", booleans.MAX_STACK, 2))
        self.assertEqual(row["stack"][0], {"with": "Hole 0", "operation": "DIFFERENCE", "solver": "EXACT"})

    def test_a_collection_operand_holding_a_changed_object_is_read(self):
        cutter = Obj("Hole", cube())
        collection = SimpleNamespace(name="Cutters", all_objects=[cutter])
        block = Obj("Block", cube(), [Modifier("Cut", None, operand_type="COLLECTION")])
        block.modifiers[0].collection = collection
        row = booleans.live(self.window(block, cutter), {"success": True}, ["Hole"])["booleans"][0]
        self.assertEqual((row["with_collection"], "with" in row), ("Cutters", False))

    def test_live_rows_follow_the_applied_rows_within_one_cap(self):
        cutter = Obj("Hole", cube())
        blocks = [Obj("Block %d" % i, cube(), [Modifier("Cut", cutter)]) for i in range(3)]
        result = {"success": True, "booleans": [{"object": "applied"}] * (booleans.MAX_ROWS - 1)}
        result = booleans.live(self.window(*blocks, cutter), result, [b.name for b in blocks])
        self.assertEqual((len(result["booleans"]), result["booleans_more"]), (booleans.MAX_ROWS, 2))
        self.assertEqual(result["booleans"][-1]["object"], "Block 0")

    def test_an_unreadable_scene_or_no_names_leave_the_result_as_it_was(self):
        result = {"success": True, "booleans": [{"object": "applied"}]}
        self.assertEqual(booleans.live(SimpleNamespace(), dict(result), ["Block"]), result)
        self.assertEqual(booleans.live(self.window(), dict(result), []), result)


class SceneObjects(list):
    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class ResultTests(unittest.TestCase):
    ROWS = [{"object": "Block", "with": "Hole", "operation": "DIFFERENCE", "solver": "EXACT", "applied": True,
             "faces": [6, 12], "volume_m3": [64.0, 48.0]}]

    def test_the_models_copy_keeps_the_rows(self):
        raw = {"success": True, "result": None, "booleans": self.ROWS, "booleans_more": 2, "undoable": False,
               "changes": {"added": [], "modified": [], "removed": [], "scope": "x"}}
        for follows in (False, True):
            copy = model_results.model_result("angelo_execute_python", raw, observation_follows=follows)
            self.assertEqual((copy["booleans"], copy["booleans_more"]), (self.ROWS, 2))

    def test_attach_reads_live_stacks_of_a_completed_script_only(self):
        window = SimpleNamespace(scene=SimpleNamespace(objects=SceneObjects()))
        with patch.object(booleans, "live", side_effect=lambda w, result, names, **_: dict(result, seen=names)) as live, \
                patch.object(script_after, "notes"):
            script_after.attach(window, {"success": True, "changes": {"added": [{"name": "A"}],
                                                                      "modified": [{"name": "B"}, {"name": "A"}]}})
            self.assertEqual(live.call_args.args[2], ["A", "B"])
            live.reset_mock()
            script_after.attach(window, {"success": False, "error": "boom", "changes": {"added": [{"name": "A"}]}})
            script_after.attach(window, {"success": True, "error": "rolled back", "changes": {}})
            live.assert_not_called()


if __name__ == "__main__":
    unittest.main()
