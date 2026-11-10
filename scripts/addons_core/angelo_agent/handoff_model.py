# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded model-facing historical result copies; no mutation or completion authority."""

from . import model_results as M
from .handoff_json import UNSUPPORTED, WORK_LIMIT
from .handoff_program_copy import compact as program_copy, copied
from .handoff_projection import Projection, compact as primitive_copy
from .handoff_receipts import Reader, Refused


def _capped_changes(changes, reader):
    output = copied(changes, reader, ("scope",))
    omitted = {}
    for key in M.CHANGE_LISTS:
        values = output.get(key)  # Locally copied dictionary with admitted string keys.
        if type(values) is list and len(values) > M.MAX_CHANGED:
            omitted[key] = len(values) - M.MAX_CHANGED
            kept = []
            for index in range(M.MAX_CHANGED):
                reader.visit()
                kept.append(values[index])
            output[key] = kept
    if omitted:
        output["omitted"] = omitted
    return output


def _single(tool, result, reader, observation_follows):
    if tool == M.MERGE_TOOL:
        changes = reader.read(result, "changes")
        if type(changes) is dict:
            added = []
            for row in reader.rows(reader.read(changes, "added") or []):
                added.append(reader.read(row, "name") if type(row) is dict else row)
            result = dict(result, changes=dict(copied(changes, reader), added=added))
    dropped = set(M.TIMING)
    failed = bool(reader.read(result, "error")) or reader.read(result, "success") is False
    if tool in M.QUERY_TOOLS and not failed:
        dropped.update(M.QUERY_DIAGNOSTICS)
    script = tool == M.SCRIPT_TOOL and not failed and reader.read(result, "success") is True
    if script:
        dropped.update(M.SCRIPT_BOOKKEEPING)
        if observation_follows:
            dropped.update(M.OBSERVED)
    output = copied(result, reader, dropped)
    if script:
        changes = output.get("changes")
        if type(changes) is dict:
            output["changes"] = _capped_changes(changes, reader)
        after = output.get("after")
        if observation_follows and type(after) is dict:
            after = copied(after, reader, ("scene_settings",))
            if reader.read(after, "objects") or reader.read(after, "omitted"):
                output["after"] = after
            else:
                output.pop("after")
    return program_copy(tool, output, reader)


def model_result(tool, result, *, budget, observation_follows=False):
    """Same model result policy, iterative normalized children and budgeted program copy."""
    if type(tool) is not str or type(observation_follows) is not bool:
        return Projection(reason=UNSUPPORTED)
    if tool and not budget.scan(len(tool)):
        return Projection(reason=WORK_LIMIT)
    reader, root = Reader(budget), [None]
    stack = [("record", result, root, 0)]
    try:
        while stack:
            action, source, destination, index = stack.pop()
            reader.visit()
            if action == "parts":
                if index == len(source):
                    continue
                destination.append(None)
                stack.extend((("parts", source, destination, index + 1),
                              ("record", source[index], destination, index)))
                continue
            if type(source) is not dict:
                destination[index] = source
                continue
            output = copied(source, reader, (M.WORKER_FINISHES,))
            values = reader.read(output, "results")
            if reader.present(output, "normalized") and type(values) is list:
                output["results"] = []
                destination[index] = output
                stack.append(("parts", values, output["results"], 0))
            else:
                destination[index] = _single(tool, output, reader, observation_follows)
        return primitive_copy(root[0], budget=budget)
    except Refused as error:
        return Projection(reason=error.args[0])
    except (IndexError, RuntimeError):
        return Projection(reason=UNSUPPORTED)
