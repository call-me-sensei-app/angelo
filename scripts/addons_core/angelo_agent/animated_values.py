# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stored values an animation replaces, measured with Blender's own evaluator rather than predicted.

A channel an Action, NLA strip or driver animates holds a stored value only
until Blender evaluates that animation again. Two facts follow, and commit
states both (``camera_guards``):

* a program that writes a frame-update setting (``program_frames.FRAME_UPDATE_PATHS``)
  makes Blender evaluate the scene's animation at its frame, committed or
  refused (the rollback restores the frame through the same setting): every
  unkeyed edit of an animated channel (a stored value its animation does not
  give, found before any write by evaluating the animation of every animated ID
  and writing the stored values back) that the update replaced is named
  (``unkeyed_values_replaced``) instead of claiming nothing changed; values
  that merely follow their keys to the new frame are not;
* a program that does not, but writes a channel an animation holds (a
  transform, a modifier's or constraint's setting, a re-created modifier whose
  keys Blender kept), commits a value that stands only until the frame changes:
  each such channel is named with the value its animation gives
  (``unkeyed_writes``; visibility, not a refusal).

Both are measured. ``Channels`` reads the stored value of every channel an
animation of the given IDs can write (``bone_state.animated_paths``, every slot,
strip and driver: a superset), each ID whole or not at all: one past
``MAX_CHANNELS``, or with NLA strips deeper than they are read, is left out and
the statement says it is partial (``UNMEASURED`` when nothing could be).
Otherwise an evaluation that replaced the stored values of an ID it had listed
only in part lost the rest.

