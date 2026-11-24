# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gaussian splats to clean meshes, and splats back to PLY (doc/angelo/features.rst, "Splats to meshes").

Blender 5.3 imports splats as a point cloud (``ply_import_gsplat.cc``): ``radiance:base`` holds the raw DC
spherical-harmonic coefficient in xyz and the decoded opacity in w, ``scale`` the decoded (exponentiated) axis
lengths, ``rotation`` a unit quaternion, ``radiance:sh_<i>`` the higher bands. It renders them but gives no geometry,
and cannot write them back (Blender #159470).

``angelo.splat_to_mesh`` makes a new mesh object beside the selected splat cloud and never changes the cloud:

1. **Floaters out, by density, not by a fixed number.** Each splat's cell (twice the median splat radius) sums the
   opacity of the splats in it; a splat is kept when its cell and the 26 around it hold at least a quarter of what
   the typical splat's neighbourhood holds (the median over splats: floaters, one per cell, can outnumber a small
   object's cells, never its splats; the neighbourhood keeps a surface's sparse edges, which have full cells beside
   them). Captured surfaces fill their cells; floaters are faint and alone. The rule is relative to the capture,
   so it holds for an object on a table and for a room alike.
2. **Volume, then surface.** The kept splats become a temporary vertex cloud carrying each splat's radius (1.5 x its
   largest axis), and a Geometry Nodes tree runs Mesh to Points, Points to Volume and Volume to Mesh (OpenVDB):
   compiled code, no Python loop over splats, and a closed surface by construction.
3. **Collision** (``kind='COLLISION'``): the voxel starts as coarse as the splats' radius allows without holes:
   every splat at least ``SHEET`` voxels wide (below). To fit ``max_triangles`` the surface stays where it is:
   Blender's Decimate Collapse (quadric error) thins the surface as made, kept only when it fits, stays closed, does
   not pass through itself (collapse can pull a thin shell's two sheets through each other) and lies no further off
   the splats than the surface it came from beyond sampling noise (``splat_measure``). Only when it can't does the
   voxel grow, from the fewest triangles the collapse reached and never past the raw-count rule's voxel, every
   splat widened with it, which moves every surface outward (a room's walls ended 2.55% of its diagonal off,
   2026-11-20); the voxels between a refused one and a kept one are searched, refining while each finer kept
   result lies measurably closer to the splats and further off than the median splat radius (the capture's own
   resolution), always ending within the budget (``splat_budget``). The
   report says which way the budget was met; the operator warns if a result is ever over it. Merging flat regions
   first (Planar decimate) was dropped: it rarely fit, cost most of a room's time (and 11 minutes on a million-splat
   floor plan), and left flat scenes' meshes further off the surface than collapse does. (Volume to Mesh's
   adaptivity changed no triangle count on these fog volumes, so it is not used.)
   Its convex-hull colliders come from ``collision_fit`` (at most ``max_hulls``), so Unreal, Unity and Godot all
   get collision, not only Godot through OMI.
   **Visual** (``kind='VISUAL'``): the same hole-free voxel with no budget, and a ``Color`` attribute, each vertex
   taking the colour (0.5 + 0.2821 x DC) of its nearest kept splat (``mathutils.kdtree``).

The operator reports kept and dropped splats, triangles and voxel size. ``angelo.export_splat_ply`` writes a splat
cloud in the standard 3D Gaussian Splatting PLY layout (opacity as a logit, scale as a log, DC and higher bands as
``f_dc_*``/``f_rest_*``), which Blender's importer reads back unchanged.
"""

import math

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from mathutils.kdtree import KDTree

from . import splat_budget
from .ui_text import n_, rpt_

SH_C0 = 0.28209479177387814
TEMP = splat_budget.TEMP


def read_attribute(attribute, width):
    """An attribute's values as float32 (n, width), whichever per-item field its type uses."""
    flat = np.empty(len(attribute.data) * width, dtype=np.float32)
    for field in ("vector", "color", "value"):
        try:
            attribute.data.foreach_get(field, flat)
            return flat.reshape(-1, width)
        except (AttributeError, TypeError, RuntimeError):
            continue
    raise TypeError("cannot read %s (%s)" % (attribute.name, attribute.data_type))


