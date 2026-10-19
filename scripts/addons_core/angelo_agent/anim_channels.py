# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Channels, keys and the private-Action guard shared by the staged animation kernels.

Channels are read and written through the owner's assigned Action slot and its
channelbag (Blender 5 layered Actions), never ``Action.fcurves``. A kernel may
edit an Action only when it is the program's private staged copy
(``require_private``) and the live Action it stands for is used by nothing but
the target object and its own data (``anim_stage`` records the live use at
staging): an Action another object, an NLA strip or a fake user also keeps is
refused (``ACTION_SHARED``) rather than edited for everyone or silently split;
``action_set`` ``SINGLE_USER`` isolates it explicitly. Drivers and NLA tweak
mode are refused, as for ``keyframe_insert``.

Bounds: an operation reads or writes at most ``MAX_KEYS`` keys over at most
``MAX_CHANNELS`` channels; the kernels check before writing. ``MAX_KEYS`` stays
under the observation budget (``scene_queries.MAX_METADATA_ITEMS``, 12,000
items, about one per key) and the keyframes check's 8,192 keys, so an edited
object stays bindable by an exact revision and checkable; every key operation
is linear in the keys but DECIMATE (n log n).
"""

from .refusal_codes import RefusalError


FRAME_EPSILON = .0001
MAX_KEYS = 8000
MAX_CHANNELS = 1024
SHARED = "action_shared"      # extras: handle -> {"object"|"data": other live users of that Action}
ASSIGNED = "action_assigned"  # extras: handle -> live Action uid an ASSIGN keeps until commit


# Only intentional domain refusals grant correction. Wrapper-only codes (such as
# STRIP_OVERLAP) stay unknown unless their underlying error supplied a policy.
_REFUSAL_CODES = {
    **dict.fromkeys((
        "ACTION_ASSIGNED", "ACTION_ID", "ACTION_NOT_SHARED", "ACTION_ORPHANED", "ACTION_SHARED", "ALREADY_ASSIGNED",
        "CHANNEL_MISMATCH", "CHANNEL_NOT_FOUND", "FMODIFIER_NOT_FOUND", "KEYS_NOT_FOUND", "KEY_NOT_FOUND",
        "MARKER_AMBIGUOUS", "MARKER_CAMERA_UNDECLARED", "MARKER_NOT_FOUND", "NAME_TAKEN", "NOTHING_TO_STASH",
        "NOT_A_CAMERA", "NO_ACTION", "NO_DATA", "NO_NLA", "OWNER_TYPE_MISMATCH", "RNA_ARRAY_LENGTH",
        "SLOT_AMBIGUOUS", "SLOT_NOT_FOUND", "SLOT_TYPE", "STRIP_NOT_FOUND", "TRACK_AMBIGUOUS", "TRACK_NOT_FOUND",
        "TWEAK_MODE",
    ), "VALIDATION"),
    **dict.fromkeys(("ACTION_LINKED", "DRIVEN", "FMODIFIER_UNSUPPORTED", "STRIP_ANIMATED", "STRIP_UNSUPPORTED"),
                    "UNSUPPORTED"),
    **dict.fromkeys(("FMODIFIER_COST", "TOO_MANY_CHANNELS", "TOO_MANY_FMODIFIERS", "TOO_MANY_KEYS",
                     "TOO_MANY_MARKERS", "TOO_MANY_STRIPS", "TOO_MANY_TRACKS"), "LIMIT_EXCEEDED"),
    **dict.fromkeys(("ACTION_EXPIRED", "ACTION_NOT_FOUND"), "STALE_DEPENDENCY"),
    **dict.fromkeys(("VALUE_NOT_RETAINED", "STRIP_VALUE_NOT_RETAINED"), "INTERNAL_ERROR"),
}


class AnimationError(RefusalError):
    """A refused animation edit; domain ``code`` stays stable for existing callers."""

    def __init__(self, code, reason, *, refusal_code=None, details=None):
        policy = _REFUSAL_CODES.get(code, "UNKNOWN") if refusal_code is None else refusal_code
        super().__init__("%s: %s" % (code, reason), code=policy, details=details)
        self.refusal_code = policy
        self.code = code


def owner_and_path(obj, path):
    """(ID that owns the channel, path inside it): ``data.`` paths belong to the object data."""
    if path is not None and path.startswith("data."):
        if obj.data is None:
            raise AnimationError("NO_DATA", "the object has no data to animate")
        return obj.data, path[len("data."):]
    return obj, path


def channelbag(owner, ensure=False):
    """The channelbag of ``owner``'s assigned Action slot, or None (created on the first strip when ensure)."""
    animation = getattr(owner, "animation_data", None)
    action = getattr(animation, "action", None)
    slot = getattr(animation, "action_slot", None)
    if action is None or slot is None:
        return None
    for layer in action.layers:
        for strip in layer.strips:
            bag = strip.channelbag(slot)
            if bag is not None:
                return bag
    if not ensure:
        return None
    layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type="KEYFRAME")
    return strip.channelbag(slot, ensure=True)


