# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-in ``claude`` and ``codex`` executables for window fixtures. No network, account or inference.

GUI suites put a ``bin`` folder first on PATH with two shell wrappers that run
this file under the app's own Python: ``<python> fake_provider_cli.py claude
"$@"``. It answers only what the app asks a provider CLI during setup:

* ``claude --version``, ``claude auth status``, ``claude auth login --claudeai``
  and the stream-json ``initialize`` handshake that lists models; a user turn
  gets the result chosen by ``ANGELO_FAKE_CLI_TURN`` (``limit``, ``offline``
  or, by default, a plain error), or, with ``ANGELO_FAKE_CLI_ROUNDS`` naming a
  folder, the next scripted round from it (``play_round``): the typed plans and
  stream events a model would send, written by the suite itself. A round item
  ``{"stream_plan": {...}}`` streams one StructuredOutput call the way Claude
  Code 2.1.293 writes it with fine-grained tool streaming (``plan_events``);
  with a ``gate`` folder, each fragment waits for the suite's acknowledgement
  of the one before (``wait_for_gate``), so no frame is missed on a busy Mac.
* ``<state>/models.json`` replaces either tool's model list, makes listing it fail, or holds the listing until the
  suite releases it (``model_override``).
* ``codex login status``, ``codex login`` and the app-server requests the
  catalog makes (``initialize``, ``account/read``, ``config/read``,
  ``model/list``). A chat's threads and turns too (``thread/start``,
  ``thread/resume``, ``turn/start``, ``turn/interrupt``): each ``turn/start``
  takes the next outcome of ``ANGELO_FAKE_CLI_CODEX_TURNS`` (a comma list kept
  in the state folder, so a restarted app-server continues it): ``rate`` (the
  RC4 gate's throttle, ``responseTooManyFailedAttempts`` HTTP 429), ``usage``
  (``usageLimitExceeded``), ``ok`` (an empty plan with the reply "Done.") or
  ``held`` (``ok`` once the suite releases ``codex_turn_hold``: the turn runs
  for exactly as long as the suite needs, on any machine load). A suite scripts
  the turns still to come with ``script_codex_turns``.
  It replies, then sends the turn's notifications in the order and shape codex
  0.162 does, and logs each request to ``<state>/codex-requests.jsonl``.
  ``call`` and ``call_approval`` (steering, 1.0.10) run the turn on a thread of its own: it holds at
  ``ANGELO_FAKE_CLI_CODEX_GATE`` (one hold per turn) so the suite can type, then makes one dynamic tool call
  (``angelo_inspect_scene``, or a script that waits for the person's approval) and waits for Angelo's answer, as
  Angelo's runtime holds a cell. ``turn/steer`` for the open turn is kept (logged) and read at the next model
  request, as Codex does: each one's user item, with its ``clientUserMessageId`` as ``clientId``, then a reply
  naming what it read ("Read: ..."); a steer for no open turn is refused "no active turn to steer".

Sign-in is a marker file in the profile the real CLI would use
(``CLAUDE_CONFIG_DIR`` or ``$HOME/.claude``; ``CODEX_HOME`` or
``$HOME/.codex``), so signing in a private profile never signs in the
default one. ``ANGELO_FAKE_CLI_STATE`` collects one JSON line per call;
``<state>/login-fails`` makes the next login fail. ``login`` never opens a
browser: it waits ``ANGELO_FAKE_CLI_LOGIN_SECONDS`` (default 1) instead, or,
with ``ANGELO_FAKE_CLI_LOGIN_GATE`` naming a folder, until the suite releases
it (``release``): a suite that must see a sign-in in progress holds it there
for exactly as long as it needs, on any machine load.

Holds (1.0.8): a moment a suite must observe (a sign-in in progress, a plan
being written, a pause before the runtime's verdict) is held by the stand-in
until the suite says it has seen it, never timed with a sleep the suite has to
catch. ``hold(folder)`` is a round item that waits there; ``held(folder)``
says the stand-in has reached it and ``release(folder)`` lets it go on. A
sleep raced a busy main thread: a stall longer than the pause applied both
sides of it in one pump pass (test_blender_turn_recovery's "Preparing the
next steps", 2.5 s; test_blender_first_run's 1 s sign-in).
"""

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

MARKER = "fake-signed-in"
# The tested ceilings in angelo_agent.cli_runtime.RUNTIMES: a fixture inside the
# tested range gets no version notice and passes the Codex floor check.
CLAUDE_VERSION = "2.1.301"
CODEX_VERSION = "0.162.0"
CLAUDE_MODELS = [
    {"value": "default", "displayName": "Default (recommended)", "description": "Fixture model list",
     "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "opus", "displayName": "Opus", "description": "Fixture", "supportedEffortLevels": ["low", "medium", "high"]},
]
CODEX_MODELS = [
    {"model": "gpt-6.1-sol", "displayName": "GPT-6.1 Sol", "isDefault": True, "defaultReasoningEffort": "low",
     "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}],
     "inputModalities": ["text", "image"]},
]
GATE_SECONDS = 120  # A gated stream goes on after this long without an acknowledgement (the suite has failed).
# A hold's own limit (a gate or a login): GATE_SECONDS, or what ANGELO_FAKE_CLI_HOLD_SECONDS says ("inf": only a
# release ends it). A host test that checks a hold lasts until released sets it, so no clock decides that check.
HOLD_SECONDS = float(os.environ.get("ANGELO_FAKE_CLI_HOLD_SECONDS") or GATE_SECONDS)
TURN_RESULTS = {
    "limit": "You've hit your session limit · resets 4:30pm (UTC)",
    "offline": "API Error: Connection error.",
}


def record(tool, arguments):
    state = os.environ.get("ANGELO_FAKE_CLI_STATE")
    if state:
        Path(state).mkdir(parents=True, exist_ok=True)
        with open(Path(state) / "calls.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"tool": tool, "arguments": arguments,
                                     "profile": str(profile(tool))}) + "\n")


def profile(tool):
    if tool == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path(os.environ["HOME"]) / ".claude")
    return Path(os.environ.get("CODEX_HOME") or Path(os.environ["HOME"]) / ".codex")


def signed_in(tool):
    return (profile(tool) / MARKER).is_file()


LOGIN_GATE = "ANGELO_FAKE_CLI_LOGIN_GATE"


def login(tool):
    gate = os.environ.get(LOGIN_GATE)
    if gate:   # Held until the suite has seen the sign-in in progress (``release``).
        wait_for_gate(gate, _next_hold(gate))
    else:
        time.sleep(float(os.environ.get("ANGELO_FAKE_CLI_LOGIN_SECONDS", "1")))
    failure = Path(os.environ.get("ANGELO_FAKE_CLI_STATE", "/nonexistent")) / "login-fails"
    if failure.is_file():
        failure.unlink()
        print("Login was cancelled in the browser.")
        return 1
    profile(tool).mkdir(parents=True, exist_ok=True)
    (profile(tool) / MARKER).write_text("fixture\n")
    print("Login successful.")
    return 0


_SEND_LOCK = threading.Lock()   # A call turn writes from its own thread: one whole line at a time.


def send(message):
    line = json.dumps(message) + "\n"
    with _SEND_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def plan_fragments(plan, chars):
    """The ``input_json_delta`` texts ``plan_events`` streams ``plan`` as, in order."""
    text, chars = json.dumps(plan, ensure_ascii=False), max(1, int(chars))
    return [text[start:start + chars] for start in range(0, len(text), chars)]


def plan_events(plan, index=0, tool_id="toolu_fixture_plan", chars=16, seconds=0.1, session="fixture", gate=None):
    """One StructuredOutput call as Claude Code 2.1.293 streams it with fine-grained tool streaming.

    The call's input (``plan``, a reply among its fields, in the key order it
    was written) arrives as ``input_json_delta`` fragments of ``chars``
    characters, ``seconds`` apart, between the block's start and stop; then the
    runtime sends the whole call as an assistant message. Whether the runtime
    accepts the call (a ``tool_result``) is the round's next events. With a
    ``gate`` folder, every fragment is followed by a ``{"gate": ...}`` item:
    the next one waits until the suite says it drew this one (``wait_for_gate``).
    """
    def stream(event):
        return {"type": "stream_event", "event": event, "session_id": session}
    events = [stream({"type": "content_block_start", "index": index, "content_block": {
        "type": "tool_use", "id": tool_id, "name": "StructuredOutput", "input": {}}})]
    for number, fragment in enumerate(plan_fragments(plan, chars)):
        if number and seconds:
            events.append({"sleep": seconds})
        events.append(stream({"type": "content_block_delta", "index": index, "delta": {
            "type": "input_json_delta", "partial_json": fragment}}))
        if gate:
            events.append({"gate": {"folder": str(gate), "fragment": number}})
    events.append(stream({"type": "content_block_stop", "index": index}))
    events.append({"type": "assistant", "session_id": session, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "StructuredOutput", "input": plan}]}})
    return events


def round_events(events):
    """A round's items with each ``{"stream_plan": {...plan_events arguments}}`` expanded."""
    for item in events:
        if "stream_plan" in item:
            yield from plan_events(**item["stream_plan"])
        else:
            yield item


def _write_atomically(path, text):
    """Write ``path`` whole. The other side polls these files, opening each for a moment without delete sharing,
    and Windows refuses a replace (even a POSIX-semantics one) while such a handle is open: retried for as long as
    a poll could hold it, never past GATE_SECONDS."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    deadline = time.monotonic() + GATE_SECONDS
    while True:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def wait_for_gate(folder, fragment):
    """Hold the stream after fragment ``fragment`` until the suite acknowledges it.

    ``<folder>/sent`` names the fragment just sent; the suite writes its number
    to ``<folder>/ack`` once it has drawn and checked the frame that fragment
    produced, so however slowly the app draws, every fragment gets its frame.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    _write_atomically(folder / "sent", str(fragment))
    deadline = time.monotonic() + HOLD_SECONDS
    while time.monotonic() < deadline:
        try:
            if int((folder / "ack").read_text(encoding="utf-8")) >= fragment:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.02)


def _next_hold(folder):
    """The number of this hold in ``folder``: each login held there is the next fragment of its gate."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    counter = folder / "holds"
    number = int(counter.read_text(encoding="utf-8")) if counter.is_file() else 0
    _write_atomically(counter, str(number + 1))
    return number


def fresh(folder):
    """``folder`` emptied for a new hold (or login gate): nothing left from a run before in the same output folder."""
    import shutil
    shutil.rmtree(folder, ignore_errors=True)
    return Path(folder)


def hold(folder):
    """A round item: the stream waits here until the suite releases ``folder`` (``release``). One folder per hold."""
    return {"gate": {"folder": str(folder), "fragment": 0}}


def gate_sent(folder):
    """The fragment the stand-in sent last and holds the next one after (``wait_for_gate``); -1 before the first."""
    try:
        return int((Path(folder) / "sent").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return -1


def acknowledge(folder, fragment):
    """Tell the stand-in that fragment ``fragment`` was seen and checked: it goes on to the next."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    _write_atomically(Path(folder) / "ack", str(fragment))


def held(folder):
    """Whether the stand-in waits at ``folder`` now: it reached a hold (or a login) the suite has not released."""
    sent = gate_sent(folder)
    try:
        acknowledged = int((Path(folder) / "ack").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        acknowledged = -1
    return sent > acknowledged


def release(folder):
    """Let the stand-in go on from the hold (or the login) it waits at in ``folder``; nothing when none waits."""
    if held(folder):
        acknowledge(folder, gate_sent(folder))


def play_round(folder, event):
    """Answer one user message with the next scripted round of ``folder``.

    ``round-<n>.json`` is a list of stream-json events sent in order; an item
    ``{"sleep": seconds}`` waits instead, ``{"stream_plan": ...}`` streams a
    plan (``plan_events``) and ``{"gate": ...}`` waits for the suite (``wait_for_gate``). The message's text is saved as
    ``prompt-<n>.txt`` first, so a suite can check what the host told the model.
    A missing round answers with a runtime error, never with an invented plan.
    """
    folder.mkdir(parents=True, exist_ok=True)
    counter = folder / "next"
    index = int(counter.read_text()) if counter.is_file() else 0
    counter.write_text(str(index + 1))
    content = event.get("message", {}).get("content")
    text = content if isinstance(content, str) else next((item.get("text", "") for item in content or ()
                                                          if isinstance(item, dict) and item.get("type") == "text"), "")
    (folder / ("prompt-%d.txt" % index)).write_text(text, encoding="utf-8")
    script = folder / ("round-%d.json" % index)
    events = json.loads(script.read_text(encoding="utf-8")) if script.is_file() else [
        {"type": "result", "subtype": "success", "is_error": True, "session_id": "fixture",
         "result": "Fixture runtime: no scripted round %d" % index}]
    for item in round_events(events):
        if "sleep" in item:
            time.sleep(float(item["sleep"]))
        elif "gate" in item:
            wait_for_gate(**item["gate"])
        else:
            send(item)


def model_override(tool, default):
    """The model list a handshake answers with: ``<state>/models.json`` (``{"claude": [...] | "fail", "codex": ...}``,
    and ``"claude_hold"`` / ``"codex_hold"``: a folder where the handshake or ``model/list`` waits, after reading its
    list, until the suite releases it: a slow catalog the suite sees loading for as long as it needs)
    replaces ``default`` for a tool while it names it, so a suite can drive single-model, no-default, empty and
    failing catalogs; read on every handshake, so a suite can change it between catalog loads."""
    path = _codex_state() / "models.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(tool, default)
    except (OSError, ValueError):
        return default


def _hold_listing(key):
    """Wait where ``models.json`` names a hold for this listing, if it does (``model_override``)."""
    folder = model_override(key, None)
    if folder:
        wait_for_gate(folder, _next_hold(folder))


def claude_session():
    rounds = os.environ.get("ANGELO_FAKE_CLI_ROUNDS")
    for line in sys.stdin:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "control_request" and event.get("request", {}).get("subtype") == "initialize":
            models = model_override("claude", CLAUDE_MODELS)
            _hold_listing("claude_hold")   # A slow handshake: the chat's loading state.
            if models == "fail":
                send({"type": "control_response", "response": {"subtype": "error", "request_id": event["request_id"],
                                                               "error": "Fixture: initialize failed"}})
                continue
            send({"type": "control_response", "response": {"subtype": "success", "request_id": event["request_id"],
                                                           "response": {"models": models}}})
        elif event.get("type") == "control_request":
            send({"type": "control_response", "response": {"subtype": "success", "request_id": event.get("request_id"),
                                                           "response": {}}})
        elif event.get("type") == "user" and rounds:
            play_round(Path(rounds), event)
        elif event.get("type") == "user":
            text = TURN_RESULTS.get(os.environ.get("ANGELO_FAKE_CLI_TURN", ""), "Fixture runtime: no inference available")
            send({"type": "result", "subtype": "success", "is_error": True, "result": text, "session_id": "fixture",
                  "usage": {"input_tokens": 0, "output_tokens": 0}})
    return 0


def claude(arguments):
    if arguments == ["--version"]:
        print(CLAUDE_VERSION + " (Claude Code)")
        return 0
    if arguments[:2] == ["auth", "status"]:
        if signed_in("claude"):
            print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}))
            return 0
        print(json.dumps({"loggedIn": False}))
        return 1
    if arguments[:2] == ["auth", "login"]:
        return login("claude")
    if "-p" in arguments:
        return claude_session()
    print("fake claude: unsupported arguments", file=sys.stderr)
    return 2


