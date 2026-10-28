# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Claude correction that patches the plan the host rejected or stopped, instead of resending all of it.

After the host rejects a Claude plan before it ran, or stops it at an edit that changed nothing (on its stage, at
dispatch, or a script that raised whose Main data the host restored), the next plan may carry ``patch`` with
``operations`` empty: RFC 6902 ``replace``, ``remove`` and ``add`` steps on ``{"operations": [...]}``, the plan as
the host's report numbered it. As RFC 6902 says, the steps apply in order, each to the document the step before
produced (RFC 6901 pointers; ``-`` appends). Operations that already ran keep their places: a step that changes or
removes one, replaces the whole list, or adds an operation before one is refused. The host then leaves the ran
operations out, and the outcome is an ordinary plan: validated, screened, approved and run exactly as if the model
had written it out, so the approval mode and Undo are unchanged.

A patch that cannot apply, one beside operations, one with no plan to patch, or one that leaves a plan the plan
schema could not carry (an operation other than exactly ``{tool, arguments}`` with one of the request's tools,
more than 64 operations, or none) is a plan problem: nothing ran, it is counted as a rejection, it names what the
plan has, and the held plan stays patchable. Patches compose: a patched plan the host rejects or stops again is
the plan the next patch applies to. Its numbering leaves out the operations that ran, so the note then says which
operations of the model's patched plan the report's numbers are.

Recorded (LANE-g2 count, 2026-10-25): 8 of the 18 rounds current parallel trials lost were Claude plans resent
whole (2.1-8.3k output tokens, about 20 s each) to change one operation, a name or a check. Break-it E2E-19B
(M-2, M-3, L-1, L-2, L-5): steps were applied by original index, a patch could build an operation that failed
the whole request, composed plans were renumbered unseen, a Unicode digit escaped as a ValueError, and a raised
script that left changes was held as "nothing changed".
"""

import copy
import re

MAX_STEPS = 64
MAX_PATH = 512
MAX_OPERATIONS = 64  # The plan schema's ``operations`` maxItems.
OPS = ("replace", "remove", "add")
SCHEMA = {"type": "array", "items": {"type": "object", "properties": {
    "op": {"enum": list(OPS)}, "path": {"type": "string"}, "value": {}},
    "required": ["op", "path"]}}
NOTE = ('The plan the host reported on can be corrected with "patch" instead of being sent again: "operations": [] '
        'and "patch": RFC 6902 replace, remove and add steps ({"op": ..., "path": "/operations/<index>/...", '
        '"value": ...}) on that plan as the host numbered it, each applied to the plan the step before produced. '
        'Operations that already ran keep their places and are not run again; a step may not change or remove one '
        'or add an operation before one. The patched plan is validated and approved like any plan.')
RENUMBERED = ("That plan is your patched plan without the operations that already ran: its operations[{numbers}] "
              "are operations[{yours}] of your patched plan.")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")  # RFC 6901 array index: ASCII digits, no leading zero.
_ESCAPE = re.compile(r"~(?![01])")


class PatchError(ValueError):
    pass


def _tokens(path):
    if not isinstance(path, str) or not path.startswith("/") or len(path) > MAX_PATH or _ESCAPE.search(path):
        raise PatchError("is not an RFC 6901 pointer of at most %d characters starting with /" % MAX_PATH)
    return [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]


def _index(token, size, op):
    """An existing item's index, or for ``add`` an insertion point (``-`` or ``size`` appends)."""
    if token == "-" and op == "add":
        return size
    if not _INDEX.match(token):
        raise PatchError("names %r, which is not an array index (0, 1, 2, ...)" % token[:32])
    index = int(token)
    if index > size or index == size and op != "add":
        raise PatchError("names %d, but there are %d items there" % (index, size))
    return index


def _shape(value):
    return ("an object with %s" % ", ".join(sorted(map(str, value))[:12]) if isinstance(value, dict)
            else "%d items" % len(value) if isinstance(value, list) else "a value")


def _apply(document, tokens, step):
    """One RFC 6902 step inside an operation (``tokens`` relative to it)."""
    parent = document
    for token in tokens[:-1]:
        if isinstance(parent, dict) and token in parent:
            parent = parent[token]
        elif isinstance(parent, list) and _INDEX.match(token) and int(token) < len(parent):
            parent = parent[int(token)]
        else:
            raise PatchError("reaches %r, which is not in %s" % (token[:32], _shape(parent)))
    last, op = tokens[-1], step["op"]
    if isinstance(parent, dict):
        if op != "add" and last not in parent:
            raise PatchError("names %r, which is not in %s" % (last[:32], _shape(parent)))
        if op == "remove":
            del parent[last]
        else:
            parent[last] = copy.deepcopy(step["value"])
    elif isinstance(parent, list):
        index = _index(last, len(parent), op)
        if op == "remove":
            del parent[index]
        elif op == "add":
            parent.insert(index, copy.deepcopy(step["value"]))
        else:
            parent[index] = copy.deepcopy(step["value"])
    else:
        raise PatchError("goes inside a value that has no items")


