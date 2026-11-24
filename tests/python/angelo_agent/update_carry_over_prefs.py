# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every preference a person can change, as an app reads it (``values``), and what differs between two readings
(``differences``): how ``update_carry_over_gate`` proves an update on one Blender base left the person's preferences as
they were. The earlier app reads them after it seeded them and again when it opens after the update, so both readings
come from the same code. Standard library only: the walker takes the RNA objects it is given.

What is read (break-it review r2 B1: everything userpref.blend keeps):
* every writable value of ``bpy.context.preferences``, following pointer sections and COLLECTIONS (themes, UI styles,
  asset libraries, script directories, extension repositories, auto-run paths, studio lights, ...), each item keyed
  by its name when the collection's names are unique, else by its index;
* each enabled add-on and its own preferences (``addons.<module>``, Angelo's provider, models, reasoning, CLI paths and
  consents among them), walked the same way;
* every item of the user keyconfig a person changed or added (``is_user_modified`` / ``is_user_defined``), keyed by
  its identity (``keymaps.<keymap>.<operator>#<n>``, n its place among that keymap's changed items of that operator,
  never its index among all items, which a fixed default keymap shifts): event, modifiers, active and its operator's
  properties (a menu's name, extend, toggle, ...).
Nothing is left out unless ``EXCLUDED`` names it with the control run that justifies it; the receipt lists them.
Every STRING value (add-on preferences' included, and a keymap item's string operator properties) and every value
that can hold a credential (an RNA PASSWORD property, or a name such as ``access_token``, ``auth_token``, ``api_key``)
is compared by its SHA-256 only (``sha256:<hex>``): the comparison needs only equality, so no reading ever holds a
string a person typed (a key, a token, a URL with a password; break-it review r5 S1: a name list alone missed
``openai_key``, ``license_key`` and credentials inside URLs).
Every path the walker could not read is listed under ``UNREADABLE`` (a family lost silently would compare nothing).
Each keymap a person changed also records its item count (``keymaps.<km>[]``) and the default items it no longer has
(``keymaps.<km>.missing``: a shortcut they removed), from the default keyconfig."""

import hashlib
import json
import re

SECRET = re.compile(r"token|secret|passw|api_?key|credential|cookie|bearer", re.I)
UNREADABLE = "<unreadable>"
DEPTH = 8   # deep enough for a theme's space > region > colour; ID data is never followed (none is a preference)
# {name prefix: why}: a prefix is added only with the 2.0.0 -> 2.0.0 control's diff as its reason; the receipt lists them.
EXCLUDED = {"is_dirty": "runtime 'unsaved changes' flag (U.runtime.is_dirty), never written to userpref.blend; it differed between two window launches of the published 2.0.0 (control 2026-11-20, 2.0.0 -> 2.0.0, gate b1bf6bbe940: its only difference of 6091 values)"}
KEYMAP_ITEM = ("idname", "type", "value", "any", "shift", "ctrl", "alt", "oskey", "hyper", "key_modifier", "active",
               "map_type", "repeat", "direction")


def _plain(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    try:
        return [_plain(item) for item in value]   # bpy arrays (colours, vectors)
    except TypeError:
        return repr(value)


def _is_id(struct):
    return hasattr(struct, "users") and hasattr(struct, "name_full")


def _keys(items):
    """Each collection item's key: its name when the names are present and unique, else its index."""
    names = [getattr(item, "name", None) for item in items]
    if all(isinstance(name, str) and name for name in names) and len(set(names)) == len(names):
        return names
    return [str(index) for index in range(len(items))]


def digest(value):
    """``sha256:<hex>`` of a value a reading must not hold: compared by equality, never kept."""
    return "sha256:" + hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _is_secret(prop):
    return getattr(prop, "subtype", "") == "PASSWORD" or bool(SECRET.search(prop.identifier))


def _unreadable(found, key):
    found.setdefault(UNREADABLE, []).append(key)


def _walk(struct, path, depth, found, seen):
    pointer = getattr(struct, "as_pointer", None)
    # A struct embedded at its parent's start shares its address (a theme space's generic colours): its type tells it apart.
    marker = (pointer() if callable(pointer) else id(struct), struct.bl_rna.identifier)
    if marker in seen or depth > DEPTH or _is_id(struct):
        return
    seen.add(marker)
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type":
            continue
        key = path + name
        if any(key.startswith(prefix) for prefix in EXCLUDED):
            continue
        if prop.type == "POINTER":
            try:
                child = getattr(struct, name, None)
            except (AttributeError, TypeError, ValueError):
                _unreadable(found, key)
                continue
            if child is not None and hasattr(child, "bl_rna"):
                _walk(child, key + ".", depth + 1, found, seen)
        elif prop.type == "COLLECTION":
            try:
                items = list(getattr(struct, name))
            except (AttributeError, TypeError, ValueError):
                _unreadable(found, key)
                continue
            found[key + "[]"] = len(items)
            for item_key, item in zip(_keys(items), items):
                if hasattr(item, "bl_rna"):
                    _walk(item, "%s[%s]." % (key, item_key), depth + 1, found, seen)
        elif not prop.is_readonly:
            try:
                value = getattr(struct, name)
            except (AttributeError, TypeError, ValueError):
                _unreadable(found, key)
                continue
            found[key] = digest(value) if prop.type == "STRING" or _is_secret(prop) else _plain(value)


FAMILIES = {"themes": "themes[", "keymaps": "keymaps.", "angelo_settings": "addons.angelo_agent.",
            "asset_libraries": "filepaths.asset_libraries["}


def families(found):
    """How many values of each family a reading holds: a walker or environment that silently lost one shows here."""
    return {name: sum(1 for key in found if key.startswith(prefix)) for name, prefix in FAMILIES.items()}


def _properties(item):
    properties = getattr(item, "properties", None)
    if properties is None:
        return {}
    try:
        names = list(properties.keys())
    except (AttributeError, TypeError):
        return {}
    found = {}
    for name in sorted(names):
        value = getattr(properties, name, None)
        found[name] = digest(value) if isinstance(value, str) else _plain(value)
    return found


def identity(item):
    """A keymap item as a person sees it: operator, event, modifiers and its operator's properties."""
    return "%s %s" % (json.dumps([_plain(getattr(item, field, None)) for field in KEYMAP_ITEM if field != "active"]),
                      json.dumps(_properties(item), sort_keys=True))


def _missing(keymap, default):
    """The default items of ``keymap`` its user version no longer has (a removed shortcut), by identity."""
    have = {}
    for item in keymap.keymap_items:
        have[identity(item)] = have.get(identity(item), 0) + 1
    gone = []
    for item in getattr(default, "keymap_items", ()):
        key = identity(item)
        if have.get(key, 0) > 0:
            have[key] -= 1
        else:
            gone.append(key)
    return sorted(gone)


def unreadable(found):
    return list(found.get(UNREADABLE, []))


def values(preferences, keyconfig=None, default_keyconfig=None):
    """``{"view.show_tooltips_python": True, ..., "addons.<module>.<pref>": ..., "keymaps.<km>.<op>#<n>": [...]}``."""
    found = {"addons": sorted(addon.module for addon in getattr(preferences, "addons", ())), UNREADABLE: []}
    _walk(preferences, "", 0, found, set())
    for addon in getattr(preferences, "addons", ()):
        own = getattr(addon, "preferences", None)
        if own is not None and hasattr(own, "bl_rna"):
            _walk(own, "addons.%s." % addon.module, 0, found, set())
    defaults = {keymap.name: keymap for keymap in (getattr(default_keyconfig, "keymaps", ()) if default_keyconfig else ())}
    for keymap in (getattr(keyconfig, "keymaps", ()) if keyconfig is not None else ()):
        if getattr(keymap, "is_user_modified", False):
            found["keymaps.%s[]" % keymap.name] = len(keymap.keymap_items)
            found["keymaps.%s.missing" % keymap.name] = _missing(keymap, defaults.get(keymap.name))
        seen = {}
        for item in keymap.keymap_items:
            if not (getattr(item, "is_user_modified", False) or getattr(item, "is_user_defined", False)):
                continue
            seen[item.idname] = seen.get(item.idname, -1) + 1
            found["keymaps.%s.%s#%d" % (keymap.name, item.idname, seen[item.idname])] = (
                [_plain(getattr(item, field, None)) for field in KEYMAP_ITEM] + [_properties(item)])
    return found


def differences(before, after):
    """The preference names whose value changed, appeared or went: [] when the person's preferences are as they were."""
    return sorted(name for name in set(before) | set(after) if before.get(name, ...) != after.get(name, ...))
