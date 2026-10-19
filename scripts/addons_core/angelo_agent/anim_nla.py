# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``nla_edit`` on a staged object, and the NLA stack snapshot commit rebuilds from.

Tracks and strips are addressed by exact name; a track name used twice is
ambiguous and refused (Blender keeps strip names unique per object, not track
names). Only plain action strips are admitted: meta, transition and sound
strips, strips with animated influence or time, strip F-Curve modifiers and
tweak mode are refused, so a snapshot (``snapshot``/``restore``) holds every
setting a rebuild can write, the active track included. A strip's active flag
is read-only from Python: a rebuilt stack has no active strip, so the rollback
of a stack that had one reads back as changed and is reported unverified.
New tracks and strips are created unselected. A strip
added from an existing Action plays a staged copy of it; ``strip_sources``
(``anim_stage``) keeps the live Action, which commit puts back.

Commit (``rebuild``): the live object's tracks are replaced by the staged
stack, strips pointing at the live Actions their staged copies stand for, or
at the staged Action itself when the program made it (a push-down or a stash);
the previous stack is kept as a snapshot and restored on rollback.
"""

from .refusal_codes import refusal_code, refusal_details
from .anim_channels import AnimationError
from .anim_structure_spec import STRIP_FIELDS

MAX_TRACKS = 64
MAX_STRIPS = 256
# Settings copied verbatim, after timing; frame_start/frame_end are written through the raw setters on rebuild.
STRIP_COPY = ("blend_type", "extrapolation", "blend_in", "blend_out", "use_auto_blend", "influence", "mute",
              "use_reverse", "use_sync_length", "select")


def admit(animation):
    """Raise unless every track and strip is something a snapshot captures exactly."""
    if animation is None:
        return
    if animation.use_tweak_mode:
        raise AnimationError("TWEAK_MODE", "the NLA is in tweak mode; exit it first")
    if len(animation.nla_tracks) > MAX_TRACKS:
        raise AnimationError("TOO_MANY_TRACKS", "at most %d NLA tracks" % MAX_TRACKS)
    strips = [strip for track in animation.nla_tracks for strip in track.strips]
    if len(strips) > MAX_STRIPS:
        raise AnimationError("TOO_MANY_STRIPS", "at most %d NLA strips" % MAX_STRIPS)
    for strip in strips:
        if strip.type != "CLIP":
            raise AnimationError("STRIP_UNSUPPORTED", "%s strips (meta, transition, sound) need reviewed Python" % strip.type)
        if strip.use_animated_influence or strip.use_animated_time or len(strip.fcurves) or len(strip.modifiers):
            raise AnimationError("STRIP_ANIMATED", "strips with animated influence or time, or strip modifiers, need "
                                 "reviewed Python")


def _track(animation, name):
    found = [track for track in animation.nla_tracks if track.name == name]
    if not found:
        names = ", ".join(repr(track.name[:64]) for track in animation.nla_tracks[:16])
        raise AnimationError("TRACK_NOT_FOUND", "no NLA track is named %r; tracks: %s" % (name[:64], names or "none"))
    if len(found) > 1:
        raise AnimationError("TRACK_AMBIGUOUS", "%d NLA tracks are named %r; rename one first" % (len(found), name[:64]))
    return found[0]


def _strip(track, name):
    strip = track.strips.get(name)
    if strip is None:
        raise AnimationError("STRIP_NOT_FOUND", "track %r has no strip named %r" % (track.name[:64], name[:64]))
    return strip


def slot_range(action, slot):
    """The keyed frame range of ``slot``'s channels (the Action's manual range when it has one)."""
    if action.use_frame_range:
        return action.frame_start, action.frame_end
    frames = [point.co.x for layer in action.layers for item in layer.strips
              for bag in ([item.channelbag(slot)] if slot is not None else []) if bag is not None
              for curve in bag.fcurves for point in curve.keyframe_points]
    return (min(frames), max(frames)) if frames else (0.0, 1.0)


def write_strip(strip, settings):
    """Typed strip settings, timing first; read back so a clamped or overlapping value fails."""
    for name in STRIP_FIELDS:
        value = settings.get(name)
        if value is None:
            continue
        attribute = {"frame_start": "frame_start_ui", "frame_end": "frame_end_ui"}.get(name, name)
        setattr(strip, attribute, value)
        actual = getattr(strip, name)
        if (abs(actual - value) > 1e-4) if isinstance(value, float) else actual != value:
            raise AnimationError("STRIP_VALUE_NOT_RETAINED", "%s read back %r after writing %r (the strip may overlap "
                                 "another or exceed its action range)" % (name, actual, value))


def _unique_name(animation, name):
    if any(track.name == name for track in animation.nla_tracks):
        raise AnimationError("NAME_TAKEN", "an NLA track is already named %r" % name[:64])


def apply(context, obj, operation):
    """nla_edit: one track or strip edit on the staged object's NLA stack."""
    from .anim_actions import resolve_action_id, staged_reference
    params = operation["parameters"]
    kind = params["operation"]
    animation = obj.animation_data
    if animation is None and kind != "TRACK_ADD":
        raise AnimationError("NO_NLA", "the object has no animation data or NLA tracks")
    animation = animation or obj.animation_data_create()
    admit(animation)
    strips = context.extras.setdefault("strip_sources", {}).setdefault(operation["target"], {})
    if kind == "TRACK_ADD":
        _unique_name(animation, params["track"])
        if len(animation.nla_tracks) >= MAX_TRACKS:
            raise AnimationError("TOO_MANY_TRACKS", "at most %d NLA tracks" % MAX_TRACKS)
        track = animation.nla_tracks.new()
        track.name, track.select = params["track"], False
        if params["position"] == "BOTTOM":
            move_track(animation, track, 0)
    elif kind == "TRACK_REMOVE":
        track = _track(animation, params["track"])
        for strip in track.strips:
            strips.pop(strip.name, None)
        animation.nla_tracks.remove(track)
    elif kind == "TRACK_SET":
        track = _track(animation, params["track"])
        if params["name"] is not None and params["name"] != track.name:
            _unique_name(animation, params["name"])
            track.name = params["name"]
        for key, attribute in (("mute", "mute"), ("solo", "is_solo"), ("lock", "lock")):
            if params[key] is not None:
                setattr(track, attribute, params[key])
        if params["index"] is not None:
            move_track(animation, track, params["index"])
    elif kind == "STRIP_ADD":
        track = _track(animation, params["track"])
        live = resolve_action_id(params["action"])
        action = staged_reference(context, live)
        name = params["name"] or live.name
        try:
            strip = track.strips.new(name[:64], int(round(params["frame"])), action)
        except RuntimeError as error:
            raise AnimationError("STRIP_OVERLAP", "the track has no room for the strip at that frame: " + str(error)[:120],
                                 refusal_code=refusal_code(error), details=refusal_details(error))
        strip.select = False
        from .anim_actions import slot_named, auto_slot
        strip.action_slot = (slot_named(action, params["slot"]) if params["slot"] is not None else auto_slot(
            action, context.extras.get("live_names", {}).get(operation["target"], obj.name), animation.last_slot_identifier))
        start, end = slot_range(action, strip.action_slot)  # The range Blender would give a new strip of this slot.
        strip.action_frame_end, strip.action_frame_start = max(end, start + 1), start
        if abs(strip.frame_start - params["frame"]) > 1e-4:
            strip.frame_start_ui = params["frame"]
        write_strip(strip, params["settings"])
        strips[strip.name] = int(live.session_uid)
    elif kind == "STRIP_REMOVE":
        track = _track(animation, params["track"])
        strip = _strip(track, params["strip"])
        strips.pop(strip.name, None)
        track.strips.remove(strip)
    else:
        track = _track(animation, params["track"])
        strip = _strip(track, params["strip"])
        if params["move_to_track"] is not None:
            strip = move_strip(strip, track, _track(animation, params["move_to_track"]), strips)
        if params["name"] is not None and params["name"] != strip.name:
            if any(item.name == params["name"] for item_track in animation.nla_tracks for item in item_track.strips):
                raise AnimationError("NAME_TAKEN", "a strip is already named %r" % params["name"][:64])
            source = strips.pop(strip.name, None)
            strip.name = params["name"]
            if source is not None:
                strips[strip.name] = source
        write_strip(strip, params["settings"])
    return _summary(obj, operation, animation)


