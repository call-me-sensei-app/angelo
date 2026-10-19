# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private names for staged copies, and the public names commit gives back what it keeps.

Blender names a copy with the lowest free suffix, so a staged copy of the live
"Wood" held "Wood.001", a name the live file leaves free. A material a program
asked for by that name was refused NAME_TAKEN, and a datablock made in the
stage skipped the number Blender would give ("Cube.002" for a duplicate's
mesh where Blender gives "Cube.001"). Every copy staging makes of a live ID
therefore takes a private name (``PREFIX``), recorded in ``stage.extras`` with
the name of the ID it copies; a copy a kernel makes of a staged copy inherits
the prefix. Lookups never use these names: a staged material, node group or
World stands for its live ID by the session UID it carries.

At commit, ``publish`` gives every staged ID the commit keeps its public name
(Blender adds the lowest suffix the live file leaves free), renames Action
slots and AnimData slot hints named after a private copy, and journals each
rename for rollback, so no private name reaches the file. Results and check
rows show the public name (``public``). Pure Python apart from the RNA it is
handed; no bpy import.
"""

PREFIX = "__angelo_clone_"
NAMES = "public_names"  # stage.extras: private name -> public name of the ID the copy stands for.
STAGE = "stage_name_id"  # stage.extras: the stage's short id, part of every private name.
RESERVED = "reserved_names"  # stage.extras: private name -> the exact name commit gives the kept copy.
CHANGED = "published_names"  # stage.extras: a reserved name -> the different name commit had to give.
FIXES = "_name_fixes"  # Private key of a commit's result: names the staged results gave that commit changed.


def hide(extras, block, public_name):
    """Give a staged copy its private name; ``public_name`` is the name of the live ID it copies."""
    block.name = PREFIX + extras.get(STAGE, "stage")[:8] + "_" + public_name
    extras.setdefault(NAMES, {})[block.name] = public_name
    return block


def public(extras, name):
    """The public name a private (or private-derived, "<private>.001") name stands for; any other name unchanged."""
    if not isinstance(name, str) or not name.startswith(PREFIX):
        return name
    names = extras.get(NAMES, {})
    if name in names:
        return names[name]
    base, dot, digits = name.rpartition(".")
    if dot and digits.isdigit() and base in names:
        return names[base]
    rest = name[len(PREFIX):]  # A name Blender truncated: drop the stage id.
    return rest.split("_", 1)[1] if "_" in rest else rest


def mentions(value):
    """Whether a result value names a private copy anywhere."""
    if isinstance(value, dict):
        return any(mentions(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(mentions(item) for item in value)
    return isinstance(value, str) and PREFIX in value


def shown(extras, value):
    """``value`` with every private name replaced by its public name (results and check rows)."""
    if isinstance(value, dict):
        return {key: shown(extras, item) for key, item in value.items()}
    if isinstance(value, list):
        return [shown(extras, item) for item in value]
    if isinstance(value, str) and PREFIX in value:
        if value.startswith(PREFIX):
            return public(extras, value)
        head, _, tail = value.partition(PREFIX)  # A slot identifier: a two-letter ID code, then the name.
        return head + public(extras, PREFIX + tail)
    return value


def reserve(extras, block, taken):
    """The exact name commit will give a kept private copy (an isolated material, a shared Action's copy),
    reserved now so results can name it: its public name, else that name with the lowest suffix no ID holds
    (``taken(name)``) and no other reservation has. Any other block keeps its own name."""
    if not block.name.startswith(PREFIX):
        return block.name
    reserved = extras.setdefault(RESERVED, {})
    if block.name in reserved:
        return reserved[block.name]
    wanted = public(extras, block.name)
    used = set(reserved.values())
    if taken(wanted) or wanted in used:
        stem, dot, digits = wanted.rpartition(".")
        stem = stem if dot and digits.isdigit() else wanted
        number = 1
        while taken("%s.%03d" % (stem, number)) or "%s.%03d" % (stem, number) in used:
            number += 1
        wanted = "%s.%03d" % (stem, number)
    reserved[block.name] = wanted
    return wanted


def correct(result):
    """Give a committed result's rows the names commit actually gave (pops ``FIXES``; ``ProgramJob.commit``).

    ``names``: a reserved name -> the name commit had to give instead (unique, so every row naming it is
    rewritten); ``moved``: per ID type, a created ID's staged name -> the name it came back under after a
    suspension (``stage_new_objects.correct_result``, its own type's fields only); ``world``: [the name the World was reported under,
    its committed name], applied only to the World rows (another row may name the live World the copy replaced).
    """
    fixes = result.pop(FIXES, None)
    if not fixes:
        return result
    names = fixes.get("names") or {}

    def fix(value):
        if isinstance(value, dict):
            return {key: fix(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fix(item) for item in value]
        return names.get(value, value) if isinstance(value, str) else value

    for key in ("operation_results", "delta", "checks"):
        if names and key in result:
            result[key] = fix(result[key])
    if fixes.get("moved"):  # Created IDs renamed while their stage was suspended (stage_new_objects).
        from .stage_new_objects import correct_result
        correct_result(result, fixes["moved"])
    if fixes.get("world"):
        reported, actual = fixes["world"]
        rows = [row.get("world") for row in result.get("operation_results") or () if row.get("kind") == "world_configure"]
        rows += [item.get("changes", {}).get("world", {}).get("after")
                 for item in (result.get("delta") or {}).get("details") or ()]
        for row in rows:
            if isinstance(row, dict) and row.get("name") == reported:
                row["name"] = actual
    return result


def publish(extras, blocks, journal):
    """At commit: each kept staged ID with a private name takes its public name; slots named after one are renamed.

    ``blocks`` are the staged IDs the commit keeps, in the order commit found them; ``journal`` takes
    ``(holder, attribute, previous value)`` rows that a rollback restores in reverse.
    """
    reserved = extras.get(RESERVED, {})
    blocks = sorted(blocks, key=lambda block: block.name not in reserved)  # Reserved names first: they are exact.
    for block in blocks:
        if block.name.startswith(PREFIX):
            journal.append((block, "name", block.name))
            wanted = reserved.get(block.name)
            block.name = wanted or public(extras, block.name)
            if wanted is not None and block.name != wanted:  # Taken since staging: results are corrected.
                extras.setdefault(CHANGED, {})[wanted] = block.name
        for slot in getattr(block, "slots", ()) if block.id_type == "ACTION" else ():
            if PREFIX in slot.identifier:
                journal.append((slot, "name_display", slot.name_display))
                slot.name_display = public(extras, slot.name_display)
        animation = getattr(block, "animation_data", None)
        hint = getattr(animation, "last_slot_identifier", "") if animation is not None else ""
        if PREFIX in hint:
            journal.append((animation, "last_slot_identifier", hint))
            animation.last_slot_identifier = shown(extras, hint)
