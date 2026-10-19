# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a reviewed script's Booleans did (script_booleans), through the real executor, and the kit's cut proofs.

Run by ``run_native_suites.py --include test_blender_script_booleans`` (BACKGROUND). Every script runs through
``BlenderExecutor.execute("angelo_execute_python")``, where the hook runs. It proves in the real Blender:

* applied Booleans: a correct cut, a cutter that misses, a cutter wound inward, one that swallows the block, a cut
  that keeps under half, UNION and INTERSECT, a collection operand, a Boolean that is not first, ``modifier``
  omitted (Blender's exec applies nothing), an ``EXEC_DEFAULT`` positional, a ``temp_override`` object,
  ``use_selected_objects``, an open mesh, a mesh past the triangle cap, mirrored and inward-wound objects, the row
  cap; volumes checked against Blender's own ``bmesh`` volume on held-out shapes (cylinders, spheres, rotated and
  scaled objects) the measure was not developed on;
* live stacks: a stack left on the object, and a target whose cutter a later script moved;
* adversarial cases: a small real cut (a bolt hole) never reads as unchanged, and an object the view
  layer does not evaluate (disabled in viewports, excluded, in a viewport-disabled collection) claims no measure;
* a script that raises after an apply gets no ``booleans``, and ``bpy.ops`` is restored after every script;
* the kit (script_kit_geometry): a crowded cut, and a retried one, whose difference removed nothing are refused
  (a stand-in difference), and real crowded cuts still pass;
* recorded, not asserted: the milliseconds the hook adds on a 300-object scene and the bytes it adds.

No provider, account or network. Writes script-booleans.json under ANGELO_QA_DIR.
"""

import json
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

import _bpy
import bmesh
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import model_results, script_booleans, script_kit_geometry  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.script_kit import ScriptKit  # noqa: E402

bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window
REAL_FACTORY = _bpy.ops.create_function
# The miss's bound: 0.01% of the smaller of the 64 m³ block and its 2 x 2 x 6 m cutter.
UNCHANGED_BY_24 = "volume and faces unchanged (within 0.0024 m³)"
# A 4 cm bolt hole through a 10 x 1 x 4 m wall: a real cut of 0.00126 m³, under 0.01% of the wall.
BOLT = ("t = box('Wall', (0,0,0), (10,1,4))\n"
        "bm = bmesh.new()\n"
        "bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.02, radius2=0.02, depth=2)\n"
        "me = bpy.data.meshes.new('Bolt'); bm.to_mesh(me); bm.free()\n"
        "c = bpy.data.objects.new('Bolt', me); bpy.context.scene.collection.objects.link(c)\n"
        "c.location, c.rotation_euler = (5, 0.5, 2), (math.pi / 2, 0, 0)\n"
        "boolean(t, c)\n")
restored = []   # Whether bpy.ops builds operators as before, after each script.

HELPERS = """
import bpy, bmesh, math
def box(name, lo, hi, inward=False, link=True):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = [(l + h) / 2 + c * (h - l) for c, l, h in zip(v.co, lo, hi)]
    if inward:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    if link:
        bpy.context.scene.collection.objects.link(o)
    return o
def boolean(target, cutter, operation='DIFFERENCE', solver='EXACT', name='Cut'):
    md = target.modifiers.new(name, 'BOOLEAN')
    md.object, md.operation, md.solver = cutter, operation, solver
    return md
def apply(target, name='Cut'):
    if isinstance(target, bpy.types.Modifier):   # apply(boolean(...)): the modifier's own object and name.
        target, name = target.id_data, target.name
    bpy.context.view_layer.objects.active = target
    return bpy.ops.object.modifier_apply(modifier=name)
