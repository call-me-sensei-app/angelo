# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unkeyed values Blender keeps, kept through staging and commit.

Blender keeps a stored value an animation does not give (an artist's unkeyed
edit) until it evaluates that animation again: a frame change, or an edit of
the curves. Changing another setting or inserting a key does not. The host
evaluates more often than that: staging evaluates every fresh staged copy, and
an evaluation it tags (a kernel keying a channel) or causes at commit (giving
the live object its edited Action) evaluates the whole ID. Each replaced every
unkeyed edit of the ID's animated channels, which commit then wrote back to the
live object with nothing said (a transform of a keyed object reset its keyed
rotation's unkeyed edit; a key insert dropped the same program's earlier
unkeyed rotation).

So every host evaluation keeps them, as ``animated_values`` measures: the
stored value of every channel an animation can write is read before and
written back after (``kept_update`` around each stage update, ``keep`` after
commit's transfers and their update), except where the program itself makes
Blender evaluate the animation (``follow``: the channels it keys or whose curves
it edits, an Action or NLA it changes, and every channel of a program that
moves the frame) and channels a driver holds. What a frame change replaces is
stated (``unkeyed_values_replaced``); what the next one will replace of the
program's own writes too (``unkeyed_writes``).
"""

from .animated_values import Channels, _differs, _key, _read, _write

FOLLOW = "kept_values_follow"  # stage.extras: {handle or alias: [object-relative paths] or "*"}, or "*" for all.
_CACHE = "kept_values_channels"  # stage.extras: (stage shape, the channels kept), rebuilt when the shape changes.
ALL = "*"
_KEYED = {"keyframe_insert": "property", "keyframe_delete": "property", "keyframe_configure": "property",
          "keyframe_edit": "property", "keyframe_copy": "property", "fcurve_modifier": "property"}
_WHOLE = frozenset({"action_set", "nla_edit", "nla_push_down", "operator_run"})


def follow(program):
    """What the program makes Blender evaluate: ``{reference: paths or ALL}``, or ALL for a program that writes a
    frame-update setting (every animation is evaluated at its frame)."""
    from .program_frames import frame_updates
    if frame_updates(program):
        return ALL
    found = {}

    def add(reference, paths):
        if reference is None:
            return
        if paths == ALL or found.get(reference) == ALL:
            found[reference] = ALL
        else:
            found.setdefault(reference, []).extend(paths)
    for operation in program["operations"]:
        kind, params, target = operation["kind"], operation["parameters"], operation["target"]
        if kind in _KEYED:
            add(target, ALL if params.get(_KEYED[kind]) is None else [params[_KEYED[kind]]])
        elif kind == "property_keyframe":
            add(target, list(params["values"]))
        elif kind in _WHOLE or kind == "shape_key_value" and params.get("frame") is not None:
            add(target, ALL)
    return found


def _owners(obj):
    """``[(ID, prefix)]``: the IDs whose animation can hold an object's channels, with their path from it."""
    data = getattr(obj, "data", None)
    material = getattr(obj, "active_material", None)
    found = [(obj, ""), (data, "data."), (getattr(data, "shape_keys", None), "data.shape_keys."),
             (material, "active_material."), (getattr(material, "node_tree", None), "active_material.node_tree.")]
    for index, slot in enumerate(getattr(obj, "material_slots", ())):  # Every slot's, as the commit check lists.
        prefix = "material_slots[%d].material." % index
        found += [(slot.material, prefix), (getattr(slot.material, "node_tree", None), prefix + "node_tree.")]
    return [(block, prefix) for block, prefix in found if block is not None]


def _followed(prefix, path, paths):
    whole = prefix + path
    return paths == ALL or any(whole == item or whole.startswith(item + ".") or whole.startswith(item + "[")
                               for item in paths)


def _drivers(owner):
    """The channel paths ``owner``'s drivers hold (Blender evaluates those, kept or not)."""
    animation = getattr(owner, "animation_data", None)
    return frozenset(curve.data_path for curve in animation.drivers) if animation is not None else frozenset()


def _kept(objects, rule):
    """``[(owner, path)]``: the channels of ``objects`` (``{reference: object}``) the host must keep, given ``rule``
    (``follow``'s): every channel an Action or NLA of their IDs lists, except driven and followed ones."""
    if rule == ALL:
        return []
    pairs = [(reference, block, prefix) for reference, obj in objects.items() for block, prefix in _owners(obj)]
    prefixes = {}
    for reference, block, prefix in pairs:
        prefixes.setdefault(_key(block), []).append((prefix, rule.get(reference, ())))
    channels = Channels([block for _reference, block, _prefix in pairs], read=False)
    drivers = {}
    found = []
    for owner, path in channels.entries:
        pointer = _key(owner)
        if pointer not in drivers:
            drivers[pointer] = _drivers(owner)
        if path not in drivers[pointer] and not any(_followed(prefix, path, paths)
                                                    for prefix, paths in prefixes.get(pointer, ())):
            found.append((owner, path))
    return found


def _stage_shape(context):
    """What changes whenever a staged object's IDs can: the stage's objects, aliases and owned IDs, counted. A kernel
    that gives an object other data, material, shape keys or a copy tracks a new ID, so the count grows; only kinds
    ``follow`` lists change curves, on channels they follow. Checking this per update, not every object's IDs, keeps
    a stage of many objects (a batch of a thousand copies) as fast as one without kept values."""
    scene = getattr(context, "scene", None)  # A rebuilt stage (a resumed one) has another scene and context.
    return (id(context), getattr(scene, "session_uid", None), len(getattr(context, "objects", {}) or {}),
            len(getattr(context, "aliases", {}) or {}), len(getattr(context, "owned_ids", ()) or ()))


def kept_update(context, update):
    """Run ``update`` (a stage view-layer update) and write back each stored value it replaced that Blender keeps.
    The kept list is built once per stage shape (``_stage_shape``) and kept in the stage's extras."""
    rule = context.extras.get(FOLLOW, ALL)
    shape = _stage_shape(context)
    cached = context.extras.get(_CACHE)
    if cached is not None and cached[0] == shape:
        entries = cached[1]
    else:
        objects = dict(getattr(context, "objects", {}) or {})
        objects.update({"$" + alias.lstrip("$"): obj for alias, obj in (getattr(context, "aliases", {}) or {}).items()})
        entries = _kept(objects, rule) if objects else []
        context.extras[_CACHE] = (shape, entries)
    if not entries:
        return update()
    before = [_read(owner, path) for owner, path in entries]
    result = update()
    restored = False
    for (owner, path), value in zip(entries, before):
        if value is None or not _differs(value, _read(owner, path)):
            continue
        try:
            _write(owner, path, value)
            restored = True
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError, ReferenceError):
            continue
    if restored:
        context.view_layer.update()  # Their evaluated state; no animation is tagged, so nothing is replaced again.
    return result


