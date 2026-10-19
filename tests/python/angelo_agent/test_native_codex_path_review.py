# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for the Codex path: LOCAL dimensions, apply_scale, plan bindings, the status line and batch note.

Each FINDING test asserted the correct behaviour and failed before its fix; it now pins the fix. Where the fix chose a
different correct behaviour than the reproduction asserted, the test says so: a mirrored scale is baked as
Blender's Apply Scale bakes it instead of refused, and the batch note states the counts copies() makes.
A PIN test pins behaviour verified as sound.

Blender references (this tree): ``Object.dimensions`` is ``BKE_object_dimensions_eval_cached_get``
(source/blender/makesrna/intern/rna_object.cc:1217), the evaluated bounds times ``math::to_scale`` of the world
matrix, the column lengths (source/blender/blenkernel/intern/object.cc:3754, blenlib/BLI_math_matrix.hh:1294).
The evaluated mesh runs shape keys and a PARSKEL parent's deform as virtual modifiers even when
``obj.modifiers`` is empty (source/blender/blenkernel/intern/modifier.cc:620-660). ``Matrix.decompose`` negates
the rotation and every scale axis of a negative-determinant matrix (blenlib/intern/math_matrix_c.cc:2019-2029),
which ``action_object_management.apply_transform`` feeds to ``Matrix.LocRotScale``; Blender's own Apply Scale bakes
``BKE_object_scale_to_mat3`` and leaves the rotation channels alone (editors/object/object_transform.cc:815,
1058-1061).
"""

import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_recipes import expand_recipe, scale_facts  # noqa: E402
from angelo_agent.plan_bindings import OwnCommits  # noqa: E402
from angelo_agent.plan_progress import SEPARATOR, split, summary_title, thinking_status  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402
from angelo_agent.transform_bake import bake  # noqa: E402
from angelo_agent.validation_reader import ValidationReader  # noqa: E402


# --------------------------------------------------------------------------------------------------- fakes

CUBE = [(-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1), (1, -1, -1), (1, -1, 1), (1, 1, -1), (1, 1, 1)]
FACES = [(0, 2, 6, 4), (1, 5, 7, 3), (0, 4, 5, 1), (2, 3, 7, 6), (0, 1, 3, 2), (4, 6, 7, 5)]


class Collection(list):
    def get(self, name):
        return next((value for value in self if value.name == name), None)


def corners(low, high):
    return [[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]


def rows(matrix):
    return [[float(value) for value in row] for row in np.asarray(matrix, dtype=float)]


def mesh_object(world=np.eye(4), bounds=((-1, -1, -1), (1, 1, 1)), modifiers=(), **fields):
    """A base cube mesh (the base data) whose cached evaluated bounds (``bound_box``) may differ, as Blender's do."""
    edges = sorted({tuple(sorted((a, b))) for face in FACES for a, b in zip(face, face[1:] + face[:1])})
    data = NS(vertices=[NS(co=list(co), groups=[]) for co in CUBE], edges=[NS(vertices=e) for e in edges],
              polygons=[NS(vertices=f, material_index=0) for f in FACES], loops=list(range(24)),
              uv_layers=Collection(), shape_keys=None)
    obj = NS(type="MESH", name="Review cube", mode="OBJECT", data=data, matrix_world=rows(world),
             bound_box=corners(*bounds), modifiers=Collection(modifiers), constraints=[], parent=None,
             parent_type="OBJECT", animation_data=None, location=[0, 0, 0], rotation_euler=[0, 0, 0],
             scale=[1, 1, 1], material_slots=[])
    for key, value in fields.items():
        setattr(obj, key, value)
    return obj


def blender_dimensions(obj):
    """``Object.dimensions``: evaluated bounds (``bound_box``) times the world matrix's column lengths."""
    box = obj.bound_box
    extent = [max(c[i] for c in box) - min(c[i] for c in box) for i in range(3)]
    world = np.asarray(obj.matrix_world, dtype=float)
    return [float(np.linalg.norm(world[:3, axis])) * extent[axis] for axis in range(3)]


def rotation_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


def scale_matrix(x, y, z):
    return np.diag([x, y, z, 1.0])


HANDLE, REVISION = "scn:" + "a" * 32 + ":7", "b" * 64
BEVEL = {"width": 0.125, "segments": 3, "apply_scale": True}


