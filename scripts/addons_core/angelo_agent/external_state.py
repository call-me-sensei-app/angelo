# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Angelo's window is doing, as a connected app reads it (``angelo_state``). Main thread, read-only.

The window's mode and active tool with its key settings, the selection, the workspace and its editors, the viewport's
shading, overlays and view, the scene, view layer, frame, range and playback, the active camera, the render engine
and output, running jobs, the recent Undo steps and whether the file has unsaved changes. Nothing here writes: every
value is read from ``bpy`` as it is now, so a read costs no model call and leaves nothing in the file. Never a file
path (the output's format and size, never where it goes; results are projected again by the service) and never an
account or key. Each section is read on its own: one Blender can't give is ``{"unavailable": reason}`` and the rest
still come. doc/angelo/external_mcp.rst, "State".
"""

from . import live_windows

MAX_NAMES = 50      # Selected object names listed; the count is always exact.
MAX_UNDO = 12       # Newest Undo steps listed, the active one marked.
MAX_AREAS = 24      # Editors of the window listed.
MAX_BOUNDS = 200    # Selected objects the selection's bounds are measured over (``bounds_partial`` beyond).
SECTIONS = ("mode", "tool", "selection", "workspace", "viewport", "scene", "render", "jobs", "undo", "file")


def _round(values, places=4):
    return [round(float(v), places) for v in values]


def _active(view_layer):
    return view_layer.objects.active if view_layer is not None else None


def mode_section(window, view_layer):
    obj = _active(view_layer)
    return {"mode": getattr(obj, "mode", None) or "OBJECT", "active_object": getattr(obj, "name", None)}


# Context mode -> the tool settings' paint settings that hold its brush and its unified size and strength (each paint
# mode's own ``unified_paint_settings``, rna_sculpt_paint.cc), as Blender's brush panels pick them
# (properties_paint_common.UnifiedPaintPanel._paint_settings).
PAINT_SETTINGS = {"SCULPT": "sculpt", "PAINT_VERTEX": "vertex_paint", "PAINT_WEIGHT": "weight_paint",
                  "PAINT_TEXTURE": "image_paint", "SCULPT_CURVES": "curves_sculpt", "PARTICLE": "particle_edit",
                  "PAINT_GREASE_PENCIL": "gpencil_paint", "SCULPT_GREASE_PENCIL": "gpencil_sculpt_paint",
                  "WEIGHT_GREASE_PENCIL": "gpencil_weight_paint", "VERTEX_GREASE_PENCIL": "gpencil_vertex_paint"}


def context_mode(bpy, scene, view_layer):
    """Blender's own context mode for this window's scene and view layer (``EDIT_TEXT``, ``EDIT_METABALL``,
    ``SCULPT_CURVES``, ...): the key its workspace tools are stored under. Read from Blender, never rebuilt from the
    object's type and mode (a font's ``EDIT_FONT`` is no context mode). The override only names where to read."""
    with bpy.context.temp_override(scene=scene, view_layer=view_layer):
        return bpy.context.mode


def _brush(settings, mode):
    """The active brush of the paint settings that supplied it, with the size and strength that apply, as Blender
    applies them (``BKE_brush_size_get``, ``BKE_brush_alpha_get``): the brush's own ``use_unified_size`` and
    ``use_unified_strength`` pick its paint settings' unified value or its own (``UnifiedPaintSettings``' own
    ``use_unified_*`` are deprecated flags Blender no longer reads). Sizes are diameters in pixels."""
    paint = getattr(settings, PAINT_SETTINGS[mode], None) if mode in PAINT_SETTINGS else None
    brush = getattr(paint, "brush", None)
    if brush is None:
        return None
    unified = getattr(paint, "unified_paint_settings", None)   # Particle Edit's brush has none: its own values.
    size = unified.size if unified is not None and getattr(brush, "use_unified_size", False) else brush.size
    strength = (unified.strength if unified is not None and getattr(brush, "use_unified_strength", False)
                else brush.strength)
    return {"name": getattr(brush, "name", None) or getattr(paint, "tool", None), "diameter_px": int(size),
            "strength": round(float(strength), 4)}


def tool_section(bpy, window, view_layer, scene):
    """The workspace's active tool for the window's context mode and the settings that change what it does."""
    mode = context_mode(bpy, scene, view_layer)
    tool = window.workspace.tools.from_space_view3d_mode(mode, create=False)
    settings = scene.tool_settings
    section = {"tool": getattr(tool, "idname", None), "context_mode": mode,
               "snap": bool(settings.use_snap), "snap_elements": sorted(settings.snap_elements),
               "proportional_edit": bool(settings.use_proportional_edit),
               "pivot": settings.transform_pivot_point,
               "orientation": scene.transform_orientation_slots[0].type}
    brush = _brush(settings, mode)
    if brush is not None:
        section["brush"] = brush
    return section


def _world_bounds(objects):
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    for obj in objects:
        rows = [list(obj.matrix_world[i]) for i in range(3)]   # Blender's Matrix and plain lists index alike.
        for corner in obj.bound_box:
            for axis in range(3):
                value = sum(rows[axis][j] * corner[j] for j in range(3)) + rows[axis][3]
                low[axis], high[axis] = min(low[axis], value), max(high[axis], value)
    return None if low[0] == float("inf") else {"min": _round(low), "max": _round(high)}


def selection_section(window, view_layer):
    """The view layer's own selected list (never a walk over every object), its bounds over at most ``MAX_BOUNDS``."""
    selected = list(view_layer.objects.selected)
    active = _active(view_layer)
    section = {"active": getattr(active, "name", None), "selected_count": len(selected),
               "selected": [obj.name for obj in selected[:MAX_NAMES]], "bounds": _world_bounds(selected[:MAX_BOUNDS])}
    if len(selected) > MAX_NAMES:
        section["truncated"] = True
    if len(selected) > MAX_BOUNDS:
        section["bounds_partial"] = True
    if active is not None and active.mode == "EDIT" and active.type == "MESH":
        mesh = active.data
        section["edit"] = {"vertices": mesh.total_vert_sel, "edges": mesh.total_edge_sel,
                           "faces": mesh.total_face_sel,
                           "select_mode": list(window.scene.tool_settings.mesh_select_mode)}
    return section


