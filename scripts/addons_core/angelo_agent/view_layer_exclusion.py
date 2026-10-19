# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which collections give an object a Base in the bound view layer, exactly as Blender decides it.

An object has a Base when one of its collections has a layer collection whose own ``exclude`` flag is clear,
wherever its parents are (``layer.cc``: "Layer collection exclude is not inherited"). Setting ``exclude`` through
RNA runs ``layer_collection_flag_recursive_set`` on that layer collection: excluding it clears its own
``LAYER_COLLECTION_PREVIOUSLY_EXCLUDED`` and, on every descendant, records whether it was excluded already in
that hidden flag before excluding it; including it clears ``exclude`` on every descendant without the hidden flag.
So a child excluded before its parent stays excluded when the parent is included again.

RNA does not expose the hidden flag; ``_angelo_stage.layer_flags`` reads both bits and ``set_layer_flags`` writes
them back. Staging records the tree with both (``LAYER_TREE``: [depth, name, exclude, linked, previously]) and
replays the program's edits in program order (``ORDER``) with Blender's algorithm, each on the layer collection
``visibility_set`` edits (the first of that name, a local collection before a library one); an edit that would not
change the flag is skipped, as commit skips it, because writing an unchanged value still re-runs the recursion.
Without the native read the hidden flag is unknown (None), and a result it decides is unknown too, which counts as
excluded (conservative). Commit restores the whole tree's bits on rollback (``LayerFlags``): restoring the edited
flag through RNA would re-run the recursion and change the descendants.

The tree has no 1,024-row cap, and each name's first row is found once (never a quadratic search).
"""

MASTER_COLLECTION = "Scene Collection"
MAX_TREE_ROWS = 1 << 16  # A bound against runaway data, far past any scene's collections.
LAYER_TREE = "layer_tree"  # Extras: the bound view layer's rows when staged.
ORDER = "exclude_order"  # In the visibility intents: [name, exclude] in program order.
_EXCLUDE, _PREVIOUSLY = 1, 2


def _linked(item):
    return getattr(item.collection, "library", None) is not None


def native_rows(view_layer):
    """(depth, name, bits, linked, session UID) of each layer collection, depth first, read natively (no RNA walk);
    None without the native read."""
    try:
        from _bpy import _angelo_stage
        read = _angelo_stage.layer_flags
    except (ImportError, AttributeError):
        return None
    return read(view_layer.id_data, view_layer.name)


def native_flags(view_layer):
    """Each layer collection's exclude (1) and previously-excluded (2) bits, depth first, or None."""
    rows = native_rows(view_layer)
    return None if rows is None else [row[2] for row in rows]


def _walk(view_layer):
    """(depth, layer collection), depth first, through RNA (without the native read); None past MAX_TREE_ROWS. A
    stack, not a queue: popping the front of a long list is quadratic."""
    rows, pending = [], [(item, 1) for item in reversed(view_layer.layer_collection.children)]
    while pending:
        if len(rows) == MAX_TREE_ROWS:
            return None
        item, depth = pending.pop()
        rows.append((depth, item))
        pending.extend((child, depth + 1) for child in reversed(item.children))
    return rows


def layer_collection(view_layer, name, limit=MAX_TREE_ROWS):
    """The LayerCollection ``visibility_set`` edits for ``name``: the first local one depth first, else the first."""
    pending, seen, linked = list(reversed(view_layer.layer_collection.children)), 0, None
    while pending and seen < limit:
        item = pending.pop()
        seen += 1
        if item.collection.name == name:
            if not _linked(item):
                return item
            linked = linked or item
        pending.extend(reversed(item.children))
    return linked


def _keys(native):
    """Each native row's path of collection session UIDs (a collection under two parents has two paths)."""
    keys, stack = [], []
    for depth, _name, _bits, _linked, uid in native:
        del stack[depth - 1:]
        stack.append(uid)
        keys.append(tuple(stack))
    return keys


def _native_tree(native):
    return [[depth, name, bool(bits & _EXCLUDE), bool(linked), bool(bits & _PREVIOUSLY)]
            for depth, name, bits, linked, _uid in native]


def layer_tree(view_layer):
    """[depth, name, exclude, linked, previously excluded (None: unknown)] rows, depth first; None past the cap."""
    native = native_rows(view_layer)
    if native is not None:
        return None if len(native) > MAX_TREE_ROWS else _native_tree(native)
    walked = _walk(view_layer)  # Without the native read the hidden flag is unknown.
    return None if walked is None else [[depth, item.collection.name, bool(item.exclude), _linked(item), None]
                                        for depth, item in walked]