def recipe_object(**changes):
    """An unrotated object whose world matrix is its parent's times its basis (the scale times the delta scale)."""
    fields = dict(scale=(2, 0.5, 1.5), type="MESH", children=(), constraints=(), animation_data=None,
                  delta_location=(0, 0, 0), delta_rotation_euler=(0, 0, 0), delta_scale=(1, 1, 1),
                  delta_rotation_quaternion=(1, 0, 0, 0), modifiers=(), parent=None, rotation_euler=(0, 0, 0))
    fields.update(changes)
    basis = scale_matrix(*[a * b for a, b in zip(fields["scale"], fields["delta_scale"])])
    parent = fields["parent"]
    world = (np.asarray(parent.matrix_world, dtype=float) if parent is not None else np.eye(4)) @ basis
    fields.setdefault("matrix_basis", rows(basis))
    fields.setdefault("matrix_world", rows(world))
    return NS(**fields)


def animation(paths=(), drivers=(), tracks=()):
    bag = NS(fcurves=[NS(data_path=path) for path in paths])
    action = NS(layers=[NS(strips=[NS(channelbag=lambda slot: bag)])])
    return NS(action=action, action_slot=object(), drivers=[NS(data_path=path) for path in drivers],
              nla_tracks=list(tracks))


def expand_live(obj):
    """What ``action_recipes.execute`` does for apply_scale: facts read from the object, then the expansion."""
    observation = {"target": HANDLE, "status": "pass", "revision": REVISION, "scale_facts": scale_facts(obj)}
    return expand_recipe("bevel-and-shade", HANDLE, dict(BEVEL), observation)


# ------------------------------------------------------------------------------- 1. LOCAL dimensions

class LocalDimensionsTests(unittest.TestCase):
    def local(self, obj):
        return ValidationReader({"targets": {"$o": obj}}).dimensions(obj, "LOCAL")

    def test_shape_keyed_mesh_without_modifiers_reads_the_base_mesh(self):
        """FINDING (fixed): a LOCAL check is documented as Blender's ``Object.dimensions``, but a mesh with
        shape keys and no ``obj.modifiers`` takes the base-mesh path (validation_reader.py:224-243), while Blender's
        dimensions come from the evaluated bounds the shape-key virtual modifier deformed (modifier.cc:647-657).
        Here Key 1 (value 1) moves the +X face to x=3: Blender says [8, 2, 2] for a scale of 2 on X; the reader
        says [4, 2, 2], so a check stating the observed dimensions fails and one stating the base mesh passes.
        """
        obj = mesh_object(world=scale_matrix(2, 1, 1), bounds=((-1, -1, -1), (3, 1, 1)))
        obj.data.shape_keys = NS(key_blocks=[NS(name="Basis", value=0.0), NS(name="Key 1", value=1.0)])
        self.assertEqual(blender_dimensions(obj), [8.0, 2.0, 2.0])
        result = verify(VerificationContext({"$o": obj}, [obj]),
                        [{"kind": "dimensions", "target": "$o", "expected": [8, 2, 2], "tolerance": 1e-9,
                          "space": "LOCAL"}])
        self.assertEqual(self.local(obj), blender_dimensions(obj))
        self.assertTrue(result["passed"], result["checks"][0])

    def test_armature_parent_deform_without_modifiers_reads_the_base_mesh(self):
        """FINDING (fixed): a PARSKEL parent (parent_type ARMATURE/LATTICE/CURVE) deforms through a virtual
        modifier (modifier.cc:627-644) with ``obj.modifiers`` empty, so LOCAL (and WORLD) read the undeformed base
        mesh: [2, 2, 2] where Blender's dimensions are [6, 2, 2]."""
        armature = NS(type="ARMATURE", name="Rig", parent=None, constraints=[], animation_data=None)
        obj = mesh_object(bounds=((-1, -1, -1), (5, 1, 1)), parent=armature, parent_type="ARMATURE")
        self.assertEqual(self.local(obj), blender_dimensions(obj))

    def test_local_matches_blenders_formula_for_rotated_sheared_negative_and_modified_objects(self):
        """PIN: the formula is Blender's: column lengths of the world matrix (a rotated, non-uniformly scaled
        parent's shear, a mirrored axis and a delta scale included) times the object-space extent, for the base
        mesh path and the evaluated-bounds path (a viewport modifier); WORLD stays along the world axes."""
        parent = rotation_z(0.5) @ scale_matrix(3, 1, 1)
        child = rotation_z(0.7) @ scale_matrix(1, 2, 0.5)
        delta = scale_matrix(1, 1, 1.5)
        for world in (parent @ child, parent @ child @ delta, scale_matrix(-2, 1, 1) @ rotation_z(0.3),
                      parent @ scale_matrix(1, -1, 1)):
            for modifiers in ((), (NS(name="Array", type="ARRAY", show_viewport=True),)):
                with self.subTest(world=np.round(world, 3).tolist(), modifiers=len(modifiers)):
                    obj = mesh_object(world=world, modifiers=modifiers)
                    expected = blender_dimensions(obj)
                    for got, want in zip(self.local(obj), expected):
                        self.assertAlmostEqual(got, want, places=9)
        hidden = mesh_object(world=scale_matrix(2, 1, 1), bounds=((-1, -1, -1), (6, 1, 1)),
                             modifiers=(NS(name="Array", type="ARRAY", show_viewport=False),))
        self.assertEqual(self.local(hidden), [4.0, 2.0, 2.0], "a modifier hidden in viewports is not evaluated")

    def test_one_reader_per_verify_so_no_cached_dimension_survives_a_write(self):
        """PIN: ``dimension_cache`` is per ValidationReader and ``verify`` builds one per call
        (scene_validation.py:77; live_checks and program_verification call verify per check)."""
        obj = mesh_object(world=scale_matrix(2, 1, 1))
        context = VerificationContext({"$o": obj}, [obj])
        check = {"kind": "dimensions", "target": "$o", "expected": [4, 2, 2], "tolerance": 1e-9, "space": "LOCAL"}
        self.assertTrue(verify(context, [check, check])["passed"])
        obj.matrix_world = rows(scale_matrix(3, 1, 1))
        self.assertEqual(verify(context, [check])["status"], "fail")


