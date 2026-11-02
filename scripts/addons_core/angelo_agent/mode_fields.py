# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which fields each mode of a flat-schema kind takes: stated and refused from the one table. No bpy.

Several kinds publish one flat parameter object for all of their modes (keyframe_edit's operation, action_set's
mode, a light's light_type...), so every field there reads as open to every mode, while the validator refuses a
field another mode owns. A model that follows the schema writes action_set ASSIGN with a name, is refused, and loses
a correction round (1.0.7 bug sweep). The same class was fixed for the job tool (job_spec.fields_by_kind).

A table maps each mode to ``(required, optional)`` field names; ``common`` fields are taken by every mode. The
validator reads the table through ``problem`` and the model-facing statement is ``statement`` of the same table
(kind_notes puts it where each model reads the kind), so the two cannot drift; the refusal names the fields the
mode takes, so one correction converges. Required means given with a value other than the field's default.
"""


def _listed(table, value, common=()):
    required, optional = table[value]
    return [name + "*" for name in required] + list(optional) + list(common)


def statement(subject, key, table, common=False, extra=""):
    """``<subject> by <key> (* required; any other is refused): A: x* y; B/C: none.``

    Modes with the same fields share an entry. ``common``: the kind also has fields every mode takes, left
    unlisted: the statement then says another mode's field is refused and the rest apply to all. ``extra``: a
    further sentence made from the same table's owner.
    """
    entries, seen = [], {}
    for value in table:
        listed = " ".join(_listed(table, value)) or "none"
        if listed in seen:
            entries[seen[listed]][0].append(value)
        else:
            seen[listed] = len(entries)
            entries.append(([value], listed))
    marked = "* required; " if any(required for required, _ in table.values()) else ""
    refused = "another's is refused; the rest apply to all" if common else "any other is refused"
    text = "%s by %s (%s%s): " % (subject, key, marked, refused)
    text += "; ".join("/".join(values) + ": " + listed for values, listed in entries)
    return text + "." + (" " + extra if extra else "")


def problem(value, table, given, present=None, common=()):
    """The refusal text when ``value`` is given a field it doesn't take or lacks one it needs, else None.

    ``given``: the fields set to something other than their default, besides the mode's own key. ``present``:
    what counts as supplying a required field, when that differs from ``given`` (a required field that may be
    null). The text names everything the mode takes, from the same table as the statement.
    """
    required, optional = table[value]
    foreign = [name for name in given if name not in required and name not in optional and name not in common]
    supplied = given if present is None else present
    missing = [name for name in required if name not in supplied]
    if not foreign and not missing:
        return None
    parts = (["takes no " + ", ".join(sorted(foreign))] if foreign else []) + (
        ["needs " + ", ".join(missing)] if missing else [])
    return "%s %s; %s takes %s" % (value, " and ".join(parts), value, " ".join(_listed(table, value, common)) or "none")


def refuse_unset(kind, key, table, params):
    """Raise ValueError naming what the mode takes, for a kind whose mode-owned fields are null when not given."""
    owned = sorted({name for required, optional in table.values() for name in required + optional})
    refused = problem(params[key], table, [name for name in owned if params.get(name) is not None])
    if refused:
        raise ValueError(kind + " " + refused)
