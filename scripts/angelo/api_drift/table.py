# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the one renamed-and-removed table from API dumps, versioning renames and the cited overlay.

Inputs (host Python, no Blender needed):

* the dumps (``dump.py``) of Blender releases up to the target, oldest first; the target is the newest;
* the socket renames in the target's source (``versioning.socket_renames``);
* ``curated.json``: rows a person wrote, each with its source (release notes, a recorded raise), for what
  no dump can prove (an operator moved to C++, guidance for a removal with no rename).

Rules, the same for every name:

* An entry is a name a script could write that an older release had and the target lacks: an attribute
  (property or function) of a struct, an enum item of a property, an operator or one of its arguments, a
  node socket, a member of a Python module.
* It names a replacement only on exact evidence: a versioning rename; an enum item of the same property
  with the same stored value, or the same UI name or description, that appeared when the old one went; a
  property or function of the same struct, of the same kind, with the same non-empty UI name or
  description, that appeared when the old one went; or a curated row. Exactly one candidate, or none is
  named. A wrong replacement would mislead, so anything else says only that Blender removed the name.
* The release it went in is named only when the release before it was dumped too; otherwise the sentence
  says between which dumped releases.
* ``certain`` (the pre-run refusal and the result note may use the row) is true only for curated rows and
  for rows a replay of recorded scripts has cleared (``replay_cleared`` in the overlay). Error sentences use
  every row: they only explain a failure that already happened.
