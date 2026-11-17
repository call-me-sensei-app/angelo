# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""App-level guards for two defects the 2.0.0 upstream picks brought in (external audit, 2026-11-15):

- F1, transform_convert.cc (pick aba753750796, upstream 4250b61164e): the Grease Pencil Draw-mode restriction read
  the active object's mode with no active object. A compositor node or a sequencer strip moved in a scene with no
  objects crashed. Here: both moves in an objectless scene, then the intended case, an active Grease Pencil object in
  Draw mode with a second one selected, whose transform must leave the other object alone.
- F2, image.cc BKE_image_is_float (pick 7eb8cd0aba98, upstream for #34549): on a failed buffer load it released the
  image with the address of the lock instead of the lock, releasing a bogus render result and unlocking the viewer
  lock it never took. Here: Project Edit (paint.image_from_view) with a canvas image whose file is missing, then the
  viewer lock used many times (a corrupted lock hangs or crashes there).

A window is required: ``run_native_suites.py --gui --include test_blender_upstream_fix_guards.py``.
"""

import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-upstream-fix-guards"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "facts": {}}


def check(name, value):
    REPORT["checks"][name] = bool(value)
    assert value, name


def area_override(space_type, ui_type=None):
    """Turn the 3D viewport's area into the given editor and return an override for its window region."""
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas), key=lambda a: a.width * a.height)
    area.type = space_type
    if ui_type is not None:
        area.ui_type = ui_type
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=window, area=area, region=region)


def objectless_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    check("no_objects", len(bpy.data.objects) == 0 and bpy.context.view_layer.objects.active is None)


def compositor_node_moves():
    REPORT["phase"] = "F1 compositor"
    scene = bpy.context.scene
    tree = bpy.data.node_groups.new("Guard Compositor", "CompositorNodeTree")
    scene.compositing_node_group = tree
    node = tree.nodes.new("CompositorNodeRGB")
    node.location = (0.0, 0.0)
    node.select = True
    tree.nodes.active = node
    with area_override('NODE_EDITOR', 'CompositorNodeTree'):
        space = bpy.context.space_data
        space.node_tree = tree
        bpy.ops.transform.translate(value=(40.0, 0.0, 0.0))
    REPORT["facts"]["compositor_node_location"] = tuple(node.location)
    check("compositor_node_moved_without_active_object", node.location.x > 0.0)


def sequencer_strip_moves():
    REPORT["phase"] = "F1 sequencer"
    scene = bpy.context.scene
    bpy.context.workspace.sequencer_scene = scene   # Blender 5.x: the sequencer edits its workspace's scene.
    editor = scene.sequence_editor_create()
    strip = editor.strips.new_effect("Guard Color", 'COLOR', channel=1, frame_start=1, length=10)
    for other in editor.strips_all:
        other.select = other == strip
    editor.active_strip = strip
    with area_override('SEQUENCE_EDITOR'):
        bpy.context.space_data.view_type = 'SEQUENCER'
        bpy.ops.transform.seq_slide(value=(5.0, 0.0))
    REPORT["facts"]["strip_frame_start"] = strip.frame_start
    check("sequencer_strip_moved_without_active_object", strip.frame_start == 6)


def grease_pencil_draw_mode_moves_only_the_active():
    REPORT["phase"] = "F1 grease pencil"
    with area_override('VIEW_3D'):
        bpy.ops.object.grease_pencil_add(type='STROKE', location=(0.0, 0.0, 0.0))
        other = bpy.context.object
        bpy.ops.object.grease_pencil_add(type='STROKE', location=(3.0, 0.0, 0.0))
        active = bpy.context.object
        other.select_set(True)
        active.select_set(True)
        bpy.context.view_layer.objects.active = active
        before = [tuple(p.position) for p in other.data.layers[0].current_frame().drawing.attributes["position"].data]
        bpy.ops.object.mode_set(mode='PAINT_GREASE_PENCIL')
        try:
            bpy.ops.transform.translate(value=(1.0, 0.0, 0.0))
            REPORT["facts"]["draw_mode_translate"] = "ran"
        except RuntimeError as error:   # Not every build polls a transform in Draw mode; the guard is what matters.
            REPORT["facts"]["draw_mode_translate"] = "refused: %s" % error
        bpy.ops.object.mode_set(mode='OBJECT')
        after = [tuple(p.position) for p in other.data.layers[0].current_frame().drawing.attributes["position"].data]
    check("draw_mode_transform_leaves_the_other_object", before == after)


def project_edit_with_missing_canvas():
    REPORT["phase"] = "F2 project edit"
    scene = bpy.context.scene
    with area_override('VIEW_3D'):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        image = bpy.data.images.new("Guard Missing Canvas", 8, 8)
        image.source = 'FILE'
        image.filepath = str(OUT / "does-not-exist" / "missing-canvas.png")
        image.reload()   # Unloads the generated buffer: the next acquisition reads the missing file.
        check("canvas_buffer_cannot_load", not image.has_data or image.size[0] == 0)
        paint = scene.tool_settings.image_paint
        paint.mode = 'IMAGE'
        paint.canvas = image
        bpy.context.view_layer.objects.active = cube
        target = OUT / "project-edit.png"
        result = bpy.ops.paint.image_from_view(filepath=str(target))
    REPORT["facts"]["image_from_view"] = sorted(result)
    check("project_edit_with_missing_canvas_finishes", result == {'FINISHED'})


def viewer_lock_still_works():
    """Every viewer-image acquisition takes and releases the viewer lock; a corrupted lock hangs or crashes here."""
    REPORT["phase"] = "F2 viewer lock"
    scene = bpy.context.scene
    tree = scene.compositing_node_group
    rgb = tree.nodes.new("CompositorNodeRGB")
    viewer = tree.nodes.new("CompositorNodeViewer")
    tree.links.new(rgb.outputs[0], viewer.inputs[0])
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 32, 32, 100
    camera = bpy.data.objects.new("Guard Camera", bpy.data.cameras.new("Guard Camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    reads = 0
    for _ in range(20):
        bpy.ops.render.render(write_still=False, use_viewport=False)
        viewer_image = bpy.data.images.get("Viewer Node")
        if viewer_image is not None and viewer_image.size[0] > 0:
            _ = viewer_image.pixels[0]
            reads += 1
    REPORT["facts"]["viewer_reads"] = reads
    check("viewer_lock_acquired_and_released_twenty_times", reads == 20)


def run():
    try:
        bpy.context.preferences.view.show_splash = False
        bpy.context.preferences.use_preferences_save = False
        objectless_scene()
        compositor_node_moves()
        sequencer_strip_moves()
        grease_pencil_draw_mode_moves_only_the_active()
        project_edit_with_missing_canvas()
        viewer_lock_still_works()
        REPORT["phase"] = "complete"
        REPORT["success"] = all(REPORT["checks"].values())
    except Exception:   # noqa: BLE001
        REPORT["error"] = traceback.format_exc()
        print(REPORT["error"], flush=True)
    (OUT / "upstream-fix-guards.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("UPSTREAM_FIX_GUARDS_DONE", REPORT["success"], REPORT["phase"], flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1.0)
