# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread native capture for the read-only ``angelo_view`` tool.

Pixels come from ``GPUOffScreen.draw_view3d`` and a direct GPU texture read.
``view_png`` encodes them on worker threads that never touch ``bpy`` while the
main thread draws the next view. No Blender Image, temporary file, Undo step or
Main ID is created; only the chat's private reference PNG is written, because
that file is the existing provider image delivery path.

``draw_view3d`` takes its shading from a SpaceView3D, so one artist viewport's
display settings are normalized for the duration of this call only. Nothing
processes events or redraws between that change and its restoration in
``finally``; the add-on Python those writes run afterwards follows the
approval mode (``view_policy``). MATERIAL is drawn as a viewport image render, in which EEVEE
compiles material shaders synchronously instead of drawing placeholders.
RENDERED is the one shading that does not draw the viewport: ``view_render``
renders the scene camera with the scene's engine as a background job. WEIGHT
does not either: ``view_weights`` draws the targets' weights at the same
cameras (doc/angelo/view_sources.rst).
"""

from collections import namedtuple
import concurrent.futures
import datetime
import os
import time

import bpy
from mathutils import Matrix, Vector

from . import scene_queries, view_cache, view_compare, view_free_camera, view_policy
from .input_policy import tool_running
from .view_framing import (AXIS_DIRECTIONS, axis_basis, basis_from_view_matrix, camera_matrix,
                           orthographic_fit, orthographic_projection, output_size, render_aspect, view_matrix)
from .view_blur import EEVEE_LOOKS, NOT_DRAWN, own_offscreen_per_draw, scene_blurs
from .view_looks import BACKGROUND, BEST_EFFORT, EEVEE_SHADINGS, OVERLAY, SHADING, display_keys  # noqa: F401
from .view_png import encode_png
from .view_spec import (ATTRIBUTE, DEPTH, FIT, LIT, MAX_PAIRS, RENDERED, SCENE_CAMERA, TOOL_NAME, WEIGHT,
                        framing_kind, validate_view)

DRAWABLE = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "GREASEPENCIL",
                      "POINTCLOUD", "VOLUME"})
MAX_FRAMING_INSTANCES = 50000
_type_filters = None


class DisplayOverrides:
    """Normalize one viewport's display, then restore it in reverse order.

    Each old value is read immediately before its own assignment, and equal
    values are not written (no redundant RNA update, such as the geometry
    re-tag that entering MATERIAL causes for objects in Sculpt Mode).
    """

    def __init__(self):
        self._undo = []
        self.unavailable = []

    def apply(self, owner, settings):
        for key, value in settings:
            old = getattr(owner, key)
            if not isinstance(old, (str, bool, int, float)):
                old = tuple(old)
            if old == value:
                continue
            try:
                setattr(owner, key, value)
            except TypeError:
                if key not in BEST_EFFORT:
                    raise
                self.unavailable.append(key)
                continue
            self._undo.append((owner, key, old))

    def restore(self):
        errors = []
        while self._undo:
            owner, key, old = self._undo.pop()
            try:
                setattr(owner, key, old)
            except Exception as error:  # Keep restoring the rest; report every failure.
                errors.append(f"{key}: {error}")
        if errors:
            raise RuntimeError("Could not restore the artist's viewport display: " + "; ".join(errors))


def _view3d_areas(window):
    areas = [area for area in window.screen.areas if area.type == "VIEW_3D"]
    if not areas:
        raise RuntimeError("Keep a visible 3D Viewport in the bound Blender window")
    return areas


def _draw_area(areas):
    """Prefer a viewport whose temporary shading change has no side effects.

    Leaving RENDERED restarts that viewport's render engine, local view and
    local collections filter the drawn objects, and camera view lets EEVEE use
    the camera's depth of field and render border.
    """
    def cost(area):
        space = area.spaces.active
        return (space.shading.type == "RENDERED", space.local_view is not None,
                bool(space.use_local_collections), space.region_3d.view_perspective == "CAMERA")
    return min(areas, key=cost)


def _object_type_filters(space):
    global _type_filters
    if _type_filters is None:
        _type_filters = tuple(sorted(p.identifier for p in space.bl_rna.properties
                                     if p.identifier.startswith("show_object_viewport_")))
    return tuple(bool(getattr(space, name)) for name in _type_filters)


ViewportState = namedtuple("ViewportState", "local_view local_collections type_filters camera_view rendered")


def _viewport_state(space, shading):
    """Drawing-viewport state that is not normalized: part of the key and notes."""
    state = ViewportState(space.local_view is not None, bool(space.use_local_collections),
                          _object_type_filters(space), space.region_3d.view_perspective == "CAMERA",
                          space.shading.type == "RENDERED")
    notes = []
    if state.local_view or state.local_collections:
        notes.append({"code": "viewport_object_filter",
                      "detail": "The only drawable 3D Viewport uses local view or local collections; hidden objects are omitted"})
    if not all(state.type_filters):
        notes.append({"code": "viewport_type_filter",
                      "detail": "The drawing viewport hides some object types; those objects are omitted"})
    if state.camera_view and shading in EEVEE_SHADINGS:
        notes.append({"code": "viewport_in_camera_view",
                      "detail": "EEVEE may apply the camera's depth of field or render border to this view"})
    return state, notes


def _targets(window, handles):
    """Resolve handles and read each production revision (None when unsupported)."""
    objects, records = [], []
    for handle in handles:
        obj = scene_queries.resolve_object(window, handle)
        record = {"object": handle, "name": obj.name[:128], "type": obj.type,
                  "visible": bool(obj.visible_get(view_layer=window.view_layer))}
        try:
            record.update(revision=scene_queries.object_revision(window, obj), revision_exact=True)
        except scene_queries.SceneQueryError as error:
            record.update(revision=None, revision_exact=False, revision_limitation=str(error)[:240])
        objects.append(obj)
        records.append(record)
    return objects, records


def _hair_reach(evaluated, matrix):
    """How far an object's particle hair may reach past its bounds (its longest hair length, scaled; 0 for none)."""
    lengths = [system.settings.hair_length for system in getattr(evaluated, "particle_systems", ())
               if system.settings.type == "HAIR"]
    return max(lengths) * max(matrix.to_scale()) * 1.25 if lengths else 0.0   # A quarter more for children's kinks.