def guard(context, target, family):
    """Refuse an Action the live scene shares with other users, or one an ASSIGN keeps live."""
    if context.extras.get(ASSIGNED, {}).get(target) is not None and family == "object":
        raise AnimationError("ACTION_ASSIGNED", "this program assigns an existing Action, which commit uses unedited; "
                             "edit its keys in a later program")
    others = context.extras.get(SHARED, {}).get(target, {}).get(family, 0)
    if others:
        raise AnimationError("ACTION_SHARED", "the Action is also used by %d other user(s) (objects, NLA strips or a "
                             "fake user); give this object its own copy first with action_set mode SINGLE_USER, or "
                             "edit it for every user with reviewed Python" % others)


def private_animation(context, obj, target, path):
    """(owner, path in owner, its private staged Action) for an edit of ``path``'s channels."""
    owner, inner = owner_and_path(obj, path)
    animation = getattr(owner, "animation_data", None)
    if animation is None or animation.action is None:
        raise AnimationError("KEYS_NOT_FOUND", "the %s has no Action with keys" % ("data" if owner != obj else "object"))
    if len(animation.drivers):
        raise AnimationError("DRIVEN", "animation with drivers needs reviewed Python; driver expressions are not "
                             "evaluated natively")
    if animation.use_tweak_mode:
        raise AnimationError("TWEAK_MODE", "the NLA is in tweak mode; exit it first")
    guard(context, target, "data" if owner != obj else "object")
    action = context.require_private(animation.action)
    return owner, inner, action


def select_curves(bag, path, index):
    """F-Curves of ``path`` (every channel when None) at ``index`` (-1: every array index)."""
    if bag is None:
        return []
    found = [curve for curve in bag.fcurves if (path is None or curve.data_path == path)
             and (index < 0 or curve.array_index == index)]
    if len(found) > MAX_CHANNELS:
        raise AnimationError("TOO_MANY_CHANNELS", "at most %d channels per operation" % MAX_CHANNELS)
    return found


def edit_curves(context, obj, target, params):
    """The selected, keyed F-Curves of a private staged Action, with the key budget checked."""
    owner, inner, _action = private_animation(context, obj, target, params.get("property"))
    curves = select_curves(channelbag(owner), inner, params.get("index", -1))
    if not curves:
        raise AnimationError("CHANNEL_NOT_FOUND", "no animated %s channel%s" % (
            params.get("property") or "object", "" if params.get("index", -1) < 0 else " at index %d" % params["index"]))
    if sum(len(curve.keyframe_points) for curve in curves) > MAX_KEYS:
        raise AnimationError("TOO_MANY_KEYS", "the channels hold more than %d keys" % MAX_KEYS)
    return owner, curves


def in_selection(frame, frames, frame_range):
    if frames:
        return any(abs(frame - wanted) < FRAME_EPSILON for wanted in frames)
    if frame_range is not None:
        return frame_range[0] - FRAME_EPSILON <= frame <= frame_range[1] + FRAME_EPSILON
    return True


def selected_points(curve, frames=(), frame_range=None):
    """Keyframe points of ``curve`` at ``frames`` or inside ``frame_range`` (every key when neither)."""
    return [point for point in curve.keyframe_points if in_selection(point.co.x, frames, frame_range)]


def require_frames(curves, frames, path):
    """Raise when an explicitly named frame has no key on a selected channel."""
    for curve in curves:
        for frame in frames:
            if not any(abs(point.co.x - frame) < FRAME_EPSILON for point in curve.keyframe_points):
                raise AnimationError("KEY_NOT_FOUND", "no %s[%d] key at frame %d" % (
                    (path or curve.data_path)[:128], curve.array_index, frame))


def finish(curves):
    """Sort keys, merge exact duplicates and recalculate automatic handles after an edit."""
    for curve in curves:
        curve.keyframe_points.sort()
        curve.keyframe_points.deduplicate()
        curve.keyframe_points.handles_recalc()
        curve.update()


def remove_empty(owner, curves):
    """Remove channels an edit left without keys (and without modifiers), as Delete Keyframes does."""
    bag = channelbag(owner)
    removed = 0
    for curve in curves:
        if not len(curve.keyframe_points) and not len(curve.modifiers) and bag is not None:
            bag.fcurves.remove(curve)
            removed += 1
    return removed