def _step(operations, ran, step):
    """One RFC 6902 step on ``{"operations": operations}``; ``ran`` flags each operation's place, kept in step."""
    if not isinstance(step, dict) or step.get("op") not in OPS or (step["op"] != "remove" and "value" not in step):
        raise PatchError("is {op: replace|remove|add, path, value}; replace and add take a value")
    tokens, op = _tokens(step.get("path")), step["op"]
    if tokens[0] != "operations":
        raise PatchError("starts at /operations, the plan's only member")
    if len(tokens) == 1:  # The whole list: replace (or add, which replaces an existing member).
        if op == "remove" or not isinstance(step["value"], list) or any(ran):
            raise PatchError("replaces the whole plan, which needs a list and no operation that already ran")
        operations[:], ran[:] = copy.deepcopy(step["value"]), [False] * len(step["value"])
        return
    index = _index(tokens[1], len(operations), op if len(tokens) == 2 else "replace")
    if len(tokens) == 2 and op == "add":
        if any(ran[index:]):
            raise PatchError("adds an operation before operations[%d], which already ran" % ran.index(True, index))
        operations.insert(index, copy.deepcopy(step["value"]))
        ran.insert(index, False)
        return
    if ran[index]:
        raise PatchError("changes operations[%d], which already ran" % index)
    if len(tokens) > 2:
        _apply(operations[index], tokens[2:], step)
    elif op == "remove":
        del operations[index], ran[index]
    else:
        operations[index] = copy.deepcopy(step["value"])


def patched(operations, ran, steps, tools=None):
    """``(plan to run, the patched plan's index of each of its operations)``: ``operations`` (indexes ``ran`` already
    ran) with ``steps`` applied in order, the ran ones left out; ``tools`` the request's tool names (None: any)."""
    if not isinstance(steps, list) or not 0 < len(steps) <= MAX_STEPS:
        raise PatchError("patch: 1 to %d steps; %s given" % (MAX_STEPS, len(steps) if isinstance(steps, list) else "none"))
    document, flags = copy.deepcopy(list(operations)), [index in ran for index in range(len(operations))]
    for number, step in enumerate(steps):
        try:
            _step(document, flags, step)
        except PatchError as error:
            path = str(step.get("path"))[:120] if isinstance(step, dict) else ""
            raise PatchError("patch[%d] (%s) %s" % (number, path, error)) from None
    kept = [index for index, done in enumerate(flags) if not done]
    plan = [document[index] for index in kept]
    if not plan or len(plan) > MAX_OPERATIONS:
        raise PatchError("patch: the patched plan has %d operations to run; a plan runs 1 to %d"
                         % (len(plan), MAX_OPERATIONS))
    for index, operation in zip(kept, plan):
        if (not isinstance(operation, dict) or set(operation) != {"tool", "arguments"}
                or not isinstance(operation["arguments"], dict)
                or not isinstance(operation["tool"], str) or tools is not None and operation["tool"] not in tools):
            raise PatchError("patch: operations[%d] of the patched plan is %s; an operation is exactly "
                             '{"tool": one of this request\'s tools, "arguments": {...}}' % (index, _shape(operation)))
    return plan, kept


class PlanPatches:
    """One Claude request's patchable plan: the last plan the host rejected or stopped, and what of it ran."""

    def __init__(self, allowed=None):
        self.allowed = allowed  # The request's allowed tools (None: every declared tool), as the plan schema has.
        self.sent = None  # The operations of the last plan the model sent, written out or patched.
        self.numbers = None  # For a patched plan without its ran operations: each one's index in the patched plan.
        self.held = None  # (operations, indexes that ran, numbers): what the next round may patch.

    def _tools(self):
        from .tool_specs import declared
        return frozenset(tool["name"] for tool in declared(self.allowed))

    def apply(self, plan):
        """The round's plan to run: ``plan`` itself, or the held plan patched; a plan problem when that fails."""
        if plan is None:
            return None
        steps, plan = plan.get("patch"), {key: value for key, value in plan.items() if key != "patch"}
        if plan.get("problem") is not None:
            return plan  # The round's plan never runs (an empty or superseded plan): a held plan stays patchable.
        held, self.held = self.held, None
        if steps is None:
            self.sent, self.numbers = copy.deepcopy(plan.get("operations")), None  # As written, before screening.
            return plan
        self.held = held  # A patch that fails leaves the plan it was for patchable.
        if held is None:
            problem = "patch: there is no rejected or stopped plan to patch in this request; send operations"
        elif plan.get("operations"):
            problem = "patch: a plan carries operations or a patch, not both"
        else:
            try:
                operations, kept = patched(held[0], held[1], steps, self._tools())
            except PatchError as error:
                problem = str(error)
            else:
                self.held, self.sent = None, copy.deepcopy(operations)
                self.numbers = kept if kept != list(range(len(kept))) else None
                return dict(plan, operations=operations)
        return dict(plan, operations=[], problem=problem)

    def hold(self, prompt, run=None, unchanged=True):
        """Keep the last plan for a patch and return ``prompt`` with the note. ``run``: its results, the last one
        the edit that stopped it; ``unchanged``: that edit changed nothing. A raised script counts only when the
        host restored Main data; otherwise nothing is held and ``prompt`` is returned as it is."""
        if run is not None and not unchanged:
            last = run.results[-1] if run.results else None
            if not (isinstance(last, dict) and last.get("script_error") is True and last.get("main_data_restored") is True):
                self.held = None
                return prompt
        ran = () if run is None else [index for index in range(len(run.results) - 1) if index not in run.refused]
        self.held = (self.sent or [], frozenset(ran), self.numbers)
        return self.note(prompt)

    def note(self, prompt, separator="\n"):
        """``prompt`` with the patch note when a plan is held (``separator`` " " keeps a results header one line),
        and which operations of the model's patched plan the report's numbers are when they differ."""
        if prompt is None or self.held is None:
            return prompt
        numbers, text = self.held[2], NOTE
        if numbers:
            text += " " + RENUMBERED.format(numbers=", ".join(map(str, range(len(numbers)))),
                                            yours=", ".join(map(str, numbers)))
        return prompt + separator + text