def _framing_points(window, objects):
    """World-space bounds of visible drawn geometry, including instances.

    Targets without drawable geometry (empties, lights, cameras) contribute
    their origin. Instance iteration is bounded; a truncated scan is reported.
    """
    layer = window.view_layer
    wanted = {obj.session_uid for obj in objects}
    points, truncated = [], False
    for index, instance in enumerate(layer.depsgraph.object_instances):
        if index >= MAX_FRAMING_INSTANCES:
            truncated = True
            break
        owner = (instance.parent if instance.is_instance else instance.object).original
        if (wanted and owner.session_uid not in wanted) or not instance.show_self:
            continue
        evaluated = instance.object
        if evaluated.type not in DRAWABLE:
            continue
        if not instance.is_instance and not owner.visible_get(view_layer=layer):
            continue
        matrix = instance.matrix_world.copy()
        corners = [matrix @ Vector(corner) for corner in evaluated.bound_box]
        reach = 0.0 if instance.is_instance else _hair_reach(evaluated, matrix)
        if reach:   # Particle hair is drawn beyond its emitter's bounds, up to its length away.
            low = [min(c[axis] for c in corners) - reach for axis in range(3)]
            high = [max(c[axis] for c in corners) + reach for axis in range(3)]
            corners = [Vector((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1])
                       for z in (low[2], high[2])]
        points.extend(tuple(corner) for corner in corners)
    for obj in objects:
        if obj.type not in DRAWABLE:
            points.append(tuple(obj.matrix_world.translation))
    if not points:
        raise RuntimeError("The targets are hidden or have no visible geometry, so there is nothing to frame. "
                           "Choose visible targets, or SCENE_CAMERA or an earlier view_id")
    return points, truncated


