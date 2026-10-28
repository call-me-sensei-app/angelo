# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How an object is hidden, stated as facts beside every reader's other facts about it.

The maintainer (2026-10-25), on an instancer in a collection hidden by its eye, which the readers state as shown: "Then
it should be told so that if it needs to move it, it knows it already exists so it doesn't create a new instance".
Every reader that lists an object (the per-turn facts, ``angelo_scene_object`` and ``angelo_scene_query`` rows, a
script's ``after`` rows, a program's geometry rows, ``kit.bounds`` when only hidden objects match its prefix) states
``visibility`` for an object any of these hide, one key each, named as ``visibility_set`` and Blender name them, and
nothing for an object none of them hides:

- ``hidden``: its own eye in the view layer (``Object.hide_get``);
- ``hide_viewport``: its own Disable in Viewports (the monitor icon);
- ``hide_render``: its own Disable in Renders;
- ``collection_hidden``: the collections whose eye in the view layer (``LayerCollection.hide_viewport``) hides it;
- ``collection_hide_viewport`` and ``collection_hide_render``: the collections whose Disable in Viewports or in
  Renders (``Collection.hide_viewport``, ``Collection.hide_render``) does;
- ``collection_exclude``: its collections, when the view layer excludes every one (it has no Base there).

A collection setting is stated only when it takes effect, as Blender decides it (``layer.cc``
``layer_collection_sync``): an eye, a Disable in Viewports and a Disable in Renders hold for every collection inside
the one that carries it. A viewport shows an object through any of its collections free of both the eye and Disable
in Viewports, so when every included collection path carries one of them both are stated, each with the collections
that carry it (a path hidden by an eye and another disabled in viewports hide it too, REVIEW-HIDDEN-FLAGS-2 M1); a
render shows it through any path free of Disable in Renders, stated when every included path carries it. Exclusion is each layer collection's
own, never inherited (``view_layer_exclusion``). What an object's instances show stays ``object_bounds``' fact,
stated beside these for a hidden object wherever the instance pass reads them.

