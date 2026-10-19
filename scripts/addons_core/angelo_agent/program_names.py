# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object names in program order: what staging promises and commit carries out.

Staging never edits a live object, so renaming or deleting an existing object
is an intent commit carries out (``extras["renames"]``, ``extras["deleted"]``)
and its live original keeps its name in Main until then. Names nevertheless
follow Blender's own order of events, as a plain script running the same
edits would see them:

* a name is free once the operation that renamed its holder away or deleted
  it has run (delete "Camera", then ``camera_create`` "Camera" gets
  "Camera"); otherwise it came out "Camera.001" or was refused
  ``NAME_TAKEN`` (every recorded "replace the camera" trial lost
  plans to it);
* a name a pending rename will give is taken, although Main still shows it
  free (rename the camera to "Shot", then ``camera_create`` "Shot" gets
  "Shot.001", and an exact-name kind refuses ``NAME_TAKEN`` at once);
* a deleted object another scene still links is only unlinked by commit, as
  Blender's delete leaves it, so it keeps its name (``KEPT_ELSEWHERE``,
  recorded when the stage is created).

A model writes a plan in the order of the person's sentence: "add a camera
named Camera ..., then delete the old camera" creates before it deletes. So a
name every declared holder of which the program renames away or deletes
*later* may be taken at once (``plan_names``, before staging, with the live
names): the request becomes a claim and commit gives the name after the
release. Only an unambiguous release qualifies: if a holder keeps the name to
the end (renamed back, or deleted while another scene still links it) or a
second operation asks for the same name, the plan is refused before staging
with the reason (``NAME_AMBIGUOUS``) rather than suffixed and failed after it.

``program_name`` is the name Blender gives at this point of the program: the
requested one when free, else the lowest free ``.NNN`` of its base. A name
that Main holds only for a declared holder this program released becomes a
claim: the new object holds a fixed-length private placeholder in Main and
``extras[CLAIMS]`` maps it to the name commit gives it. ``committed_name``
reads it for checks, results and the delta. A claimed object with an alias is
also entered in ``extras["live_names"]`` under that alias, the map kernels
read for a staged object's artist-facing name when they name derived data (a
new Action, the slot a first key creates, separated parts, batch copies,
unnamed duplicates and conversions), so the placeholder never becomes part of
a committed name; ``name_new_slot`` does the same for a staged copy of an
existing object, whose private ``__angelo_copy_`` name Blender would otherwise
give the slot. A check may never expect one of these private names.

``apply_names`` (commit, inside the Undo operator) first moves every renamed
or removed holder of a name the program gives away to a private temporary
name, then gives each renamed object and each claim its name, journaling
every step so rollback restores them in reverse. A rename of an object the
commit removes is skipped, since it would only hold a name. A name taken
since staging fails the commit rather than being suffixed.

Pure Python; Blender objects and the Main lookup (``bpy.data.objects.get``)
are passed in.
"""

import itertools
import re
import uuid

CLAIMS = "name_claims"  # Placeholder name of a new staged object -> the name commit gives it.
FORWARD = "forward_claims"  # [reference, name]: requests plan_names lets take a name released later.
KEPT_ELSEWHERE = "kept_elsewhere"  # Deleted declared objects another scene links: commit only unlinks them.
# A new object without an alias (a separated part, an unaliased create), by the name commit gives it -> the
# collections it holds on the stage; the Scene Collection unless its kernel says otherwise (program_memberships).
NAMED = "named_memberships"
MASTER_COLLECTION = "Scene Collection"
PLACEHOLDER = "__angelo_name_"
TEMPORARY = "__angelo_rename_"
PRIVATE_PREFIXES = ("__angelo_copy_", PLACEHOLDER, TEMPORARY)  # Staged copies, claims, commit's temporary names.
_NUMBERED = re.compile(r"\.\d+$")  # The numeric suffix Blender splits off (BLI_string_split_name_number).


def private_name(name):
    """Whether ``name`` is one of the private names staging and commit use, never an artist's object."""
    return isinstance(name, str) and name.startswith(PRIVATE_PREFIXES)


def _removed(extras):
    """Handles commit removes from Main: deleted, and linked by no other scene."""
    return set(extras.get("deleted", ())) - set(extras.get(KEPT_ELSEWHERE, ()))