def _fitted_camera(points, basis, source, epoch, scene_uid):
    center, half, distance, depth = orthographic_fit(points, basis)
    world = camera_matrix(center, basis, distance)
    return {"epoch": epoch, "scene_uid": scene_uid, "source": source, "projection": "ORTHOGRAPHIC",
            "matrix_world": world, "view_matrix": view_matrix(world),
            "projection_matrix": orthographic_projection(half, half, depth), "aspect": 1.0,
            "details": {"ortho_scale": half * 2, "clip_start": 0.0, "clip_end": depth,
                        "center_world": list(center), "direction_world": list(basis[2])}}


def _scene_camera(window, epoch, resolution):
    scene, depsgraph = window.scene, window.view_layer.depsgraph
    obj = scene.camera
    if obj is None or obj.type != "CAMERA":
        raise RuntimeError("The scene has no active camera object; choose an axis or FIT view instead")
    data = obj.data
    if data.type == "PANO":
        raise RuntimeError("Panoramic scene cameras are not supported by native views")
    aspect = render_aspect(scene.render)
    evaluated = obj.evaluated_get(depsgraph)
    world = evaluated.matrix_world.normalized()
    # Square output pixels: the aspect already includes the render pixel
    # aspect, so the camera matrix uses scale 1 at the actual output size.
    width, height = output_size(resolution, aspect)
    projection = evaluated.calc_matrix_camera(depsgraph, x=width, y=height, scale_x=1, scale_y=1)
    details = {"camera_object": scene_queries.object_handle(obj), "camera_name": obj.name[:128],
               "clip_start": data.clip_start, "clip_end": data.clip_end,
               "shift": [data.shift_x, data.shift_y], "sensor_fit": data.sensor_fit}
    if data.type == "ORTHO":
        details["ortho_scale"] = data.ortho_scale
    else:
        details.update(lens_mm=data.lens, sensor_width_mm=data.sensor_width, sensor_height_mm=data.sensor_height)
    return {"epoch": epoch, "scene_uid": scene.session_uid, "source": SCENE_CAMERA,
            "projection": "ORTHOGRAPHIC" if data.type == "ORTHO" else "PERSPECTIVE",
            "matrix_world": [list(row) for row in world], "view_matrix": [list(row) for row in world.inverted()],
            "projection_matrix": [list(row) for row in projection], "aspect": width / height, "details": details}


def _camera(window, primary, item, points, epoch, resolution):
    """The exact camera for one validated views item."""
    scene_uid = window.scene.session_uid
    kind = framing_kind(item)
    if kind == "REUSE":
        camera = view_cache.cameras.resolve(item, epoch=epoch, scene_uid=scene_uid)
        camera["reused_from"] = item
        return camera
    if kind == SCENE_CAMERA:
        return _scene_camera(window, epoch, resolution)
    if kind == FIT:
        rows = [list(row) for row in primary.spaces.active.region_3d.view_matrix]
        return _fitted_camera(points, basis_from_view_matrix(rows), FIT, epoch, scene_uid)
    return _fitted_camera(points, axis_basis(AXIS_DIRECTIONS[item]), "AXIS:" + item, epoch, scene_uid)


def _plan_views(window, primary, checked, objects, targets, epoch, viewport, references):
    """Cameras, sizes, cache keys and cache hits; returns (plans, truncated). Free cameras: view_free_camera."""
    points, truncated, shared = None, False, {}
    if any(framing_kind(item) in ("AXIS", FIT) for item in checked["views"]):
        points, truncated = _framing_points(window, objects)
    plans = []
    for index, item in enumerate(checked["views"]):
        camera, facts = view_free_camera.plan(window, references, index, item, shared, epoch, checked["resolution"])
        camera = camera or _camera(window, primary, item, points, epoch, checked["resolution"])
        width, height = output_size(checked["resolution"], camera["aspect"])
        if facts.get("pair") is not None:   # Side by side: halves fitted to a deliverable picture.
            width, height = view_compare.fitted(width, height, item)
        key = None if facts.get("pair") else view_cache.image_key(  # A pair's picture also holds its image.
            scene_uid=window.scene.session_uid, epoch=epoch, view_layer=window.view_layer.as_pointer(),
            frame=window.scene.frame_current, viewport=viewport, targets=checked["targets"],
            revisions=[target["revision"] for target in targets], shading=checked["shading"],
            size=(width, height), camera=camera)
        plans.append(dict(facts, item=item, camera=camera, width=width, height=height, key=key,
                          cached=view_cache.images.get(key)))
    view_compare.prepare(plans, window, objects)  # Each pair's image (and OUTLINE's silhouettes) before drawing.
    return plans, truncated


