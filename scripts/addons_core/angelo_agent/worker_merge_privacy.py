# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a merge's append brings besides the part, taken out at once: nothing of the worker's own reaches the person's
file (break-it E2E-20 F-L-2, privacy; its review BREAKIT-C7A).

``worker_merge._append`` appends a part's objects, materials and collections from its worker's file, and Blender
appends every datablock they reference, recording where each came from. Left as it is, the person's file then holds:

* the worker's Scene, whole, when the part references it (a driver's Scene target, an ID property): its master
  collection (the merged objects then live in two scenes), its world, view layers, line styles, tool settings' brushes
  and palettes, its render output folder. Every user of an appended Scene is remapped to the lead's own scene (a
  driver reading the worker's frame reads the person's) and the Scene goes, with every datablock only it brought;
* the Library of the part file, emptied by the append but kept, with the part's absolute path (the person's or the
  builder's home folder), and the Library of any file the worker linked from one of Angelo's private folders, with
  what it links: made local (all of it, to a fixed point, since Blender makes a datablock local only once a local one
  uses it) and the Library removed. The person's own libraries stay linked;
* on every reusable datablock (meshes, materials, images, worlds, node groups, actions...) a weak library reference
  naming the part's path, which Blender **writes into every save**, every Undo step and autosave: cleared;
* paths into Angelo's private folders, wherever a kept datablock holds one (``worker_merge_paths``): cut to their
  file name, each buffer written whole, and a worker's file kept only as written again from what Blender decoded,
  never its own bytes (``worker_merge_media``).

