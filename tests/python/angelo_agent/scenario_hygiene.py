# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scene hygiene, a state digest and the end-of-session Undo probe of a scenario session (inside Blender).

The same functions run in Angelo's trial driver and, over the MCP add-on's
socket, in stock Blender:

* ``state_digest``: a SHA-256 of the scene's authored state (objects, data
  sizes, modifiers, materials, lights, cameras, world, frame range, render
  size). The drivers record it before the first turn and after every turn.
* ``report``: leftovers a person would clean up: objects the prompts never
  asked for, default names, duplicate ``.001`` materials, empty material
  slots, negative scales and a non-Object mode (counted in ``issues``), plus
  orphan data-blocks the session created (reported apart from ``issues``:
  Blender drops them on save, and deleting an object leaves its data behind
  however it was deleted). ``orphans`` taken before the first turn is the
  baseline, so the factory file's own orphans never count.
* ``save_copy``: the end-of-session copy for scoring, without a thumbnail.
* ``undo_probe``: run once at the very end, after the scene was saved for
  scoring. It presses Undo until it reaches the starting state (or cannot
  undo any more), digesting the scene after every step, and reports which
  turn boundaries a person could return to, how many steps each turn took and
  whether one Undo goes back exactly one turn. It changes the scene, so
  nothing may be graded after it.
"""

import hashlib
import json
import re

DEFAULT_NAME = re.compile(r"^(Cube|Sphere|UV Sphere|Ico ?Sphere|Cylinder|Plane|Cone|Torus|Circle|Empty|Camera|Light|"
                          r"Point|Spot|Area|Suzanne|Monkey|Text|BezierCurve|BezierCircle|NurbsPath|Armature|GPencil|"
                          r"Stroke|Grid|Mesh|Object)(\.\d{3,})?$")
ORPHAN_COLLECTIONS = ("meshes", "materials", "node_groups", "images", "textures", "actions", "curves", "cameras",
                      "lights", "worlds", "collections", "armatures")
UNDO_LIMIT = 400


def _round(values, digits=5):
    return [round(float(v), digits) for v in values]


def _object_state(obj):
    data = obj.data
    entry = {"name": obj.name, "type": obj.type, "parent": obj.parent.name if obj.parent else None,
             "matrix": [_round(row) for row in obj.matrix_world], "hide": [obj.hide_viewport, obj.hide_render],
             "modifiers": [[m.name, m.type, m.show_viewport] for m in getattr(obj, "modifiers", [])],
             "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
             "collections": sorted(c.name for c in obj.users_collection),
             "animated": bool(obj.animation_data and obj.animation_data.action)}
    if obj.type == "MESH":
        entry["data"] = [data.name, len(data.vertices), len(data.edges), len(data.polygons)]
    elif obj.type == "LIGHT":
        entry["data"] = [data.name, data.type, round(data.energy, 4), _round(data.color, 4)]
    elif obj.type == "CAMERA":
        entry["data"] = [data.name, data.type, round(data.lens, 4)]
    elif data is not None:
        entry["data"] = [data.name]
    return entry


def state_digest(bpy, scene):
    """SHA-256 of the scene's authored state (no evaluation, no frame change)."""
    world = scene.world
    state = {"objects": sorted((_object_state(obj) for obj in scene.objects), key=lambda e: e["name"]),
             "world": [world.name, len(world.node_tree.nodes) if world and world.node_tree else 0] if world else None,
             "camera": scene.camera.name if scene.camera else None,
             "frames": [scene.frame_start, scene.frame_end],
             "render": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
                        scene.render.engine, scene.render.fps, scene.render.use_motion_blur],
             "materials": sorted(m.name for m in bpy.data.materials)}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _expected(targets, name):
    import scenario_checks
    return any(scenario_checks.matches(name, target) for target in targets)


def check_targets(turns):
    """Every object target the scenario's checks name (``{"name"}`` or ``{"prefix"}``)."""
    found = []
    for turn in turns:
        for check in turn["checks"]:
            for key in ("target", "skins"):
                target = check.get(key)
                if isinstance(target, dict) and target not in found and ("name" in target or "prefix" in target):
                    found.append(target)
    return found


