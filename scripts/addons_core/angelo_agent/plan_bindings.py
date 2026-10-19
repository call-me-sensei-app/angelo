# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Revisions a request's own commits replaced, and the plan items still bound to them.

A Codex typed plan (codex_plan) is resolved as a whole before its first item
runs, so every inferred binding (a program's read/write set, a recipe's
revision, a verification's read set) takes the revision the turn observed.
When an earlier item, or an in-turn call of the same request, commits an edit
to that object, a later item is bound to a revision the request itself has
replaced, and its owner refuses it as changed. In recorded runs
four ``bevel_and_shade`` plans of [transform_apply, bevel-and-shade]
failed so: "Recipe target changed since observation".

``OwnCommits`` records, for each committed result, which bound revision each
written object had and which one the commit produced, and ``rebind`` moves a
later item's bindings along exactly those steps. Nothing else is rebound: a
revision the request did not replace, or an object changed by anyone after the
request's own commit, still reaches the owner's exact comparison and fails
closed. Plan items bound to an object the request never committed are returned
unchanged.
"""

import copy

_SETS = ("read_set", "write_set")


def _bindings(arguments):
    """``{handle: revision}`` an item's arguments bind (resolved, long forms)."""
    bound = {}
    for field in _SETS:
        for entry in arguments.get(field) or () if isinstance(arguments.get(field), list) else ():
            if isinstance(entry, dict) and isinstance(entry.get("handle"), str) and isinstance(entry.get("revision"), str):
                bound[entry["handle"]] = entry["revision"]
    if isinstance(arguments.get("target"), str) and isinstance(arguments.get("revision"), str):
        bound[arguments["target"]] = arguments["revision"]
    return bound


class OwnCommits:
    """``handle -> {replaced revision: revision the request's own commit produced}``."""

    def __init__(self, calls=()):
        self.replaced = {}
        for call, result in calls:
            self.record(call, result)

    def record(self, call, result):
        """Note a committed result of ``call`` ({tool, arguments}, resolved); anything else changes nothing."""
        live = result.get("live_revisions") if isinstance(result, dict) else None
        arguments = call.get("arguments") if isinstance(call, dict) else None
        if not isinstance(live, dict) or live.get("status") != "COMMITTED" or not isinstance(arguments, dict):
            return
        bound = _bindings(arguments)
        for row in live.get("objects") or ():
            if not isinstance(row, dict):
                continue
            handle, revision = row.get("handle"), row.get("object_revision")
            old = bound.get(handle)
            if isinstance(revision, str) and isinstance(old, str) and old != revision:
                self.replaced.setdefault(handle, {})[old] = revision

    def latest(self, handle, revision):
        """The revision ``revision`` of ``handle`` became through the request's own commits."""
        steps, seen = self.replaced.get(handle, {}), set()
        while revision in steps and revision not in seen:
            seen.add(revision)
            revision = steps[revision]
        return revision

    def rebind(self, operation):
        """``operation`` with every binding the request's own commits replaced moved to their result."""
        arguments = operation.get("arguments") if isinstance(operation, dict) else None
        if not self.replaced or not isinstance(arguments, dict) or not any(
                self.latest(handle, revision) != revision for handle, revision in _bindings(arguments).items()):
            return operation
        arguments = copy.deepcopy(arguments)
        for field in _SETS:
            for entry in arguments.get(field) or () if isinstance(arguments.get(field), list) else ():
                if isinstance(entry, dict) and isinstance(entry.get("handle"), str) and isinstance(entry.get("revision"), str):
                    entry["revision"] = self.latest(entry["handle"], entry["revision"])
        if isinstance(arguments.get("target"), str) and isinstance(arguments.get("revision"), str):
            arguments["revision"] = self.latest(arguments["target"], arguments["revision"])
        return dict(operation, arguments=arguments)
