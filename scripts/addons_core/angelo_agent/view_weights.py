# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with WEIGHT shading: the targets' vertex weights, drawn without entering Weight Paint.

Blender shows weights only in Weight Paint mode (or as an Edit Mode overlay),
and a view never changes the mode. So this module draws the targets itself,
on the GPU into an off-screen buffer with a depth test, at the exact cameras
``view_capture`` planned (axis, FIT, SCENE_CAMERA, REUSE): Blender's weight
ramp (blue 0, cyan, green 0.5, yellow, red 1), unassigned vertices black,
each vertex shaded by the angle between its normal and the camera so the form
reads (doc/angelo/view_sources.rst, "Vertex weights and colour attributes").

Weights come from the original mesh's vertex group (``group``, else the
target's active one). Positions and triangles come from the evaluated mesh
when its vertex count matches, so armatures and shape keys deform it as in
the viewport; otherwise from the original mesh, which the result reports. The
evaluated mesh is the object's temporary ``to_mesh`` copy, cleared at once;
nothing enters the file. Main thread; model-facing text is English.
"""

import concurrent.futures
import time

from .view_png import encode_png

MAX_VERTICES = 500_000   # In all targets: weights are read one vertex at a time.
BACKGROUND = (0.05, 0.05, 0.055, 1.0)  # view_capture.BACKGROUND.
AMBIENT = 0.35           # Shade of a face seen edge-on; a face seen square-on is fully lit.
NOTE = ("Vertex weights drawn by the host (Blender shows them only in Weight Paint): blue 0, cyan 0.25, green 0.5, "
        "yellow 0.75, red 1, black where a vertex is not in the group, shaded by the angle to the camera. Only the "
        "targets are drawn. Not a render, and not authority for edits.")


def ramp(weights):
    """RGB from weights 0 to 1 on Blender's weight ramp; NaN (unassigned) is black. float32 (n, 3)."""
    import numpy
    w = numpy.clip(weights, 0.0, 1.0)
    rgb = numpy.stack([numpy.clip(4.0 * w - 2.0, 0.0, 1.0),
                       numpy.where(w < 0.75, numpy.clip(4.0 * w, 0.0, 1.0), numpy.clip(4.0 - 4.0 * w, 0.0, 1.0)),
                       numpy.clip(2.0 - 4.0 * w, 0.0, 1.0)], axis=1).astype(numpy.float32)
    rgb[numpy.isnan(weights)] = 0.0
    return rgb


def group_weights(obj, group_name):
    """``(weights per original vertex, NaN where unassigned; the group's name)``; RuntimeError without the group."""
    import numpy
    groups = obj.vertex_groups
    group = groups.get(group_name) if group_name is not None else groups.active
    if group is None:
        names = ", ".join(repr(item.name[:48]) for item in list(groups)[:8]) or "none"
        raise RuntimeError("'%s' has no vertex group %s. Its groups: %s" % (
            obj.name[:64], repr(group_name[:64]) if group_name is not None else "that is active", names))
    index, mesh = group.index, obj.data
    weights = numpy.full(len(mesh.vertices), numpy.nan, dtype=numpy.float32)
    for vertex in mesh.vertices:
        for element in vertex.groups:
            if element.group == index:
                weights[vertex.index] = element.weight
                break
    return weights, group.name


def _arrays(mesh):
    import numpy
    count, triangles = len(mesh.vertices), len(mesh.loop_triangles)
    positions, normals = numpy.empty(count * 3, numpy.float32), numpy.empty(count * 3, numpy.float32)
    indices = numpy.empty(triangles * 3, numpy.int32)
    mesh.vertices.foreach_get("co", positions)
    mesh.vertex_normals.foreach_get("vector", normals)
    mesh.loop_triangles.foreach_get("vertices", indices)
    return positions.reshape(count, 3), normals.reshape(count, 3), indices.reshape(triangles, 3)


