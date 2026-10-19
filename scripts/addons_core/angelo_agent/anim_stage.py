# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live animation facts recorded once at staging, for the animation kernels and commit.

Staging copies every Action a written object uses (its active Action, its
data's and its NLA strips', ``program_objects._clone_action``), so kernels
never touch a live Action. What the copies cannot show is recorded here in the
stage's JSON ``extras`` while the live graph is at hand:

* ``action_shared``: per written handle, how many users the live object and
  data Actions have besides the object itself and its own data (other
  objects, NLA strips, a fake user). Kernels refuse to edit such an Action
  (``anim_channels.guard``) rather than edit it for everyone or split it
  silently; ``action_set`` ``SINGLE_USER`` clears the record explicitly.
* ``action_sources``: each staged Action copy's name -> its live Action's
  session UID, so commit knows which committed copy stands for which live
  Action (``anim_commit.replaced_actions``: only that copy takes the original's
  name when the original is left unused).
* ``strip_sources``: per handle, each NLA strip's name -> the live Action it
  plays. Strip Actions are never edited, so the NLA rebuild at commit points
  strips back at the live Actions rather than at their staged copies.
* ``live_actions``: per handle, the session UIDs of the live object and data
  Actions before the program, for commit's own-data rebinding and reports.

Names of private stage copies are unique in Main while the stage exists; a
copy renamed by a bundle reload simply misses its entry, and commit then
keeps the copy (the conservative fallback).
"""

def _uid(block):
    return int(block.session_uid) if block is not None else None


def _action(block):
    animation = getattr(block, "animation_data", None) if block is not None else None
    return getattr(animation, "action", None) if animation is not None else None


def own_users(obj):
    """The object and the IDs it owns whose animation commit transfers with it: its data and shape keys."""
    data = obj.data
    blocks = [obj] + ([data] if data is not None else [])
    keys = getattr(data, "shape_keys", None) if data is not None else None
    return blocks + ([keys] if keys is not None else [])


def other_users(obj, action):
    """Users of a live ``action`` besides ``obj`` and its own data (other IDs, NLA strips, a fake user)."""
    if action is None:
        return 0
    own = sum(1 for block in own_users(obj) if _action(block) == action)
    return max(0, int(action.users) - own)


def record(stage, scene):
    """Fill ``stage.extras`` with the live animation facts of every written object (see the module doc)."""
    extras = stage.extras
    shared, sources, strips, live = {}, {}, {}, {}
    for handle in stage.binding["writes"]:
        original, staged = stage.originals[handle], stage.objects.get(handle)
        if staged is None:
            continue
        live_object, live_data = _action(original), _action(original.data)
        shared[handle] = {"object": other_users(original, live_object), "data": other_users(original, live_data)}
        live[handle] = {"object": _uid(live_object), "data": _uid(live_data)}
        for live_action, staged_action in ((live_object, _action(staged)), (live_data, _action(staged.data))):
            if live_action is not None and staged_action is not None:
                sources[staged_action.name] = _uid(live_action)
        strips[handle] = {}
        live_animation, staged_animation = original.animation_data, staged.animation_data
        if live_animation is None or staged_animation is None:
            continue
        for live_track, staged_track in zip(live_animation.nla_tracks, staged_animation.nla_tracks):
            for live_strip, staged_strip in zip(live_track.strips, staged_track.strips):
                if live_strip.action is not None:
                    strips[handle][staged_strip.name] = _uid(live_strip.action)
                    if staged_strip.action is not None:
                        sources[staged_strip.action.name] = _uid(live_strip.action)
    extras.update(action_shared=shared, action_sources=sources, strip_sources=strips, live_actions=live)
    kinds = {operation["kind"] for operation in stage.binding["program"]["operations"]}
    if "marker_edit" in kinds:
        from .anim_markers import stage_markers
        stage_markers(stage, scene)


def find_action(uid):
    """The live Action with this session UID, or None."""
    import bpy
    return next((action for action in bpy.data.actions if int(action.session_uid) == uid), None)


def action_digest(action):
    """Exact digest of an Action's complete RNA, without its name and custom properties (copies compare equal)."""
    import hashlib
    import json
    from .program_dependencies import _rna_values
    values = _rna_values(action)
    for key in ("name", "name_full", "custom_properties"):
        values.pop(key, None)
    return hashlib.blake2b(json.dumps(values, sort_keys=True, separators=(",", ":")).encode(), digest_size=32).hexdigest()