def orphans(bpy):
    """``{collection: [names]}`` of data-blocks with no users (fake users excluded)."""
    found = {}
    for collection in ORPHAN_COLLECTIONS:
        names = sorted(block.name for block in getattr(bpy.data, collection)
                       if block.users == 0 and not block.use_fake_user)
        if names:
            found[collection] = names
    return found


def saved_orphans(bpy):
    """``{collection: [names]}`` of the data-blocks a reopened save of this file would show as unused.

    A save writes only data-blocks with users or a fake user (zero-user data is
    dropped, as Blender does). One written data-block comes back without users
    when every data-block using it is one the save drops: a material whose only
    user was the mesh of a deleted object. Measured in Blender 5.2: such a
    material is written and reopens with no users; a zero-user one is not
    written at all; a fake-user one is written and kept. Fake-user data is not
    counted here, as ``orphans`` does not count it either.
    """
    dropped = lambda block: block.users == 0 and not block.use_fake_user
    owner = {block: collection for collection in ORPHAN_COLLECTIONS for block in getattr(bpy.data, collection)
             if block.users > 0 and not block.use_fake_user}
    found = {}
    for block, users in (bpy.data.user_map(subset=set(owner)).items() if owner else ()):
        if users and all(dropped(user) for user in users):
            found.setdefault(owner[block], []).append(block.name)
    return {collection: sorted(names) for collection, names in found.items()}


def report(bpy, scene, targets, absent=(), baseline=None):
    """Leftovers a person would clean up; ``issues`` sums the countable problems.

    ``targets``: what the prompts named (``check_targets``); ``absent``: names
    the prompts asked to delete (the default Cube, Camera and Light);
    ``baseline``: ``orphans`` before the first turn, left out of ``new_orphans``.
    """
    unexpected, default_named, negative = [], [], []
    for obj in scene.objects:
        wanted = _expected(targets, obj.name) or (obj.parent is not None and _expected(targets, obj.parent.name))
        if not wanted:
            unexpected.append(obj.name)
            if DEFAULT_NAME.match(obj.name):
                default_named.append(obj.name)
        if any(value < 0 for value in obj.scale):
            negative.append(obj.name)
    before = baseline or {}
    new_orphans = {collection: [name for name in names if name not in before.get(collection, ())][:20]
                   for collection, names in orphans(bpy).items()}
    new_orphans = {collection: names for collection, names in new_orphans.items() if names}
    duplicates = [m.name for m in bpy.data.materials if re.search(r"\.\d{3,}$", m.name)]
    empty_slots = sorted({obj.name for obj in scene.objects for slot in obj.material_slots if slot.material is None})
    meshes_without_material = sorted(obj.name for obj in scene.objects if obj.type == "MESH" and
                                     not any(slot.material for slot in obj.material_slots))
    leftovers = [name for name in absent if scene.objects.get(name) is not None]
    mode = bpy.context.mode
    issues = (len(unexpected) + len(duplicates) + len(empty_slots) + len(negative) + len(leftovers)
              + (mode != "OBJECT"))
    return {"objects": len(scene.objects), "unexpected_objects": sorted(unexpected)[:40],
            "default_named": sorted(default_named), "leftover_defaults": leftovers, "new_orphans": new_orphans,
            "new_orphan_count": sum(len(names) for names in new_orphans.values()),
            "duplicate_materials": duplicates[:20], "empty_material_slots": empty_slots[:20],
            "meshes_without_material": meshes_without_material[:20], "negative_scale": negative, "mode": mode,
            "collections": len(bpy.data.collections), "objects_in_scene_root": len(scene.collection.objects),
            "issues": issues}