def layer_state(view_layer):
    """(rows, {collection path: native bits} or None, native bits or None); None past the cap."""
    native = native_rows(view_layer)
    if native is None:
        rows = layer_tree(view_layer)
        return None if rows is None else (rows, None, None)
    if len(native) > MAX_TREE_ROWS:
        return None
    bits = [row[2] for row in native]
    return _native_tree(native), dict(zip(_keys(native), bits)), bits


def predicted_bits(rows, order):
    """The native bits Blender's rule gives every row after ``order``, or None where a hidden flag is unknown."""
    excluded, previously = simulate(rows, order)
    return [None if a is None or b is None else (_EXCLUDE if a else 0) | (_PREVIOUSLY if b else 0)
            for a, b in zip(excluded, previously)]


def _first_rows(rows):
    """Each name's row ``visibility_set`` edits: the first local one, else the first library one."""
    first, linked = {}, {}
    for index, row in enumerate(rows):
        (linked if row[3] else first).setdefault(row[1], index)
    return {**linked, **first}


def simulate(rows, order=None):
    """(exclude, previously excluded) of every row after the edits in ``order``, Blender's recursion, in program
    order; None where an unknown hidden flag decides it."""
    excluded = [bool(row[2]) for row in rows]
    previously = [row[4] if len(row) > 4 else None for row in rows]
    first = _first_rows(rows) if order else {}
    for name, value in order or ():
        index = first.get(name)
        if index is None or excluded[index] is bool(value):
            continue  # Unchanged: commit writes nothing, so the recursion never runs.
        end = index + 1
        while end < len(rows) and rows[end][0] > rows[index][0]:
            end += 1
        excluded[index] = bool(value)
        if value:
            previously[index] = False
            for child in range(index + 1, end):
                previously[child], excluded[child] = excluded[child], True
        else:
            for child in range(index + 1, end):
                if previously[child] is False:
                    excluded[child] = False
                elif previously[child] is None and excluded[child] is not False:
                    excluded[child] = None
    return excluded, previously


def included(rows, order=None):
    """The collection names an object has a Base through after ``order``: a known clear flag only; a library
    collection counts only when no local collection has its name."""
    local = {row[1] for row in rows if not row[3]}
    names = {MASTER_COLLECTION}
    for row, excluded in zip(rows, simulate(rows, order)[0]):
        if excluded is False and (not row[3] or row[1] not in local):
            names.add(row[1])
    return names


def program_order(extras):
    return list(((extras.get("visibility") or {}).get(ORDER)) or ())


def has_base(extras, members):
    """Whether an object in the collections ``members`` has a Base in the bound view layer after this program's
    exclusion edits so far (a collection it creates is linked included); None when no tree was recorded."""
    rows = extras.get(LAYER_TREE)
    if rows is None:
        return None
    names = included(rows, program_order(extras))
    created = set(extras.get("created_collections", ()))
    return any(name in names or name in created for name in members)


def effective_collections(extras):
    """The visibility intents' collection edits, each ``exclude`` being the flag the program ends with (None when
    unknown, which no check matches), so a staged check reads what commit produces."""
    intents = extras.get("visibility") or {}
    edits = {name: dict(values) for name, values in intents.get("collections", {}).items()}
    rows = extras.get(LAYER_TREE)
    order = program_order(extras)
    if rows is None or not order:
        return edits
    after = simulate(rows, order)[0]
    for name, index in _first_rows(rows).items():
        if after[index] is not bool(rows[index][2]) or "exclude" in edits.get(name, {}):
            edits.setdefault(name, {})["exclude"] = after[index]
    return edits


class LayerFlags:
    """A rollback journal holder: ``flags`` reads each layer collection's exclusion bits by path, and writing them
    back restores exactly what was read (natively), whatever RNA's recursive update did meanwhile. A layer
    collection made since (a collection the commit created) keeps its bits."""

    def __init__(self, view_layer):
        self.view_layer = view_layer

    @property
    def flags(self):
        return layer_state(self.view_layer)[1]

    @flags.setter
    def flags(self, saved):
        from _bpy import _angelo_stage
        native = native_rows(self.view_layer)
        _angelo_stage.set_layer_flags(self.view_layer.id_data, self.view_layer.name,
                                      [saved.get(key, row[2]) for key, row in zip(_keys(native), native)])


def native_rollback(view_layer, keys):
    """A ``LayerFlags`` holder when the native write exists and ``keys`` (the bits by path) were read, else None."""
    try:
        from _bpy import _angelo_stage
    except ImportError:
        return None
    return LayerFlags(view_layer) if keys is not None and hasattr(_angelo_stage, "set_layer_flags") else None