# --------------------------------------------------------------------------- 2. bevel-and-shade apply_scale

class ApplyScaleTests(unittest.TestCase):
    def assertRefused(self, obj, code=None):
        with self.assertRaises(RefusalError) as caught:
            expanded = expand_live(obj)
            kinds = [operation["kind"] for operation in expanded["operations"]]
            self.fail("expanded to %s without a refusal" % kinds)
        if code:
            self.assertIn(code, str(caught.exception))

    def test_delta_scale_at_unit_scale_is_silently_skipped(self):
        """FINDING (fixed): ``_scale_operations`` returns [] for a unit ``scale`` before it looks at the
        refusals (action_recipes.py:196-197), so an object stretched by ``delta_scale`` (2, 1, 1) gets no
        transform_apply and no DELTA_TRANSFORMS refusal: the request for an even bevel is certified with the bevel
        still stretched 2:1 in X."""
        self.assertRefused(recipe_object(scale=(1, 1, 1), delta_scale=(2, 1, 1)), "DELTA_TRANSFORMS")

    def test_a_parents_non_uniform_scale_is_not_refused(self):
        """FINDING (fixed): the parameter's note promises "set apply_scale to bevel evenly", but a parent's
        scale (or a scaled matrix_parent_inverse) is never read (``scale_facts`` reads no parent): with a parent
        scaled (3, 1, 1) the recipe applies nothing (own scale 1) or bakes only the child's own scale, adds a
        passing scale==1 check, and the bevel stays stretched 3:1 in the world."""
        parent = NS(type="MESH", scale=(3, 1, 1), matrix_world=rows(scale_matrix(3, 1, 1)))
        for own in ((1, 1, 1), (2, 0.5, 1.5)):
            with self.subTest(own=own):
                self.assertRefused(recipe_object(scale=own, parent=parent), "PARENT_SCALE")
        turned = NS(type="EMPTY", scale=(1, 1, 1), matrix_world=rows(rotation_z(0.7)))
        expanded = expand_live(recipe_object(parent=turned))
        self.assertEqual(expanded["operations"][0]["kind"], "transform_apply", "a parent that only turns is fine")

    def test_mirrored_scale_with_keyed_rotation_is_baked(self):
        """FINDING (fixed in the kernel rather than refused): ``apply_transform`` rebuilt the basis
        from ``Matrix.decompose``, which for a negative determinant returns the rotation turned 180 degrees and
        every scale axis negated (math_matrix_c.cc:2025-2028). For scale (-2, 1, 1) it baked diag(-2, -1, -1) and
        wrote a rotation of 180 degrees about X, so a keyed rotation flipped the object on the next frame.
        ``transform_bake.bake`` now bakes what Blender's Apply Scale bakes (object_transform.cc:815, 1058): the
        scale matrix alone, faces flipped, the rotation channels untouched, so any rotation the keys give later
        shows the same object."""
        expanded = expand_live(recipe_object(scale=(-2, 1, 1), animation_data=animation(paths=("rotation_euler",))))
        self.assertEqual(expanded["operations"][0]["parameters"], {"location": False, "rotation": False, "scale": True})
        turn = rotation_z(0.4)[:3, :3] @ np.array([[1, 0, 0], [0, 0.6, -0.8], [0, 0.8, 0.6]])
        scale = np.diag([-2.0, 1.0, 1.0])
        applied, translation, mirrors = bake((turn @ scale).tolist(), [-2, 1, 1], [1, 2, 3], apply_location=False,
                                             apply_rotation=False, apply_scale=True)
        self.assertTrue(np.allclose(applied, scale), "the scale alone is baked")
        self.assertEqual((translation, mirrors), ([0.0, 0.0, 0.0], True), "faces flip, as Corrective Flip Normals")
        for keyed in (np.eye(3), rotation_z(1.1)[:3, :3], turn):  # Whatever the rotation keys evaluate to later.
            self.assertTrue(np.allclose(keyed @ np.eye(3) @ np.asarray(applied), keyed @ scale))

    def test_every_apply_keeps_the_world_result_and_resets_only_its_channels(self):
        """Blender's Apply for every flag combination, mirrored and rotated: the world matrix times the baked mesh
        is what it was, with only the applied channels reset (location 0, rotation identity, scale 1)."""
        rng = np.random.default_rng(7)
        for scale in ([2, 0.5, 1.5], [-2, 1, 1], [-1, -3, 0.5]):
            angle = rng.uniform(-math.pi, math.pi, 3)
            rotation = (rotation_z(angle[0])[:3, :3]
                        @ np.array([[1, 0, 0], [0, math.cos(angle[1]), -math.sin(angle[1])],
                                    [0, math.sin(angle[1]), math.cos(angle[1])]]))
            location = rng.uniform(-3, 3, 3)
            before = np.eye(4)
            before[:3, :3], before[:3, 3] = rotation @ np.diag(scale), location
            for flags in [(lo, ro, sc) for lo in (False, True) for ro in (False, True) for sc in (False, True)]:
                with self.subTest(scale=scale, flags=flags):
                    applied, translation, mirrors = bake((rotation @ np.diag(scale)).tolist(), scale, location.tolist(),
                                                         apply_location=flags[0], apply_rotation=flags[1],
                                                         apply_scale=flags[2])
                    baked = np.eye(4)
                    baked[:3, :3], baked[:3, 3] = applied, translation
                    after = np.eye(4)
                    after[:3, :3] = (np.eye(3) if flags[1] else rotation) @ np.diag([1, 1, 1] if flags[2] else scale)
                    after[:3, 3] = [0, 0, 0] if flags[0] else location
                    self.assertTrue(np.allclose(after @ baked, before), "the world result is unchanged")
                    self.assertEqual(mirrors, np.linalg.det(np.asarray(applied)) < 0)
        with self.assertRaises(ValueError):  # Rotation alone over a zero scale axis has no inverse, as in Blender.
            bake(np.diag([2.0, 0.0, 1.0]).tolist(), [2, 0, 1], [0, 0, 0], apply_location=False, apply_rotation=True,
                 apply_scale=False)

    def test_object_space_modifiers_change_the_world_result(self):
        """FINDING (fixed): the doc says the object's shape in the world is unchanged, but ``scale_facts`` reads
        no modifiers. Existing modifiers measured in object space change their world effect once the scale is
        baked: Solidify thickness 0.1 on the X faces of a cube scaled 2 is 0.2 m before, 0.1 m after; an Array's
        constant offset halves; a Hook's stored inverse no longer cancels (the hooked vertices are scaled again);
        Multires displacements are not rescaled (Blender calls multiresModifier_scale_disp, the kernel does not).
        The recipe's checks (finite, scale==1, bevel values, shading graph) all pass."""
        for kind in ("SOLIDIFY", "ARRAY", "HOOK", "MULTIRES"):
            with self.subTest(kind=kind):
                modifier = NS(name=kind.title(), type=kind, show_viewport=False)  # Hidden, it still renders.
                self.assertRefused(recipe_object(scale=(2, 1, 1), modifiers=[modifier]), "MODIFIERS: baking")
        subdivided = expand_live(recipe_object(scale=(2, 1, 1), modifiers=[NS(name="Subdivision", type="SUBSURF")]))
        self.assertEqual(subdivided["operations"][0]["kind"], "transform_apply", "subdivision is affine-invariant")
        unit = expand_live(recipe_object(scale=(1, 1, 1), modifiers=[NS(name="Solidify", type="SOLIDIFY")]))
        self.assertEqual(unit["operations"][0]["kind"], "modifier_add", "nothing to bake, nothing refused")

    def test_refusals_and_unit_scale_as_documented(self):
        """PIN: children, deltas, keyed/driven/NLA scale, constraints, non-mesh and a zero axis refuse before
        approval; a scale within 1e-6 of one adds nothing; the added scale check uses params tolerance."""
        refused = {"CHILDREN": dict(children=(object(),)), "DELTA_TRANSFORMS": dict(delta_location=(0, 0, 1)),
                   "ANIMATED_SCALE": dict(animation_data=animation(drivers=("delta_scale",))),
                   "CONSTRAINED": dict(constraints=(object(),)), "NOT_A_MESH": dict(type="CURVE"),
                   "ZERO_SCALE": dict(scale=(2, 0.0000005, 1))}
        for code, change in refused.items():
            with self.subTest(code=code):
                self.assertRefused(recipe_object(**change), code)
        unit = expand_live(recipe_object(scale=(1.0000005, 1, 1)))
        self.assertEqual([op["kind"] for op in unit["operations"]], ["modifier_add", "geometry_nodes"])
        scaled = expand_recipe("bevel-and-shade", HANDLE, dict(BEVEL, tolerance=0.05),
                               {"target": HANDLE, "status": "pass", "scale_facts": scale_facts(recipe_object())})
        (check,) = [c for c in scaled["expected_checks"] if c["kind"] == "transform"]
        self.assertEqual((check["expected"], check["tolerance"]), ([1, 1, 1], 0.05))

    def test_apply_scale_is_in_both_plan_grammars(self):
        """PIN: apply_scale is accepted by Claude's plan schema and by the Codex strict grammar (decoded, then
        validated with the default when null)."""
        from angelo_agent import codex_plan
        from angelo_agent.plan_schema import plan_schema
        from angelo_agent.tool_specs import TOOLS
        self.assertIn("apply_scale", json.dumps(plan_schema(TOOLS)))
        self.assertIn("apply_scale", json.dumps(codex_plan.output_schema()))
        operation = {"tool": "angelo_apply_recipe", "arguments": {"recipe": "bevel-and-shade", "target": "o4",
                     "parameters": {"width": 0.1, "segments": 2, "apply_scale": True}, "mode": "APPLY"}}
        plan, problem = codex_plan.parse_plan(json.dumps({"reply": "", "operations": [operation]}))
        self.assertIsNone(problem)
        self.assertIs(plan["operations"][0]["arguments"]["parameters"]["apply_scale"], True)


