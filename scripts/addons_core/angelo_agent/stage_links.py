# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Links between staged copies: every ID pointer a stage's own IDs hold names the stage, data no evaluation writes or
still data read in place while staged, and the ID the person keeps once committed (doc/angelo/agent_execution.rst,
"Links between staged copies"; routes: ``stage_routes``).

Staging copies a bound object with ``ID.copy()``, which keeps every pointer of the copy on the live ID it named
(apart from the copy's own self-references), and ``copy_object`` then points the structural slots (data, materials,
node groups, Actions, particle settings) at their private copies. Every other link (a mesh's texture space mesh, an
ID property of either store, a node socket, a driver target, a light linking collection) stayed on the live data:
the stage's active depsgraph built that data and wrote its evaluated animation back to the person's file before any
approval (B1), a stage that read it was refused only after that ("still aliases original data"), and a bundle took
a copy of it that commit kept with no users (audit-1010 §8: a mutual ``texture_mesh`` pair).

``close`` (``finish_stage``, before the first evaluation) relinks the stage's own IDs with ``_angelo_stage.relink``,
which visits every pointer Blender's ``foreach_ID_link`` knows: every bound object's links name its staged copy, and
within one bound object's unit (the object, its data and the data's shape keys) the data and keys their copies; every
other target takes its ``stage_routes`` route: a *reference copy* (``REFERENCE``, the live ID's session UID, its own
links closed the same way, round after round; overrides as plain local copies), ``live`` or ``borrowed`` (stays), or
a coded refusal. Links between two units name a reference copy: staging reads the other unit's data as it was, and
commit maps the link to that unit's committed copy when it took the live data's place.

``commit`` (``program_commit``, after every transfer, journaled) points the stage's links to reference copies and
reference stand-ins at the ID the person keeps: the copy that took the live ID's place when this commit left the live
ID without users (a replaced pair, as ``_adopt_names`` decides), else the live ID itself; links to staged objects at
their live objects. Rollback replays the journal backwards (``rollback``). ``verify`` refuses a commit whose kept
copies or written live objects still name stage data cleanup frees.