"""


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def fresh():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def script(code, clean=True):
    """One reviewed script through the executor; its result."""
    if clean:
        fresh()
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "QA Booleans",
                                                                  "code": HELPERS + code})
    restored.append(bpy.ops._op_create_function is REAL_FACTORY)
    return result


def only(result):
    rows = result.get("booleans") or []
    return rows[0] if len(rows) == 1 else {"rows": rows, "error": result.get("error")}


def applied_rows():
    """Each applied case: the script's lines after the helpers and the row it must give (keys checked)."""
    cases = {
        "a_correct_cut_reports_the_volume_it_removed": (
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (1,1,-1), (3,3,5))))",
            {"object": "T", "with": "C", "operation": "DIFFERENCE", "solver": "EXACT", "applied": True,
             "faces": [6, 12], "volume_m3": [64.0, 48.0]}, ("bounds", "fact")),
        "a_cutter_that_misses_reports_volume_and_faces_unchanged": (
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (9,1,-1), (11,3,5))))",
            {"faces": [6, 6], "volume_m3": [64.0, 64.0], "fact": UNCHANGED_BY_24}, ()),
        "a_cutter_wound_inward_reports_the_volume_unchanged": (   # The recorded castle: 10 faces, nothing removed.
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (1,1,-1), (3,3,5), inward=True)))",
            {"faces": [6, 10], "volume_m3": [64.0, 64.0], "fact": "volume unchanged (within 0.0024 m³)"}, ()),
        "a_cutter_that_swallows_the_block_reports_no_faces_left": (
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (-1,-1,-1), (5,5,5))))",
            {"faces": [6, 0], "volume_m3": [64.0, None], "volume": "after: no faces", "fact": "no faces left"}, ()),
        "a_cut_that_keeps_under_half_states_the_share": (
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (-1,-1,1), (5,5,5))))",
            {"volume_m3": [64.0, 16.0], "fact": "kept 25% of its volume",
             "bounds": [[[0.0, 0.0, 0.0], [4.0, 4.0, 4.0]], [[0.0, 0.0, 0.0], [4.0, 4.0, 1.0]]]}, ()),
        "a_union_reports_the_volume_it_added": (
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (2,2,2), (6,6,6)), 'UNION'))",
            {"operation": "UNION", "volume_m3": [64.0, 120.0],
             "bounds": [[[0.0, 0.0, 0.0], [4.0, 4.0, 4.0]], [[0.0, 0.0, 0.0], [6.0, 6.0, 6.0]]]}, ("fact",)),
        "an_intersect_reports_the_share_it_kept": (
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (2,2,2), (6,6,6)), 'INTERSECT'))",
            {"operation": "INTERSECT", "volume_m3": [64.0, 8.0], "fact": "kept 12% of its volume"}, ()),
        "a_difference_that_grew_says_so": (   # The MANIFOLD solver with a cutter wound inward.
            "apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (1,1,-1), (3,3,5), inward=True), solver='MANIFOLD'))",
            {"solver": "MANIFOLD", "volume_m3": [64.0, 80.0], "fact": "volume grew by 25%"}, ()),
        "a_boolean_that_is_not_first_measures_the_base_mesh": (
            "t = box('T', (0,0,0), (4,4,4)); t.modifiers.new('Rows', 'ARRAY').count = 3\n"
            "boolean(t, box('C', (1,1,-1), (3,3,5))); apply(t)\n"
            "assert [m.name for m in t.modifiers] == ['Rows']",
            {"applied": True, "faces": [6, 12], "volume_m3": [64.0, 48.0]}, ()),
        "an_exec_default_positional_is_observed": (
            "t = box('T', (0,0,0), (4,4,4)); boolean(t, box('C', (1,1,-1), (3,3,5)))\n"
            "bpy.context.view_layer.objects.active = t\n"
            "bpy.ops.object.modifier_apply('EXEC_DEFAULT', modifier='Cut')",
            {"applied": True, "volume_m3": [64.0, 48.0]}, ()),
        "a_mirrored_target_has_a_positive_volume": (   # Blender's Boolean flips a negative transform's faces.
            "t = box('T', (0,0,0), (4,4,4)); t.scale = (-1, 1, 1)\napply(boolean(t, box('C', (-3,1,-1), (-1,3,5))))",
            {"volume_m3": [64.0, 48.0]}, ("fact",)),
        "a_target_wound_inward_has_a_negative_volume": (
            "apply(boolean(box('T', (0,0,0), (4,4,4), inward=True), box('C', (1,1,-1), (3,3,5))))",
            {"volume_m3": [-64.0, 48.0], "fact": script_booleans.NEGATIVE}, ()),
        "a_scaled_moved_target_is_measured_in_its_evaluated_matrix": (   # No update between the move and the apply.
            "t = box('T', (0,0,0), (4,4,4)); t.scale = (2, 2, 2); t.location = (10, 0, 0)\n"
            "apply(boolean(t, box('C', (11,1,-1), (13,3,5))))",
            {"volume_m3": [512.0, 492.0]}, ("bounds",)),
        "a_small_real_cut_never_reads_as_unchanged": (   # A bolt hole: no fact, the numbers stand.
            BOLT + "apply(t)", {"faces": [6, 24], "volume_m3": [40.0, 39.999]}, ("fact",)),
        "an_unevaluated_target_keeps_its_face_counts_and_claims_no_world_measure": (
            "t = box('T', (0,0,0), (2,2,2)); boolean(t, box('C', (1,-1,-1), (3,3,3)))\n"
            "t.hide_viewport = True; t.scale = (2, 2, 2)\n"   # Never evaluated: its matrix stays the old one.
            "apply(t)",
            {"applied": True, "faces": [6, 10], "volume": script_booleans.NOT_EVALUATED},
            ("volume_m3", "bounds", "fact")),
        "an_open_mesh_reports_faces_only": (
            "me = bpy.data.meshes.new('P'); me.from_pydata([(0,0,0),(4,0,0),(4,4,0),(0,4,0)], [], [(0,1,2,3)])\n"
            "t = bpy.data.objects.new('P', me); bpy.context.scene.collection.objects.link(t)\n"
            "apply(boolean(t, box('C', (1,1,-1), (3,3,1))))",
            {"object": "P", "faces": [1, 2], "volume": "open mesh"}, ("volume_m3",)),
    }
    for name, (code, expected, absent) in cases.items():
        row = only(script(code))
        check(name, all(row.get(key) == value for key, value in expected.items())
              and not any(key in row for key in absent), row=row)


