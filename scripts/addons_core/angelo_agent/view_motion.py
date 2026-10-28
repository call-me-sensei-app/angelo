# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``frames``: one strip of a viewport look over a few frames, the frame put back.

A still view cannot show motion: whether a walk cycle slides, a door swings
the right way, an object leaves the frame. A strip draws the same look at
2 to 8 frames and returns them as one picture, left to right then top to
bottom, each labelled with its frame number (doc/angelo/view_sources.rst,
"Motion strips"). It counts as one view of the request's budget.

* **Camera.** An axis or FIT view is fitted to the targets over all the
  frames and then held, so movement shows; SCENE_CAMERA follows the scene's
  camera at each frame; a REUSE view_id keeps its camera.
* **The frame.** ``scene.frame_set`` changes it for each draw and puts the
  frame and subframe back in ``finally``. That runs what scrubbing the
  timeline runs: frame-change handlers and drivers, and unbaked simulations
  compute the frames shown. A strip runs without the person's approval, so,
  as for a RENDERED look, a scene whose frame change would run Python from
  add-ons or scripts (``render_extensions``: frame-change handlers that do not
  ship with the application, scripted drivers the scene reaches) is refused at
  admission and again just before the first frame change, naming the
  approved route; shipped handlers and physics are reported. Depsgraph-update
  handlers, and message-bus subscribers to the viewport settings a strip sets
  and puts back (called after it ends), that do not ship are refused in the
  Read-only and Ask approval modes and allowed, and named, in the Auto modes,
  whose typed edits run them too. After every strip ``view_scene_check``
  compares the document with its state before, so a result never says the
  scene is unchanged when that is not known.
* **Bounded.** At most ``view_spec.MAX_FRAMES`` frames; a strip stops drawing
  after ``STRIP_SECONDS`` and says which frames it left empty.

