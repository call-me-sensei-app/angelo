# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker chat may say to the model runtime Angelo runs for it, and what comes back (``runtime_service``).

A worker chat's provider Python runs in the worker Blender; its model runtime child (``codex app-server`` or
``claude -p``) runs in Angelo with Angelo's credentials, and every line between them crosses a relay
(doc/angelo/parallel_agents.rst, "Worker chats host their own controller"). Each line the worker sends is checked
here before it reaches the child, against an allowlist of exactly what Angelo's providers send:

* **Codex** (JSON-RPC lines): ``initialize``, ``initialized``, ``account/read``, ``config/read``, ``model/list``,
  ``thread/start``/``resume``/``fork``, ``turn/start``/``interrupt``, each with only the parameters the providers
  use (a thread stays read-only, asks before acting, works in the chat's workspace and uses the connection's own
  model provider: no ``config`` override can move the key to another endpoint). A ``thread/resume``, ``thread/fork``
  or ``turn/start`` names a thread this child started, resumed or forked for its chat now (a pooled child given to
  another chat forgets its first chat's, ``reassigned``), or one Angelo saw the chat's children open (``recorded``,
  ``runtime_threads``: never what the worker wrote); any other id reaches the child as a fresh random one, so the
  child answers with its own error for a thread that does not exist and never opens the person's other Codex threads.
  A ``thread/start`` names no thread (the child chooses its id). A ``turn/start``'s ``input`` holds only ``text``
  items and ``localImage`` items whose path resolves inside the chat's reference folders (``image_roots``); Angelo
  reads each such image itself, never following a link, and sends the child its bytes as a ``data:`` URL framed as
  Codex frames a local image, so the prompt is the same and the child opens no path. A thread is a warm-up base of the
  connection only when the neutral warm-up was the first turn on a thread this child started, and no turn runs on a
  base after it.
* **Claude** (stream-json lines): ``user`` messages of text and base64 images, the ``interrupt`` / effort-only
  ``apply_flag_settings`` control requests, and an ``initialize`` that is exactly one Angelo's builders make
  (``claude_transport`` with the typed-plan schema of a set of Angelo's tools, or ``claude_direct``), whatever its
  system prompt.
* Only while the child serves a chat the worker hosts on the child's own connection and key (``serves``,
  ``runtime_bindings``): otherwise only an interrupt passes, which ends work and never starts it.
* Answers only to the child's own requests that ``runtime_requests`` relays, each once and only in the shape of its
  method; Angelo answers every other request of the child itself (approvals declined) and never relays it.

Anything else, any line that is not one JSON object, and any line holding an ``apiKey`` field is refused: a refused
request is answered to the worker as an error (JSON-RPC ``-32600``, or a control ``error``) and logged; it never
reaches the child. Lines from the child have every secret Angelo launched it with, and every key Angelo holds now
(``live_secrets``: a key replaced since the launch, another connection's), replaced by ``[redacted]``. The child's
answers to ``account/read`` give the account identity a cache-affinity digest is keyed by (``account``), never the
worker's word.

Standard library only and no ``bpy``.
"""

import hashlib
import json
import threading
import time
import uuid

from . import runtime_requests as requests
from .runtime_bindings import read_image
from .runtime_requests import DENIED
REDACTED = "[redacted]"
MIN_SECRET = 8   # Shorter values are not secrets Angelo set (and replacing them would garble the line).
LIVE_SECONDS = 1.0   # How long the keys Angelo holds are reused before they are read again.
CONFLICT = "conflict"   # ``Filter.account`` once the child named two accounts: no digest is keyed by either.

_THREAD = frozenset({"threadId", "cwd", "modelProvider", "sandbox", "approvalPolicy", "serviceTier",
                     "baseInstructions", "model", "dynamicTools", "cacheAffinityKey",
                     "experimentalPredictionMode", "ephemeral", "excludeTurns"})
_START = _THREAD - {"threadId"}   # A new thread's id is the child's to choose: none is passed through.
_TURN = frozenset({"threadId", "input", "summary", "model", "effort", "serviceTierForTurn", "outputSchema"})
CODEX_METHODS = {
    "initialize": None, "initialized": None, "account/read": None, "config/read": None, "model/list": None,
    "thread/start": _START, "thread/resume": _THREAD, "thread/fork": _THREAD,
    "turn/start": _TURN, "turn/interrupt": frozenset({"threadId", "turnId"}),
}
_FIXED = {"sandbox": "read-only", "approvalPolicy": "on-request"}
_THREADS = frozenset({"thread/start", "thread/resume", "thread/fork"})
_NAMED = frozenset({"thread/resume", "thread/fork", "turn/start"})   # Requests on a thread the chat must know.


def _key(name):
    return name.replace("_", "").replace("-", "").lower() if isinstance(name, str) else name


def holds_api_key(value):
    """Whether any object in ``value`` has an ``apiKey`` field, in any spelling (``api_key``, ``API-KEY``)."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if any(_key(name) == "apikey" for name in item):
                return True
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def account_identity(account):
    """The identity ``codex_runtime.Runtime.note_account`` digests from an ``account/read`` result's account."""
    identity = json.dumps([account.get("type"), account.get("email")]) if isinstance(account, dict) else ""
    return hashlib.sha256(identity.encode()).hexdigest()


class Verdict:
    """``line`` goes to the child (``passed``), or ``reply`` (a line, or None) goes back to the worker; ``child``
    (a line, or None) is Angelo's own answer to the child's request a refused worker answer was for."""

    def __init__(self, passed, line=None, reply=None, reason=None, child=None):
        self.passed, self.line, self.reply, self.reason, self.child = passed, line, reply, reason, child


class Outbound:
    """A child line's routing: ``line`` (redacted) to the worker, or ``answer`` to the child, Angelo's own."""

    def __init__(self, line=None, answer=None):
        self.line, self.answer = line, answer


class Filter:
    """One child's checks: ``inbound`` (worker -> child) and ``outbound`` (child -> worker)."""

    def __init__(self, kind, workspace, model_provider=None, secrets=(), recorded=None, live_secrets=None,
                 serves=None, image_roots=None, opened=None, based=None, bases=None):
        """``recorded()``: thread ids Angelo saw the chat's children open; ``live_secrets()``: the keys Angelo holds
        now; ``serves()``: whether a hosted chat of the child's binding is its chat now; ``image_roots()``: the
        folders a turn's local image may be in; ``opened(thread)`` / ``based(thread)``: a thread (a warm-up base)
        the child opened, to record; ``bases()``: the connection's warm-up bases, which no turn runs on."""
        if kind not in ("codex", "claude"):
            raise ValueError("Unknown runtime kind")
        self.kind, self.workspace, self.model_provider = kind, str(workspace), model_provider
        self.secrets = _secrets(secrets)
        self.recorded, self.live_secrets = recorded or (lambda: ()), live_secrets or (lambda: ())
        self.serves, self.image_roots = serves or (lambda: True), image_roots or (lambda: ())
        self.opened, self.based = opened or (lambda thread: None), based or (lambda thread: None)
        self.bases, self._lock = bases or (lambda: ()), threading.Lock()
        self._asked = {}             # The child's own relayed requests by id -> what an answer must fit, once each.
        self._sent = {}              # The worker's thread and account requests by id -> their method.
        self._threads = set()        # Threads this child started, resumed or forked.
        self._started = set()        # Of those, the ones it started (``thread/start``): a warm-up base starts so.
        self._turned = set()         # Threads a turn was sent on.
        self._based = set()          # Threads this child made bases of the connection.
        self._live, self._initialized_sent = ((), -LIVE_SECONDS), False
        self.account = None          # ``account_identity`` of the child's account/read answers, or ``CONFLICT``.
        self.initialized = None      # The ``initialize`` result Angelo got itself (an API-key sign-in), or None.

    # Worker -> child -------------------------------------------------------------------------------------------

    def inbound(self, line):
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            return Verdict(False, reason="not JSON")
        if not isinstance(message, dict):
            return Verdict(False, reason="not one JSON object")
        if holds_api_key(message):
            return self._deny(message, "an apiKey field")
        if not _ending(message) and not self._serving():
            return self._deny(message, "a chat this runtime does not serve")
        if self.kind == "codex" and "method" in message and "id" in message and json.dumps(message["id"]) in self._sent:
            return self._deny(message, "a request id already in flight")   # It would take that one's answer (audit 3).
        problem = self._codex(message) if self.kind == "codex" else self._claude(message)
        if isinstance(problem, Verdict):
            verdict = problem
        elif problem:
            return self._deny(message, problem)
        else:
            verdict = Verdict(True, line=line)
        if verdict.passed and self.kind == "codex" and message.get("method") in _THREADS | {"account/read"}:
            with self._lock:
                self._sent[json.dumps(message.get("id"))] = message["method"]
        return verdict

    def _codex(self, message):
        method = message.get("method")
        if method is None:
            identity = message.get("id")
            if ("result" in message) == ("error" in message):
                return "an answer to no request of the runtime"
            asked = self._answered(identity)
            if asked is None:
                return "an answer to no request of the runtime"
            if not requests.codex_fits(asked, message):
                return Verdict(False, reason="an answer that does not fit %s" % asked,
                               child=requests.codex_refusal(asked, identity))
            return None
        if not isinstance(method, str) or method not in CODEX_METHODS:
            return "the method %r" % (method,)
        if self.initialized is not None and method in ("initialize", "initialized"):
            return self._local_initialize(message)
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return "parameters that are not an object"
        allowed = CODEX_METHODS[method]
        if allowed is not None and not set(params) <= allowed:
            return "the parameters %s" % sorted(set(params) - allowed)
        for name, value in _FIXED.items():
            if name in params and params[name] != value:
                return "%s %r" % (name, params[name])
        if "cwd" in params and params["cwd"] != self.workspace:
            return "another working folder"
        if "modelProvider" in params and params["modelProvider"] != self.model_provider:
            return "another model provider"
        problem = self._turn(params) if method == "turn/start" else None   # Its input, whatever thread it names.
        if problem and not isinstance(problem, dict):
            return problem
        sent = problem or None   # A turn with its local images read here, or None.
        if method in _NAMED and not self._known(params.get("threadId")):
            # Unknown to its chat (audit #2): a thread that cannot exist, so the child answers with its own error and
            # never opens another of the person's threads, nor runs a turn on one another chat left loaded in it.
            sent = dict(problem or params, threadId=str(uuid.uuid4()))
        return None if sent is None else Verdict(True, line=json.dumps(dict(message, params=sent), ensure_ascii=False))

    def reassigned(self):
        """Another chat claimed it (``runtime_service._serves``): its threads and pending requests were the last's."""
        with self._lock:
            for known in (self._threads, self._started, self._turned, self._asked, self._sent):
                known.clear()

    def _serving(self):
        try:
            return self.serves() is True
        except Exception:  # noqa: BLE001 - unknown: it serves no chat.
            return False

    def _turn(self, params):
        """Why a ``turn/start``'s ``input`` is refused, or the ``Verdict`` that sends it with each local image read here
        (``_images``). A warm-up turn that is the first turn on a thread this child started marks that thread a base
        of the connection; no turn runs on a base after its warm-up (its forks share it as their opening)."""
        items = params.get("input", [])
        if not isinstance(items, list):
            return "an input that is not a list"
        for item in items:
            if not isinstance(item, dict):
                return "an input item that is not an object"
            if item.get("type") == "text" and set(item) == {"type", "text"} and isinstance(item["text"], str):
                continue
            if item.get("type") != "localImage" or set(item) != {"type", "path"} or not isinstance(item["path"], str):
                return "the input item %r" % (item.get("type"),)
        from .codex_bases import WARM_UP
        thread = params.get("threadId")
        if isinstance(thread, str):
            try:
                bases = set(self.bases())
            except Exception:  # noqa: BLE001 - the record could not be read: every thread may be a base.
                return "a turn on a thread whose warm-up record could not be read"
            with self._lock:
                if thread in self._based or thread in bases:
                    return "a turn on a warm-up base"
                first, started = thread not in self._turned, thread in self._started
                self._turned.add(thread)
                if first and started and items == [{"type": "text", "text": WARM_UP}]:
                    self._based.add(thread)
                else:
                    started = False
            if started:
                self.based(thread)
        return self._images(params, items)

    def _images(self, params, items):
        """The turn with each ``localImage`` as Codex itself would put it (``<image name=[Image #n] path=...>``, the
        image, ``</image>``), its bytes read here (``runtime_bindings.read_image``) as a ``data:`` URL, so the child
        never opens a path the worker named; None when it has none, or why one is refused."""
        if not any(item["type"] == "localImage" for item in items):
            return None
        try:
            roots = tuple(self.image_roots())
        except Exception:  # noqa: BLE001 - no folder known: no local image passes.
            roots = ()
        rewritten, number = [], 0
        for item in items:
            if item["type"] != "localImage":
                rewritten.append(item)
                continue
            url = read_image(item["path"], roots)
            if url is None:
                return "a local image outside the chat's references"
            number += 1
            rewritten += [{"type": "text", "text": '<image name=[Image #%d] path="%s">' % (number, item["path"])},
                          {"type": "image", "url": url}, {"type": "text", "text": "</image>"}]
        return dict(params, input=rewritten)

    def _known(self, thread):
        if not isinstance(thread, str) or not thread:
            return False
        with self._lock:
            if thread in self._threads:
                return True
        try:
            return thread in set(self.recorded())
        except Exception:  # noqa: BLE001 - the chat's record could not be read: the thread is unknown.
            return False

    def _local_initialize(self, message):
        """Angelo initialized this child itself (it signs in with the key first): the worker's turn is answered here."""
        if message.get("method") == "initialized":
            return Verdict(False, reason="initialized here")
        if "id" not in message:
            return "initialize without an id"
        reply = json.dumps({"id": message["id"], "result": self.initialized}, ensure_ascii=False)
        return Verdict(False, reply=reply, reason="initialized here")

    def _claude(self, message):
        kind = message.get("type")
        if kind == "user":
            return None if _claude_user(message) else "a user message Angelo's providers do not send"
        if kind == "control_response":
            response = message.get("response")
            identity = response.get("request_id") if isinstance(response, dict) else None
            expected = self._answered(identity)
            if expected is None:
                return "an answer to no request of the runtime"
            if not requests.claude_fits(expected, message):
                return Verdict(False, reason="an answer that does not fit %s" % expected.get("subtype"),
                               child=requests.claude_refusal(expected, identity))
            return None
        if kind != "control_request":
            return "the message type %r" % (kind,)
        request = message.get("request")
        if not isinstance(request, dict):
            return "a control request without a request"
        subtype = request.get("subtype")
        if subtype == "interrupt" and set(request) <= {"subtype"}:
            return None
        if subtype == "apply_flag_settings" and set(request) <= {"subtype", "settings"} and isinstance(
                request.get("settings"), dict) and set(request["settings"]) <= {"effortLevel"}:
            return None
        if subtype == "initialize" and built_initialize(request):
            return None
        return "the control request %r" % (subtype,)

    def _answered(self, identity):
        """What the child's request ``identity`` asked (it is then answered), or None: none, or answered already."""
        if identity is None:
            return None
        with self._lock:
            return self._asked.pop(json.dumps(identity), None)

    def _deny(self, message, reason):
        reply = None
        if self.kind == "codex" and "method" in message and "id" in message:
            reply = {"id": message["id"], "error": {"code": -32600, "message": DENIED}}
        elif self.kind == "claude" and message.get("type") == "control_request" and "request_id" in message:
            reply = {"type": "control_response", "response": {"subtype": "error", "request_id": message["request_id"],
                                                              "error": DENIED}}
        return Verdict(False, reply=json.dumps(reply, ensure_ascii=False) if reply else None, reason=reason)

    # Child -> worker -------------------------------------------------------------------------------------------

    def outbound(self, line):
        """A child line's ``Outbound``: the child's requests Angelo answers itself go back to it (``answer``); a
        relayed one is remembered (answerable once, in its method's shape); the rest go to the worker redacted."""
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            message = None
        if not isinstance(message, dict):
            return Outbound(self.redact(line))
        if self.kind == "codex" and "method" in message and "id" in message:
            answer = requests.codex_answer(message)
            if answer is not None:
                return Outbound(answer=answer)
            with self._lock:
                self._asked[json.dumps(message["id"])] = message["method"]
        elif self.kind == "claude" and message.get("type") == "control_request" and "request_id" in message:
            answer = requests.claude_answer(message)
            if answer is not None:
                return Outbound(answer=answer)
            with self._lock:
                self._asked[json.dumps(message["request_id"])] = requests.claude_expected(message)
        elif self.kind == "codex" and "id" in message and "method" not in message:
            self._observe(message)
        elif self.kind == "claude" and message.get("type") in ("system", "result"):
            session = message.get("session_id")
            if isinstance(session, str) and session:   # The session Claude itself named: resumable later.
                with self._lock:
                    fresh = session not in self._threads
                    self._threads.add(session)
                if fresh:
                    self.opened(session)
        return Outbound(self.redact(line))

    def _observe(self, message):
        """The child's answer to a worker's thread or account request: the thread it opened, the account it named."""
        with self._lock:
            method = self._sent.pop(json.dumps(message.get("id")), None)
        result = message.get("result")
        if method is None or not isinstance(result, dict):
            return
        if method in _THREADS:
            thread = result.get("thread")
            if isinstance(thread, dict) and isinstance(thread.get("id"), str):
                with self._lock:
                    self._threads.add(thread["id"])
                    if method == "thread/start" and thread["id"] not in self._turned:
                        self._started.add(thread["id"])
                self.opened(thread["id"])
        elif method == "account/read" and isinstance(result.get("account"), dict):
            identity = account_identity(result["account"])
            with self._lock:
                self.account = identity if self.account in (None, identity) else CONFLICT

    def _all_secrets(self):
        live, read = self._live
        if time.monotonic() - read >= LIVE_SECONDS:
            try:
                live = _secrets(self.live_secrets())
            except Exception:  # noqa: BLE001 - the launch's own secrets still apply.
                live = ()
            self._live = (live, time.monotonic())
        return sorted(set(self.secrets) | set(live), key=len, reverse=True)

    def redact(self, text):
        for secret in self._all_secrets():
            if secret in text:
                text = text.replace(secret, REDACTED)
            escaped = json.dumps(secret)[1:-1]
            if escaped != secret and escaped in text:
                text = text.replace(escaped, REDACTED)
        return text


def _ending(message):
    """An interrupt: it ends the child's work and starts none, so it passes whichever chat the child serves."""
    if message.get("method") == "turn/interrupt":
        return True
    request = message.get("request")
    return (message.get("type") == "control_request" and isinstance(request, dict)
            and request.get("subtype") == "interrupt")


def _claude_user(message):
    """A ``user`` line as ``ClaudeProvider`` writes it: text, or text and base64 images (``references``)."""
    body = message.get("message")
    if not set(message) <= {"type", "session_id", "parent_tool_use_id", "message"} or not isinstance(body, dict):
        return False
    content = body.get("content")
    if set(body) != {"role", "content"} or body["role"] != "user":
        return False
    if isinstance(content, str):
        return True
    return isinstance(content, list) and all(isinstance(block, dict) and (
        (block.get("type") == "text" and set(block) == {"type", "text"} and isinstance(block["text"], str))
        or (block.get("type") == "image" and set(block) == {"type", "source"} and isinstance(block["source"], dict)
            and block["source"].get("type") == "base64" and set(block["source"]) == {"type", "media_type", "data"}
            and all(isinstance(value, str) for value in block["source"].values()))) for block in content)


def _secrets(values):
    return sorted({value for value in values if isinstance(value, str) and len(value) >= MIN_SECRET},
                  key=len, reverse=True)


# Claude's initialize: exactly one Angelo builds ---------------------------------------------------------------------

_SCHEMAS = {}   # A plan schema's tool names -> the schema Angelo builds for them (the last few).


def built_initialize(request):
    """Whether ``request`` is exactly an ``initialize`` Angelo's builders make for its own system prompt: the typed
    plan's (``claude_transport.initialization_request`` with ``tool_specs.native_plan_schema`` of the tools the
    schema names) or the direct tools' (``claude_direct.initialization_request``)."""
    from . import claude_direct, claude_transport
    prompt = request.get("systemPrompt")
    if not (isinstance(prompt, list) and len(prompt) == 1 and isinstance(prompt[0], str)):
        return False
    if "jsonSchema" not in request:
        return claude_direct.initialization_request(prompt[0])["request"] == request
    names = _schema_tools(request["jsonSchema"])
    if names is None or not names <= _tool_names():   # Only Angelo's own tools: an unknown name is refused here.
        return False
    schema = _SCHEMAS.get(names)
    if schema is None:
        from .tool_specs import native_plan_schema
        schema = native_plan_schema(names)
        if len(_SCHEMAS) >= 8:
            _SCHEMAS.clear()
        _SCHEMAS[names] = schema
    return claude_transport.initialization_request(schema, prompt[0])["request"] == request


def _tool_names():
    from .tool_specs import TOOLS
    return frozenset(tool["name"] for tool in TOOLS)


def _schema_tools(schema):
    """The tool names a typed-plan schema offers (its operations' ``tool`` const or enum), or None (also for more
    variants or names than Angelo has tools: the set is bounded before anything is built from it)."""
    try:
        operations = schema["properties"]["operations"]
        if operations.get("maxItems") == 0:
            return frozenset()
        items = operations["items"]
        names, variants, bound = set(), [items] + list(items.get("oneOf", ())), len(_tool_names()) + 2
        if len(variants) > bound:
            return None
        for variant in variants:
            tool = (variant.get("properties") or {}).get("tool") or {}
            names.update(tool.get("enum") or ())
            if "const" in tool:
                names.add(tool["const"])
            if len(names) > bound:
                return None
    except (KeyError, TypeError, AttributeError):
        return None
    return frozenset(names) if all(isinstance(name, str) for name in names) else None