def operands_and_calls():
    row = only(script(
        "t = box('T', (0,0,0), (4,4,4))\ncutters = bpy.data.collections.new('QA Cutters')\n"
        "bpy.context.scene.collection.children.link(cutters)\n"
        "for lo, hi in (((1,1,-1), (2,2,5)), ((3,1,-1), (3.5,3,5))):\n"
        "    cutters.objects.link(box('C', lo, hi, link=False))\n"
        "md = t.modifiers.new('Cut', 'BOOLEAN'); md.operand_type = 'COLLECTION'; md.collection = cutters\napply(t)"))
    check("a_collection_operand_is_named_and_measured", row.get("with_collection") == "QA Cutters"
          and "with" not in row and row.get("volume_m3") == [64.0, 56.0], row=row)
    result = script("t = box('T', (0,0,0), (4,4,4)); boolean(t, box('C', (1,1,-1), (3,3,5)))\n"
                    "bpy.context.view_layer.objects.active = t\nresult = sorted(bpy.ops.object.modifier_apply())")
    rows = result.get("booleans") or []
    check("an_omitted_modifier_applies_nothing_and_the_rows_say_so",
          result.get("result") == ["CANCELLED"] and len(rows) == 2 and rows[0].get("applied") is False
          and "faces" not in rows[0] and rows[1].get("live") is True and rows[1].get("volume_m3") == [64.0, 48.0],
          rows=rows, result=result.get("result"))
    result = script("t = box('T', (0,0,0), (4,4,4)); boolean(t, box('C', (1,1,-1), (3,3,5)))\n"
                    "other = box('Other', (10,0,0), (11,1,1)); boolean(other, t)\n"
                    "bpy.context.view_layer.objects.active = other\n"
                    "with bpy.context.temp_override(object=t):\n"
                    "    bpy.ops.object.modifier_apply(modifier='Cut')")
    rows = result.get("booleans") or []   # Then Other's live Boolean, whose operand (T) the script changed.
    check("a_temp_override_object_is_the_one_measured",
          [(row.get("object"), row.get("applied"), row.get("live")) for row in rows]
          == [("T", True, None), ("Other", None, True)] and rows[0].get("volume_m3") == [64.0, 48.0], rows=rows)
    result = script("a = box('A', (0,0,0), (4,4,4)); b = box('B', (10,0,0), (14,4,4))\n"
                    "c = box('C', (1,1,-1), (3,3,5))\n"
                    "for o in (a, b):\n    boolean(o, c); o.select_set(True)\n"
                    "bpy.context.view_layer.objects.active = a\n"
                    "bpy.ops.object.modifier_apply(modifier='Cut', use_selected_objects=True)")
    rows = {row.get("object"): row for row in result.get("booleans") or []}
    check("use_selected_objects_gives_a_row_per_object",
          rows.get("A", {}).get("volume_m3") == [64.0, 48.0]
          and rows.get("B", {}).get("fact") == UNCHANGED_BY_24 and len(rows) == 2, rows=rows)
    result = script("\n".join(
        "apply(boolean(box('T%d', (%d,0,0), (%d,1,1)), box('C%d', (%d.25,0.25,-1), (%d.75,0.75,2))))"
        % (i, 2 * i, 2 * i + 1, i, 2 * i, 2 * i) for i in range(11)))
    check("rows_are_capped_and_the_rest_counted", len(result.get("booleans") or ()) == script_booleans.MAX_ROWS
          and result.get("booleans_more") == 11 - script_booleans.MAX_ROWS,
          rows=len(result.get("booleans") or ()), more=result.get("booleans_more"))
    started = time.perf_counter()
    row = only(script("bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=520, v_segments=260, radius=2)\n"
                      "me = bpy.data.meshes.new('Dense'); bm.to_mesh(me); bm.free()\n"
                      "t = bpy.data.objects.new('Dense', me); bpy.context.scene.collection.objects.link(t)\n"
                      "apply(boolean(t, box('C', (9,9,9), (10,10,10)), solver='MANIFOLD'))"))
    check("a_mesh_past_the_triangle_cap_reports_faces_only",
          row.get("volume") == "over %d triangles" % script_booleans.MAX_TRIANGLES and row.get("faces") == [135200] * 2
          and "volume_m3" not in row and "bounds" not in row, row=row, seconds=round(time.perf_counter() - started, 3))