def is_splat_cloud(obj):
    return (obj is not None and obj.type == 'POINTCLOUD'
            and all(name in obj.data.attributes for name in ("radiance:base", "scale")))


def splats(obj):
    """World-space positions, opacity, radius and colour of a splat cloud."""
    attributes = obj.data.attributes
    local = read_attribute(attributes["position"], 3)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    positions = local @ matrix[:3, :3].T + matrix[:3, 3]
    base = read_attribute(attributes["radiance:base"], 4)
    scale = read_attribute(attributes["scale"], 3) * float(np.cbrt(abs(np.linalg.det(matrix[:3, :3]))))
    radius = 1.5 * scale.max(axis=1)
    colour = np.clip(0.5 + SH_C0 * base[:, :3], 0.0, 1.0)
    return positions, base[:, 3], radius, colour


def keep_mask(positions, opacity, radius):
    """Splats whose neighbourhood (their cell and the 26 around it) holds at least a quarter of the opacity that the
    typical splat's neighbourhood holds. Floaters are faint and alone; a sparse patch of a real surface (its edge, a
    thin part) has full cells beside it. "Typical" is the median over splats, not over cells: floaters occupy a cell
    each and on a small object outnumber its cells (two spheres, 2026-11-15), never its splats. A cell alone dropped
    real surface at a capture's sparse edges (3,410 of 60,000 splats on two spheres), so the neighbourhood counts."""
    cell = 2.0 * float(np.median(radius))
    keys = np.floor(positions / cell).astype(np.int64)
    keys -= keys.min(axis=0) - 1   # Codes for the cells and their neighbours (a margin of one cell each side).
    dims = keys.max(axis=0) + 2
    if float(np.prod(dims, dtype=np.float64)) >= 2.0 ** 62:
        # Too many cells for one int64 code (a capture tens of kilometres across at a few centimetres a cell, or a
        # stray point far away): codes would merge distinct cells. Keep every splat rather than judge them wrongly.
        return np.ones(len(positions), dtype=bool)
    # One integer per splat's cell, so the cells come from a 1-D unique (sorted, the same on every machine), not a
    # sort of rows: 8 x faster on a million splats, the same cells (2026-11-20).
    code, inverse = np.unique((keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2], return_inverse=True)
    inverse = inverse.ravel()
    weight = np.bincount(inverse, weights=opacity)
    sorted_code, sorted_weight = code, weight
    around = np.zeros(len(code))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                other = code + (dx * dims[1] + dy) * dims[2] + dz
                at = np.clip(np.searchsorted(sorted_code, other), 0, len(sorted_code) - 1)
                around += np.where(sorted_code[at] == other, sorted_weight[at], 0.0)
    threshold = 0.25 * float(np.median(around[inverse]))
    return around[inverse] >= threshold


def _menu(node, socket, choices):
    """Set a menu socket by the first choice it accepts (identifiers and labels differ between versions)."""
    for choice in choices:
        try:
            node.inputs[socket].default_value = choice
            return
        except (TypeError, ValueError):
            continue
    raise ValueError("no choice of %s accepted on %s" % (choices, node.name))


