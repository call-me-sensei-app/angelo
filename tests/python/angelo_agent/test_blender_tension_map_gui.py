# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tension map in the running app, in Director (1.0.9): the + menu's one click, the card and the overlay.

Real default startup (Director). A posed character (a bundled Human Base Mesh body on a simple armature, arms raised
and bent, spine bent) and a cloth hammock (pinned at two corners, simulated frame by frame). The card is answered
with simulated clicks at its drawn rectangles.

Covered, each with a screenshot: the + menu lists Tension map; its operator adds the modifier after the Armature,
turns the overlay on and opens the non-modal corner card; the character shows stretch and compression around the
bent joints; a click on a range segment changes the overlay; a new pose changes the values live; Material Preview
keeps the overlay and EEVEE reads the attribute through an Attribute node; the cloth stretches near its pins while
it simulates; Remove takes the modifiers off and hides the map, and one Undo brings them back. Receipt:
``tension-map-gui.json``.

Run through ``run_native_suites.py --gui --include test_blender_tension_map_gui``.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Quaternion, Vector

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-tension-map-qa"))
OUT.mkdir(parents=True, exist_ok=True)
for name, folder in (("ANGELO_STATE_DIR", "state"), ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library")):
    os.environ.setdefault(name, str(OUT / folder))   # run_native_suites sets them before the add-on starts.
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
REPORT = OUT / "tension-map-gui.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": []}
checks, details = state["checks"], state["details"]
started = time.monotonic()
phase_started = {}
BODY = "Body Male - Stylized"
CLOTH_FRAMES = 36


def log(*parts):
    print("ANGELO_TENSION_MAP_QA", *parts, flush=True)


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def wait(phase, ready, limit=20.0):
    phase_started.setdefault(phase, time.monotonic())
    if ready():
        return True
    if time.monotonic() - phase_started[phase] > limit:
        raise TimeoutError(f"{phase}: timed out after {limit:.0f} s")
    return False


def main_window():
    return bpy.context.window_manager.windows[0]


def view3d():
    area = max((a for a in main_window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def override():
    area, region = view3d()
    return bpy.context.temp_override(window=main_window(), area=area, region=region)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=main_window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def card():
    from angelo_agent import card_host
    shown = card_host.active_card()
    return shown if shown is not None and shown.kind == "tension_map" else None


def hit(action, value=None):
    from angelo_agent import card_host
    plan = card_host._active.plan if card_host._active else None
    if plan is None:
        return None
    _area, region = view3d()
    rect = next((r for r, a, v in plan.hits if a == action and (value is None or v == value)), None)
    return (region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2) if rect else None


def click(xy):
    window = main_window()
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(xy[0]), y=int(xy[1]))
        window.event_simulate(type='LEFTMOUSE', value=value, x=int(xy[0]), y=int(xy[1]))


def space():
    return view3d()[0].spaces.active


def select_only(objects, active):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in objects)
    bpy.context.view_layer.objects.active = active


def frame_on(objects):
    select_only(objects, objects[0])
    with override():
        bpy.ops.view3d.view_selected()


def tension(obj, name="tension"):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return []
    found = [0.0] * len(attribute.data)
    attribute.data.foreach_get("value", found)
    return found


# -- the posed character --------------------------------------------------------------------------------------------
def load_body():
    from angelo_agent import asset_start
    row = asset_start.asset(BODY)
    path = asset_start.asset_path(row)
    kind = "collections" if row["datablock"] == "COLLECTION" else "objects"
    with bpy.data.libraries.load(path, link=False) as (source, target):
        setattr(target, kind, [row["datablock_name"]])
    loaded = getattr(target, kind)[0]
    objects = list(loaded.all_objects) if kind == "collections" else [loaded]
    for obj in objects:
        if obj.name not in bpy.context.scene.collection.all_objects:
            bpy.context.scene.collection.objects.link(obj)
    return max((o for o in objects if o.type == 'MESH'), key=lambda o: len(o.data.vertices))


