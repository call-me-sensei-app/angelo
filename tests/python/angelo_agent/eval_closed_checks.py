# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the in-app trial driver (``eval_live_agent_session``) grades a task's closed checks over the scene, and the
scene digest a read-only turn must leave unchanged. Runs in Blender (``bpy`` objects are passed in)."""

import copy

import eval_live_tasks as live
from angelo_agent.scene_validation import VerificationContext, verify


def scene_digest(scene):
    """Names, types and transforms: a read-only turn must leave these unchanged."""
    return sorted((obj.name, obj.type, tuple(round(v, 6) for v in obj.location),
                   tuple(round(v, 6) for v in obj.dimensions)) for obj in scene.objects)


def bind_checks(checks, obj):
    """Concrete checks for the agent's actual objects; ``$bevel`` names its BEVEL modifier.

    ``obj`` is the eval object, or a dict of check alias -> object for a task
    graded on several objects; each ``$bevel`` binds to its own target's bevel.
    """
    targets = obj if isinstance(obj, dict) else {"$eval": obj}
    bound = copy.deepcopy(checks)
    for check in bound:
        if check.get("name") == "$bevel":
            target = targets.get(check.get("target", "$eval"))
            bevel = next((m.name for m in target.modifiers if m.type == "BEVEL"), None) if target else None
            check["name"] = bevel or "missing bevel modifier"
    return bound


def grading_targets(scene, obj):
    """The aliases task checks name: the eval object, the scene and the coverage set's other objects."""
    return {"$eval": obj, "$scene": scene, "$copy": scene.objects.get(live.COPY_OBJECT),
            "$rig": scene.objects.get(live.RIG_OBJECT), "$sketch": scene.objects.get(live.SKETCH_OBJECT),
            "$text": scene.objects.get(live.TEXT_OBJECT), "$strokes": scene.objects.get(live.STROKES_OBJECT),
            "$hair": scene.objects.get(live.HAIR_OBJECT)}


def smooth_shaded(obj):
    """Smooth faces on the base mesh, or a verified face smooth-shading node graph."""
    if obj.type == "MESH" and obj.data.polygons and all(face.use_smooth for face in obj.data.polygons):
        return True
    for modifier in obj.modifiers:
        if modifier.type == "NODES":
            check = {"kind": "smooth_shading_graph", "target": "$eval", "modifier": modifier.name, "expected": True}
            if verify(VerificationContext({"$eval": obj}, list(obj.users_scene[0].objects)), [check])["success"]:
                return True
    return False


def closed_checks(scene, task):
    """``(passed, result)``: the task's closed checks over the agent's objects (a delegation task names several)."""
    if task.get("targets"):  # The delegation set grades the objects it names.
        objects = {alias: scene.objects.get(name) for alias, name in live.task_targets(task).items()}
        obj = objects.get("$eval")
        result = (verify(VerificationContext(objects, list(scene.objects)), bind_checks(task["checks"], objects))
                  if all(objects.values()) else {"success": False, "status": "fail", "reason": "object missing"})
    else:
        obj = scene.objects.get(live.EVAL_OBJECT)
        result = (verify(VerificationContext(grading_targets(scene, obj), list(scene.objects)),
                         bind_checks(task["checks"], obj))
                  if obj is not None else {"success": False, "status": "fail", "reason": "object missing"})
    passed = bool(result["success"])
    if obj is not None and task.get("smooth_shading"):
        result["smooth_shaded"] = smooth_shaded(obj)
        passed = passed and result["smooth_shaded"]
    return passed, result
