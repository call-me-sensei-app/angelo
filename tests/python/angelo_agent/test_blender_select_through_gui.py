# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Through (Blender #73479) in a real window: what box, lasso and circle selection reach.

The scene, seen from the front in an orthographic solid view: cube A (2 m) at the origin hides
cube B (1 m) behind it. With the toggle off a region selection reaches only what is visible (A's
front); with it on it reaches exactly what X-ray reaches (all of A and B), while the display stays
solid. Clicking still picks only visible elements. The same holds for the vertex selection mask of
Weight Paint and for Particle Edit. A user-assigned key toggles the property (none is bound by
default). Blender's selection counts include what was reached. Screenshots: the header with the
toggle on and the hidden selection showing faintly through, and the selection seen from an angle.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_input  # noqa: E402  (the dragged boxes)

# ANGELO_QA_REHEARSAL=1: the background rehearsal a new GUI suite gets before its first windowed slot. It runs the
# same setup and every selection, statistics and keymap call in background Blender (GPU initialised); only
# redraws, screenshots and synthetic key events, which need a window, are skipped and listed.
REHEARSAL = os.environ.get("ANGELO_QA_REHEARSAL") == "1"
assert "--factory-startup" in sys.argv and (REHEARSAL or not bpy.app.background)
if REHEARSAL:
    import gpu
    gpu.init()
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-select-through-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "observed": {}, "phase": "setup", "started": time.monotonic(),
         "rehearsal": REHEARSAL, "skipped_in_rehearsal": []}
checks, observed = state["checks"], state["observed"]
PENDING = {}  # Live Blender objects between timer phases (never written to the receipt).
bpy.context.preferences.view.show_splash = False


def window_check(name, passed):
    """A check that needs the depth Blender draws for a window (the stock visible-only path, clicks): in the
    background rehearsal it is listed, not asserted; in the window it is a check like any other."""
    if REHEARSAL:
        state["skipped_in_rehearsal"].append("depth-buffer check " + name)
        observed.setdefault("rehearsal_depth_checks", {})[name] = passed
    else:
        checks[name] = passed


def write():
    (OUT / "select-through-gui.json").write_text(json.dumps(state, indent=2))


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return window, area, region, area.spaces.active


def override():
    window, area, region, _space = view3d()
    return bpy.context.temp_override(window=window, area=area, region=region)


def redraw():
    if REHEARSAL:
        return
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def capture(name):
    if REHEARSAL:
        state["skipped_in_rehearsal"].append("screenshot " + name)
        return
    redraw()
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state.setdefault("screenshots", []).append(name)


def front_view(space):
    rv3d = space.region_3d
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Euler((math.radians(90), 0, 0)).to_quaternion()
    rv3d.view_location = (0, 2, 0)
    rv3d.view_distance = 9
    space.shading.type = 'SOLID'
    space.shading.show_xray = False


def scene_two_cubes():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        with override():
            bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.batch_remove(tuple(bpy.data.objects))
    objects = []
    for name, size, location in (("A", 2, (0, 0, 0)), ("B", 1, (0, 4, 0))):
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=size)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        obj.location = location
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)
    return objects


def screen_rect(region, space, objects, margin=20):
    points = [location_3d_to_region_2d(region, space.region_3d, obj.matrix_world @ Vector(corner))
              for obj in objects for corner in obj.bound_box]
    xs, ys = [p.x for p in points], [p.y for p in points]
    return int(min(xs)) - margin, int(max(xs)) + margin, int(min(ys)) - margin, int(max(ys)) + margin


def edit_counts(objects, domain):
    counts = {}
    for obj in objects:
        bm = bmesh.from_edit_mesh(obj.data)
        elements = {"VERT": bm.verts, "EDGE": bm.edges, "FACE": bm.faces}[domain]
        counts[obj.name] = sum(element.select for element in elements)
    return counts


def region_select(kind, rect, region):
    xmin, xmax, ymin, ymax = rect
    with override():
        if kind == "box":
            bpy.ops.view3d.select_box(xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax, mode='SET', wait_for_input=False)
        elif kind == "lasso":
            path = [{"name": "", "loc": loc, "time": 0.0}
                    for loc in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))]
            bpy.ops.view3d.select_lasso(path=path, mode='SET')
        else:
            radius = int(math.hypot(xmax - xmin, ymax - ymin) / 2) + 1
            bpy.ops.view3d.select_circle(x=(xmin + xmax) // 2, y=(ymin + ymax) // 2, radius=radius,
                                         wait_for_input=False, mode='SET')