What is still there after that refuses the merge (``RefusalError``, so ``worker_merge.merge`` removes everything it
added): never a file with a private path. ``scrub`` runs right after each part's append, before any copy of it is
made, inside the merge's one Undo step and its rollback, so no state with any of it is ever saved, pushed to Undo or
autosaved.
"""

from . import worker_merge_paths as paths
from . import worker_merge_strings, worker_merge_verify

NEVER_PART = ("Scene", "Library", "WindowManager", "Screen", "WorkSpace")   # A worker's document, never its part.
LISTED = 24   # Names per list in the answer.


def scrub(scene, before, roots, private):
    """After a part's append: ``scene`` is the lead's, ``before`` every datablock's pointer before the append
    (``worker_material_check.ids``), ``roots`` the datablocks the append asked for, ``private`` the merge's
    ``worker_merge_paths.PrivateFolders``. Returns what the answer reports: ``{"kinds": {kind: count added},
    "scene_users": [...], "packed": [...], "detached": [...], "cleared": [...]}``."""
    import bpy
    from .refusal_codes import RefusalError
    added = _added(bpy, before)
    scenes = [item for _kind, item in added if paths.type_name(item) == "Scene"]
    users = bpy.data.user_map(subset=scenes) if scenes else {}
    scene_users = sorted({user.name for found in users.values() for user in found
                          if paths.type_name(user) not in NEVER_PART})
    for appended in scenes:
        appended.user_remap(scene)
    roots = [item for item in roots if item is not None]
    kept = _reached(bpy, roots, [item for _kind, item in added])
    if _make_local(bpy, kept, private):   # Its copies are new datablocks: what the part keeps, again.
        added = _added(bpy, before)
        kept = _reached(bpy, roots, [item for _kind, item in added])
    still = sorted(item.name for item in kept if _private_library(bpy, item, private))
    if still:
        raise RefusalError("A worker's part links %s from one of Angelo's own files, which can't be made part of this "
                           "file; nothing was merged" % ", ".join(still[:8]), code="UNSUPPORTED")
    # Also packed data only the person's own linked data uses (an Essentials node group their asset uses): Blender
    # 5.3 crashed on Redo after an Undo that took such data back (a plain append or link did too; BREAKIT-C7A-13 M1).
    # Angelo 2.0.0 fixes that crash (test_blender_redo_packed_linked_tracking keeps it fixed) and still refuses such
    # data here, as defense in depth; lifting this refusal is on the 2.1.0 backlog.
    still = sorted(item.name for item in kept if _linked_packed(item))
    if still:
        raise RefusalError("A worker's part holds %s, packed data from another file that only the person's own linked "
                           "data uses, which this version of Angelo doesn't merge; nothing was merged"
                           % ", ".join(still[:8]),
                           code="UNSUPPORTED")
    libraries = {item.library for item in kept if item.library is not None}   # The person's own libraries stay.
    # What a save writes for them besides: each kept linked datablock's name (its placeholder), and a Library this
    # append added: never cleaned (a name links it), only checked (BREAKIT-C7A-14 H1).
    also = [item for item in kept if item.library is not None] + [item for _kind, item in added if item in libraries]
    gone = [item for _kind, item in added if item not in kept and item not in libraries]
    if gone:
        bpy.data.batch_remove(gone)
    local = [item for item in kept if item.library is None]
    for item in local:
        reference = item.library_weak_reference
        if reference is not None:   # Read only by a later append's reuse, which never matches an empty path.
            paths.overwrite(reference, "filepath", "")
        # Its name written whole, as it is: a name that once was a path keeps that path after its terminator, which
        # every save writes and the check's copies cannot show (BREAKIT-C7A-3 H1). A name Blender keeps (its own
        # font's) refuses before anything is written: it is the file's own, never one a script set.
        try:
            paths.overwrite(item, "name", item.name)
        except AttributeError:
            pass
    packed, detached, unsafe, rewritten = paths.blender_paths(bpy, local, private)
    if unsafe:
        raise RefusalError("A worker's part holds a packed file Angelo can't write again without the worker's own "
                           "bytes (in %s); nothing was merged" % ", ".join(unsafe[:6]), code="UNSUPPORTED")
    cleared = worker_merge_strings.clean_strings(bpy, local, private)
    kinds = {}
    for kind, item in _added(bpy, before):
        if item in kept and kind not in ("objects", "collections"):
            kinds[kind] = kinds.get(kind, 0) + 1
    return {"kinds": kinds, "scene_users": scene_users, "packed": packed, "detached": detached,
            "cleared": sorted(set(cleared) - set(packed) - set(detached)), "check": (local, private, rewritten, also)}


def verify(reports, before=None):
    """The merge's check, once all its parts are scrubbed (``reports``: each part's ``scrub``): their kept datablocks
    read as a save writes them, by another rule than the cleaning's (``worker_merge_verify``, fail closed): a private
    path, packed bytes the merge did not write again itself, or a file that cannot be checked refuse the merge.
    ``before`` (``worker_material_check.ids``, taken before the first append): every local datablock the merge has
    added since is read too, not only those its parts brought: its own collection for each part, a mirrored
    collection and an object's copy for each further placement. A save writes them all, and the check writes only
    what it is given (``worker_merge_verify.saved_copy``)."""
    import bpy
    from .refusal_codes import RefusalError
    from .worker_material_check import added_kinds
    local = [item for report in reports for item in report["check"][0]]
    written = [item for report in reports for item in report["check"][2]]
    also = [item for report in reports for item in report["check"][3]]
    if before is not None:
        given = {item.as_pointer() for item in local + also}
        local += [item for kind in added_kinds(bpy) if kind != "libraries" for item in getattr(bpy.data, kind)
                  if item.library is None and item.as_pointer() not in before and item.as_pointer() not in given]
    try:
        left, foreign = (worker_merge_verify.check(bpy, local, reports[0]["check"][1], written, also) if reports
                         else ([], []))
    except worker_merge_verify.Unchecked as reason:
        raise RefusalError("Angelo couldn't check this merge for its own files: %s; nothing was merged" % reason,
                           code="UNSUPPORTED") from None
    if foreign:
        raise RefusalError("A worker's part holds a packed file Angelo can't write again without the worker's own "
                           "bytes (in %s); nothing was merged" % ", ".join(foreign[:6]), code="UNSUPPORTED")
    if left:
        raise RefusalError("A worker's part keeps a path into Angelo's own folders that can't be cleared (in %s); "
                           "nothing was merged" % ", ".join(left[:6]), code="UNSUPPORTED")


def _added(bpy, before):
    from .worker_material_check import added_kinds
    return [(kind, item) for kind in added_kinds(bpy) for item in getattr(bpy.data, kind)
            if item.as_pointer() not in before]


def _reached(bpy, roots, added):
    """The appended datablocks the part itself uses: what ``roots`` reach, never through a worker's document."""
    uses = {}
    for used, users in bpy.data.user_map(subset=added).items():
        for user in users:
            uses.setdefault(user, []).append(used)
    kept, stack = set(), list(roots)
    while stack:
        item = stack.pop()
        if item in kept or paths.type_name(item) in NEVER_PART:
            continue
        kept.add(item)
        stack.extend(uses.get(item, ()))
    return kept & set(added)


def _private_library(bpy, item, private):
    library = item.library
    return library is not None and private.holds(bpy.path.abspath(library.filepath, library=library.parent))


def _linked_packed(item):
    """Whether ``item`` is linked data packed in the file (Blender 5's packed linked data): written whole by every
    save, its own packed files and properties too, from any library (BREAKIT-C7A-11 H1)."""
    return item.library is not None and bool(getattr(item, "is_linked_packed", False))


