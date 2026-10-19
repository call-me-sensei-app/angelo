# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a model reads of a tool result: the host's own bookkeeping left out.

Every tool result is journaled, saved in the chat, drawn in its activity row
and handed to request receipts exactly as the executor returned it. A model
reads a copy (``model_result``) without the fields that describe the host
rather than the scene:

* timing (``native_exec_ms``, ``native_dispatch_wait_ms``, a query's
  ``query_ms``): local measurements, read by the person through
  ``angelo_agent_metrics``, never a fact about the request;
* a scene query's ``cache`` and ``budgets`` (``scene_queries.execute``):
  cache counters and the host's fixed limits of every search, detail,
  relationship and delta result. Truncation is still stated
  where it happens (``truncated``, ``next_cursor``, ``uncertain_count``);
* a completed script's consent, confinement and per-flag Undo bookkeeping
  (``trusted_scripts.script_result``, ``script_errors.undo_facts``): its
  ``undo`` sentence states the same outcome and stays, as do ``output``,
  ``result``, ``changes``, ``after`` and ``booleans``. The change list's
  ``scope`` sentence (the tool description states it) goes too, and each of
  its ``added``, ``removed`` and ``modified`` lists keeps its first
  ``MAX_CHANGED`` rows, as ``after`` keeps its objects
  (``script_after.MAX_OBJECTS``), with ``omitted`` counting the rest per list;
  ``object_count_before`` and ``object_count_after`` stay. A round-2 rebuild
  script listed about 400 objects there: a 12.2k-token result that Codex cut.
  The chat and journal keep every row.

A failed result keeps every field but timing: what became of a failed edit
is what the model must read. In a recorded heavy-castle warm run this left out
3,227 B of five script results and 1,465 B of two lookups, each written to the
prompt cache once and read in every later request (doc/angelo/performance.rst,
"Heavy-scene payloads").

``observation_follows``: the same message carries a fresh turn-context
observation (a Claude continuation built by ``turn_context``), whose scene
entry states what a script's ``after.scene_settings`` and ``verified`` (the
scene's name and object count) would; those copies are then left out, and an
``after`` with no objects with them. A Codex thread reads a script's result
inside its turn with no observation after it, and a batch or the
``ANGELO_TURN_CONTEXT=legacy`` continuation re-sends ``angelo_inspect_scene``,
which states neither, so those keep them.

A finished typed program's copy is compact (``program_model_result``): every handle, revision, check and failure,
and a short geometry row per object.

Pure dictionaries; no Blender access. The raw result is never modified.
"""

from . import program_model_result

TIMING = ("native_exec_ms", "native_dispatch_wait_ms")
QUERY_DIAGNOSTICS = ("cache", "budgets", "query_ms")
QUERY_TOOLS = frozenset({"angelo_scene_search", "angelo_scene_object", "angelo_scene_relationships",
                         "angelo_scene_delta"})
SCRIPT_TOOL = "angelo_execute_python"
SCRIPT_BOOKKEEPING = ("undoable", "main_data_undoable", "undo_scope", "image_pixels_undoable", "undo_available",
                      "external_effects_undoable", "execution_mode", "confined", "consent", "stop_interruptible",
                      "self_checks_passed", "self_checks_failed")
OBSERVED = ("verified",)  # What a following turn-context observation states (observation_follows).
CHANGE_LISTS = ("added", "removed", "modified")
MAX_CHANGED = 24   # Rows per change list in a model's copy: as a script's after (script_after.MAX_OBJECTS).


def model_result(tool, result, *, observation_follows=False):
    """A model's copy of one tool result (module docstring); ``result`` itself is unchanged."""
    if not isinstance(result, dict):
        return result
    if "normalized" in result and isinstance(result.get("results"), list):
        # A normalised lookup's slot (plan_recovery._normalized): each call's own result, read the same way.
        return dict(result, results=[model_result(tool, part, observation_follows=observation_follows)
                                     for part in result["results"]])
    dropped = set(TIMING)
    failed = bool(result.get("error")) or result.get("success") is False
    if tool in QUERY_TOOLS and not failed:
        dropped.update(QUERY_DIAGNOSTICS)
    script = tool == SCRIPT_TOOL and not failed and result.get("success") is True
    if script:
        dropped.update(SCRIPT_BOOKKEEPING)
        if observation_follows:
            dropped.update(OBSERVED)
    if not dropped & set(result) and not script:
        return program_model_result.compact(tool, result)
    output = {key: value for key, value in result.items() if key not in dropped}
    if script:
        changes = output.get("changes")
        if isinstance(changes, dict):
            output["changes"] = capped_changes(changes)
        after = output.get("after")
        if observation_follows and isinstance(after, dict):
            after = {key: value for key, value in after.items() if key != "scene_settings"}
            if after.get("objects") or after.get("omitted"):
                output["after"] = after
            else:
                output.pop("after")
    return program_model_result.compact(tool, output)  # A finished program: a short summary per object.


def capped_changes(changes):
    """A script's change list without its ``scope`` sentence, each list at most ``MAX_CHANGED`` rows, the rest
    counted in ``omitted`` (``{list: count}``)."""
    output = {key: value for key, value in changes.items() if key != "scope"}
    omitted = {key: len(output[key]) - MAX_CHANGED for key in CHANGE_LISTS
               if isinstance(output.get(key), list) and len(output[key]) > MAX_CHANGED}
    for key in omitted:
        output[key] = output[key][:MAX_CHANGED]
    if omitted:
        output["omitted"] = omitted
    return output


def plan_results(operations, applied, *, observation_follows=False):
    """A plan's ``{"success", "operations": [...]}`` read-back with each result in its model copy.

    ``operations`` are the plan's ``{tool, arguments}`` in the order of the
    results; anything else is returned unchanged.
    """
    rows = applied.get("operations") if isinstance(applied, dict) else None
    if not isinstance(rows, list) or not isinstance(operations, (list, tuple)) or len(rows) > len(operations):
        return applied
    shown = [model_result(operation.get("tool") if isinstance(operation, dict) else None, row,
                          observation_follows=observation_follows)
             for operation, row in zip(operations, rows)]
    if all(after is before for before, after in zip(rows, shown)):
        return applied
    return dict(applied, operations=shown)