def move_track(animation, track, index):
    """Rebuild the stack with ``track`` at ``index`` (0 = bottom); tracks have no RNA move."""
    order = [item for item in animation.nla_tracks if item != track]
    order.insert(min(index, len(order)), track)
    if [item.as_pointer() for item in order] == [item.as_pointer() for item in animation.nla_tracks]:
        return track
    state = [track_state(item) for item in order]
    restore(animation, state)
    return animation.nla_tracks[min(index, len(order) - 1)]


def move_strip(strip, source, target, strips):
    """Move a strip to another track at the same frames; refused where it would overlap."""
    row = strip_state(strip)
    name = strip.name
    source.strips.remove(strip)
    try:
        moved = build_strip(target, row, lambda strip_row: strip_row["action"])
    except (RuntimeError, AnimationError) as error:
        build_strip(source, row, lambda strip_row: strip_row["action"])
        raise AnimationError("STRIP_OVERLAP", "track %r has no room for the strip: %s" % (target.name[:64], str(error)[:120]),
                             refusal_code=refusal_code(error), details=refusal_details(error))
    if moved.name != name:
        moved.name = name
    return moved


def strip_state(strip):
    slot = strip.action_slot
    return {"name": strip.name, "action": strip.action, "slot": slot.identifier if slot is not None else None,
            "action_frame_start": strip.action_frame_start, "action_frame_end": strip.action_frame_end,
            "scale": strip.scale, "repeat": strip.repeat, "frame_start": strip.frame_start,
            "frame_end": strip.frame_end, **{name: getattr(strip, name) for name in STRIP_COPY}}


