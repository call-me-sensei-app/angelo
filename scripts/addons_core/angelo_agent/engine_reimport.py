# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read an export back and compare it with the scene it came from. Main thread.

``snapshot(objects, scene)`` describes the source before or after export; ``compare(path, fmt, source, scene,
view_layer)`` imports the written .glb or .fbx with Blender's own importer into a temporary collection of the
active scene (never a temporary scene, whose context the importers don't follow), describes what arrived the same
way, removes every data block the import made and restores the selection, active object, active collection and
frame, then returns findings: hierarchy, bounds, units and facing, bones, skin weights (small influences dropped),
materials and colour spaces, and local paths written into the file.

Blender's importer is a stand-in for the engine's: it reads what the file carries, so a finding here is a fact about
the file, not about the engine's renderer. The import pushes no Undo step (operators called from Python don't), and
nothing it made outlives the comparison. doc/angelo/engine_preflight.rst ("Re-import comparison") is the contract.
"""

import os
import re
import time

import bpy

COLLECTION = "Angelo Re-import Check"
TOLERANCE = 0.01          # Bounds agree within 1 % of the largest side (and 1 mm).
# An animated object's place is not compared, only its size: where it sits depends on the frame, on how each format
# samples its curves (FBX simplifies them) and on which action the importer makes active (Blender's glTF importer
# activates the file's first animation only).
WEIGHT_EPSILON = 0.01     # A dropped influence below this changes nothing visible; above it, it is reported.
SKIN_SAMPLE = 20_000      # Source vertices counted per skinned mesh (evenly spaced)...
ARRIVED_SAMPLE = 0        # ...and every vertex of what arrived (0: all), for the most influences it keeps.
DATA_COLLECTIONS = ("objects", "meshes", "materials", "images", "armatures", "actions", "cameras", "lights",
                    "node_groups", "collections", "curves", "textures")   # Shape keys go with their mesh.
_SUFFIX = re.compile(r"\.\d+$")   # Blender numbers taken names with any count of digits (".001", ".1000").


def _metres(scene):
    return scene.unit_settings.scale_length or 1.0


def _bounds(obj, depsgraph):
    """World-space axis-aligned box of ``obj``'s evaluated geometry, or None for objects without geometry."""
    if obj.type not in {"MESH", "CURVE", "FONT", "SURFACE", "META"}:
        return None
    evaluated = obj.evaluated_get(depsgraph)
    matrix = evaluated.matrix_world
    corners = [matrix @ _vector(corner) for corner in evaluated.bound_box]
    if not corners:
        return None
    low = [min(corner[axis] for corner in corners) for axis in range(3)]
    high = [max(corner[axis] for corner in corners) for axis in range(3)]
    return low, high


def _vector(values):
    from mathutils import Vector
    return Vector(values)


def _moves(obj):
    """Whether ``obj`` or an object above it has animation (its place then depends on the frame and the sampling)."""
    while obj is not None:
        data = obj.animation_data
        if data is not None and (data.action is not None or len(data.nla_tracks)):
            return True
        obj = obj.parent
    return False