"""

import json
from pathlib import Path

TABLE_SCHEMA = 1
# Every Blender release from 3.6 on, for naming the release a name went in.
RELEASES = ("3.6", "4.0", "4.1", "4.2", "4.3", "4.4", "4.5", "5.0", "5.1", "5.2", "5.3")
USES = ("attribute", "enum_item", "operator", "operator_argument", "socket", "module_member")


def release_key(version):
    major, minor = version.split(".")[:2]
    return int(major), int(minor)


def load_dumps(paths):
    dumps = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
    return sorted(dumps, key=lambda dump: release_key(dump["version"]))


def gone_phrase(last, since, dumped):
    """``Blender 5.0`` when the release before ``since`` was dumped, else the dumped bracket."""
    index = RELEASES.index(since) if since in RELEASES else -1
    if index > 0 and RELEASES[index - 1] == last and last in dumped:
        return "Blender %s" % since
    return "A Blender release after %s (by %s)" % (last, since)


# --- Collecting names per release --------------------------------------------------------------------


def struct_members(dump):
    """``{(struct, name): record}`` of every property and function, with ``kind``."""
    members = {}
    for struct, record in dump["structs"].items():
        for name, prop in record.get("properties", {}).items():
            members[(struct, name)] = dict(prop, kind="property")
        for name, function in record.get("functions", {}).items():
            members[(struct, name)] = dict(function, kind="function")
    return members


def enum_items(dump):
    """``{(struct, property): {identifier: [value, name, description]}}`` for static enum items."""
    items = {}
    for struct, record in dump["structs"].items():
        for name, prop in record.get("properties", {}).items():
            if prop.get("type") == "ENUM" and prop.get("items"):
                items[(struct, name)] = {item[0]: item[1:] for item in prop["items"]}
    return items


def node_sockets(dump):
    """``{(node, side): {name: identifier}}``; a name that appears twice keeps its first identifier."""
    sockets = {}
    for node, record in dump["nodes"].items():
        for side in ("inputs", "outputs"):
            names = {}
            for name, identifier in record.get(side, []):
                names.setdefault(name, identifier)
            sockets[(node, side)] = names
    return sockets


def module_members(dump):
    members = set()
    for module, record in dump["modules"].items():
        for name, attributes in record.get("members", {}).items():
            members.add((module, name))
            for attribute in attributes or ():
                members.add((module + "." + name, attribute))
    return members


# --- Entries ---------------------------------------------------------------------------------------


def _single(candidates):
    return candidates[0] if len(candidates) == 1 else None


def member_replacement(old_record, new_struct_members, older_struct_members):
    """The one member that appeared when ``old_record`` went, of its kind, with its UI name or description."""
    appeared = {name: record for name, record in new_struct_members.items() if name not in older_struct_members}
    candidates = []
    for name, record in appeared.items():
        if record["kind"] != old_record["kind"] or record.get("type") != old_record.get("type"):
            continue
        for field in ("name", "description"):
            if old_record.get(field) and record.get(field) == old_record.get(field):
                candidates.append(name)
                break
    return _single(sorted(set(candidates)))


def enum_replacement(old_item, new_items, older_items):
    appeared = {key: value for key, value in new_items.items() if key not in older_items}
    candidates = [key for key, (value, name, description) in appeared.items()
                  if value == old_item[0] or (old_item[1] and name == old_item[1])
                  or (old_item[2] and description == old_item[2])]
    return _single(sorted(candidates))


def removed_entries(dumps):
    """Every name of an older dump absent from the target dump, with the dumped releases bracketing it."""
    target = dumps[-1]
    dumped = [dump["version"] for dump in dumps]
    target_members = struct_members(target)
    target_member_names = {name for _struct, name in target_members}
    target_enums = enum_items(target)
    target_sockets = node_sockets(target)
    target_modules = module_members(target)
    by_struct = {}
    for (struct, name), record in target_members.items():
        by_struct.setdefault(struct, {})[name] = record
    found = {}

    def add(key, entry):
        found.setdefault(key, entry)  # The oldest dump that had it decides nothing but "last" below.
        found[key]["last"] = entry["last"]
        found[key]["since"] = entry["since"]

    for index, dump in enumerate(dumps[:-1]):
        later = dumps[index + 1]
        last, since = dump["version"], later["version"]
        members, later_members = struct_members(dump), struct_members(later)
        for (struct, name), record in members.items():
            if struct not in target["structs"] or (struct, name) in target_members:
                continue
            if (struct, name) in later_members:
                continue  # Went in a later release; that pair records it.
            later_struct = {n: r for (s, n), r in later_members.items() if s == struct}
            older_struct = {n for (s, n) in members if s == struct}
            replacement = member_replacement(record, later_struct, older_struct)
            if replacement and (struct, replacement) not in target_members:
                replacement = None
            add(("attribute", struct, None, name),
                {"use": "attribute", "owner": struct, "property": None, "name": name, "last": last, "since": since,
                 "replacements": [replacement] if replacement else [],
                 "evidence": "dump" if replacement else None,
                 "elsewhere": name in target_member_names})
        enums, later_enums = enum_items(dump), enum_items(later)
        for (struct, prop), items in enums.items():
            if (struct, prop) not in target_enums:
                continue
            for identifier, item in items.items():
                if identifier in target_enums[(struct, prop)] or identifier in later_enums.get((struct, prop), {}):
                    continue
                replacement = enum_replacement(item, later_enums.get((struct, prop), {}), items)
                if replacement and replacement not in target_enums[(struct, prop)]:
                    replacement = None
                add(("enum_item", struct, prop, identifier),
                    {"use": "enum_item", "owner": struct, "property": prop, "name": identifier, "last": last,
                     "since": since, "replacements": [replacement] if replacement else [],
                     "evidence": "dump" if replacement else None, "elsewhere": False})
        for operator, record in dump["operators"].items():
            if operator in later["operators"]:
                for argument in record["properties"]:
                    if (argument not in later["operators"][operator]["properties"]
                            and operator in target["operators"]
                            and argument not in target["operators"][operator]["properties"]):
                        add(("operator_argument", operator, None, argument),
                            {"use": "operator_argument", "owner": operator, "property": None, "name": argument,
                             "last": last, "since": since, "replacements": [], "evidence": None,
                             "elsewhere": False})
            elif operator not in target["operators"]:
                add(("operator", None, None, operator),
                    {"use": "operator", "owner": None, "property": None, "name": operator, "last": last,
                     "since": since, "replacements": [], "evidence": None, "elsewhere": False})
        sockets, later_sockets = node_sockets(dump), node_sockets(later)
        for (node, side), names in sockets.items():
            if (node, side) not in target_sockets:
                continue
            current = target_sockets[(node, side)]
            for name in names:
                if name in current or name in current.values() or name in later_sockets.get((node, side), {}):
                    continue  # Still a key (by name or identifier), or went later.
                add(("socket", node, side, name),
                    {"use": "socket", "owner": node, "property": side, "name": name, "last": last, "since": since,
                     "replacements": [], "evidence": None, "elsewhere": False,
                     "node_name": target["nodes"][node]["name"]})
        modules, later_modules = module_members(dump), module_members(later)
        for module, name in modules:
            if (module, name) in target_modules or (module, name) in later_modules:
                continue
            add(("module_member", module, None, name),
                {"use": "module_member", "owner": module, "property": None, "name": name, "last": last,
                 "since": since, "replacements": [], "evidence": None, "elsewhere": False})
    for entry in found.values():
        entry["dumped"] = dumped
    return found


def _dump_before(dumps, release):
    """The newest dump of a release older than ``release``, or None."""
    older = [dump for dump in dumps or () if release_key(dump["version"]) < release_key(release)]
    return older[-1] if older else None


def apply_versioning(found, renames, target, dumps=None):
    """Socket renames from versioning: chains across releases followed to the target's name.

    A call that renames on both sides (``version_node_socket_name``) does not say which side had the old name,
    so each side is kept only when the dump of the release before shows the old name there; with no such dump,
    neither is. With that dump, a one-sided rename is kept only when the old name was there too (a name that
    existed only between two development builds is never stated as renamed).
    """
    target_sockets = node_sockets(target) if target else {}
    by_old = {}
    for rename in renames:
        if rename["node"] is None:
            continue
        before = _dump_before(dumps, rename["release"])
        before_sockets = node_sockets(before) if before else None
        sides = ("inputs", "outputs") if rename["side"] == "both" else (rename["side"],)
        for side in sides:
            if before_sockets is None:
                if rename["side"] == "both":
                    continue
            else:
                names = before_sockets.get((rename["node"], side), {})
                if rename["old"] not in names and rename["old"] not in names.values():
                    continue
            by_old[(rename["node"], side, rename["old"])] = rename
    for (node, side, old), rename in by_old.items():
        new, seen = rename["new"], {old}
        while (node, side, new) in by_old and new not in seen:  # 4.0 X -> Y, then 4.2 Y -> Z: X -> Z.
            seen.add(new)
            new = by_old[(node, side, new)]["new"]
        current = target_sockets.get((node, side))
        if current is not None and (old in current or old in current.values()):
            continue  # The old name is a key again (or an identifier): not an error to explain.
        if current is not None and new not in current:
            continue  # The chain does not end at a socket the target has: name no replacement from it.
        key = ("socket", node, side, old)
        entry = found.setdefault(key, {"use": "socket", "owner": node, "property": side, "name": old,
                                       "last": None, "since": rename["release"], "elsewhere": False,
                                       "node_name": (target or {}).get("nodes", {}).get(node, {}).get("name")})
        entry["replacements"] = [new]
        entry["evidence"] = "versioning"
        entry["since"] = rename["release"]
    return found


def hint(entry):
    """The sentence a failed use gains (after ". "); the curated sentence when there is one."""
    if entry.get("hint"):
        return entry["hint"]
    dumped = entry.get("dumped", ())
    if entry.get("evidence") == "versioning" or entry.get("last") is None:
        when = "Blender %s" % entry["since"]
    else:
        when = gone_phrase(entry["last"], entry["since"], dumped)
    new = entry["replacements"][0] if len(entry.get("replacements", ())) == 1 else None
    use, owner, name = entry["use"], entry.get("owner"), entry["name"]
    if use == "socket":
        side = "input" if entry["property"] == "inputs" else "output"
        node = entry.get("node_name") or owner
        return ("%s renamed the %s %s '%s' to '%s'" % (when, node, side, name, new) if new
                else "%s removed the %s %s '%s'" % (when, node, side, name))
    if use == "enum_item":
        return ("%s renamed '%s' to '%s'" % (when, name, new) if new
                else "%s removed '%s' from %s.%s" % (when, name, owner, entry["property"]))
    if use == "attribute":
        return ("%s renamed '%s' to '%s'" % (when, name, new) if new
                else "%s removed %s.%s" % (when, owner, name))
    if use == "operator":
        return ("%s replaced bpy.ops.%s with bpy.ops.%s" % (when, name, new) if new
                else "%s removed bpy.ops.%s" % (when, name))
    if use == "operator_argument":
        return "%s removed the argument '%s' of bpy.ops.%s" % (when, name, owner)
    return "%s removed %s.%s" % (when, owner, name)


def merge_curated(found, curated):
    """Curated rows replace a generated row's hint and replacements (and add rows no dump shows)."""
    cleared = {tuple(key) for key in curated.get("replay_cleared", ())}
    for row in curated["rows"]:
        key = (row["use"], row.get("owner"), row.get("property"), row["name"])
        entry = found.setdefault(key, {"use": row["use"], "owner": row.get("owner"),
                                       "property": row.get("property"), "name": row["name"], "last": None,
                                       "since": row.get("since"), "elsewhere": row.get("elsewhere", False)})
        entry.update({"replacements": row.get("replacements", entry.get("replacements", [])),
                      "evidence": "curated", "source": row["source"]})
        for field in ("hint", "refusal", "note", "elsewhere", "on"):
            if field in row:
                entry[field] = row[field]
        entry["certain"] = True
    for key, entry in found.items():
        entry.setdefault("certain", key in cleared)
    return found


