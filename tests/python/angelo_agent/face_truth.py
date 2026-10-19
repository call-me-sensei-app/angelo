# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The readings of a face or hair test: of its ground truth (from the design) and of a side's scene.

A test declares ``FACE`` beside its cameras::

    FACE = {"design": "portrait",                     # head_designs: the ground truth's head
            "head": [{"prefix": "Head"}],             # what the head is (its eyes included)
            "eyes": {"L": {"name": "Head Eye L"}, "R": {"name": "Head Eye R"}},
            "hair": {"style": "short", "target": {"prefix": "Hair"}}}   # or None

``truth(name)`` reads the ground truth from its design alone (``FieldCaster``:
the head's signed distance field and the eyes' ellipsoids; ``hair_kit``'s
strands), memoised per process: no stored picture or mesh is trusted, and the
same NumPy reads it on every host. ``read(bpy, scene, depsgraph, name)`` reads
a side's scene with ``SceneCaster``. Both go through ``face_measure`` with the
same windows and rows (``placements``: from the design's expected landmark
heights, never from either reading), so only the caster differs;
``face_variants`` measures what that difference costs on the ground truth's
own mesh.

``read`` also measures the relief both ways: every visible front-view point of
the side's head against the truth's surface (its distance field), and every
visible point of the truth's head against the side's surfaces (a BVH of the
side's evaluated head meshes). The profile landmarks a test grades are those
its truth has well defined (``face_compare.defined_landmarks``); the relief
regions are placed from the truth's readings (``regions``).
"""

import hashlib
import json
import math
import os
from pathlib import Path

import numpy

import face_casters as casters
import face_compare
import face_measure as measure
import hair_kit
import head_designs

HAIRLINE_COLUMNS = tuple(round(value, 4) for value in numpy.arange(-0.045, 0.0451, 0.003))
HAIR_PITCH = {"front": 0.001, "side": 0.002, "back": 0.002, "top": 0.002}
HAIR_VIEWS = ("front", "side", "back", "top")
# What a truth reading depends on besides the test's own sources (``scenario_specs.sources_digest``): the code
# that reads it. A change to any of them is a new cache entry, never a stale truth.
READER_SOURCES = ("face_casters.py", "face_measure.py", "face_truth.py", "face_compare.py")
_TRUTHS = {}


def frame(camera):
    """The reference picture's extent in its view's coordinates (1280 x 720, the ortho scale across)."""
    half = camera["ortho_scale"] / 2.0
    return (-half, half, -half * 720.0 / 1280.0, half * 720.0 / 1280.0)


def _module(name):
    import scenario_specs
    return scenario_specs.module(name)


def _box_bounds(view, boxes):
    corners = numpy.asarray([(x, y, z) for low, high in boxes for x in (low[0], high[0]) for y in (low[1], high[1])
                             for z in (low[2], high[2])])
    u, v, _ = view.project(corners)
    return float(u.min()), float(u.max()), float(v.min()), float(v.max())


def fields(design):
    """``{name: (distance function, box)}`` of the ground truth's objects: the head and its two eyes."""
    found = {"Head": (casters.design_field(design), (design["low"], design["high"]))}
    for spec in design["eyes"]:
        scale = spec.get("scale", (1.0, 1.0, 1.0))
        reach = spec["radius"] * max(scale) + 0.001
        for key, sign in (("L", 1.0), ("R", -1.0)):
            x, y, z = spec["centre"]
            centre = (sign * x, y, z)
            found["Head Eye " + key] = (casters.eye_field(centre, spec["radius"], scale),
                                        (tuple(c - reach for c in centre), tuple(c + reach for c in centre)))
    return found


def placements(module):
    """Where both readings look, from the design's expected landmark heights (view coordinates): the profile
    landmarks' anchors, the row the mouth is read along and the row the jaw line is read down from."""
    target_z = float(module.CAMERAS["front"]["target"][2])
    seeds = {key: z - target_z for key, z in head_designs.SEEDS[module.FACE["design"]].items()}
    mouth = seeds.get("stomion", min(seeds.values()) + 0.03)
    return seeds, {"mouth": mouth, "jaw": seeds.get("sublabiale", mouth - 0.008)}


def regions(reading, defined, target_z):
    """Relief regions ``{name: [u0, u1, v0, v1]}`` of the front view, placed from the truth's own readings."""
    marks = {name: reading["landmarks"][name]["v"] for name in defined}
    eyes = reading["eyes"]
    eye_u = abs(eyes["L"][0])
    eye_v = (eyes["L"][2] + eyes["R"][2]) * 0.5 - target_z
    nose_top = marks.get("nasion", eye_v + 0.006)
    nose_bottom = marks["subnasale"] - 0.002 if "subnasale" in marks else marks.get("pronasale", eye_v - 0.04) - 0.008
    corner = max(abs(reading["mouth"]["left"][0]), abs(reading["mouth"]["right"][0])) + 0.004 \
        if reading.get("mouth") else 0.026
    mouth_bottom = marks["sublabiale"] if "sublabiale" in marks else reading["rows"]["mouth"] - 0.010
    chin_bottom = (reading["menton"] + 0.001) if reading.get("menton") is not None else mouth_bottom - 0.02
    found = {
        "eye L": [eye_u - 0.016, eye_u + 0.016, eye_v - 0.011, eye_v + 0.011],
        "eye R": [-eye_u - 0.016, -eye_u + 0.016, eye_v - 0.011, eye_v + 0.011],
        "nose": [-0.014, 0.014, nose_bottom, nose_top],
        "mouth": [-corner, corner, mouth_bottom, nose_bottom],
        "chin": [-0.025, 0.025, chin_bottom, mouth_bottom],
        "cheek L": [0.022, 0.052, nose_bottom, eye_v - 0.013],
        "cheek R": [-0.052, -0.022, nose_bottom, eye_v - 0.013],
        "forehead": [-0.045, 0.045, eye_v + 0.016, eye_v + 0.055],
    }
    return {name: [round(value, 5) for value in box] for name, box in found.items()}


def cache_path(name):
    """Where a test's truth readings are kept between processes: beside the scenario references (whose root the
    launchers hand every trial process), keyed by the digest of everything that decides them."""
    import scenario_specs
    here = Path(__file__).resolve().parent
    sha = hashlib.sha256(scenario_specs.sources_digest(name).encode())
    for file_name in READER_SOURCES:
        sha.update((here / file_name).read_bytes().replace(b"\r\n", b"\n"))
    return scenario_specs.reference_root() / "face-truth" / name / (sha.hexdigest()[:16] + ".npz")


def _flatten(value, arrays, key):
    if isinstance(value, numpy.ndarray) and value.dtype != object:
        arrays[key] = value
        return {"__array__": key}
    if isinstance(value, dict):
        return {str(k): _flatten(v, arrays, "%s/%s" % (key, k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_flatten(v, arrays, "%s/%d" % (key, i)) for i, v in enumerate(value)]
    if isinstance(value, (numpy.floating, numpy.integer, numpy.bool_)):
        return value.item()
    return value


def _restore(value, archive):
    if isinstance(value, dict):
        if set(value) == {"__array__"}:
            return archive[value["__array__"]]
        return {k: _restore(v, archive) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v, archive) for v in value]
    return value


def _save(path, found):
    arrays = {}
    tree = _flatten({key: value for key, value in found.items() if key != "fields"}, arrays, "t")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("%s.%d.tmp.npz" % (path.stem, os.getpid()))
    numpy.savez_compressed(temporary, __tree__=numpy.frombuffer(json.dumps(tree).encode(), dtype=numpy.uint8),
                           **arrays)
    os.replace(temporary, path)


def _load(path):
    with numpy.load(path, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    return _restore(json.loads(bytes(arrays.pop("__tree__")).decode()), arrays)


def truth(name):
    """The ground truth's readings (memoised, and kept on disk by ``cache_path``):
    ``{"head", "defined", "regions", "fields", "hair"}``."""
    if name in _TRUTHS:
        return _TRUTHS[name]
    module = _module(name)
    path = None
    try:
        path = cache_path(name)
        if path.is_file():
            found = _load(path)
            found["fields"] = fields(head_designs.design(module.FACE["design"]))
            _TRUTHS[name] = found
            return found
    except (OSError, ValueError, KeyError):   # An unreadable entry is read again from the design.
        pass
    found = _read_truth(name)
    if path is not None:
        try:
            _save(path, found)
        except OSError:
            pass
    _TRUTHS[name] = found
    return found


def _read_truth(name):
    module = _module(name)
    face = module.FACE
    design = head_designs.design(face["design"])
    found_fields = fields(design)
    views = {view: casters.View(module.CAMERAS[view]) for view in ("front", "side")}
    boxes = [box for _function, box in found_fields.values()]
    anchors, rows = placements(module)
    centres = head_designs.eye_centres(design)
    reading = measure.measure_head(
        {view: casters.FieldCaster(views[view], found_fields) for view in views}, casters.name_matcher(face["head"]),
        {view: _box_bounds(views[view], boxes) for view in views},
        {view: frame(module.CAMERAS[view]) for view in views}, anchors,
        {"L": list(centres[0]), "R": list(centres[1])}, rows)
    defined = face_compare.defined_landmarks(reading["landmarks"])
    target_z = float(module.CAMERAS["front"]["target"][2])
    result = {"head": reading, "defined": defined, "regions": regions(reading, defined, target_z),
              "fields": found_fields, "hair": None}
    if face.get("hair"):
        strands = list(hair_kit.strands(design, face["hair"]["style"])[0].astype(numpy.float64))
        result["hair"] = hair_reading(module.CAMERAS, lambda view: casters.FieldCaster(
            casters.View(module.CAMERAS[view]), found_fields), strands, lambda found_name: False,
            boxes + [_strand_box(strands)], reading["eyes"], target_z)
    return result


def design_reading(name, design):
    """Another design read exactly as the truth is (its own fields, the test's placements, the truth's jaw
    columns): what a mesh build of that design should read, to compare a mesh reading with."""
    module = _module(name)
    face = module.FACE
    known = truth(name)
    found_fields = fields(design)
    views = {view: casters.View(module.CAMERAS[view]) for view in ("front", "side")}
    boxes = [box for _function, box in found_fields.values()]
    anchors, rows = placements(module)
    centres = head_designs.eye_centres(design)
    reading = measure.measure_head(
        {view: casters.FieldCaster(views[view], found_fields) for view in views}, casters.name_matcher(face["head"]),
        {view: _box_bounds(views[view], boxes) for view in views},
        {view: frame(module.CAMERAS[view]) for view in views}, anchors,
        {"L": list(centres[0]), "R": list(centres[1])}, rows, known["head"]["jaw"]["u"])
    mine, mine_rows, mine_columns = measure.surface_points(views["front"], reading["front"])
    theirs, their_rows, their_columns = measure.surface_points(views["front"], known["head"]["front"])
    relief = {"side_to_truth": {"u": reading["front"]["us"][mine_columns], "v": reading["front"]["vs"][mine_rows],
                                "d": field_distance(known["fields"], mine)},
              "truth_to_side": {"u": known["head"]["front"]["us"][their_columns],
                                "v": known["head"]["front"]["vs"][their_rows],
                                "d": field_distance(found_fields, theirs)}}
    return {"head": reading, "relief": relief}


def errors(name, side):
    """Every face error of a reading (``read`` or ``design_reading``) against the truth, keyed like the checks."""
    module = _module(name)
    known = truth(name)
    pixel = {view: module.CAMERAS[view]["ortho_scale"] / 1280.0 for view in ("front", "side")}
    head = side["head"]
    return {"landmarks": face_compare.landmarks(head.get("landmarks"), known["head"]["landmarks"], known["defined"],
                                                pixel["side"]),
            "eyes": face_compare.eyes(head["eyes"], known["head"]["eyes"], pixel["front"], pixel["side"]),
            "jaw": face_compare.jaw(head, known["head"], pixel["front"]),
            "mouth": face_compare.mouth(head.get("mouth"), known["head"].get("mouth"), pixel["front"]),
            "outline front": face_compare.outline(head.get("front"), known["head"]["front"], pixel["front"]),
            "outline side": face_compare.outline(head.get("side"), known["head"]["side"], pixel["side"]),
            "relief": face_compare.relief(side.get("relief"), known["regions"])}


def _strand_box(strands):
    points = numpy.concatenate(strands)
    return tuple(points.min(axis=0)), tuple(points.max(axis=0))


def hair_reading(cameras, caster_for, strands, is_hair_name, boxes, eyes, target_z):
    """Hair masks of every hair view, the hairline over the forehead and the length at the back and sides."""
    eye_v = (eyes["L"][2] + eyes["R"][2]) * 0.5 - target_z
    views = {}
    for view in HAIR_VIEWS:
        camera = cameras[view]
        lens = casters.View(camera)
        us, vs = measure.grid(_box_bounds(lens, boxes), frame(camera), step=HAIR_PITCH[view])
        depth = measure.strand_depths(lens, strands, us, vs)
        views[view] = measure.hair_view(caster_for(view), us, vs, depth, is_hair_name)
    return {"views": views,
            "hairline": measure.hairline(views["front"], HAIRLINE_COLUMNS, eye_v),
            "length": {"back": measure.lowest_hair(views["back"], 0.0, 0.020),
                       "sides": measure.lowest_hair(views["front"], 0.078, 0.110)}}


def field_distance(found_fields, points):
    """Distance from each point to the nearest of the truth's surfaces (its distance fields)."""
    return numpy.min([numpy.abs(function(points)) for function, _box in found_fields.values()], axis=0)


def scene_distance(depsgraph, match, points, trees=None):
    """Distance from each world point to the nearest surface of the scene's matching evaluated meshes
    (``trees``: the BVHs a reading already built, by match)."""
    from mathutils import Vector
    distances = numpy.full(len(points), numpy.inf)
    if trees is None or match not in trees:
        trees = {match: casters.mesh_trees(depsgraph, match)}
    for _name, tree in trees[match]:
        for index, point in enumerate(points.tolist()):
            found = tree.find_nearest(Vector(point))
            if found[0] is not None and found[3] < distances[index]:
                distances[index] = found[3]
    return distances


def read(bpy, scene, depsgraph, name):
    """A side's readings of the test's head (and hair), in the same windows and rows as the truth's."""
    module = _module(name)
    face = module.FACE
    known = truth(name)
    match = casters.name_matcher(face["head"])
    views = {view: casters.View(module.CAMERAS[view]) for view in ("front", "side")}
    trees = {}
    scene_casters = {view: casters.SceneCaster(scene, depsgraph, views[view], trees) for view in views}
    centres = casters.object_centres(depsgraph, casters.name_matcher(list(face["eyes"].values())))
    eyes = {}
    for key, target in face["eyes"].items():
        mine = [centre for found, centre in centres.items() if casters.name_matcher([target])(found)]
        eyes[key] = list(numpy.mean(mine, axis=0)) if mine else None
    anchors, rows = placements(module)
    reading = measure.measure_head(scene_casters, match,
                                   {view: casters.scene_bounds(depsgraph, views[view], match) for view in views},
                                   {view: frame(module.CAMERAS[view]) for view in views}, anchors, eyes, rows,
                                   known["head"]["jaw"]["u"])
    result = {"head": reading, "hair": None}
    if reading.get("front") is not None:
        mine, mine_rows, mine_columns = measure.surface_points(views["front"], reading["front"])
        theirs, their_rows, their_columns = measure.surface_points(views["front"], known["head"]["front"])
        result["relief"] = {
            "side_to_truth": {"u": reading["front"]["us"][mine_columns], "v": reading["front"]["vs"][mine_rows],
                              "d": field_distance(known["fields"], mine)},
            "truth_to_side": {"u": known["head"]["front"]["us"][their_columns],
                              "v": known["head"]["front"]["vs"][their_rows],
                              "d": scene_distance(depsgraph, match, theirs, trees)}}
    if face.get("hair"):
        hair_match = casters.name_matcher([face["hair"]["target"]])
        either = casters.name_matcher(list(face["head"]) + [face["hair"]["target"]])
        strands, sources = casters.hair_strands(depsgraph, hair_match)
        boxes = _scene_boxes(depsgraph, either) + ([_strand_box(strands)] if strands else [])
        result["hair"] = hair_reading(module.CAMERAS, lambda view: _MatchedCaster(casters.SceneCaster(
            scene, depsgraph, casters.View(module.CAMERAS[view]), trees), either), strands, hair_match, boxes,
            known["head"]["eyes"], float(module.CAMERAS["front"]["target"][2]))
        result["hair"]["sources"] = sources
        result["hair"]["strand_count"] = len(strands)
    result["rays"] = sum(caster.rays for caster in scene_casters.values())
    return result


class _MatchedCaster:
    """A caster that only ever sees the head and the hair (a backdrop or a floor is passed through)."""

    def __init__(self, caster, match):
        self.caster, self.match = caster, match

    def first(self, us, vs, match=None):
        return self.caster.first(us, vs, match or self.match)


def _scene_boxes(depsgraph, match):
    """World boxes of the evaluated head and hair objects (what a hair view must cover)."""
    from mathutils import Vector
    boxes = []
    for instance in depsgraph.object_instances:
        obj = instance.object
        parent = obj.parent.original.name if obj.parent else None
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES"} or \
                not match(obj.original.name, parent):
            continue
        corners = numpy.asarray([tuple(instance.matrix_world @ Vector(corner)) for corner in obj.bound_box])
        boxes.append((tuple(corners.min(axis=0)), tuple(corners.max(axis=0))))
    return boxes
