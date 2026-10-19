# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host gaps in the compiled app: revisions the host left out, rule-based selection, result facts.

Run by ``run_native_suites.py --include test_blender_rc10_gaps`` (BACKGROUND). It proves in the real Blender:

* revisions (``revision_budget``): what ``object_revision`` costs on plain grid meshes (recorded); a script that
  changes meshes of about 90,000 vertices gets a revision for each in its ``after`` and for the scene settings,
  and the next program, which sets a scene setting with its binding left to inference, commits; a turn context
  whose budget is already spent still gives the scene settings and every small object a revision;
* rule-based selection: a material on every upward face of a 6,400-face grid (past the 4,096 bound of explicit
  index lists) commits within one program;
* typed world facts: a committed program that creates a Sun light, or sets a Sky, states the Sun lights and each
  one's angle from the sky's sun (``world``), and one that touches neither states nothing;
* sizes: a script's result states the world-space bounds of a ground it added (scaled and moved);
* compact results: a 30-object batch's committed result reads as one row per object (live row with bounds),
  at under 60% of the raw bytes;
* adversarial cases: a failing brightness reading never fails a commit, a rename never moves one
  object's size to another, lights hidden for render are not listed, a dependency outside the scene is named.

No provider, account or network.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bmesh
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import context_ledger, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent import revision_budget, scene_settings, turn_context  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def run(arguments):
    """Bind, stage and commit an angelo_scene_program; its final result."""
    native.precondition(W, "angelo_scene_program", arguments)
    job = native.begin(W, "angelo_scene_program", arguments)
    result = job.poll()
    while not (result.get("final") or result.get("ready_to_commit")):
        result = job.poll()
    return job.commit() if result.get("ready_to_commit") else result


def grid(name, vertices, size=10.0, location=(0, 0, 0)):
    side = max(2, int(round(vertices ** .5)))
    mesh = bpy.data.meshes.new(name)
    work = bmesh.new()
    bmesh.ops.create_grid(work, x_segments=side - 1, y_segments=side - 1, size=size)
    work.to_mesh(mesh)
    work.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    W.scene.collection.objects.link(obj)
    return obj


def revision_costs():
    """Recorded, not asserted: the costs revision_budget's bounds rest on."""
    costs = {}
    for count in (100, 4096, 10000, 90000):
        obj = grid("QA Cost %d" % count, count)
        W.view_layer.update()
        best = None
        for _ in range(3):
            queries.invalidate(W)
            started = time.process_time()
            queries.object_revision(W, obj)
            spent = time.process_time() - started
            best = spent if best is None else min(best, spent)
        costs[len(obj.data.vertices)] = round(best * 1000, 3)
        bpy.data.objects.remove(obj, do_unlink=True)
    best = None
    for _ in range(3):  # The first call also imports its readers.
        started = time.process_time()
        scene_settings.settings_revision(W.scene)
        spent = time.process_time() - started
        best = spent if best is None else min(best, spent)
    costs["scene_settings"] = round(best * 1000, 3)
    report["observed"]["revision_ms"] = costs


