# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude's direct-tools route (opt-in): Angelo's tools as calls a Claude Code session makes one at a time.

The shipped Claude route answers every model round with one ``StructuredOutput``
plan (``providers.ClaudeProvider._run_plans``). On this route the session calls
Angelo's tools itself, as Blender + MCP and Angelo's Codex route let a model do,
and decides for itself whether to build in one call or iterate.

Off by default (``DEFAULT``). ``ANGELO_CLAUDE_DIRECT_TOOLS=1`` in the app's
environment turns it on and ``=0`` off, whatever the default (``requested``): the controller hands it to each request's context like
debug mode (``Controller._bind_session``); a change relaunches the session
(``ClaudeProvider._runtime_binding``). Off, nothing here runs and the
launch, initialization and turns are the shipped route's, byte for byte.

Mechanism: Claude Code's own channel for host-served tools (an "SDK MCP server",
checked in 2.1.301). The initialize request names the server
(``sdkMcpServers``); Claude Code then sends ``control_request`` ``mcp_message``
on stdout (initialize, tools/list, tools/call, notifications) and Angelo answers
each with a ``control_response`` ``{"mcp_response": ...}`` on stdin (``answer``).
No MCP server process, socket, port or SDK dependency exists; the launch keeps
every shipped argument except that ``--allowedTools`` names the server
(``ALLOW_RULE``). The tools load with the request (ToolSearch is not among
``--tools``), SDK servers have no idle timeout and a call's default timeout is
about a day, so the host's own deadlines (``tool_deadlines``) govern approvals.