The viewport display is normalized and restored exactly as ``view_capture``
does. Main thread; model-facing text is English.
"""

import datetime
import time

from . import view_cache, view_free_camera
from .input_policy import tool_running
from .view_blur import NOT_DRAWN, own_offscreen_per_draw
from .view_framing import output_size
from .view_png import encode_png
from .view_spec import CAMERA, FIT, SCENE_CAMERA, framing_kind

STRIP_SECONDS = 20.0
GAP = 2                   # Dark pixels between tiles.
GRID = {2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2), 7: (4, 2), 8: (4, 2)}
PHYSICS = frozenset({"CLOTH", "SOFT_BODY", "FLUID", "DYNAMIC_PAINT", "PARTICLE_SYSTEM", "COLLISION", "OCEAN"})
SCANNED_OBJECTS = 2000    # Objects inspected for unbaked physics.


def tile_size(resolution, aspect, columns, rows):
    """Each tile's size, so the whole strip (tiles and gaps) has ``resolution`` as its longer side."""
    width, height = output_size(resolution, columns * aspect / rows)
    tile_width = max(16, (width - GAP * (columns - 1)) // columns)
    return tile_width, max(16, round(tile_width / aspect))


def compose(tiles, frames, columns, rows, size):
    """One RGBA picture (top row first) of the tiles in reading order, each labelled; missing tiles stay dark."""
    import numpy
    from .view_pixels import draw_label
    width, height = size
    sheet = numpy.zeros((rows * height + GAP * (rows - 1), columns * width + GAP * (columns - 1), 4), numpy.uint8)
    sheet[..., :3], sheet[..., 3] = 24, 255
    for index, frame in enumerate(frames):
        top, left = (index // columns) * (height + GAP), (index % columns) * (width + GAP)
        if frame in tiles:
            sheet[top:top + height, left:left + width] = tiles[frame]
        draw_label(sheet[top:top + height, left:left + width], str(frame), 2, 2)
    return sheet


def scene_effects(scene):
    """What changing the frame runs or computes besides drawing: ``(side_effects, uncertainty)``."""
    import bpy
    handlers = len(bpy.app.handlers.frame_change_pre) + len(bpy.app.handlers.frame_change_post)
    side_effects = [{"code": "frame_change_handlers_ran", "detail": (
        "%d frame-change handler(s) that ship with the application ran for each frame, as when the person scrubs "
        "the timeline" % handlers)}] if handlers else []  # Any other would have refused the strip.
    physics = getattr(scene, "rigidbody_world", None) is not None or any(
        modifier.type in PHYSICS for index, obj in enumerate(scene.objects) if index < SCANNED_OBJECTS
        for modifier in obj.modifiers)
    uncertainty = [{"code": "simulation_frames", "detail": (
        "The scene has physics or particles: unbaked frames are computed in the order shown, which can differ "
        "from playback from the start")}] if physics else []
    return side_effects, uncertainty


def extension_refusal(scene, mode="ASK", written=()):
    """``(why changing this scene's frame needs the person's approval, or None, the extension report)``.

    ``mode`` is the chat's approval mode: the Auto modes let depsgraph-update handlers and message-bus
    subscribers to the ``written`` viewport settings run (render_extensions).
    """
    from . import render_extensions
    report = render_extensions.inspect(scene, render_extensions.FRAME_BAKE, written)
    if not report.blocking(mode):
        return None, report
    return ("Changing this scene's frame, or the viewport display a strip sets, runs Python from add-ons or "
            "scripts (%s), and a strip runs without the "
            "person's approval, so no frame was changed. Render the frames as a job they approve: angelo_job_start "
            "RENDER_ANIMATION over those frames (a small resolution and few samples keep it quick; its approval "
            "names this Python), then angelo_view with shading RENDERED, image set to its job ID and frame for each "
            "frame to see.%s" % (report.summary(), render_extensions.read_only_note(mode))), report


def _refusal(window, mode, written):
    import bpy
    from . import job_ui
    if tool_running(window):
        return "Finish the current Blender interaction before viewing frames"
    if getattr(window.screen, "is_animation_playing", False):
        return "The animation is playing; stop playback to view a strip of frames"
    if bpy.app.is_job_running("RENDER") or job_ui.manager().active() is not None:
        return "A render or job is running; a strip changes the frame, so it waits until that ends"
    return extension_refusal(window.scene, mode, written)[0]  # Admission.


def _fixed_camera(window, primary, item, objects, frames, epoch, resolution, deadline):
    """``(camera, facts)``: the held camera of an axis, FIT, free or REUSE strip, fitted or clipped over every frame.

    None for SCENE_CAMERA. ``facts`` are a free camera's (view_free_camera.notes), else empty.
    """
    from . import view_capture as capture
    kind = framing_kind(item)
    if kind == SCENE_CAMERA:
        return None, {}
    stored = view_cache.cameras.resolve(item, epoch=epoch, scene_uid=window.scene.session_uid) if (
        kind == "REUSE") else None
    if kind == CAMERA or stored is not None and "free" in stored:
        # Clip planes that hold every visible box at every frame shown; a REUSE keeps its pose, lens and aspect.
        corners, complete = [], True
        for frame in frames:
            window.scene.frame_set(frame)
            found, whole = view_free_camera.scene_bounds(window)
            corners.extend(found or ())
            complete = complete and whole
            if time.perf_counter() > deadline:
                complete = False
                break
        values, aspect = (item, None) if stored is None else (stored["free"], stored["aspect"])
        camera, facts = view_free_camera.camera(window, values, (corners or None, complete), epoch, resolution,
                                                aspect)
        if stored is not None:
            camera["reused_from"] = item
        return camera, facts
    points = None
    if kind in ("AXIS", FIT):
        points = []
        for frame in frames:
            window.scene.frame_set(frame)
            try:
                points.extend(capture._framing_points(window, objects)[0])
            except RuntimeError:
                pass  # Hidden at this frame; the others frame the strip.
            if time.perf_counter() > deadline:
                break
        if not points:
            raise RuntimeError("The targets have no visible geometry at these frames, so there is nothing to frame")
    return capture._camera(window, primary, item, points, epoch, resolution), {}


def _draw(window, area, shading, cameras, frames, size, deadline):
    """``({frame: RGBA tile top row first}, frames left undrawn)``; the display is restored in ``finally``."""
    import gpu
    import numpy
    from mathutils import Matrix
    from . import view_capture as capture
    space, region = area.spaces.active, next(r for r in area.regions if r.type == "WINDOW")
    scene, layer = window.scene, window.view_layer
    from . import view_warm
    overrides, tiles, missing, failed = capture.DisplayOverrides(), {}, [], set()
    fresh = own_offscreen_per_draw(scene, layer, shading)  # Each tile unblurred by the one before (view_blur).
    offscreen = gpu.types.GPUOffScreen(*size)
    try:
        overrides.apply(space.shading, capture.SHADING[shading])
        overrides.apply(space.overlay, capture.OVERLAY.get(shading, (("show_overlays", False),)))
        for view_region in (tuple(space.region_quadviews) or (space.region_3d,)):
            overrides.apply(view_region, (("use_clip_planes", False),))
        for index, frame in enumerate(frames):
            if index and time.perf_counter() > deadline:
                missing = list(frames[index:])
                break
            scene.frame_set(frame)
            camera = cameras(frame)
            if fresh and index:
                previous, offscreen = offscreen, None
                previous.free()
                offscreen = gpu.types.GPUOffScreen(*size)
            offscreen.draw_view3d(scene, layer, space, region, Matrix(camera["view_matrix"]),
                                  Matrix(camera["projection_matrix"]), do_color_management=True)
            failed |= view_warm.failed(window, shading)   # Each frame's own (VW8-H-1).
            buffer = offscreen.texture_color.read()
            buffer.dimensions = size[0] * size[1] * 4
            tiles[frame] = numpy.frombuffer(bytes(buffer), dtype=numpy.uint8).reshape(size[1], size[0], 4)[::-1]
    finally:
        if offscreen is not None:
            offscreen.free()
        overrides.restore()
    return tiles, missing, failed


def execute(window, checked, references, controller=None):
    """Draw the strip and return a view result with one picture; never leaves the frame or display changed."""
    import bpy
    from . import scene_queries, view_scene_check, view_warm
    from . import view_capture as capture
    from .view_policy import mode_of
    wall = time.perf_counter()
    mode = mode_of(controller)  # Without a chat: Ask, the strict mode.
    written = capture.display_keys(checked["shading"])
    refusal = _refusal(window, mode, written)
    if refusal:
        raise RuntimeError(refusal)
    areas = capture._view3d_areas(window)
    area = capture._draw_area(areas)
    region = next(r for r in area.regions if r.type == "WINDOW")
    scene, item, frames = window.scene, checked["views"][0], checked["frames"]
    original = (scene.frame_current, scene.frame_subframe)
    columns, rows = GRID[len(frames)]
    deadline = wall + STRIP_SECONDS
    with bpy.context.temp_override(window=window, area=area, region=region):
        window.view_layer.update()
        epoch = scene_queries.document_epoch()
        objects, targets = capture._targets(window, checked["targets"])
        _viewport, uncertainty = capture._viewport_state(area.spaces.active, checked["shading"])
        side_effects, physics = scene_effects(scene)
        refusal, report = extension_refusal(scene, mode, written)  # Again at start, before the first frame.
        if refusal:
            raise RuntimeError(refusal)
        handlers = report.view_python  # What the Auto modes let the strip run (subscribers: after it ends).
        check = view_scene_check.SceneCheck(window, deep=bool(report.depsgraph_handlers))
        try:
            fixed, facts = _fixed_camera(window, areas[0], item, objects, frames, epoch, checked["resolution"],
                                         deadline)
            uncertainty.extend(view_free_camera.notes([facts]))
            render = scene.render
            aspect = fixed["aspect"] if fixed is not None else (
                render.resolution_x * render.pixel_aspect_x / (render.resolution_y * render.pixel_aspect_y))
            size = tile_size(checked["resolution"], aspect, columns, rows)
            if fixed is not None and abs(fixed["aspect"] - size[0] / size[1]) > 0.02 and framing_kind(item) == "REUSE":
                uncertainty.append({"code": "strip_tile_aspect", "detail": "The reused camera is drawn in tiles "
                                    "of a slightly different shape"})

            def camera(_frame):
                return fixed if fixed is not None else capture._scene_camera(window, epoch, max(size))
            tiles, missing, failed = _draw(window, area, checked["shading"], camera, frames, size, deadline)
        finally:
            scene.frame_set(original[0], subframe=original[1])
        scene_check = check.finish()
        if scene_queries.document_epoch() != epoch:
            raise RuntimeError("Document IDs were replaced during the strip; search the scene and view again")
    sheet = compose(tiles, frames, columns, rows, size)
    png = encode_png(sheet.tobytes(), sheet.shape[1], sheet.shape[0], bottom_up=False)
    reference = references.new_observation("Frames %d-%d · %s" % (frames[0], frames[-1], checked["shading"].lower()))
    try:
        with open(reference["path"], "wb") as handle:
            handle.write(png)
    except Exception:
        references.discard_observation(reference)
        raise
    reference["bytes"] = len(png)
    if fixed is not None:
        held = {key: value for key, value in fixed.items() if key != "reused_from"}
        view_cache.cameras.remember(reference["id"], held)  # A later view may REUSE the strip's camera.
    uncertainty += physics + ([view_warm.note(failed)] if failed else [])
    if missing:
        uncertainty.append({"code": "strip_truncated", "detail": "Stopped after %d s; frames %s are left empty"
                            % (STRIP_SECONDS, ", ".join(map(str, missing)))})
    view = dict(reference, view_id=reference["id"], view=item, framing="STRIP", shading=checked["shading"],
                width=sheet.shape[1], height=sheet.shape[0], cache_hit=False, frames=list(frames),
                grid=[columns, rows], tile=list(size),
                camera=capture._public_camera(fixed) if fixed is not None else {"source": SCENE_CAMERA,
                                                                              "per_frame": True})
    if own_offscreen_per_draw(scene, window.view_layer, checked["shading"]):
        view["motion_blur"] = NOT_DRAWN  # The scene renders with blur; these tiles show none (view_blur).
    return view_scene_check.claims({"success": True, "read_only": True, "scene_changed": False, "document_epoch": epoch,
            "scene": scene.name[:128], "frame": original[0], "frames": list(frames), "missing_frames": missing,
            "coverage": "TARGETS" if targets else "VISIBLE_SCENE", "targets": targets, "views": [view],
            "shading": checked["shading"], "resolution": checked["resolution"],
            "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "uncertain": bool(uncertainty), "uncertainty": uncertainty, "side_effects": side_effects,
            "timing": {"capture_wall_ms": round((time.perf_counter() - wall) * 1000, 3)},
            "note": ("Frames %s drawn as a %s viewport look, left to right then top to bottom, each labelled with its "
                     "frame number in its top-left corner%s. The scene is back at frame %d. Viewport data, not a "
                     "render." % (", ".join(map(str, frames)), checked["shading"],
                                  "; the camera is held, so movement shows" if fixed is not None else
                                  "; the scene camera at each frame", original[0]))}, scene_check, handlers,
                                 report.subscribers is None)