def script_read_back():
    """A large outdoor scene's shape: a script edits two ~90,000-vertex meshes and the ground, then a scene setting."""
    for name, count in (("QA Foliage", 90000), ("QA Grass", 90000), ("QA Ground", 9000)):
        grid(name, count, size=16.0)
    W.view_layer.update()
    code = ("import bpy\n"
            "for name in ('QA Foliage', 'QA Grass', 'QA Ground'):\n"
            "    for v in bpy.data.objects[name].data.vertices:\n"
            "        v.co.z += 0.01\n"
            "result = {'edited': 3}\n")
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "Lift three meshes", "code": code})
    after = result.get("after") or {}
    rows = {row.get("name"): row for row in after.get("objects") or ()}
    check("the_read_back_gives_every_changed_mesh_a_revision",
          result.get("success") and set(rows) == {"QA Foliage", "QA Grass", "QA Ground"}
          and all("object_revision" in row for row in rows.values()),
          rows={name: sorted(row) for name, row in rows.items()}, error=result.get("error"))
    settings = after.get("scene_settings") or {}
    check("the_read_back_keeps_the_scene_settings_revision", isinstance(settings.get("revision"), str),
          scene_settings=sorted(settings))
    table = IdentifierTable()
    table.compact(result)
    context_ledger.observe_committed(table, result)
    program = {"mode": "APPLY", "operations": [{"kind": "property_set", "parameters": {
        "values": {"render.resolution_x": 1280}}}]}
    try:
        committed = run(validate_call("angelo_scene_program", expand_call("angelo_scene_program", program, table)))
        status, error = committed.get("status"), committed.get("error")
    except Exception as error_:  # noqa: BLE001  (recorded as the check's evidence)
        status, error = None, repr(error_)
    check("the_next_scene_setting_binds_by_inference_and_commits",
          status == "committed" and W.scene.render.resolution_x == 1280, status=status, error=error)


def spent_turn_context():
    """A send whose budget is spent: the scene settings and small objects (within their allowance) get revisions."""
    small = grid("QA Small", 400, location=(30, 0, 0))
    large = grid("QA Large", 40000, location=(-30, 0, 0))
    wrapped = grid("QA Wrapped", 100, location=(30, 30, 0))  # Small itself, but its revision hashes the large mesh.
    wrapped.modifiers.new("Wrap", "SHRINKWRAP").target = large
    for obj in (small, large, wrapped):
        obj.select_set(True)
    W.view_layer.objects.active = None
    W.view_layer.update()
    past = turn_context.REVISION_BUDGET_SECONDS + revision_budget.SMALL_ALLOWANCE_SECONDS / 2
    readings = iter([0.0] + [past] * 10 ** 4)  # Every later reading: past the budget, within the allowance.
    observation = turn_context.prepare(W, None, clock=lambda: next(readings)).observation
    rows = {row["name"]: row for row in observation["objects"]}
    check("a_spent_budget_still_reads_the_scene_settings_revision",
          isinstance(observation["scene_settings"].get("revision"), str), scene_settings=observation["scene_settings"])
    check("a_spent_budget_leaves_a_small_revision_in_within_its_allowance",
          "object_revision" in rows["QA Small"] and revision_budget.small(small), row=rows["QA Small"])
    check("a_spent_budget_leaves_a_large_one_out_with_its_reason",
          rows["QA Large"].get("revision_omitted", {}).get("reason") == "BUDGET", row=rows["QA Large"])
    check("a_small_mesh_that_hashes_another_is_not_small",
          not revision_budget.small(wrapped)
          and rows["QA Wrapped"].get("revision_omitted", {}).get("reason") == "BUDGET", row=rows["QA Wrapped"])
    for obj in (small, large, wrapped):
        obj.select_set(False)


def rule_based_selection():
    """A material on every upward face of a valley with more than 4,096 of them."""
    valley = grid("QA Valley", 6561, size=40.0, location=(0, 60, 0))  # 81 x 81 vertices: 6,400 upward faces.
    W.view_layer.update()
    handle = queries.object_handle(valley)
    program = {"mode": "APPLY", "read_set": [], "expected_checks": [],
               "write_set": [{"handle": handle, "revision": queries.object_revision(W, valley)}],
               "operations": [
                   {"kind": "material_principled", "target": handle,
                    "parameters": {"name": "QA Valley Earth", "inputs": {"Base Color": [.24, .12, .055, 1]}}},
                   {"kind": "material_principled", "target": handle,
                    "parameters": {"name": "QA Valley Grass", "inputs": {"Base Color": [.19, .23, .065, 1]},
                                   "face_selector": {"normal": [0, 0, 1], "normal_angle": .3}}},
                   {"kind": "transform", "target": handle, "parameters": {"location": [0, 61, 0]}}]}
    try:
        result = run(validate_call("angelo_scene_program", program))
        status, error = result.get("status"), result.get("error")
    except Exception as error_:  # noqa: BLE001  (recorded as the check's evidence)
        status, error = None, repr(error_)
    slots = [slot.material.name if slot.material else None for slot in valley.material_slots]
    grass = slots.index("QA Valley Grass") if "QA Valley Grass" in slots else -1
    on_grass = sum(1 for face in valley.data.polygons if face.material_index == grass)
    check("a_rule_based_selector_matches_past_4096_faces_and_commits",
          status == "committed" and on_grass == len(valley.data.polygons) == 6400,
          status=status, error=error, faces_on_grass=on_grass, slots=slots)


