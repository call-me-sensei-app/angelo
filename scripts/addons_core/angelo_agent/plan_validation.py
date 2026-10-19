# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the host checks a Claude typed plan before its first operation runs.

``plan_problems`` validates every operation exactly (``tool_specs.validate_call``,
after the context's short identifiers are resolved) and returns the problem
lines of a plan the host rejects. A Codex typed plan uses it as it is.

``screen_plan`` is the Claude loop's screen (doc/angelo/agent_integration.rst,
"Operations the host refused alone"). Mutating operations stay all-or-nothing:
one invalid edit, program, recipe, script or standalone verification rejects
the whole plan with exactly the lines ``plan_problems`` gives, because the steps
of a plan can depend on each other. Two kinds of invalid operation no longer
reject the whole plan:

* a lookup (a tool whose declared contract is read-only, other than a
  standalone verification: ``plan_recovery.is_lookup``). One whose arguments
  have exactly one meaning is normalised first (``NORMALIZERS``; only
  ``angelo_view`` has one, ``view_spec.normalize_view``) and its result says
  so. Any other is refused on its own: it never runs, and its result is the
  error the host gives a call it refuses at admission (the validator's message,
  ``exact_schemas`` of a deferred tool).
* a background job's start (``STOPS``: the job family's tools that are not
  read-only). It changes nothing until the host admits it, and a start the
  host refuses at admission is already an edit that changed nothing
  (``claude_outcomes.correctable_failure``), so it gets the same error and
  ``scene_changed`` false. A self-check never holds it back. Its arguments
  are never normalised.

The plan runs up to its first operation refused alone and nothing after it
(``ScreenedPlan.stop``, ``plan_recovery.run_operations``): the model wrote the
rest to follow it, and a lookup can be a point the plan waits at (a look at
``LAST_JOB`` waits for the render) or have an effect its contract does not
count (a job cancel). A plan with operations refused alone costs one
correction of the request's budget, as its rejection did; one that sends an
operation refused before again unchanged is rejected whole (``RefusalMemory``).
A plan of which nothing valid is left to run, or whose views exceed the
request's budget, is rejected as before. No Blender access.
"""

import json
from dataclasses import dataclass, field

from .claude_evidence import VIEW_IMAGE_LIMIT, VISUAL_IMAGE_TOOLS, one_shot
from .job_spec import JOB_NAMES
from .plan_recovery import is_lookup
from .plan_schema import deferred_schemas
from .protocol import RpcError
from .refusal_codes import error_result, refusal_code
from .tool_specs import TOOL_MAP, validate_call
from .view_spec import TOOL_NAME as VIEW_TOOL, normalize_view

# Tool -> fn(arguments) returning ``(calls, note)`` (each call's arguments already valid) or None. Lookups only.
NORMALIZERS = {VIEW_TOOL: normalize_view}
# Background job starts: refused alone, and the plan stops there (module docstring).
STOPS = frozenset(name for name in JOB_NAMES if name in TOOL_MAP and not TOOL_MAP[name]["annotations"]["readOnlyHint"])
MAX_OPERATIONS = 64
# Why a normalised view was refused instead: a fact of the request's budget, never the plan-level rejection's words
# (this plan runs).
OVER_BUDGET = ("Its normalised form would add views beyond the %d this request keeps; ask for fewer kept views (a "
               "RENDERED look of SCENE_CAMERA alone is not one)." % VIEW_IMAGE_LIMIT)
# A plan with neither a reply nor operations: nothing to show and nothing to run.
EMPTY_PLAN = ("Your last plan had neither a reply nor operations, so nothing ran. Answer the person in reply, "
              "or submit the operations the request still needs.")


def lookups_only(operations):
    """Whether every operation only reads (a standalone verification proves something by itself)."""
    return bool(operations) and all(isinstance(operation, dict) and operation.get("tool") != "angelo_scene_verify"
        and TOOL_MAP.get(operation.get("tool"), {}).get("annotations", {}).get("readOnlyHint")
        for operation in operations)


def superseded_plans(submitted, plan):
    """The problem with a turn that called StructuredOutput more than once, or None.

    ``submitted`` holds the calls the runtime accepted (one its schema check
    rejected is a retry, never a plan: fast tiers resubmit after a wrong
    shape). The runtime returns only the last call. When an earlier call held
    operations the last one drops (it has none, or an earlier one had an edit
    the last one does not repeat), the model believes they ran; nothing ran.
    Recorded (Haiku): a lookup, then "Done." with no
    operations, and the move it was asked for never ran. Earlier lookups the
    last plan does not need are dropped without a word.
    """
    calls = [item for item in submitted if isinstance(item, dict)]
    earlier = [item.get("operations") for item in calls[:-1]
               if isinstance(item.get("operations"), list) and item["operations"]
               and item["operations"] != plan["operations"]]
    if not earlier or (plan["operations"] and all(lookups_only(operations) for operations in earlier)):
        return None
    return ("You called StructuredOutput %d times in one turn. Only the last call counts, so the operations of "
            "the earlier ones never ran and nothing changed. Submit the whole plan in one call (set verify=true "
            "when you must see a lookup's result first)." % len(calls))


def _checked(operations, allowed, resolve):
    """Each operation as ``(resolved arguments or None, ValueError or None)``; contract breaches raise."""
    checked = []
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != {"tool", "arguments"}:
            raise RpcError("Invalid native plan operation")
        if allowed is not None and operation["tool"] not in allowed:
            raise RpcError("Native plan used a tool this request does not allow")
        arguments = None
        try:
            arguments = resolve(operation["tool"], operation["arguments"]) if resolve else operation["arguments"]
            validate_call(operation["tool"], arguments)
            checked.append((arguments, None))
        except ValueError as error:
            checked.append((arguments, error))
    return checked


def _line(index, operation, error):
    return f"operations[{index}] ({str(operation['tool'])[:64]}): {str(error)[:1000]}"


def plan_problems(operations, allowed, resolve=None):
    """Validate a whole plan before its first operation.

    Returns ``(problems, operations)``: rejection lines, and the operations with
    their short identifiers resolved (``resolve`` is the host's
    ``identifiers.expand_call`` for this context); those resolved operations are
    what runs. Validation is the host's exact validate_call. A tool outside the
    request's allowed set, or a malformed operation, is a runtime contract
    breach (the plan schema already excludes both) and fails the request instead.
    """
    checked = _checked(operations, allowed, resolve)
    problems = [_line(index, operation, error)
                for index, (operation, (_arguments, error)) in enumerate(zip(operations, checked)) if error is not None]
    resolved = [{"tool": operation["tool"], "arguments": arguments}
                for operation, (arguments, _error) in zip(operations, checked) if arguments is not None]
    return problems, resolved


def _lookup(tool):
    """Read-only by the tool's declared contract, and not a standalone verification (plan_recovery.is_lookup)."""
    return isinstance(tool, str) and tool in TOOL_MAP and is_lookup(tool)