def _draw_all(window, area, shading, jobs, pool):
    """Draw every (index, camera, width, height, encode) job; return encode futures."""
    import gpu
    space, region = area.spaces.active, next(r for r in area.regions if r.type == "WINDOW")
    scene, layer = window.scene, window.view_layer
    overrides, offscreens, futures = DisplayOverrides(), {}, {}
    try:
        overrides.apply(space.shading, SHADING[shading])
        overrides.apply(space.overlay, OVERLAY.get(shading, (("show_overlays", False),)))
        for view_region in (tuple(space.region_quadviews) or (space.region_3d,)):
            overrides.apply(view_region, (("use_clip_planes", False),))
        fresh = own_offscreen_per_draw(scene, layer, shading)  # No earlier draw to blur toward (view_blur).
        for index, camera, width, height, encode in jobs:
            if fresh and (width, height) in offscreens:
                offscreens.pop((width, height)).free()  # One EEVEE instance at a time.
            offscreen = offscreens.get((width, height))
            if offscreen is None:
                offscreen = offscreens[(width, height)] = gpu.types.GPUOffScreen(width, height)
            offscreen.draw_view3d(scene, layer, space, region, Matrix(camera["view_matrix"]),
                                  Matrix(camera["projection_matrix"]), do_color_management=True)
            buffer = offscreen.texture_color.read()
            buffer.dimensions = width * height * 4
            futures[index] = pool.submit(encode, bytes(buffer), width, height)
    finally:
        for offscreen in offscreens.values():
            offscreen.free()
        overrides.restore()
    return futures, overrides.unavailable


def _public_camera(camera):
    def rounded(matrix):
        return [[round(float(value), 7) for value in row] for row in matrix]
    result = {"source": camera["source"], "projection": camera["projection"],
              "matrix_world": rounded(camera["matrix_world"]),
              "projection_matrix": rounded(camera["projection_matrix"])}
    result.update(camera["details"])
    if camera.get("reused_from"):
        result["reused_from"] = camera["reused_from"]
    return result


def _capture(window, area, shading, plans):
    """Draw cache misses and wait for their PNGs; the artist display is restored first."""
    jobs = [(index, plan["camera"], plan["width"], plan["height"], plan.get("encode", encode_png))
            for index, plan in enumerate(plans) if plan["cached"] is None]
    capture = {"encoded": {}, "unavailable": [], "compiling": False, "draw_wall_ms": 0.0,
               "encode_wait_wall_ms": 0.0}
    if not jobs:
        return capture
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PAIRS, thread_name_prefix="AngeloViewPNG") as pool:
        futures, capture["unavailable"] = _draw_all(window, area, shading, jobs, pool)
        capture["compiling"] = shading in EEVEE_SHADINGS and bpy.app.is_job_running("SHADER_COMPILATION")
        drawn = time.perf_counter()
        capture["encoded"] = {index: future.result() for index, future in futures.items()}
    capture["draw_wall_ms"] = (drawn - started) * 1000
    capture["encode_wait_wall_ms"] = (time.perf_counter() - drawn) * 1000
    return capture


