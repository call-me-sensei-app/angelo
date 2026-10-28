# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex typed plans: a GPT edit ends with one structured final message (toolset 30).

A GPT edit used to take two model rounds after the request: one wrote the
program in code mode's ``exec``, and after the host committed and verified it,
another only wrote the closing message (2.5-2.7 s of a 7.8-9.0 s edit in
recorded runs, doc/angelo/gpt_launch.rst). A Claude typed plan has no such
round: Claude Code returns the plan as structured output and the host runs it
and writes the reply (providers.ClaudeProvider).

A Codex chat thread on an OpenAI connection now does the same. Every
``turn/start`` sends ``outputSchema`` (``output_schema``), a strict variant of the Claude plan
schema (``plan_schema.plan_document`` -> ``strict_schema``) limited to
``PLAN_TOOLS``, which Codex passes to the model as a strict ``json_schema``
response format. The turn's final message is ``{reply, operations}``; tools are
still called from ``exec`` within the turn (reads, schema lookups, views,
reviewed Python, an edit whose result a later step reads). After ``turn/completed`` the host
decodes the plan (``strict_schema.decode``), validates it exactly
(``plan_validation.plan_problems``), runs it, and ends the request with its
verified summary (``claude_outcomes.certified_completion``). A rejected plan or
a program that failed on its stage changes nothing and goes back once or twice
for a fix, as for Claude.