def track_state(track):
    return {"name": track.name, "mute": track.mute, "solo": track.is_solo, "lock": track.lock, "select": track.select,
            "active": track.active, "strips": [strip_state(strip) for strip in track.strips]}


def snapshot(animation):
    """The whole NLA stack as plain rows (Action IDs kept by reference)."""
    return [track_state(track) for track in animation.nla_tracks] if animation is not None else []


def build_strip(track, row, action_of):
    action = action_of(row)
    strip = track.strips.new(row["name"], int(round(row["frame_start"])), action)
    if action is not None:  # Exactly the staged slot: Blender's own pick follows the (live or private) ID name.
        strip.action_slot = next((item for item in action.slots if item.identifier == row["slot"]), None)
    strip.action_frame_start, strip.action_frame_end = row["action_frame_start"], row["action_frame_end"]
    strip.scale, strip.repeat = row["scale"], row["repeat"]
    strip.frame_start_raw, strip.frame_end_raw = row["frame_start"], row["frame_end"]
    for name in STRIP_COPY:
        setattr(strip, name, row[name])
    return strip


def build_track(animation, row, action_of):
    track = animation.nla_tracks.new()
    track.name, track.mute, track.lock, track.select = row["name"], row["mute"], row["lock"], row["select"]
    for strip_row in row["strips"]:
        build_strip(track, strip_row, action_of)
    if row["solo"]:
        track.is_solo = True
    return track


def restore(animation, rows, action_of=lambda row: row["action"]):
    """Replace every track of ``animation`` with ``rows`` (a snapshot).

    ``nla_tracks.new`` makes each new track the active one, so the snapshot's
    active track (or none) is set again last; otherwise a rollback would leave a
    different track active and its read-back could not be verified as exact.
    """
    for track in list(animation.nla_tracks):
        animation.nla_tracks.remove(track)
    tracks = [build_track(animation, row, action_of) for row in rows]
    animation.nla_tracks.active = next((track for track, row in zip(tracks, rows) if row["active"]), None)


def comparable(rows, action_key):
    """Snapshot rows with Actions replaced by ``action_key(action)`` and floats rounded, for equality."""
    def value(item):
        return round(item, 4) if isinstance(item, float) else item
    return [dict({key: value(item) for key, item in row.items() if key != "strips"},
                 strips=[{key: action_key(item) if key == "action" else value(item) for key, item in strip.items()}
                         for strip in row["strips"]]) for row in rows]


def _summary(obj, operation, animation):
    from .action_context import _readback
    tracks = [[track.name[:64], [[strip.name[:64], round(strip.frame_start, 3), round(strip.frame_end, 3)]
                                 for strip in track.strips[:16]]] for track in animation.nla_tracks[:16]]
    return _readback(obj, operation, tracks=tracks, track_count=len(animation.nla_tracks))