def _tree():
    tree = bpy.data.node_groups.new(TEMP, 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    for name, kind in (("Voxel Size", 'NodeSocketFloat'), ("Adaptivity", 'NodeSocketFloat')):  # i18n: socket names
        tree.interface.new_socket(name, in_out='INPUT', socket_type=kind)
    nodes, links = tree.nodes, tree.links
    group_in, group_out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    radius = nodes.new('GeometryNodeInputNamedAttribute')
    radius.data_type = 'FLOAT'
    radius.inputs["Name"].default_value = "angelo_radius"
    to_points = nodes.new('GeometryNodeMeshToPoints')
    to_volume = nodes.new('GeometryNodePointsToVolume')
    to_mesh = nodes.new('GeometryNodeVolumeToMesh')
    _menu(to_volume, "Resolution Mode", ("VOXEL_SIZE", "Size"))   # i18n: socket and menu item identifiers
    _menu(to_mesh, "Resolution Mode", ("VOXEL_SIZE", "Size"))   # i18n: socket and menu item identifiers
    to_mesh.inputs["Threshold"].default_value = 0.5
    links.new(group_in.outputs["Geometry"], to_points.inputs["Mesh"])
    links.new(radius.outputs["Attribute"], to_points.inputs["Radius"])
    links.new(to_points.outputs["Points"], to_volume.inputs["Points"])
    links.new(radius.outputs["Attribute"], to_volume.inputs["Radius"])
    links.new(group_in.outputs["Voxel Size"], to_volume.inputs["Voxel Size"])
    links.new(group_in.outputs["Voxel Size"], to_mesh.inputs["Voxel Size"])
    links.new(group_in.outputs["Adaptivity"], to_mesh.inputs["Adaptivity"])
    links.new(to_volume.outputs["Volume"], to_mesh.inputs["Volume"])
    links.new(to_mesh.outputs["Mesh"], group_out.inputs["Geometry"])
    return tree


def _surface(positions, radius, voxel, adaptivity):
    """The closed surface around the splats as a new mesh datablock (evaluated, then copied out)."""
    mesh = bpy.data.meshes.new(TEMP)
    mesh.vertices.add(len(positions))
    mesh.vertices.foreach_set("co", positions.astype(np.float32).ravel())
    attribute = mesh.attributes.new("angelo_radius", 'FLOAT', 'POINT')
    attribute.data.foreach_set("value", radius.astype(np.float32))
    holder = bpy.data.objects.new(TEMP, mesh)
    bpy.context.scene.collection.objects.link(holder)
    tree = _tree()
    modifier = holder.modifiers.new(TEMP, 'NODES')
    modifier.node_group = tree
    keys = {item.name: item.identifier for item in tree.interface.items_tree if getattr(item, "in_out", "") == 'INPUT'}
    inputs = modifier.properties.inputs   # Blender 5.3: generated property groups, not modifier ID properties.
    getattr(inputs, keys["Voxel Size"]).value = float(voxel)
    getattr(inputs, keys["Adaptivity"]).value = float(adaptivity)
    try:
        evaluated = holder.evaluated_get(bpy.context.evaluated_depsgraph_get())
        result = bpy.data.meshes.new_from_object(evaluated)
    finally:
        bpy.data.objects.remove(holder)
        bpy.data.meshes.remove(mesh)
        bpy.data.node_groups.remove(tree)
    return result


# How many voxels wide every splat must be so no captured wall vanishes. Points to Volume builds its level set with a
# narrow band only one voxel wide (geometry/intern/points_to_volume.cc creates the grid with background 1.0) and
# Volume to Mesh draws its fog at 0.5. A thin slab of splats inside a closed shell (a room's walls) then lost whole
# walls at some placements of the grid: at sqrt(3)/2 ("always holds a voxel centre") a room lost walls at 3 to 5 of 8
# grid phases at every voxel tried, 0.3 to 0.46 m off, at its first voxel too; at (sqrt(3) + 1)/2 it kept every wall
# at every phase, a uniform shell 0.065 to 0.081 m off (2026-11-21, the review of f3436a404a9;
# test_blender_splat_mesh's closed-shell phase sweep). The same walls meshed alone did not vanish, so the loss comes
# with enclosure (most likely the level set's sign flood fill misjudging slabs under two voxels thick), not the fog
# depth alone. That (sqrt(3) + 1)/2 suffices for enclosed shells is measured (8 grid phases x 3 voxels on one room),
# not derived; it also puts every point of a feature at least half a voxel inside its spheres. A wider narrow band is
# the exact fix, in C++: next release.
SHEET = (math.sqrt(3) + 1) / 2


# The last report of ``build`` (what the operator reported, with each step): a diagnostic the splat suite reads.
last_report = {}


def build(obj, kind, max_triangles):
    """The new mesh object for ``obj``'s splats, and a report of what was kept and made: ``reduction`` says how the
    collision mesh met its budget ("none": as made; "collapse"; "voxel": the voxel had to grow), ``steps`` what each
    voxel tried and ``within_budget`` whether it fits."""
    global last_report
    last_report = {}
    positions, opacity, radius, colour = splats(obj)
    # A splat with a non-finite position, radius or opacity (a broken capture) has no place in any cell or volume.
    usable = np.isfinite(positions).all(axis=1) & np.isfinite(radius) & (radius > 0) & np.isfinite(opacity)
    keep = np.zeros(len(positions), dtype=bool)
    if usable.any():
        keep[usable] = keep_mask(positions[usable], opacity[usable], radius[usable])
    positions, radius, colour = positions[keep], radius[keep], colour[keep]
    # The coarsest voxel the splats' own radius keeps hole-free (SHEET); Visual stays there, Collision may grow.
    voxel = float(np.median(radius)) / SHEET if len(radius) else 1.0
    steps, kept = [], {}
    if kind == 'VISUAL':
        mesh, reduction = _surface(positions, np.maximum(radius, SHEET * voxel), voxel, 0.0), "none"
    else:
        every = max(1, len(positions) // 2000)   # The capture each result is judged against (a fixed stride).
        mesh, voxel, reduction, kept = splat_budget.fit(
            lambda v: _surface(positions, np.maximum(radius, SHEET * v), v, 0.0), voxel, max_triangles, steps,
            positions[::every], radius[::every], SHEET, float(np.median(radius)))
    if kind == 'VISUAL' and len(mesh.vertices):
        tree = KDTree(len(positions))
        for index, point in enumerate(positions):
            tree.insert(point, index)
        tree.balance()
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coords)
        nearest = [tree.find(point)[1] for point in coords.reshape(-1, 3)]
        rgba = np.concatenate([colour[nearest], np.ones((len(nearest), 1))], axis=1).astype(np.float32)
        attribute = mesh.color_attributes.new("Color", 'FLOAT_COLOR', 'POINT')
        attribute.data.foreach_set("color", rgba.ravel())
        mesh.color_attributes.active_color = attribute
    mesh.name = "%s %s" % (obj.name, "Collision" if kind == 'COLLISION' else "Mesh")
    result = bpy.data.objects.new(mesh.name, mesh)
    for collection in obj.users_collection:
        collection.objects.link(result)
    report = {"kept": int(keep.sum()), "dropped": int((~keep).sum()), "triangles": splat_budget.triangles(mesh),
              "voxel_size": round(voxel, 5), "voxel": voxel, "reduction": reduction, "steps": steps}
    if kind == 'COLLISION':
        # Crossings as measured on the kept collapse. A surface kept as made comes from OpenVDB, closed and uncrossed
        # by construction, so it is not measured again (that would cost most on the largest budgets).
        report.update(within_budget=report["triangles"] <= max_triangles, crossings=kept.get("crossings"),
                      coverage_ok=kept.get("coverage", True))
    last_report = report
    return result, report


class ANGELO_OT_splat_to_mesh(bpy.types.Operator):
    bl_idname = "angelo.splat_to_mesh"
    bl_label = "Splats to Mesh"
    bl_description = "Make a clean mesh from the selected Gaussian splats: a closed collision mesh or a coloured mesh"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=(
        ('COLLISION', "Collision", "A closed, light mesh for physics and game engines"),
        ('VISUAL', "Visual", "A finer mesh coloured from the splats"),
    ), default='COLLISION')
    max_triangles: IntProperty(name="Triangle Budget", default=5000, min=100, max=2_000_000,
                               description="The most triangles a collision mesh may have")
    max_hulls: IntProperty(name="Most Hulls", default=16, min=1, max=64,
                           description="The most convex collision hulls fitted to a collision mesh")

    @classmethod
    def poll(cls, context):
        return is_splat_cloud(context.active_object)

    def execute(self, context):
        obj, report = build(context.active_object, self.kind, self.max_triangles)
        if not len(obj.data.polygons):
            self.report({'WARNING'}, rpt_("No surface found in these splats"))
            return {'CANCELLED'}
        if self.kind == 'COLLISION':
            # The mesh is concave static collision (Game Physics Mesh shape, Collision Only); its convex hulls
            # (collision_fit) serve moving bodies and engines that read only convex collision by name (Unreal).
            obj.angelo_physics.shape = 'TRIMESH'
            obj.angelo_physics.collision_only = True
            obj.display_type = 'WIRE'
            from . import collision_fit
            collision_fit.fit(obj, 'DECOMPOSE', self.max_hulls, 64)
        self.report({'INFO'}, rpt_("{name}: {kept} splats kept, {dropped} dropped as floaters, {triangles} triangles",
                                   name=obj.name, kept=report["kept"], dropped=report["dropped"],
                                   triangles=report["triangles"]))
        if not report.get("within_budget", True):   # Never expected (the search ends within it), but never silent.
            self.report({'WARNING'}, rpt_("{name}: {triangles} triangles, over the triangle budget of {budget}",
                                          name=obj.name, triangles=report["triangles"], budget=self.max_triangles))
        if not report.get("coverage_ok", True):   # The last resort lost part of the capture: never silent either.
            self.report({'WARNING'}, rpt_("{name}: this collision mesh may be missing part of the splats",
                                          name=obj.name))
        return {'FINISHED'}


