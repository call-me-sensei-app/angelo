# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed-camera captures of a scenario scene, inside Blender: ray-cast depth/ID maps and beauty renders.

The ray-cast capture is the objective half of end-of-session scoring. For each
fixed camera (``scenario_<id>.CAMERAS``) one ray per pixel is cast through the
evaluated scene (``Scene.ray_cast``, instances and modifiers included), giving a
depth map (``inf`` where the ray reaches the sky) and the name of the object it
hit. The same function runs on the ground truth and on each side's saved scene
in the same stock Blender, so the maps differ only by what was built: no
renderer, GPU or colour-management difference enters the score.
``scenario_scoring`` compares the maps on the host.

Render visibility decides what a ray can hit: before capturing, every object
and collection takes its render visibility in the viewport, so a hidden helper
counts exactly as it would in a render. Only call this on a throwaway copy.
"""

import math

CAPTURE_RESOLUTION = (384, 216)
SENSOR_WIDTH = 36.0


def camera_basis(spec):
    """(location, right, up, forward) unit vectors of a camera spec, +Y up for straight-down views."""
    from mathutils import Vector
    location, target = Vector(spec["location"]), Vector(spec["target"])
    forward = (target - location).normalized()
    up = Vector((0.0, 1.0, 0.0)) if abs(forward.z) > 0.99 else Vector((0.0, 0.0, 1.0))
    right = forward.cross(up).normalized()
    return location, right, right.cross(forward).normalized(), forward


def rays(spec, width, height):
    """Ray origins and directions for every pixel centre, row 0 at the top (image order)."""
    location, right, up, forward = camera_basis(spec)
    aspect = height / width
    output = []
    for row in range(height):
        v = (0.5 - (row + 0.5) / height) * aspect
        for column in range(width):
            u = (column + 0.5) / width - 0.5
            if spec.get("ortho_scale"):
                scale = spec["ortho_scale"]
                output.append((location + right * (u * scale) + up * (v * scale), forward))
            else:
                half = SENSOR_WIDTH / (2.0 * spec["lens"])  # tan(horizontal fov / 2), sensor fit AUTO (landscape).
                output.append((location, (forward + right * (2 * u * half) + up * (2 * v * half)).normalized()))
    return output


def match_render_visibility(bpy, scene):
    for obj in scene.objects:
        obj.hide_set(False)
        obj.hide_viewport = obj.hide_render
    for collection in bpy.data.collections:
        collection.hide_viewport = collection.hide_render
    bpy.context.view_layer.update()


def capture(bpy, scene, cameras, resolution=CAPTURE_RESOLUTION):
    """``{view: {"depth": [...], "ids": [...]}}`` (row-major lists) plus the hit-object name table."""
    width, height = resolution
    depsgraph = bpy.context.evaluated_depsgraph_get()
    names, index = [], {}
    views = {}
    for view, spec in cameras.items():
        depth, ids = [], []
        for origin, direction in rays(spec, width, height):
            hit, location, _normal, _face, obj, _matrix = scene.ray_cast(depsgraph, origin, direction, distance=1.0e4)
            if not hit:
                depth.append(math.inf)
                ids.append(-1)
                continue
            name = obj.original.name
            if name not in index:
                index[name] = len(names)
                names.append(name)
            # Distance along the view axis (planar depth), so orthographic and perspective maps compare alike.
            depth.append((location - origin).dot(camera_basis(spec)[3]))
            ids.append(index[name])
        views[view] = {"depth": depth, "ids": ids}
    return {"resolution": [width, height], "names": names, "views": views,
            "parents": {name: (bpy.data.objects[name].parent.name if bpy.data.objects.get(name) and
                               bpy.data.objects[name].parent else None) for name in names}}


def save_capture(path, data):
    """Compact numpy archive of a capture (Blender's bundled NumPy)."""
    import json
    import numpy
    width, height = data["resolution"]
    arrays = {}
    for view, maps in data["views"].items():
        arrays[view + ".depth"] = numpy.asarray(maps["depth"], dtype=numpy.float32).reshape(height, width)
        arrays[view + ".ids"] = numpy.asarray(maps["ids"], dtype=numpy.int32).reshape(height, width)
    arrays["meta"] = numpy.frombuffer(json.dumps({"names": data["names"], "parents": data["parents"],
                                                  "resolution": data["resolution"]}).encode(), dtype=numpy.uint8)
    numpy.savez_compressed(path, **arrays)


REVIEW_LOOK = ("AgX", "AgX - Punchy")
JPEG_QUALITIES = (92, 88, 84, 80, 76, 72, 68, 64, 60)


def render_views(bpy, scene, cameras, folder, resolution, samples=64, frame=None, prefix="", max_bytes=None):
    """Renders from fixed cameras with the scene's own lights, world and materials; ``{view: path}``.

    The camera, resolution, samples, still settings and colour management
    (``REVIEW_LOOK``, exposure 0) are the same for the ground truth and both
    sides, so side-by-side images differ only by what each scene contains.
    With ``max_bytes`` (the references) each view is a JPEG at the highest
    quality in ``JPEG_QUALITIES`` that fits the budget; otherwise a PNG.
    """
    from pathlib import Path
    import scenario_build_kit as kit
    scene.view_settings.view_transform, scene.view_settings.look = REVIEW_LOOK
    scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
    render = scene.render
    render.resolution_x, render.resolution_y = resolution
    render.resolution_percentage = 100
    render.film_transparent = False
    render.use_motion_blur = False
    render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = samples
    settings = render.image_settings
    settings.file_format = "JPEG" if max_bytes else "PNG"
    settings.color_mode = "RGB"
    if not max_bytes:
        settings.color_depth = "8"
    if frame is not None:
        scene.frame_set(frame)
    written = {}
    for view, spec in cameras.items():
        camera = kit.camera_object(bpy, "Scenario View " + view, spec["location"], spec["target"],
                                   spec.get("lens", 50.0), ortho_scale=spec.get("ortho_scale"))
        camera.data.sensor_width, camera.data.sensor_fit = SENSOR_WIDTH, "AUTO"
        scene.camera = camera
        bpy.ops.render.render(write_still=False, scene=scene.name)
        result = bpy.data.images["Render Result"]
        path = Path(folder) / ("%s%s.%s" % (prefix, view, "jpg" if max_bytes else "png"))
        if max_bytes:
            for quality in JPEG_QUALITIES:
                settings.quality = quality
                result.save_render(str(path), scene=scene)
                if path.stat().st_size <= max_bytes:
                    break
            else:
                raise ValueError("%s does not fit %d bytes at JPEG quality %d" % (view, max_bytes, quality))
        else:
            result.save_render(str(path), scene=scene)
        written[view] = str(path)
        bpy.data.objects.remove(camera, do_unlink=True)
    return written