# What codex 0.162 ends a turn with, recorded against a local endpoint answering HTTP 429 (codex_retry).
CODEX_ERRORS = {
    "rate": {"message": "exceeded retry limit, last status: 429 Too Many Requests",
             "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}},
             "additionalDetails": None, "misalignment": None},
    "usage": {"message": "You\u2019ve hit your usage limit. Try again at 4:30 PM.", "codexErrorInfo": "usageLimitExceeded",
              "additionalDetails": None, "misalignment": None},
}


def _codex_state():
    return Path(os.environ.get("ANGELO_FAKE_CLI_STATE") or Path(os.environ["HOME"]) / ".fake-cli")


def next_codex_turn():
    """The next scripted turn outcome; the queue lives in the state folder, so it survives a restarted app-server."""
    path = _codex_state() / "codex-turns.json"
    queue = (json.loads(path.read_text()) if path.is_file() else
             [item.strip() for item in os.environ.get("ANGELO_FAKE_CLI_CODEX_TURNS", "").split(",") if item.strip()])
    outcome = queue.pop(0) if queue else "ok"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomically(path, json.dumps(queue))
    return outcome


def script_codex_turns(outcomes, state=None):
    """The outcomes of the next ``turn/start`` requests, replacing what is left of the queue (``next_codex_turn``).
    Called between turns: the stand-in reads the queue when a turn starts."""
    folder = Path(state) if state else _codex_state()
    folder.mkdir(parents=True, exist_ok=True)
    _write_atomically(folder / "codex-turns.json", json.dumps(list(outcomes)))