def _pending(extras, reference=None):
    """Names pending renames (of objects that stay) and claims hold, except ``reference``'s own rename."""
    removed = _removed(extras)
    names = {name for handle, name in extras.get("renames", {}).items() if handle != reference and handle not in removed}
    names.update(extras.get(CLAIMS, {}).values())
    return names


def released(extras, name, reference=None):
    """Whether every declared live object holding ``name`` in Main was renamed away or removed earlier."""
    if name in _pending(extras, reference):
        return False
    holders = [handle for handle, live in extras.get("live_names", {}).items()
               if live == name and not handle.startswith("$")]  # "$alias" entries are claims (_pending).
    renames, removed = extras.get("renames", {}), _removed(extras)
    return bool(holders) and all(handle != reference and (handle in removed or handle in renames) for handle in holders)


def occupied(extras, name, lookup, me=None, reference=None):
    """Whether ``name`` is taken at this point of the program, so Blender would suffix it.

    ``lookup`` finds an object of Main by name; ``me`` is the object being
    named (its own name never collides); ``reference`` is the existing object
    being renamed, whose own pending rename does not count.
    """
    if name in _pending(extras, reference):
        return True
    holder = lookup(name)
    return holder is not None and holder != me and not released(extras, name, reference)


def program_name(extras, wanted, lookup, me=None):
    """The name Blender gives ``wanted`` at this point of the program.

    ``wanted`` when it is free, else the lowest free ``.NNN`` of its base, as
    Blender's name map chooses (main_namemap.cc), with names this program
    gives or releases counted where Blender would count them.
    """
    if not occupied(extras, wanted, lookup, me):
        return wanted
    base = _NUMBERED.sub("", wanted)
    for number in itertools.count(1):
        candidate = "%s.%03d" % (base, number)
        if not occupied(extras, candidate, lookup, me):
            return candidate


def committed_name(extras, obj):
    """The name commit gives a new staged object: its claim, or the name it holds."""
    return extras.get(CLAIMS, {}).get(obj.name, obj.name)


def current_name(extras, reference, obj):
    """A staged object's name at this point of the program: a pending rename, its live name or claim, or its own."""
    return (extras.get("renames", {}).get(reference) or extras.get("live_names", {}).get(reference)
            or committed_name(extras, obj))


def _reordered(extras):
    """Whether program order can differ from Main's: only a rename, a deletion or a claim makes it differ."""
    return bool(extras.get("renames") or extras.get("deleted") or extras.get(CLAIMS) or extras.get(FORWARD))


def _take_forward(extras, reference, name):
    """Whether plan_names lets ``reference`` take ``name`` now; consumes that one decision."""
    forward = extras.get(FORWARD)
    if forward and [reference, name] in forward:
        forward.remove([reference, name])
        return True
    return False


def _claim(extras, obj, wanted, reference):
    # Fixed length: a stage check compares it as an expected name, which the check schema bounds (64).
    obj.name = PLACEHOLDER + uuid.uuid4().hex[:12]
    extras.setdefault(CLAIMS, {})[obj.name] = wanted  # Keyed by the name Blender actually gave the placeholder.
    if reference:
        extras.setdefault("live_names", {})[reference] = wanted
    return wanted


def _give(extras, obj, name, reference, lookup):
    holder = lookup(name)
    if holder is not None and holder != obj:  # Released by a declared holder, which keeps it in Main until commit.
        return _claim(extras, obj, name, reference)
    if obj.name != name:
        obj.name = name  # Free in Main too, so Blender keeps it, unless it is too long (it is truncated).
    return obj.name


def _unaliased(extras, reference, name):
    """Record a new object without an alias by the name commit gives it, in the Scene Collection by default."""
    if reference is None:
        extras.setdefault(NAMED, {}).setdefault(name, [MASTER_COLLECTION])
    return name


def name_created(context, obj, wanted, reference, lookup, requested=True):
    """The name commit gives ``obj``, which a kernel just made asking Blender for ``wanted`` (alias ``reference``).

    Without a rename, deletion or claim in the program, Blender's name in Main
    is already the program's, and nothing more is done. ``requested``: the
    operation asks for ``wanted`` by name (not a source's name it copies), so
    a plan_names decision may apply.
    """
    extras = context.extras
    if not _reordered(extras):
        return _unaliased(extras, reference, obj.name)
    if requested and _take_forward(extras, reference, wanted):
        return _unaliased(extras, reference, _claim(extras, obj, wanted, reference))  # Released later in the program.
    return _unaliased(extras, reference, _give(extras, obj, program_name(extras, wanted, lookup, obj), reference, lookup))


