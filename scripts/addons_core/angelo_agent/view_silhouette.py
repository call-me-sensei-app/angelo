# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The targets alone at a view's camera: their silhouette and depth, for ``compare`` OUTLINE and DEPTH shading.

Drawn on the GPU into an off-screen buffer of the view's size, at the exact
camera the view is drawn with, flat and with a depth test, nothing else in the
scene: so the outline is the targets' own, whatever stands in front of them,
and each drawn pixel's depth gives back the 3D point it shows.

What is drawn is what the depsgraph draws for the targets
(``depsgraph.object_instances``): each target's own evaluated geometry and
every instance it makes (Geometry Nodes instances, instanced collections,
particle instances), each at its own matrix:

* meshes, and curves, surfaces, text and metaballs as Blender evaluates them
  into a mesh: their triangles;
* hair and other curves (``CURVES``, also an instance a node tree makes):
  each curve as a line as wide as its points' median radius, at least a
  sample (``SUPERSAMPLE``);
  a curves geometry with no curves draws nothing;
* particle hair (``ParticleSystem.co_hair``, parents and children), as lines
  at its strands' mean radius, up to ``MAX_HAIR_POINTS`` points (more is
  stated);
* anything else (empties, lights, point clouds, volumes) draws nothing and is
  counted (``not_drawn``).

