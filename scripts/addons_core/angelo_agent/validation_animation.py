# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed key, shape-key and NLA predicates for already-resolved objects.

Keys are read from the object's own Action slot channelbag (Blender 5 layered
Actions), the same channels the animation kernels edit. These certify stored
keys, shape-key values and NLA structure, not evaluated motion. A shape-key
value that an Action animates is inconclusive. Read-only; no frame changes.
"""

from .validation_reader import Inconclusive, values_near

ANIMATION_CHECKS = frozenset({"keyframes", "shape_key", "nla", "fcurve_modifier", "markers"})
FRAME_EPSILON = .0001


def _channelbag(obj):
    animation = getattr(obj, "animation_data", None)
    action = getattr(animation, "action", None)
    slot = getattr(animation, "action_slot", None)
    if action is None or slot is None:
        return None
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            bag = strip.channelbag(slot) if hasattr(strip, "channelbag") else None
            if bag is not None:
                return bag
    return None


def _keyframes(reader, obj, check):
    # A data path ("data.lens") is keyed on the object's data, under the path inside it.
    owner, data_path = obj, check["property"]
    if data_path.startswith("data."):
        owner, data_path = getattr(obj, "data", None), data_path[len("data."):]
    bag = _channelbag(owner)
    curves = []
    if bag is not None:
        if check["index"] < 0:  # Every array index of the path (a quaternion has four, a colour four).
            for index in range(64):
                curve = bag.fcurves.find(data_path, index=index)
                if curve is None and index >= 3:
                    break
                curves += [curve] if curve is not None else []
        else:
            curve = bag.fcurves.find(data_path, index=check["index"])
            curves = [curve] if curve is not None else []
    if len(curves) and sum(len(curve.keyframe_points) for curve in curves) > 8192:
        raise Inconclusive("Key count exceeds observation limits")
    reader.spend(1 + len(check["frames"]) + len(check["absent"]))

    def keys(frame):
        return [point for curve in curves for point in curve.keyframe_points if abs(point.co.x - frame) < FRAME_EPSILON]

    missing, present, mismatched = [], [], []
    for frame in check["frames"]:
        found = keys(frame)
        if not found:
            missing.append(frame)
        for key, attribute in (("interpolation", "interpolation"), ("easing", "easing"), ("handle_type", "handle_left_type"),
                               ("key_type", "type")):
            if key in check and any(getattr(point, attribute) != check[key] or
                                    (key == "handle_type" and point.handle_right_type != check[key]) for point in found):
                mismatched.append([frame, key])
    if "extrapolation" in check and any(curve.extrapolation != check["extrapolation"] for curve in curves):
        mismatched.append([None, "extrapolation"])
    present = [frame for frame in check["absent"] if keys(frame)]
    observed = {"channels": len(curves), "keys": sum(len(curve.keyframe_points) for curve in curves)}
    if missing or present or mismatched:
        observed.update(missing=missing, still_present=present, mismatched=mismatched[:16])
    return not missing and not present and not mismatched, observed


def _shape_key(reader, obj, check):
    key = getattr(getattr(obj, "data", None), "shape_keys", None)
    block = key.key_blocks.get(check["name"]) if key is not None else None
    reader.spend(1)
    exists = block is not None
    if not check.get("exists", True) or not exists:
        return exists == check.get("exists", True), {"exists": exists}
    observed = {"exists": True, "value": float(block.value)}
    if "value" in check:
        bag = _channelbag(key)
        path = 'key_blocks["%s"].value' % block.name.replace('"', '\\"')
        if bag is not None and bag.fcurves.find(path) is not None:
            raise Inconclusive("An animated shape-key value can differ from its stored value")
        tolerance = check.get("tolerance", max(1e-6, 4e-7 * abs(check["value"])))
        return values_near(float(block.value), check["value"], tolerance), observed
    return True, observed


def _nla(reader, obj, check):
    animation = getattr(obj, "animation_data", None)
    tracks = len(animation.nla_tracks) if animation is not None else 0
    action = animation is not None and animation.action is not None
    reader.spend(1)
    passed = tracks >= check.get("minimum_tracks", 0) and ("active_action" not in check or action == check["active_action"])
    observed = {"tracks": tracks, "active_action": action}
    if any(key in check for key in ("action", "track_names", "absent_tracks", "absent_strips", "strip")):
        from .validation_anim_edits import nla_structure
        structure, details = nla_structure(reader, obj, check)
        passed, observed = passed and structure, dict(observed, **details)
    return passed, observed


def evaluate_animation(reader, obj, check):
    """Return (passed, bounded metrics) for one canonical key, shape-key or NLA check."""
    if check["kind"] == "keyframes":
        return _keyframes(reader, obj, check)
    if check["kind"] == "shape_key":
        return _shape_key(reader, obj, check)
    if check["kind"] in {"fcurve_modifier", "markers"}:
        from . import validation_anim_edits as edits
        return (edits.fcurve_modifier if check["kind"] == "fcurve_modifier" else edits.markers)(reader, obj, check)
    return _nla(reader, obj, check)