For the first fact, before any write, ``at_frame`` runs the update a frame
change runs at the scene's own frame (a time-source update: no edit tag, so
physics caches and bakes stay as they are), reads every listed channel and
writes the stored values back; it runs only when every animated ID is listed.
Tagging each animated ID as edited instead
(``update_tag(refresh={'TIME'})``) outdated the unbaked physics caches of
every animated object. For the second fact, ``evaluate`` asks
Blender to evaluate just the IDs the program wrote (that tag and a view-layer
update, which applies NLA tracks, solo, ``use_nla``, slots, influence and empty
curves exactly as a frame change does), reads the result and writes the
committed values back: those IDs are already edited by the program, as a
re-evaluation of their animation treats them. A hand-written model of those
rules named channels Blender leaves alone and missed ones it replaces.
"""

import ast

from .dependency_paths import path_steps

MAX_CHANNELS = 4096
MAX_LISTED = 32
_TOLERANCE = 1e-5
WRITE_FACT = ("These values are written without a key on channels an animation holds: each stays only until the "
              "frame changes, when its keys or driver replace it with the value under next")
FRAME_FACT = ("Blender's frame update after this program replaced these unkeyed edits of animated channels (stored "
              "values their animation did not give) with what the animation gives (value: before, now: after), as "
              "any frame change does")
UNMEASURED = ("Not measured: the scene animates more channels than a commit reads (%d), or NLA strips deeper than it "
              "reads them. Any unkeyed edit of an animated channel stands only until its animation is evaluated "
              "again (a frame change, or an edit of that animation)" % MAX_CHANNELS)


def _key(value):
    pointer = getattr(value, "as_pointer", None)
    return pointer() if callable(pointer) else id(value)


def _read(owner, path):
    """``(is an array, values)`` of a channel now, or None when its path does not resolve."""
    try:
        value = owner.path_resolve(path)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, ReferenceError):
        return None
    if isinstance(value, (bool, int, float, str)):
        return False, (value,)
    try:
        return True, tuple(value)
    except TypeError:
        return None


def _write(owner, path, read):
    """Write a value ``_read`` gave back to its channel."""
    array, values = read
    text = path_steps(path)[-1][0]
    parent = path[:len(path) - len(text)].rstrip(".")
    holder = owner.path_resolve(parent) if parent else owner
    if text.startswith("["):
        key = ast.literal_eval(text[1:-1])
        holder[key] = values if array else values[0]
    else:
        setattr(holder, text.lstrip("."), values if array else values[0])


def _differs(first, second):
    """The indices at which two reads differ (all of them when one does not resolve)."""
    if first is None or second is None:
        return [] if first == second else list(range(len((first or second)[1])))
    found = []
    for index, (a, b) in enumerate(zip(first[1], second[1])):
        if isinstance(a, float) or isinstance(b, float):
            if abs(a - b) > _TOLERANCE * max(1.0, abs(a), abs(b)):
                found.append(index)
        elif a != b:
            found.append(index)
    return found


class Channels:
    """Every channel an animation of ``owners`` can write, with its stored value now (``values``); each owner whole
    or, past ``MAX_CHANNELS`` or with NLA strips past their depth bound, not at all (``skipped``, ``partial``)."""

    def __init__(self, owners, read=True):
        from .bone_state import animated_paths
        self.entries, self.skipped, seen = [], set(), set()
        for owner in owners:
            if owner is None or _key(owner) in seen:
                continue
            seen.add(_key(owner))
            paths, controls = animated_paths(owner), strip_controls(owner)
            paths = paths | controls if controls is not None else {"*"}
            if "*" in paths or len(self.entries) + len(paths) > MAX_CHANNELS:
                self.skipped.add(_key(owner))  # Never evaluated: what it holds unlisted would be lost.
                continue
            self.entries.extend((owner, path) for path in sorted(paths))
        self.partial = bool(self.skipped)
        self.original_entries = self.entries  # What ``values`` was read on; ``rebind`` gives ``entries`` a new list.
        self.values = self.read() if read else None  # ``read=False``: the caller reads only what it needs.

    def read(self):
        return [_read(owner, path) for owner, path in self.entries]

    def rebind(self, replaced):
        """Read the IDs commit gave in place of listed ones (``replaced``: ``(old, new[, ...])`` pairs: object data,
        shape keys, Actions, Worlds, trees) from now on: the live object shows the new one."""
        swaps = {_key(pair[0]): pair[1] for pair in replaced
                 if pair[0] is not None and pair[1] is not None and pair[0] != pair[1]}
        self.entries = [(swaps.get(_key(owner), owner), path) for owner, path in self.entries]

    def changes(self, before, after, only=None):
        """``[(entry index, channel index)]`` that differ between two reads (``only``: a set of those to keep)."""
        return [(slot, index) for slot, (first, second) in enumerate(zip(before, after))
                for index in _differs(first, second) if only is None or (slot, index) in only]

    def stated(self, pairs, before, after, fact, extra=None):
        """The result's statement of ``pairs``: ``{"fact", "channels", "count", "truncated"[, "partial"]}``."""
        channels = []
        for slot, index in pairs[:MAX_LISTED]:
            owner, path = self.entries[slot]
            entry = {"id": str(owner.name)[:64], "path": path[:96], "index": index,
                     "value": _shown(before[slot], index), "now" if extra is None else "next": _shown(after[slot], index)}
            if extra is not None:
                entry.update(extra(owner, path, index))
            channels.append(entry)
        result = {"fact": fact, "channels": channels, "count": len(pairs), "truncated": len(pairs) > MAX_LISTED}
        if self.partial:
            result["partial"] = True  # Some channels could not be listed (MAX_CHANNELS, or NLA past its bound).
        return result


def strip_controls(owner):
    """The NLA strips' own animated settings an evaluation writes (each curve of ``strip.fcurves``: influence,
    strip time), meta strips' inner ones included; None past eight levels. ``animated_paths`` lists the strips'
    Actions only; without these an evaluation replaced an unkeyed strip influence it could not write back."""
    from .bone_state import quoted
    animation, paths = getattr(owner, "animation_data", None), set()

    def walk(strips, prefix, depth):
        for strip in strips:
            here = "%s.strips[%s]" % (prefix, quoted(strip.name))
            paths.update("%s.%s" % (here, curve.data_path) for curve in getattr(strip, "fcurves", ()) or ())
            inner = list(getattr(strip, "strips", ()) or ())
            if inner and (depth >= 8 or not walk(inner, here, depth + 1)):
                return False
        return True
    for track in getattr(animation, "nla_tracks", ()) or ():
        if not walk(track.strips, "animation_data.nla_tracks[%s]" % quoted(track.name), 0):
            return None
    return paths