One ``Pass`` per read: the view layer's collection tree is walked once, and only when an object in a collection is
asked about (``Object.users_collection``, or a program's memberships by name).
"""

from .scene_read_values import label

MASTER = "Scene Collection"
MAX_ROWS = 1 << 16   # Layer collections walked; past it no collection fact is stated (a bound against runaway data).
MAX_NAMES = 4        # Collections named per fact; the rest are counted.
COLLECTION_KEYS = (("collection_hidden", 2), ("collection_hide_viewport", 3), ("collection_hide_render", 4))
_UNREADABLE = (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError)


def _true(value):
    return value is True   # Blender's own True; a stand-in's value is never read as hidden.


def hide_get(obj, view_layer):
    """``obj``'s own eye in ``view_layer``: False without a Base there, or when it cannot be read."""
    try:
        return _true(obj.hide_get(view_layer=view_layer) if view_layer is not None else obj.hide_get())
    except _UNREADABLE:
        return False


def _disabled_in_renders(master):
    """Whether a collection under ``master`` is Disabled in Renders. One C-level read of every collection's flag
    comes first: with none set anywhere (most scenes) none is set under ``master`` either, and no tree is walked.
    ``children_recursive`` is Python that rebuilds the whole tree per call (about 0.12 ms at 511 collections, paid by
    every single-object read); it decides only when some collection is set, since that one may be another scene's."""
    try:
        import array
        import bpy
        collections = bpy.data.collections
        if isinstance(collections, bpy.types.bpy_prop_collection):
            flags = array.array("b", bytes(len(collections)))
            collections.foreach_get("hide_render", flags)
            if not any(flags):
                return False
    except (ImportError, *_UNREADABLE):
        pass   # A stand-in or an unreadable collection: the walk below decides.
    return any(_true(child.hide_render) for child in master.children_recursive)


def _names(names):
    names = list(dict.fromkeys(names))
    shown = [label(name) for name in names[:MAX_NAMES]]
    return shown + ["+%d more" % (len(names) - MAX_NAMES)] if len(names) > MAX_NAMES else shown


class _Tree:
    """A view layer's collection paths: per collection, one ``(excluded, name, eye, monitor, render)`` state for
    each layer collection that shows it, the last three the names on its path that carry the setting."""

    def __init__(self):
        self.by_pointer, self.by_name, self.restricted = {}, {}, False

    def facts(self, states):
        states = list(states)
        if not states:
            return {}
        included = [state for state in states if not state[0]]
        if not included:
            return {"collection_exclude": _names(state[1] for state in states)}
        facts = {}
        if all(state[2] or state[3] for state in included):   # The viewport shows it through a path free of both.
            for key, index in COLLECTION_KEYS[:2]:
                names = [name for state in included for name in state[index]]
                if names:
                    facts[key] = _names(names)
        if all(state[4] for state in included):
            facts["collection_hide_render"] = _names(name for state in included for name in state[4])
        return facts


def _edited(rows, edits):
    """{name: index of the row a program's edits by that name land on}: the first local row depth first, else the
    first (``visibility_set``, ``view_layer_exclusion._first_rows``)."""
    if not edits:
        return {}
    first, linked = {}, {}
    for index, (_depth, item) in enumerate(rows):
        name = item.collection.name
        if name in edits:
            (linked if getattr(item.collection, "library", None) is not None else first).setdefault(name, index)
    return {**linked, **first}


def _walk(view_layer, edits):
    """The ``_Tree`` of ``view_layer`` with ``edits`` applied, or None (unreadable, or past ``MAX_ROWS``)."""
    root = view_layer.layer_collection
    rows, pending = [], [(child, 1) for child in reversed(list(root.children))]
    while pending:
        if len(rows) == MAX_ROWS:
            return None
        item, depth = pending.pop()
        rows.append((depth, item))
        pending.extend((child, depth + 1) for child in reversed(list(item.children)))
    edited = _edited(rows, edits)
    datablocks = {name: rows[index][1].collection.as_pointer() for name, index in edited.items()}
    tree, chain = _Tree(), [((), (), ())]
    root_state = (False, MASTER, (), (), ())
    tree.by_pointer[root.collection.as_pointer()] = [root_state]
    tree.by_name[MASTER] = [root_state]
    for index, (depth, item) in enumerate(rows):
        collection = item.collection
        name, pointer = collection.name, collection.as_pointer()
        layer_edit = edits.get(name, {}) if edited.get(name) == index else {}
        data_edit = edits.get(name, {}) if datablocks.get(name) == pointer else {}
        excluded = layer_edit.get("exclude", item.exclude)
        own = (layer_edit.get("hidden", item.hide_viewport), data_edit.get("hide_viewport", collection.hide_viewport),
               data_edit.get("hide_render", collection.hide_render))
        del chain[depth:]
        inherited = tuple(names + ((name,) if _true(flag) else ()) for names, flag in zip(chain[-1], own))
        chain.append(inherited)
        state = (_true(excluded), name) + inherited
        tree.by_pointer.setdefault(pointer, []).append(state)
        if getattr(collection, "library", None) is None:   # A program links only into local collections.
            tree.by_name.setdefault(name, []).append(state)
        tree.restricted = tree.restricted or state[0] or any(inherited)
    return tree


class Pass:
    """The ``visibility`` facts of one read pass's objects in ``view_layer``. ``edits``: {collection name: {"exclude"
    | "hidden" | "hide_viewport" | "hide_render": bool}} applied as the tree is read (a program's intents: the state
    its commit gives)."""

    def __init__(self, view_layer, edits=None):
        self._layer, self._edits, self._tree, self._read, self._renders = view_layer, edits or {}, None, False, None

    def tree(self):
        if not self._read:
            self._read = True
            try:
                self._tree = _walk(self._layer, self._edits) if self._layer is not None else None
            except _UNREADABLE:
                self._tree = None
        return self._tree

    def of(self, obj, collections=None, hidden=None):
        """``obj``'s facts (module docstring), or None when nothing hides it. ``collections``: the names of its
        collections (a program's memberships after commit) in place of ``Object.users_collection``; ``hidden``: its
        eye (None: read from the view layer)."""
        facts = {"hidden": True} if (hide_get(obj, self._layer) if hidden is None else _true(hidden)) else {}
        for key in ("hide_viewport", "hide_render"):
            if _true(getattr(obj, key, None)):
                facts[key] = True
        if collections is None and self._shown(obj):
            return facts or None   # Shown, and no collection is left out of renders: no collection fact applies.
        tree = self.tree()
        if tree is not None and tree.restricted:
            try:
                states = ([state for name in collections for state in tree.by_name.get(name, ())]
                          if collections is not None else
                          [state for collection in obj.users_collection
                           for state in tree.by_pointer.get(collection.as_pointer(), ())])
            except _UNREADABLE:
                states = []
            facts.update(tree.facts(states))
        return facts or None

    def _shown(self, obj):
        """Whether ``obj`` is shown in the view layer while no collection of its scene is disabled in renders: then
        no collection setting applies to it, and its collections are never read (``Object.users_collection`` costs
        about 0.1 ms an object in a scene of 200 collections, Mac 2)."""
        try:
            if not _true(obj.visible_get(view_layer=self._layer)):
                return False
            if self._renders is None:
                self._renders = _disabled_in_renders(self._layer.layer_collection.collection)
            return not self._renders
        except _UNREADABLE:
            return False

    def entry(self, obj, **known):
        """``{"visibility": facts}`` for a reader's row, or {} when nothing hides ``obj`` (``of``'s ``known``)."""
        facts = self.of(obj, **known)
        return {"visibility": facts} if facts else {}


def staged(stage, view_layer):
    """``of(handle, staged object)``: the ``Pass.entry`` of the facts a program's commit gives an object of its stage
    in the bound ``view_layer``: its eye as the program leaves it (else as it is live), its own Disable flags as
    staged, and its collections as commit links them, with the program's collection edits (``view_layer_exclusion``).
    Optional evidence: a stage these cannot be read from states none, and never fails the program."""
    from .action_object_management import _membership
    from .view_layer_exclusion import effective_collections
    try:
        extras = stage.extras
        eyes = {**extras.get("live_hidden", {}), **(extras.get("visibility") or {}).get("objects", {})}
        found = Pass(view_layer, effective_collections(extras))
        aliases = {value.as_pointer(): "$" + key.lstrip("$") for key, value in stage.context.aliases.items()}
    except _UNREADABLE:
        return lambda handle, obj: {}

    def of(handle, obj):
        try:
            reference = handle if handle is not None else aliases.get(obj.as_pointer())
            if reference is None:
                return {}
            original = stage.originals.get(reference)
            hidden = (eyes[reference] if reference in eyes else
                      hide_get(original, view_layer) if original is not None else False)
            return found.entry(obj, collections=_membership(stage.context, reference), hidden=hidden)
        except _UNREADABLE:
            return {}
    return of