def codex_turn_hold(state=None):
    """The folder a ``held`` turn waits at, after its ``turn/started``, until the suite releases it (``release``)."""
    return (Path(state) if state else _codex_state()) / "codex-turn-hold"


def codex_turn(message, number, outcome):
    params = message.get("params") or {}
    thread, turn = params.get("threadId"), "fixture-turn-%d-%d" % (os.getpid(), number)
    with open(_codex_state() / "codex-requests.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"method": "turn/start", "time": time.time(), "turn": turn, "outcome": outcome,
                                 "input": params.get("input"), "plan": "outputSchema" in params}) + "\n")
    send({"id": message["id"], "result": {"turn": {"id": turn, "status": "inProgress", "error": None, "items": []}}})

    def note(method, **values):
        send({"method": method, "params": dict(threadId=thread, **values)})

    note("turn/started", turn={"id": turn, "status": "inProgress", "error": None})
    user = {"type": "userMessage", "id": "user-" + turn, "content": params.get("input") or []}
    note("item/started", turnId=turn, item=user)
    note("item/completed", turnId=turn, item=user)
    if outcome in CODEX_ERRORS:
        error = CODEX_ERRORS[outcome]
        note("thread/status/changed", status={"type": "systemError"})
        note("error", error=error, willRetry=False, turnId=turn)
        note("turn/completed", turn={"id": turn, "status": "failed", "error": error, "durationMs": 12})
        return
    if outcome == "held":
        hold_at = codex_turn_hold()
        wait_for_gate(hold_at, _next_hold(hold_at))
    text = '{"reply":"Done.","operations":[]}' if "outputSchema" in params else "Done."
    item = {"type": "agentMessage", "id": "msg-" + turn, "text": text}
    note("item/started", turnId=turn, item=dict(item, text=""))
    note("item/agentMessage/delta", turnId=turn, itemId=item["id"], delta=text)
    note("item/completed", turnId=turn, item=item)
    note("turn/completed", turn={"id": turn, "status": "completed", "error": None, "durationMs": 40})