def rig_body(body):
    """A test fixture rig: bones placed from the body's own bounds and hands (no particular asset's numbers), soft
    weights by distance to each bone. Returns the armature."""
    bpy.context.view_layer.update()   # A freshly linked object's world matrix is current only after an update.
    points = [body.matrix_world @ v.co for v in body.data.vertices]
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    height, mid_y = high.z - low.z, (low.y + high.y) / 2
    at = lambda x, fraction: Vector((x, mid_y, low.z + fraction * height))  # noqa: E731
    bones = {"Hips": (at(0, 0.52), at(0, 0.46), None), "Spine": (at(0, 0.52), at(0, 0.7), None),
             "Chest": (at(0, 0.7), at(0, 0.84), "Spine"), "Head": (at(0, 0.84), at(0, 1.0), "Chest")}
    for side, sign in (("L", 1), ("R", -1)):
        hand = max(points, key=lambda p: sign * p.x)
        shoulder = at(sign * max(0.1 * height, 0.3 * abs(hand.x)), 0.81)
        wrist = shoulder.lerp(hand, 0.9)
        elbow = shoulder.lerp(wrist, 0.5)
        bones["UpperArm." + side] = (shoulder, elbow, "Chest")
        bones["Forearm." + side] = (elbow, wrist, "UpperArm." + side)
        bones["Hand." + side] = (wrist, hand, "Forearm." + side)
        for name, (top, bottom) in (("Thigh", (0.48, 0.27)), ("Shin", (0.27, 0.04))):
            bones[name + "." + side] = (at(sign * 0.09 * height, top), at(sign * 0.09 * height, bottom), "Hips")
    rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    bpy.context.scene.collection.objects.link(rig)
    select_only([rig], rig)
    with override():
        bpy.ops.object.mode_set(mode='EDIT')
        for name, (head, tail, parent) in bones.items():
            edit = rig.data.edit_bones.new(name)
            edit.head, edit.tail = head, tail
            if parent:
                edit.parent = rig.data.edit_bones[parent]
        bpy.ops.object.mode_set(mode='OBJECT')
    segments = {name: (head, tail) for name, (head, tail, _parent) in bones.items()}
    groups = {name: body.vertex_groups.new(name=name) for name in segments}
    sigma = 0.045 * height
    for vertex, point in zip(body.data.vertices, points):
        weights = {}
        for name, (a, b) in segments.items():
            ab = b - a
            t = max(0.0, min(1.0, (point - a).dot(ab) / max(ab.length_squared, 1e-12)))
            weights[name] = math.exp(-((point - (a + t * ab)).length / sigma) ** 2)
        total = sum(weights.values()) or 1.0
        for name, weight in weights.items():
            if weight / total > 0.01:
                groups[name].add([vertex.index], weight / total, 'REPLACE')
    body.modifiers.new("Armature", 'ARMATURE').object = rig
    body.modifiers.move(len(body.modifiers) - 1, 0)   # First, as a rig's deform usually is.
    return rig


def pose(rig, name, axis, degrees):
    """Rotate ``name`` about an armature-space ``axis`` (the bone's own rest frame takes it)."""
    bone = rig.pose.bones[name]
    local = rig.data.bones[name].matrix_local.to_3x3().inverted() @ Vector(axis)
    bone.rotation_mode = 'QUATERNION'
    bone.rotation_quaternion = Quaternion(local.normalized(), math.radians(degrees))


def strike_pose(rig, amount=1.0):
    pose(rig, "UpperArm.L", (0, 1, 0), -55 * amount)
    pose(rig, "Forearm.L", (0, 1, 0), -70 * amount)
    pose(rig, "UpperArm.R", (1, 0, 0), 60 * amount)
    pose(rig, "Forearm.R", (1, 0, 0), 80 * amount)
    pose(rig, "Spine", (1, 0, 0), 22 * amount)
    pose(rig, "Thigh.L", (1, 0, 0), 45 * amount)
    pose(rig, "Shin.L", (1, 0, 0), -60 * amount)


# -- the cloth ------------------------------------------------------------------------------------------------------
def make_cloth(offset):
    mesh = bpy.data.meshes.new("Hammock")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=40, y_segments=40, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    cloth = bpy.data.objects.new("Hammock", mesh)
    cloth.location = offset
    bpy.context.scene.collection.objects.link(cloth)
    pins = cloth.vertex_groups.new(name="Pins")
    corners = [v.index for v in mesh.vertices if abs(v.co.y) > 0.97 and abs(abs(v.co.x) - 1.0) < 0.06]
    pins.add(corners, 1.0, 'REPLACE')
    modifier = cloth.modifiers.new("Cloth", 'CLOTH')
    modifier.settings.vertex_group_mass = "Pins"
    modifier.settings.quality = 6
    modifier.settings.tension_stiffness = modifier.settings.compression_stiffness = 2.0   # A stretchy knit.
    modifier.point_cache.frame_end = CLOTH_FRAMES + 10
    return cloth