def rename_new_object(context, obj, wanted, reference, lookup, requested=True):
    """Rename a new staged object (alias ``reference``) as Blender would at this point of the program.

    Returns the name commit gives it; any other name than ``wanted`` is
    Blender's suffix, which an exact-name kind refuses. ``requested`` as for
    ``name_created``: an unnamed copy named after its source never is.
    """
    extras = context.extras
    claims = extras.get(CLAIMS)
    if claims and obj.name in claims:  # Renaming away from a claimed name gives the claim up.
        del claims[obj.name]
        extras.get("live_names", {}).pop(reference, None)
    if not _reordered(extras):
        if obj.name != wanted:
            obj.name = wanted
        return _unaliased(extras, reference, obj.name)
    if requested and _take_forward(extras, reference, wanted):
        return _unaliased(extras, reference, _claim(extras, obj, wanted, reference))  # Released later in the program.
    return _unaliased(extras, reference, _give(extras, obj, program_name(extras, wanted, lookup, obj), reference, lookup))


def live_rename_taken(extras, name, reference, lookup):
    """Whether renaming ``reference``'s existing object to ``name`` collides at this point of the program."""
    if _take_forward(extras, reference, name):
        return False  # Its holder releases it later in this program; commit renames after the release.
    if name == extras.get("live_names", {}).get(reference) and name not in _pending(extras, reference):
        return False  # Back to its own live name, which nothing else took meanwhile.
    return occupied(extras, name, lookup, reference=reference)


def name_new_slot(extras, obj, reference):
    """Before a kernel keys ``obj`` without an assigned slot: the slot Blender creates gets the artist-facing name.

    Blender names a new slot after the AnimData's last slot identifier, else
    the ID's name (animrig ``Action::slot_add_for_id``), and never renames it.
    A staged object's ID name is private (a staged copy or a claim), so the
    last identifier is set to what keying the live object would use.
    """
    animation = obj.animation_data
    if animation is not None and (getattr(animation, "action_slot", None) is not None or animation.last_slot_identifier):
        return
    live = extras.get("live_names", {}).get(reference)
    if live is not None:
        (animation or obj.animation_data_create()).last_slot_identifier = "OB" + live


def removable(obj, bound):
    """Whether commit removes a deleted object: every collection linking it belongs to the bound scene."""
    return all(any(collection == item for item in bound) for collection in obj.users_collection)


def kept_elsewhere(originals, deleted, bound):
    """The declared objects the program deletes that another scene still links (they keep their names)."""
    return sorted(handle for handle in deleted if handle in originals and not removable(originals[handle], bound))


def record_kept_elsewhere(extras, originals, deleted, bound):
    """At stage creation: the declared objects the program deletes that another scene still links."""
    kept = kept_elsewhere(originals, deleted, bound)
    if kept:
        extras[KEPT_ELSEWHERE] = kept


_NAMED_CREATIONS = frozenset({"create_primitive", "create_object", "camera_create", "light_create", "armature_create"})


def _request(operation):
    """``(reference, name)`` an operation asks for by name, else None: a named new object or any rename."""
    kind, params = operation["kind"], operation.get("parameters") or {}
    alias = params.get("alias")
    reference = ("$" + alias.lstrip("$")) if alias else None
    if kind == "object_rename":
        return operation["target"], params["name"]
    if (kind in _NAMED_CREATIONS or kind in {"object_duplicate", "object_convert"}) and params.get("name"):
        return reference, params["name"]
    return None


def _effects(operation, declared):
    """The declared objects an operation renames (with the new name) and deletes."""
    from .action_catalog import deleted_references
    renamed = ([(operation["target"], operation["parameters"]["name"])]
               if operation["kind"] == "object_rename" and operation["target"] in declared else [])
    return renamed, [handle for handle in deleted_references(operation) if handle in declared]


def _touched(holder, later):
    return any(holder in removed or any(target == holder for target, _ in renamed) for renamed, removed in later)


