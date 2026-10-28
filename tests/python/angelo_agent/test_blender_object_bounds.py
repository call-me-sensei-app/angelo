# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every way Angelo and Blender read an object's size, against the geometry it really has (background).

The class: a reader that reports a box the geometry does not have. Blender's cached and evaluated bounds padded a
legacy curve's points by their radius, a bevel scale (1 by default), as if it were a distance, so a planter's 6 cm
curve ribs read 2 m wide (Sol live trial, e2e 03020c0ecca); and an instance's temporary object kept its owner's curve
cache, so a curve's own mesh and every instance it makes read the owner's curve (break-it REVIEW-BOUNDS).

Held-out geometry, none of it that planter: curves with and without a bevel, a bevelled curve with an Array, a
Bezier, text filled and with Fill Mode None, a NURBS surface with a Mirror, a metaball, a lattice, a mesh (the
control), a mesh with a Subdivision Surface, Geometry Nodes instances of meshes and of curves, a curve whose nodes
leave only instances, and a collection instance of a modified curve.

The truth comes from the geometry itself, never from a bounds reader: evaluated meshes (``to_mesh``), a lattice's
points, and for a curve primitive instanced by nodes (which converts to no mesh) the circle it was built as. Readers:

* an object's own geometry (Blender's contract, instances left out): ``Object.bound_box`` and ``dimensions`` of the
  original and of ``evaluated_get``; each depsgraph instance's own object through its matrix (Cycles culls with the
  same box); Angelo's ``angelo_script`` result bounds and per-turn dimensions;
* everything an object shows (instances included): the live part's size (native) and ``angelo_view`` framing.

Each box is compared per axis, minimum and maximum (a dimensions reader by size), within ``TOLERANCE`` of the
truth's largest extent plus ``EPSILON``. A reader with no truth to compare against fails. Two documented classes are
kept as recorded exceptions, never passed silently: a Subdivision Surface's readers may read the cage, an upper bound
(the limit surface lies inside it); and an object with no geometry of its own (its nodes leave only instances, an
empty) has no own box to compare, so its own readers are listed, with what they read.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-object-bounds-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import object_bounds, scene_query_results, script_kit, turn_context, view_capture  # noqa: E402