def workspace_section(window):
    screen = window.screen
    if screen is None:   # Between screens for a moment (live_windows): said, never raised.
        return {"unavailable": "the window is changing screens"}
    return {"workspace": window.workspace.name, "screen": screen.name,
            "editors": [{"type": area.type, "ui_type": area.ui_type, "width": area.width, "height": area.height}
                        for area in live_windows.areas(window)[:MAX_AREAS]]}


def view3d(window):
    """The window's largest 3D viewport (the one the person most likely works in), as (area, space, region_3d)."""
    areas = [area for area in live_windows.areas(window) if area.type == "VIEW_3D"]
    if not areas:
        return None
    area = max(areas, key=lambda a: a.width * a.height)
    space = area.spaces.active
    return area, space, space.region_3d


def viewport_section(window):
    found = view3d(window)
    if found is None:
        return {"unavailable": "no 3D viewport in this workspace"}
    _area, space, region = found
    shading, overlay = space.shading, space.overlay
    return {"shading": shading.type, "light": shading.light, "color": shading.color_type,
            "xray": bool(shading.show_xray), "overlays": bool(overlay.show_overlays),
            "wireframe_overlay": bool(overlay.show_wireframes), "gizmos": bool(space.show_gizmo),
            "view": {"perspective": region.view_perspective, "distance": round(float(region.view_distance), 4),
                     "target": _round(region.view_location), "rotation": _round(region.view_rotation),
                     "lens": round(float(space.lens), 2)},
            "local_view": space.local_view is not None}


def scene_section(window, scene, view_layer):
    camera, screen = scene.camera, window.screen
    return {"scene": scene.name, "view_layer": getattr(view_layer, "name", None), "frame": scene.frame_current,
            "range": [scene.frame_start, scene.frame_end],
            "preview_range": [scene.frame_preview_start, scene.frame_preview_end] if scene.use_preview_range else None,
            "fps": round(scene.render.fps / (scene.render.fps_base or 1.0), 3),
            "playing": bool(screen.is_animation_playing) if screen is not None else None,
            "camera": getattr(camera, "name", None), "unit_system": scene.unit_settings.system,
            "unit_scale": round(float(scene.unit_settings.scale_length), 6)}


def render_section(scene):
    render = scene.render
    return {"engine": render.engine, "resolution": [render.resolution_x, render.resolution_y],
            "percentage": render.resolution_percentage, "format": render.image_settings.file_format,
            "frame_range": [scene.frame_start, scene.frame_end]}


def jobs_section(jobs):
    """Angelo's background jobs (renders, bakes, simulations): the running one with its progress, and the queue."""
    if jobs is None:
        return {"running": None, "queued": 0}
    record = jobs.active()
    running = None
    if record is not None:
        running = {"id": record.get("id"), "kind": record.get("kind"), "status": record.get("status"),
                   "progress": round(float(record.get("progress") or 0.0), 4),
                   "eta_seconds": record.get("eta_s")}
    return {"running": running, "queued": len(getattr(jobs, "queue", ()) or ())}


def undo_section(window_manager):
    stack = window_manager.undo_stack   # None until the window's first Undo push (a background Blender has none).
    if stack is None:
        return {"steps": [], "total": 0, "can_redo": False}
    steps = list(stack.steps)
    active = stack.active_index
    newest = steps[-MAX_UNDO:]
    first = len(steps) - len(newest)
    return {"steps": [{"name": step.name, "active": first + i == active} for i, step in enumerate(newest)],
            "total": len(steps), "can_redo": active < len(steps) - 1}


def file_section(data):
    return {"saved": bool(data.filepath), "unsaved_changes": bool(data.is_dirty)}


def _section(name, bpy, window, jobs):
    """One section, called directly (no closure holds the window, scene or view layer: live_target)."""
    scene, view_layer = window.scene, window.view_layer
    if name == "mode":
        return mode_section(window, view_layer)
    if name == "tool":
        return tool_section(bpy, window, view_layer, scene)
    if name == "selection":
        return selection_section(window, view_layer)
    if name == "workspace":
        return workspace_section(window)
    if name == "viewport":
        return viewport_section(window)
    if name == "scene":
        return scene_section(window, scene, view_layer)
    if name == "render":
        return render_section(scene)
    if name == "jobs":
        return jobs_section(jobs)
    if name == "undo":
        return undo_section(bpy.context.window_manager)
    return file_section(bpy.data)


def read(bpy, window, jobs=None):
    """Every section of the window's state, each read on its own."""
    state = {}
    for name in SECTIONS:
        try:
            state[name] = _section(name, bpy, window, jobs)
        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError, IndexError, ReferenceError) as error:
            state[name] = {"unavailable": type(error).__name__}
    return state