def held_out_volumes():
    """Shapes the measure was not developed on, against Blender's own bmesh volume of the same meshes."""
    code = ("t = bpy.data.objects.new('Drum', bpy.data.meshes.new('Drum'))\n"
            "bpy.context.scene.collection.objects.link(t)\n"
            "bm = bmesh.new()\n"
            "bmesh.ops.create_cone(bm, cap_ends=True, segments=40, radius1=1.5, radius2=1.5, depth=3)\n"
            "bm.to_mesh(t.data); bm.free()\n"
            "s = bpy.data.objects.new('Ball', bpy.data.meshes.new('Ball'))\n"
            "bpy.context.scene.collection.objects.link(s)\n"
            "bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)\n"
            "bm.to_mesh(s.data); bm.free()\n"
            "t.rotation_euler = (0.3, 0.7, 1.1); t.scale = (1.0, 2.0, 0.5); t.location = (3, -2, 1)\n"
            "s.location = (3.8, -1.2, 1.4)\n"
            "bpy.context.view_layer.update()\n"
            "before = bmesh.new(); before.from_mesh(t.data); before.transform(t.matrix_world)\n"
            "volume_before = before.calc_volume(signed=True); before.free()\n"
            "apply(boolean(t, s))\n"
            "after = bmesh.new(); after.from_mesh(t.data); after.transform(t.matrix_world)\n"
            "result = [volume_before, after.calc_volume(signed=True)]; after.free()")
    result = script(code)
    row, expected = only(result), result.get("result") or [0.0, 0.0]
    measured = row.get("volume_m3") or [None, None]
    check("held_out_shapes_match_blenders_bmesh_volume", all(
        value is not None and abs(value - want) <= 1e-3 * abs(want) for value, want in zip(measured, expected))
        and expected[1] < expected[0], measured=measured, bmesh=expected, row=row)