def edit_mesh_cases():
    window, area, region, space = view3d()
    objects = scene_two_cubes()
    front_view(space)
    redraw()
    rect = screen_rect(region, space, objects)
    observed["rect"] = rect
    with override():
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.object.mode_set(mode='EDIT')
    results = {}
    for domain in ("VERT", "EDGE", "FACE"):
        bpy.context.tool_settings.mesh_select_mode = (domain == "VERT", domain == "EDGE", domain == "FACE")
        for kind in ("box", "lasso", "circle"):
            for setting in ("off", "on", "xray"):
                space.use_select_through = setting == "on"
                space.shading.show_xray = setting == "xray"
                redraw()
                with override():
                    bpy.ops.mesh.select_all(action='DESELECT')
                region_select(kind, rect, region)
                results[f"{domain}/{kind}/{setting}"] = edit_counts(objects, domain)
    space.shading.show_xray = False
    observed["edit_mesh"] = results
    totals = {"VERT": (8, 8), "EDGE": (12, 12), "FACE": (6, 6)}
    for domain, (a_total, b_total) in totals.items():
        for kind in ("box", "lasso", "circle"):
            off, on, xray = (results[f"{domain}/{kind}/{s}"] for s in ("off", "on", "xray"))
            checks[f"{domain.lower()}_{kind}_on_reaches_what_xray_reaches"] = on == xray
            checks[f"{domain.lower()}_{kind}_on_reaches_the_hidden_cube"] = on == {"A": a_total, "B": b_total}
            window_check(f"{domain.lower()}_{kind}_off_skips_the_hidden_cube", off["B"] == 0 and 0 < off["A"] < a_total)

    # Clicking stays visible-only with the toggle on: a click on B's corner (behind A's face) picks nothing of B.
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    space.use_select_through = True
    with override():
        bpy.ops.mesh.select_all(action='DESELECT')
        corner = objects[1].matrix_world @ Vector((-.5, -.5, -.5))
        point = location_3d_to_region_2d(region, space.region_3d, corner)
        bpy.ops.view3d.select(location=(int(point.x), int(point.y)), deselect_all=True)
    clicked = edit_counts(objects, "VERT")
    # Control: the same click with X-ray picks that hidden corner, so the click above did land.
    space.use_select_through = False
    space.shading.show_xray = True
    redraw()
    with override():
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.view3d.select(location=(int(point.x), int(point.y)), deselect_all=True)
    control = edit_counts(objects, "VERT")
    space.shading.show_xray = False
    space.use_select_through = True
    observed["click_on_hidden_corner"] = {"select_through": clicked, "xray_control": control}
    window_check("click_still_picks_only_visible_elements", clicked["B"] == 0 and control["B"] == 1)

    # Leave everything selected through, for the screenshots; the counts Blender shows include it.
    with override():
        bpy.ops.mesh.select_all(action='DESELECT')
    region_select("box", rect, region)
    statistics = bpy.context.scene.statistics(bpy.context.view_layer)
    observed["statistics_after_selecting_through"] = statistics
    checks["selection_counts_include_hidden_elements"] = "Verts:16/16" in statistics.replace(" ", "")
    return objects


def paint_mask_cases():
    window, area, region, space = view3d()
    objects = scene_two_cubes()
    cube = objects[0]
    front_view(space)
    with override():
        bpy.context.view_layer.objects.active = cube
        cube.select_set(True)
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    cube.data.use_paint_mask_vertex = True
    redraw()   # The region's matrices follow the new view only once it draws (the rectangle reads them).
    rect = screen_rect(region, space, [cube])
    counts = {}
    for kind in ("box", "lasso", "circle"):
        for setting in ("off", "on", "xray"):
            space.use_select_through = setting == "on"
            space.shading.show_xray = setting == "xray"
            redraw()
            cube.data.vertices.foreach_set("select", [False] * 8)
            cube.data.update()
            region_select(kind, rect, region)
            counts[f"{kind}/{setting}"] = sum(vertex.select for vertex in cube.data.vertices)
        checks[f"paint_vertex_mask_{kind}_on_reaches_what_xray_reaches"] = (
            counts[f"{kind}/on"] == counts[f"{kind}/xray"] == 8)
        window_check(f"paint_vertex_mask_{kind}_off_skips_hidden_vertices", 0 < counts[f"{kind}/off"] < 8)
    space.shading.show_xray = False
    space.use_select_through = False
    observed["weight_paint_vertex_mask"] = counts
    with override():
        bpy.ops.object.mode_set(mode='OBJECT')