def write_ply(obj, path):
    """The splat cloud in the 3D Gaussian Splatting PLY layout, in the cloud's own (local) space."""
    attributes = obj.data.attributes
    positions = read_attribute(attributes["position"], 3)
    base = read_attribute(attributes["radiance:base"], 4)
    scale = read_attribute(attributes["scale"], 3)
    rotation = (read_attribute(attributes["rotation"], 4) if "rotation" in attributes
                else np.tile([1.0, 0.0, 0.0, 0.0], (len(positions), 1)).astype(np.float32))
    bands = []
    index = 0
    while "radiance:sh_%d" % index in attributes:
        bands.append(read_attribute(attributes["radiance:sh_%d" % index], 3))
        index += 1
    opacity = np.clip(base[:, 3].astype(np.float64), 1e-7, 1 - 1e-7)
    columns = [positions, base[:, :3]]
    # f_rest is channel-major: every band's R, then every G, then every B.
    columns += [np.stack([band[:, channel] for band in bands], axis=1) for channel in range(3)] if bands else []
    columns += [np.log(opacity / (1 - opacity))[:, None], np.log(np.maximum(scale, 1e-12)), rotation]
    data = np.concatenate(columns, axis=1).astype("<f4")
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2"] + ["f_rest_%d" % i for i in range(3 * len(bands))]
    names += ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % len(data)   # i18n: PLY file format
    header += "".join("property float %s\n" % name for name in names) + "end_header\n"
    with open(path, "wb") as stream:
        stream.write(header.encode("ascii"))
        stream.write(data.tobytes())
    return len(data)