Before a yield, ``stand_in`` points the links that stay live at empty *yield stand-ins* (``STAND_IN``), so a detached
Main or a bundle never takes the person's data with it; ``live`` points them back after the stage is attached or
hydrated, before it is validated or evaluated. A *reference stand-in* (``substitute``, ``REFERENCE_STAND_IN``) is
never resolved by ``live``: only commit maps it back. Linked data stays as it is (a bundle writes it as a link).
"""

from .program_jobs import ProgramError
from .program_dependencies import MAX_STAGE_IDS, MAX_TOTAL_VERTICES, _bpy, _embedded_links
from . import stage_names

STAND_IN = "_angelo_stage_stand_in_uid"  # A yield stand-in: the session UID of the live ID ``live`` restores.
REFERENCE = "_angelo_stage_reference_uid"  # A reference copy: the session UID of the live ID it stands for.
REFERENCE_STAND_IN = "_angelo_stage_substitute_uid"  # A reference stand-in: only commit maps it back.
SOURCES = "reference_sources"  # stage.extras: str(session UID) -> [bpy.data collection, name] of that live ID.
PROVENANCE = "reference_provenance"  # stage.extras: str(UID) -> {"override_reference", "override_root"} UIDs.
JOURNAL = "_angelo_commit_relink_journals"  # Stage attribute: the commit's relink journals (never persisted).
MAX_ROUNDS = 32  # Reference copies of reference copies' links: real chains are a few deep, the ID budget binds first.
ALIASING = "A staged mutable material, action or node group still aliases original data"
ESCAPED = "A committed copy still links to temporary staged data"
STALE = "Data the staged edit links to is no longer in the file; read the scene again"


def _module():
    try:
        from _bpy import _angelo_stage
    except ImportError:
        return None
    return _angelo_stage if hasattr(_angelo_stage, "relink") and hasattr(_angelo_stage, "reach") else None


def self_link_flags(owner, *, strict=False):
    """Flags of natively covered owner-to-itself edges, or None without complete bounded evidence.

    This proves pointer coverage only, never an external dependency's exact contents or mutation authority.
    """
    module = _module()
    required = ("links", "relink", "unrelink", "reach", "copy_plain", "stand_in", "session_uid_last")
    if module is None or any(not callable(getattr(module, name, None)) for name in required):
        if strict:
            raise ProgramError("Native self-link coverage is unavailable", code='UNSUPPORTED')
        return None
    try:
        pointer = owner.as_pointer()
        flags = [flags for source, _holder, target, flags in module.links([owner])
                 if source.as_pointer() == pointer and target.as_pointer() == pointer]
        if not flags:
            return None
        reached, _animated = module.reach([owner], [], MAX_STAGE_IDS)
        # Older walks can append a terminal embedded ID after checking their limit.
        if reached is None or len(reached) > MAX_STAGE_IDS:
            if strict:
                raise ProgramError("Native self-link closure exceeds the ID budget", code='LIMIT_EXCEEDED')
            return None
        if any(item.as_pointer() == pointer for item in reached):
            return flags
        if strict:
            raise ProgramError("Native self-link closure omitted its owner", code='INTERNAL_ERROR')
    except ProgramError:
        if strict:
            raise
    except (AttributeError, ReferenceError, TypeError, ValueError, RuntimeError) as error:
        if strict:
            raise ProgramError("Native self-link coverage could not be read", code='INTERNAL_ERROR') from error
    return None


def available():
    """Whether this binary relinks natively (an older one refuses any stage that is not closed, as before)."""
    return _module() is not None


def relink(ids, pairs=()):
    """``_angelo_stage.relink`` over stage-owned ``ids``: (outside IDs, journal)."""
    return _module().relink(list(ids), [tuple(pair) for pair in pairs])


def session_uid_last():
    """The newest session UID given so far (IDs made later have greater ones), or None on an older binary."""
    module = _module()
    newest = getattr(module, "session_uid_last", None) if module is not None else None
    return newest() if newest is not None else None


def outside(ids):
    """What stage-owned ``ids`` name outside themselves (a relink with no pairs writes nothing)."""
    return relink(ids)[0]


def _blocks(stage):
    """Every ID the stage owns (staged copies and what kernels created), once each."""
    created = getattr(stage, "created_ids", None)
    if created is None:
        created = getattr(getattr(stage, "context", None), "created_ids", ())
    seen, result = set(), []
    for block in list(stage.owned_ids) + list(created or ()):
        if block is not None and block.as_pointer() not in seen:
            seen.add(block.as_pointer())
            result.append(block)
    return result


def _holders(blocks):
    return [block for block in blocks if block is not None and not block.is_embedded_data]


_COLLECTIONS = {}


def _collection_name(block):
    """The ``bpy.data`` collection that holds ``block`` (``Mesh``: ``meshes``), by its RNA type's declared base chain."""
    if not _COLLECTIONS:
        properties = _bpy().data.bl_rna.properties
        _COLLECTIONS.update((prop.fixed_type.identifier, prop.identifier) for prop in properties
                            if prop.type == "COLLECTION")
    kind = block.bl_rna
    while kind is not None and kind.identifier not in _COLLECTIONS:
        kind = kind.base
    return _COLLECTIONS[kind.identifier] if kind is not None else None


def _bound_pairs(stage):
    """Every bound object -> its staged copy; each unit's live data and keys -> their copies (for its own holders)."""
    objects, units = [], []
    for handle, copy in stage.objects.items():
        original = stage.originals.get(handle)
        if original is None or original == copy:
            continue
        objects.append((original, copy))
        if original.data is None or copy.data is None or original.data == copy.data:
            continue
        pairs = [(original.data, copy.data)]
        live_keys, keys = getattr(original.data, "shape_keys", None), getattr(copy.data, "shape_keys", None)
        if live_keys is not None and keys is not None and live_keys != keys:
            pairs.append((live_keys, keys))
        units.append((_holders([copy, copy.data, keys]), pairs))
    return objects, units


