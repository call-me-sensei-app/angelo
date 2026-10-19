# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Other builds of the face and hair tests' ground truths, and builds with one known defect (stock Blender).

The graders' own uncertainty, measured rather than assumed:

* **Other ways to make the same head or hair** (``follows the brief``): the
  ground truth meshed coarser and finer, triangulated, voxel-remeshed by a
  modifier, under a Subdivision Surface. Each must pass every check, and its
  errors are the graders' noise floor: what a perfect build reads as. Particle
  hair is checked against what Blender was asked to grow
  (``particle_hair_reading``): every strand, children included, read in world
  space on a moved and turned head, starting on its surface, the parents as
  long as asked. The same strands as legacy curve splines are strands too.
* **One known defect** (the nose 4 mm further forward, the eyes 3 mm higher,
  the jaw 4 mm wider, the mouth 25 % wider, the head 3 % larger, the hair 2 cm
  shorter, the hairline 6 mm lower, no hair, hair as solid tubes): each must
  fail at least the checks named for it, and the measured error is set beside
  the change that was made (the graders' calibration). Other checks a defect
  also fails are reported with their readings: the relief, for one, measures
  shape, so a feature moved whole along the face can leave it within bounds
  while the eye, landmark or outline check fails.

``VARIANTS`` plugs into ``oneshot_variants.py`` (``-- oneshot_head_portrait``
and the rest); ``--calibrate OUT.json`` also writes every variant's readings::

    Blender -b --factory-startup --python face_variants.py -- --calibrate OUT.json oneshot_head_portrait
"""

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

LANDMARKS = "the profile's brow, nose, lips and chin within 2 mm"
EYES = "both eyes within 2 mm of the pictures"
JAW = "the chin's bottom and the jaw line within 2 mm"
MOUTH = "the mouth line's corners and middle within 2 mm"
FRONT, SIDE = "the front outline within 2 mm of the front picture", "the side outline within 2 mm of the side picture"
RELIEF = "the face's relief within 2 mm, region by region"
STRANDS = "the hair is at least 10000 strands named 'Hair', rooted on the scalp, never inside the head"
SHAPE = "the hair covers what the pictures show (overlap 0.85) and its outline is within 4 mm"
HAIRLINE = "the hairline over the forehead within 3 mm"
LENGTH = "the hair's length at the back and the sides within 10 mm"


# ---------------------------------------------------------------- builders


def _module(name):
    import scenario_specs
    return scenario_specs.module(name)


def ground_truth(bpy, name):
    """What a trial's perfect agent leaves: the start file's head (when the test has one), then the brief's build."""
    import head_kit
    module = _module(name)
    if getattr(module, "START", None):
        head_kit.build_head(bpy, module.START_DESIGN, cell=module.START_CELL)
    module.build_turn(bpy, "brief")
    bpy.context.view_layer.update()


def _swap_head(bpy, design, cell=0.0012):
    import head_kit
    import head_sdf
    head = bpy.data.objects["Head"]
    vertices, quads = head_sdf.surface_nets(design, design["low"], design["high"], cell)
    mesh = head_kit.mesh_from_quads(bpy, "Head", vertices, quads)
    for material in head.data.materials:
        mesh.materials.append(material)
    old, head.data = head.data, mesh
    bpy.data.meshes.remove(old)


def _design(name):
    import head_designs
    return copy.deepcopy(head_designs.design(_module(name).FACE["design"]))


def _edit(design, part, change):
    """Apply ``change`` to every step of the design labelled ``part``."""
    touched = 0
    for step in design["steps"]:
        if step.get("part") == part:
            change(step)
            touched += 1
    if not touched:
        raise KeyError("no step %r in %s" % (part, design["name"]))
    return design


def _shift(step, offset):
    for key in ("centre", "a", "b"):
        if key in step:
            step[key] = tuple(value + delta for value, delta in zip(step[key], offset))


def remeshed(cell):
    def build(bpy, name):
        ground_truth(bpy, name)
        _swap_head(bpy, _design(name), cell)
    return build


def triangulated(bpy, name):
    import bmesh
    ground_truth(bpy, name)
    mesh = bpy.data.objects["Head"].data
    work = bmesh.new()
    work.from_mesh(mesh)
    bmesh.ops.triangulate(work, faces=work.faces[:])
    work.to_mesh(mesh)
    work.free()


def voxel_remesh_modifier(bpy, name):
    ground_truth(bpy, name)
    modifier = bpy.data.objects["Head"].modifiers.new("Remesh", "REMESH")
    modifier.mode, modifier.voxel_size, modifier.use_smooth_shade = "VOXEL", 0.001, True


def subdivided(bpy, name):
    ground_truth(bpy, name)
    _swap_head(bpy, _design(name), 0.002)
    modifier = bpy.data.objects["Head"].modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = modifier.render_levels = 1


def defect(part, change):
    def build(bpy, name):
        ground_truth(bpy, name)
        _swap_head(bpy, _edit(_design(name), part, change))
    build.design = lambda name: _edit(_design(name), part, change)   # Read analytically too (``calibrate``).
    return build


def eyes_moved(offset):
    def build(bpy, name):
        ground_truth(bpy, name)
        for key in ("Head Eye L", "Head Eye R"):
            obj = bpy.data.objects[key]
            obj.location = tuple(value + delta for value, delta in zip(obj.location, offset))
        design = _design(name)
        for step in design["steps"]:   # The sockets move with the eyes.
            if step.get("mirror") and step["kind"] in ("sphere", "ellipsoid") and abs(step["centre"][0] -
                                                                                    design["eyes"][0]["centre"][0]) < 0.004:
                _shift(step, offset)
        _swap_head(bpy, design)
    return build


def scaled(factor):
    def build(bpy, name):
        ground_truth(bpy, name)
        for key in ("Head", "Head Eye L", "Head Eye R"):
            obj = bpy.data.objects[key]
            obj.location = tuple(value * factor for value in obj.location)
            obj.scale = tuple(value * factor for value in obj.scale)
    return build


# ---------------------------------------------------------------- hair builders


def _replace_hair(bpy, name, change):
    import hair_kit
    import head_designs
    module = _module(name)
    for obj in [obj for obj in bpy.data.objects if obj.name.startswith("Hair")]:
        bpy.data.objects.remove(obj, do_unlink=True)
    positions, radii = hair_kit.strands(head_designs.design(module.FACE["design"]), module.FACE["hair"]["style"])
    positions = change(positions)
    return hair_kit.hair_object(bpy, "Hair", positions, radii, bpy.data.materials.get("Hair Brown"),
                                bpy.data.objects["Head"])


def hair_shorter(metres):
    def change(positions):
        import numpy
        lowest = positions[:, :, 2].min()
        cut = lowest + metres
        positions[:, :, 2] = numpy.maximum(positions[:, :, 2], numpy.minimum(cut, positions[:, :1, 2]))
        return positions

    def build(bpy, name):
        ground_truth(bpy, name)
        _replace_hair(bpy, name, change)
    return build


def hairline_lower(metres):
    def change(positions):
        front = positions[:, 0, 1] < -0.02
        positions[front, :, 2] -= metres
        return positions

    def build(bpy, name):
        ground_truth(bpy, name)
        _replace_hair(bpy, name, change)
    return build


def no_hair(bpy, name):
    ground_truth(bpy, name)
    for obj in [obj for obj in bpy.data.objects if obj.name.startswith("Hair")]:
        bpy.data.objects.remove(obj, do_unlink=True)


def particle_hair_reading(bpy, name, count=400, children=3, length=0.03):
    """The reader of particle hair, checked against what Blender was asked to grow: particle hair with
    interpolated children on the head, moved and turned so object and world space differ. Every strand read
    must start on the head's surface in world space and be ``length`` long (children within 25 %)."""
    import numpy
    import face_casters
    import face_truth
    ground_truth(bpy, name)
    for obj in [obj for obj in bpy.data.objects if obj.name.startswith("Hair")]:
        bpy.data.objects.remove(obj, do_unlink=True)
    head = bpy.data.objects["Head"]
    head.location = (0.05, 0.02, -0.01)
    head.rotation_euler = (0.0, 0.0, 0.3)
    system = head.modifiers.new("Hair", "PARTICLE_SYSTEM").particle_system
    settings = system.settings
    settings.type, settings.count, settings.hair_length = "HAIR", count, length
    settings.child_type, settings.child_percent, settings.rendered_child_count = "INTERPOLATED", children, children
    settings.display_step = 3
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    strands, sources = face_casters.hair_strands(depsgraph, face_casters.name_matcher([{"prefix": "Hair"}]))
    roots = numpy.asarray([strand[0] for strand in strands]) if strands else numpy.zeros((0, 3))
    distance = face_truth.scene_distance(depsgraph, face_casters.name_matcher([{"name": "Head"}]), roots)
    lengths = numpy.asarray([numpy.linalg.norm(numpy.diff(strand, axis=0), axis=1).sum() for strand in strands])
    parents = lengths[:count]
    # The reader is right when every strand is there, starts on the surface in world space and the parents are
    # as long as asked; how long Blender interpolates each child is Blender's own (its range is recorded).
    children_lengths = lengths[count:]
    return {"strands": len(strands), "asked": count * (1 + children), "sources": sources,
            "root_distance_max_mm": round(float(distance.max()) * 1000, 3) if len(distance) else None,
            "parent_length_mm": [round(float(parents.min()) * 1000, 2), round(float(parents.max()) * 1000, 2)],
            "child_length_mm": [round(float(children_lengths.min()) * 1000, 2),
                                round(float(children_lengths.max()) * 1000, 2)] if len(children_lengths) else None,
            "length_median_mm": round(float(numpy.median(lengths)) * 1000, 2) if len(lengths) else None,
            "ok": bool(len(strands) == count * (1 + children) and len(distance) and distance.max() < 0.0005
                       and numpy.allclose(parents, length, rtol=0.02)
                       and abs(numpy.median(lengths) - length) < 0.05 * length)}


def hair_as_tubes(bpy, name):
    """Hair as solid mesh tubes (a mesh named Hair), not strands: it should fail the strands check only."""
    import bmesh
    import numpy
    ground_truth(bpy, name)
    import face_casters
    hair = bpy.data.objects["Hair"]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    strands, _sources = face_casters.hair_strands(depsgraph, face_casters.name_matcher([{"prefix": "Hair"}]))
    work = bmesh.new()
    for points in strands[::3]:
        for a, b in zip(points[:-1], points[1:]):
            direction = b - a
            length = float(numpy.linalg.norm(direction))
            if length < 1e-6:
                continue
            side = numpy.cross(direction, (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 * length else (1.0, 0.0, 0.0))
            side = side / numpy.linalg.norm(side) * 0.0006
            up = numpy.cross(direction / length, side)
            ring_a = [work.verts.new(tuple(a + side * c + up * s)) for c, s in ((1, 0), (0, 1), (-1, 0), (0, -1))]
            ring_b = [work.verts.new(tuple(b + side * c + up * s)) for c, s in ((1, 0), (0, 1), (-1, 0), (0, -1))]
            for index in range(4):
                work.faces.new((ring_a[index], ring_a[(index + 1) % 4], ring_b[(index + 1) % 4], ring_b[index]))
    mesh = bpy.data.meshes.new("Hair Tubes")
    work.to_mesh(mesh)
    work.free()
    material = hair.data.materials[0] if hair.data.materials else None
    bpy.data.objects.remove(hair, do_unlink=True)
    tubes = bpy.data.objects.new("Hair Tubes", mesh)
    bpy.context.scene.collection.objects.link(tubes)
    if material is not None:
        mesh.materials.append(material)


# ---------------------------------------------------------------- the table


def _nose_forward(step):
    _shift(step, (0.0, -0.004, 0.0))


def _jaw_wider(step):
    _shift(step, (0.004, 0.0, 0.0))   # Mirrored parts: both sides move out.


def _mouth_wider(step):
    """Lips and mouth line 25 % wider (about 5 mm at each corner of the portrait's mouth)."""
    for key in ("centre", "a", "b"):
        if key in step:
            step[key] = (step[key][0] * 1.25,) + tuple(step[key][1:])
    if "radii" in step:
        step["radii"] = (step["radii"][0] * 1.25,) + tuple(step["radii"][1:])


# (what is built another way or broken, the builder, the checks it must fail: none when it follows the brief).
# A defect must fail at least its own checks; whatever else it fails is reported with its readings.
FACE_VARIANTS = (
    ("the ground truth as built", ground_truth, ()),
    ("meshed with 1.6 mm cells", remeshed(0.0016), ()),
    ("meshed with 0.8 mm cells", remeshed(0.0008), ()),
    ("triangulated", triangulated, ()),
    ("voxel-remeshed (1 mm) by a modifier", voxel_remesh_modifier, ()),
    ("a 2 mm mesh under a Subdivision Surface", subdivided, ()),
    ("the eyes 3 mm higher", eyes_moved((0.0, 0.0, 0.003)), (EYES,)),
    # Scaled about the origin: features near it (an anime head's eyes, 1.8 mm) move less than the tolerance, so
    # only the outlines (7 mm at the crown) are this defect's own checks; the rest are reported with their readings.
    ("the head 3 % larger", scaled(1.03), (FRONT, SIDE)),
)
NOSE = ("the nose tip 4 mm further forward", defect("nose tip", _nose_forward), (LANDMARKS,))
JAW_WIDER = ("the jaw 4 mm wider each side", defect("jaw", _jaw_wider), (FRONT,))
MOUTH_WIDER = ("the mouth 25 % wider", defect("mouth", _mouth_wider), (MOUTH,))
def fewer_strands(count):
    """The same hairstyle in ``count`` strands (the truth's roots are shuffled, so the first ``count`` are spread
    over the whole scalp): the brief asks for ten thousand at least, not the truth's 14,000 or 16,000."""
    def build(bpy, name):
        ground_truth(bpy, name)
        _replace_hair(bpy, name, lambda positions: positions[:count])
    return build


def hair_as_splines(bpy, name):
    """The truth's strands as one legacy curve object, a poly spline each with a thin bevel: still individual
    strands, so it must pass every check (both sides of a Sol run built hair this way)."""
    import face_casters
    ground_truth(bpy, name)
    hair = bpy.data.objects["Hair"]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    strands, _sources = face_casters.hair_strands(depsgraph, face_casters.name_matcher([{"prefix": "Hair"}]))
    curve = bpy.data.curves.new("Hair Splines", "CURVE")
    curve.dimensions, curve.bevel_depth, curve.bevel_resolution = "3D", 0.00008, 0
    for points in strands:
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        spline.points.foreach_set("co", [value for point in points for value in (*point, 1.0)])
    material = hair.data.materials[0] if hair.data.materials else None
    bpy.data.objects.remove(hair, do_unlink=True)
    splines = bpy.data.objects.new("Hair Splines", curve)
    bpy.context.scene.collection.objects.link(splines)
    if material is not None:
        curve.materials.append(material)


HAIR_VARIANTS = (
    ("the ground truth as built", ground_truth, ()),
    ("the same style in 10,000 strands, the brief's least", fewer_strands(10000), ()),
    ("the strands as legacy curve splines", hair_as_splines, ()),
    ("hair as solid mesh tubes", hair_as_tubes, (STRANDS,)),
    ("the hairline 6 mm lower", hairline_lower(0.006), (HAIRLINE,)),
    ("no hair", no_hair, (STRANDS, SHAPE, HAIRLINE, LENGTH)),
)
VARIANTS = {
    "oneshot_head_stylised": FACE_VARIANTS + (NOSE, MOUTH_WIDER),
    "oneshot_head_portrait": FACE_VARIANTS + (NOSE, JAW_WIDER, MOUTH_WIDER),
    "oneshot_head_anime": FACE_VARIANTS + (MOUTH_WIDER,),
    "oneshot_hair_short": HAIR_VARIANTS,
    "oneshot_hair_long": HAIR_VARIANTS + (("the hair 2 cm shorter", hair_shorter(0.02), (LENGTH, SHAPE)),),
}


def calibrate(bpy, names, out):
    """Every variant graded, with the readings that decide its checks; written to ``out`` as JSON."""
    import scenario_checks
    import scenario_specs
    table = []
    for name in names:
        for label, builder, expected in VARIANTS[name]:
            bpy.ops.wm.read_factory_settings(use_empty=False)
            builder(bpy, name)
            bpy.context.view_layer.update()
            result = scenario_checks.evaluate(bpy, bpy.context.scene, scenario_specs.tasks(name)[0]["scenario_checks"])
            failed = sorted(entry["label"] for entry in result["results"] if not entry["passed"])
            table.append({"test": name, "variant": label, "follows_the_brief": not expected,
                          "expected_failures": sorted(expected), "failed": failed,
                          "as_expected": not failed if not expected else set(expected) <= set(failed),
                          "readings": {entry["label"]: entry.get("details") for entry in result["results"]
                                       if entry["kind"].startswith(("face_", "hair"))}})
            if hasattr(builder, "design"):   # What the changed design itself reads, from its distance fields.
                import face_truth
                table[-1]["analytic"] = face_truth.errors(name, face_truth.design_reading(name, builder.design(name)))
            print("CALIBRATION", json.dumps({key: table[-1][key] for key in ("test", "variant", "as_expected",
                                                                                "failed")}), flush=True)
        if _module(name).FACE.get("hair"):
            bpy.ops.wm.read_factory_settings(use_empty=False)
            reading = particle_hair_reading(bpy, name)
            table.append({"test": name, "variant": "particle hair with children, read in world space",
                          "follows_the_brief": True, "expected_failures": [], "failed": [],
                          "as_expected": reading["ok"], "readings": reading})
            print("CALIBRATION", json.dumps({"test": name, "variant": table[-1]["variant"],
                                             "as_expected": reading["ok"], "reading": reading}, default=str),
                  flush=True)
    Path(out).write_text(json.dumps(table, indent=1, default=str))
    return table


def main(argv):
    out = None
    if "--calibrate" in argv:
        index = argv.index("--calibrate")
        out = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]
    import bpy
    table = calibrate(bpy, argv or sorted(VARIANTS), out or str(HERE / "face-calibration.json"))
    done = [row["as_expected"] for row in table]
    print("VARIANTS %d of %d as expected" % (sum(done), len(done)), flush=True)
    return 0 if all(done) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