Bounds are checked before any geometry is copied: ``MAX_TRIANGLES`` triangles
(counted from the evaluated meshes' own triangle counts) and
``MAX_SEGMENTS`` curve segments in all. Main thread only; nothing enters the
file, no data-block, Undo step or viewport change is made, and the GPU depth
state is put back as it was.
"""

MAX_TRIANGLES = 6_000_000
MAX_SEGMENTS = 3_000_000
MAX_HAIR_POINTS = 200_000
# Drawn with this many samples a pixel side, and a pixel any of its samples shows a target is the targets': an
# antialiased picture (a render, a viewport, a photo) shows a pixel the subject only touches as differing from
# its background, so a reference's subject is read that way (view_outline.THRESHOLD) and the silhouette is too.
# Drawn one sample a pixel, a comb's 4-pixel slots stayed open in the silhouette and closed in Blender's picture.
SUPERSAMPLE = 2
MESHLIKE = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META"})


def _world(positions, matrix):
    import numpy
    matrix = numpy.array(matrix, dtype=numpy.float64)
    return (positions.astype(numpy.float64) @ matrix[:3, :3].T + matrix[:3, 3]).astype(numpy.float32)


def _mesh_arrays(mesh):
    import numpy
    count, triangles = len(mesh.vertices), len(mesh.loop_triangles)
    positions, indices = numpy.empty(count * 3, numpy.float32), numpy.empty(triangles * 3, numpy.int32)
    mesh.vertices.foreach_get("co", positions)
    mesh.loop_triangles.foreach_get("vertices", indices)
    return positions.reshape(count, 3), indices.reshape(triangles, 3)


def _curves_arrays(curves):
    """``(local positions, segment index pairs, median radius)`` of evaluated curves; None when it has none."""
    import numpy
    count, curve_count = len(curves.points), len(curves.curves)
    if not count or not curve_count:
        return None
    positions = numpy.empty(count * 3, numpy.float32)
    curves.position_data.foreach_get("vector", positions)
    offsets = numpy.empty(curve_count + 1, numpy.int32)
    curves.curve_offset_data.foreach_get("value", offsets)
    last = numpy.zeros(count, dtype=bool)
    ends = offsets[1:] - 1
    last[ends[(ends >= 0) & (ends < count)]] = True      # A curve's last point starts no segment.
    starts = numpy.flatnonzero(~last).astype(numpy.int32)
    radius = curves.attributes.get("radius")
    median = 0.0
    if radius is not None and radius.domain == "POINT" and radius.data_type == "FLOAT" and len(radius.data) == count:
        values = numpy.empty(count, numpy.float32)
        radius.data.foreach_get("value", values)
        median = float(numpy.median(values))
    return positions.reshape(count, 3), numpy.column_stack((starts, starts + 1)), median


def _particle_hair(obj, budget):
    """Each particle hair system of an evaluated object as world line segments at its strands' mean radius
    (``radius_scale`` times the mean of root and tip): ``([(positions, pairs, radius)], points used, cut short)``."""
    import numpy
    found, used, cut = [], 0, False
    for system in obj.particle_systems:
        settings = system.settings
        if settings.type != "HAIR" or cut:
            continue
        steps, points, pairs = 2 ** settings.display_step + 1, [], []
        for particle in range(len(system.particles) + len(system.child_particles)):
            if used + steps > budget:
                cut = True
                break
            start = len(points)
            points.extend(tuple(system.co_hair(obj, particle_no=particle, step=step)) for step in range(steps))
            pairs.extend((index, index + 1) for index in range(start, start + steps - 1))
            used += steps
        if pairs:
            radius = settings.radius_scale * (settings.root_radius + settings.tip_radius) / 2.0
            found.append((numpy.array(points, numpy.float32), numpy.array(pairs, numpy.int32), radius))
    return found, used, cut


def _mesh_item(obj, matrix, meshes, counted):
    """One mesh-like instance's triangles at ``matrix``; ``counted(n)`` raises past the triangle bound first."""
    if obj.type == "MESH":
        mesh = obj.data
        counted(len(mesh.loop_triangles))
        key = mesh.as_pointer()
        if key not in meshes:
            meshes[key] = _mesh_arrays(mesh)
        local, indices = meshes[key]
    else:
        mesh = obj.to_mesh()
        try:
            if mesh is None:
                return None
            counted(len(mesh.loop_triangles))
            local, indices = _mesh_arrays(mesh)
        finally:
            obj.to_mesh_clear()
    return ("TRIS", _world(local, matrix), indices, 0.0) if len(indices) else None


def geometry(window, objects):
    """Everything the depsgraph draws for the targets, and what was left out: ``(items, facts)``.

    ``items`` are ``(kind, world positions, indices, radius)``; refuses at once past a bound, before copying.
    """
    depsgraph = window.view_layer.depsgraph
    wanted = {obj.session_uid for obj in objects}
    items, totals, drawn, meshes, cut = [], {"triangles": 0, "segments": 0, "hair_points": 0}, set(), {}, False

    def counted(triangles):
        totals["triangles"] += triangles
        if totals["triangles"] > MAX_TRIANGLES:
            raise RuntimeError("the targets draw more than %d triangles" % MAX_TRIANGLES)

    def lines(positions, pairs, radius, owner):
        totals["segments"] += len(pairs)
        if totals["segments"] > MAX_SEGMENTS:
            raise RuntimeError("the targets draw more than %d curve segments" % MAX_SEGMENTS)
        items.append(("LINES", positions, pairs, radius))
        drawn.add(owner)

    for instance in depsgraph.object_instances:
        owner = (instance.parent if instance.is_instance else instance.object).original.session_uid
        if owner not in wanted:
            continue
        obj = instance.object
        own = instance.is_instance or instance.show_self   # An emitter may hide itself and show only its particles.
        if own and obj.type in MESHLIKE:
            item = _mesh_item(obj, instance.matrix_world, meshes, counted)
            if item is not None:
                items.append(item)
                drawn.add(owner)
        elif own and obj.type == "CURVES":
            found = _curves_arrays(obj.data)
            if found is not None:
                lines(_world(found[0], instance.matrix_world), found[1], found[2], owner)
        if not instance.is_instance and instance.show_particles and len(getattr(obj, "particle_systems", ())):
            systems, used, short = _particle_hair(obj, MAX_HAIR_POINTS - totals["hair_points"])
            totals["hair_points"] += used
            cut = cut or short
            for positions, pairs, radius in systems:
                lines(positions, pairs, radius, owner)
    facts = {"triangles": totals["triangles"], "curve_segments": totals["segments"], "not_drawn": len(wanted - drawn)}
    if cut:
        facts["particle_hair_cut_at_points"] = MAX_HAIR_POINTS
    return items, facts


def _pixel_world(camera, width, height):
    """World size of one pixel: everywhere when orthographic, at look_at's distance when perspective."""
    details = camera["details"]
    if "ortho_scale" in details:
        return details["ortho_scale"] / max(width, height)
    if "look_at" in details and "lens_mm" in details:
        import math
        distance = math.dist(details["location"], details["look_at"])
        return distance * details["sensor_width_mm"] / details["lens_mm"] / max(width, height)
    return None


def draw(items, camera, width, height):
    """``(mask rows top first, depth rows top first, facts)`` of the items drawn at one camera.

    ``depth`` is the nearest drawn surface's window depth (0 near to 1 far; 1 where nothing is drawn), so a
    silhouette pixel gives back the 3D point it shows (``view_outline.unproject``).
    """
    import gpu
    import numpy
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix
    pixel = _pixel_world(camera, width, height)
    flat = gpu.shader.from_builtin("UNIFORM_COLOR")
    line = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    wide, high = width * SUPERSAMPLE, height * SUPERSAMPLE
    offscreen = gpu.types.GPUOffScreen(wide, high)
    strands = False
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            test, write = gpu.state.depth_test_get(), gpu.state.depth_mask_get()
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(True)
            try:
                with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.matrix.load_matrix(Matrix(camera["view_matrix"]))
                    gpu.matrix.load_projection_matrix(Matrix(camera["projection_matrix"]))
                    for kind, positions, indices, radius in items:
                        shader = flat if kind == "TRIS" else line
                        shader.bind()
                        shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
                        if kind == "LINES":
                            shader.uniform_float("viewportSize", (wide, high))
                            strand = 2.0 * radius * SUPERSAMPLE / pixel if pixel else 0.0
                            shader.uniform_float("lineWidth", max(1.0, strand))
                            strands = True
                        batch_for_shader(shader, kind, {"pos": positions}, indices=indices).draw(shader)
            finally:
                gpu.state.depth_test_set(test)
                gpu.state.depth_mask_set(write)
            depth = numpy.array(numpy.asarray(framebuffer.read_depth(0, 0, wide, high)), dtype=numpy.float32)[::-1]
        buffer = offscreen.texture_color.read()
        buffer.dimensions = wide * high * 4
        covered = numpy.frombuffer(bytes(buffer), dtype=numpy.uint8).reshape(high, wide, 4)[::-1, :, 3] > 127
    finally:
        offscreen.free()
    blocks = (height, SUPERSAMPLE, width, SUPERSAMPLE)
    facts = {"pixel_world": float("%.4g" % pixel)} if pixel else {}
    if strands:
        facts["strands"] = True
    return (covered.reshape(blocks).any(axis=(1, 3)), numpy.ascontiguousarray(depth.reshape(blocks).min(axis=(1, 3))),
            facts)
