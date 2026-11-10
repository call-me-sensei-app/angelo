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
    libraries = {item.library for item in kept if item.library is not None}   # The person's own libraries stay.
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
    packed, detached, unsafe = paths.blender_paths(bpy, local, private)
    if unsafe:
        raise RefusalError("A worker's part holds a packed file Angelo can't write again without the worker's own "
                           "bytes (in %s); nothing was merged" % ", ".join(unsafe[:6]), code="UNSUPPORTED")
    cleared = worker_merge_strings.clean_strings(bpy, local, private)
    left = worker_merge_verify.leaks(bpy, local, private)   # Read as written, by another rule (fail closed).
    if left:
        raise RefusalError("A worker's part keeps a path into Angelo's own folders that can't be cleared (in %s); "
                           "nothing was merged" % ", ".join(left[:6]), code="UNSUPPORTED")
    kinds = {}
    for kind, item in _added(bpy, before):
        if item in kept and kind not in ("objects", "collections"):
            kinds[kind] = kinds.get(kind, 0) + 1
    return {"kinds": kinds, "scene_users": scene_users, "packed": packed, "detached": detached,
            "cleared": sorted(set(cleared) - set(packed) - set(detached))}


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


def _make_local(bpy, kept, private):
    """Every kept datablock linked from a file in a private folder made local, users before what they use: Blender
    makes one local only once a local datablock uses it (one used only by linked data is indirect, and skipped), and
    copies one that linked data still uses, which would leave the original linked (BREAKIT-C7A H1). One order for
    all, from one map of who uses whom (BREAKIT-C7A-2 M3: rounds were quadratic on a chain); a cycle's members, and
    any still linked after it, are taken as they come. Whether any was made local."""
    linked = [item for item in kept if _private_library(bpy, item, private)]
    if not linked:
        return False
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
    for item in order:
        if item.library is not None:
            local = item.make_local()
            if local is not None and local != item:
                kept.add(local)   # A copy: the original is made local in its turn, or stays only if unused.
    return True


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
