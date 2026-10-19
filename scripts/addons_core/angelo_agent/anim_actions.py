# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``action_set``: which Action and slot a staged object uses, and references to existing Actions.

Existing Actions are named by the ``dat:<epoch>:ACTION:<uid>`` ID an object
read shows (``scene_queries``), never by name. A referenced Action is read,
never written: the stage plays a private copy of it (``staged_reference``)
and records its live session UID and exact digest, and commit gives the live
object the live Action back after checking it did not change meanwhile
(``anim_commit``).

Ownership rules, all refusals rather than silent changes:

* the previous Action must stay used by something: an Action this object was
  the last user of is refused unless ``stash_previous`` keeps it in a muted
  ``[Action Stash]`` NLA track at the bottom of the stack, as Blender does;
* ``SINGLE_USER`` is the explicit isolation of a shared Action: this object
  gets its own copy (named with Blender's suffix) and the others keep theirs;
  commit reports it in ``isolated_shared_data``;
* a new slot edits the Action, so it is refused on a shared one; ``RENAME``
  renames the Action the object uses at commit, for every user, as renaming
  an ID does.
"""

from .anim_channels import AnimationError, SHARED, ASSIGNED, guard
from .anim_structure_spec import ACTION_ID_PATTERN

STASH_TRACK = "[Action Stash]"


def resolve_action_id(action_id):
    """The live, local Action an ``dat:<epoch>:ACTION:<uid>`` ID names in the current document."""
    import re
    import bpy
    from . import scene_queries
    if re.fullmatch(ACTION_ID_PATTERN, action_id) is None:
        raise AnimationError("ACTION_ID", "name an Action by the dat:...:ACTION:... ID an object read shows")
    _prefix, epoch, _kind, uid = action_id.split(":")
    if epoch != scene_queries.document_epoch():
        raise AnimationError("ACTION_EXPIRED", "the Action ID is from an earlier document epoch; read the object again")
    action = next((item for item in bpy.data.actions if int(item.session_uid) == int(uid)), None)
    if action is None:
        raise AnimationError("ACTION_NOT_FOUND", "no Action has this ID")
    if action.library is not None or getattr(action, "override_library", None) is not None:
        raise AnimationError("ACTION_LINKED", "linked Actions are not assigned natively")
    return action


def staged_reference(context, live):
    """A private copy of a live Action for the stage to play, with its source and digest recorded."""
    from .anim_stage import action_digest
    if live in context.owned_ids:
        raise AnimationError("ACTION_NOT_FOUND", "the Action belongs to the private stage", refusal_code="VALIDATION")
    from .stage_names import hide
    copy = hide(context.extras, context.track_id(live.copy()), live.name)
    context.extras.setdefault("action_sources", {})[copy.name] = int(live.session_uid)
    context.extras.setdefault("referenced_actions", {})[str(int(live.session_uid))] = action_digest(live)
    return copy


def slot_named(action, text):
    """The slot of ``action`` whose identifier, or else unique display name, is ``text``."""
    slots = [slot for slot in action.slots if slot.identifier == text]
    slots = slots or [slot for slot in action.slots if slot.name_display == text]
    if len(slots) != 1:
        names = ", ".join(repr(slot.identifier) for slot in action.slots[:16])
        raise AnimationError("SLOT_NOT_FOUND" if not slots else "SLOT_AMBIGUOUS",
                             "the Action has no single slot %r; its slots: %s" % (text[:66], names or "none"))
    if slots[0].target_id_type not in {"OBJECT", "UNSPECIFIED"}:
        raise AnimationError("SLOT_TYPE", "slot %r animates a %s, not an object" % (text[:66], slots[0].target_id_type))
    return slots[0]


def auto_slot(action, live_name, last_identifier):
    """The slot Blender would give the live object: its last slot, else one named after it, else a lone slot.

    Blender picks by the ID's name, and staged copies carry private names, so
    kernels choose explicitly with the live object's name (commit sets the same
    slot by identifier).
    """
    for identifier in (last_identifier, "OB" + live_name):
        slot = next((item for item in action.slots if item.identifier == identifier), None)
        if slot is not None and slot.target_id_type in {"OBJECT", "UNSPECIFIED"}:
            return slot
    lone = [item for item in action.slots if item.target_id_type in {"OBJECT", "UNSPECIFIED"}]
    return lone[0] if len(action.slots) == 1 and lone else None


def _live_uid(context, action):
    return context.extras.get("action_sources", {}).get(action.name) if action is not None else None


def _orphaned(context, target, action):
    """Whether replacing ``action`` would leave the live Action it stands for without users."""
    uid = _live_uid(context, action)
    if uid is None or context.extras.get(SHARED, {}).get(target, {}).get("object", 0):
        return False
    live = context.extras.get("live_actions", {}).get(target, {})
    strips = context.extras.get("strip_sources", {}).get(target, {}).values()
    return uid == live.get("object") and uid != live.get("data") and uid not in strips


def _replace(context, obj, target, params, animation):
    """Before the active Action changes: stash or refuse an Action that would be left without users."""
    previous = animation.action
    if params.get("stash_previous"):
        if previous is None:
            raise AnimationError("NOTHING_TO_STASH", "the object has no active Action to stash")
        from .anim_nla import admit, move_track
        admit(animation)
        track = animation.nla_tracks.new()
        track.name, track.mute, track.select = STASH_TRACK, True, False
        from .anim_stage import find_action
        live = find_action(_live_uid(context, previous)) if _live_uid(context, previous) is not None else None
        from .stage_names import public  # A staged copy's private name never names a strip.
        strip = track.strips.new(public(context.extras, (live or previous).name)[:64], int(previous.frame_range[0]),
                                 previous)
        strip.select = False
        if animation.action_slot is not None:
            strip.action_slot = animation.action_slot
        if live is not None and context.extras.get(SHARED, {}).get(target, {}).get("object", 0):
            # A shared Action is unedited here (edits are refused): the stash plays the live one, not a copy.
            context.extras.setdefault("strip_sources", {}).setdefault(target, {})[strip.name] = int(live.session_uid)
        move_track(animation, track, 0)
        return
    if _orphaned(context, target, previous):
        from .anim_stage import find_action
        live = find_action(_live_uid(context, previous))
        raise AnimationError("ACTION_ORPHANED", "nothing else uses the current Action %r, so it would be lost on save; "
                             "set stash_previous to keep it in the NLA" % (live.name[:64] if live else ""))


def apply(context, obj, operation):
    """action_set on a staged object."""
    import bpy
    from .action_context import _readback
    params, target = operation["parameters"], operation["target"]
    mode = params["mode"]
    animation = obj.animation_data
    if animation is not None and (len(animation.drivers) or animation.use_tweak_mode):
        raise AnimationError("DRIVEN" if len(animation.drivers) else "TWEAK_MODE",
                             "animation with drivers or in NLA tweak mode needs reviewed Python")
    if mode != "NEW" and mode != "ASSIGN" and (animation is None or animation.action is None) and mode != "SLOT":
        raise AnimationError("NO_ACTION", "the object has no active Action")
    shared = context.extras.setdefault(SHARED, {}).setdefault(target, {"object": 0, "data": 0})
    if mode == "NEW":
        animation = animation or obj.animation_data_create()
        live_name = context.extras.get("live_names", {}).get(target, obj.name)
        name = params["name"] or live_name + "Action"
        if bpy.data.actions.get(name) is not None:
            raise AnimationError("NAME_TAKEN", "an Action is already named %r" % name[:64])
        _replace(context, obj, target, params, animation)
        action = context.track_id(bpy.data.actions.new(name))
        animation.action = action
        animation.action_slot = action.slots.new(id_type="OBJECT", name=live_name[:62])
        shared["object"] = 0
        context.extras.get(ASSIGNED, {}).pop(target, None)
    elif mode == "ASSIGN":
        animation = animation or obj.animation_data_create()
        live = resolve_action_id(params["action"])
        if _live_uid(context, animation.action) == int(live.session_uid):
            raise AnimationError("ALREADY_ASSIGNED", "the object already uses this Action")
        _replace(context, obj, target, params, animation)
        copy = staged_reference(context, live)
        last = animation.last_slot_identifier
        animation.action = copy
        animation.action_slot = (slot_named(copy, params["slot"]) if params["slot"] is not None else
                                 auto_slot(copy, context.extras.get("live_names", {}).get(target, obj.name), last))
        context.extras.setdefault(ASSIGNED, {})[target] = int(live.session_uid)
        shared["object"] = int(live.users)  # Every live user is another user: kernels must not edit it here.
    elif mode == "SINGLE_USER":
        if not shared.get("object") and target not in context.extras.get(ASSIGNED, {}):
            raise AnimationError("ACTION_NOT_SHARED", "the Action has no other users; it is already this object's own")
        from .anim_stage import find_action
        live = find_action(_live_uid(context, animation.action)) if _live_uid(context, animation.action) else None
        isolated = context.extras.setdefault("isolated_actions", {})
        isolated[target] = {"name": (live or animation.action).name, "other_users": shared.get("object", 0)}
        shared["object"] = 0
        context.extras.get(ASSIGNED, {}).pop(target, None)
    elif mode == "UNLINK":
        _replace(context, obj, target, params, animation)
        animation.action = None
        shared["object"] = 0
        context.extras.get(ASSIGNED, {}).pop(target, None)
    elif mode == "RENAME":
        taken = bpy.data.actions.get(params["name"])
        if taken is not None and int(taken.session_uid) != _live_uid(context, animation.action) and taken != animation.action:
            raise AnimationError("NAME_TAKEN", "another Action is already named %r" % params["name"][:64])
        context.extras.setdefault("action_renames", {})[target] = params["name"]
    else:
        if animation is None or animation.action is None:
            raise AnimationError("NO_ACTION", "the object has no active Action")
        if params["new_slot"] is not None:
            guard(context, target, "object")
            context.require_private(animation.action)
            animation.action_slot = animation.action.slots.new(id_type="OBJECT", name=params["new_slot"])
        else:
            if target not in context.extras.get(ASSIGNED, {}):  # An assigned Action's slot is set live at commit.
                guard(context, target, "object")
            animation.action_slot = slot_named(animation.action, params["slot"]) if params["slot"] is not None else None
    animation = obj.animation_data
    action = animation.action if animation is not None else None
    slot = animation.action_slot if animation is not None else None
    return _readback(obj, operation, action=action.name[:64] if action is not None else None,
                     slot=slot.identifier if slot is not None else None,
                     tracks=len(animation.nla_tracks) if animation is not None else 0)
