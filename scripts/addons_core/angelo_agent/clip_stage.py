# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the clip export plays its clips: a stage of copies, so the person's scene is never changed
(doc/angelo/rigging.rst, "Clips"; the 2.1.0 clip export break-it review, S1-S6, and its re-read, S-A to S-C).

``stage(armature, meshes, clips)`` makes a copy of the armature (the same armature data) and of each skinned mesh
(its own mesh data and shape keys, bound to the copied armature), under the originals' names: the originals are
renamed aside meanwhile, so the files carry the person's object and mesh names. The copies keep no action or NLA
track of the person's, but keep their drivers, whose variables are pointed at the copies (a corrective shape key
driven by an elbow bends with the staged elbow). The person's actions, NLA tracks, slots, drivers and selection are
never touched: there is nothing of theirs to put back but the names and the scene's frame range and current frame.
Each of those is put back on its own, whatever another step raised, and the copies are removed. Data linked from
another file or a library override cannot be renamed or staged, so it is refused by name before anything starts
(``editable_or_refuse``). Two objects sharing one mesh get a mesh each on the stage (and in the file).

On the stage, every clip (``clip_set.clips``) is one NLA track of the copied armature named after it, holding a strip
of the clip's action with the clip's slot, frames, trim, scale, repeat, blends and animated influence and time. A
scene's frame range cannot start below 0, so a clip is placed from a non-negative whole frame (its origin: the first
whole frame at or after its start, 0 for one that starts below it); a mesh whose shape keys have a track named like
the clip gets that strip on a track of the same name, moved with it. ``solo(stage, clip)`` lets one clip play alone
(the other tracks muted) over its own frames: the scene's frame range is the clip's while it plays, so an exporter
that bakes the scene range bakes exactly the clip, every whole frame inside it.
"""

import contextlib
import math
import sys

import bpy

from . import clip_set
from .rig_edit import RigError

ASIDE = "⁣angelo-aside"   # A suffix no person types: the originals' names while the copies hold theirs.
STRIP_FIELDS = ("action_frame_start", "action_frame_end", "scale", "repeat", "use_reverse", "blend_type",
                "use_sync_length", "use_auto_blend", "blend_in", "blend_out")
ANIMATED = (("use_animated_influence", "influence"), ("use_animated_time", "strip_time"))


class Stage:
    def __init__(self, armature, meshes):
        self.armature, self.meshes = armature, meshes   # The copies.
        self.tracks = {}   # Clip name -> [(animation data, track)] on the stage.
        self.spans = {}    # Clip name -> (origin, end) on the stage.


def editable_or_refuse(objects):
    """Linked or overridden data cannot be renamed aside or copied beside itself: refused by name, up front (S-C)."""
    blocks = [block for obj in objects for block in ((obj, obj.data) if obj.type == 'MESH' else (obj,))]
    held = [b.name for b in blocks if b.library is not None or b.override_library is not None]
    if held:
        raise RigError("not_local", *dict.fromkeys(held))


def visible_or_refuse(objects):
    """A hidden or excluded object would be left out of the file while the report said it was written: refused by
    name instead (S4)."""
    layer = bpy.context.view_layer
    hidden = [o.name for o in objects if o.name not in layer.objects or not o.visible_get(view_layer=layer)]
    if hidden:
        raise RigError("hidden", *hidden)


def origin(clip):
    """The whole frame a clip starts at on the stage: never below 0."""
    return float(max(0, math.ceil(clip.start)))


def _place(track, name, action, slot, at, source_strip):
    """A strip of ``action`` on ``track`` from frame ``at``, playing what ``source_strip`` plays (or the whole action)."""
    strip = track.strips.new(name, int(math.floor(at)), action)
    if source_strip is None:
        strip.action_frame_start, strip.action_frame_end = clip_set.action_range(action)
    else:
        for field in STRIP_FIELDS:
            setattr(strip, field, getattr(source_strip, field))
    if strip.frame_start != at:
        strip.frame_start_ui = at   # Moves the strip, its length kept.
    strip.extrapolation = 'NOTHING'
    strip.action_slot = slot   # The clip's own slot: nothing is added to the person's action.
    return strip


def _controls(strip, source, shift):
    """The source strip's animated influence and time, moved with the clip."""
    for flag, path in ANIMATED:
        if not getattr(source, flag):
            continue
        setattr(strip, flag, True)
        theirs, ours = source.fcurves.find(path), strip.fcurves.find(path)
        if theirs is None or ours is None:
            continue
        while len(ours.keyframe_points):
            ours.keyframe_points.remove(ours.keyframe_points[0])
        for key in theirs.keyframe_points:
            ours.keyframe_points.insert(key.co[0] + shift, key.co[1])


def _drivers_only(ad):
    """A copy's animation data without the person's action or tracks; its drivers stay."""
    if ad is None:
        return
    ad.action = None
    for track in list(ad.nla_tracks):
        ad.nla_tracks.remove(track)


def _retarget(ad, mapping):
    """Point a copy's driver variables at the copies instead of the originals."""
    if ad is None:
        return
    for curve in ad.drivers:
        for variable in curve.driver.variables:
            for target in variable.targets:
                new = next((n for old, n in mapping if target.id is not None and target.id == old), None)
                if new is not None:
                    target.id = new