class ANGELO_OT_export_splat_ply(bpy.types.Operator):
    bl_idname = "angelo.export_splat_ply"
    bl_label = "Export Splats (.ply)"
    bl_description = "Write the selected Gaussian splats as a PLY file other splat tools read"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.ply", options={'HIDDEN'})
    # The browser warns before the person replaces a file (review S-S2).
    check_existing: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return is_splat_cloud(context.active_object)

    def invoke(self, context, _event):
        if not self.filepath:   # The document's folder and one clean name: never a folder from the object's name.
            from . import export_names
            self.filepath = export_names.default_export_path(context.active_object.name, ".ply", bpy.data.filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def check(self, _context):
        """A typed name keeps the .ply extension."""
        fixed = bpy.path.ensure_ext(self.filepath, ".ply")
        changed = fixed != self.filepath
        self.filepath = fixed
        return changed

    def execute(self, context):
        from . import owned_paths
        path = bpy.path.abspath(self.filepath)
        try:   # The person's pick in the file browser, which C recorded; a path a script names is refused.
            owned_paths.require_writable(path)
            count = write_ply(context.active_object, path)
        except OSError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_("Wrote {count} splats", count=count))
        return {'FINISHED'}


CLASSES = (ANGELO_OT_splat_to_mesh, ANGELO_OT_export_splat_ply)


TITLE = n_("Splats to mesh")
WHY = n_("A clean collision or coloured mesh from Gaussian splats")


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "splat_mesh", TITLE, WHY, order=24, section=chat_destinations.CREATE,
        operator=ANGELO_OT_splat_to_mesh.bl_idname, owner="splat_mesh"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("splat_mesh")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