def _alone(tool):
    """Whether an invalid ``tool`` is refused on its own instead of rejecting its plan."""
    return _lookup(tool) or isinstance(tool, str) and tool in STOPS


def refused_result(operation, error, budget=False):
    """What an operation the host refused alone returns: the error a call refused at admission gets (Controller).

    A job start also says it changed nothing, as a start refused at admission
    does (job_host). ``budget``: its normalised form was refused for the view
    budget (``OVER_BUDGET``), so the model does not ask for exactly that next.
    """
    result = error_result(error)
    if operation.get("tool") in STOPS:
        result["scene_changed"] = False
    if refusal_code(error) == "UNKNOWN":
        result["error_code"] = "VALIDATION"
    if budget:
        result["view_budget"] = OVER_BUDGET
    try:
        schemas = deferred_schemas(operation["tool"], operation["arguments"])
    except Exception:  # Untrusted, possibly malformed arguments: the error alone still stands.
        schemas = {}
    if schemas:
        result["exact_schemas"] = schemas
    return result


@dataclass
class ScreenedPlan:
    """A screened plan: ``problems`` reject it whole; otherwise ``operations`` run, aligned with the model's.

    ``refused`` maps the index of each refused lookup to its rejection line,
    ``stops`` that of each refused job start; their slots in ``operations``
    keep the model's own operations and never run, and nothing after the first
    stop runs. ``companions`` maps an index to the further calls its normalised
    lookup made, ``notes`` to what the normalisation did.
    """

    problems: list
    operations: list = field(default_factory=list)
    refused: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)  # Index -> the result of a refused lookup or job start.
    companions: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    stops: dict = field(default_factory=dict)

    def stop(self):
        """The index of its first operation refused alone, where it stops (plan_recovery.run_operations), else its length."""
        return min([*self.refused, *self.stops], default=len(self.operations))

    def counted(self):
        """Whether the plan costs a correction: it refused something alone. The screen charges it, so a plan past the
        budget is rejected whole; a refused job start the run then reaches has been paid for (``providers``)."""
        return bool(self.refused or self.stops)

    def calls(self, end):
        """The calls of the plan's slots before ``end`` that run: refused ones left out, normalised ones with theirs."""
        return [call for index, operation in enumerate(self.operations[:end]) if index not in self.refused
                for call in [operation] + self.companions.get(index, [])]

    def alone(self):
        """Every operation refused alone, in plan order, as its rejection line."""
        lines = {**self.refused, **self.stops}
        return [lines[index] for index in sorted(lines)]

    def dispatched(self):
        """Every call the plan may make: refused operations and all after the first stop left out (budget, pins)."""
        return self.calls(self.stop())