def save_copy(bpy, window, path):
    """Save a copy of the open file for scoring, with no thumbnail; preferences are left as they were.

    An automatic thumbnail draws the scene offscreen; from a timer that trips
    Metal's one-context-per-framebuffer assertion in development builds (the
    add-on's request snapshots avoid it the same way).
    """
    preferences = bpy.context.preferences
    filepaths = preferences.filepaths
    preview_type, dirty = filepaths.file_preview_type, preferences.is_dirty
    try:
        filepaths.file_preview_type = "NONE"
        with bpy.context.temp_override(window=window):
            result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, check_existing=False)
    finally:
        filepaths.file_preview_type = preview_type
        if not preferences.bl_rna.properties["is_dirty"].is_readonly:
            preferences.is_dirty = dirty
    if "FINISHED" not in result:
        raise RuntimeError("Saving the scoring copy failed: %s" % sorted(result))
    return str(path)


def finish(bpy, window, turns, digests, path, baseline=None, probe=True):
    """End of session: hygiene, the saved copy, then the Undo probe (it changes the scene; nothing may follow).

    ``probe`` False leaves the Undo probe to ``undo_probe`` later: a diagnostic run's ask-why follow-up
    (``eval_ask_why``) comes between, so the agent still has the graded scene in front of it. Returns what was
    measured; a failure keeps the earlier parts and adds ``error``.
    """
    import traceback
    output = {}
    try:
        scene = window.scene
        output["hygiene"] = report(bpy, scene, check_targets(turns), ("Cube", "Camera", "Light"), baseline)
        output["final_digest"] = state_digest(bpy, scene)
        output["saved"] = save_copy(bpy, window, path)
        if probe:
            output["undo"] = undo_probe(bpy, window, digests)
    except Exception:
        output["error"] = traceback.format_exc()[-3000:]
    return output


def late_undo_probe(bpy, window, digests):
    """The Undo probe after a diagnostic follow-up: ``{"undo": ..., "undo_after_ask_why": True}`` or ``error``."""
    import traceback
    try:
        return {"undo": undo_probe(bpy, window, digests), "undo_after_ask_why": True}
    except Exception:
        return {"undo_error": traceback.format_exc()[-3000:], "undo_after_ask_why": True}


def undo_probe(bpy, window, digests, limit=UNDO_LIMIT):
    """Press Undo back towards the first digest; report the turn boundaries it passes.

    ``digests``: the state before turn 1, then after each turn. ``steps`` lists,
    per Undo press, the index of the turn boundary it landed on (None when the
    state matches no boundary).
    """
    positions = {}
    for index, value in enumerate(digests):
        positions.setdefault(value, index)   # A no-op turn repeats its predecessor's digest.
    landed, stopped = [], "limit"
    with bpy.context.temp_override(window=window):
        for _ in range(limit):
            if not bpy.ops.ed.undo.poll():
                stopped = "nothing left to undo"
                break
            bpy.ops.ed.undo()
            current = state_digest(bpy, window.scene)
            landed.append(positions.get(current))
            if current == digests[0]:
                stopped = "reached the start"
                break
    turns = len(digests) - 1
    reached = [index for index in landed if index is not None]
    boundaries = {k: k in reached for k in range(turns)}
    first = landed[0] if landed else None
    return {"undo_steps": len(landed), "stopped": stopped, "landed": landed[:120],
            "reaches_start": 0 in reached,
            "first_undo_returns_previous_turn": turns > 0 and first == turns - 1,
            "first_undo_lands_on": first,
            "turn_boundaries_reachable": sum(boundaries.values()), "turns": turns,
            "steps_per_turn": _steps_per_turn(landed, turns)}


def _steps_per_turn(landed, turns):
    """Undo presses between consecutive boundaries, from the last turn back (None where never reached)."""
    output, previous_position, previous_boundary = {}, 0, turns
    for position, boundary in enumerate(landed, 1):
        if boundary is not None and boundary < previous_boundary:
            # Every turn between the boundary reached and the previous one shares these presses.
            output["%d-%d" % (boundary + 1, previous_boundary)] = position - previous_position
            previous_position, previous_boundary = position, boundary
    return output
