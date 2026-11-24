# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rigging operators as typed wrapped operations (doc/angelo/wrapped_ops.rst; doc/angelo/rigging.rst, "Owed").

Plain data and plain functions, no Blender: ``wrapped_ops.registry()`` imports this module to register
``WRAPPED_OPS``. Each declaration wraps one operator of ``rigging_ops`` or ``ik_controls``; its parameters are a
closed JSON Schema whose names are the operator's own properties.

* **Not wrapped**: ``angelo.export_action_clips`` writes files (the picked file and clips beside it). The pattern has
  no output-path parameter yet, so it stays the person's, from its save browser, until one exists and a break-it
  review passes it.
* **input_bound** (``bone_frames`` = the deforming bones of the armature that is baked or read, times the frames of
  the actions played): PROVISIONAL, measured on the Linux lane VM (c2d-highcpu-56, ``PROVISIONAL_TIMINGS``) with the
  pattern's own rule (largest measured size whose nearest-rank p95 of 20 trials fits ``TURN_BUDGET_S``). The pattern
  requires Mac 1 and the Windows VM (``wrapped_op_budget.bound_from_timings``); those are owed before merge, and the
  numbers here are replaced by theirs. No Background Job kind exists for these bakes; over the bound a call is
  refused with nothing to shrink in its parameters (a shorter action or a deform-only source is the person's step).
* **checks** read the runner's before/after snapshots: the rig facts of Part E revision 7 (``SNAPSHOT_FACTS``). A fact
  a snapshot lacks is reported ``unknown`` (never a pass).
"""

SNAPSHOT_FACTS = {   # Part E revision 7's rig facts (v210-features, 2026-11-18), names as agreed.
    "objects": "{name: {'type', 'digest', 'parent', 'collections'}}: every object; its data digest (never the frame)",
    "actions": "{name: {'digest', 'users'}}: every action",
    "collections": "[name]: every collection",
    "armatures": "{name: {'bones', 'constraints'}}: each armature's bone and pose-constraint counts",
}

PROVISIONAL_TIMINGS = "linux-vm c2d-highcpu-56, 2026-11-18, v210-rigging timings.json"

ARMATURE_NAME = {"type": "string", "minLength": 1, "maxLength": 63}
VECTOR = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}


def _schema(properties, required=()):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(required)}


def _identity(schema):
    return {name: name for name in schema["properties"]}


def _check(name, ok, why):
    return {"check": name, "status": "unknown" if ok is None else ("pass" if ok else "fail"), "why": why}


def _fact(snapshot, key):
    return snapshot.get(key) if isinstance(snapshot, dict) else None


def _unchanged(name, before, after, label):
    objects_b, objects_a = _fact(before, "objects"), _fact(after, "objects")
    if objects_b is None or objects_a is None or name not in objects_b:
        return _check(label, None, "the snapshot lacks %r's digest" % name)
    return _check(label, objects_a.get(name, {}).get("digest") == objects_b[name].get("digest"),
                  "%s is read, never changed" % name)


def _new_actions(before, after):
    actions_b, actions_a = _fact(before, "actions"), _fact(after, "actions")
    if actions_b is None or actions_a is None:
        return None
    return sorted(set(actions_a) - set(actions_b))


def _same_objects(before, after, label):
    objects_b, objects_a = _fact(before, "objects"), _fact(after, "objects")
    if objects_b is None or objects_a is None:
        return _check(label, None, "the snapshot lacks the objects")
    return _check(label, set(objects_a) == set(objects_b), "no object is added or removed")


def retarget_checks(parameters, before, after):
    new = _new_actions(before, after)
    return [_unchanged(parameters["source"], before, after, "source_unchanged"),
            _check("one_new_action", None if new is None else len(new) == 1, "the target gets one new action"),
            _same_objects(before, after, "no_objects_added")]


def game_rig_checks(parameters, before, after):
    objects_b, objects_a = _fact(before, "objects"), _fact(after, "objects")
    added = None if objects_b is None or objects_a is None else sorted(set(objects_a) - set(objects_b))
    armatures = None if added is None else [n for n in added if objects_a[n].get("type") == "ARMATURE"]
    collections = (None if _fact(before, "collections") is None or _fact(after, "collections") is None
                   else sorted(set(after["collections"]) - set(before["collections"])))
    return [_unchanged(parameters["source"], before, after, "control_rig_unchanged"),
            _check("one_game_rig", None if armatures is None else len(armatures) == 1, "one new armature"),
            _check("one_collection", None if collections is None else len(collections) == 1,
                   "the game rig and its meshes in one new collection")]


def edits_one_armature(parameters, before, after):
    """The operation edits the armature it names and adds no object."""
    objects_b, objects_a = _fact(before, "objects"), _fact(after, "objects")
    others = (None if objects_b is None or objects_a is None else
              [n for n in objects_b if n != parameters["armature"]
               and objects_a.get(n, {}).get("digest") != objects_b[n].get("digest")])
    return [_same_objects(before, after, "no_objects_added"),
            _check("only_the_armature", None if others is None else not others, "no other object changes")]


def _counts(snapshot, name):
    armatures = _fact(snapshot, "armatures")
    return None if armatures is None or name not in armatures else armatures[name]


def ik_add_checks(parameters, before, after):
    b, a = _counts(before, parameters["armature"]), _counts(after, parameters["armature"])
    grew = None if b is None or a is None else (a.get("bones", 0) > b.get("bones", 0)
                                                 and a.get("constraints", 0) > b.get("constraints", 0))
    return edits_one_armature(parameters, before, after) + [
        _check("controls_added", grew, "effectors, poles and their constraints added to the armature")]


def ik_fk_checks(parameters, before, after):
    b, a = _counts(before, parameters["armature"]), _counts(after, parameters["armature"])
    same = None if b is None or a is None else (a.get("bones") == b.get("bones")
                                                 and a.get("constraints") == b.get("constraints"))
    return edits_one_armature(parameters, before, after) + [
        _check("no_bones_or_constraints_added", same, "the switch changes influences and keys only")]


def _receipt(after):
    return {"actions": len(_fact(after, "actions") or ()), "objects": len(_fact(after, "objects") or ())}


RETARGET = _schema({
    "source": ARMATURE_NAME, "target": ARMATURE_NAME,
    "root_motion": {"enum": ["KEEP", "IN_PLACE", "ROOT"], "default": "KEEP"},
    "limbs": {"enum": ["FK", "IK"], "default": "FK"},
    "plant_feet": {"type": "boolean", "default": False}}, required=("source", "target"))
GAME_RIG = _schema({"source": ARMATURE_NAME, "target_engine": {"enum": ["GODOT", "UNITY", "UNREAL"],
                                                               "default": "UNREAL"}}, required=("source",))
ROOT_MOTION = _schema({"armature": ARMATURE_NAME, "mode": {"enum": ["ROOT", "IN_PLACE"], "default": "ROOT"},
                       "yaw": {"type": "boolean", "default": False}}, required=("armature",))
IK_ADD = _schema({"armature": ARMATURE_NAME}, required=("armature",))
IK_FK = _schema({"armature": ARMATURE_NAME, "mode": {"enum": ["IK", "FK"], "default": "IK"},
                 "limb": {"enum": ["ALL", "ARMS", "LEGS"], "default": "ALL"},
                 "snap": {"type": "boolean", "default": True}, "key": {"type": "boolean", "default": False}},
                required=("armature",))
POSE = _schema({
    "armature": ARMATURE_NAME,
    "targets": {"type": "array", "uniqueItems": True, "minItems": 1, "items": {
        "enum": ["HIPS", "HEAD", "LEFT_HAND", "RIGHT_HAND", "LEFT_FOOT", "RIGHT_FOOT"]}},
    "hips": VECTOR, "head": VECTOR, "left_hand": VECTOR, "right_hand": VECTOR, "left_foot": VECTOR,
    "right_foot": VECTOR,
    "pin_feet": {"type": "boolean", "default": True}, "limits": {"type": "boolean", "default": True},
    "balance": {"type": "boolean", "default": True}}, required=("armature", "targets"))

# Provisional bounds (bone_frames), see the module's docstring; replaced by Mac 1 + Windows measurements at merge.
# 33-bone rig, nearest-rank p95 of 20 trials (s): retarget 0.38 / 1.55 / 3.17 / 6.24 at 8 250 / 33 000 / 66 000 /
# 132 000 bone_frames (the largest measured: not a ceiling); game rig 0.79 / 3.06 / 6.05 / 11.91 (over at 132 000);
# root motion 0.32 / 1.20 / 2.40 / 4.76. Pose Body: p95 0.007 s, constant per call (input_bound None).
BAKE_BOUND = {"rig_retarget": {"bone_frames": 132000}, "game_rig_make": {"bone_frames": 66000},
              "root_motion_extract": {"bone_frames": 132000}}

WRAPPED_OPS = (
    dict(kind="rig_retarget", operator="angelo.retarget_animation", source="angelo", target="ARMATURE",
         mode="OBJECT", parameters=RETARGET, map=_identity(RETARGET), reads_only=False, creates=None,
         new_ids={"ACTION"}, mutates={"animation", "pose"}, input_bound=BAKE_BOUND["rig_retarget"],
         checks=retarget_checks, receipt=_receipt, guide="rigging"),
    dict(kind="game_rig_make", operator="angelo.make_game_rig", source="angelo", target="ARMATURE", mode="OBJECT",
         parameters=GAME_RIG, map=_identity(GAME_RIG), reads_only=False, creates={"object_with_children"},
         new_ids={"ARMATURE", "ACTION", "COLLECTION", "MESH"}, mutates=set(),
         input_bound=BAKE_BOUND["game_rig_make"], checks=game_rig_checks, receipt=_receipt, guide="rigging"),
    dict(kind="root_motion_extract", operator="angelo.extract_root_motion", source="angelo", target="ARMATURE",
         mode="OBJECT", parameters=ROOT_MOTION, map=_identity(ROOT_MOTION), reads_only=False, creates=None,
         mutates={"animation", "pose"}, input_bound=BAKE_BOUND["root_motion_extract"],
         checks=edits_one_armature, receipt=_receipt, guide="rigging"),
    dict(kind="ik_controls_add", operator="angelo.add_ik_controls", source="angelo", target="ARMATURE",
         mode="OBJECT", parameters=IK_ADD, map=_identity(IK_ADD), reads_only=False, creates=None,
         mutates={"armature", "pose"}, input_bound=None, checks=ik_add_checks, guide="rigging"),
    dict(kind="ik_fk_set", operator="angelo.set_ik_fk", source="angelo", target="ARMATURE", mode="OBJECT",
         parameters=IK_FK, map=_identity(IK_FK), reads_only=False, creates=None, mutates={"pose", "animation"},
         input_bound=None, checks=ik_fk_checks, guide="rigging"),
    dict(kind="body_pose", operator="angelo.pose_body", source="angelo", target="ARMATURE", mode="OBJECT",
         parameters=POSE, map=_identity(POSE), reads_only=False, creates=None, mutates={"pose"},
         input_bound=None, checks=edits_one_armature, guide="rigging"),
)
