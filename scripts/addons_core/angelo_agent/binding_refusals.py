# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Refusals of inferred bindings: text where an object handle belongs, and a handle with no revision to bind.

``not_a_handle``: fast tiers targeted objects by name (doc/angelo/fast_tier_reliability.rst); "look it up"
would cost them a round without helping, so the refusal names the fix.

An inferred binding (``identifiers``) binds the revision the context's latest observation showed. When there
is none the program is refused, and the refusal used to say only "revision_omitted says why; OVER_LIMIT is
angelo_execute_python's ... from a lookup". Recorded (GPT 6.1 Sol): the scene
settings' revision had been left out for the host's own read budget, the refusal named OVER_LIMIT, and the
model's lookup (angelo_inspect_scene) returns no revision at all; earlier one-shot trials
met the same text for objects listed without a revision or only in the change feed.

``omission`` records, per observation row, why it has no revision; ``refusal`` states that reason and, when a
program can bind the object at all, the one lookup that returns its revision: ``angelo_scene_object`` for an
object (its ``object_revision``), ``angelo_scene_settings`` for the bound scene (its ``revision``). An object
over the typed programs' limits, or whose revision cannot be read, is named as such with the tool that can edit
it instead. Pure Python; facts only.
"""

# A row listed without a revision and without revision_omitted: the turn context offers revisions only to some rows.
NOT_OFFERED = "NOT_OFFERED"
_KEYS = ("reason", "detail", "counts")
SCENE_LOOKUP = "angelo_scene_settings returns the scene's revision"
OBJECT_LOOKUP = 'angelo_scene_object {"object":"%s"} returns its object_revision'
SCRIPT = "angelo_execute_python can edit it"
# An undeclared dependency (a modifier's or constraint's object) that is not in the bound scene: no lookup returns its
# revision (object_revision reads only the bound scene), so no program can bind it.
OUTSIDE_SCENE = ("%r, a parent, modifier, constraint, driver or camera focus object of an input, is not in the bound "
                 "scene, so no lookup returns its revision and no program can bind it; angelo_execute_python can edit "
                 "them")


def _why(key):
    from .turn_context import MAX_SELECTED_REVISIONS, SMALL_SCENE_OBJECTS
    return {
        NOT_OFFERED: ("it is listed without one: the observation gives revisions to the active object, objects the "
                      "message names, up to %d selected, recently changed objects and every object of a scene of at "
                      "most %d" % (MAX_SELECTED_REVISIONS, SMALL_SCENE_OBJECTS)),
        "BUDGET": "the host's read time for that observation ran out before it",
        "selected_revision_count": ("more objects are selected than the observation gives revisions (%d)"
                                    % MAX_SELECTED_REVISIONS),
    }.get(key)


def omission(entry):
    """Why the observation row ``entry`` shows no revision (a small dict), or None when it shows one."""
    if not isinstance(entry, dict) or isinstance(entry.get("object_revision"), str):
        return None
    omitted = entry.get("revision_omitted")
    if not isinstance(omitted, dict):
        return {"reason": NOT_OFFERED}
    return {key: str(omitted[key])[:200] for key in _KEYS if omitted.get(key) is not None}


def refusal(short, omitted, scene=False):
    """The refusal text for an inferred binding of ``short`` (o<n>) without an observed revision."""
    omitted = omitted if isinstance(omitted, dict) else {}
    reason, detail = omitted.get("reason"), str(omitted.get("detail") or "")
    if reason == "OVER_LIMIT" and detail == "program_input_mesh_limits":
        from .action_schema_types import OVER_LIMIT_NOTE
        return ("%s is over the typed programs' mesh input limits (%s), so no program can bind it; %s"
                % (short, omitted.get("counts") or "see revision_omitted", OVER_LIMIT_NOTE))
    if reason == "UNSUPPORTED" or (reason == "BUDGET" and "original_state_work_budget" in detail):
        return "%s's revision cannot be read (%s), so no program can bind it; %s" % (short, detail or reason, SCRIPT)
    why = _why(detail) or _why(reason) or "it is not one of its listed objects"
    lookup = SCENE_LOOKUP if scene else OBJECT_LOOKUP % short
    return ("%s has no revision in your latest observation (%s): %s; declare it in read_set or write_set"
            % (short, why, lookup))


def not_a_handle(value):
    """The refusal text for a name (or other text) where an object handle belongs, naming the fix."""
    text = "".join(character if " " <= character else " " for character in str(value))
    return ("'%s' is not an object handle: target an existing object by its o<n> handle from the observation, never "
            "its name, and an object this program creates by its $alias"
            % (text if len(text) <= 64 else text[:63] + "…"))
