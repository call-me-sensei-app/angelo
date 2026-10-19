# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``visibility_set``: view-layer hiding of an object, and a collection's visibility.

An object target takes ``hidden`` (the view layer's eye, ``Object.hide_set``);
its own disable flags (``hide_viewport``, ``hide_render``, ``hide_select``) are
``property_set`` paths. The bound scene's handle with a ``collection`` name
takes the view layer's ``exclude`` and ``hidden`` (eye) for that collection
and the collection's own ``hide_viewport``, ``hide_render`` and
``hide_select``. Both live outside the staged copies (a view layer's Bases and
layer collections), so the kernel records intents in ``context.extras`` and
commit applies them to the bound view layer with a journal; the staged object
is hidden on the stage's view layer too, so previews match. Bpy-free: the
predicates take RNA objects (or stand-ins) from their callers.

An object only in collections excluded from the view layer has no Base, so no
visibility to set. Staging records the bound view layer's collection tree
(``LAYER_TREE``) and refuses, as a correctable ``VALIDATION`` before anything
live is written, any step that would leave an object this program hides
without one: the hide itself, a later move of that object, or a later exclude
of one of its collections (``has_base``, in program order, with Blender's own
rule in ``view_layer_exclusion``). Commit applies the program's exclusion
edits in program order, then hides objects, then applies the other collection
fields; an object without a Base there is a stale dependency when the view
layer's collections changed since staging, and an internal fault otherwise
(without that distinction a stale one was an INTERNAL_ERROR at commit).
"""
from .refusal_codes import RefusalError
from .view_layer_exclusion import (LAYER_TREE, ORDER, effective_collections, has_base, included,  # noqa: F401
                                   layer_collection, layer_tree, simulate)

COLLECTION_FIELDS = ("exclude", "hidden", "hide_viewport", "hide_render", "hide_select")
_LAYER_FIELDS = {"exclude": "exclude", "hidden": "hide_viewport"}  # LayerCollection RNA names.
MASTER_COLLECTION = "Scene Collection"
MAX_LAYER_ROWS = 1024  # The scene settings revision's rows (layer_rows).


def collection_state(view_layer, name):
    item = layer_collection(view_layer, name)
    if item is None:
        return None
    state = {field: bool(getattr(item, attribute)) for field, attribute in _LAYER_FIELDS.items()}
    state.update({field: bool(getattr(item.collection, field)) for field in ("hide_viewport", "hide_render", "hide_select")})
    return state


def layer_rows(scene):
    """Bounded collection visibility of every view layer, for the scene settings revision."""
    rows = []
    for view_layer in scene.view_layers:
        pending = [(item, 1) for item in view_layer.layer_collection.children]
        while pending and len(rows) < MAX_LAYER_ROWS:
            item, depth = pending.pop(0)
            collection = item.collection
            rows.append([view_layer.name, depth, collection.name, item.exclude, item.hide_viewport,
                         collection.hide_viewport, collection.hide_render, collection.hide_select])
            pending[:0] = [(child, depth + 1) for child in item.children]
    return rows


def not_in_view_layer(kind, name, members, why=""):
    """The correctable refusal for an object that would have no visibility to set."""
    listed = ", ".join(repr(member[:64]) for member in list(members)[:8])
    return RefusalError("%s NOT_IN_VIEW_LAYER: %r would be only in collections excluded from the view layer (%s)%s, "
                        "where an object has no visibility to set. Keep it in an included collection, include the "
                        "collection before hiding the object, or leave its visibility as it is."
                        % (kind, name[:64], listed, why), code='VALIDATION')


def _members(context, reference):
    from .action_object_management import _membership
    return _membership(context, reference)


def _shown(context, reference, obj):
    from .program_names import current_name
    return current_name(context.extras, reference, obj)  # Never a staged copy's private name.


def apply(context, operation):
    """Record the intent (and hide a staged object on the stage's view layer)."""
    params, target = operation["parameters"], operation["target"]
    intents = context.extras.setdefault("visibility", {"objects": {}, "collections": {}})
    if params["collection"] is None:
        obj = context.resolve(target)
        members = _members(context, target)
        base = has_base(context.extras, members)
        if base is False or base is None and context.extras.get("live_hidden", {}).get(target, False) is None:
            raise not_in_view_layer("visibility_set", _shown(context, target, obj), members)
        with_base = context.view_layer.objects.get(obj.name)
        if with_base is not None:
            obj.hide_set(params["hidden"], view_layer=context.view_layer)
        intents["objects"][target] = params["hidden"]
        return {"kind": operation["kind"], "target": target, "object": obj.name, "hidden": params["hidden"]}
    if target not in context.scene_handles:
        raise RefusalError("A collection's visibility is set through the bound scene's handle", code='VALIDATION')
    name = params["collection"]
    known = context.extras.get("scene_collections")
    if name == MASTER_COLLECTION or (known is not None and name not in known):
        raise RefusalError("visibility_set COLLECTION_NOT_FOUND: name a child collection of the bound scene", code='VALIDATION')
    edits = {field: params[field] for field in COLLECTION_FIELDS if params[field] is not None}
    intents["collections"].setdefault(name, {}).update(edits)
    if "exclude" in edits:  # Commit replays exclusion edits in program order (an edit sets the descendants too).
        intents.setdefault(ORDER, []).append([name, edits["exclude"]])
    if edits.get("exclude"):  # No object this program hides may be left without a Base.
        for reference in intents["objects"]:
            members = _members(context, reference)
            if reference not in context.extras.get("deleted", ()) and has_base(context.extras, members) is False:
                raise not_in_view_layer("visibility_set", _shown(context, reference, context.resolve(reference)),
                                        members, " once %r is excluded, after this program set its visibility"
                                        % name[:64])
    return {"kind": operation["kind"], "target": target, "collection": name, **edits}


def hidden_state(obj, view_layer):
    """The object's view-layer hide state, or None without a Base (an excluded collection).

    ``hide_get`` returns False for an object without a Base, so the Base is
    looked up too; ``hide_get`` first brings the view layer's Bases in sync
    with a just-linked object.
    """
    hidden = bool(obj.hide_get(view_layer=view_layer))
    return hidden if view_layer.objects.get(obj.name) == obj else None


class BaseHidden:
    """A journal holder: ``hidden`` reads and writes an object's view-layer hide state."""

    def __init__(self, obj, view_layer):
        self.obj, self.view_layer = obj, view_layer

    @property
    def hidden(self):
        return self.obj.hide_get(view_layer=self.view_layer)

    @hidden.setter
    def hidden(self, value):
        self.obj.hide_set(value, view_layer=self.view_layer)


def apply_intents(extras, targets, view_layer, journal, error, stale=None, refused=None):
    """Carry out recorded intents on the bound view layer; ``journal`` gets (holder, name, previous).

    ``targets`` maps handles to live originals and ``$aliases`` to committed new objects. The program's exclusion
    edits come first, in program order, each skipped when it would not change the flag; the view layer must then
    hold exactly the flags Blender's rule predicts from its state before them (view_layer_exclusion.simulate), else
    the commit is refused (``refused``, default ``error``) and rolled back. Then the hides (staging checked that
    every object hidden keeps a Base; an object the program deletes is skipped), then the other collection fields.
    ``error`` builds an internal fault; ``stale`` (default ``error``) a change made after staging.
    """
    from .view_layer_exclusion import layer_state, native_flags, native_rollback, predicted_bits, simulate
    stale, refused = stale or error, refused or error
    intents = extras.get("visibility") or {}
    collections = intents.get("collections", {})
    recorded = extras.get(LAYER_TREE)
    order = intents.get(ORDER)
    if order is None:  # Recorded before the order was kept: each collection's exclude, in its own order.
        order = [[name, edits["exclude"]] for name, edits in collections.items() if "exclude" in edits]
    created = set(extras.get("created_collections", ()))  # Linked by this commit, under the master collection.
    state = layer_state(view_layer) if order or (recorded is not None and intents.get("objects")) else None
    rows, keys, bits = state if state is not None else (None, None, None)
    before = None if rows is None else [row for row in rows if row[1] not in created]
    if order:
        holder = native_rollback(view_layer, keys)
        if holder is not None:  # Every layer collection's bits, restored exactly on rollback (both flags).
            journal.append((holder, "flags", keys))
        for name, value in order:
            _exclude(view_layer, name, value, journal if holder is None else None, error, stale)
        if rows is not None:  # Live flags against Blender's rule applied to the state just before the edits.
            live = native_flags(view_layer) if bits is not None else None
            if live is not None:
                expected = predicted_bits(rows, order)
                wrong = len(live) != len(expected) or any(want is not None and got != want
                                                          for got, want in zip(live, expected))
            else:
                expected = simulate(rows, order)[0]
                after = layer_tree(view_layer) or []
                wrong = len(after) != len(expected) or any(want is not None and row[2] is not want
                                                           for row, want in zip(after, expected))
            if wrong:
                raise refused("COMMIT_FIDELITY: the view layer's collection exclusions after this program's edits "
                              "are not the ones Blender's rule gives; nothing was changed")
    deleted = set(extras.get("deleted", ()))
    for handle, hidden in intents.get("objects", {}).items():
        if handle in deleted:  # Removed by this commit: its hide is moot (staging skips it too).
            continue
        obj = targets.get(handle)
        if obj is None or hidden_state(obj, view_layer) is None:
            message = "visibility_set NOT_IN_VIEW_LAYER: the object's collection is excluded from the view layer"
            if recorded is not None and before != [list(row) for row in recorded]:
                raise stale(message + "; the view layer's collections changed after the program was staged")
            raise error(message)
        holder = BaseHidden(obj, view_layer)
        journal.append((holder, "hidden", holder.hidden))
        holder.hidden = hidden
    for name, edits in collections.items():
        _apply_collection(view_layer, name, {field: value for field, value in edits.items() if field != "exclude"},
                          journal, error, stale)


def _found(view_layer, name, stale):
    item = layer_collection(view_layer, name)
    if item is None:  # Staging found it in the bound scene (scene_collections): renamed or removed since.
        raise stale("visibility_set COLLECTION_NOT_FOUND: " + name[:64])
    return item


def _exclude(view_layer, name, value, journal, error, stale):
    """One exclusion edit through RNA, skipped when the flag already has the value (writing it would still re-run
    Blender's recursion). ``journal`` is None when the whole tree's bits are journaled natively; otherwise only the
    edited flag is (without the native write, exact restoration of the hidden flag is not possible)."""
    item = _found(view_layer, name, stale)
    if bool(item.exclude) == bool(value):
        return
    if journal is not None:
        journal.append((item, "exclude", item.exclude))
    item.exclude = value
    if bool(item.exclude) != bool(value):
        raise error("visibility_set could not set exclude of %s" % name[:64])


def _apply_collection(view_layer, name, edits, journal, error, stale):
    if not edits:
        return
    item = _found(view_layer, name, stale)
    for field in edits:
        holder, attribute = (item, _LAYER_FIELDS[field]) if field in _LAYER_FIELDS else (item.collection, field)
        journal.append((holder, attribute, getattr(holder, attribute)))
        setattr(holder, attribute, edits[field])
        if bool(getattr(holder, attribute)) != edits[field]:
            raise error("visibility_set could not set %s of %s" % (field, name[:64]))


def evaluate(context_visibility, view_layer, obj, check):
    """The ``visibility`` check: staged intents (with the recorded live state) or the live view layer."""
    if check.get("collection") is not None:
        name = check["collection"]
        state = collection_state(view_layer, name) if view_layer is not None else None
        if context_visibility is not None:
            state = dict(state or {}, **context_visibility.get("collections", {}).get(name, {})) or None
        if state is None:
            return False, {"collection": name[:64], "found": False}
        wanted = {field: check[field] for field in COLLECTION_FIELDS if check.get(field) is not None}
    else:
        if context_visibility is not None:
            hidden = context_visibility.get("objects", {}).get(check["target"])
        else:
            hidden = hidden_state(obj, view_layer) if view_layer is not None else obj.hide_get()
        state, wanted = {"hidden": hidden}, {"hidden": check["hidden"]}
        if hidden is None:
            return False, {"hidden": None, "note": "not in the bound view layer"}
    wrong = sorted(field for field, value in wanted.items() if state.get(field) != value)
    observed = {field: state.get(field) for field in wanted}
    if wrong:
        observed["mismatched"] = wrong
    return not wrong, observed