def eevee_reads_the_attribute():
    """A plane stretched on one half, emission = tension, rendered with EEVEE: the stretched half is brighter."""
    scene = bpy.data.scenes.new("EeveeCheck")
    mesh = bpy.data.meshes.new("Shaded")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=16, y_segments=16, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    plane = bpy.data.objects.new("Shaded", mesh)
    scene.collection.objects.link(plane)
    plane.shape_key_add(name="Basis")
    key = plane.shape_key_add(name="Half", from_mix=False)
    for point, vertex in zip(key.data, mesh.vertices):
        point.co = Vector((vertex.co.x * 2.0 if vertex.co.x > 0 else vertex.co.x, vertex.co.y, 0.0))
    key.value = 1.0
    plane.modifiers.new("Tension", 'TENSION')
    material = bpy.data.materials.new("TensionShaded")
    tree = material.node_tree
    tree.nodes.clear()
    attribute = tree.nodes.new("ShaderNodeAttribute")
    attribute.attribute_name = "tension"
    emission = tree.nodes.new("ShaderNodeEmission")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(attribute.outputs["Fac"], emission.inputs["Strength"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    mesh.materials.append(material)
    camera = bpy.data.objects.new("EeveeCamera", bpy.data.cameras.new("EeveeCamera"))
    camera.data.type, camera.data.ortho_scale, camera.location = 'ORTHO', 4.5, (0.5, 0, 5)
    scene.collection.objects.link(camera)
    scene.camera = camera
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 64, 32
    path = OUT / "eevee-tension.png"
    scene.render.filepath = str(path)
    with bpy.context.temp_override(window=main_window(), scene=scene):
        bpy.ops.render.render(write_still=True, scene=scene.name)
    image = bpy.data.images.load(str(path))
    pixels, (width, height) = list(image.pixels), image.size
    row = height // 2

    def brightness(x):
        index = (row * width + x) * 4
        return sum(pixels[index:index + 3])
    left, right = brightness(width // 5), brightness(width * 4 // 5)
    bpy.data.scenes.remove(scene)
    return right > left + 0.2, {"left": round(left, 3), "right": round(right, 3)}


def draw_cost():
    """Observed, not checked: the main window's redraw on a dense deforming mesh with the map off and on (median of
    9 frames each, the shape key changing every frame so the map is re-extracted). The slot holds Mac 1's lock."""
    mesh = bpy.data.meshes.new("DenseDraw")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=600, y_segments=600, size=2.0)
    bm.to_mesh(mesh)
    bm.free()
    dense = bpy.data.objects.new("DenseDraw", mesh)
    bpy.context.scene.collection.objects.link(dense)
    dense.location = (0.0, -6.0, 0.0)
    dense.shape_key_add(name="Basis")
    key = dense.shape_key_add(name="Ripple", from_mix=False)
    for point, vertex in zip(key.data, mesh.vertices):
        point.co = Vector((vertex.co.x * 1.15, vertex.co.y, 0.1 * math.sin(6.0 * vertex.co.x)))
    dense.modifiers.new("Tension", 'TENSION')
    frame_on([dense])
    timings = {}
    for label, on in (("off", False), ("on", True), ("off_again", False)):
        space().overlay.show_tension = on
        samples = []
        for step in range(10):
            key.value = 0.3 + 0.05 * step
            started = time.perf_counter()
            bpy.context.view_layer.update()
            with bpy.context.temp_override(window=main_window()):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            if step:   # The first frame of each arm warms up.
                samples.append(1000.0 * (time.perf_counter() - started))
        samples.sort()
        timings[label] = round(samples[len(samples) // 2], 2)
    timings["vertices"] = len(mesh.vertices)
    return timings


def finish(error=None):
    if error is not None:
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        log(state["traceback"])
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    log(json.dumps({"phase": state["phase"], "success": state["success"],
                    "failed": [k for k, v in checks.items() if not v]}))
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import chat_destinations, director
    phase = state["phase"]
    try:
        if phase == "boot":
            import addon_utils
            if not addon_utils.check("angelo_agent")[1]:
                addon_utils.enable("angelo_agent", default_set=False, persistent=False)
            state["phase"] = "settle"
            return .5
        if phase == "settle":
            if not wait(phase, lambda: qa_boot.started() and any(a.type == 'VIEW_3D' for a in main_window().screen.areas),
                        limit=60):
                state["waiting_on"] = qa_boot.waiting_on()
                return .3
            check("runs_in_director", director.is_director(main_window()))
            feature = chat_destinations.feature("tension_map")
            check("plus_menu_lists_tension_map", feature is not None and feature.operator == "angelo.tension_map")
            for obj in list(bpy.context.scene.objects):
                if obj.type == 'MESH':
                    bpy.data.objects.remove(obj)
            body = load_body()
            rig = rig_body(body)
            strike_pose(rig)
            state.update(body=body.name, rig=rig.name)
            check("overlay_starts_off", not space().overlay.show_tension)
            frame_on([body])
            select_only([body], body)
            state["phase"] = "one_click"
            return .5
        body = bpy.data.objects.get(state.get("body", ""))
        rig = bpy.data.objects.get(state.get("rig", ""))
        if phase == "one_click":
            with override():   # What the + menu row runs (INVOKE_DEFAULT on its operator).
                bpy.ops.angelo.tension_map('INVOKE_DEFAULT')
            state["phase"] = "card"
            return .4
        if phase == "card":
            if not wait(phase, lambda: card() is not None and hit("stretch") is not None):
                return .2
            check("card_is_non_modal", not card().modal)
            types = [m.type for m in body.modifiers]
            check("modifier_added_after_the_armature",
                  'TENSION' in types and types.index('TENSION') == types.index('ARMATURE') + 1, types)
            check("overlay_turned_on", space().overlay.show_tension)
            values = tension(body)
            check("posed_character_stretches_and_compresses",
                  values and max(values) > 0.05 and min(values) < -0.05,
                  {"min": round(min(values), 4) if values else None, "max": round(max(values), 4) if values else None})
            capture("tension-1-posed-character.png")
            click(hit("stretch", 0.1))
            state["phase"] = "ranged"
            return .4
        if phase == "ranged":
            if not wait(phase, lambda: abs(space().overlay.tension_stretch_max - 0.1) < 1e-4, limit=5):
                return .2
            check("a_range_click_changes_the_overlay", True)
            click(hit("compress", 0.1))
            capture("tension-2-range-10-percent.png")
            state["values"] = tension(body)
            strike_pose(rig, 1.6)
            state["phase"] = "repose"
            return .4
        if phase == "repose":
            bpy.context.view_layer.update()
            after = tension(body)
            check("a_new_pose_updates_the_values_live",
                  after and state["values"] and max(abs(a - b) for a, b in zip(after, state["values"])) > 0.02)
            capture("tension-3-new-pose.png")
            space().shading.type = 'MATERIAL'
            state["phase"] = "material_preview"
            return 2.0
        if phase == "material_preview":
            capture("tension-4-material-preview.png")
            space().shading.type = 'SOLID'
            ok, readings = eevee_reads_the_attribute()
            check("eevee_reads_the_tension_attribute", ok, readings)
            cloth = make_cloth(body.location + Vector((3.0, 0.0, 1.6)))
            state.update(cloth=cloth.name, frame=1)
            bpy.context.scene.frame_set(1)
            frame_on([cloth])
            select_only([cloth], cloth)
            with override():
                bpy.ops.angelo.tension_map('INVOKE_DEFAULT')
            state["phase"] = "simulate"
            return .4
        cloth = bpy.data.objects.get(state.get("cloth", ""))
        if phase == "simulate":
            if state["frame"] < CLOTH_FRAMES:
                state["frame"] += 1
                bpy.context.scene.frame_set(state["frame"])
                if state["frame"] == CLOTH_FRAMES // 3:
                    capture("tension-5-cloth-falling.png")
                return .05
            check("cloth_card_measures_the_cloth", card() is not None and card().source.snapshot().names == (cloth.name,))
            check("cloth_measured_after_the_simulation", [m.type for m in cloth.modifiers] == ['CLOTH', 'TENSION'])
            values = tension(cloth)
            check("cloth_stretches_near_its_pins", values and max(values) > 0.01,
                  {"min": round(min(values), 4) if values else None, "max": round(max(values), 4) if values else None})
            area, region = view3d()
            with override():
                bpy.ops.view3d.view_selected()
                bpy.ops.view3d.zoom(delta=-2)
            click(hit("stretch", 0.05))
            state["phase"] = "cloth_shot"
            return .4
        if phase == "cloth_shot":
            check("cloth_range_click_applies", abs(space().overlay.tension_stretch_max - 0.05) < 1e-4)
            capture("tension-6-cloth.png")
            state["phase"] = "remove"
            return .3
        if phase == "remove":
            if not wait(phase, lambda: hit("remove") is not None):
                return .2
            click(hit("remove"))
            state["phase"] = "removed"
            return .4
        if phase == "removed":
            if not wait(phase, lambda: card() is None, limit=5):
                return .2
            check("remove_takes_the_modifier_off_and_hides_the_map",
                  not any(m.type == 'TENSION' for m in cloth.modifiers) and not space().overlay.show_tension)
            with override():
                bpy.ops.ed.undo()
            cloth = bpy.data.objects.get(state["cloth"])
            check("one_undo_brings_it_back", cloth is not None and any(m.type == 'TENSION' for m in cloth.modifiers),
                  {"cloth": cloth is not None, "modifiers": [m.type for m in cloth.modifiers] if cloth else None})
            state["phase"] = "draw_cost"
            return .3
        if phase == "draw_cost":
            details["draw_ms_dense_mesh"] = draw_cost()
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