TOLERANCE, EPSILON = 0.05, 1e-3   # Of the truth's largest extent, plus a millimetre (Angelo rounds to 1 mm).
CIRCLE = 0.1                      # The radius of the curve circles the nodes instance (their own box, the truth).
report = {"checks": {}, "readings": {}, "exceptions": [], "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "paid_requests": 0}


def link(scene, obj):
    scene.collection.objects.link(obj)
    return obj


def node_group(name, build):
    group = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    group.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    source, output = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    links.new(build(nodes, links, source.outputs[0]), output.inputs[0])
    return group


def instance_on(points_of, instance):
    def build(nodes, links, geometry):
        node = nodes.new("GeometryNodeInstanceOnPoints")
        links.new(points_of(nodes, links, geometry), node.inputs["Points"])
        links.new(instance(nodes), node.inputs["Instance"])
        return node.outputs["Instances"]
    return build


def grid(nodes, links, geometry):
    node = nodes.new("GeometryNodeMeshGrid")
    node.inputs["Size X"].default_value = node.inputs["Size Y"].default_value = 1.0
    return node.outputs["Mesh"]


def cube(nodes):
    node = nodes.new("GeometryNodeMeshCube")
    node.inputs["Size"].default_value = (0.1, 0.1, 0.1)
    return node.outputs["Mesh"]


def circle(nodes):
    node = nodes.new("GeometryNodeCurvePrimitiveCircle")
    node.inputs["Radius"].default_value = CIRCLE
    return node.outputs["Curve"]


def poly(name, points, bevel=0.0):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions, curve.bevel_depth = '3D', bevel
    spline = curve.splines.new('POLY')
    spline.points.add(len(points) - 1)
    for point, co in zip(spline.points, points):
        point.co = (*co, 1.0)
    return bpy.data.objects.new(name, curve)


RIB = [(0, 0, 0), (0.1, 0.02, 0), (0.2, 0.0, 0.03), (0.3, 0.01, 0.0)]


def fixtures(scene):
    """{name: (object, {"cage": ..., "instance_box": ...})}; each object stands apart from the others."""
    made = {}

    def add(obj, location, **flags):
        obj.location = location
        made[obj.name] = (link(scene, obj), flags)
        return obj
    add(poly("Poly curve, bevel 2 mm", RIB, 0.002), (0, 0, 0))
    add(poly("Poly curve, no bevel", RIB), (1, 0, 0))
    arrayed = add(poly("Bevelled curve with an Array", [(0, 0, 0), (0.2, 0, 0)], 0.01), (2, 0, 0))
    array = arrayed.modifiers.new("Array", 'ARRAY')
    array.count, array.relative_offset_displace = 3, (0, 2, 0)
    bezier = bpy.data.curves.new("Bezier curve, bevel 1 cm", 'CURVE')
    bezier.dimensions, bezier.bevel_depth = '3D', 0.01
    spline = bezier.splines.new('BEZIER')
    spline.bezier_points.add(1)
    for point, co in zip(spline.bezier_points, ((0, 0, 0), (0.4, 0.1, 0))):
        point.co, point.handle_left_type, point.handle_right_type = co, 'AUTO', 'AUTO'
    add(bpy.data.objects.new(bezier.name, bezier), (3, 0, 0))
    for name, fill, x in (("Text", 'BOTH', 4), ("Text, Fill Mode None", 'NONE', 5)):
        text = bpy.data.curves.new(name, 'FONT')
        text.body, text.size, text.fill_mode = "Angelo", 0.2, fill
        add(bpy.data.objects.new(name, text), (x, 0, 0))
    bpy.ops.surface.primitive_nurbs_surface_surface_add(radius=0.25, location=(6, 0, 0))
    surface = bpy.context.active_object
    surface.name = "NURBS surface with a Mirror"
    for collection in surface.users_collection:   # The operator links it to the active collection.
        collection.objects.unlink(surface)
    mirror_at = link(scene, bpy.data.objects.new("Mirror centre", None))   # The mirrored copy lands beside it.
    mirror_at.location = (6.6, 0, 0)
    mirror = surface.modifiers.new("Mirror", 'MIRROR')
    mirror.use_axis, mirror.mirror_object = (True, False, False), mirror_at
    add(surface, (6, 0, 0))
    ball = bpy.data.metaballs.new("Metaball")
    ball.resolution = 0.1
    ball.elements.new().radius = 0.6
    add(bpy.data.objects.new("Metaball", ball), (8, 0, 0))
    lattice = add(bpy.data.objects.new("Lattice", bpy.data.lattices.new("Lattice")), (9, 0, 0))
    lattice.scale = (0.3, 0.4, 0.5)
    mesh = bpy.data.meshes.new("Cube")
    mesh.from_pydata([(x, y, z) for x in (0, 0.5) for y in (0, 0.5) for z in (0, 0.5)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    add(bpy.data.objects.new("Mesh (control)", mesh), (10, 0, 0))
    sub = add(bpy.data.objects.new("Mesh with a Subdivision Surface", mesh.copy()), (11, 0, 0), cage=True)
    sub.modifiers.new("Subdivision", 'SUBSURF').levels = 2
    vertex = bpy.data.meshes.new("Vertex")
    vertex.from_pydata([(0, 0, 0)], [], [])
    for name, build, x, flags in (
            ("Mesh instances from nodes", instance_on(grid, cube), 13, {"instances": 9}),   # A 3 x 3 grid.
            ("Curve instances from nodes", instance_on(grid, circle), 15,
             {"instance_box": ((-CIRCLE, -CIRCLE, 0.0), (CIRCLE, CIRCLE, 0.0)), "instances": 9})):
        obj = add(bpy.data.objects.new(name, vertex.copy()), (x, 0, 0), **flags)
        obj.modifiers.new("Nodes", 'NODES').node_group = node_group(name, build)
    path = add(poly("Curve leaving only instances", [(0.2 * i, 0, 0) for i in range(11)]), (17, 0, 0), instances=11)
    path.modifiers.new("Nodes", 'NODES').node_group = node_group(
        "Cubes on a curve", instance_on(lambda nodes, links, geometry: geometry, cube))
    hidden = bpy.data.collections.new("Instanced curve")
    held = poly("Instanced bevelled curve", [(0, 0, 0), (0.3, 0, 0)], 0.02)
    hidden.objects.link(held)
    copies = held.modifiers.new("Array", 'ARRAY')
    copies.count, copies.relative_offset_displace = 2, (0, 3, 0)
    empty = add(bpy.data.objects.new("Collection instance of a modified curve", None), (20, 0, 0), instances=1)
    empty.instance_type, empty.instance_collection = 'COLLECTION', hidden
    held_out(add, vertex)
    return made


def held_out(add, vertex):
    """Cases added after REVIEW-BOUNDS-2. Named by that review, so not held out from this fix: instances away from
    their object's origin, Object Info instances of a curve made a mesh, a hidden curve; named by REVIEW-INSTANCES:
    a collection instance away from its origin. Neither named: nested instances, instances realized by nodes. The
    held-out validation of this fix is the cases its next review names, run unchanged."""
    def moved(nodes, links, geometry):   # Points 3 m from the object's origin, which lies outside its instances.
        node = nodes.new("GeometryNodeTransform")
        node.inputs["Translation"].default_value = (3.0, 0.0, 0.0)
        links.new(grid(nodes, links, geometry), node.inputs["Geometry"])
        return node.outputs["Geometry"]

    def realized(nodes, links, geometry):
        node = nodes.new("GeometryNodeRealizeInstances")
        links.new(instance_on(grid, cube)(nodes, links, geometry), node.inputs["Geometry"])
        return node.outputs["Geometry"]

    def tube(nodes, links, geometry):
        node, profile = nodes.new("GeometryNodeCurveToMesh"), nodes.new("GeometryNodeCurvePrimitiveCircle")
        profile.inputs["Radius"].default_value = 0.03
        links.new(geometry, node.inputs["Curve"])
        links.new(profile.outputs["Curve"], node.inputs["Profile Curve"])
        return node.outputs["Mesh"]
    for name, build, x, count in (("Instances away from their object's origin", instance_on(moved, cube), 23, 9),
                                  ("Instances realized by nodes", realized, 30, None)):
        add(bpy.data.objects.new(name, vertex.copy()), (x, 0, 0), instances=count).modifiers.new(
            "Nodes", 'NODES').node_group = node_group(name, build)
    piped = add(poly("Curve made a mesh by nodes", [(0, 0, 0), (0.4, 0, 0.1)]), (32, 0, 0))
    piped.modifiers.new("Nodes", 'NODES').node_group = node_group("Tube", tube)

    def object_info(nodes):   # The piped curve itself as each instance (a temporary object keeping its geometry set).
        node = nodes.new("GeometryNodeObjectInfo")
        node.inputs["Object"].default_value, node.inputs["As Instance"].default_value = piped, True
        return node.outputs["Geometry"]
    add(bpy.data.objects.new("Instances of a curve made a mesh", vertex.copy()), (34, 0, 0), instances=9).modifiers.new(
        "Nodes", 'NODES').node_group = node_group("Curve instances", instance_on(grid, object_info))
    inner = bpy.data.collections.new("Instanced instancer")
    instancer = bpy.data.objects.new("Instancer in a collection", vertex.copy())
    instancer.modifiers.new("Nodes", 'NODES').node_group = node_group("Inner cubes", instance_on(grid, cube))
    inner.objects.link(instancer)
    hiding = add(bpy.data.objects.new("Instancer hiding itself", vertex.copy()), (44, 0, 0), instances=9)
    hiding.modifiers.new("Nodes", 'NODES').node_group = node_group("Hidden maker", instance_on(grid, cube))
    hiding.show_instancer_for_viewport = False   # Its own row is never yielded (REVIEW-INSTANCES-5 M-B).
    nest = add(bpy.data.objects.new("Nested instances", None), (36, 0, 0), instances=9)   # The inner grid's cubes.
    nest.instance_type, nest.instance_collection = 'COLLECTION', inner
    away = bpy.data.collections.new("Placed away")
    member = bpy.data.objects.new("Cube placed away", bpy.data.meshes["Cube"].copy())
    member.location = (3.0, 1.0, 0.0)   # 3 m from the empty: its origin lies outside what it shows.
    away.objects.link(member)
    far = add(bpy.data.objects.new("Collection instance away from its origin", None), (42, 0, 0), instances=1)
    far.instance_type, far.instance_collection = 'COLLECTION', away
    twin = add(poly("Visible twin of a hidden curve", RIB, 0.004), (39, 0, 0))
    hidden = add(bpy.data.objects.new("Hidden bevelled curve, never evaluated", twin.data), (40, 0, 0),
                 twin=twin.name)   # Hidden before any evaluation; the same data its twin shows is its truth.
    hidden.hide_viewport = True


def box(points):
    points = list(points)
    if not points:
        return None
    return [[min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]]


def union(*boxes):
    boxes = [b for b in boxes if b]
    return box([b[0] for b in boxes] + [b[1] for b in boxes]) if boxes else None


def corners(obj, matrix):
    return box(matrix @ Vector(c) for c in obj.bound_box)


def mesh_box(obj, matrix):
    try:
        data = obj.to_mesh()
    except RuntimeError:
        return None
    found = box(matrix @ v.co for v in data.vertices) if data is not None else None
    obj.to_mesh_clear()
    return found


def geometry_truth(obj, matrix, flags):
    """World box of ``obj``'s own geometry from the geometry itself, or None."""
    if obj.type == 'LATTICE':
        return box(matrix @ point.co_deform for point in obj.data.points)
    found = mesh_box(obj, matrix)
    if found is None and flags.get("instance_box"):   # A curve primitive: no mesh, the circle it was built as.
        low, high = flags["instance_box"]
        found = box(matrix @ Vector((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1])
                    for z in (low[2], high[2]))
    return found


ELEMENTS = {"mesh": "vertices", "curves": "points", "pointcloud": "points", "volume": "grids",
            "grease_pencil": "layers"}


def owns_geometry(evaluated):
    """Whether ``evaluated`` has geometry of its own (an empty mesh from nodes that leave only instances has none)."""
    if evaluated.type == 'LATTICE':
        return True
    if evaluated.type == 'EMPTY':
        return False
    geometry = bpy.types.GeometrySet.from_evaluated_object(evaluated)
    return any(len(getattr(data, elements)) for kind, elements in ELEMENTS.items()
               if (data := getattr(geometry, kind, None)) is not None)


NO_LEVEL = 2 ** 31 - 1   # A persistent id's unused levels: a dupli at the top level has every level past 0 unused.


def instances(depsgraph, made):
    """{fixture name: [(type, box read, truth, component)]} for every depsgraph instance a fixture makes, read during
    the iteration (an instance's temporary object is reused for the next one). ``component``: a part of the object's
    own geometry drawn as a dupli (a curve's mesh), at the top level and of the object itself, never an instance."""
    owners = {obj: name for name, (obj, _flags) in made.items()}
    found = {name: [] for name in made}
    for instance in depsgraph.object_instances:
        name = owners.get(instance.parent.original) if instance.is_instance and instance.parent else None
        if name is not None:
            temporary, matrix = instance.object, instance.matrix_world.copy()
            component = temporary.original == made[name][0] and all(i == NO_LEVEL for i in instance.persistent_id[1:])
            found[name].append((temporary.type, corners(temporary, matrix),
                                geometry_truth(temporary, matrix, made[name][1]), component))
    return found


def native_size(obj):
    """The live part's size (``_bpy._angelo_live.depsgraph_size``) of a scene holding only ``obj``."""
    import _bpy
    scene = bpy.data.scenes.new("Only " + obj.name)
    try:
        scene.collection.objects.link(obj)
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            graph = bpy.context.evaluated_depsgraph_get()
        size = _bpy._angelo_live.depsgraph_size(graph)
        return [[0.0, 0.0, 0.0], list(size)] if size else None
    finally:
        scene.collection.objects.unlink(obj)
        bpy.data.scenes.remove(scene)


def within(found, truth, sized=False):
    """``found`` matches ``truth`` per axis (a dimensions reader by size)."""
    tolerance = TOLERANCE * max(truth[1][i] - truth[0][i] for i in range(3)) + EPSILON
    if sized:
        return all(abs((found[1][i] - found[0][i]) - (truth[1][i] - truth[0][i])) <= tolerance for i in range(3))
    return all(abs(found[k][i] - truth[k][i]) <= tolerance for k in (0, 1) for i in range(3))


def inside(found, outer, inner, sized=False):
    """``found`` holds ``inner`` and lies within ``outer`` (an upper bound such as a subdivision cage); a dimensions
    reader by size."""
    tolerance = TOLERANCE * max(inner[1][i] - inner[0][i] for i in range(3)) + EPSILON
    if sized:
        size = [[found[1][i] - found[0][i] for i in range(3)] for found in (found, outer, inner)]
        return all(size[2][i] - tolerance <= size[0][i] <= size[1][i] + tolerance for i in range(3))
    return all(found[0][i] <= inner[0][i] + tolerance and found[1][i] >= inner[1][i] - tolerance
               and found[0][i] >= outer[0][i] - tolerance and found[1][i] <= outer[1][i] + tolerance
               for i in range(3))


def rounded(found):
    return [[round(v, 4) for v in corner] for corner in found] if isinstance(found, list) else found


def grade(name, reader, found, truth, sized=False, cage=None, unmeasured=None):
    key = "%s: %s" % (name, reader)
    report["readings"][key] = {"read": rounded(found), "truth": rounded(truth)}
    if unmeasured:   # Nothing to compare (no geometry of its own, a reader for other types): listed, with its read.
        report["exceptions"].append({"check": key, "class": unmeasured, "read": rounded(found)})
        return
    if truth is None or not isinstance(found, list):
        report["checks"][key] = False   # A reader with no truth to compare, or none read, fails.
        return
    ok = within(found, truth, sized)
    if not ok and cage is not None and inside(found, cage, truth, sized):
        report["exceptions"].append({"check": key, "class": "subdivision cage (an upper bound)",
                                     "read": rounded(found), "cage": rounded(cage)})
        ok = True
    report["checks"][key] = ok


def dimensions(obj):
    return [[0.0, 0.0, 0.0], list(obj.dimensions)]


def angelo_readers(name, obj, own_truth, cage, own, depsgraph):
    """Angelo's own size facts: the box its own geometry has, or none stated (no geometry of its own, a curve no
    depsgraph evaluates: its data's box pads every point by its radius)."""
    found = object_bounds.world_bounds(obj, depsgraph=depsgraph)
    entry = {}
    facts_of = getattr(object_bounds, "InstanceFacts", None)
    try:
        turn_context._shape(obj, entry, facts_of(depsgraph) if facts_of else None)
    except TypeError:   # Before instance facts: the reader took no third argument.
        turn_context._shape(obj, entry)
    dims = [[0, 0, 0], entry["dimensions"]] if "dimensions" in entry else None
    if not own:
        report["checks"]["%s: angelo_script result bounds states no box" % name] = found is None
        report["checks"]["%s: per-turn dimensions state no box" % name] = dims is None
        report["readings"]["%s: Angelo's own box" % name] = {"script": found, "per-turn": dims}
        return entry
    grade(name, "angelo_script result bounds", found, own_truth, cage=cage,
          unmeasured=None if obj.type in object_bounds.GEOMETRY_TYPES else "reports geometry types only")
    grade(name, "per-turn dimensions", dims, own_truth, True, cage)
    return entry


def instance_readers(name, obj, entry, rows, depsgraph, window, expected):
    """What its instances show, as every reader states it: the per-turn fact, a script's and a program's row
    (``InstanceFacts``) and the scene object read, against their geometry; none stated for an object built to make
    none (the ``instances`` it was built with). Read through ``getattr``: without them (before) they fail."""
    shown = [truth for _kind, _read, truth, component in rows if truth is not None and not component]
    truth = union(*shown)
    facts_of = getattr(object_bounds, "InstanceFacts", None)
    detail = getattr(scene_query_results, "instance_detail", lambda window, obj: {})
    facts = {"per-turn objects[].instances": entry.get("instances"),
             "script and program rows' instances": facts_of(depsgraph).of(obj) if facts_of else None,
             "angelo_scene_object instances": detail(window, obj).get("instances")}
    for reader, fact in facts.items():
        if expected is None:
            report["checks"]["%s: %s states none" % (name, reader)] = fact is None
            continue
        grade(name, reader, fact.get("bounds") if fact else None, truth)


def run():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj)
    made = fixtures(scene)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    window = SimpleNamespace(view_layer=bpy.context.view_layer, scene=scene)
    made_instances, shown_truth, kit = instances(depsgraph, made), {}, script_kit.ScriptKit(bpy, scene)
    for name, (obj, flags) in made.items():
        evaluated = obj.evaluated_get(depsgraph)
        if flags.get("twin"):   # Never evaluated: its truth is the geometry its twin shows from the same data.
            twin = made[flags["twin"]][0].evaluated_get(depsgraph)
            own, own_truth = False, geometry_truth(twin, obj.matrix_world.copy(), flags)
        else:
            own = owns_geometry(evaluated)
            own_truth = geometry_truth(evaluated, evaluated.matrix_world.copy(), flags) if own else None
        rows = made_instances[name]
        shown = shown_truth[name] = union(own_truth, *(truth for _type, _read, truth, _component in rows))
        cage = (box(obj.matrix_world @ v.co for v in obj.data.vertices) if flags.get("cage") else None)
        none = None if own else ("not evaluated: Blender's box is its data's" if flags.get("twin")
                                 else "no geometry of its own: Blender's box is its data's")
        grade(name, "Object.bound_box (original)", corners(obj, obj.matrix_world), own_truth, cage=cage,
              unmeasured=none)
        grade(name, "Object.bound_box (evaluated)", corners(evaluated, evaluated.matrix_world), own_truth,
              cage=cage, unmeasured=none)
        grade(name, "Object.dimensions (original)", dimensions(obj), own_truth, True, cage, none)
        grade(name, "Object.dimensions (evaluated)", dimensions(evaluated), own_truth, True, cage, none)
        entry = angelo_readers(name, obj, own_truth, cage, own, depsgraph)
        if flags.get("twin"):
            if own_truth is not None:   # Its size, wherever its twin stands.
                report["readings"]["%s: twin's size" % name] = rounded(own_truth)
            continue   # Not shown: no instance, framing, size or kit reader sees it.
        instance_readers(name, obj, entry, rows, depsgraph, window, flags.get("instances"))
        for index, (kind, read, truth, _component) in enumerate(rows):
            grade(name, "instance %d %s bound_box (Cycles culling)" % (index, kind), read, truth,
                  unmeasured=None if truth is not None else "an instance with no geometry of its own")
        if obj.type != 'LATTICE':   # Neither the live part's size nor framing counts a lattice: it draws nothing.
            points, _truncated = view_capture._framing_points(window, [obj])
            grade(name, "angelo_view framing", box(points), shown, cage=cage)
            try:
                low, high = kit.bounds(obj) if shown is not None else (None, None)
            except ValueError:   # "No object with geometry matches": it found none of what the object shows.
                low = high = None
            grade(name, "kit.bounds (scripts)", [list(low), list(high)] if low else None, shown, cage=cage)
    limits(depsgraph, made, made_instances)   # Before the scenes below make the main depsgraph stale.
    if hasattr(object_bounds, "_update_count"):
        kept_facts_follow_changes(scene)
        depsgraph = bpy.context.evaluated_depsgraph_get()
    for name, (obj, flags) in made.items():   # Last: each one's own scene changes what the main depsgraph holds.
        if obj.type != 'LATTICE' and not flags.get("twin"):
            cage = box(obj.matrix_world @ v.co for v in obj.data.vertices) if flags.get("cage") else None
            grade(name, "live part size (native)", native_size(obj), shown_truth[name], True, cage)
    report["success"] = bool(report["checks"]) and all(report["checks"].values())


def kept_facts_follow_changes(scene):
    """The cache (kept by the depsgraph's update count): a kept fact is never stale after an edit or a frame change,
    even with every change handler cleared (REVIEW-INSTANCES-LATENCY M2: it watches none)."""
    bpy.app.handlers.depsgraph_update_post.clear()
    bpy.app.handlers.frame_change_post.clear()
    mesh = bpy.data.meshes.new("Kept probe")
    mesh.from_pydata([(0, 0, 0)], [], [])
    obj = bpy.data.objects.new("Kept probe", mesh)
    scene.collection.objects.link(obj)
    obj.modifiers.new("Nodes", 'NODES').node_group = node_group("Kept probe", instance_on(grid, cube))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    first = object_bounds.InstanceFacts(depsgraph).of(obj)
    report["checks"]["kept facts: a read after nothing changed is the kept fact"] = \
        first is not None and object_bounds.InstanceFacts(depsgraph).of(obj) is first
    obj.location.x += 50.0   # An edit: evaluated anew, so a new update count.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    moved = object_bounds.InstanceFacts(depsgraph).of(obj)
    report["checks"]["kept facts: an edit is never answered from the cache"] = bool(first and moved) and \
        abs((moved["bounds"][0][0] - first["bounds"][0][0]) - 50.0) < 0.01
    obj.animation_data_create()
    obj.location.x = 0.0
    obj.keyframe_insert("location", index=0, frame=1)
    obj.location.x = 10.0
    obj.keyframe_insert("location", index=0, frame=2)
    scene.frame_set(1)
    at_one = object_bounds.InstanceFacts(bpy.context.evaluated_depsgraph_get()).of(obj)
    scene.frame_set(2)
    at_two = object_bounds.InstanceFacts(bpy.context.evaluated_depsgraph_get()).of(obj)
    report["checks"]["kept facts: a frame change is never answered from the cache"] = bool(at_one and at_two) and \
        abs((at_two["bounds"][0][0] - at_one["bounds"][0][0]) - 10.0) < 0.01
    report["readings"]["kept facts"] = {"first": first, "moved": moved, "frame 1": at_one, "frame 2": at_two}
    scene.frame_set(1)
    bpy.data.objects.remove(obj)


def limits(depsgraph, made, rows):
    """REVIEW-INSTANCES-3 L-C: a per-object limit makes only the objects past it unknown (their own geometry still
    known); each object is expanded alone, so another object's fact never depends on it."""
    import _bpy
    native = getattr(_bpy._angelo_live, "instance_bounds", None)
    report["checks"]["limits: the native instance pass exists"] = native is not None
    if native is None:
        return
    shown = {name: sum(1 for _kind, _read, truth, component in found if truth is not None and not component)
             for name, found in rows.items()}
    instancers = [(name, obj) for name, (obj, flags) in made.items() if flags.get("instances")]
    found = native(depsgraph, [obj for _name, obj in instancers], 5)
    report["checks"]["limits: the evaluated depsgraph is read"] = found is not None
    for name, obj in instancers if found is not None else ():
        value = found.get(obj.session_uid)
        report["checks"]["limits: %s with a limit of 5 instances" % name] = \
            value is not None and ((value[0] is None) == (shown[name] > 5))
    alone = native(depsgraph, [instancers[0][1]], 0) if instancers else None
    report["checks"]["limits: an object asked alone is the only one read"] = \
        bool(alone) and set(alone) == {instancers[0][1].session_uid}


try:
    run()
except Exception:   # noqa: BLE001 - recorded, the run fails.
    report["error"] = traceback.format_exc()
    report["success"] = False
(OUT / "object-bounds.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
for key, ok in report["checks"].items():
    if not ok:
        print("FAILED %s: %s" % (key, json.dumps(report["readings"].get(key))))
print("OBJECT-BOUNDS %s: %d checks, %d failed, %d exceptions%s" % (
    "PASSED" if report["success"] else "FAILED", len(report["checks"]),
    sum(not ok for ok in report["checks"].values()), len(report["exceptions"]),
    "\n" + report["error"] if "error" in report else ""))
sys.stdout.flush()
if not report["success"]:
    os._exit(1)
