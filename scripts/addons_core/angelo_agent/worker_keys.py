# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a placed root's own keys put it at other frames (``worker_placement.misplaced``, break-it E2E-15 H-1).

Blender plays a root's keys after a placement, at every frame change: keys on only some of its channels put back their
values beside the placed values of the others. A copy is right when, at each frame, it is where the placement carries
its animation (the placement applied to its keys' own pose then); when it is where its keys put it as built, the
placement is simply lost there, which the merge lists as moving later. Neither (an upside-down part keyed on Z, turned
about X; a part keyed on its place, turned; a turntable tilted) is warned. Told for a root its active action alone
keys with nothing else acting on it.

Where the keys play is judged across their whole range, never only near the lead's frame (break-it E2E-18 M-1: a
two-key full turn, tilted, is in both poses at its keys and in neither between them): each key time, three points
inside each key interval, and the frames now and next. The poses are composed from the channels as Blender composes
them (``BKE_object_to_mat4``) in plain matrix maths, once per object for the keys' own pose and once per copy for the
rest, never by writing channels; a plain move is told exactly with no frame at all (it can only leave the keyed
location axes behind). The whole check has a time budget per merge, and a copy past it is counted as unchecked, never
judged by guess (E2E-18 M-2: a 3,968-copy keyed merge froze Angelo 18.7 s).
"""

import time

from .worker_placement import CHANNELS, MOVE, ROUNDING, TRANSFORM_PATHS, _far, _keyed_paths, _strips, _wrong

NEITHER = ("its keys set only some of its channels: at other frames they put back their values beside the placed "
           "ones, so it lands neither where the placement carries its animation nor where its keys put it")
FRAMES = 64      # The most frames one root is judged at.
SECONDS = 1.0    # The whole check's budget in one merge (main thread, inside the merge's Undo step).
ROTATIONS = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}   # Else rotation_euler.


def _transform_keys(obj):
    """{(data path, index): F-curve} of ``obj``'s keyed transform channels when its active action alone sets them (in
    full, replacing: no NLA track playing any transform path, no driver on one), else None."""
    data = obj.animation_data
    if (data is None or data.action is None or data.action_slot is None or data.action_blend_type != "REPLACE"
            or data.action_influence < 1.0
            or {driver.data_path for driver in data.drivers if not driver.mute} & TRANSFORM_PATHS
            or any(_keyed_paths(getattr(strip, "action", None), getattr(strip, "action_slot", None)) & TRANSFORM_PATHS
                   for track in data.nla_tracks if not track.mute for strip in _strips(track.strips))):
        return None
    curves = {(curve.data_path, curve.array_index): curve for layer in data.action.layers for strip in layer.strips
              for bag in (strip.channelbag(data.action_slot),) if bag is not None for curve in bag.fcurves
              if not curve.mute and curve.data_path in TRANSFORM_PATHS}
    return None if any(path not in CHANNELS for path, _index in curves) else curves


def compose(channels, mode):
    """The basis Blender composes from stored channels ({path: values}) in rotation ``mode``: location plus its delta,
    the delta rotation after the rotation, the scale times its delta (``BKE_object_to_mat4``)."""
    from mathutils import Euler, Matrix, Quaternion, Vector
    if mode == "QUATERNION":
        turn = Quaternion(channels["delta_rotation_quaternion"]).normalized().to_matrix() @ Quaternion(
            channels["rotation_quaternion"]).normalized().to_matrix()
    elif mode == "AXIS_ANGLE":   # Blender exposes no delta axis-angle to Python: it stays the identity.
        angle, *axis = channels["rotation_axis_angle"]
        turn = Matrix.Rotation(angle, 3, Vector(axis).normalized()) if Vector(axis).length else Matrix.Identity(3)
    else:
        turn = Euler(channels["delta_rotation_euler"], mode).to_matrix() @ Euler(channels["rotation_euler"],
                                                                                   mode).to_matrix()
    scale = [a * b for a, b in zip(channels["scale"], channels["delta_scale"])]
    basis = (turn @ Matrix.Diagonal(scale)).to_4x4()
    basis.translation = [a + b for a, b in zip(channels["location"], channels["delta_location"])]
    return basis


def _frames(curves, frame):
    """Each key time, three points inside each key interval (where a turn between equal-looking keys shows), the
    frame now and the next: at most ``FRAMES``, evenly kept, the first and last key and now and next always."""
    keys = sorted({float(point.co[0]) for curve in curves.values() for point in curve.keyframe_points})
    times = sorted(set(keys) | {a + (b - a) * part for a, b in zip(keys, keys[1:]) for part in (0.25, 0.5, 0.75)})
    if len(times) > FRAMES - 2:
        step = (len(times) - 1) / (FRAMES - 3)
        times = [times[round(index * step)] for index in range(FRAMES - 2)]
    return sorted(set(times) | {float(frame), float(frame) + 1.0})


def _own(obj, curves, as_built, frame):
    """[(keyed values, the keys' own pose)] at each judged frame, once per object and action (copies share theirs)."""
    data = obj.animation_data
    key = (data.action.session_uid, data.action_slot.handle, obj.rotation_mode, frame)
    cache = as_built.setdefault("keys_own", {})
    if key not in cache:
        stored = {path: list(values) for path, values in as_built["channels"].items()}
        cache[key] = [(keyed, compose(_with(stored, keyed), obj.rotation_mode))
                      for keyed in ({k: curve.evaluate(at) for k, curve in curves.items()}
                                    for at in _frames(curves, frame))]
    return cache[key]


def _with(channels, keyed):
    merged = {path: list(values) for path, values in channels.items()}
    for (path, index), value in keyed.items():
        merged[path][index] = value
    return merged


def _full(curves, mode):
    """Every channel a placement writes is keyed: the keys put back the whole basis, so the placement is simply lost
    (listed as moving later), never neither."""
    rotation = ROTATIONS.get(mode, "rotation_euler")
    return all((path, index) in curves for path, count in (("location", 3), (rotation, 4 if mode in ROTATIONS else 3),
                                                            ("scale", 3)) for index in range(count))


def _moved_only(transform):
    m = transform.to_3x3()
    return all(m[i][j] == (1.0 if i == j else 0.0) for i in range(3) for j in range(3))


def budget():
    """One merge's budget: its deadline and how many copies were past it."""
    return {"deadline": time.perf_counter() + SECONDS, "unchecked": 0}


def keys_mix(copy, as_built, frame, transform, spend=None):
    """Whether ``copy``'s keys put it, at any frame they play, neither where the placement carries its animation
    (``transform`` applied to its keys' own pose then) nor where its keys put it as built (break-it E2E-15 H-1; E2E-17
    H-1, M-1; E2E-18 M-1). Told for a root its active action alone keys with nothing else acting on it (no constraint,
    no rigid body); None when it cannot be told, or when ``spend`` (``budget()``) is spent: counted there."""
    if copy.parent is not None or len(copy.constraints) or copy.rigid_body is not None or frame is None:
        return None
    curves = _transform_keys(copy)
    if not curves or _full(curves, copy.rotation_mode):
        return None if not curves else False
    if _moved_only(transform):   # Exact: only the keyed location axes are put back, so a move off both kinds of axis.
        tolerance = MOVE + ROUNDING * max(_far(transform), _far(as_built["basis"]))
        offs = [abs(transform[axis][3]) > tolerance for axis in range(3)]
        keyed = {index for path, index in curves if path == "location"}
        return any(offs[axis] for axis in keyed) and any(offs[axis] for axis in range(3) if axis not in keyed)
    if spend is not None and time.perf_counter() > spend["deadline"]:
        spend["unchecked"] += 1
        return None
    mode = copy.rotation_mode
    written = {path: list(getattr(copy, path)) for path in CHANNELS}
    reach = max(_far(copy.matrix_basis), _far(transform), _far(as_built["basis"]))
    if _wrong(compose(written, mode), copy.matrix_basis, reach):   # Composed otherwise than here: never guessed.
        return None
    for keyed, own in _own(copy, curves, as_built, frame):
        later, carried = compose(_with(written, keyed), mode), transform @ own
        far = max(reach, _far(later), _far(own), _far(carried))
        if _wrong(later, carried, far) and _wrong(later, own, far):
            return True
    return False