def _write_views(references, plans, encoded, shading):
    """Write each PNG to a new private chat reference; discard all on failure."""
    views, written = [], []
    try:
        for index, plan in enumerate(plans):
            png = plan["cached"]["png"] if plan["cached"] else encoded[index]
            kind = framing_kind(plan["item"])
            label = view_free_camera.label(plan) or f"{plan['item'] if kind != 'REUSE' else 'Reused'} view"
            reference = references.new_observation(f"{label} · {shading.lower()}")
            written.append(reference)
            with open(reference["path"], "wb") as handle:
                handle.write(png)
            os.chmod(reference["path"], 0o600)
            reference["bytes"] = len(png)
            views.append(view_compare.with_pair(plan, dict(
                reference, view_id=reference["id"], view=plan["item"], framing=kind, shading=shading,
                width=plan["width"], height=plan["height"], camera=_public_camera(plan["camera"]),
                cache_hit=plan["cached"] is not None, **({"depth": plan["depth"]} if "depth" in plan else {}))))
    except Exception:
        for reference in written:
            references.discard_observation(reference)
        raise
    return views


def _remember(plans, views, encoded, cacheable):
    """Admit REUSE cameras for every view; cache only certain, fresh drawings."""
    for index, (plan, view) in enumerate(zip(plans, views)):
        camera = {key: value for key, value in plan["camera"].items() if key != "reused_from"}
        view_cache.cameras.remember(view["view_id"], camera)
        if plan["cached"] is None and cacheable:
            view_cache.images.put(plan["key"], encoded[index], plan["width"], plan["height"], camera)