# ------------------------------------------------------------------------------------ 3. plan bindings

class OwnCommitBindingTests(unittest.TestCase):
    TARGET = "scn:" + "e" * 32 + ":9"
    OBSERVED, AFTER_OWN, MANUAL = "1" * 64, "2" * 64, "3" * 64

    def committed(self, revision):
        return {"live_revisions": {"status": "COMMITTED", "objects": [{"handle": self.TARGET,
                                                                       "object_revision": revision}]}}

    def call(self, revision):
        return {"tool": "angelo_apply_recipe", "arguments": {"recipe": "bevel-and-shade", "target": self.TARGET,
                                                               "revision": revision, "mode": "APPLY",
                                                               "parameters": {}}}

    def test_a_manual_change_is_never_absorbed(self):
        """PIN: a later item is moved only along the request's own commit (OBSERVED -> AFTER_OWN). A change the
        person makes after that commit (AFTER_OWN -> MANUAL) leaves the item at AFTER_OWN, which the owner's exact
        comparison refuses; one made before it is never mapped (the own commit itself would have been refused).
        Revisions are read inside the commit operator (program_commit_report.post_commit_revisions)."""
        own = OwnCommits()
        own.record(self.call(self.OBSERVED), self.committed(self.AFTER_OWN))
        rebound = own.rebind(self.call(self.OBSERVED))["arguments"]["revision"]
        self.assertEqual(rebound, self.AFTER_OWN)
        self.assertNotEqual(rebound, self.MANUAL)
        self.assertEqual(own.rebind(self.call(self.MANUAL))["arguments"]["revision"], self.MANUAL)
        own.record(self.call(self.MANUAL), {"live_revisions": {"status": "PREVIEW", "objects": [
            {"handle": self.TARGET, "object_revision": "4" * 64}]}})
        self.assertEqual(own.latest(self.TARGET, self.MANUAL), self.MANUAL, "only COMMITTED results move a binding")