The schema is byte-identical on every turn of a thread: OpenAI caches
``text.format`` with the prompt prefix. Voice threads, collaborators, prompt
enhancement and external (non-OpenAI) connections keep the previous contract.
"""

import functools
import json
import re
import threading
import time
import uuid

from .action_schema_types import stored_forms
from . import plan_steps, tool_deadlines
from .claude_stream import STATUS_PLAN, STATUS_THINKING, THINKING_SECONDS as PROGRESS_SECONDS
from .plan_bindings import OwnCommits
from .plan_progress import PlanProgress, status as plan_status
from .plan_runaway import TurnWatch
from .plan_schema import CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS, plan_document
from .protocol import RpcError
from .schema_compaction import compact_nullable_scalars, compact_schema
from .strict_schema import StrictDecodeError, decode, inline_references, strict_schema
from .strict_shapes import annotate
from .tool_specs import TOOL_MAP, TOOLS, PART_NAMES, cube_batch, note_reports
from .ui_text import n_, notice

# Reviewed Python and file effects stay in-turn: their result is what the model needs next.
IN_TURN_ONLY = frozenset({"angelo_execute_python", "angelo_write_file", "angelo_delete_file"})
# Edits a plan may end a turn with, and the standalone verification that proves one.
PLAN_TOOLS = frozenset(tool["name"] for tool in TOOLS
                       if not tool["annotations"]["readOnlyHint"] and tool["name"] not in IN_TURN_ONLY
                       ) | {"angelo_scene_verify"} | (PART_NAMES & frozenset(TOOL_MAP))   # A worker's report (P1-6 M1).
MAX_OPERATIONS = 64
# Fragments shared through $defs from this size; smaller than the Claude plan's
# 100 bytes because the strict variant repeats its null wrappers.
STRICT_SHARED_BYTES = 40
DISPATCH_SECONDS = 190
# What a plan thread's instructions state exactly (codex_reference): a correction repeats no schema of these.
REFERENCE = (CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS)
# Whether the output schema offers the shorter forms of strict_shapes. One switch: with it off the schema has
# one complete form per object again, and plans written either way decode alike.
SHORT_FORMS = True
PLAN_GUIDANCE = """
Every turn ends with one final message in the response format, {reply, operations}. operations are the edits that
finish the request: the host validates them exactly, runs them in order after your turn with the person's approvals
and shows its verified result, with no further model round, so they must include every edit the request still needs.
Nothing in operations has run when you write it; an edit called from exec runs during your turn and its result comes
back to you. The host binds each operation just before it runs, past what the earlier ones committed, so consecutive
edits of one object in operations need no newer revision. An edit already run from exec runs again if it is also in
operations. An operation is {tool, arguments} with the tool's arguments (program items as in the Program reference).
Write an object's fields in the format's order and, where the format lets you, close it after the last one you need:
a field left out takes its default, or keeps its setting where it has none, and a field you skip before it is null.
A field described as JSON text holds that object as a JSON string (the parameters of a kind the format does not
spell out, a deferred check, a deferred tool's arguments). The host shows the person each edit's verified result. A
reply never claims an edit is done; the verified result says what happened. A plan the host rejects, or a program
that fails on its stage, changes nothing and comes back once for a fix.
"""
SUPERSEDED = ("Only the operations of your turn's last message run. An earlier message listed operations that the "
              "last one does not repeat, so nothing ran and nothing changed. Submit the whole plan in your final message.")
SHAPE = "Your final message was not a plan: it needs exactly a reply string and an operations list (at most 64)."
NOT_CERTIFIED = n_("Native plan completed. Tool read-backs are shown above; scene edits have Undo, "
                   "external effects do not.")


def plan_tools(allowed=None):
    return PLAN_TOOLS - PART_NAMES if allowed is None else PLAN_TOOLS & frozenset(allowed)


def plan_mode(context, external):
    """Whether this Codex text context ends its turns with a typed plan.

    OpenAI connections only (the strict response format is OpenAI's), no voice,
    no context with its own base instructions (prompt enhancement), and only
    when the context may edit through a plan (collaborators may not).
    """
    if external or getattr(context, "voice", None) is not None or getattr(context, "base_instructions", None) is not None:
        return False
    tools = plan_tools(getattr(context, "allowed_tools", None))
    return any(not TOOL_MAP[name]["annotations"]["readOnlyHint"] for name in tools)


@functools.lru_cache(maxsize=8)
def _documents(allowed):
    # A grammar can refuse nothing, so pointers show only the handle (action_schema_types.stored_forms).
    document = stored_forms(plan_document(TOOLS, plan_tools(allowed)))
    # No verify (a Codex turn reads results in-turn) and no "conf" (Auto
    # routing's escalation is the Claude plan's); a reply is always written.
    for name in ("verify", "conf"):
        document["properties"].pop(name, None)
    document["required"] = ["reply", "operations"]
    if SHORT_FORMS:
        annotate(document)  # Everyday objects may be closed early instead of padded with nulls (strict_shapes).
    # Order preserved: the model writes properties in schema order (strict_schema._discriminators_first).
    strict = compact_schema(strict_schema(document), min_shared_bytes=STRICT_SHARED_BYTES, preserve_order=True)
    return inline_references(document), compact_nullable_scalars(strict)


def output_schema(allowed=None):
    """The thread's strict plan schema (``turn/start`` ``outputSchema``); never mutate it."""
    return _documents(None if allowed is None else frozenset(allowed))[1]


def ordinary_schema(allowed=None):
    """The plan schema ``decode`` reads strict outputs against (references inlined)."""
    return _documents(None if allowed is None else frozenset(allowed))[0]


def parse_plan(text, allowed=None):
    """``(plan, problem)`` for one final message.

    A message that is not a JSON object is a plain answer (a runtime ignoring
    the format must not lose it). A JSON object must be a plan; its strict
    form is decoded to the ordinary one, which the host then validates exactly.
    """
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return {"reply": text, "operations": []}, None
    if (set(value) != {"reply", "operations"} or not isinstance(value["reply"], str)
            or not isinstance(value["operations"], list) or len(value["operations"]) > MAX_OPERATIONS):
        return None, SHAPE
    try:
        return decode(value, ordinary_schema(allowed)), None
    except StrictDecodeError as error:
        return None, "Your plan's %s. Write that object as a JSON string." % error


_REPLY_START = re.compile(r'\s*\{\s*"reply"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class ReplyStream:
    """The text a plan message shows as it streams.

    A JSON message streams only its reply (strict outputs keep the schema's
    key order, reply first); raw JSON never reaches the chat. A message that
    does not start with ``{`` is a plain answer and streams as it is.
    """

    def __init__(self):
        self.buffer, self.position, self.done, self.plain = "", None, False, False

    def feed(self, delta):
        if not isinstance(delta, str):
            return ""
        if self.plain:
            return delta
        if self.done:
            return ""
        self.buffer += delta
        if self.position is None:
            stripped = self.buffer.lstrip()
            if stripped and not stripped.startswith("{"):
                self.plain, self.done = True, True
                return self.buffer
            match = _REPLY_START.match(self.buffer)
            if match is None:
                compact = re.sub(r"\s+", "", self.buffer)
                self.done = not '{"reply":"'.startswith(compact)
                return ""
            self.position = match.end()
        text, index, buffer = [], self.position, self.buffer
        while index < len(buffer):
            character = buffer[index]
            if character == '"':
                self.done = True
                break
            if character != "\\":
                text.append(character)
                index += 1
                continue
            if index + 1 >= len(buffer):
                break
            escape = buffer[index + 1]
            if escape != "u":
                text.append(_ESCAPES.get(escape, escape))
                index += 2
                continue
            code, used = self._unicode(buffer, index)
            if used == 0:
                break
            text.append(code)
            index += used
        self.position = index
        return "".join(text)

    def _unicode(self, buffer, index):
        """``(text, characters used)`` for a ``\\uXXXX`` escape (and its low surrogate), or ``("", 0)`` to wait."""
        if index + 6 > len(buffer):
            return "", 0
        try:
            code = int(buffer[index + 2:index + 6], 16)
        except ValueError:
            self.done = True
            return "", 0
        if not 0xD800 <= code < 0xDC00:
            return chr(code), 6
        if index + 12 > len(buffer):
            return "", 0
        try:
            low = int(buffer[index + 8:index + 12], 16) if buffer[index + 6:index + 8] == "\\u" else -1
        except ValueError:
            low = -1
        if 0xDC00 <= low < 0xE000:
            return chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)), 12
        return "�", 6


class PlanTurns:
    """One request's turns on a plan thread: streamed replies, final messages, in-turn calls."""

    def __init__(self, allowed=None):
        self.allowed = allowed
        self.schema = output_schema(allowed)
        self.call_completions, self.ran_completions = [], []
        self.calls = []  # The request's in-turn host calls ({tool, arguments} resolved, result).
        self.ran = []  # Plan operations that ran without failing, as dispatched, with their results.
        self.begin_turn()

    def begin_turn(self):  # watch: a message that keeps streaming without finishing (plan_runaway)
        self.streams, self.messages, self.progress, self.watch = {}, [], {}, TurnWatch()

    def reply_delta(self, item, delta, emit=None):
        """The reply text a message delta adds; ``emit`` also gets a status saying what the plan will make.

        The status carries the plan's names and provisional steps (``plan_progress``, ``plan_steps``): the
        first at once, later changes at most twice a second.
        """
        self.watch.feed(item, delta)
        entry = self.progress.setdefault(item, [PlanProgress(), None, STATUS_PLAN])  # [scan, when shown, shown]
        if callable(emit):
            entry[0].feed(delta)
            current = plan_status(STATUS_PLAN, entry[0].detail(), plan_steps.encode(entry[0]))
            if current != entry[2] and (entry[1] is None or time.monotonic() - entry[1] >= PROGRESS_SECONDS):
                entry[1:] = [time.monotonic(), current]
                emit("status", {"text": current})
        return self.streams.setdefault(item, ReplyStream()).feed(delta)

    def message_completed(self, item, text, emit=None):
        """Record one agent message; return the text the chat shows for it.

        A message that showed what its plan makes takes that away (``emit``): it is written, and its steps
        become rows only when the host runs them.
        """
        entry = self.progress.get(item)
        if callable(emit) and entry is not None and entry[1] is not None:
            emit("status", {"text": STATUS_THINKING})
        self.messages.append((item, text if isinstance(text, str) else ""))
        plan, _ = parse_plan(self.messages[-1][1], self.allowed)
        return plan["reply"] if plan else ""

    def final_plan(self):
        """``(item, plan, problem)`` of the finished turn; no message is an empty plan."""
        if not self.messages:
            return None, {"reply": "", "operations": []}, None
        parsed = [parse_plan(text, self.allowed) for _, text in self.messages]
        plan, problem = parsed[-1]
        if problem is None and any(earlier and earlier["operations"] and earlier["operations"] != plan["operations"]
                                   for earlier, _ in parsed[:-1]):
            plan, problem = None, SUPERSEDED
        return self.messages[-1][0], plan, problem


def _current(provider, context, fence):
    with provider._scope.lock:
        return provider.controller is context and provider._scope.fence == fence and not context.cancelled


def _native_call(provider, context, fence, name, arguments):
    unavailable = {"error": "This Codex request no longer owns native dispatch; inspect before retrying"}
    done, answer = threading.Event(), []

    def receive(result):
        answer.append(result if isinstance(result, dict) else unavailable)
        done.set()
    if not _current(provider, context, fence):
        return unavailable
    context.request_tool("codex-plan/" + str(uuid.uuid4()), name, arguments, receive)
    if not tool_deadlines.await_host(done, context, DISPATCH_SECONDS):   # Not while the chat waits for its scene (R5-M-1).
        raise RpcError("Native Blender approval/dispatch timed out")
    return answer[0] if _current(provider, context, fence) else unavailable


def emit_summary(context, reply, calls, *, emit_uncertified=False, completions=None):
    """End a turn whose edits ran with the host's account of them.

    ``calls`` are in-turn calls (only when the final ``reply`` is empty, which
    then would be the closing message) or a plan's operations (``reply`` "").
    A certified summary when host checks prove every edit
    (``claude_outcomes.certified_completion``), otherwise, if
    ``emit_uncertified``, the plain notice a Claude plan ends with.
    """
    from .claude_outcomes import certified_completion
    if (reply or "").strip() or not calls or context.cancelled:
        return
    from .program_completion import completions_for
    completions = completions if completions is not None else completions_for(context, [result for _, result in calls])
    summary = certified_completion([call for call, _ in calls], [result for _, result in calls], completions=completions,
                                   read_only=lambda name: TOOL_MAP[name]["annotations"]["readOnlyHint"])
    if summary is not None:
        context.emit("message", {"role": "system", "text": summary, "host_summary": True})
    elif emit_uncertified:
        context.emit("message", {"role": "system", **notice(NOT_CERTIFIED)})


_BINDINGS = ("read_set", "write_set", "revision")


def _unbound(operation):
    """An operation without the bindings the host infers: an in-turn commit moves the observed revision."""
    arguments = operation["arguments"]
    return operation["tool"], {key: value for key, value in arguments.items() if key not in _BINDINGS}


def _repeats(operation, calls):
    """An edit the model already ran from exec this request (a second run would apply it twice)."""
    return (not TOOL_MAP[operation["tool"]]["annotations"]["readOnlyHint"]
            and any(not result.get("error") and isinstance(call.get("arguments"), dict)
                    and _unbound(call) == _unbound(operation) for call, result in calls))


def finish(provider, turns, context, fence, native_id, status):
    """Run the plans that end a request's turns; returns the request status.

    ``status`` is the first turn's. A correction is another turn on the same
    thread (``provider.plan_turn``) with the problem as its message: a plan the
    host rejected, a plan that stopped before changing anything, or one that
    stopped at a later item which changed nothing (an informed stale correction included)
    after earlier items committed. The last gets their receipts and a fresh
    observation (``continuation_prompt``).
    """
    from .claude_outcomes import correctable_failure
    from .plan_schema import PlanRejected, rejection_prompt, staged_rejection_prompt
    from .plan_validation import plan_problems
    from .plan_recovery import FAILED_EDIT_CORRECTIONS
    from .providers import CLAUDE_PLAN_REJECTIONS
    rejections = failed_edits = 0   # Rejected plans and failed edits that changed nothing: counted apart (plan_recovery).
    while True:
        from .program_completion import completions_for
        incoming = provider.turn_calls()
        turns.calls.extend(incoming)
        turns.call_completions.extend(completions_for(context, [result for _, result in incoming]))
        if status != "completed":
            return status
        if not _current(provider, context, fence):
            return "interrupted"  # Stopped or replaced after the turn: nothing of its plan runs.
        item, plan, problem = turns.final_plan()
        operations = []
        if problem is None:
            problems, operations = plan_problems(plan["operations"], plan_tools(turns.allowed),
                                                 getattr(context, "resolve_call", None))
            if problems:
                getattr(context, "record_refused_plan", lambda *_: None)(plan["operations"], problems)
                problem = rejection_prompt(problems, plan["operations"], *REFERENCE)
        if problem is not None:
            rejections += 1
            if rejections > CLAUDE_PLAN_REJECTIONS:
                raise PlanRejected("Host rejected the plan before running any operation:\n" + problem)
            context.emit("message", {"role": "system", **notice(
                "Nothing of the last plan ran; asking for a corrected plan:\n{problem}", problem=problem)})
            status = provider.plan_turn(native_id, problem, turns)
            continue
        operations = [operation for operation in operations if not _repeats(operation, turns.calls + turns.ran)]
        if not operations:
            if turns.ran:  # A correction with nothing left to do: the host's account of what ran.
                emit_summary(context, "", turns.ran, emit_uncertified=True, completions=turns.ran_completions)
            else:
                emit_summary(context, plan["reply"], turns.calls, completions=turns.call_completions)
            return status
        # Until a later message's handoff carries these results, the thread lacks them (Controller).
        context.emit("message_complete", {"item": item or "codex-plan", "text": plan["reply"], "plan_operations": True})
        executed, results, failed = _run(provider, context, fence, operations, OwnCommits(turns.calls + turns.ran))
        if failed is None:
            # As after a Claude plan: the reply came first, the host's account of the plan follows.
            turns.ran.extend(zip(executed, results))
            turns.ran_completions.extend(completions_for(context, results))
            emit_summary(context, "", turns.ran, emit_uncertified=True, completions=turns.ran_completions)
            return "completed" if _current(provider, context, fence) else "interrupted"
        problem, failure_status = failed
        index = len(results) - 1
        turns.ran.extend(zip(executed[:index], results[:index]))
        turns.ran_completions.extend(completions_for(context, results[:index]))
        # A one-item plan that changed nothing: one corrected plan, approved again (as Claude's loop). Only at index 0:
        # a later failure after a committed move (no scene_changed) was called unchanged and the move ran again.
        correctable = correctable_failure(executed, results) if index == 0 else None
        # Or the failing item changed nothing after earlier items committed (or was bound stale).
        continuable = None if correctable else correctable_failure([executed[index]], [results[index]])
        if (correctable or continuable) and failed_edits < FAILED_EDIT_CORRECTIONS and _current(provider, context, fence):
            failed_edits += 1
            if correctable:
                context.emit("message", {"role": "system", **notice(
                    "The plan stopped before changing the scene; asking for a corrected plan:\n{problem}", problem=correctable)})
                prompt = staged_rejection_prompt(correctable, operations, *REFERENCE)
            else:
                context.emit("message", {"role": "system", **notice(
                    "The plan stopped at operations[{index}]; asking for the edits still needed:\n{problem}", index=index, problem=continuable)})
                prompt = continuation_prompt(context, executed, results, _observation(provider, context, fence))
            status = provider.plan_turn(native_id, prompt, turns)
            continue
        context.emit("message", {"role": "system", "text": problem})
        return failure_status


def _run(provider, context, fence, operations, own):
    """Run a validated plan in order: ``(executed, results, failure)``.

    ``executed`` are the operations as dispatched (cube/location-only plans as one ``angelo_apply_batch``, as for
    Claude, a worker's progress reports on its tile once that commits); ``failure`` is ``(problem, status)`` at the
    first failed result, or None. Each item is bound, just before it runs, past the revisions the request's own
    commits replaced (``own``, plan_bindings): a plan is resolved before its first item, so a later item editing the
    same object would otherwise carry the pre-commit revision and be refused as changed.
    """
    from .claude_outcomes import native_result_problem
    if (cubes := cube_batch(planned := operations)) is not None:   # Reports aside (N2), shown once it commits (P5).
        operations = [{"tool": "angelo_apply_batch", "arguments": {"operations": cubes}}]
    operations, results = list(operations), []
    observe = getattr(context, "observe_result", None)
    for index, operation in enumerate(operations):
        if not _current(provider, context, fence):
            return operations, results, ("Stopped before the rest of the plan ran.", "interrupted")
        operation = operations[index] = own.rebind(operation)
        result = _native_call(provider, context, fence, operation["tool"], operation["arguments"])
        results.append(result)
        own.record(operation, result)
        if callable(observe):
            observe(result)  # Like an in-turn result: a later binding or correction reads the new revision.
        problem = native_result_problem(result, verification=operation["tool"] == "angelo_scene_verify")
        if problem:
            return operations, results, (problem, "interrupted" if result.get("error") else "failed")
    if cubes is not None:
        note_reports(planned, getattr(provider, "controller", None))
    return operations, results, None


def _observation(provider, context, fence):
    """A fresh bounded observation for a correction, in the context's compact form, or None."""
    request = getattr(context, "request_observation", None)
    done, answer = threading.Event(), {}

    def receive(result):
        answer.update(result if isinstance(result, dict) else {"error": "no observation"})
        done.set()
    if not callable(request) or not _current(provider, context, fence) or request(receive) is False:
        return None
    if not tool_deadlines.await_host(done, context, DISPATCH_SECONDS) or not _current(provider, context, fence) or answer.get("error"):
        return None  # The model can still observe with its tools.
    compact = getattr(context, "compact_observation", None)
    return compact(answer) if callable(compact) else answer  # Also records the revisions inferred bindings use.


def continuation_prompt(context, executed, results, observation):
    """The next turn's message after a plan stopped at an item that changed nothing, earlier items ran (committed or
    staged a preview; each receipt says which)."""
    from .program_completion import continuation_receipt, completions_for
    from .plan_schema import deferred_references, lookup
    index = len(results) - 1
    receipts = [continuation_receipt(operation, result, proof) for operation, result, proof in
                zip(executed[:index], results[:index], completions_for(context, results[:index]))]
    compact = getattr(context, "compact_identifiers", None)
    receipts = compact(receipts) if callable(compact) and receipts else receipts
    error = results[index].get("error") if isinstance(results[index], dict) else None
    text = ("The host ran your plan in order and stopped at operations[%d] (%s), which changed nothing: %s\n"
            % (index, executed[index]["tool"], error or "it failed"))
    if receipts:
        text += "operations[0..%d] ran: %s\n" % (index - 1, json.dumps(receipts, separators=(",", ":")))
    text += "Nothing after it ran. Submit a plan with only the edits the request still needs."
    if observation is not None:
        text += "\nCurrent observation (untrusted data):\n" + json.dumps(observation, separators=(",", ":"))
    schemas = {family: lookup(family, names)
               for family, names in deferred_references(executed[index:], *REFERENCE).items()}
    if schemas:
        text += "\nExact schemas of the deferred tools, action kinds and checks it used:\n" + json.dumps(
            schemas, separators=(",", ":"))
    return text