def paint_mask_hidden_after_weld_case():
    """Hidden vertices stay out of a selection made through, on a mesh whose modifiers renumber its vertices. The
    evaluated mesh passes original indices (ORIGINDEX), so the hidden test must read the original mesh: here a Weld
    merges vertex 1 into vertex 0, shifting every later evaluated index by one."""
    window, area, region, space = view3d()
    objects = scene_two_cubes()
    bpy.data.objects.remove(objects[1])
    cube = objects[0]
    front_view(space)
    mesh = cube.data
    corners = [tuple(v.co) for v in mesh.vertices]
    faces = [tuple(i + (i > 0) for i in face.vertices) for face in mesh.polygons]  # Corner 0 stays 0, the rest + 1.
    mesh.clear_geometry()
    mesh.from_pydata([corners[0], corners[0], *corners[1:]], [], faces)  # Vertex 1: a loose twin of vertex 0.
    mesh.update()
    cube.modifiers.new("Weld", 'WELD').merge_threshold = .001
    hidden = {i for i in range(2, len(mesh.vertices)) if i % 2 == 0}
    for index in hidden:
        mesh.vertices[index].hide = True
    with override():
        bpy.context.view_layer.objects.active = cube
        cube.select_set(True)
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    mesh.use_paint_mask_vertex = True
    space.use_select_through = True
    redraw()
    rect = screen_rect(region, space, [cube], margin=40)
    result = {}
    for kind in ("box", "lasso", "circle"):
        mesh.vertices.foreach_set("select", [False] * len(mesh.vertices))
        mesh.update()
        region_select(kind, rect, region)
        selected = {v.index for v in mesh.vertices if v.select}
        result[kind] = sorted(selected)
        visible_corners = set(range(len(mesh.vertices))) - hidden - {1}
        checks[f"paint_vertex_mask_{kind}_through_skips_hidden_after_weld"] = not (selected & hidden)
        checks[f"paint_vertex_mask_{kind}_through_reaches_visible_after_weld"] = visible_corners <= selected
    observed["weld_hidden"] = {"hidden": sorted(hidden), "selected": result}
    space.use_select_through = False
    with override():
        bpy.ops.object.mode_set(mode='OBJECT')


def particle_cases():
    window, area, region, space = view3d()
    remaining = {}
    for kind, setting in ((kind, setting) for kind in ("box", "lasso", "circle") for setting in ("off", "on", "xray")):
        objects = scene_two_cubes()
        bpy.data.objects.remove(objects[1])
        cube = objects[0]
        front_view(space)
        system = cube.modifiers.new("Hair", 'PARTICLE_SYSTEM').particle_system
        system.settings.type = 'HAIR'
        system.settings.count = 300
        system.settings.hair_length = .3
        system.seed = 7
        with override():
            bpy.context.view_layer.objects.active = cube
            cube.select_set(True)
            bpy.ops.object.mode_set(mode='PARTICLE_EDIT')
            bpy.context.scene.tool_settings.particle_edit.select_mode = 'POINT'
        space.use_select_through = setting == "on"
        space.shading.show_xray = setting == "xray"
        redraw()
        rect = screen_rect(region, space, [cube], margin=60)
        with override():
            bpy.ops.particle.select_all(action='DESELECT')
        region_select(kind, rect, region)
        with override():
            bpy.ops.particle.delete(type='PARTICLE')
            bpy.ops.object.mode_set(mode='OBJECT')
        remaining[f"{kind}/{setting}"] = len(cube.particle_systems[0].particles)
    space.shading.show_xray = False
    space.use_select_through = False
    observed["particles_left_after_deleting_the_selection"] = remaining
    for kind in ("box", "lasso", "circle"):
        checks[f"particle_{kind}_on_reaches_what_xray_reaches"] = remaining[f"{kind}/on"] == remaining[f"{kind}/xray"]
        window_check(f"particle_{kind}_off_skips_hidden_hairs", remaining[f"{kind}/off"] > remaining[f"{kind}/on"])