def unmeasured():
    """The statement of a fact that could not be measured (``UNMEASURED``)."""
    return {"fact": UNMEASURED, "channels": [], "count": 0, "truncated": False, "partial": True}


def _shown(read, index):
    if read is None or index >= len(read[1]):
        return None
    value = read[1][index]
    return round(value, 6) if isinstance(value, float) else value


def animated_ids(depsgraph):
    """The IDs ``depsgraph`` evaluates that an Action (directly or in an NLA strip) or a driver animates."""
    try:
        import bpy
        actions = set(bpy.data.actions)
        users = bpy.data.user_map(subset=actions) if actions else {}
    except (ImportError, AttributeError):
        return []
    found, seen = [], set()
    candidates = [owner for owners in users.values() for owner in owners]
    candidates += [evaluated.original for evaluated in depsgraph.ids]  # Drivers without an Action too.
    for owner in candidates:
        animation = getattr(owner, "animation_data", None)
        if (animation is None or _key(owner) in seen or _key(owner.evaluated_get(depsgraph)) == _key(owner)
                or not (animation.action is not None or len(animation.nla_tracks) or len(animation.drivers))):
            continue
        seen.add(_key(owner))
        found.append(owner)
    return found


def _restore(channels, before, after):
    """Write each stored value an evaluation replaced back (a listed channel; animatable means writable)."""
    for slot, (first, second) in enumerate(zip(before, after)):
        if first is not None and _differs(first, second):
            try:
                _write(*channels.entries[slot], first)
            except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError):
                continue


def at_frame(channels, update, view_layer):
    """What the animation of every listed ID gives at the scene's frame, by the update a frame change runs
    (``update``: a time-source update, no edit tag); the stored values are written back. Call only when
    ``channels`` lists every animated ID whole (it is not ``partial``). Returns the read."""
    before = channels.read()
    update()
    after = channels.read()
    _restore(channels, before, after)
    view_layer.update()
    return after


def evaluate(channels, slots, view_layer):
    """Blender's evaluation of the animation of the IDs owning ``slots`` (each listed whole: ``Channels``) at the
    scene's time, read; the stored values are written back afterwards. For IDs the program wrote: the tag is an
    edit's, as the write already is. Returns the read."""
    owners = {_key(channels.entries[slot][0]): channels.entries[slot][0] for slot in slots}
    before = channels.read()
    for owner in owners.values():
        owner.update_tag(refresh={"TIME"})
    view_layer.update()
    after = channels.read()
    _restore(channels, before, after)
    view_layer.update()
    return after


def held_by(owner, path, index):
    """What holds a channel: its driver, or its keys (an Action or NLA strip)."""
    animation = getattr(owner, "animation_data", None)
    driven = animation is not None and any(curve.data_path == path and curve.array_index == index
                                           for curve in animation.drivers)
    return {"held_by": "driver" if driven else "keys"}


def note(report):
    """The sentence a refusal adds for the stored values a frame update replaced (empty for none)."""
    if not report or not report["channels"]:
        return ""
    shown = ", ".join("%r %s[%d] %s -> %s" % (item["id"], item["path"], item["index"], item["value"], item["now"])
                      for item in report["channels"][:8])
    more = report["count"] - min(8, len(report["channels"]))
    return (" Blender's frame update replaced unkeyed edits of animated channels with what their animation gives, as "
            "any frame change does: " + shown + (" and %d more" % more if more > 0 else "") + ".")