# ------------------------------------------------------------------------- 4. status line and batch note

class ThinkingStatusTests(unittest.TestCase):
    def test_status_line_is_bounded_data_and_never_replaces_another_status(self):
        """PIN: the title is at most 64 characters, whitespace and the status separator are folded away, and only
        the generic "Thinking" status takes it."""
        text = "**" + "Planning\x1fthe   bridge " * 20 + "**"
        status = thinking_status("Thinking", text)
        base, detail = split(status)
        self.assertEqual(base, "Thinking")
        self.assertLessEqual(len(detail), 64)
        self.assertNotIn(SEPARATOR, detail)
        for other in ("Approval required", "Preparing the next steps" + SEPARATOR + "Deck", "Connecting"):
            self.assertEqual(thinking_status(other, "**Planning**"), other)

    def test_the_title_is_a_title_not_body_text(self):
        """FINDING (fixed, display only): ``summary_title`` takes the last ``**...**`` in the last 4096 characters
        (plan_progress.py:97, 110). Inline bold in a part's body wins over the part's title, and a window that
        starts inside a title pairs that title's closing ``**`` with the next opening one, so the live row shows a
        run of body text as the title. Display only: the text is the same public summary the thinking row shows."""
        self.assertEqual(summary_title("**Planning the bridge**\n\nThe deck needs **two** posts"),
                         "Planning the bridge")
        text = "**" + "A" * 5000 + "** then I place the posts **Checking spacing**"
        self.assertTrue(text[-4096:].startswith("A"), "the window starts inside the first title")
        self.assertEqual(summary_title(text), "Checking spacing")


