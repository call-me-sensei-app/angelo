# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed predicates for F-Curve modifiers, NLA structure and timeline markers.

Read-only and bounded, like ``validation_animation``: they read stored data
(the slot's channels, the NLA stack, the scene's markers), never evaluated
motion, and change no frame. On a private stage the Action a check names is
read under the name commit gives it (``action_names`` in the verification
context: renames, assigned Actions, adopted original names).
"""

from .validation_reader import Inconclusive, context_value, resolve_target

FLOAT_TOLERANCE = 1e-4


def _modifier_curves(obj, check):
    from .anim_channels import owner_and_path, channelbag
    try:
        owner, path = owner_and_path(obj, check["property"])
    except ValueError:
        return []
    bag = channelbag(owner)
    if bag is None:
        return []
    return [curve for curve in bag.fcurves if curve.data_path == path
            and (check["index"] < 0 or curve.array_index == check["index"])]


def fcurve_modifier(reader, obj, check):
    """A named F-Curve modifier on every matching channel, with its settings (or its absence)."""
    from .generic_settings import evaluate
    curves = _modifier_curves(obj, check)
    reader.spend(1 + len(curves))
    if not curves:
        return False, {"channels": 0}
    wanted = check.get("exists", True)
    observed, passed = {"channels": len(curves)}, True
    for curve in curves:
        modifier = next((item for item in curve.modifiers if item.name == check["name"]), None)
        if (modifier is not None) != wanted:
            observed.setdefault("mismatched_channels", []).append(curve.array_index)
            passed = False
            continue
        if modifier is not None and check.get("values"):
            ok, values = evaluate(modifier, check["values"], check.get("tolerance", FLOAT_TOLERANCE),
                                  lambda reference: resolve_target(reader.context, reference))
            observed["values"] = values.get("values")
            if not ok:
                observed.setdefault("mismatched_channels", []).append(curve.array_index)
                passed = False
    return passed, observed


def _strip_values(strip, values):
    mismatched = []
    for name, value in values.items():
        actual = getattr(strip, name, None)
        if isinstance(value, bool) or isinstance(value, str):
            ok = actual == value
        else:
            ok = actual is not None and abs(float(actual) - float(value)) <= FLOAT_TOLERANCE * max(1, abs(value))
        if not ok:
            mismatched.append(name)
    return mismatched


def nla_structure(reader, obj, check):
    """The extended ``nla`` fields: active Action name, track names present/absent, one strip's settings."""
    animation = getattr(obj, "animation_data", None)
    tracks = list(animation.nla_tracks) if animation is not None else []
    reader.spend(1 + len(tracks))
    names = [track.name for track in tracks]
    observed, failures = {"tracks": [name[:64] for name in names[:16]]}, []
    if "action" in check:
        action = animation.action if animation is not None else None
        mapped = context_value(reader.context, "action_names", None) or {}
        name = mapped.get(action.name, action.name) if action is not None else None
        observed["action"] = name[:64] if name is not None else None
        if name != check["action"]:
            failures.append("action")
    missing = [name for name in check.get("track_names", ()) if names.count(name) != 1]
    present = [name for name in check.get("absent_tracks", ()) if name in names]
    strips = {strip.name: (track.name, strip) for track in tracks for strip in track.strips}
    still = [name for name in check.get("absent_strips", ()) if name in strips]
    if missing or present or still:
        observed.update(missing_tracks=missing, still_present=present + still)
        failures.append("tracks")
    if "strip" in check:
        wanted = check["strip"]
        found = strips.get(wanted["name"])
        if found is None or found[0] != wanted["track"]:
            observed["strip"] = None if found is None else {"track": found[0][:64]}
            failures.append("strip")
        else:
            mismatched = _strip_values(found[1], wanted.get("values", {}))
            observed["strip"] = {"frame_start": round(found[1].frame_start, 4), "frame_end": round(found[1].frame_end, 4)}
            if mismatched:
                observed["strip"]["mismatched"] = mismatched
                failures.append("strip")
    return not failures, observed


def markers(reader, scene, check):
    """Markers present by exact unique name (with frame and bound camera when stated) and names absent."""
    rows = list(getattr(scene, "timeline_markers", ()))
    if getattr(getattr(scene, "bl_rna", None), "identifier", "Scene") != "Scene":
        return False, {"reason": "markers belong to the scene; target the scene handle"}
    reader.spend(1 + len(rows))
    by_name = {}
    for marker in rows:
        by_name.setdefault(marker.name, []).append(marker)
    failures = []
    for wanted in check.get("present", ()):
        found = by_name.get(wanted["name"], [])
        if len(found) != 1:
            failures.append({"name": wanted["name"][:64], "count": len(found)})
            continue
        marker = found[0]
        if "frame" in wanted and int(marker.frame) != wanted["frame"]:
            failures.append({"name": wanted["name"][:64], "frame": int(marker.frame)})
        if "camera" in wanted:
            expected = resolve_target(reader.context, wanted["camera"]) if wanted["camera"] is not None else None
            if wanted["camera"] is not None and expected is None:
                raise Inconclusive("The marker's camera is not a bound target")
            if marker.camera != expected:
                failures.append({"name": wanted["name"][:64], "camera": getattr(marker.camera, "name", "")[:64] or None})
    still = [name for name in check.get("absent", ()) if name in by_name]
    observed = {"markers": [[marker.name[:64], int(marker.frame)] for marker in rows[:32]]}
    if failures or still:
        observed.update(mismatched=failures[:16], still_present=still)
    return not failures and not still, observed
