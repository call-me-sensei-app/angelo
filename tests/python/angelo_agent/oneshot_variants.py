# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Other ways to build a one-shot test's scene, graded by the test's own checks (stock Blender, unbilled).

A brief leaves the making to the model, so its closed checks must hold
however the scene is made, and fail only for what the brief asks and the
build lacks. The reference job proves that the checks pass on one build (the
test's stand-in). This job grades more: every variant starts from the
stand-in and either makes one thing another way (it must still pass every
check) or breaks one thing the brief asks for (it must fail exactly the
checks for it, named in ``VARIANTS``).

Run it in a background stock Blender before a test with new checks is given
to a model, and again when its checks change::

    Blender --background --factory-startup --python oneshot_variants.py -- oneshot_tree oneshot_cottage oneshot_castle

It prints one ``VARIANT`` line per build and ends with ``VARIANTS n of m as
expected``; the exit code is 0 only when all are. Nothing is saved.

The first version of the tree's checks failed leaves placed as instances
(the object that places them has no faces, no material and an origin on the
ground): that is what this job is for.
"""

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import oneshot_cottage_variants as cottage_variants  # noqa: E402

LEAF = ([(-0.08, -0.05, 0.0), (0.08, -0.05, 0.0), (0.08, 0.05, 0.0), (-0.08, 0.05, 0.0)], [(0, 1, 2, 3)])
LIMBS, PIECES = "at least five limbs spread from the fork", "foliage of a few thousand separate leaves or clusters"
ROOF = "terracotta tiles (the brief's, not the picture's slate)"
LANTERN = ("a lantern on the front wall right of the door, about 1.9 m up", "the lantern's glass glows warm",
           "a warm point light inside the lantern")
DOOR_SIZE = "a door 0.9 m wide and 2 m tall, mid front wall"
DOOR_POSITION, DOOR_VISIBLE = "the door is in the front wall", "the door can be seen from the front"


# ---------------------------------------------------------------- helpers

def _swap(bpy, obj, name, geometry, material=None):
    """Give an object another mesh and remove the one it had, as a tidy build would."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in geometry[0]], [], [tuple(f) for f in geometry[1]])
    mesh.update()
    for kept in [material] if material is not None else list(obj.data.materials):
        mesh.materials.append(kept)
    old, obj.data = obj.data, mesh
    bpy.data.meshes.remove(old)
    return obj


def _remove(bpy, objects):
    """Remove objects and the data only they used, as a tidy build would."""
    for obj in list(objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            (bpy.data.lights if isinstance(data, bpy.types.Light) else bpy.data.meshes).remove(data)


def _named(bpy, prefix):
    return [obj for obj in bpy.data.objects if obj.name.startswith(prefix)]


def _leaf_points(plan, count):
    rng = random.Random(1)
    return [(tip[0] + rng.uniform(-0.55, 0.55), tip[1] + rng.uniform(-0.55, 0.55), tip[2] + rng.uniform(-0.4, 0.4))
            for _ in range(count // len(plan["tips"]) + 1) for tip in plan["tips"]][:count]


def _instance_on_points(bpy, obj, instanced):
    """A Geometry Nodes modifier that leaves only instances of ``instanced`` on the object's points."""
    group = bpy.data.node_groups.new("Place Leaves", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    entry, result = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    source, place = group.nodes.new("GeometryNodeObjectInfo"), group.nodes.new("GeometryNodeInstanceOnPoints")
    source.inputs["Object"].default_value = instanced
    group.links.new(entry.outputs[0], place.inputs["Points"])
    group.links.new(source.outputs["Geometry"], place.inputs["Instance"])
    group.links.new(place.outputs["Instances"], result.inputs[0])
    obj.modifiers.new("Place Leaves", "NODES").node_group = group


# ---------------------------------------------------------------- the tree

def _tree(bpy):
    import oneshot_kit
    import scenario_oneshot_tree as tree
    tree.build(bpy)
    return tree, oneshot_kit.tree_plan(tree.HEIGHT, tree.SEED)


def tree_instances(bpy):
    import scenario_build_kit as kit
    _, plan = _tree(bpy)
    foliage, leaves = bpy.data.objects["Tree Foliage"], bpy.data.materials["Tree Leaves"]
    _swap(bpy, foliage, "Tree Foliage Points", (_leaf_points(plan, 3000), []), material=leaves)
    foliage.data.materials.clear()          # The object that places the leaves has no material of its own.
    leaf = kit.mesh_object(bpy, "Tree Leaf", *LEAF, location=(0.0, 0.0, 4.5))
    kit.assign(leaf, leaves)
    _instance_on_points(bpy, foliage, leaf)


def tree_leaf_objects(bpy):
    """1,600 leaf objects named as Blender names copies of 'Tree Foliage' ('Tree Foliage.001' on): the brief's
    exact name, so the variant tests the construction and not a naming."""
    _, plan = _tree(bpy)
    leaves = bpy.data.materials["Tree Leaves"]
    _remove(bpy, [bpy.data.objects["Tree Foliage"]])
    mesh = bpy.data.meshes.new("Tree Leaf")
    mesh.from_pydata(LEAF[0], [], LEAF[1])
    mesh.materials.append(leaves)
    for index, point in enumerate(_leaf_points(plan, 1600)):
        obj = bpy.data.objects.new("Tree Foliage.%03d" % index if index else "Tree Foliage", mesh)
        obj.location, obj.rotation_euler = point, (index * 0.7, index * 1.3, index * 0.4)
        bpy.context.scene.collection.objects.link(obj)


def tree_dense(bpy):
    import oneshot_kit
    _, plan = _tree(bpy)
    _swap(bpy, bpy.data.objects["Tree Foliage"], "Tree Foliage Dense",
          oneshot_kit.leaf_cards(plan["tips"], 60000, plan["scale"], 5))


def tree_blobs(bpy):
    import oneshot_kit
    import scenario_build_kit as kit
    _, plan = _tree(bpy)
    _swap(bpy, bpy.data.objects["Tree Foliage"], "Tree Foliage Blobs", oneshot_kit.merged(
        [kit.uv_sphere_geometry(0.75, 32, 16, centre=tip) for tip in plan["tips"][::4]]))


def tree_curves(bpy):
    _, plan = _tree(bpy)
    bark = bpy.data.materials["Tree Bark"]
    _remove(bpy, _named(bpy, "Tree Branch"))
    for index, tubes in enumerate(plan["limbs"]):
        curve = bpy.data.curves.new("Tree Branch %02d" % (index + 1), "CURVE")
        curve.dimensions, curve.bevel_depth, curve.bevel_resolution = "3D", 0.05, 2
        for points, _radii in tubes:
            spline = curve.splines.new("POLY")
            spline.points.add(len(points) - 1)
            for point, position in zip(spline.points, points):
                point.co = (*position, 1.0)
        curve.materials.append(bark)
        bpy.context.scene.collection.objects.link(bpy.data.objects.new(curve.name, curve))


def tree_fork_in_trunk(bpy):
    """The trunk object carries the fork and the first metre of every limb; the branches start further out."""
    import oneshot_kit
    _, plan = _tree(bpy)
    wood = [oneshot_kit.tube_geometry(*plan["trunk"], segments=12)]
    for index, tubes in enumerate(plan["limbs"]):
        points, radii = tubes[0]
        wood.append(oneshot_kit.tube_geometry(points[:5], radii[:5], segments=6))
        _swap(bpy, bpy.data.objects["Tree Branch %02d" % (index + 1)], "Tree Branch Outer", oneshot_kit.merged(
            [oneshot_kit.tube_geometry(points[4:], radii[4:], segments=6)]
            + [oneshot_kit.tube_geometry(p, r, segments=6) for p, r in tubes[1:]]))
    _swap(bpy, bpy.data.objects["Tree Trunk"], "Tree Trunk Forked", oneshot_kit.merged(wood))


def tree_three_limbs(bpy):
    _tree(bpy)
    keep = ("Tree Branch 01", "Tree Branch 03", "Tree Branch 05")
    _remove(bpy, [obj for obj in _named(bpy, "Tree Branch") if obj.name not in keep])


# ---------------------------------------------------------------- the cottage

def _cottage(bpy):
    import oneshot_kit
    import scenario_oneshot_cottage as cottage
    cottage.build(bpy)
    return cottage, oneshot_kit.cottage_plan(cottage.SIZE, cottage.EAVES, cottage.RIDGE)


def cottage_solid_walls(bpy):
    import oneshot_kit
    cottage, plan = _cottage(bpy)
    half_x, half_y = plan["half"]
    profile = [(-half_y, 0.0), (half_y, 0.0), (half_y, cottage.EAVES), (0.0, cottage.RIDGE - 0.12),
               (-half_y, cottage.EAVES)]
    _swap(bpy, bpy.data.objects["Cottage Walls"], "Cottage Walls Solid", oneshot_kit._prism(profile, -half_x, half_x))


def cottage_windows_as_two_objects(bpy):
    import oneshot_kit
    import scenario_build_kit as kit
    _, plan = _cottage(bpy)
    timber, glass = bpy.data.materials["Cottage Timber"], bpy.data.materials["Cottage Lit Glass"]
    parts = [oneshot_kit._window((x, y, z), outward, width, height) for x, y, z, outward, width, height in plan["windows"]]
    _remove(bpy, _named(bpy, "Cottage Window"))
    kit.assign(kit.mesh_object(bpy, "Cottage Window Frames", *oneshot_kit.merged([frame for frame, _ in parts])), timber)
    kit.assign(kit.mesh_object(bpy, "Cottage Window Glass", *oneshot_kit.merged([pane for _, pane in parts])), glass)


def cottage_dressed(bpy):
    """A lantern in two parts (its glass a Blender sibling, 'Lantern.001'), shutters and a sill on a front window,
    a door frame and a doorstep."""
    import scenario_build_kit as kit
    _, plan = _cottage(bpy)
    timber, stone = bpy.data.materials["Cottage Timber"], bpy.data.materials["Cottage Stone"]
    kit.assign(kit.box_object(bpy, "Lantern.001", (0.1, 0.1, 0.2), plan["lantern"]), bpy.data.materials["Lantern Glass"])
    front = plan["front"]
    for side in (-1, 1):
        kit.assign(kit.box_object(bpy, "Cottage Window Shutter %d" % (side + 2), (0.45, 0.04, 1.2),
                                  (2.4 + side * 0.74, front - 0.02, 1.6)), timber)
    kit.assign(kit.box_object(bpy, "Cottage Window Sill", (1.2, 0.16, 0.06), (2.4, front - 0.06, 0.97)), timber)
    kit.assign(kit.box_object(bpy, "Cottage Door Frame", (1.1, 0.06, 2.1), (0.0, front + 0.02, 1.05)), timber)
    kit.assign(kit.box_object(bpy, "Cottage Doorstep", (1.4, 0.5, 0.12), (0.0, front - 0.3, 0.06)), stone)


def cottage_slate_roof(bpy):
    _cottage(bpy)
    shader = next(node for node in bpy.data.materials["Cottage Terracotta Tiles"].node_tree.nodes
                  if node.type == "BSDF_PRINCIPLED")
    shader.inputs["Base Color"].default_value = (0.2, 0.21, 0.24, 1.0)


def cottage_no_lantern(bpy):
    _cottage(bpy)
    _remove(bpy, [bpy.data.objects["Lantern"], bpy.data.objects["Lantern Light"]])


def cottage_three_windows(bpy):
    _cottage(bpy)
    _remove(bpy, [obj for obj in _named(bpy, "Cottage Window") if obj.name[15:17] in ("04", "05")])


def cottage_buried_door(bpy):
    _cottage(bpy)
    bpy.data.objects["Cottage Door"].location.y += 0.25


# (what is built another way or broken, the builder, the labels of the checks that must fail: none when it follows the brief)
VARIANTS = {
    "oneshot_tree": (
        ("leaves as instances of one leaf on points", tree_instances, ()),
        ("leaves as 1,600 objects sharing a mesh", tree_leaf_objects, ()),
        ("leaves as one mesh of 60,000 cards", tree_dense, ()),
        ("limbs as bevelled curves", tree_curves, ()),
        ("the fork is part of the trunk object", tree_fork_in_trunk, ()),
        ("seven smooth blobs for foliage", tree_blobs, (PIECES,)),
        ("three limbs", tree_three_limbs, (LIMBS,)),
    ),
    "oneshot_cottage": (
        ("walls as one solid", cottage_solid_walls, ()),
        ("all frames in one object, all glass in another", cottage_windows_as_two_objects, ()),
        ("lantern in parts, shutters, a sill, a door frame, a doorstep", cottage_dressed, ()),
        ("a brick-textured terracotta roof", cottage_variants.brick_roof, ()),
        ("stone walls as a noise mix of two warm greys", cottage_variants.noise_walls, ()),
        ("a terracotta roof a Voronoi texture colours (measured)", cottage_variants.voronoi_roof, ()),
        ("a roof coloured through its UV map (measured)", cottage_variants.uv_roof, ()),
        ("a 1.6 m doorstep beside a 0.9 m leaf", cottage_variants.wide_doorstep, ()),
        # Placement the brief allows where a probe aims (a check tests exactly what the brief states).
        ("a rear window opening at the back wall's probe", cottage_variants.rear_window_opening, ()),
        ("a roof window let into the front slope", cottage_variants.roof_window, ()),
        ("the chimney through the ridge at x = -2.5", cottage_variants.inboard_chimney, ()),
        # Separately named parts in front of the leaf are other parts (the brief's naming): the door is not what
        # is seen there.
        ("decorative named planks over the door leaf", cottage_variants.door_overlays, (DOOR_VISIBLE,)),
        ("the picture's slate roof", cottage_slate_roof, (ROOF,)),
        ("a blue roof a Voronoi texture colours (measured)", cottage_variants.blue_voronoi_roof, (ROOF,)),
        ("no lantern", cottage_no_lantern, LANTERN),
        ("three windows", cottage_three_windows, ("at least four windows",)),
        ("the door buried in the wall", cottage_buried_door, (DOOR_VISIBLE,)),
        ("no door", cottage_variants.no_door, (DOOR_SIZE, DOOR_POSITION, DOOR_VISIBLE, "a timber door")),
        ("a door leaf 1.6 m wide", cottage_variants.wide_door, (DOOR_SIZE,)),
        ("a door one metre off centre", cottage_variants.off_centre_door, (DOOR_SIZE, DOOR_POSITION, DOOR_VISIBLE)),
        ("the door moved to the back", cottage_variants.rear_door, (DOOR_POSITION, DOOR_VISIBLE)),
        ("only two narrow door edge strips", cottage_variants.sparse_door, (DOOR_POSITION, DOOR_VISIBLE)),
        ("most of the door obscured by stone", cottage_variants.obscured_door, (DOOR_VISIBLE,)),
        # A seam on the leaf's centre line is a hole where the door is probed: the ray passes it (no sampling).
        ("a door mesh of planks with a 7 mm seam on its centre line", cottage_variants.plank_door,
         (DOOR_POSITION, DOOR_VISIBLE)),
        ("a door of six sibling plank objects with a 6 mm seam on its centre line", cottage_variants.plank_objects,
         (DOOR_POSITION, DOOR_VISIBLE)),
    ),
}

import castle_variants  # noqa: E402  (the castle's own variants, beside its checks)
VARIANTS["oneshot_castle"] = castle_variants.VARIANTS
import parallel_variants  # noqa: E402  (the parallel-agents tests' own variants, beside their kits)
VARIANTS.update(parallel_variants.VARIANTS)


def grade(bpy, name, label, builder, expected):
    """Build one variant on the factory scene and grade it; True when exactly the expected checks fail."""
    import scenario_checks
    import scenario_specs
    bpy.ops.wm.read_factory_settings(use_empty=False)
    builder(bpy)
    bpy.context.view_layer.update()
    result = scenario_checks.evaluate(bpy, bpy.context.scene, scenario_specs.tasks(name)[0]["scenario_checks"])
    failed = [entry for entry in result["results"] if not entry["passed"]]
    as_expected = sorted(entry.get("label") for entry in failed) == sorted(expected)
    print("VARIANT", json.dumps({"test": name, "variant": label, "as_expected": as_expected,
                                 "follows_the_brief": not expected,
                                 "failed": [entry.get("label") for entry in failed]}), flush=True)
    if not as_expected:
        for entry in failed:
            print("  DETAIL", entry.get("label"), json.dumps(entry.get("details"), default=str)[:400], flush=True)
    return as_expected


def main(names):
    names = names or sorted(VARIANTS)
    unknown = [name for name in names if name not in VARIANTS]
    if unknown:
        raise SystemExit("No variants for %s; choose from %s" % (", ".join(unknown), ", ".join(sorted(VARIANTS))))
    import bpy
    done = [grade(bpy, name, *variant) for name in names for variant in VARIANTS[name]]
    print("VARIANTS %d of %d as expected" % (sum(done), len(done)), flush=True)
    return 0 if all(done) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