def _weights(obj, sample=SKIN_SAMPLE):
    """How many bone influences the vertices of a skinned mesh carry: {"most": the most on one vertex, "over": {k:
    vertices with more than k}, "dropped": {k: the largest normalized weight past the k largest}} over evenly spaced
    vertices, or None. The exporters keep each vertex's largest influences, so this says exactly what a limit of k
    drops, without pairing vertices (compression moves them)."""
    from .engine_facts import deform_armature
    armature = deform_armature(obj)   # The one the check and the fix read.
    if obj.type != "MESH" or armature is None:
        return None
    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    groups = {group.index for group in obj.vertex_groups if group.name in deform}
    vertices = obj.data.vertices
    step = max(1, len(vertices) // sample) if sample else 1
    most, over, dropped = 0, {}, {}
    for index in range(0, len(vertices), step):
        weights = sorted((item.weight for item in vertices[index].groups if item.group in groups
                          and item.weight > 0.0), reverse=True)
        total = sum(weights) or 1.0
        most = max(most, len(weights))
        for limit in range(1, len(weights)):   # Keys are strings: a description travels as JSON (web_readback).
            key = str(limit)
            over[key] = over.get(key, 0) + step
            dropped[key] = max(dropped.get(key, 0.0), weights[limit] / total)
    return {"most": most, "over": over, "dropped": dropped, "sampled": step > 1}


def _images(material):
    if material is None or material.node_tree is None:
        return []
    return [{"name": os.path.splitext(node.image.name)[0], "colorspace": node.image.colorspace_settings.name}
            for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeTexImage" and node.image]


def snapshot(objects, scene, depsgraph, sample=SKIN_SAMPLE):
    """{name: description} of ``objects`` as an export would see them; lengths in metres; ``sample`` skinned vertices
    per mesh."""
    metres = _metres(scene)
    out = {}
    for obj in objects:
        box = _bounds(obj, depsgraph)
        out[obj.name] = {
            "type": obj.type, "parent": obj.parent.name if obj.parent else None,
            "bounds": [[value * metres for value in side] for side in box] if box else None,
            "bones": sorted(bone.name for bone in obj.data.bones) if obj.type == "ARMATURE" else None,
            "materials": [slot.material.name if slot.material else "" for slot in obj.material_slots],
            "images": [image for slot in obj.material_slots for image in _images(slot.material)],
            "weights": _weights(obj, sample), "metres": metres, "animated": _moves(obj)}
    return out


def with_baked(source, baked):
    """``source`` (``snapshot``) with each ``[object, material]`` of ``baked`` the object's one material: an export
    writes every slot with it, and an importer merges identical slots."""
    out = dict(source)
    for item in baked:   # [object, material] or engine_link_assets.baked_for_save's [..., libraries].
        name, material = item[0], item[1]
        if name in out:
            out[name] = dict(out[name], materials=[material])
    return out


def _stem(name):
    """A name without Blender's collision number: an imported "Cube" arrives as "Cube.001", "Cube.001" as "Cube.007"."""
    return _SUFFIX.sub("", name)


def _centre(description):
    box = description["bounds"]
    return [(low + high) / 2.0 for low, high in zip(*box)] if box else None


def _kind(description):
    return "MESH" if description["type"] in {"CURVE", "FONT", "SURFACE", "META"} else description["type"]


def match(source, imported):
    """{imported name: source name} pairing what arrived with what was exported. The source objects still exist while
    the file is read, so Blender renames every imported object; within each name stem, objects of the same kind pair
    first, then the rest (an object can arrive as another kind: a mesh without faces as an empty), each with the
    nearest by its box's centre (in name order where there is none)."""
    groups = {}
    for side, descriptions in (("source", source), ("imported", imported)):
        for name, description in descriptions.items():
            groups.setdefault(_stem(name), {"source": [], "imported": []})[side].append(name)
    pairs = {}
    for group in groups.values():
        wanted, arrived = sorted(group["source"]), sorted(group["imported"])
        for same_kind in (True, False):
            for name in [name for name in arrived if name not in pairs]:
                here = _centre(imported[name])
                candidates = [candidate for candidate in wanted if not same_kind
                              or _kind(source[candidate]) == _kind(imported[name])]
                if not candidates:
                    continue

                def distance(candidate):
                    there = _centre(source[candidate])
                    return 0.0 if here is None or there is None else sum((a - b) ** 2 for a, b in zip(here, there))
                best = min(candidates, key=distance)
                wanted.remove(best)
                pairs[name] = best
    return pairs


def _new_ids(before):
    return {collection: [block for block in getattr(bpy.data, collection)
                         if block.as_pointer() not in before[collection]] for collection in DATA_COLLECTIONS}


def _remove(created):
    """Remove what the import made, objects first so no data block keeps a user."""
    for collection in DATA_COLLECTIONS:
        store = getattr(bpy.data, collection)
        for block in created[collection]:
            try:
                store.remove(block, do_unlink=True)
            except (ReferenceError, RuntimeError, TypeError, AttributeError):
                pass   # Already freed with its owner.


def _import(path, fmt):
    if fmt == "FBX":
        import addon_utils
        if not hasattr(bpy.types, "IMPORT_SCENE_OT_fbx"):
            addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
        return bpy.ops.import_scene.fbx(filepath=path)
    from .gltf_checks import check_file
    from .gltf_morph import import_options
    from .web_export import _addon
    _addon()
    check_file(path, self_contained=True)   # An engine's asset can change after Angelo wrote it: checked like any.
    # No bone display shapes: the importer would add them with an operator, into a mesh being edited.
    return bpy.ops.import_scene.gltf(filepath=path, disable_bone_shape=True, **import_options(path))


class ReadBackSkipped(RuntimeError):
    """The person is in a mode the importers can't run beside (Sculpt, Paint, Pose...): nothing was read."""


def _leave_edit_mode(context):
    """Edit Mode keeps its changes outside the mesh and the importers switch modes: leave it, as the exporter did
    (``web_export.run_exporter``), and return what to enter again. Any other mode refuses (ReadBackSkipped)."""
    if context.mode == "OBJECT":
        return None
    editing = [obj for obj in context.objects_in_mode or () if obj.mode == "EDIT"]
    if not editing or not context.mode.startswith("EDIT"):
        raise ReadBackSkipped(context.mode)
    bpy.ops.object.mode_set(mode="OBJECT")
    return editing


def _memberships():
    """{collection pointer: object pointers} of every collection and scene now, to undo links the import adds."""
    found = {collection.as_pointer(): (collection, {obj.as_pointer() for obj in collection.objects})
             for collection in bpy.data.collections}
    for scene in bpy.data.scenes:
        found[scene.collection.as_pointer()] = (scene.collection, {obj.as_pointer() for obj in
                                                                   scene.collection.objects})
    return found


def _unlink_added(memberships, created):
    """Unlink objects that existed before from the collections the import linked them into."""
    new = {obj.as_pointer() for obj in created["objects"]}
    for collection, before in memberships.values():
        try:
            added = [obj for obj in collection.objects if obj.as_pointer() not in before
                     and obj.as_pointer() not in new]
        except ReferenceError:
            continue
        for obj in added:
            collection.objects.unlink(obj)


def read_back(path, fmt, window=None):
    """Import ``path`` into a temporary collection of the window's (or context's) scene and describe it; nothing
    the import made remains. Returns ({source-like name: description}, extra names without a source match)."""
    override = {} if window is None else {"window": window}
    with bpy.context.temp_override(**override):
        context = bpy.context
        scene, view_layer = context.scene, context.view_layer
        before = {collection: {block.as_pointer() for block in getattr(bpy.data, collection)}
                  for collection in DATA_COLLECTIONS}
        kept = {"active": view_layer.objects.active, "selected": list(context.selected_objects),
                "collection": view_layer.active_layer_collection,
                "frame": (scene.frame_current, scene.frame_start, scene.frame_end, scene.render.fps,
                          scene.render.fps_base)}
        editing = _leave_edit_mode(context)
        memberships = _memberships()
        holder = bpy.data.collections.new(COLLECTION)
        scene.collection.children.link(holder)
        try:
            view_layer.active_layer_collection = view_layer.layer_collection.children[holder.name]
            for obj in kept["selected"]:
                obj.select_set(False)
            result = _import(path, fmt)   # The glTF importer reads context.object: no override hides it.
            if "FINISHED" not in result:
                raise RuntimeError("Blender's importer couldn't read the file back")
            # The glTF importer's bone display shapes live in its own collection: not the file's objects.
            helpers = bpy.data.collections.get("glTF_not_exported")
            imported = [obj for obj in bpy.data.objects if obj.as_pointer() not in before["objects"]
                        and not (helpers is not None and obj.name in helpers.objects)]
            current = kept["frame"][0]
            scene.frame_set(current)   # The source was described at this frame; an import may move it.
            depsgraph = context.evaluated_depsgraph_get()
            return snapshot(imported, scene, depsgraph, ARRIVED_SAMPLE)
        finally:
            created = _new_ids(before)
            _unlink_added(memberships, created)
            _remove(created)
            view_layer.update()   # The view layer's object list is stale until it syncs with the removals.
            current, start, end, fps, fps_base = kept["frame"]
            scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base = start, end, fps, fps_base
            if scene.frame_current != current:
                scene.frame_set(current)
            try:
                view_layer.active_layer_collection = kept["collection"]
            except (ReferenceError, TypeError):
                pass
            for obj in kept["selected"]:
                if obj.name in view_layer.objects:
                    obj.select_set(True)
            view_layer.objects.active = kept["active"]
            if editing and kept["active"] is not None and kept["active"].mode != "EDIT":
                for obj in editing:
                    obj.select_set(True)
                bpy.ops.object.mode_set(mode="EDIT")


def _finding(code, severity, subject, message, looked_for, found):
    return {"code": code, "severity": severity, "subject": subject, "message": message,
            "facts": {"looked_for": looked_for, "found": found}}


def _size(box):
    return [high - low for low, high in zip(*box)]


def _bounds_finding(name, src, got):
    want, have = _size(src["bounds"]), _size(got["bounds"])
    largest = max(max(want), 1e-6)
    if all(abs(a - b) <= max(TOLERANCE * largest, 0.001) for a, b in zip(want, have)) and (src.get("animated") or all(
            abs(a - b) <= max(TOLERANCE * largest, 0.001) for a, b in zip(src["bounds"][0], got["bounds"][0]))):
        return None
    shown = "%.3g × %.3g × %.3g m at (%.3g, %.3g, %.3g)" % (*have, *got["bounds"][0])
    expected = "%.3g × %.3g × %.3g m at (%.3g, %.3g, %.3g)" % (*want, *src["bounds"][0])
    if max(have) > 1e-6:
        ratios = [b / a for a, b in zip(sorted(want), sorted(have)) if a > 1e-6]
        if ratios and max(ratios) / min(ratios) < 1.01 and abs(ratios[0] - 1) > TOLERANCE:
            return _finding("reimport_units", "warn", name, "%s reads back %.4g times its size" % (name, ratios[0]),
                            expected, shown)
        if sorted(round(value, 3) for value in want) == sorted(round(value, 3) for value in have) and any(
                abs(a - b) > max(TOLERANCE * largest, 0.001) for a, b in zip(want, have)):
            return _finding("reimport_facing", "warn", name, "%s reads back turned: its sides swapped axes" % name,
                            expected, shown)
    return _finding("reimport_bounds", "warn", name, "%s reads back at a different size or place" % name,
                    expected, shown)


def _weights_finding(name, src, got):
    want, have = src["weights"], got["weights"]
    if not want or not have or have["most"] >= want["most"]:
        return None
    limit = have["most"]
    largest = want["dropped"].get(str(limit), 0.0)
    if largest < WEIGHT_EPSILON:
        return None
    count = want["over"].get(str(limit), 0)
    return _finding("reimport_weights", "warn" if largest >= 0.05 else "info", name,
                    "%s: the file keeps %d bone influences per vertex, the scene has up to %d; %d vertices lose "
                    "weights up to %.3f" % (name, limit, want["most"], count, largest),
                    "up to %d influences per vertex" % want["most"],
                    "%d in the file, %d vertices lose weights up to %.3f" % (limit, count, largest))


def _material_findings(name, src, got):
    out = []
    # Distinct names as they stand: a file keeps each material once and an importer merges slots that share one, while
    # "Metal" and "Metal.001" are two materials on both sides.
    want = list(dict.fromkeys(material for material in src["materials"] if material))
    have = list(dict.fromkeys(material for material in got["materials"] if material))
    if len(want) != len(have):
        out.append(_finding("reimport_materials", "warn", name, "%s reads back with %d materials, not %d" % (
            name, len(have), len(want)), ", ".join(want) or "none",
            ", ".join(_SUFFIX.sub("", material) for material in have) or "none"))
    spaces = {image["name"]: image["colorspace"] for image in got["images"]}
    for image in src["images"]:
        arrived = spaces.get(image["name"])
        if arrived and arrived != image["colorspace"] and {arrived, image["colorspace"]} <= {"sRGB", "Non-Color"}:
            out.append(_finding("reimport_colour_space", "warn", image["name"],
                                "Image %s reads back as %s; Blender shows it as %s" % (
                                    image["name"], arrived, image["colorspace"]), image["colorspace"], arrived))
    return out


def _union(descriptions):
    boxes = [item["bounds"] for item in descriptions if item["bounds"]]
    if not boxes:
        return None
    return ([min(box[0][axis] for box in boxes) for axis in range(3)],
            [max(box[1][axis] for box in boxes) for axis in range(3)])


def compare(source, imported, merged=False):
    """Findings for what the file carries against the scene (``snapshot`` of each). ``merged``: the export joined
    static objects (the web export's draw-call merge), so objects are compared as one whole by their bounds, and
    only the ones that arrived by name (skinned and animated ones are never merged) one by one."""
    out = []
    pairs = match(source, imported)
    matched = {origin: imported[name] for name, origin in pairs.items()}
    extra = [name for name in imported if name not in pairs]
    missing = [name for name in source if name not in matched and source[name]["type"] in {"MESH", "ARMATURE",
                                                                                         "CURVE", "FONT", "EMPTY"}]
    if merged:
        want, have = _union(source.values()), _union(imported.values())
        if want and have:
            whole = _bounds_finding("the scene", {"bounds": want}, {"bounds": have})
            if whole:
                out.append(whole)
        missing = extra = []   # Joined objects arrive under the merge's names.
    if missing:
        out.append(_finding("reimport_missing", "warn", missing[0], "%d objects didn't arrive in the file: %s" % (
            len(missing), ", ".join(sorted(missing)[:6])), "every exported object", "%d missing" % len(missing)))
    if extra:
        out.append(_finding("reimport_extra", "info", extra[0], "The file has %d objects the scene doesn't: %s" % (
            len(extra), ", ".join(sorted(extra)[:6])), "only the scene's objects", "%d extra" % len(extra)))
    changed = sorted(name for name in matched if _kind(source[name]) != _kind(matched[name]))
    if changed:
        kinds = sorted({"%s as %s" % (source[name]["type"].lower(), matched[name]["type"].lower()) for name in changed})
        out.append(_finding("reimport_kind", "info", changed[0], "%d objects arrive as another kind (%s): %s" % (
            len(changed), ", ".join(kinds), ", ".join(changed[:6])), "the same kind", "%d changed" % len(changed)))
    for name in sorted(matched):
        src, got = source[name], matched[name]
        parent = pairs.get(got["parent"]) if got["parent"] else None
        if parent != src["parent"]:
            out.append(_finding("reimport_parent", "warn", name, "%s reads back under %s, not %s" % (
                name, got["parent"] or "the scene", src["parent"] or "the scene"), src["parent"] or "none",
                got["parent"] or "none"))
        if src["bounds"] and got["bounds"]:
            finding = _bounds_finding(name, src, got)
            if finding:
                out.append(finding)
        if src["bones"] is not None and got["bones"] is not None and set(src["bones"]) != set(got["bones"]):
            added = sorted(set(got["bones"]) - set(src["bones"]))
            lost = sorted(set(src["bones"]) - set(got["bones"]))
            out.append(_finding("reimport_bones", "info" if not lost else "warn", name,
                                "%s: %d bones added (%s), %d lost (%s)" % (
                                    name, len(added), ", ".join(added[:4]), len(lost), ", ".join(lost[:4])),
                                "%d bones" % len(src["bones"]), "%d bones" % len(got["bones"])))
        weights = _weights_finding(name, src, got)
        if weights:
            out.append(weights)
        out.extend(_material_findings(name, src, got))
    return out


def local_paths(path, needles):
    """Which of ``needles`` (labels -> local path strings) the written file contains, by label."""
    with open(path, "rb") as handle:
        data = handle.read()
    found = []
    for label, needle in needles.items():
        if needle and len(needle) > 3 and (needle.encode("utf-8") in data or needle.encode("utf-16-le") in data):
            found.append(label)
    return sorted(found)


def check(path, fmt, source, window=None, merged=False, needles=None):
    """The comparison's report: findings and its time. Never raises for a file that won't read back: says so.
    ``needles``: {label: local path} to look for in the file (default: the home folder and the .blend's folder)."""
    started = time.perf_counter()
    try:
        findings = compare(source, read_back(path, fmt, window), merged)
    except ReadBackSkipped as mode:
        findings = [_finding("reimport_skipped", "info", "", "The file wasn't read back: the read-back runs in Object "
                             "or Edit Mode, and Blender is in %s" % mode, "Object or Edit Mode", str(mode))]
    except Exception as error:   # noqa: BLE001 - a comparison that can't run is a finding, never a failed export.
        name = type(error).__name__
        findings = [_finding("reimport_failed", "warn", "", "The file couldn't be read back (%s)" % name,
                             "a file Blender's importer reads", name)]
    if needles is None:
        home = os.path.expanduser("~")
        needles = {"home folder": home if home not in {"", "/", "~"} else ""}
        if bpy.data.filepath:
            needles["the .blend's folder"] = os.path.dirname(bpy.data.filepath)
    paths = local_paths(path, needles)
    if paths:
        findings.append(_finding("path_in_file", "warn", "", "The written file contains paths on this computer (%s)" %
                                 ", ".join(paths), "no local paths", ", ".join(paths)))
    return {"findings": findings, "ms": round((time.perf_counter() - started) * 1000.0, 1)}