What the model sees (``claude_direct_schema.tool_list``): every tool the request allows, in a fixed
order, by name and argument schema only, as the plan's variants show them (the
instructions carry each tool's guidance). Core tools carry the typed plan's exact argument schemas
(``plan_schema.plan_arguments`` with ``CLAUDE_CORE_ACTIONS``: short identifiers,
core kinds exact and the rest named, tagged unions as if/then, no maxLength,
the script's length targets); deferred tools are named with a generic object
schema and a pointer to ``angelo_agent_capabilities``, as the plan names them.
Instructions: the base text every Claude session reads, then
``claude_direct_text.CLAUDE_TOOLS_INSTRUCTIONS`` instead of the plan text.

Each call (``DirectRequest.call``) runs as a one-operation plan through the
host code the plan route uses: ``screen_plan`` (validation with exact schemas
back, the view budget, view normalisation; one invalid operation is rejected whole),
``run_operations`` (visual evidence, completions, the self-check sharer ahead of
a job start), self-check notes, kept views captured again after an edit
(``stale_views``), ``present_plan_checks``, ``plan_results`` and the context's
short identifiers with the lookups recorded for inferred bindings. Approval,
Undo, journaling, invocation guards and script consent are
``Controller.request_tool``'s, unchanged. Calls of one request run one at a
time, in arrival order.

Where the plan route ends a turn after a failure (a decision such as a decline,
Stop or a document change; an edit that failed and may have changed the scene;
a used correction budget: ``plan_recovery.correction``), this route answers that
call saying so and what it changed (``_ended``), tells the person as the plan route
does, and refuses every later call of the request (``closed``), so nothing
further runs before the person looks; a host error fails the request with it. A call
the host rejects before running anything returns its error and exact schemas, as
on Codex, without a budget; ``MAX_CALLS`` bounds a runaway request.
"""

import json
import threading

from . import model_results, product_env, query_observations, request_path, worker_news
from .claude_direct_text import CLAUDE_TOOLS_INSTRUCTIONS
from .claude_evidence import ClaudeVisualEvidence
from .claude_outcomes import native_result_problem, present_plan_checks
from .claude_transport import prompt_fields
from .claude_direct_notes import ENDED, LIMIT, MAY_HAVE_CHANGED, NOT_CURRENT, NOT_RUN, REFUSED, call_note, change_state
from .claude_direct_schema import tool_list, typed_arguments  # noqa: F401  (tool_list: tests and probes)
from .plan_kinds import CLAUDE_CORE_ACTIONS
from .plan_recovery import correction, decided, ended_row, stopped_note, unchanged_failure, run_operations
from .plan_schema import deferred_references, deferred_schemas, lookup
from .plan_validation import screen_plan
from .program_completion import completions_for
from .protocol import RpcError
from .tool_specs import TOOL_MAP
from .ui_text import notice

FLAG = "CLAUDE_DIRECT_TOOLS"
# Whether a Claude session uses this route when the app's environment does not say (``requested``). The A/B against
# the plan route decides it (doc/angelo/agent_integration.rst, "Direct tools").
DEFAULT = False
SERVER = "angelo"
PREFIX = "mcp__%s__" % SERVER
ALLOW_RULE = "mcp__" + SERVER   # One --allowedTools rule for every tool of the server.
PROTOCOL = "2024-11-05"         # The MCP revision Claude Code's SDK servers answer with.
INSTRUCTIONS = CLAUDE_TOOLS_INSTRUCTIONS
# A runaway guard, far above any recorded request (the plan route allows 16 rounds of up to 64 operations).
MAX_CALLS = 256
# A direct session's own values for the Claude Code settings that would change the route, whatever the person's
# shell holds: results are never cut at Claude Code's 25,000-token default (the plan route has no such cap; host
# results are bounded by the host), a call waits for the host's own deadlines (tool_deadlines), and no call moves to
# the background.
SESSION_ENVIRONMENT = {"MAX_MCP_OUTPUT_TOKENS": "200000", "MCP_TOOL_TIMEOUT": "100000000",
                       "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "0"}


def requested(environ=None):
    """Whether Claude sessions use this route: ``ANGELO_CLAUDE_DIRECT_TOOLS=1`` on, ``=0`` off, else ``DEFAULT``."""
    value = product_env.get(FLAG, environ)
    return {"1": True, "0": False}.get(value, DEFAULT)


def enabled(context):
    """Whether this request's context (``routing.RuntimeContext`` or a test double) uses the route."""
    return getattr(context, "claude_direct_tools", False) is True


def initialization_request(instructions):
    """The session's initialize request: the server named, no plan schema (``claude_transport.initialization_request``).

    ``sdkMcpServerConfigs`` keeps a long approval in the foreground on a
    runtime that would background it; a runtime that predates the field ignores it.
    """
    return {"type": "control_request", "request_id": "angelo-init", "request": {
        "subtype": "initialize", "hooks": None, **prompt_fields(instructions),
        "sdkMcpServers": [SERVER], "sdkMcpServerConfigs": {SERVER: {"disableAutoBackground": True}}}}


def _content(text):
    return [{"type": "text", "text": text}]


def _error(text):
    return {"content": _content(json.dumps({"error": text}, separators=(",", ":"))), "isError": True}


# The MCP channel (reader thread) -----------------------------------------------------------------

def answer(provider, transport, event):
    """Answer one ``mcp_message`` control request. tools/call runs on its own thread; the rest answer at once."""
    request = event.get("request") or {}
    message = request.get("message") if isinstance(request.get("message"), dict) else {}
    method, identity = message.get("method"), message.get("id")
    with transport.lock:
        turn = transport.turn
        owner = turn.owner if turn is not None else transport.owner

    def reply(payload):
        try:
            provider._write({"type": "control_response", "response": {
                "subtype": "success", "request_id": event.get("request_id"),
                "response": {"mcp_response": payload}}}, transport, owner=owner)
            return True
        except (OSError, RpcError, ValueError):
            return False   # The session is gone or no longer this request's; Claude Code ends the call with it.

    def result(value):
        news = value.get(worker_news.CARRY) if isinstance(value, dict) else None
        if news is not None:   # Host bookkeeping: never written; told only once this reply is (worker_news).
            value = {key: item for key, item in value.items() if key != worker_news.CARRY}
        if reply({"jsonrpc": "2.0", "id": identity, "result": value}):
            worker_news.acknowledge(news)

    if request.get("server_name") != SERVER:
        return reply({"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Unknown server"}})
    if identity is None:   # A notification (notifications/initialized): acknowledged, nothing to do.
        return result({})
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        version = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else PROTOCOL
        return result({"protocolVersion": version, "capabilities": {"tools": {}},
                       "serverInfo": {"name": SERVER, "version": "1"}})
    if method == "tools/list":
        return result({"tools": tool_list(getattr(owner.controller, "allowed_tools", None))})
    if method != "tools/call":
        return reply({"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}})
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    state = getattr(provider, "_direct", None)
    if (turn is None or state is None or not state.owner.same_request(turn.owner)
            or not provider._current(turn.owner, transport)):
        return result(_error(NOT_CURRENT))
    # The same call delivered again (its JSON-RPC id, else the control request's) runs once: a delivery while it
    # runs gets its answer when it ends, a later one the answer it had.
    key = ("call", identity) if identity is not None else ("request", event.get("request_id"))
    if not state.admit(key, result):
        return None
    ticket = state.arrived()   # Busy from now: the turn's silence clock rests while the host works (turn_activity).

    def work():
        try:
            value = state.call(params.get("name"), params.get("arguments"), ticket)
        except Exception as error:  # noqa: BLE001 - every call is answered; the error is the result.
            value = _error(state.why(error))
        finally:
            state.left()
        state.answered(key, value)
    threading.Thread(target=work, daemon=True, name="AngeloClaudeCall").start()


# One request's calls -----------------------------------------------------------------------------

WHY_LIMIT = 300   # Characters of a reason the model reads on one line (``DirectRequest.why``).


def clip_words(text, limit):
    """``text`` at most ``limit`` characters, cut at a word boundary and ended with "…" when cut: a reason never ends
    in half a word, and never in half a path the model would act on (Linux CI, 2026-10-24: a refusal ended in
    "open /tmp/shl-vbetds" when the cut fell inside the project's path). One word longer than ``limit`` is cut
    where it must, still with "…"."""
    if len(text) <= limit:
        return text
    head = text[:limit - 1]   # Room for the "…".
    if text[limit - 1] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;:") + "…"


class DirectRequest:
    """One request's direct calls: run one at a time through the plan route's host code (module docstring)."""

    def __init__(self, provider, context, owner, checks):
        self.provider, self.context, self.owner, self.checks = provider, context, owner, checks
        self.evidence = ClaudeVisualEvidence(context.references)
        self.calls = []        # (operation, result) of every successful call, for the host's account at the end.
        self.completions = []
        self.closed = None     # Why later calls are refused (module docstring), once the request ended.
        self.closed_by_call = False   # A call's failure or decision ended the request: the person has read why.
        self.error = None      # A host error a call raised: the request fails with it (``run``), as a plan's does.
        self.status = None     # The request's status when a call ended it: "failed" or "interrupted".
        self.count = self.failures = self.scripts = 0
        self.dispatched = False   # Whether the call running now reached the host's dispatch (``_native``).
        self._busy, self._count_lock, self._order = 0, threading.Lock(), threading.Condition()
        self._tickets, self._next, self._running = 0, 0, threading.Lock()
        self._waiting, self._answers = {}, {}   # Each call's deliveries awaiting its result, and results given.

    def admit(self, key, respond):
        """True when the call ``key`` names is new: it runs, and ``answered`` gives ``respond`` its result.

        A delivery of a call this request has had already never runs it again: while it runs, ``respond`` waits for
        its result; after, it gets the result at once.
        """
        with self._count_lock:
            if key in self._answers:
                cached = self._answers[key]
            elif key in self._waiting:
                self._waiting[key].append(respond)
                return False
            else:
                self._waiting[key] = [respond]
                return True
        respond(cached)
        return False

    def answered(self, key, value):
        """Give every delivery of the call ``key`` its result; keep it for a later delivery (bounded)."""
        with self._count_lock:
            waiting = self._waiting.pop(key, [])
            if len(self._answers) < 4 * MAX_CALLS:
                self._answers[key] = value
        for respond in waiting:
            respond(value)

    def arrived(self):
        """This call's place in the order calls arrived in (``call`` runs them in it)."""
        with self._count_lock:
            self._busy += 1
            self._tickets += 1
            return self._tickets - 1

    def left(self):
        with self._count_lock:
            self._busy -= 1

    def busy(self):
        with self._count_lock:
            return self._busy > 0

    def current(self):
        return self.provider._current(self.owner)

    def close(self, why, status=None):
        self.closed = self.closed or why
        self.status = self.status or status
        self.closed_by_call = self.closed_by_call or status is not None

    def why(self, error):
        """A host error or reason as the model may read it: redacted, on one bounded line, ending with a full stop."""
        host = getattr(self.context, "host", self.context)
        text = clip_words(" ".join(str(getattr(host, "redact", str)(error) or "it failed").split()), WHY_LIMIT)
        return text if text.endswith((".", "!", "?", "…")) else text + "."

    def _ended(self, why, status, state):
        """End the request at the call running now (later calls are refused); the note that call's result carries."""
        self.close(self.why(why), status)
        return ENDED.format(why=self.closed, state=state)

    def _native(self, operation):
        self.dispatched = True
        return self.provider._native_call(operation["tool"], operation["arguments"], owner=self.owner)

    def _emit(self, kind, data):
        self.provider._emit(self.owner, kind, data)

    def call(self, name, arguments, ticket=None):
        """One tool call's MCP result (``{"content": [...], "isError": bool}``), run after the calls that arrived
        before it (``ticket``, from ``arrived``) and before the ones after it."""
        with self._order:
            while ticket is not None and self._next != ticket:
                self._order.wait()
        try:
            with self._running:   # One at a time, whoever calls (a call without a ticket included).
                return self._call(name, arguments)
        finally:
            if ticket is not None:
                with self._order:
                    self._next += 1
                    self._order.notify_all()

    def _call(self, name, arguments):
        if not self.current():
            return _error(NOT_CURRENT)
        if self.closed:
            return _error(REFUSED.format(why=self.closed.rstrip(".")))
        self.count += 1
        if self.count > MAX_CALLS:   # The plan route's round limit fails its request; so does this.
            self.error = self.error or RpcError(LIMIT % MAX_CALLS)
            return _error(self._ended(LIMIT % MAX_CALLS, "failed", NOT_RUN))
        allowed = getattr(self.context, "allowed_tools", None)
        if not isinstance(name, str) or name not in TOOL_MAP or (allowed is not None and name not in allowed):
            return _error("This request has no Angelo tool named %r; this call did not run." % str(name)[:80])
        self.dispatched = False
        try:
            # A deferred tool's values arrive as strings where its declared schema types nothing (typed_arguments).
            return self._run({"tool": name, "arguments": typed_arguments(name, arguments if isinstance(arguments, dict) else {})})
        except Exception as error:  # noqa: BLE001 - where the plan route's loop raises, its request fails.
            # The request fails with this error once the turn ends (``run``), as a plan's does; until then no later
            # call runs. A call that reached dispatch may have changed the scene: the model is told so.
            self.error = self.error or error
            return _error(self._ended("the host stopped it: %s" % error, "failed",
                                      change_state(name, None, None, self.dispatched)))

    def _run(self, operation):
        context, operations = self.context, [operation]
        screened = screen_plan(operations, getattr(context, "allowed_tools", None),
                               getattr(context, "resolve_call", None), self.evidence.problem)
        if screened.problems:   # One operation: an invalid lookup or job start is rejected whole, never run.
            getattr(context, "record_refused_plan", lambda *_: None)(operations, screened.problems)
            return self._rejected(operation, screened.problems)
        run = run_operations(screened.operations, self._native, self.current, self.evidence, context.references,
                             completion=lambda result: completions_for(context, [result])[0],
                             screened=screened, before=self.checks.sharer(context))
        if run is None or not self.current():
            return _error(NOT_CURRENT)
        ran = screened.operations
        result = run.results[0] if run.results else None
        if not run.failure and not run.failed_lookups and native_result_problem(result) is None:
            # Only a call that succeeded counts toward the host's closing account (certified_completion's contract).
            self.calls.append((ran[0], result))
            self.completions.append(run.completions[0] if run.completions else None)
        failed = self.checks.read(ran, run.results)   # Every script check of the call, as the plan route reads them.
        extra = {}
        notes = [self._failure_note(ran, run, extra)]
        notes.append(None if run.failure else self.checks.note(failed))
        if self.evidence.edited and self.evidence.dirty and not self.closed:
            refreshed = self.evidence.refresh(self._native, self.current)
            if refreshed is None:
                return _error(NOT_CURRENT)
            if isinstance(refreshed, str):   # A capture the person or the host decided: the plan route ends here too.
                self._emit("message", {"role": "system", **notice(
                    "Edits may have been applied, but fresh visual verification failed: {problem}. Inspect the scene; "
                    "do not automatically retry the edits.", problem=refreshed)})
                notes.append(self._ended(refreshed, "interrupted", MAY_HAVE_CHANGED))
            else:
                extra.update(refreshed)
        return self._packed(ran, run, extra, [note for note in notes if note])

    def _failure_note(self, ran, run, extra):
        """What the plan route's correction decides for a failed call, said in this call's result; None if it ran.

        A correction the plan route would ask for goes back as this call's note, with the exact schemas of the deferred
        tools, kinds and checks it used (``extra["exact_schemas"]``), as the plan route's correction prompt carries
        them. The person reads the failed call's own row, as on Codex; the plan route's "asking for a corrected
        plan" rows describe a plan this route does not have. Where the plan route ends the turn, the request ends
        here (``_ended``) and the person reads the same row.
        """
        if not run.failure and not run.failed_lookups:
            return None
        unchanged = unchanged_failure(ran, run.results) if run.failure else None
        note = call_note(stopped_note(ran, run, unchanged), ran[0]["tool"])
        if not run.failure:
            return note
        fix = None if decided(run.results[-1] if run.results else None) else correction(
            ran, run, unchanged, self.failures, self.scripts)
        if fix is None:
            row = ended_row(ran, run, run.failure[0])
            if row:
                self._emit("message", row)
            result = run.results[-1] if run.results and isinstance(run.results[-1], dict) else {}
            state = change_state(ran[0]["tool"], result, unchanged, self.dispatched)
            return " ".join(filter(None, (note, self._ended(run.failure[0], run.failure[1], state))))
        self.failures, self.scripts = self.failures + (not fix.script), self.scripts + fix.script
        if fix.prompt is not None:
            schemas = {family: lookup(family, names)
                       for family, names in deferred_references(ran, CLAUDE_CORE_ACTIONS).items()}
            if schemas:
                extra["exact_schemas"] = schemas
        return note

    def _rejected(self, operation, problems):
        """A call the host rejected before running anything: its error and the exact schemas it needs."""
        lines = [call_note(line, operation["tool"]) for line in problems]
        value = {"error": " ".join(lines), "error_code": "VALIDATION", "scene_changed": False}
        try:
            schemas = deferred_schemas(operation["tool"], operation["arguments"], CLAUDE_CORE_ACTIONS)
        except Exception:  # noqa: BLE001 - untrusted arguments: the error alone still stands.
            schemas = {}
        if schemas:
            value["exact_schemas"] = schemas
        compact = getattr(self.context, "compact_identifiers", None)
        value = compact(value) if callable(compact) else value
        return {"content": _content(json.dumps(value, ensure_ascii=False, separators=(",", ":"))), "isError": True}

    def _packed(self, ran, run, extra, notes):
        """The call's result as the model reads it: the plan route's presentation of a one-operation plan."""
        context = self.context
        applied = {"success": not (run.failure or run.failed_lookups or run.refused), "operations": run.results}
        applied = present_plan_checks(ran, applied, completions=run.completions)
        applied = model_results.plan_results(ran, applied, observation_follows=False)
        shown = applied["operations"][0] if applied.get("operations") else {"error": "No result"}
        records = self.evidence.take_images()
        value = shown
        if extra or records:
            value = {"result": shown, **extra}   # Kept views captured again, a correction's exact schemas.
            if records:
                value["visual_evidence"] = [{"image": index + 1, "id": record["id"], "name": record.get("name", ""),
                                             "view": record.get("view")} for index, record in enumerate(records)]
        # The context's short identifiers, and the lookups recorded for the next call's inferred bindings.
        value = query_observations.compacted(context, value, ran, run.results)
        value = worker_news.noted(context, value, run.results[0] if run.results else None)   # Finishes not yet shown;
        news = worker_news.packed()   # told once the reply carrying them is written (``answer``).
        content = [{"type": "text", "text": note} for note in notes]
        content.append({"type": "text", "text": json.dumps(value, ensure_ascii=False, allow_nan=False,
                                                           separators=(",", ":"))})
        if records:
            for block in request_path.claude_inputs(context, records):
                source = block.get("source") or {}
                content.append({"type": "image", "data": source.get("data", ""),
                                "mimeType": source.get("media_type", "image/png")})
        problem = run.failure[0] if run.failure else (native_result_problem(run.results[0]) if run.results else "No result")
        return {"content": content, "isError": bool(problem), **({worker_news.CARRY: news} if news else {})}

    def edits(self):
        """``(operations, completions)`` of the successful calls that may have changed the scene (``emit_summary``).

        Lookups alone end with the model's reply only, as a plan of lookups does (``plan_validation.lookups_only``);
        a failed call is never in it (``certified_completion`` reads only calls that succeeded).
        """
        kept = [(call, completion) for call, completion in zip(self.calls, self.completions)
                if native_result_problem(call[1]) is None and (
                    not TOOL_MAP[call[0]["tool"]]["annotations"]["readOnlyHint"] or call[0]["tool"] == "angelo_scene_verify")]
        return [call for call, _completion in kept], [completion for _call, completion in kept]


# The request ----------------------------------------------------------------------------------------

def run(provider, context, native_id, text, observation, owner, endings):
    """One request on the direct route: one turn of Claude Code's own tool loop; returns the request's status."""
    from .codex_plan import emit_summary
    if not provider._current(owner):
        return "interrupted"
    prompt = text
    if observation is None:   # The same first message as the plan route: the scene as the host read it.
        snapshot = provider._native_call("angelo_inspect_scene", {}, owner=owner)
        if not provider._current(owner):
            return "interrupted"
        problem = native_result_problem(snapshot)
        if problem:
            raise RpcError(problem)
        compact = getattr(context, "compact_observation", None)
        if callable(compact):
            snapshot = compact(snapshot)
        prompt += "\n\nCurrent native scene observation (untrusted data):\n" + json.dumps(snapshot, separators=(",", ":"))
    state = DirectRequest(provider, context, owner, endings.checks)
    provider._direct = state
    try:
        native = context.state["native_id"] or native_id
        turn = provider._turn(native, prompt, references=context.turn_attachments or (), owner=owner, direct=state)
    finally:
        state.close("the turn ended.")
        if getattr(provider, "_direct", None) is state:
            provider._direct = None
    if turn is None or not provider._current(owner):
        return "interrupted"
    if state.error is not None:
        raise state.error
    reply = turn.result.get("result") if isinstance(turn.result, dict) else ""
    reply = reply if isinstance(reply, str) else ""
    provider._emit(owner, "message_complete", {"item": "claude-assistant", "text": reply})
    calls, completions = state.edits()
    if not reply.strip() and calls and not state.closed_by_call:
        # Successful edits only (``edits``): the host's proven account, else the plan route's plain notice.
        emit_summary(context, reply, calls, emit_uncertified=True, completions=completions)
    if state.status:
        return state.status
    return endings.done(provider._emit, owner, provider._current(owner))