class BatchNoteTests(unittest.TestCase):
    def test_the_placement_note_counts_copies_for_mirror_and_several_sources(self):
        """FINDING (fixed): PLACEMENT_NOTE, model-visible, said "n positions make n-1 copies"; with ``mirror`` they
        make 2n-1 more instances and with k sources (n-1)*k objects (batch_spec.copies). It now says "n-1 more
        instances (2n-1 with mirror), each copying every source", which is what copies() counts."""
        from angelo_agent.batch_spec import PLACEMENT_NOTE, copies
        base = {"sources": ["$post"], "linked": True, "array": None, "at": [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
                "rotations": None, "scales": None, "along": None, "scatter": None, "mirror": None, "vary": None}
        self.assertNotIn("n-1 copies", PLACEMENT_NOTE)
        self.assertIn("n positions make n-1 more instances (2n-1 with mirror), each copying every source",
                      PLACEMENT_NOTE)
        for mirror in (None, "X"):
            for sources in (["$post"], ["$post", "$cap"]):
                with self.subTest(mirror=mirror, sources=len(sources)):
                    params = dict(base, mirror=mirror, sources=sources)
                    n = len(params["at"])
                    self.assertEqual(copies(params), ((2 * n - 1) if mirror else (n - 1)) * len(sources))


if __name__ == "__main__":
    unittest.main()
