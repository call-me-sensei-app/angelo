# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What one animation clip is, for every format the clip export writes (doc/angelo/rigging.rst, "Clips"; the 2.1.0
clip export break-it review, M1 and S1-S6).

A **clip** is one action as the armature plays it: the active action (with the armature's slot), or the one unmuted
strip of an unmuted NLA track (with the strip's own slot, trim, scale and repeat). Muted strips and tracks are not
clips; a track with several unmuted strips is a sequence and is refused by name (bake or split it first). Each clip
has a unique name: its action's, and the track's beside it when two clips share an action (two tracks playing one
walk). Its frames are what it plays: the strip's start to end, or the action's own range (its manual frame range when
set), subframes included and below frame 0 too (the stage plays it from a non-negative whole frame). Both formats play clips from the same definition (``clip_stage``). Nothing adds a slot to the person's action:
a clip without a slot for the armature is refused by name.

**Shape keys**: a skinned mesh whose shape keys have an unmuted NLA track named like a clip plays that track's strip
in that clip (``shape_key_tracks``).
"""

from .rig_edit import RigError


class Clip:
    __slots__ = ("name", "action", "slot", "track", "strip", "start", "end")

    def __init__(self, name, action, slot, track, strip, start, end):
        self.name, self.action, self.slot, self.track, self.strip = name, action, slot, track, strip
        self.start, self.end = float(start), float(end)   # Subframes kept: the stage places them (clip_stage).

    @property
    def frames(self):
        return self.end - self.start + 1


def action_range(action):
    start, end = action.frame_range   # The manual frame range when the action has one, else its keys' range.
    return start, end


def clips(armature):
    """The armature's clips, in a fixed order: the active action, then each track from the bottom."""
    ad = armature.animation_data
    if ad is None:
        return []
    found, multi = [], []
    if ad.action is not None:
        start, end = action_range(ad.action)
        found.append(Clip(ad.action.name, ad.action, ad.action_slot, None, None, start, end))
    for track in ad.nla_tracks:
        if track.mute:
            continue
        strips = [s for s in track.strips if not s.mute and s.action is not None]
        if len(strips) > 1:
            multi.append(track.name)
            continue
        if strips:
            strip = strips[0]
            found.append(Clip(strip.action.name, strip.action, strip.action_slot, track, strip, strip.frame_start,
                              strip.frame_end))
    if multi:
        raise RigError("multi_strip_track", *multi)
    names = [clip.name for clip in found]
    for clip in found:
        if names.count(clip.action.name) > 1 and clip.track is not None:
            clip.name = "%s %s" % (clip.action.name, clip.track.name)
    unslotted = [clip.name for clip in found if clip.slot is None]
    if unslotted:
        raise RigError("no_slot", *unslotted)
    return found


def shape_key_tracks(meshes, clip):
    """(mesh, track) for each mesh whose shape keys have a track named like ``clip``."""
    out = []
    for mesh in meshes:
        keys = getattr(mesh.data, "shape_keys", None)
        ad = keys.animation_data if keys is not None else None
        if ad is not None:
            out += [(mesh, t) for t in ad.nla_tracks if t.name in (clip.name, clip.action.name) and not t.mute]
    return out