def _unreleased(holder, name, later, kept, live_names):
    """Why ``holder`` still has ``name`` at the end of the program, or None when the program releases it."""
    final, deleted = name, False
    for renamed, removed in later:
        final = next((new for target, new in renamed if target == holder), final)
        deleted = deleted or holder in removed
    label = "the existing object " + repr(live_names[holder][:64])
    if final != name or deleted and holder not in kept:
        return None
    if deleted:
        return "stays with %s after this program deletes it, because another scene still links it" % label
    if _touched(holder, later):
        return "goes back to %s later in this program" % label
    return "stays with %s, which this program neither renames nor deletes" % label


def plan_names(operations, live_names, kept=()):
    """Before staging: the requests that may take a name a declared holder releases later in the program.

    ``live_names`` maps each declared handle to its name; ``kept`` lists the
    deleted ones another scene still links. Returns ``[reference, name]``
    pairs for ``extras[FORWARD]``; raises ``NAME_AMBIGUOUS`` (VALIDATION) when
    a request's holder is released later but keeps the name after all, or
    another operation asks for the same name.
    """
    from .program_jobs import ProgramError
    declared, kept = set(live_names), set(kept)
    effects = [_effects(operation, declared) for operation in operations]
    requests = [_request(operation) for operation in operations]
    current, forward = dict(live_names), []
    for index, request in enumerate(requests):
        if request is not None:
            reference, name = request
            holders = [handle for handle, held in current.items() if held == name and handle != reference]
            later = effects[index + 1:]
            if holders and any(_touched(handle, later) for handle in holders):  # The plan means to release it.
                problems = [reason for reason in (_unreleased(handle, name, later, kept, live_names) for handle in holders)
                            if reason]
                if any(other is not None and other[1] == name and other[0] != reference for other in requests[index + 1:]):
                    problems.append("is held by the existing object %r until later in this program, and another "
                                    "operation asks for it too" % live_names[holders[0]][:64])
                if problems:
                    raise ProgramError("%s NAME_AMBIGUOUS: %r %s; choose another name"
                                       % (operations[index]["kind"], name[:64], problems[0]), code="VALIDATION")
                forward.append([reference, name])
        renamed, removed = effects[index]
        current.update(renamed)
        current.update({handle: None for handle in removed if handle not in kept})
    return forward


def _set_name(block, name, journal):
    journal.append((block, "name", block.name))
    block.name = name


def apply_names(stage, scene, new_objects, journal):
    """Give renamed live objects and claimed new objects their names, journaled; raise rather than suffix."""
    from .program_jobs import ProgramError
    extras = stage.extras
    renames, claims = extras.get("renames", {}), extras.get(CLAIMS, {})
    if not renames and not claims:
        return
    deleted = [stage.originals[handle] for handle in extras.get("deleted", ()) if handle in stage.originals]
    doomed = set()
    if deleted:
        from .program_objects import _scene_collections
        bound = [scene.collection, *_scene_collections(scene)]
        doomed = {obj.as_pointer() for obj in deleted if removable(obj, bound)}
    # A rename of an object commit removes changes nothing the artist keeps, and would only hold a name.
    targets = [(stage.originals[handle], name, True) for handle, name in renames.items()
               if stage.originals[handle].as_pointer() not in doomed]
    targets += [(obj, claims[obj.name], False) for obj in new_objects if obj.name in claims]
    final = {obj.as_pointer(): name for obj, name, _ in targets}
    wanted = set(final.values())
    for holder in [obj for obj, _, live in targets if live] + deleted:
        if holder.name not in wanted or final.get(holder.as_pointer()) == holder.name:
            continue
        if holder.as_pointer() not in final and holder.as_pointer() not in doomed:
            # Staging counted it as removed (KEPT_ELSEWHERE is recorded at stage creation).
            raise ProgramError("object_delete NAME_TAKEN: another scene links the deleted object since staging, so "
                               "it keeps the name " + repr(holder.name[:64]), code='STALE_DEPENDENCY')
        _set_name(holder, TEMPORARY + uuid.uuid4().hex[:12], journal)
    for obj, name, live in targets:
        if obj.name != name:
            _set_name(obj, name, journal)
        if obj.name != name:
            raise ProgramError(("object_rename " if live else "") + "NAME_TAKEN: the name " + repr(name[:64]) +
                               " was taken before commit", code='STALE_DEPENDENCY')