def typed_world_facts():
    """A typed Sky sun and a Sun light about 158 degrees apart, which neither result used to compare. A committed
    program that changes the World or a Sun light now states both, as a script's result does."""
    scene = W.scene
    scene_handle = scene_settings.scene_handle(scene)
    light = {"kind": "light_create", "parameters": {"name": "QA Sun", "alias": "sun", "location": [0, -10, 10],
                                                    "look_at": [0, 0, 0],  # Shines toward +Y: it stands at bearing 180.
                                                    "settings": {"light_type": "SUN", "energy": 3}}}
    created = run(validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [],
                                                         "expected_checks": [], "operations": [light]}))
    world = created.get("world") or {}
    check("a_typed_sun_light_states_the_sun_lights", created.get("status") == "committed"
          and any(row.get("name") == "QA Sun" for row in world.get("sun_lights") or ()), world=world)
    sky = {"kind": "world_configure", "target": scene_handle, "parameters": {"strength": .5, "sky": {
        "type": "MULTIPLE_SCATTERING", "sun_elevation": .35, "sun_rotation": 0.0}}}  # Bearing 0: due +Y.
    configured = run(validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "expected_checks": [], "operations": [sky],
        "write_set": [{"handle": scene_handle, "revision": scene_settings.settings_revision(scene)}]}))
    world = configured.get("world") or {}
    rows = [row for row in world.get("sun_lights") or () if row.get("name") == "QA Sun"]
    check("a_typed_sky_states_how_far_its_sun_is_from_each_sun_light",
          configured.get("status") == "committed" and world.get("shows") == "SKY" and rows
          and isinstance(rows[0].get("from_sky_deg"), (int, float)) and rows[0]["from_sky_deg"] > 90,
          world=world, error=configured.get("error"))
    holder = grid("QA Unlit Holder", 100, location=(0, -60, 0))
    W.view_layer.update()
    moved = run(validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "expected_checks": [],
        "write_set": [{"handle": queries.object_handle(holder), "revision": queries.object_revision(W, holder)}],
        "operations": [{"kind": "transform", "target": queries.object_handle(holder),
                        "parameters": {"location": [0, -61, 0]}}]}))
    check("a_program_that_touches_neither_reports_no_world",
          moved.get("status") == "committed" and "world" not in moved, keys=sorted(moved))