def keep(check, stage, view_layer):
    """After commit's transfers and the update that evaluates them: each listed channel of the live objects
    the program wrote back at what the transfers stored, except what the program has Blender evaluate."""
    if check is None or check.after_writes is None or check.frame_written:
        return []
    rule = stage.extras.get(FOLLOW, ALL)
    if rule == ALL:
        return []
    owners = {}
    created = {"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()}
    for reference, obj in list(stage.originals.items()) + list(created.items()):
        if reference in set(stage.binding.get("writes", ())) or reference in created:
            for block, prefix in _owners(obj):
                owners.setdefault(_key(block), []).append((prefix, rule.get(reference, ())))
    channels, stored, now = check.channels, check.after_writes, check.channels.read()
    restored = []
    for slot, (owner, path) in enumerate(channels.entries):
        rules = owners.get(_key(owner))
        if (rules is None or path in _drivers(owner) or any(_followed(prefix, path, paths) for prefix, paths in rules)
                or stored[slot] is None or now[slot] is None or not _differs(stored[slot], now[slot])):
            continue
        try:
            _write(owner, path, stored[slot])
            restored.append(slot)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError):
            continue
    if restored:
        view_layer.update()
    return restored


def rolled_back(check, view_layer):
    """After a failed commit's rollback and its update: every listed channel back at what it stored before the commit
    (driven ones excepted). The rollback gives each object its Action again, and that update re-evaluates it, so
    without this an artist's unkeyed edits are lost on a failure. A program that writes a frame
    setting is left to Blender's frame update, whose replacements are stated (camera_guards.rolled_back)."""
    if check is None or check.frame_written or check.channels.values is None:
        return []
    restored = []
    for slot, ((owner, path), value) in enumerate(zip(check.channels.original_entries, check.channels.values)):
        now = None if value is None else _read(owner, path)  # None too for an ID the rollback removed.
        if now is None or not _differs(value, now):
            continue
        try:
            if path in _drivers(owner):
                continue
            _write(owner, path, value)
            restored.append(slot)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError, ReferenceError):
            continue
    if restored:
        view_layer.update()
    return restored