def target_geometry(window, obj, group_name):
    """One target as world-space arrays: ``{"positions", "normals", "indices", "weights", facts}``."""
    import numpy
    if obj.type != "MESH":
        raise RuntimeError("WEIGHT draws vertex weights of meshes; '%s' is a %s" % (obj.name[:64], obj.type))
    weights, group = group_weights(obj, group_name)
    evaluated = obj.evaluated_get(window.view_layer.depsgraph)
    temporary = evaluated.to_mesh()
    try:
        deformed = len(temporary.vertices) == len(weights)
        positions, normals, indices = _arrays(temporary if deformed else obj.data)
    finally:
        evaluated.to_mesh_clear()
    matrix = numpy.array(evaluated.matrix_world, dtype=numpy.float64)
    rotation = matrix[:3, :3]
    world = positions @ rotation.T + matrix[:3, 3]
    # Normals turn by the inverse transpose; rows multiply by the inverse itself.
    turned = normals @ numpy.linalg.inv(rotation) if abs(numpy.linalg.det(rotation)) > 1e-12 else normals
    turned /= numpy.maximum(numpy.linalg.norm(turned, axis=1, keepdims=True), 1e-12)
    assigned = ~numpy.isnan(weights)
    facts = {"group": group[:64], "vertices": len(weights), "assigned": int(assigned.sum()),
             "deformed": deformed, "edit_mode": bool(obj.data.is_editmode)}
    if assigned.any():
        facts.update(minimum=round(float(numpy.nanmin(weights)), 4), maximum=round(float(numpy.nanmax(weights)), 4))
    return {"positions": world.astype(numpy.float32), "normals": turned.astype(numpy.float32), "indices": indices,
            "weights": weights, "facts": facts}


def shaded_colours(geometry, camera):
    """RGBA float32 per vertex for one camera: the weight ramp times the angle to the camera."""
    import numpy
    world = numpy.array(camera["matrix_world"], dtype=numpy.float64)
    if camera["projection"] == "PERSPECTIVE":
        towards = world[:3, 3] - geometry["positions"]
        towards /= numpy.maximum(numpy.linalg.norm(towards, axis=1, keepdims=True), 1e-12)
        facing = numpy.abs((geometry["normals"] * towards).sum(axis=1))
    else:
        forward = world[:3, 2] / max(numpy.linalg.norm(world[:3, 2]), 1e-12)
        facing = numpy.abs(geometry["normals"] @ forward)
    rgb = ramp(geometry["weights"]) * (AMBIENT + (1.0 - AMBIENT) * facing)[:, None]
    return numpy.concatenate([rgb, numpy.ones((len(rgb), 1), numpy.float32)], axis=1).astype(numpy.float32)


def _draw(geometries, camera, width, height):
    """Raw RGBA8 bytes (bottom row first) of the targets drawn at one camera."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            gpu.state.active_framebuffer_get().clear(color=BACKGROUND, depth=1.0)
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(True)
            try:
                with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.matrix.load_matrix(Matrix(camera["view_matrix"]))
                    gpu.matrix.load_projection_matrix(Matrix(camera["projection_matrix"]))
                    for geometry in geometries:
                        if len(geometry["indices"]):
                            batch = batch_for_shader(shader, "TRIS", {"pos": geometry["positions"],
                                                                      "color": shaded_colours(geometry, camera)},
                                                     indices=geometry["indices"])
                            batch.draw(shader)
            finally:
                gpu.state.depth_test_set("NONE")
                gpu.state.depth_mask_set(False)
        buffer = offscreen.texture_color.read()
        buffer.dimensions = width * height * 4
        return bytes(buffer)
    finally:
        offscreen.free()


def capture(window, plans, objects, checked):
    """``view_capture``'s capture record for WEIGHT: every plan drawn (never cached), plus the weight facts."""
    started = time.perf_counter()
    if sum(len(obj.data.vertices) for obj in objects if obj.type == "MESH") > MAX_VERTICES:
        raise RuntimeError("The targets have more than %d vertices in all; view fewer targets" % MAX_VERTICES)
    geometries = [target_geometry(window, obj, checked.get("group")) for obj in objects]
    encoded = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plans) or 1, thread_name_prefix="AngeloViewPNG") as pool:
        # plan["encode"]: encode_png, or a reference pair's (view_compare.encoder).
        futures = {index: pool.submit(plan.get("encode", encode_png),
                                      _draw(geometries, plan["camera"], plan["width"], plan["height"]),
                                      plan["width"], plan["height"])
                   for index, plan in enumerate(plans)}
        drawn = time.perf_counter()
        encoded = {index: future.result() for index, future in futures.items()}
    uncertainty = []
    if any(not geometry["facts"]["deformed"] for geometry in geometries):
        uncertainty.append({"code": "weights_undeformed", "detail": (
            "A target's modifiers change its vertex count, so it is drawn undeformed (its original mesh)")})
    if any(geometry["facts"]["edit_mode"] for geometry in geometries):
        uncertainty.append({"code": "weights_edit_mode", "detail": (
            "A target is in Edit Mode: its weights are those of its last mode change")})
    return {"encoded": encoded, "unavailable": [], "compiling": False,
            "draw_wall_ms": (drawn - started) * 1000, "encode_wait_wall_ms": (time.perf_counter() - drawn) * 1000,
            "weights": [geometry["facts"] for geometry in geometries], "uncertainty": uncertainty}