def build(dumps, renames, curated):
    """The table: ``{"schema", "target", "dumped", "entries": [...]}`` sorted, each entry with its hint."""
    found = removed_entries(dumps) if dumps else {}
    found = apply_versioning(found, renames, dumps[-1] if dumps else None, dumps)
    found = merge_curated(found, curated)
    entries = []
    for key in sorted(found, key=lambda key: tuple("" if part is None else str(part) for part in key)):
        entry = found[key]
        entry.setdefault("replacements", [])
        entry["hint"] = hint(entry)
        entry.pop("dumped", None)
        entries.append({field: entry[field] for field in sorted(entry) if entry[field] is not None})
    return {"schema": TABLE_SCHEMA, "target": dumps[-1]["version"] if dumps else curated["target"],
            "dumped": [dump["version"] for dump in dumps], "entries": entries}


# --- The C++ rows ----------------------------------------------------------------------------------

CPP_USES = {"enum_item": "EnumItem", "attribute": "Attribute", "socket": "CollectionKey"}
CPP_HEADER = """/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* GENERATED by scripts/angelo/api_drift/cli.py render from
 * scripts/addons_core/angelo_agent/assets/api_drift.json. Do not edit: change the dumps or
 * scripts/angelo/api_drift/curated.json and render again (test_native_api_drift.py checks it). */

/* clang-format off */
"""


def c_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_cpp(table):
    """One ``RenamedName`` row per entry Blender's own errors can name: enum items, attributes, sockets."""
    lines = [CPP_HEADER.rstrip("\n")]
    for entry in table["entries"]:
        use = CPP_USES.get(entry["use"])
        if use is None or not entry.get("owner"):
            continue
        prop = entry.get("property")
        lines.append("    {BPyRenamedUse::%s, %s, %s, %s,\n     %s}," % (
            use, c_string(entry["owner"]), c_string(prop) if prop else "nullptr", c_string(entry["name"]),
            c_string(entry["hint"])))
    lines.append("/* clang-format on */")
    return "\n".join(lines) + "\n"