def _remove_copies(objects, meshes):
    for obj in objects:
        bpy.data.objects.remove(obj)
    for mesh in meshes:   # A copied mesh's shape keys go with it.
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _restore_names(names):
    for thing, name in names:
        thing.name = name


def _restore_frames(scene, frames):
    scene.frame_start, scene.frame_end = frames[0], frames[1]
    scene.frame_set(frames[2], subframe=frames[3])


def _restore(*steps):
    """Each step runs whatever another raised. The first failure is raised once all have run, unless the export is
    already failing (then it is printed, and the export's own error is the one reported)."""
    failures = []
    for step, *args in steps:
        try:
            step(*args)
        except Exception as error:   # noqa: BLE001 - every step runs; the first failure is raised below.
            failures.append(error)
    if failures and sys.exc_info()[1] is None:
        raise failures[0]
    for error in failures:
        print("Angelo: putting the scene back after a clip export: %s" % error)


@contextlib.contextmanager
def stage(armature, meshes, clips):
    scene = bpy.context.scene
    frames = (scene.frame_start, scene.frame_end, scene.frame_current, scene.frame_subframe)
    names = [(armature, armature.name)] + [(m, m.name) for m in meshes] + [(m.data, m.data.name) for m in meshes]
    made_objects, made_meshes = [], []
    try:
        arm = armature.copy()
        made_objects.append(arm)
        _drivers_only(arm.animation_data)
        scene.collection.objects.link(arm)   # The scene's own collection: never a linked one.
        copies = []
        for mesh in meshes:
            copy = mesh.copy()
            copy.data = mesh.data.copy()
            made_objects.append(copy)
            made_meshes.append(copy.data)
            _drivers_only(copy.animation_data)
            if copy.data.shape_keys is not None:
                _drivers_only(copy.data.shape_keys.animation_data)
            for modifier in copy.modifiers:
                if modifier.type == 'ARMATURE' and modifier.object == armature:
                    modifier.object = arm
            if copy.parent == armature:
                copy.parent = arm
            scene.collection.objects.link(copy)
            copies.append(copy)
        pairs = [(armature, arm)] + list(zip(meshes, copies)) + [(m.data, c.data) for m, c in zip(meshes, copies)]
        pairs += [(m.data.shape_keys, c.data.shape_keys) for m, c in zip(meshes, copies) if m.data.shape_keys]
        for _, block in pairs:
            _retarget(block.animation_data, pairs)
        for thing, name in names:
            thing.name = name + ASIDE
        arm.name = names[0][1]   # The saved names: an aside name may have been cut at the length limit.
        for index, copy in enumerate(copies):
            copy.name = names[1 + index][1]
            copy.data.name = names[1 + len(meshes) + index][1]
        staged = Stage(arm, copies)
        ad = arm.animation_data_create()
        for clip in clips:
            at = origin(clip)
            shift = at - clip.start
            track = ad.nla_tracks.new()
            track.name = clip.name
            strip = _place(track, clip.name, clip.action, clip.slot, at, clip.strip)
            if clip.strip is not None:
                _controls(strip, clip.strip, shift)
            staged.spans[clip.name] = (at, clip.end + shift)
            staged.tracks[clip.name] = [(ad, track)]
            for mesh, key_track in clip_set.shape_key_tracks(meshes, clip):
                strips = [s for s in key_track.strips if not s.mute and s.action is not None]
                if len(strips) != 1 or strips[0].action_slot is None:
                    continue
                copy = copies[meshes.index(mesh)]
                key_ad = copy.data.shape_keys.animation_data_create()
                kt = key_ad.nla_tracks.new()
                kt.name = clip.name
                played = _place(kt, clip.name, strips[0].action, strips[0].action_slot,
                                strips[0].frame_start + shift, strips[0])
                _controls(played, strips[0], shift)
                staged.tracks[clip.name].append((key_ad, kt))
        yield staged
    finally:
        _restore((_remove_copies, made_objects, made_meshes), (_restore_names, names), (_restore_frames, scene, frames))


def _plays(mesh, playing):
    """A staged mesh whose shape keys move in a clip: a track of the clip, or drivers (they play in every clip)."""
    keys = mesh.data.shape_keys
    ad = keys.animation_data if keys is not None else None
    return ad is not None and (len(ad.drivers) > 0 or any(a == ad for a, _ in playing))


@contextlib.contextmanager
def solo(staged, clip):
    """``clip`` alone on the stage, over its own frames; yields the staged meshes whose shape keys play in it."""
    scene = bpy.context.scene
    playing = staged.tracks[clip.name]
    every = {id(a): a for a, _ in sum(staged.tracks.values(), [])}.values()
    for ad in every:
        for track in ad.nla_tracks:
            track.mute = all(track != t for _, t in playing)
    start, end = staged.spans[clip.name]
    scene.frame_start = int(start)
    scene.frame_end = max(int(start), math.floor(end))   # Every whole frame inside the clip.
    scene.frame_set(int(start))
    try:
        yield [m for m in staged.meshes if _plays(m, playing)]
    finally:
        for ad in every:
            for track in ad.nla_tracks:
                track.mute = False
