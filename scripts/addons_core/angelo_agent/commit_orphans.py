# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live data a commit's own swaps left without users, freed once no rollback can need it.

A commit gives a written object an edited copy of its data, Action, World, node group, material or particle
settings instead of editing the live block in place, and keeps the replaced block for its own rollback
(``program_commit``: a failure gives each object ``journal["data"]`` back). Blender edits such data in place, so
after a successful commit the replaced block is data only Angelo's swap left without users. It stayed in the session
until the file was saved and reopened: 500 typed edits that swap data left 300 meshes and 100 materials and
140 MB more memory (doc/angelo/agent_execution.rst, "Data a commit replaced").

``free_replaced`` runs in a successful commit after its last check, its rollback scope and the stage cleanup, so no
rollback can reach the replaced blocks any more. It frees only what the commit itself replaced, with what Blender's
deletion takes with it (a mesh's Key, embedded data): a replaced block that is not the block it was replaced with,
is local and plain (no library, fake user, asset mark, override or embedding), has no users and that no other ID
points at (a driver variable's target counts as no user). Passes repeat while a replaced block's last user was
another freed replaced block (a replaced material only the replaced mesh used). What the replaced blocks merely used
(a data-linked material the swap released, a node group taken out of a replaced graph) stays, without users and
unlisted, exactly as after the same edit in place (Blender lists nothing either).

* **Production** (a native commit scope, ``program_undo.commit``): the commit scope protects Main, so the public
  removal refuses; the native right ``delete_replaced_data`` frees them, with the authority ``seal`` recorded
  before the first write of a commit that swaps (what the written Objects and Scene held then) and the filters
  checked natively by the deletion proof, every surviving reference included. Each refusal comes back with its
  reason, and so does a pass limit or anything freed outside the proof.
* **Without a native scope** (fixtures that commit through ``native.commit_job``): ``bpy.data.batch_remove``, the
  same rules, never while a preview renders.
* **Never while a render, bake or other job evaluates the scene** on its own thread (``program_context._jobs_run``).

Replaced blocks nothing uses that stay are counted and listed (``orphan_data``: ``not_freed`` and
``not_freed_reasons``), and every reason is logged. Data a deleted object released stays, as Blender's own delete leaves it.

Undo: the commit runs inside one Undo step whose state before the commit was saved first (memfile), so Undo reads
each freed block back from that copy, and the step written after the commit no longer holds it. A block read back
that way gets a new address (Blender re-reads an ID it still holds at its old one), so the scene observations of
the document before the Undo (``scene_queries``: handles, revisions, identities by address) must expire then:
``history_changed`` does it after every Undo and Redo in every session that runs programs (``native_programs``
registers it), so no observation ever depends on an ID keeping its address across Undo.
"""

from .program_dependencies import _bpy

MAX_PASSES = 16   # A replaced block whose last user was another replaced block: real chains are two deep.
# The families a commit swaps for an edited copy (the native right's ``replaceable_family``; a Key goes with its mesh).
REPLACEABLE = frozenset({"MESH", "CURVE", "META", "LATTICE", "CAMERA", "LIGHT", "ARMATURE", "GREASEPENCIL",
                         "GREASEPENCIL_V3", "CURVES", "POINTCLOUD", "VOLUME", "SPEAKER", "LIGHT_PROBE", "MATERIAL",
                         "NODETREE", "ACTION", "PARTICLE", "WORLD"})
# Staged changes whose commit swaps a live block for an edited copy (``program_commit``: data, node trees, Actions,
# particle settings, materials; a scene's World): only such a commit seals, so one that swaps nothing pays nothing.
SWAPPING = frozenset({"mesh", "armature", "camera", "light", "curve", "data", "node_trees", "materials", "animation",
                      "nla", "nla_stack", "particles", "modifiers", "world", "world_nodes"})


def _plain(old, new=None):
    """Whether ``old`` (replaced by ``new``) is local, plain data of a family a commit swaps."""
    if old is None or old == new or old.use_fake_user or old.is_embedded_data:
        return False
    if old.library is not None or getattr(old, "override_library", None) is not None:
        return False
    return getattr(old, "asset_data", None) is None and old.id_type in REPLACEABLE   # A mesh's keys go with the mesh.


def _freeable(old, new=None):
    """Whether ``old`` is plain replaced data nothing uses now."""
    return _plain(old, new) and old.users == 0


def _pointed_at(block, referrers):
    """Whether an ID other than the block's own shape keys points at it (a driver target, a custom link)."""
    keys = getattr(block, "shape_keys", None)
    return any(referrer != block and (keys is None or referrer != keys) for referrer in referrers)


def swaps(stage):
    """Whether committing ``stage`` swaps any live block for an edited copy (``SWAPPING``)."""
    return any(SWAPPING.intersection(resources) for resources in stage.changes.values())


def seal(scope, stage, scene):
    """Before the first write of a scoped commit that swaps: the native owner records what the written Objects and
    the written Scene hold, its only authority to free replaced data. Returns whether it sealed."""
    if scope is None or not swaps(stage):
        return False
    holders = [stage.originals[handle] for handle in stage.binding.get("writes", ()) if handle in stage.originals]
    bound = stage.binding.get("scene")
    if bound is not None and bound.get("write"):
        holders.append(scene)
    try:
        return scope.seal_replaceable(holders) if holders else False
    except (RuntimeError, ValueError, TypeError) as error:   # Optional: the commit goes on, and frees nothing.
        print("Angelo commit: the replaceable data was not sealed (%s)" % str(error)[:200])
        return False


def free_replaced(replaced, scope=None, max_passes=MAX_PASSES):
    """Free the replaced blocks of ``replaced`` (``[(old, new, ...)]``) that nothing uses, and nothing else.

    Returns ``(remaining, kept)``: the pairs whose old block still exists (safe to read for the result), and
    ``{"count", "reasons", "unexpected"}`` when a replaced block nothing uses stays or anything went wrong (empty
    otherwise). Every reason is also logged. Never fails the commit."""
    pairs = [_identity(pair[0]) for pair in replaced]   # Every identity is read before anything is freed.
    roots, root_ids, reasons, unexpected, unreadable = [], [], [], 0, 0
    try:
        for pair in replaced:
            try:
                if _plain(pair[0], pair[1]) and pair[0] not in roots:
                    roots.append(pair[0])
                elif (pair[0] is not None and pair[0] != pair[1] and pair[0].users == 0 and pair[0].library is None
                        and getattr(pair[0], "asset_data", None) is not None):
                    roots.append(pair[0])   # A replaced asset is never freed, only counted.
                    reasons.append("an asset-marked block is never freed")
            except ReferenceError:
                unreadable += 1   # A wrapper already freed elsewhere: skipped, never read again.
        if unreadable:
            reasons.append("%d replaced blocks could no longer be read" % unreadable)
        root_ids = [_identity(block) for block in roots]
        plain = [block for block in roots if getattr(block, "asset_data", None) is None]
        # Only a block nothing uses now can go: a later pass frees only after a first one did (exact, so a commit
        # whose replaced blocks are all still used never reaches the native right).
        if any(block.users == 0 for block in plain):
            if _scene_busy():
                reasons.append("a render, bake or other job was evaluating the scene")
            elif scope is not None:
                unexpected = _free_native(scope, plain, reasons, max_passes)
            else:
                _free_public([_identity(block) for block in plain], reasons, max_passes)
    except Exception as error:  # noqa: BLE001  Only memory is at stake: what stays is reported, never silent.
        reasons.append("%s: %s" % (type(error).__name__, str(error)[:200]))
    remaining = [pair for pair, identity in zip(replaced, pairs) if identity is None or _alive(identity)]
    left = [block for block in map(_alive, root_ids) if block is not None and block.users == 0]
    reasons = sorted(set(reasons))
    if reasons:   # Always said, in the log and, when something stays, in the result.
        print("Angelo commit: freeing replaced data: %s" % "; ".join(reasons)[:500])
    kept = {}
    if left or unexpected or reasons:
        kept = {"count": len(left), "reasons": reasons[:6], "unexpected": unexpected}
    return remaining, kept


def _scene_busy():
    """A render, bake or other job evaluates the document's scene on its own thread (``program_context._jobs_run``,
    which fails closed): freeing data it may read would race it."""
    from .program_context import _jobs_run
    return _jobs_run()


def _free_native(scope, roots, reasons, max_passes=MAX_PASSES):
    """The native right (production). Returns how many IDs the kernel freed outside its proof (reported)."""
    if not scope.frees_replaced_data():
        reasons.append("this Angelo binary has no native right to free replaced data")
        return 0
    receipt = scope.delete_replaced(roots, max_passes)
    reasons.extend(reason for _row, reason in receipt["refused"])
    if receipt["unfinished"]:
        reasons.append("freeing stopped after %s passes" % receipt["passes"])
    if receipt["error"] or not receipt["success"]:
        reasons.append(receipt["error"] or "the native right did not complete")
    return receipt["unexpected"]


def _free_public(pending, reasons, max_passes=MAX_PASSES):
    """Without a native scope: ``batch_remove``, roots only, in passes (a root freed once another root, its last user,
    went), never while a preview renders, never what another ID points at."""
    bpy = _bpy()
    if bpy.app.is_job_running("RENDER_PREVIEW"):
        reasons.append("a preview render was running")
        return
    for _ in range(max_passes):
        doomed = [block for block in map(_alive, pending) if block is not None and _freeable(block)]
        if doomed:
            referrers = bpy.data.user_map(subset=doomed)
            doomed = [block for block in doomed if not _pointed_at(block, referrers.get(block, ()))]
        if not doomed:
            return
        try:
            bpy.data.batch_remove(ids=doomed)
        except (RuntimeError, ReferenceError, TypeError) as error:
            reasons.append(str(error)[:200])
            return
    reasons.append("freeing stopped after %d passes" % max_passes)


def _collections():
    """``{RNA type name: bpy.data collection name}`` (``Mesh``: ``meshes``), read once."""
    global _COLLECTIONS
    if _COLLECTIONS is None:
        properties = _bpy().data.bl_rna.properties
        _COLLECTIONS = {prop.fixed_type.identifier: prop.identifier for prop in properties if prop.type == "COLLECTION"}
    return _COLLECTIONS


_COLLECTIONS = None


def _identity(block):
    """``(collection, name, address, session uid)`` of ``block``, None for no block, ``()`` for one already freed."""
    if block is None:
        return None
    try:
        kind = block.bl_rna
        while kind is not None and kind.identifier not in _collections():
            kind = kind.base
        if kind is None:
            return ()
        return _collections()[kind.identifier], block.name, int(block.as_pointer()), int(block.session_uid)
    except ReferenceError:
        return ()


def _alive(identity):
    """The block ``identity`` names when it is still in the file (one lookup by name, not a walk), else None."""
    if not identity:
        return None
    collection, name, pointer, uid = identity
    block = getattr(_bpy().data, collection).get(name)   # A freed local name: no block, or another one.
    if block is None or block.library is not None or int(block.as_pointer()) != pointer or block.session_uid != uid:
        return None
    return block


def history_changed(*_args):
    """After Undo or Redo: the observed document is another one (``scene_queries.invalidate``)."""
    from . import scene_queries
    scene_queries.invalidate()