def live_stacks():
    row = only(script("t = box('Keep', (0,0,0), (4,4,4)); c = box('Hole', (1,1,-1), (3,3,5))\nboolean(t, c)\n"
                      "c.hide_set(True)"))
    check("a_live_stack_is_measured_base_against_evaluated", row.get("live") is True and "applied" not in row
          and row.get("with") == "Hole" and row.get("volume_m3") == [64.0, 48.0], row=row)
    row = only(script("bpy.data.objects['Hole'].location.x += 10", clean=False))
    check("moving_a_cutter_later_reports_its_target", row.get("object") == "Keep" and row.get("live") is True
          and row.get("fact") == UNCHANGED_BY_24, row=row)
    row = only(script("t = box('Two', (0,0,0), (4,4,4))\n"
                      "boolean(t, box('H1', (1,1,-1), (2,2,5)), name='A')\n"
                      "boolean(t, box('H2', (3,3,3), (5,5,5)), 'UNION', name='B')\n"
                      "t.modifiers.new('Hidden', 'BEVEL').show_viewport = False"))
    check("a_stack_of_several_names_each", [entry.get("operation") for entry in row.get("stack") or ()]
          == ["DIFFERENCE", "UNION"] and row.get("volume_m3") == [64.0, 64.0 - 4.0 + 8.0 - 1.0], row=row)
    row = only(script(BOLT))
    check("a_small_live_cut_never_reads_as_unchanged", row.get("live") is True and row.get("faces") == [6, 24]
          and row.get("volume_m3") == [40.0, 39.999] and "fact" not in row, row=row)
    half = "t = box('T', (0,0,0), (2,2,2)); boolean(t, box('C', (1,-1,-1), (3,3,3)))\n"   # Cuts half the block.
    unevaluated = {"object": "T", "with": "C", "operation": "DIFFERENCE", "solver": "EXACT", "live": True,
                   "volume": script_booleans.NOT_EVALUATED}
    moved = ("col = bpy.data.collections.new('X'); bpy.context.scene.collection.children.link(col)\n"
             "col.objects.link(t); bpy.context.scene.collection.objects.unlink(t)\n")
    rows = {how: only(script(half + line)) for how, line in (
        ("disabled in viewports", "t.hide_viewport = True"),
        ("in an excluded collection", moved + "bpy.context.view_layer.layer_collection.children['X'].exclude = True"),
        ("in a collection disabled in viewports", moved + "col.hide_viewport = True"),
        ("hidden, still evaluated", "t.hide_set(True)"))}
    check("an_unevaluated_live_stack_is_named_without_measures_or_facts",
          all(rows[how] == unevaluated for how in list(rows)[:3])
          and rows["hidden, still evaluated"].get("volume_m3") == [8.0, 4.0], rows=rows)
    row = only(script("t = box('Shaped', (0,0,0), (4,4,4)); boolean(t, box('C', (1,1,-1), (3,3,5)))\n"
                      "t.modifiers.new('Bevel', 'BEVEL')"))
    check("a_stack_with_another_modifier_is_left_out", row == {"rows": [], "error": None}, row=row)


def failures():
    result = script("apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (1,1,-1), (3,3,5))))\nraise ValueError('late')")
    check("a_script_that_raises_after_an_apply_gets_no_booleans", result.get("error") and result.get("success")
          is not True and "booleans" not in result and restored[-1], keys=sorted(result))
    result = script("apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (1,1,-1), (3,3,5))))\nimport sys\nsys.exit(3)")
    check("a_script_that_exits_after_an_apply_gets_no_booleans_and_bpy_ops_is_back", result.get("error")
          and result.get("success") is not True and "booleans" not in result and restored[-1], keys=sorted(result))
    result = script("t = box('T', (0,0,0), (4,4,4)); boolean(t, box('C', (1,1,-1), (3,3,5)))\n"
                    "bpy.context.view_layer.objects.active = t\n"
                    "assert bpy.ops.object.modifier_apply.poll() and bpy.ops.object.modifier_apply.idname() "
                    "== 'OBJECT_OT_modifier_apply'\nresult = repr(bpy.ops.object.modifier_apply)[:30]")
    check("the_observed_operator_keeps_poll_idname_and_repr", result.get("success") is True
          and result.get("result") == "bpy.ops.object.modifier_apply(", result=result.get("result"),
          error=result.get("error"))