def close(backend, stage):
    """Relink a new stage's own IDs before its first evaluation (module docstring).

    Reference copies count toward the stage's ID budget (``_track``: LIMIT_EXCEEDED) and their meshes toward its
    geometry budget; a refused route raises before anything is evaluated.
    """
    from . import stage_routes
    if not available():
        _refuse_open(stage)
        return
    objects, units = _bound_pairs(stage)
    if objects:
        relink(_holders(_blocks(stage)), objects)
    for holders, pairs in units:
        relink(holders, pairs)
    references, pairs = {}, []
    for _ in range(MAX_ROUNDS):
        # Every round: a new reference copy's links to bound objects name their staged copies too.
        found = relink(_holders(_blocks(stage)), objects + pairs)[0]
        router = stage_routes.Router(stage)
        needed = []
        for block in found:
            route, refusal = router.route(block)
            if route == "refused":
                raise ProgramError(refusal, code='UNSUPPORTED')
            if route == "private_copy" and block.as_pointer() not in references:
                needed.append(block)
        if not needed:
            return
        if router.borrowed and any(router.reaches_borrowed(block) for block in needed):
            # A reference copy would name a borrowed material: clone every borrowed slot (as a program that borrows
            # nothing), then route again. When in doubt, copy.
            backend.unborrow(stage)
            continue
        for block in needed:
            _reference(backend, stage, block, references, pairs)
        _check_geometry(stage)
    raise ProgramError("An input dependency graph is too deep to isolate completely", code='LIMIT_EXCEEDED')


def _reference(backend, stage, live, references, pairs):
    """One private reference copy of ``live`` (a Key: of its owner, whose copy holds the copied keys)."""
    bpy = _bpy()
    if live.as_pointer() in references:
        return
    if isinstance(live, bpy.types.Key):
        owner = live.user
        if owner is not None and owner.as_pointer() not in references:
            _reference(backend, stage, owner, references, pairs)
        if live.as_pointer() not in references:   # Its owner's copy holds no copy of it.
            from .stage_routes import OTHER_REFUSAL
            from .scene_read_values import label
            raise ProgramError(OTHER_REFUSAL.format(kind="shape keys", name=label(live.name)), code='UNSUPPORTED')
        return
    made = _module().copy_plain(live)  # Local, never an override (an override copy would keep its reference).
    if made is None:
        from .stage_routes import OTHER_REFUSAL, _kind
        from .scene_read_values import label
        raise ProgramError(OTHER_REFUSAL.format(kind=_kind(live), name=label(live.name)), code='UNSUPPORTED')
    copy = backend._track(stage, made)
    stage_names.hide(stage.extras, copy, live.name)
    _mark(stage, copy, live, REFERENCE)
    references[live.as_pointer()] = copy
    pairs.append((live, copy))
    tree = getattr(copy, "node_tree", None)
    if tree is not None and tree.is_embedded_data:
        backend._track(stage, tree)
    live_keys, keys = getattr(live, "shape_keys", None), getattr(copy, "shape_keys", None)
    if live_keys is not None and keys is not None and live_keys != keys:
        backend._track(stage, keys)
        stage_names.hide(stage.extras, keys, live_keys.name)
        _mark(stage, keys, live_keys, REFERENCE)
        references[live_keys.as_pointer()] = keys
        pairs.append((live_keys, keys))


def _mark(stage, copy, live, marker):
    uid = str(int(live.session_uid))
    copy[marker] = int(live.session_uid)
    stage.extras.setdefault(SOURCES, {})[uid] = [_collection_name(live), live.name]
    override = getattr(live, "override_library", None)
    if override is not None:   # Kept for whoever needs the hierarchy (the copy itself is plain).
        reference, root = getattr(override, "reference", None), getattr(override, "hierarchy_root", None)
        stage.extras.setdefault(PROVENANCE, {})[uid] = {
            "override_reference": int(reference.session_uid) if reference is not None else None,
            "override_root": int(root.session_uid) if root is not None else None}


def substitute(backend, stage, live):
    """A reference stand-in for ``live`` (an empty ID of its type, an Empty for an object, no users, never in a
    scene): every stage link to ``live`` names it from now on; ``live`` never resolves it, commit maps it back."""
    standing = _module().stand_in(live)
    if standing is None:
        from .stage_routes import OTHER_REFUSAL, _kind
        from .scene_read_values import label
        raise ProgramError(OTHER_REFUSAL.format(kind=_kind(live), name=label(live.name)), code='UNSUPPORTED')
    backend._track(stage, standing)
    stage_names.hide(stage.extras, standing, live.name)
    _mark(stage, standing, live, REFERENCE_STAND_IN)
    relink(_holders(_blocks(stage)), [(live, standing)])
    return standing


