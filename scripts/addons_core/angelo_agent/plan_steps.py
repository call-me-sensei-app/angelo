# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A plan's provisional steps for the live row while the plan is written: grouped, carried, read back.

A typed plan shows nothing of what it will do until the model has written all
of it (9-10 s of a recorded one-shot footbridge, after its
thinking). ``plan_progress.PlanProgress`` already reads each step from the
plan's text as it streams; here those steps become the live row's rows under
"Preparing the next steps": "Post ×4", "Rail ×2", "Grey Stone", a script's own
description, each with a "Planned" chip (``chat_rows``), never a done one.

They are carried on the status text, after its detail (``encode``, read back by
``rows``), so they live exactly as long as that status: the next status (the
plan arrived and the round ended, the runtime refused it, a later plan call
started over, Stop, the next request) replaces it and they are gone. Nothing
here is stored, saved, handed off or sent to a model, and nothing decides what
runs: a step becomes a real row only when the host runs it.

Grouping is presentation: consecutive steps of one family whose names differ
only in a last variant word (``Post_FL``, ``Post_FR``; ``Rail Front``,
``Rail Back``; ``Lamp 01``) are counted under their shared stem, and a
``batch`` of one created unit is counted on that unit's row once the batch is
complete ("Post ×4", ``plan_progress``); a batch of several sources shows on
its own, by its first unit's name. Names are the model's own, shown as data
and never translated.

No ``bpy``: host-testable.
"""

import re

from .plan_progress import ROWS, SCRIPT_TOOL, SEPARATOR
from .scene_binding import TARGETLESS_KINDS

FIELD = "\x1d"  # Between a row's fields.
SHOWN = 5  # Rows the live row draws; with more groups, SHOWN - 1 rows and a count of the steps after them.
NAMED_TOOLS = frozenset({"angelo_create_cube"})  # A tool of its own that adds a named object.
DELETES = frozenset({"object_delete", "mesh_delete", "modifier_remove", "keyframe_delete", "fcurve_modifier"})
# Last words that tell copies of one thing apart (besides numbers and one- or two-letter tags such as FL or A).
VARIANTS = frozenset({"left", "right", "front", "back", "rear", "top", "bottom", "upper", "lower", "inner", "outer",
                      "north", "south", "east", "west", "near", "far"})
_LAST = re.compile(r"(.+?)[\s_.\-]+([^\s_.\-]+)")
_NUMBERED = re.compile(r"(.*[^\d\s_.\-])\d+")


def family(kind):
    """The kind of work a step does, for its icon (``chat_rows.kind_icon``)."""
    if kind == SCRIPT_TOOL:
        return "script"
    if kind in TARGETLESS_KINDS or kind in NAMED_TOOLS:
        return "create"
    if kind.startswith("material"):
        return "material"
    return "delete" if kind in DELETES else "edit"


def stem(name):
    """What copies of one thing share ("Post" of "Post_FL"), or None when the name is not one of a set."""
    if not name:
        return None
    match = _LAST.fullmatch(name)
    if match:
        last = match.group(2)
        return match.group(1) if last.isdigit() or len(last) <= 2 or last.lower() in VARIANTS else None
    match = _NUMBERED.fullmatch(name)
    return match.group(1) if match else None


def groups(steps):
    """``[(family, kind, label or None, count)]``: runs of copies of one thing counted under their stem.

    ``steps`` are ``PlanProgress.steps``; its last step is left out while it waits for a name that has not
    arrived yet, so a row never shows a kind and then changes to the name a moment later.
    """
    settled = [step for index, step in enumerate(steps)
               if (step[1] is not None or not step[2] or index < len(steps) - 1) and not _folded(step)]
    out = []
    for step in settled:
        kind, name, count = step[0], step[1], _count(step)
        kin, root = family(kind), stem(name)
        last = out[-1] if out else None
        if last and last[0] == kin and ((root and last[4] == root)
                                        or (name is None and last[2] is None and last[1] == kind)):
            last[3] += count
        else:
            out.append([kin, kind, name, count, root])
    return [(kin, kind, (name if count == 1 else root or name), count) for kin, kind, name, count, root in out]


def _count(step):
    """How many objects a step makes: a batch's instances counted on its unit (plan_progress)."""
    return step[3] if len(step) > 3 and type(step[3]) is int and step[3] > 0 else 1


def _folded(step):
    return len(step) > 4 and bool(step[4])


def encode(progress, shown=SHOWN):
    """The provisional rows of a ``PlanProgress`` as text for ``plan_progress.status`` ("" when there are none)."""
    found, rest = groups(progress.steps), progress.more_steps
    if len(found) > shown:
        rest += sum(count for _, _, _, count in found[shown - 1:])
        found = found[:shown - 1]
    rows = "".join(ROWS + FIELD.join((kin, str(count), name or "", kind)) for kin, kind, name, count in found)
    return rows + (ROWS + "+%d" % rest if rest and found else "")


def rows(status):
    """``([(family, kind, label or None, count)], more)`` carried by a status text; ``([], 0)`` for any other."""
    found, more = [], 0
    for part in str(status).partition(SEPARATOR)[2].split(ROWS)[1:]:
        if part.startswith("+") and part[1:].isdigit():
            more = int(part[1:])
            continue
        fields = part.split(FIELD)
        if len(fields) == 4 and fields[1].isdigit():
            found.append((fields[0], fields[3], fields[2] or None, int(fields[1])))
    return found, more