def kit_proofs():
    """A difference that removed nothing passed a crowded cut (overlapping=True: no volume proof) and a retried one."""
    def overlapping_target(kit):
        bm = bmesh.new()
        for lo, hi in (((0, 0, 0), (4, 1, 2)), ((3, 0, 0), (4, 4, 2))):   # Two boxes that overlap: crowded.
            for vertex in bmesh.ops.create_cube(bm, size=1)["verts"]:
                vertex.co = [(a + b) / 2 + v * (b - a) for v, a, b in zip(vertex.co, lo, hi)]
        mesh = bpy.data.meshes.new("Overlap")
        bm.to_mesh(mesh)
        bm.free()
        target = bpy.data.objects.new("Overlap", mesh)
        bpy.context.scene.collection.objects.link(target)
        return target
    real = script_kit_geometry._boolean

    def removes_nothing(bpy_, work, tool, operation, overlapping):
        if operation == "DIFFERENCE":
            return work.data.copy()
        return real(bpy_, work, tool, operation, overlapping)
    outcomes = {}
    for label, make in (("crowded", overlapping_target),
                        ("retried", lambda kit: kit.box("Plain", (0, 0, 0), (4, 4, 2)))):
        for stand_in in (False, True):
            fresh()
            kit = ScriptKit(bpy, bpy.context.scene)
            target = make(kit)
            cutter = kit.box("Door", (1, -1, -1), (2, 5, 1))
            data, faces = target.data, len(target.data.polygons)
            script_kit_geometry._boolean = removes_nothing if stand_in else real
            try:
                kit.cut(target, cutter)
                outcomes[label, stand_in] = ("cut", len(target.data.polygons) > faces)
            except ValueError as error:
                outcomes[label, stand_in] = (str(error), target.data == data and len(target.data.polygons) == faces)
            finally:
                script_kit_geometry._boolean = real
            kit.finish()
    shown = {"%s%s" % (label, " (removes nothing)" if stand_in else ""): value
             for (label, stand_in), value in outcomes.items()}
    check("a_crowded_cut_that_removed_nothing_is_refused_and_changes_nothing",
          "removed 0 m³, not the overlap's" in outcomes["crowded", True][0] and outcomes["crowded", True][1],
          outcomes=shown)
    check("a_retried_cut_that_removed_nothing_is_refused_and_changes_nothing",
          "removed 0 m³, not the overlap's" in outcomes["retried", True][0] and outcomes["retried", True][1],
          outcomes=shown)
    check("real_crowded_and_plain_cuts_still_cut",
          outcomes["crowded", False] == ("cut", True) and outcomes["retried", False] == ("cut", True), outcomes=shown)


def model_copy():
    result = script("apply(boolean(box('T', (0,0,0), (4,4,4)), box('C', (1,1,-1), (3,3,5))))")
    copy = model_results.model_result("angelo_execute_python", result, observation_follows=True)
    check("the_models_copy_keeps_the_rows", copy.get("booleans") == result.get("booleans") and copy.get("booleans"),
          rows=copy.get("booleans"))