def script_bounds():
    """A script that adds a 200 m ground for a 16 m scene: its result used to give counts, not size."""
    code = ("import bpy\n"
            "m = bpy.data.meshes.new('QA Wide Ground')\n"
            "m.from_pydata([(-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0)], [], [(0, 1, 2, 3)])\n"
            "o = bpy.data.objects.new('QA Wide Ground', m)\n"
            "bpy.context.scene.collection.objects.link(o)\n"
            "o.location = (5, 0, -1)\n"
            "o.scale = (0.5, 0.5, 1)\n"
            "result = {'added': o.name}\n")
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "Add a ground", "code": code})
    rows = {row.get("name"): row for row in (result.get("after") or {}).get("objects") or ()}
    bounds = (rows.get("QA Wide Ground") or {}).get("bounds")
    check("a_script_result_states_the_world_bounds_of_what_it_added",
          bounds == [[-45.0, -50.0, -1.0], [55.0, 50.0, -1.0]], bounds=bounds, row=rows.get("QA Wide Ground"))
    code = ("import bpy\n"
            "m = bpy.data.objects['QA Wide Ground'].data\n"
            "for v in m.vertices:\n"
            "    v.co.x *= 0.08\n"
            "    v.co.y *= 0.08\n"
            "bpy.data.objects['QA Wide Ground'].modifiers.new('Solid', 'SOLIDIFY').thickness = 2.0\n"
            "result = {'trimmed': True}\n")
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "Trim the ground", "code": code})
    rows = {row.get("name"): row for row in (result.get("after") or {}).get("objects") or ()}
    bounds = (rows.get("QA Wide Ground") or {}).get("bounds")
    code = ("import bpy\n"
            "o = bpy.data.objects.new('QA Procedural Ground', bpy.data.meshes.new('QA Procedural Ground'))\n"
            "bpy.context.scene.collection.objects.link(o)\n"
            "tree = bpy.data.node_groups.new('QA Ground Nodes', 'GeometryNodeTree')\n"
            "tree.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')\n"
            "grid = tree.nodes.new('GeometryNodeMeshGrid')\n"
            "grid.inputs['Size X'].default_value, grid.inputs['Size Y'].default_value = 20.0, 16.0\n"
            "out = tree.nodes.new('NodeGroupOutput')\n"
            "tree.links.new(grid.outputs['Mesh'], out.inputs[0])\n"
            "o.modifiers.new('Ground', 'NODES').node_group = tree\n"
            "o.location.z = -1.0\n"
            "result = {'added': o.name}\n")
    made = BlenderExecutor(W).execute("angelo_execute_python", {"description": "Procedural ground", "code": code})
    made_rows = {row.get("name"): row for row in (made.get("after") or {}).get("objects") or ()}
    check("a_geometry_nodes_object_on_an_empty_mesh_states_its_bounds",  # The usual procedural pattern.
          (made_rows.get("QA Procedural Ground") or {}).get("bounds") == [[-10.0, -8.0, -1.0], [10.0, 8.0, -1.0]],
          row=made_rows.get("QA Procedural Ground"), error=made.get("error"))
    check("a_script_that_reshapes_a_mesh_states_its_evaluated_bounds",  # The new size, the Solidify included.
          bounds is not None and bounds[0][:2] == [1.0, -4.0] and bounds[1][:2] == [9.0, 4.0]
          and abs((bounds[1][2] - bounds[0][2]) - 2.0) < 1e-3, bounds=bounds)


def compact_results():
    """A batch of copies, whose result a model now reads as one row per object."""
    from angelo_agent import model_results
    program = {"mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [], "operations": [
        {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "QA Post", "alias": "post",
                                                    "location": [0, -40, 1], "scale": [.05, .05, .5]}},
        {"kind": "batch", "parameters": {"sources": ["$post"], "array": [{"step": [1.5, 0, 0], "count": 30}]}}]}
    raw = run(validate_call("angelo_scene_program", program))
    shown = model_results.model_result("angelo_scene_program", raw)
    rows = (shown.get("live_revisions") or {}).get("objects") or []
    size = lambda value: len(json.dumps(value, separators=(",", ":")))  # noqa: E731
    first = next((row for row in rows if row.get("name") == "QA Post"), {})
    check("a_batch_result_reads_as_one_row_per_object_with_bounds",
          raw.get("status") == "committed" and len(rows) == 30 and all("bounds" in row for row in rows)
          and first.get("bounds") == [[-0.05, -40.05, 0.5], [0.05, -39.95, 1.5]]
          and "geometry" not in (shown.get("delta") or {}) and size(shown) < size(raw) * 0.6,
          rows=len(rows), first=first, raw_bytes=size(raw), shown_bytes=size(shown))


