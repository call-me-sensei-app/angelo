# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic settings a program writes: staging their live values, and refusing stale ones at commit.

Object and scene revisions cover the common settings; a generic RNA path
(``rna_rules``) may name any other. Staging records the live value of every
path a ``property_set`` or ``property_keyframe`` writes, and commit refuses,
before any mutation, a program whose recorded value no longer matches the live
one, so an edit made while a preview waited is never silently overwritten.
Scene paths outside the common list are also copied onto the private stage
scene first, so a kernel reads and writes them from the live value, and a World
written by path gets a private copy without its images (a suspended stage
must never carry a live image into its bundle).

A World other scenes (or a fake user) also use is never written in place:
its private copy keeps the whole graph and commit gives only the bound scene
that edited copy, as ``world_configure`` does (``WORLD_SHARING``); the others
keep the original. A World that gained a user after staging refuses the
commit before any mutation.
"""

from .program_jobs import ProgramError

_KINDS = ("property_set", "property_keyframe")
# stage.extras: {"uid": the live World's session UID, "isolated": whether commit assigns the staged copy}.
WORLD_SHARING = "world_sharing"


def written_settings(program):
    """(target, path) of every setting the program's operations write, in order."""
    return [(operation["target"], path) for operation in program["operations"] if operation["kind"] in _KINDS
            for path in operation["parameters"]["values"]]


def _live_owner(backend, stage, target):
    scene = stage.binding.get("scene")
    if scene is not None and target == scene["handle"]:
        return backend.scene
    return stage.originals.get(target)  # None for an object the program creates.


def capture(backend, stage):
    """Record the live value of each written setting (JSON) in ``stage.extras``."""
    from .action_properties import PropertyPathError
    records = []
    for target, path in written_settings(stage.binding["program"]):
        owner = _live_owner(backend, stage, target)
        if owner is None:
            continue
        try:
            records.append([target, path, backend._live_value(owner, path)])
        except (PropertyPathError, AttributeError, KeyError):
            records.append([target, path, None])  # Missing live: the kernel reports it.
    if records:
        stage.extras["preimages"] = records


def check(backend, stage):
    """Raise before any mutation when a recorded live value changed since staging."""
    from .action_properties import PropertyPathError, same_value
    for target, path, recorded in stage.extras.get("preimages", ()):
        owner = _live_owner(backend, stage, target)
        if owner is None:
            continue
        try:
            current = backend._live_value(owner, path)
        except (PropertyPathError, AttributeError, KeyError):
            current = None
        if not (current == recorded or (recorded is not None and same_value(recorded, current))):
            raise ProgramError("STALE_SETTING: %s changed since the program was staged; observe it again"
                               % path[:96], code='STALE_SETTING',
                               details={"handles": [target], "setting_changes": [
                                   {"handle": target, "path": path, "before": recorded, "after": current}]})
    sharing = stage.extras.get(WORLD_SHARING)
    if sharing is not None and not sharing["isolated"]:
        world = backend.scene.world
        others, who = _world_users(backend, stage, world) if world is not None else (0, "")
        if others:  # Staged to be written in place: another scene would see the edit.
            scene = stage.binding["scene"]["handle"]
            raise ProgramError("STALE_SETTING: the World gained other users since the program was staged (%s); "
                               "observe again" % who[:96], code='STALE_SETTING', details={"handles": [scene]})


def _world_users(backend, stage, world):
    """(count, names) of the World's users beside the bound scene and the stage (a fake user counts)."""
    from .node_edit_commit import other_users
    owned = {block.as_pointer() for block in list(stage.owned_ids) + list(getattr(stage.context, "created_ids", ()) or ())}
    return other_users(world, [backend.scene], owned)


def stage_scene_paths(backend, stage):
    """Copy written non-common scene paths onto the stage scene; give a World written by path a private copy."""
    from .action_properties import PROPERTY_PATHS, PropertyPathError, SCENE, data_family, holder_and_name, owner_of
    from .rna_rules import resolve
    scene = stage.binding.get("scene")
    if scene is None:
        return
    paths = [path for target, path in written_settings(stage.binding["program"])
             if target == scene["handle"] and owner_of(path) == SCENE]
    kinds = {operation["kind"] for operation in stage.binding["program"]["operations"]}
    if any(data_family(path) == "world" for path in paths) and "world_configure" not in kinds:
        _private_world(backend, stage)
    for path in paths:
        if path in PROPERTY_PATHS or data_family(path) == "world":
            continue  # Common paths were copied; the World copy carries its own settings.
        try:
            resolve(backend.scene, path)  # A path the rules refuse is never written, not even its own value here.
            live_holder, name = holder_and_name(backend.scene, path)
            staged_holder, _ = holder_and_name(stage.scene, path)
            if live_holder.bl_rna.properties[name].type != "POINTER":
                setattr(staged_holder, name, getattr(live_holder, name))
        except (PropertyPathError, AttributeError, KeyError, TypeError, ValueError):
            continue  # The kernel reports a missing or refused path with its own code.


def _private_world(backend, stage):
    world = backend.scene.world
    if world is None:
        return
    if world.library is not None or getattr(world, "override_library", None) is not None:
        raise ProgramError("A linked World cannot be edited natively", code='UNSUPPORTED')
    others, _who = _world_users(backend, stage, world)
    if others:
        # Other scenes keep the original: commit gives the bound scene this edited copy, whole graph included.
        from .node_edit_commit import private_world
        private = private_world(backend, stage, world)
    else:
        from .stage_names import hide
        private = hide(stage.extras, backend._track(stage, world.copy()), world.name)
        if private.node_tree is not None:
            backend._track(stage, private.node_tree)
            # Settings only: this copy is never committed, so its graph (images,
            # node groups) is dropped rather than carried into the private bundle.
            private.node_tree.nodes.clear()
    stage.extras[WORLD_SHARING] = {"uid": int(world.session_uid), "isolated": bool(others)}
    stage.scene.world = private


def isolated_world(stage):
    """Whether commit gives the bound scene the staged World copy because the live one is shared."""
    return bool((stage.extras.get(WORLD_SHARING) or {}).get("isolated"))


def assign_isolated_world(scene, stage, journal, isolated, record_transfer=None):
    """At commit: give the bound ``scene`` the edited copy of its shared World; return (live, copy).

    The copy already holds every written ``world.`` setting; the scenes that
    share the original keep it. Journaled for rollback; the live node groups
    and images return to the copy's graph (``node_edit_commit.finish``).
    """
    from .node_edit_commit import _owned, finish
    from .scene_read_values import label
    live, staged = scene.world, stage.scene.world
    if live is None or staged is None or int(live.session_uid) != stage.extras[WORLD_SHARING]["uid"]:
        raise ProgramError("STALE_SETTING: the scene's World changed before commit; observe again", code='STALE_SETTING')
    isolated.append({"name": label(live.name), "data_users_before": live.users})
    journal.append((scene, "world", live))
    scene.world = staged
    if record_transfer is not None:
        record_transfer(staged, scene.world, "world_assigned", stage.binding["scene"]["handle"])
    if staged.node_tree is not None:
        finish(staged.node_tree, stage, _owned(stage), record_transfer=record_transfer)
    return live, staged