def cost_and_bytes():
    """Recorded: milliseconds and bytes the hook adds to a script on a 300-object scene with a few Booleans."""
    def scene():
        fresh()
        for index in range(300):   # Walls, towers and parts: unit boxes in a 20 x 15 grid.
            x, y = index % 20, index // 20
            mesh = bpy.data.meshes.new("Part %03d" % index)
            work = bmesh.new()
            bmesh.ops.create_cube(work, size=1.0)
            work.to_mesh(mesh)
            work.free()
            obj = bpy.data.objects.new(mesh.name, mesh)
            obj.location = (x * 2.0, y * 2.0, 0.0)
            bpy.context.scene.collection.objects.link(obj)
        W.view_layer.update()
    code = ("for i in range(4):\n"
            "    t = box('Wall %d' % i, (i * 6.0, 40, 0), (i * 6.0 + 4, 41, 3))\n"
            "    boolean(t, box('Door %d' % i, (i * 6.0 + 1.5, 39, -1), (i * 6.0 + 2.5, 42, 2)))\n"
            "    if i < 3:\n"
            "        apply(t)\n"
            "bpy.data.objects['Part 000'].location.z += 0.5\n")
    real_applied, real_live = script_booleans.Applied, script_booleans.live

    class Off(real_applied):
        def __enter__(self):
            return self

        def outcome(self):
            return {}
    spent = {"measure": [], "live": []}   # Seconds in one "on" run: every measure, and the whole live reading.
    real_measure = script_booleans.measure

    def timed(key, function):
        def call(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                spent[key].append(time.perf_counter() - started)
        return call
    timed_live = timed("live", real_live)
    timings, inside = {"on": [], "off": []}, {"measure": [], "live": []}
    rows = None
    for round_ in range(14):
        state = "on" if round_ % 2 else "off"
        scene()
        script_booleans.Applied, script_booleans.live = ((Off, lambda window, result, names, clock=None: result)
                                                         if state == "off" else (real_applied, timed_live))
        script_booleans.measure = timed("measure", real_measure)
        for values in spent.values():
            values.clear()
        started = time.perf_counter()
        result = BlenderExecutor(W).execute("angelo_execute_python",
                                            {"description": "QA Booleans", "code": HELPERS + code})
        timings[state].append((time.perf_counter() - started) * 1000)
        script_booleans.Applied, script_booleans.live, script_booleans.measure = real_applied, real_live, real_measure
        if state == "on":
            rows = result.get("booleans")
            for key, values in spent.items():
                inside[key].append(round(sum(values) * 1000, 3))
    size = lambda value: len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))  # noqa: E731
    # Worst: 128-character names (label's limit), live stacks past MAX_STACK, changed bounds, a fact, rows past the cap.
    long = "N" * 124
    worst = script("".join(
        "t = box('%s %03d', (%d,0,0), (%d,1,1))\n" % (long, i, 3 * i, 3 * i + 1) + "".join(
            "boolean(t, box('%s %03d', (%d.5,0.5,0.5), (%d.5,1.5,%d)), 'UNION', name='U%d')\n"
            % (long, 100 * i + k, 3 * i, 3 * i, 2 + k, k) for k in range(5)) for i in range(10)))
    report["observed"]["cost"] = {
        "method": "BlenderExecutor.execute wall time, 7 interleaved runs with the hook and 7 with it replaced by a "
                  "no-op, "
                  "300 unit boxes plus a script adding 4 walls with door Booleans (3 applied, 1 live); median ms. "
                  "inside_ms: perf_counter ms per run in every measure (12: before, operand and after of 3 applies; "
                  "base, evaluated and operand of 1 live) and in the whole live reading (its 3 measures included)",
        "median_ms_on": round(statistics.median(timings["on"]), 2),
        "median_ms_off": round(statistics.median(timings["off"]), 2),
        "added_ms": round(statistics.median(timings["on"]) - statistics.median(timings["off"]), 2),
        "inside_ms": inside,
        "runs_ms": {key: [round(value, 1) for value in values] for key, values in timings.items()},
        "typical_bytes": size({"booleans": rows}), "typical_rows": len(rows or ()),
        "worst_bytes": size({key: worst[key] for key in ("booleans", "booleans_more") if key in worst}),
        "worst_rows": len(worst.get("booleans") or ()), "worst_more": worst.get("booleans_more")}
    check("the_cost_was_recorded", rows is not None and len(rows) == 4 and len(worst.get("booleans") or ()) == 8
          and worst.get("booleans_more") == 2, rows=rows)


try:
    applied_rows()
    operands_and_calls()
    held_out_volumes()
    live_stacks()
    failures()
    kit_proofs()
    model_copy()
    cost_and_bytes()
    check("bpy_ops_is_restored_after_every_script", restored and all(restored), scripts=len(restored))
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "script-booleans.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("SCRIPT_BOOLEANS failed checks:", json.dumps(failed))
print("SCRIPT_BOOLEANS cost:", json.dumps(report["observed"].get("cost")))
assert report["success"], failed or report.get("error")