_OPEN = {"turn": None, "steers": [], "answers": {}}   # The call turn on its thread; steers it has not read yet.
_OPEN_LOCK = threading.Lock()


def _log_codex(entry):
    with open(_codex_state() / "codex-requests.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry, time=time.time())) + "\n")


def codex_steer(message):
    params = message.get("params") or {}
    with _OPEN_LOCK:
        accepted = _OPEN["turn"] is not None and params.get("expectedTurnId") == _OPEN["turn"]
        if accepted:
            _OPEN["steers"].append(params)
    _log_codex({"method": "turn/steer", "accepted": accepted, "params": params})
    send({"id": message["id"], "result": {"turnId": params.get("expectedTurnId")}} if accepted else
         {"id": message["id"], "error": {"code": -32600, "message": "no active turn to steer"}})


def codex_call_turn(message, number, outcome):
    """A turn with one dynamic tool call, on its own thread (``call``/``call_approval``, the module's docstring)."""
    params = message.get("params") or {}
    thread, turn = params.get("threadId"), "fixture-turn-%d-%d" % (os.getpid(), number)
    plan = "outputSchema" in params
    with _OPEN_LOCK:
        _OPEN["turn"], _OPEN["steers"] = turn, []
    _log_codex({"method": "turn/start", "turn": turn, "outcome": outcome, "input": params.get("input"), "plan": plan})
    send({"id": message["id"], "result": {"turn": {"id": turn, "status": "inProgress", "error": None, "items": []}}})

    def note(method, **values):
        send({"method": method, "params": dict(threadId=thread, **values)})
    note("turn/started", turn={"id": turn, "status": "inProgress", "error": None})
    user = {"type": "userMessage", "id": "user-" + turn, "clientId": None, "content": params.get("input") or []}
    note("item/started", turnId=turn, item=user)
    note("item/completed", turnId=turn, item=user)
    gate = os.environ.get("ANGELO_FAKE_CLI_CODEX_GATE")
    if gate:
        wait_for_gate(gate, _next_hold(gate))   # The suite types its message while this turn works.
    script = {"description": "Name the scene's objects.", "code": "result = [o.name for o in bpy.data.objects]"}
    tool, arguments = (("angelo_execute_python", script) if outcome == "call_approval"
                       else ("angelo_inspect_scene", {}))
    answer, request = queue.Queue(), "fixture-call-%d" % number
    with _OPEN_LOCK:
        _OPEN["answers"][request] = answer
    send({"id": request, "method": "item/tool/call", "params": {
        "threadId": thread, "turnId": turn, "callId": "call-" + turn, "tool": tool, "arguments": arguments}})
    try:
        answered = answer.get(timeout=GATE_SECONDS)
    except queue.Empty:
        answered = None
    _log_codex({"method": "tool/answered", "turn": turn, "answered": answered is not None})
    with _OPEN_LOCK:   # The next model request reads the pending steers first, as Codex does.
        steers, _OPEN["steers"] = _OPEN["steers"], []
    texts = []
    for index, steer in enumerate(steers):
        item = {"type": "userMessage", "id": "steer-%s-%d" % (turn, index), "clientId": steer.get("clientUserMessageId"),
                "content": steer.get("input") or []}
        note("item/started", turnId=turn, item=item)
        note("item/completed", turnId=turn, item=item)
        texts += [part.get("text", "") for part in item["content"] if isinstance(part, dict)]
    reply = ("Read: " + " | ".join(texts)) if texts else "Done."
    text = json.dumps({"reply": reply, "operations": []}) if plan else reply
    item = {"type": "agentMessage", "id": "msg-" + turn, "text": text}
    note("item/started", turnId=turn, item=dict(item, text=""))
    note("item/agentMessage/delta", turnId=turn, itemId=item["id"], delta=text)
    note("item/completed", turnId=turn, item=item)
    with _OPEN_LOCK:
        _OPEN["turn"] = None
    note("turn/completed", turn={"id": turn, "status": "completed", "error": None, "durationMs": 40})


def codex_server():
    threads = turns = 0
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # Notifications such as "initialized".
        method, params = message.get("method"), message.get("params") or {}
        if method is None:   # Angelo's answer to a call turn's dynamic tool call.
            with _OPEN_LOCK:
                waiter = _OPEN["answers"].pop(message["id"], None)
            if waiter is not None:
                waiter.put(message)
            continue
        if method == "turn/steer":
            codex_steer(message)
            continue
        if method == "turn/start":
            turns += 1
            outcome = next_codex_turn()
            if outcome in ("call", "call_approval"):
                threading.Thread(target=codex_call_turn, args=(message, turns, outcome), daemon=True).start()
            else:
                codex_turn(message, turns, outcome)
            continue
        if method == "thread/start":
            threads += 1
        codex_models = model_override("codex", CODEX_MODELS)
        if method == "model/list":
            _hold_listing("codex_hold")   # A slow catalog: the chat's loading state.
        if method == "model/list" and codex_models == "fail":
            send({"id": message["id"], "error": {"code": -32603, "message": "Fixture: model/list failed"}})
            continue
        results = {"initialize": {"userAgent": "codex_cli_rs/%s (fake)" % CODEX_VERSION},
                   "account/read": {"account": {"type": "chatgpt", "planType": "plus"} if signed_in("codex") else None},
                   "config/read": {"config": {}},
                   "model/list": {"data": codex_models},
                   "thread/start": {"thread": {"id": "fixture-thread-%d-%d" % (os.getpid(), threads)},
                                    "model": params.get("model") or CODEX_MODELS[0]["model"]},
                   "thread/resume": {"thread": {"id": params.get("threadId")}},
                   "turn/interrupt": {}}
        if method in results:
            send({"id": message["id"], "result": results[method]})
        else:
            send({"id": message["id"], "error": {"code": -32601, "message": "Fixture runtime has no " + str(method)}})
    return 0


def codex(arguments):
    if "app-server" in arguments:
        return codex_server()
    if arguments[-2:] == ["login", "status"]:
        if signed_in("codex"):
            print("Logged in using ChatGPT")
            return 0
        print("Not logged in")
        return 1
    if arguments[-1:] == ["login"]:
        return login("codex")
    print("fake codex: unsupported arguments", file=sys.stderr)
    return 2


def install(directory, python=None):
    """Write ``claude`` and ``codex`` wrappers into ``directory``; return it for PATH."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for tool in ("claude", "codex"):
        wrapper = directory / tool
        wrapper.write_text('#!/bin/sh\nexec "%s" "%s" %s "$@"\n' % (python or sys.executable, Path(__file__).resolve(), tool))
        wrapper.chmod(0o755)
    return directory


def sign_in(home, tool):
    """Mark the default profile under ``home`` as signed in, as an existing everyday sign-in."""
    folder = Path(home) / (".claude" if tool == "claude" else ".codex")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MARKER).write_text("fixture\n")


if __name__ == "__main__":
    tool, arguments = sys.argv[1], sys.argv[2:]
    record(tool, arguments)
    sys.exit(claude(arguments) if tool == "claude" else codex(arguments))
