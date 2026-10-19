# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Commit steps of the animation kinds, called by ``program_commit`` inside its journaled transaction.

* ``transfer_object`` runs after the object's ``animation`` transfer (which
  gives the live object its staged Action copy and slot): an ``ASSIGN`` intent
  gives it the live Action instead, after checking that Action is unchanged
  since staging; when the object's own data shared the replaced Action (a
  camera keyed on both, Blender's default since 5.0) the data follows to the
  committed copy, keeping its slot, so the pair is not silently split; and an
  isolated shared Action is reported. ``nla_stack`` rebuilds the live NLA from
  the staged one (``anim_nla``), strips pointing at live Actions.
* ``replaced_actions`` pairs each replaced live Action with the committed copy
  that stands for it (``anim_stage`` sources), for ``_adopt_names``: a new
  Action (``NEW``) or an assigned one never takes the replaced one's name.
* ``apply_renames`` carries out ``RENAME`` intents after names are adopted.

Every live write is journaled: plain settings in the commit's settings journal,
whole-stack rebuilds as undo callables (``rollback``), run before the
commit restores each object's own journal.
"""

from .program_jobs import ProgramError
from .anim_channels import SHARED, ASSIGNED
from .anim_stage import find_action, action_digest


def _owned(stage):
    return {block.as_pointer() for block in stage.owned_ids + list(stage.context.created_ids)}


def _live_reference(stage, uid):
    """The live Action a staged reference stands for, refused when it changed after staging."""
    live = find_action(uid)
    if live is None:
        raise ProgramError("An Action the program references is no longer in the file; read the scene again", code='STALE_DEPENDENCY')
    digest = stage.extras.get("referenced_actions", {}).get(str(uid))
    if digest is not None and action_digest(live) != digest:
        raise ProgramError("An Action the program references changed after staging; the program was discarded",
                           code='STALE_DEPENDENCY')
    return live


def transfer_object(stage, handle, original, staged, resources, settings_journal, undo_journal, isolated):
    """The animation intents of one written object (see the module doc)."""
    extras = stage.extras
    if "animation" in resources and original.animation_data is not None:
        animation = original.animation_data
        assigned = extras.get(ASSIGNED, {}).get(handle)
        if assigned is not None:
            slot = staged.animation_data.action_slot if staged.animation_data else None
            animation.action = _live_reference(stage, assigned)
            if slot is not None:
                animation.action_slot = next(item for item in animation.action.slots if item.identifier == slot.identifier)
        _follow_own_data(stage, handle, original, resources, settings_journal)
        _report_isolation(stage, handle, original, isolated)
    if "nla_stack" in resources:
        rebuild_nla(stage, handle, original, staged, undo_journal)


def _follow_own_data(stage, handle, original, resources, journal):
    live = stage.extras.get("live_actions", {}).get(handle, {})
    data = original.data
    committed = original.animation_data.action
    if ("data" in resources or data is None or live.get("object") is None or live.get("object") != live.get("data")
            or committed is None or stage.extras.get("action_sources", {}).get(committed.name) != live["object"]):
        return
    animation = data.animation_data
    if animation is None or animation.action is None or int(animation.action.session_uid) != live["object"]:
        return
    handle_before = animation.action_slot_handle
    journal.append((animation, "action_slot_handle", handle_before))
    journal.append((animation, "action", animation.action))
    journal.append((animation, "action_slot_handle", handle_before))  # Restored last: after the Action.
    animation.action = committed
    animation.action_slot_handle = handle_before
    if animation.action != committed or animation.action_slot_handle != handle_before:
        raise ProgramError("The object data could not keep sharing the committed Action", code='INTERNAL_ERROR')


def _report_isolation(stage, handle, original, isolated):
    from .scene_read_values import label
    extras = stage.extras
    explicit = extras.get("isolated_actions", {}).get(handle)
    others = extras.get(SHARED, {}).get(handle, {}).get("object", 0)
    live_uid = extras.get("live_actions", {}).get(handle, {}).get("object")
    committed = original.animation_data.action
    if explicit is not None:
        isolated.append({"name": label(explicit["name"]), "action_users_before": explicit["other_users"] + 1,
                         "policy": "action_set SINGLE_USER: this object received its own copy of the Action"})
    elif others and live_uid is not None and committed is not None and int(committed.session_uid) != live_uid:
        isolated.append({"name": label(committed.name), "action_users_before": others + 1,
                         "policy": "Only this object received the edited Action; its other users keep the original"})


def rebuild_nla(stage, handle, original, staged, undo_journal):
    """Replace the live NLA stack with the staged one; strips play the live Actions their copies stand for."""
    from .anim_nla import snapshot, restore, comparable
    sources = stage.extras.get("strip_sources", {}).get(handle, {})
    animation = original.animation_data_create()
    previous = snapshot(animation)
    rows = snapshot(staged.animation_data)
    mapped = {}

    def action_of(row):
        uid = sources.get(row["name"])
        if uid is None:
            return row["action"]  # Made by the program (a push-down or a stash): the staged Action itself.
        if uid not in mapped:
            mapped[uid] = _live_reference(stage, uid)
        return mapped[uid]

    expected = [dict(row, strips=[dict(strip, action=action_of(strip)) for strip in row["strips"]]) for row in rows]
    undo_journal.append(lambda: restore(animation, previous))
    restore(animation, expected)

    def key(action):
        return int(action.session_uid) if action is not None else None
    actual, wanted = comparable(snapshot(animation), key), comparable(expected, key)
    if actual != wanted:
        detail = next(([a, w] for a, w in zip(actual, wanted) if a != w), [len(actual), len(wanted)])
        raise ProgramError("The committed NLA stack does not match its staged result: " + str(detail)[:300], code='INTERNAL_ERROR')


def replaced_actions(stage, journals):
    """(replaced live Action, committed copy standing for it) pairs for ``_adopt_names``."""
    sources, owned, pairs = stage.extras.get("action_sources", {}), _owned(stage), []
    for obj, journal in journals:
        old, animation = journal["action"], obj.animation_data
        if old is None or animation is None:
            continue
        candidates = [animation.action] + [strip.action for track in animation.nla_tracks for strip in track.strips]
        copy = next((item for item in candidates if item is not None and item.as_pointer() in owned
                     and sources.get(item.name) == int(old.session_uid)), None)
        if copy is not None:
            pairs.append((old, copy))
    return pairs


def apply_renames(stage, journal):
    """``action_set`` RENAME: the Action each object uses after commit takes its new name, for every user."""
    for handle, name in stage.extras.get("action_renames", {}).items():
        animation = stage.originals[handle].animation_data
        action = animation.action if animation is not None else None
        if action is None:
            raise ProgramError("action_set RENAME: the object has no Action at commit", code='STALE_DEPENDENCY')
        if action.name == name:
            continue
        journal.append((action, "name", action.name))
        action.name = name
        if action.name != name:
            raise ProgramError("action_set NAME_TAKEN: another Action took the name before commit", code='STALE_DEPENDENCY')


def rollback(undo_journal, errors):
    """Run the whole-stack undo callables in reverse; collect failures for the commit's report."""
    for undo in reversed(undo_journal):
        try:
            undo()
        except Exception as error:  # Reported as a partial rollback by the caller.
            errors.append(str(error))


def committed_names(stage):
    """Staged Action name -> the name commit gives it (renames, assigned live Actions, adopted originals)."""
    names = {}
    sources = stage.extras.get("action_sources", {})
    for handle, staged in stage.objects.items():
        animation = staged.animation_data
        action = animation.action if animation is not None else None
        if action is None:
            continue
        uid = stage.extras.get(ASSIGNED, {}).get(handle) or sources.get(action.name)
        live = find_action(uid) if uid is not None else None
        isolated = handle in stage.extras.get("isolated_actions", {})  # A SINGLE_USER copy keeps its own name.
        # A clone commit never transfers (no animation change) leaves the live object its live Action, shared or not.
        kept = not stage.changes.get(handle, set()) & {"animation", "nla", "nla_stack"}
        if live is not None and not isolated and (handle in stage.extras.get(ASSIGNED, {}) or kept
                                                  or not stage.extras.get(SHARED, {}).get(handle, {}).get("object")):
            names[action.name] = live.name
        if handle in stage.extras.get("action_renames", {}):
            names[action.name] = stage.extras["action_renames"][handle]
    return names