def _check_geometry(stage):
    """Reference copies count toward the geometry budget like staged meshes: every point of every geometry type."""
    bpy = _bpy()
    total = 0
    for block in _blocks(stage):
        if isinstance(block, bpy.types.Mesh):
            total += len(block.vertices)
        elif isinstance(block, (getattr(bpy.types, "Curves", ()), getattr(bpy.types, "PointCloud", ()))):
            total += len(block.points)
    if total > MAX_TOTAL_VERTICES:
        raise ProgramError("The staged program exceeds the combined geometry budget", code='LIMIT_EXCEEDED')


def _refuse_open(stage):
    """Without native relinking (an older binary): a stage with a link that needs a copy is refused before it
    evaluates, as before; so is a scene, any collection (its closure cannot be walked) or UI data."""
    from . import stage_routes
    bpy = _bpy()
    router = stage_routes.Router(stage)
    for block in _holders(_blocks(stage)):
        for _holder, _prop, dependency in _embedded_links(block):
            if dependency is None or isinstance(dependency, bpy.types.Object):
                continue   # finish_stage maps or refuses object links.
            if isinstance(dependency, bpy.types.Collection) and dependency.as_pointer() not in router.owned:
                raise ProgramError(ALIASING, code='INTERNAL_ERROR')
            route, refusal = router.route(dependency, walk=False)
            if route == "private_copy":
                raise ProgramError(ALIASING, code='INTERNAL_ERROR')
            if route == "refused":
                raise ProgramError(refusal, code='UNSUPPORTED')


def stand_in(backend, stage):
    """Before a yield: every link of the stage's own IDs to a live local ID it does not own names a yield stand-in.

    Stand-ins count toward the stage's ID budget (``_track``: LIMIT_EXCEEDED)."""
    if not available():
        return
    from .stage_borrowing import borrows_any, resident_pointers
    blocks = _blocks(stage)
    owned = {block.as_pointer() for block in blocks}
    borrowed = resident_pointers(stage) if borrows_any(stage) else frozenset()  # Cleared by release() next.
    existing = {int(block[STAND_IN]): block for block in _holders(blocks) if STAND_IN in block.keys()}
    pairs = []
    for target in outside(_holders(blocks)):
        pointer = target.as_pointer()
        if pointer in owned or pointer in borrowed or target.library is not None or target.is_embedded_data:
            continue
        uid = int(target.session_uid)
        standing = existing.get(uid)
        if standing is None:
            standing = _module().stand_in(target)
            if standing is None:
                continue   # A type with no stand-in (a library, a workspace): kept as it is.
            backend._track(stage, standing)
            stage_names.hide(stage.extras, standing, target.name)
            _mark(stage, standing, target, STAND_IN)
            existing[uid] = standing
        pairs.append((target, standing))
    if pairs:
        relink(_holders(_blocks(stage)), pairs)


def live(stage):
    """After a stage is attached or hydrated: links to yield stand-ins name their live IDs again (STALE when gone).

    Reference stand-ins (``REFERENCE_STAND_IN``) are never resolved here: the stage keeps reading them."""
    if not available():
        return
    blocks = _blocks(stage)
    owned = {block.as_pointer() for block in blocks}
    pairs = []
    for standing in _holders(blocks):
        if STAND_IN not in standing.keys():
            continue
        found = _resolve(stage, int(standing[STAND_IN]), owned)
        if found is None:
            raise ProgramError(STALE, code='STALE_DEPENDENCY')
        pairs.append((standing, found))
    if pairs:
        relink(_holders(blocks), pairs)


def references(stage, marker=REFERENCE):
    """The stage's reference copies (or other marked IDs; resident or hydrated: the marker survives a bundle).

    Never what a kernel made: ``ID.copy()`` of a reference copy inherits its marker, but is the program's own
    data (its marker is removed here, so commit keeps the copy's edits)."""
    created = getattr(stage, "created_ids", None)
    if created is None:
        created = getattr(getattr(stage, "context", None), "created_ids", ())
    made = {block.as_pointer() for block in created or () if block is not None}
    result = []
    for block in _holders(_blocks(stage)):
        if marker not in block.keys():
            continue
        if block.as_pointer() in made:
            del block[marker]
            continue
        result.append(block)
    return result


def live_source(stage, copy, owned):
    """The live ID a reference copy or reference stand-in stands for, or None when the file no longer has it."""
    marker = REFERENCE if REFERENCE in copy.keys() else REFERENCE_STAND_IN
    return _resolve(stage, int(copy[marker]), owned)