def _make_local(bpy, kept, private):
    """Every kept datablock linked from a file in a private folder, or linked and packed in the file from any file
    (BREAKIT-C7A-11 H1: then cleaned and checked as local data), made local, users before what they use: Blender
    makes one local only once a local datablock uses it (one used only by linked data is indirect, and skipped), and
    copies one that linked data still uses, which would leave the original linked (BREAKIT-C7A H1). One order for
    all, from one map of who uses whom (BREAKIT-C7A-2 M3: rounds were quadratic on a chain); a cycle's members, and
    any still linked after it, are taken as they come. A copy leaves its original linked for the linked data that used
    it then; once all are made local (a cycle's members each copied, BREAKIT-C7A-12 M2), every local user is moved to
    the copy, so the original is used by linked data alone and removed with what the part does not keep. A library
    override of data made local is made plain local data first and again after (BREAKIT-C7A-12 M1): an override's
    reference must stay linked, or the file is corrupt when read. A pass moves only the users of originals a
    local datablock still uses, found in one map (BREAKIT-C7A-13 L1: each move walks the whole file, so moving every
    copy's users in every pass cost the cycle's length cubed times the file). Whether any was made local."""
    linked = [item for item in kept if _private_library(bpy, item, private) or _linked_packed(item)]
    if not linked:
        return False
    _clear_overrides(kept, set(linked))
    users = bpy.data.user_map(subset=linked)
    waiting, uses = {}, {}
    for item in linked:
        waiting[item] = {user for user in users.get(item, ()) if user in kept and user.library is not None}
        for user in waiting[item]:
            uses.setdefault(user, []).append(item)
    order, ready, placed = [], [item for item, by in waiting.items() if not by], set()
    while ready:
        item = ready.pop()
        order.append(item)
        placed.add(item)
        for used in uses.get(item, ()):   # What ``item`` uses is ready once all its linked users are.
            waiting[used].discard(item)
            if not waiting[used] and used not in placed and used not in ready:
                ready.append(used)
    order += [item for item in linked if item not in placed]
    copies = {}
    while order:   # One pass, and another for what only linked data used then (what a cycle's members use).
        for item in order:
            if item.library is not None and item not in copies:
                local = item.make_local()
                if local is not None and local != item:
                    copies[item] = local
                    kept.add(local)
        _move_local_users(bpy, copies)
        left = [item for item in order if item.library is not None and item not in copies]
        order = left if len(left) < len(order) else []
    _clear_overrides(kept, set(linked))
    return True


def _move_local_users(bpy, copies):
    """Every local user of an original Blender copied (``copies``: original to copy) moved to the copy; linked data is
    never remapped, so the original stays for it alone."""
    still = [original for original in copies if original.library is not None]
    users = bpy.data.user_map(subset=still) if still else {}
    for original in still:
        if any(user.library is None for user in users.get(original, ())):
            original.user_remap(copies[original])


def _clear_overrides(kept, made):
    """Every kept local library override whose reference is in ``made`` (to be made local, or made so) or is already
    local, made plain local data: what it overrides is then its own; and with it every other member of its hierarchy,
    so none is left an override whose root is now local data (port review NIT). A hierarchy's root goes last, so each
    other member still has a valid root when it goes."""
    overrides = [(item, item.override_library) for item in kept
                 if item.library is None and item.override_library is not None]
    roots = []
    for item, override in overrides:
        reference = override.reference
        if reference is None or reference in made or reference.library is None:
            if not any(override.hierarchy_root == root for root in roots):
                roots.append(override.hierarchy_root)
    found = [(override.hierarchy_root == item, item) for item, override in overrides
             if any(override.hierarchy_root == root for root in roots)]
    for _root, item in sorted(found, key=lambda pair: pair[0]):
        item.make_local(clear_liboverride=True)


def answer(reports):
    """The merge's ``also_added`` from each part's ``scrub``: what it added besides objects and collections, the
    objects whose Scene reference is now this scene's, files it packed or could not, and datablocks whose text named
    a private file."""
    kinds, lists = {}, {"scene_users": [], "packed": [], "detached": [], "cleared": []}
    for report in reports:
        for kind, count in report["kinds"].items():
            kinds[kind] = kinds.get(kind, 0) + count
        for key in lists:
            lists[key].extend(report[key])
    result = {"datablocks": dict(sorted(kinds.items()))}
    for key, names in lists.items():
        if names:
            result[key] = names[:LISTED]
            if len(names) > LISTED:
                result[key + "_total"] = len(names)
    return result