def review_fixes():
    """Adversarial reproductions, in the compiled app."""
    import tempfile
    from angelo_agent import model_results, world_surface
    from angelo_agent.program_jobs import ProgramError
    # A full temporary folder while the commit reads the World's brightness: the commit stands, the World too.
    scene = W.scene
    scene_handle = scene_settings.scene_handle(scene)
    real = tempfile.TemporaryDirectory
    tempfile.TemporaryDirectory = lambda *args, **kwargs: (_ for _ in ()).throw(OSError(28, "No space left on device"))
    try:
        result = run(validate_call("angelo_scene_program", {
            "mode": "APPLY", "read_set": [], "expected_checks": [],
            "write_set": [{"handle": scene_handle, "revision": scene_settings.settings_revision(scene)}],
            "operations": [{"kind": "world_configure", "target": scene_handle,
                            "parameters": {"color": [.2, .3, .4], "strength": .7}}]}))
    finally:
        tempfile.TemporaryDirectory = real
    check("a_failing_brightness_reading_never_fails_the_commit",
          result.get("status") == "committed" and scene.world is not None
          and abs(scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value - .7) < 1e-6,
          status=result.get("status"), error=result.get("error"), world=(result.get("world") or {}).get("shows"))
    # Two inputs swap names: each object's compact row keeps its own size.
    old = grid("QA Wall", 9, size=1.0, location=(0, 90, 0))
    new = grid("QA Wall_new", 400, size=8.0, location=(10, 90, 0))
    W.view_layer.update()
    def entry(obj):
        return {"handle": queries.object_handle(obj), "revision": queries.object_revision(W, obj)}
    raw = run(validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "expected_checks": [], "write_set": [entry(old), entry(new)],
        "operations": [{"kind": "object_rename", "target": entry(old)["handle"], "parameters": {"name": "QA Wall_old"}},
                       {"kind": "object_rename", "target": entry(new)["handle"], "parameters": {"name": "QA Wall"}}]}))
    rows = {row["name"]: row for row in (model_results.model_result("angelo_scene_program", raw).get("live_revisions")
                                         or {}).get("objects") or ()}
    check("a_rename_never_moves_one_objects_size_to_another",
          raw.get("status") == "committed" and rows.get("QA Wall", {}).get("vertices") == 400
          and rows.get("QA Wall_old", {}).get("vertices") == 9, rows=rows, error=raw.get("error"))
    # Lights hidden for render, through their collection or the view layer, light nothing.
    hidden = bpy.data.collections.new("QA Hidden Lights")
    scene.collection.children.link(hidden)
    hidden.hide_render = True
    lamp = bpy.data.objects.new("QA Hidden Sun", bpy.data.lights.new("QA Hidden Sun", "SUN"))
    hidden.objects.link(lamp)
    lit = world_surface.lighting(scene)
    check("lighting_lists_only_lights_that_render",
          all(row.get("name") != "QA Hidden Sun" for row in lit.get("sun_lights") or ()), lighting=lit)
    # A modifier's object outside the bound scene: the refusal says so, with what can edit it.
    cutter = bpy.data.objects.new("QA Loose Cutter", bpy.data.meshes.new("QA Loose Cutter"))
    target = grid("QA Cut Target", 9, location=(0, 120, 0))
    target.modifiers.new("Cut", "BOOLEAN").object = cutter
    W.view_layer.update()
    try:
        refused = run(validate_call("angelo_scene_program", {
            "mode": "APPLY", "read_set": [], "expected_checks": [], "write_set": [entry(target)],
            "operations": [{"kind": "transform", "target": entry(target)["handle"],
                            "parameters": {"location": [0, 121, 0]}}]}))
        text = refused.get("error") or ""
    except ProgramError as error:
        text = str(error)
    check("a_dependency_outside_the_scene_is_named_as_such", "is not in the bound scene" in text
          and "angelo_execute_python" in text, error=text)


try:
    revision_costs()
    rule_based_selection()
    typed_world_facts()
    script_bounds()
    compact_results()
    review_fixes()
    script_read_back()
    spent_turn_context()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "rc10-gaps.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("RC10_GAPS failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