def _resolve(stage, uid, owned):
    """The live ID with session UID ``uid`` (recorded in ``SOURCES``) that the stage does not own, or None."""
    collection, name = stage.extras.get(SOURCES, {}).get(str(uid), (None, None))
    items = getattr(_bpy().data, collection, None) if collection else None
    if items is None:
        return None
    found = items.get(name) if name is not None else None
    if found is not None and int(found.session_uid) == uid and found.as_pointer() not in owned:
        return found
    return next((item for item in items if int(item.session_uid) == uid and item.as_pointer() not in owned), None)


def _referencing(stage):
    return references(stage) + references(stage, REFERENCE_STAND_IN)


def originals(stage):
    """{reference copy or stand-in pointer: the live ID it stands for}: what a transfer carries live."""
    owned = {block.as_pointer() for block in _blocks(stage)}
    result = {}
    for copy in _referencing(stage):
        found = live_source(stage, copy, owned)
        if found is not None:
            result[copy.as_pointer()] = found
    return result


def commit(stage, replaced, source_map, extra_pairs=(), *, retirements=None):
    """Point the stage's links at the IDs the person keeps (module docstring), journaled for ``rollback``.

    ``replaced``: commit's ``[(old, new[, owner])]`` pairs; ``source_map``: staged object pointer -> live object;
    ``extra_pairs``: a caller's own (staged, live) pairs, applied in the same call. Every stage-owned ID but the
    staged objects and the stage's scene and collection is relinked, one native walk: the ones commit does not keep
    are freed with the stage. No-op on a binary without native relinking.
    """
    if not available():
        return
    blocks = _blocks(stage)
    owned = {block.as_pointer() for block in blocks}
    took = {}  # Live ID pointer -> the committed copy that took its place (it has no users left).
    for pair in replaced:
        old, new = pair[0], pair[1]
        owner = pair[2] if len(pair) > 2 else old
        if old is not None and new is not None and old != new and owner is not None and (not owner.users or retirements is not None and retirements.allows(owner)):
            took[old.as_pointer()] = new
    pairs = []
    for copy in _referencing(stage):
        found = live_source(stage, copy, owned)
        if found is None:
            raise ProgramError(STALE, code='STALE_DEPENDENCY')
        pairs.append((copy, took.get(found.as_pointer(), found)))
    staged = {obj.as_pointer(): obj for obj in stage.objects.values()}
    pairs.extend((obj, source_map[pointer]) for pointer, obj in staged.items()
                 if pointer in source_map and source_map[pointer].as_pointer() != pointer)
    pairs.extend(tuple(pair) for pair in extra_pairs)
    bpy = _bpy()
    containers = (bpy.types.Scene, bpy.types.Collection)  # The stage's own scene and collection hold staged objects.
    holders = [block for block in _holders(blocks) if block.as_pointer() not in staged
               and not isinstance(block, containers)]
    if holders and pairs:
        journal = relink(holders, pairs)[1]
        setattr(stage, JOURNAL, getattr(stage, JOURNAL, []) + [journal])


def rollback(stage):
    """Replay the commit's relink journals backwards: exactly the slots they changed (``_angelo_stage.unrelink``)."""
    journals = getattr(stage, JOURNAL, [])
    setattr(stage, JOURNAL, [])
    for journal in reversed(journals):
        _module().unrelink(journal)


def verify(stage, kept, roots=()):
    """Refuse (INTERNAL_ERROR, rolled back) when a kept copy or a written live ID (``roots``) still names a staged
    ID cleanup frees."""
    if not available():
        return
    bpy = _bpy()
    owned = {block.as_pointer() for block in _blocks(stage)}
    keep = {block.as_pointer() for block in kept}
    containers = (bpy.types.Scene, bpy.types.Collection)  # The stage's own scene and collection.
    holders = _holders(list(kept) + [root for root in roots if root is not None and root.library is None])
    if not holders:
        return
    for block in kept:   # A reference copy or stand-in is never the person's: commit points every link past it.
        if any(marker in block.keys() for marker in (REFERENCE, REFERENCE_STAND_IN, STAND_IN)):
            raise ProgramError(ESCAPED, code='INTERNAL_ERROR')
    for block in outside(holders):
        if block.as_pointer() in owned and block.as_pointer() not in keep and not isinstance(block, containers):
            raise ProgramError(ESCAPED, code='INTERNAL_ERROR')