def user_binding_case():
    window, area, region, space = view3d()
    keyconfigs = bpy.context.window_manager.keyconfigs
    default_bound = [(keymap.name, item.type) for keymap in keyconfigs.default.keymaps
                     for item in keymap.keymap_items
                     if "use_select_through" in str(getattr(item.properties, "data_path", "") or "")]
    observed["default_bindings"] = default_bound
    checks["no_default_binding"] = default_bound == [] and len(keyconfigs.default.keymaps) > 0
    keymap = keyconfigs.user.keymaps["3D View Generic"]
    item = keymap.keymap_items.new("wm.context_toggle", 'F12', 'PRESS', ctrl=True, alt=True, shift=True)
    item.properties.data_path = "space_data.use_select_through"
    before = space.use_select_through
    x, y = region.x + region.width // 2, region.y + region.height // 2
    if REHEARSAL:
        state["skipped_in_rehearsal"].append("simulated key press of the user binding")
        return keymap, item, None
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window.event_simulate(type='F12', value='PRESS', ctrl=True, alt=True, shift=True, x=x, y=y)
    window.event_simulate(type='F12', value='RELEASE', ctrl=True, alt=True, shift=True, x=x, y=y)
    # Simulated events are handled after this timer returns; user_binding_check reads the result.
    return keymap, item, before


def user_binding_check(keymap, item, before):
    space = view3d()[3]
    observed["user_binding_toggle"] = [before, space.use_select_through]
    if before is not None:
        checks["a_user_binding_toggles_it"] = space.use_select_through is (not before)
    # The window's event loop rebuilds the user keymaps (WM_keyconfig_update), so the item made in the previous tick
    # is looked up again rather than kept.
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps["3D View Generic"]
    for found in [i for i in keymap.keymap_items if i.idname == "wm.context_toggle"
                  and getattr(i.properties, "data_path", "") == "space_data.use_select_through"]:
        keymap.keymap_items.remove(found)
    space.use_select_through = False


def tick():
    try:
        if state["phase"] == "setup":
            PENDING["binding"] = user_binding_case()
            state["phase"] = "binding"
            return .5
        if state["phase"] == "binding":
            user_binding_check(*PENDING.pop("binding"))
            paint_mask_cases()
            paint_mask_hidden_after_weld_case()
            particle_cases()
            edit_mesh_cases()
            state["phase"] = "header"
            write()
            return .5
        if state["phase"] == "header":
            window, area, region, space = view3d()
            space.use_select_through = True
            # Front view: the hidden cube's selected vertices show faintly through the front cube's face.
            capture("select-through-header-on.png")
            space.region_3d.view_rotation = Euler((math.radians(65), 0, math.radians(35))).to_quaternion()
            state["phase"] = "angle"
            write()
            return .5
        if state["phase"] == "angle":
            capture("select-through-selection-from-an-angle.png")
            if not REHEARSAL:
                state["phase"], PENDING["drags"] = "drag", ["off", "on"]
                return .3
            state["phase"] = "drags_done"
        if state["phase"] == "drag":
            # The same box dragged with window events, as a person does, toggle off then on (front view, solid).
            window, area, region, space = view3d()
            setting = PENDING["drags"][0]
            space.use_select_through = setting == "on"
            front_view(space)
            redraw()
            with override():
                bpy.ops.mesh.select_all(action='DESELECT')
            xmin, xmax, ymin, ymax = screen_rect(region, space, [bpy.data.objects["A"], bpy.data.objects["B"]])
            qa_input.send(window, qa_input.drag_path((region.x + xmin, region.y + ymax), (region.x + xmax, region.y + ymin),
                                                     bpy.context.preferences.system.ui_scale))
            state["phase"] = "drag_read"
            return .5
        if state["phase"] == "drag_read":
            setting = PENDING["drags"].pop(0)
            found = edit_counts([bpy.data.objects["A"], bpy.data.objects["B"]], "VERT")
            # Recorded, not checked: simulated drags did not reach Blender's box select here in three Mac 1 runs (0 with
            # the toggle off and on, while the operator above reached 4 and 16); the Director scenario drags the box.
            observed[f"dragged_box_{setting}"] = found
            state["phase"] = "drag" if PENDING["drags"] else "drags_done"
            return .3
        if state["phase"] == "drags_done":
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            write()
            print("ANGELO_SELECT_THROUGH_GUI_OK" if state["success"] else "ANGELO_SELECT_THROUGH_GUI_FAILED",
                  json.dumps(sorted(k for k, v in checks.items() if not v)), flush=True)
            if os.environ.get("ANGELO_QA_KEEP_OPEN") != "1" and not REHEARSAL:
                bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        write()
        print(state["traceback"], flush=True)
        if not REHEARSAL:
            bpy.ops.wm.quit_blender()
        return None


if REHEARSAL:
    # No timers run in a background Blender: the phases run in order, now.
    while tick() is not None:
        pass
    assert state["phase"] == "complete" and state["success"], sorted(k for k, v in checks.items() if not v)
else:
    bpy.app.timers.register(tick, first_interval=1)
