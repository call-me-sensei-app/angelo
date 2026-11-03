# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Game physics without Blender: shape fitting, axes, transforms, the OMI dictionaries in both capsule forms, Godot's
name suffixes, the findings, and the contracts that keep the settings writable by the agent
(doc/angelo/game_physics.rst). The export, import and Godot itself run in test_blender_game_physics and
test_blender_godot_physics_e2e.
"""

import ast
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDONS = ROOT / "scripts/addons_core"
PHYSICS = ADDONS / "io_scene_gltf2/angelo_physics"
sys.path.insert(0, str(PHYSICS))
sys.path.insert(0, str(ADDONS))
import shapes  # noqa: E402
import godot_suffixes  # noqa: E402
from angelo_agent import action_properties, game_physics, rna_rules  # noqa: E402


def close(a, b, tolerance=1e-6):
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def rotate(rotation, vector):
    matrix = shapes.quaternion_matrix(rotation)
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))


class Fitting(unittest.TestCase):
    def test_box_is_the_bounds(self):
        fit = shapes.fit_bounds("BOX", (-1, 0, 2), (3, 1, 2.5))
        self.assertTrue(close(fit.center, (1, 0.5, 2.25)) and close(fit.size, (4, 1, 0.5)))

    def test_sphere_radius_is_half_the_longest_side(self):
        self.assertAlmostEqual(shapes.fit_bounds("SPHERE", (0, 0, 0), (1, 4, 2)).radius, 2)

    def test_capsule_runs_along_the_longest_side(self):
        fit = shapes.fit_bounds("CAPSULE", (0, 0, 0), (1, 5, 2))
        self.assertEqual(fit.axis, 1)
        self.assertAlmostEqual(fit.radius, 1)   # Half the larger of the other two sides.
        self.assertAlmostEqual(fit.height, 5)

    def test_a_tie_goes_to_z_then_y(self):
        self.assertEqual(shapes.fit_bounds("CYLINDER", (0, 0, 0), (2, 2, 2)).axis, 2)
        self.assertEqual(shapes.fit_bounds("CYLINDER", (0, 0, 0), (2, 2, 1)).axis, 1)

    def test_a_capsule_is_at_least_its_hemispheres_and_a_cylinder_is_not_clamped(self):
        self.assertAlmostEqual(shapes.fit_bounds("CAPSULE", (0, 0, 0), (4, 4, 1), axis="Z").height, 4)
        self.assertAlmostEqual(shapes.fit_bounds("CYLINDER", (0, 0, 0), (4, 4, 1), axis="Z").height, 1)

    def test_manual_axis_auto_is_z(self):
        fit = shapes.manual("CYLINDER", (1, 2, 3), (1, 1, 1), 0.5, 3, "AUTO")
        self.assertEqual((fit.axis, fit.center, fit.height), (2, (1, 2, 3), 3))


class Axes(unittest.TestCase):
    def test_y_up_conversion(self):
        fit = shapes.to_gltf_fit(shapes.Fit("BOX", (1, 2, 3), size=(4, 5, 6)), True)
        self.assertEqual((fit.center, fit.size), ((1, 3, -2), (4, 6, 5)))
        self.assertEqual([shapes.to_gltf_axis(index, True) for index in range(3)], [0, 2, 1])
        self.assertEqual(shapes.from_gltf_vector(shapes.to_gltf_vector((1, 2, 3), True), True), (1, 2, 3))

    def test_z_up_export_keeps_axes(self):
        fit = shapes.to_gltf_fit(shapes.Fit("CAPSULE", (1, 2, 3), axis=2), False)
        self.assertEqual((fit.center, fit.axis), ((1, 2, 3), 2))

    def test_axis_rotations_lay_plus_y_along_the_axis(self):
        for axis, rotation in shapes.AXIS_ROTATIONS.items():
            expected = tuple(1.0 if index == axis else 0.0 for index in range(3))
            self.assertTrue(close([abs(value) for value in rotate(rotation, (0, 1, 0))], expected), axis)


class Transforms(unittest.TestCase):
    def test_decompose_inverts_compose(self):
        rotation = (0.0, math.sin(0.4), 0.0, math.cos(0.4))
        translation, back, scale = shapes.decompose(shapes.compose((1, 2, 3), rotation, (2, 3, 4)))
        self.assertTrue(close(translation, (1, 2, 3)) and close(back, rotation) and close(scale, (2, 3, 4)))

    def test_mirrored_matrix_flips_x(self):
        self.assertTrue(close(shapes.decompose(shapes.compose(None, None, (-1, 1, 1)))[2], (-1, 1, 1)))

    def test_placement_offsets_to_the_centre(self):
        translation, rotation, fit = shapes.placement(shapes.Fit("BOX", (0, 0.5, 0), size=(1, 1, 1)))
        self.assertTrue(close(translation, (0, 0.5, 0)) and close(rotation, shapes.IDENTITY_ROTATION))

    def test_placement_bakes_scale_and_keeps_rotation(self):
        quarter = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))
        relative = shapes.compose((5, 0, 0), quarter, (2, 3, 4))
        translation, rotation, fit = shapes.placement(shapes.Fit("BOX", (1, 0, 0), size=(1, 1, 1)), relative)
        self.assertTrue(close(fit.size, (2, 3, 4)))
        self.assertTrue(close(translation, (5, 2, 0)))   # (1, 0, 0) scaled by 2, then turned 90 degrees about Z.
        self.assertTrue(close(rotation, quarter))

    def test_capsule_bakes_its_own_axis_scale_into_height(self):
        relative = shapes.compose(None, None, (2, 3, 2))
        _t, rotation, fit = shapes.placement(shapes.Fit("CAPSULE", radius=0.5, height=2, axis=1), relative)
        self.assertAlmostEqual(fit.height, 6)
        self.assertAlmostEqual(fit.radius, 1)
        self.assertTrue(close(rotation, shapes.IDENTITY_ROTATION))


class OmiDictionaries(unittest.TestCase):
    def test_capsule_is_written_as_godot_reads_it(self):
        entry = shapes.omi_shape(shapes.Fit("CAPSULE", radius=0.4, height=3, axis=1))
        self.assertEqual(entry, {"type": "capsule", "capsule": {"radius": 0.4, "radiusTop": 0.4, "radiusBottom": 0.4,
                                                                "height": 3.0}})

    def test_mesh_shapes_point_at_their_mesh(self):
        self.assertEqual(shapes.omi_shape(kind="TRIMESH", mesh=7), {"type": "trimesh", "trimesh": {"mesh": 7}})

    def test_written_shapes_read_back(self):
        for fit in (shapes.Fit("BOX", size=(1, 2, 3)), shapes.Fit("SPHERE", radius=0.7),
                    shapes.Fit("CAPSULE", radius=0.3, height=2.5, axis=1),
                    shapes.Fit("CYLINDER", radius=0.3, height=0.2, axis=1)):
            kind, size, radius, height, mesh = shapes.read_shape(shapes.omi_shape(fit))
            self.assertEqual(kind, fit.kind)
            if kind == "BOX":
                self.assertTrue(close(size, fit.size))
            else:
                self.assertAlmostEqual(radius, fit.radius)
            if kind in ("CAPSULE", "CYLINDER"):
                self.assertAlmostEqual(height, fit.height)

    def test_2025_capsule_height_is_between_the_hemisphere_centres(self):
        parsed = shapes.read_shape({"type": "capsule", "capsule": {"height": 1.0, "radiusTop": 0.5,
                                                                   "radiusBottom": 0.5}})
        self.assertEqual(parsed[:4], ("CAPSULE", (1.0, 1.0, 1.0), 0.5, 2.0))
        self.assertEqual(shapes.read_shape({"type": "capsule"})[3], 2.0)   # The specification's defaults.

    def test_omi_collider_hull_and_unknown_types(self):
        self.assertEqual(shapes.read_shape({"type": "hull", "mesh": 2})[0::4], ("CONVEX", 2))
        self.assertIsNone(shapes.read_shape({"type": "torus"}))
        self.assertIsNone(shapes.read_shape("box"))


class GodotSuffixes(unittest.TestCase):
    def test_suffix_table(self):
        cases = {("NONE", "TRIMESH", True, False): ("-col", None),
                 ("STATIC", "CONVEX", True, False): ("-convcol", None),
                 ("NONE", "TRIMESH", True, True): ("-colonly", None),
                 ("NONE", "BOX", True, True): ("-convcolonly", "suffix_hull"),
                 ("RIGID", "CONVEX", True, False): ("-rigid", None),
                 ("RIGID", "BOX", True, False): ("-rigid", "suffix_hull"),
                 ("KINEMATIC", "BOX", True, False): (None, "suffix_body"),
                 ("NONE", "BOX", False, False): (None, "suffix_empty"),
                 ("NONE", "NONE", True, False): (None, None)}
        for arguments, expected in cases.items():
            self.assertEqual(godot_suffixes.godot_suffix(*arguments), expected, arguments)
        self.assertTrue(set(godot_suffixes.SUFFIX_NOTES) >= {"suffix_empty", "suffix_body", "suffix_hull",
                                                              "suffix_mesh"})


def record(name, kind="MESH", parent=None, body="NONE", shape="NONE", **extra):
    row = {"name": name, "type": kind, "parent": parent, "body": body, "shape": shape,
           "suffix": game_physics.name_suffix(name), "animated": False, "scale": (1.0, 1.0, 1.0),
           "has_geometry": kind != "EMPTY", "collision_only": False, "fit": "AUTO", "size": (1.0, 1.0, 1.0),
           "radius": 0.5, "height": 2.0, "offset": (0.0, 0.0, 0.0), "extents": (1.0, 1.0, 1.0) if kind != "EMPTY"
           else None}
    row.update(extra)
    return row


def codes(records, physics_format="OMI"):
    return {(item["code"], item["subject"]) for item in game_physics.findings(records, physics_format)}


class Findings(unittest.TestCase):
    def test_a_well_built_scene_has_none(self):
        rows = [record("Crate", body="RIGID", shape="BOX"), record("Floor", shape="TRIMESH"),
                record("Cart", "EMPTY", body="KINEMATIC"), record("Wheel", parent="Cart", shape="CYLINDER"),
                record("Zone", "EMPTY", body="TRIGGER", shape="SPHERE", fit="MANUAL"),
                record("Hull", parent="Crate", shape="CONVEX", collision_only=True)]
        self.assertEqual(codes(rows), set())

    def test_an_animated_shape_inside_its_body(self):
        rows = [record("Body", "EMPTY", body="RIGID"), record("Arm", "EMPTY", parent="Body", animated=True),
                record("Hand", parent="Arm", shape="BOX")]
        self.assertEqual(codes(rows), {("shape_indirect", "Hand")})
        rows[1]["animated"] = False   # Still: the exporter moves it below the body.
        self.assertEqual(codes(rows), set())

    def test_an_animated_empty_directly_below_its_body_keeps_its_shape(self):
        rows = [record("Body", "EMPTY", body="RIGID"),
                record("Paddle", "EMPTY", parent="Body", shape="BOX", fit="MANUAL", animated=True)]
        self.assertEqual(codes(rows), set())

    def test_each_mistake(self):
        rows = [record("Old-colonly.001", "EMPTY"), record("Barrel", body="RIGID", shape="TRIMESH"),
                record("Zone", "EMPTY", body="TRIGGER"), record("Inside", parent="Zone", shape="TRIMESH"),
                record("Ghost", "EMPTY", shape="CONVEX"), record("Sheet", shape="BOX", extents=(2, 2, 0)),
                record("Floaty", body="RIGID"), record("Stretched", body="STATIC", shape="BOX", scale=(1, 2, 1)),
                record("Big", body="RIGID", shape="BOX", scale=(2, 2, 2)), record("Twice-col", shape="BOX"),
                record("Shell", shape="NONE", body="STATIC", collision_only=True),
                record("Lamp", "LIGHT", shape="BOX")]
        found = codes(rows)
        for expected in [("colonly_empty", "Old-colonly.001"), ("trimesh_moving", "Barrel"),
                         ("trimesh_trigger", "Inside"), ("no_mesh", "Ghost"), ("flat_shape", "Sheet"),
                         ("body_without_shape", "Floaty"), ("scaled_body", "Stretched"), ("scaled_body", "Big"),
                         ("suffix_and_physics", "Twice-col"), ("collision_only_without_shape", "Shell"),
                         ("not_geometry", "Lamp")]:
            self.assertIn(expected, found)

    def test_uniform_scale_on_a_static_body_is_fine(self):
        self.assertEqual(codes([record("Rock", body="STATIC", shape="BOX", scale=(3, 3, 3))]), set())

    def test_suffix_format(self):
        rows = [record("Door", body="KINEMATIC", shape="BOX", suffix_note="suffix_body"),
                record("Crate", body="RIGID", shape="BOX", suffix_note="suffix_hull"),
                record("Floor", shape="TRIMESH", suffix_note=None)]
        self.assertEqual(codes(rows, "GODOT_SUFFIXES"), {("suffix_unsupported", "Door"),
                                                         ("suffix_approximate", "Crate")})

    def test_name_suffix_follows_godot(self):
        self.assertEqual(game_physics.name_suffix("Rock-convcolonly.002"), "-convcolonly")
        self.assertEqual(game_physics.name_suffix("Rock_-col_2"), "-col")
        self.assertEqual(game_physics.name_suffix("Wall$rigid"), "-rigid")
        for plain in ("PillCollider", "Crate-collision", "Column", None):
            self.assertIsNone(game_physics.name_suffix(plain))

    def test_every_template_is_filled_by_its_finding(self):
        rows = [record("A", body="RIGID"), record("B", parent="A", shape="TRIMESH")]
        for finding in game_physics.findings(rows):
            english = game_physics.english(finding)
            self.assertNotIn("{", english["message"] + english["fix"])


def _props_source():
    return ast.parse((PHYSICS / "props.py").read_text(encoding="utf-8"))


class Contracts(unittest.TestCase):
    def test_settings_run_no_python_and_cannot_be_keyed(self):
        for node in ast.walk(_props_source()):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "").endswith("Property"):
                keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                self.assertFalse({"update", "get", "set", "poll"} & set(keywords), ast.dump(node)[:80])
                if node.func.attr != "PointerProperty":   # **SETTING: not keyable, Angelo's message context.
                    self.assertIn(None, keywords)
                    self.assertEqual(ast.unparse(keywords[None]), "SETTING")
        source = (PHYSICS / "props.py").read_text(encoding="utf-8")
        self.assertIn('SETTING = {"options": set(), "translation_context": ANGELO}', source)

    def test_agent_paths_are_the_settings(self):
        tree = _props_source()
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "AngeloPhysicsSettings")
        names = {item.target.id for item in cls.body if isinstance(item, ast.AnnAssign)}
        self.assertEqual({path.split(".", 1)[1] for path in rna_rules.GAME_PHYSICS}, names)
        documented = {path for path in action_properties.PROPERTY_PATHS if path.startswith("angelo_physics.")}
        self.assertEqual(documented, set(rna_rules.GAME_PHYSICS))
        self.assertTrue(set(rna_rules.GAME_PHYSICS) <= action_properties.NOT_ANIMATABLE)
        self.assertEqual(action_properties.PROPERTY_PATHS["angelo_physics.body"][3], shapes.BODIES)
        self.assertEqual(action_properties.PROPERTY_PATHS["angelo_physics.shape"][3], shapes.SHAPES)
        self.assertEqual(action_properties.PROPERTY_PATHS["angelo_physics.axis"][3], shapes.AXES)

    def test_enum_numbers_are_fixed(self):
        source = (PHYSICS / "props.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Assign) and node.targets[0].id.endswith("_ITEMS"):
                items = ast.literal_eval(node.value)
                self.assertEqual([item[3] for item in items], list(range(len(items))), node.targets[0].id)

    def test_every_gltf_export_and_import_carries_the_extension(self):
        source = (ADDONS / "io_scene_gltf2/__init__.py").read_text(encoding="utf-8")
        self.assertIn("user_extensions = [angelo_physics.export_extension()]", source)
        self.assertIn("user_extensions = [angelo_physics.import_extension()]", source)
        self.assertIn("angelo_physics.register()", source)


if __name__ == "__main__":
    unittest.main()