def execute(window, args, references, controller=None):
    """Capture 1-3 views, or up to six reference pairs; never edits scene data, selection, mode or frame.

    RENDERED is not drawn here: ``view_render`` renders it as a background job and
    returns a pending result (``controller`` is the conversation that waits for it).
    """
    wall, cpu = time.perf_counter(), time.process_time()
    checked = validate_view(TOOL_NAME, args)
    if references is None:
        raise RuntimeError("Bind a conversation reference store before capturing views")
    if checked["shading"] == RENDERED:
        from . import view_render
        return view_render.begin(window, checked, references, controller)
    if "frames" in checked:  # A strip over several frames (view_motion, doc/angelo/view_sources.rst).
        from . import view_motion
        return view_motion.execute(window, checked, references, controller)
    if tool_running(window):
        raise RuntimeError("Finish the current Blender interaction before capturing a view")
    areas = _view3d_areas(window)
    area = _draw_area(areas)
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        # Evaluate already-tagged edits so bounds and pixels show current state.
        window.view_layer.update()
        # Read before any handle/revision work: an ID replacement detected while
        # observing rotates the epoch, which the final check below rejects.
        epoch = scene_queries.document_epoch()
        objects, targets = _targets(window, checked["targets"])
        viewport, uncertainty = _viewport_state(area.spaces.active, checked["shading"])
        if any(not target["visible"] for target in targets):
            uncertainty.append({"code": "target_hidden", "detail": "A target is hidden in this view layer and is not drawn"})
        plans, truncated = _plan_views(window, areas[0], checked, objects, targets, epoch, viewport, references)
        if truncated:
            uncertainty.append({"code": "framing_bounds_truncated",
                                "detail": "Instance bounds exceeded the scan budget; the frame may crop content"})
        uncertainty.extend(view_free_camera.notes(plans))
        python = None  # Add-on Python the display writes let run (view_policy); WEIGHT, DEPTH, cache hits write none.
        if checked["shading"] in (WEIGHT, DEPTH):   # The host draws the targets itself (view_weights, view_depth).
            from . import view_depth, view_weights
            capture = (view_weights if checked["shading"] == WEIGHT else view_depth).capture(window, plans, objects,
                                                                                             checked)
            uncertainty.extend(capture["uncertainty"])
        else:
            if any(plan["cached"] is None for plan in plans):
                python = view_policy.admit_display(area.spaces.active, checked["shading"], controller)
            capture = _capture(window, area, checked["shading"], plans)
        uncertainty.extend(view_compare.notes(plans))  # OUTLINE's, known once its pictures are made.
        if capture["unavailable"]:
            uncertainty.append({"code": "studio_light_unavailable",
                                "detail": "The built-in studio light is not installed; the artist's selection was used"})
        if capture["compiling"]:
            uncertainty.append({"code": "shaders_compiling",
                                "detail": "Shader compilation was still pending after capture; some materials may show placeholders"})
        if scene_queries.document_epoch() != epoch:
            raise RuntimeError("Document IDs were replaced during capture; search the scene and view again")
    views = _write_views(references, plans, capture["encoded"], checked["shading"])
    # LIT also depends on the scene's colour management and film settings, which no depsgraph update reports.
    _remember(plans, views, capture["encoded"],
              cacheable=not uncertainty and checked["shading"] not in (LIT, WEIGHT, DEPTH))
    side_effects = []
    if viewport.rendered and capture["encoded"]:
        # Only reachable when every 3D Viewport in the window is in Rendered shading.
        side_effects.append({"code": "rendered_viewport_progress_reset",
                             "detail": "Temporarily changing the Rendered viewport's shading restarts its render progress"})
    result = {
        "success": True, "read_only": True, "scene_changed": False, "document_epoch": epoch,
        "scene": window.scene.name[:128], "frame": window.scene.frame_current,
        "coverage": "TARGETS" if targets else "VISIBLE_SCENE", "targets": targets, "views": views,
        "shading": checked["shading"], "resolution": checked["resolution"],
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "uncertain": bool(uncertainty), "uncertainty": uncertainty, "side_effects": side_effects,
        "timing": {"capture_wall_ms": round((time.perf_counter() - wall) * 1000, 3),
                   "process_cpu_ms": round((time.process_time() - cpu) * 1000, 3),
                   "draw_wall_ms": round(capture["draw_wall_ms"], 3),
                   "encode_wait_wall_ms": round(capture["encode_wait_wall_ms"], 3)},
        "cache": view_cache.images.stats(),
        "note": ("Actual native pixels. Visible neighbors may appear; nearer geometry is cut away for fitted views. "
                 "Not a final render, and not authority for edits or sculpt pixel targeting."),
    }
    if checked["shading"] == WEIGHT:
        from .view_weights import NOTE
        result.update(weights=capture["weights"], note=NOTE)
    elif checked["shading"] == DEPTH:
        from .view_depth import NOTE
        result["note"] = NOTE
    elif checked["shading"] == ATTRIBUTE:
        result["note"] = ("Workbench solid colours from each mesh's active colour attribute (uniform where a mesh "
                          "has none), viewport data. " + result["note"])
    if checked["shading"] == "MATERIAL":
        result["material_preview"] = {"engine": "EEVEE", "lighting": "BUILTIN_HDRI",
                                      "hdri": "artist selection" if capture["unavailable"] else "forest.exr",
                                      "shader_compilation": "SYNCHRONOUS_VIEWPORT_IMAGE_RENDER"}
    elif checked["shading"] == LIT:
        engine = window.scene.render.engine
        result["lit_preview"] = {"engine": "EEVEE", "lighting": "SCENE_LIGHTS_AND_WORLD", "scene_engine": engine,
                                 "approximation": not engine.startswith("BLENDER_EEVEE")}
        result["note"] = ("Actual native pixels drawn by EEVEE with the scene's own lights and world, on viewport "
                          "data (viewport modifier levels, no compositing)%s. Visible neighbors may appear; nearer "
                          "geometry is cut away for fitted views. RENDERED renders the scene camera exactly." % (
                              "" if engine.startswith("BLENDER_EEVEE") else
                              ": an approximation of this %s scene, whose volumes, displacement and light "
                              "transport EEVEE draws differently" % engine))
        if window.scene.render.film_transparent:
            # The viewport read is opaque: where a render is transparent, this draw is black.
            result["lit_preview"]["transparent_film"] = True
            result["note"] += (" The scene's film is transparent: its world is not drawn, and the black background "
                               "here is transparent in a render (RENDERED shows it over a checkerboard).")
    if checked["shading"] in EEVEE_LOOKS and scene_blurs(window.scene, window.view_layer):
        # The scene renders with motion blur; this viewport look shows none, by design (view_blur).
        result["material_preview" if checked["shading"] == "MATERIAL" else "lit_preview"]["motion_blur"] = NOT_DRAWN
    result["note"] += view_compare.note(plans)
    return view_policy.disclose(result, python)