def _counted(calls):
    """Whether calls add views the request keeps (a RENDERED look or a finished picture is delivered once)."""
    return any(call["tool"] in VISUAL_IMAGE_TOOLS and not one_shot(call) for call in calls)


def screen_plan(operations, allowed, resolve=None, budget=None):
    """The Claude loop's screen of a plan (module docstring); ``budget(calls)`` is the view budget's problem or None."""
    checked = _checked(operations, allowed, resolve)
    lines = [_line(index, operation, error)
             for index, (operation, (_arguments, error)) in enumerate(zip(operations, checked)) if error is not None]
    if any(error is not None and not _alone(operation["tool"])
           for operation, (_arguments, error) in zip(operations, checked)):
        return ScreenedPlan(lines)   # Mutations stay all-or-nothing: exactly today's rejection.
    plan = ScreenedPlan([])
    count = len(operations)
    for index, (operation, (arguments, error)) in enumerate(zip(operations, checked)):
        tool = operation["tool"]
        if error is None:
            plan.operations.append({"tool": tool, "arguments": arguments})
            continue
        if tool in STOPS:   # Never normalised: a job's arguments are never guessed.
            plan.operations.append(operation)
            plan.stops[index] = _line(index, operation, error)
            plan.results[index] = refused_result(operation, error)
            continue
        normalized = NORMALIZERS.get(tool)
        normalized = normalized(arguments) if normalized is not None and arguments is not None else None
        if normalized is not None and count + len(normalized[0]) - 1 <= MAX_OPERATIONS:
            calls, note = normalized
            count += len(calls) - 1
            plan.operations.append({"tool": tool, "arguments": calls[0]})
            plan.companions[index] = [{"tool": tool, "arguments": call} for call in calls[1:]]
            plan.notes[index] = note
            continue
        plan.operations.append(operation)
        plan.refused[index] = _line(index, operation, error)
        plan.results[index] = refused_result(operation, error)
    problem = budget(plan.dispatched()) if budget is not None else None
    for index in sorted(index for index in plan.notes if index < plan.stop()
                        and _counted([plan.operations[index]] + plan.companions[index])) if problem else ():
        # A normalised view must never cost the plan its run: the first one whose kept views take the plan past the
        # budget, when all before it fits, is refused instead, saying why, and the plan stops there. A plan already
        # past the budget before it is rejected whole, as it would be with that view written validly.
        if budget(plan.calls(index + 1)) is None:
            continue
        if budget(plan.calls(index)) is None:
            original, (_arguments, error) = operations[index], checked[index]
            plan.operations[index] = original
            plan.refused[index] = _line(index, original, error) + " (its normalised form would exceed the view budget)"
            plan.results[index] = refused_result(original, error, budget=True)
            plan.companions.pop(index, None)
            plan.notes.pop(index)
            problem = budget(plan.dispatched())
        break
    if lines and not plan.dispatched():
        return ScreenedPlan(lines)   # Nothing valid is left to run: rejected, as before.
    if problem:
        return ScreenedPlan(plan.alone() + [problem])
    return plan


class RefusalMemory:
    """The operations a request has had refused alone, to reject a plan that sends one again unchanged.

    A refusal is deterministic: an operation sent again with the same
    arguments is refused again, and the plan around it (often its build
    script) would run again. Such a plan is rejected whole instead, nothing
    of it run, as it was before operations were refused alone.
    """

    def __init__(self):
        self.seen = set()

    def repeated(self, screened, operations):
        """Whether ``screened`` refuses an operation this request had refused before; remembers its refusals."""
        keys = {json.dumps([operations[index]["tool"], operations[index]["arguments"]], sort_keys=True, default=str)
                for index in {**screened.refused, **screened.stops}}
        repeated = bool(keys & self.seen)
        self.seen |= keys
        return repeated
