# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripted stand-ins for the two model runtimes, over the real providers. Not a test module.

No process, account, network or inference. Each stand-in drives the provider
class the app uses, so everything between the model and the host is the app's
own code, and records exactly what the runtime would hand its model:

* ``ClaudeStandIn``: ``providers.ClaudeProvider`` over the stream-json fixture
  of ``claude_protocol_fixtures``. Each scripted round is a typed plan; every
  user message the provider writes to the runtime (the request, then the
  results of a ``verify`` plan) is kept in ``prompts``.
* ``CodexStandIn``: ``providers.CodexProvider`` and a real ``Controller`` over
  a scripted app-server. A turn calls tools as the model would from ``exec``;
  every tool answer the provider sends back is kept in ``answers``.

``Dispatcher`` is the host side: it runs each tool call with the function the
caller gives it, on the thread that pumps it. A compiled-app suite pumps on
Blender's main thread and passes the real ``BlenderExecutor``; a host test
passes canned results. Used by test_native_read_tools_delivery.py and
test_blender_read_tools.py.
"""

import json
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import claude_protocol_fixtures  # noqa: E402

TIMEOUT = 60.0


def plan(*operations, reply="", verify=None):
    """One Claude round: a typed plan as the runtime returns it."""
    document = {"operations": [{"tool": tool, "arguments": arguments} for tool, arguments in operations]}
    if reply:
        document["reply"] = reply
    if verify is not None:
        document["verify"] = verify
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "stand-in-claude",
            "structured_output": document}


def message_text(event):
    """The text of one stream-json user message."""
    content = event.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    return "".join(item.get("text", "") for item in content or () if isinstance(item, dict) and item.get("type") == "text")


class Dispatcher:
    """Runs tool calls with ``execute(name, arguments)`` on the thread that calls ``pump``."""

    def __init__(self, execute):
        self.execute, self.jobs, self.calls = execute, queue.Queue(), []

    def request_tool(self, _call_id, name, arguments, callback, *, delivery_owner=None):
        self.jobs.put((name, arguments, callback))

    def run_next(self, wait):
        try:
            name, arguments, callback = self.jobs.get(timeout=wait)
        except queue.Empty:
            return False
        try:
            result = self.execute(name, arguments)
        except Exception as error:  # As the controller answers a tool that raised: an error result.
            result = {"error": str(error)}
        self.calls.append((name, arguments, result))
        callback(result)
        return True

    def pump(self, worker):
        """Serve calls until ``worker`` (the provider's request thread) ends."""
        deadline = time.monotonic() + TIMEOUT
        while worker.is_alive() or not self.jobs.empty():
            self.run_next(0.02)
            if time.monotonic() > deadline:
                raise TimeoutError("The stand-in turn did not finish")


def _run(dispatcher, request):
    """Run ``request()`` on a worker, as a provider request runs, while this thread serves its tool calls."""
    outcome = {}

    def work():
        try:
            outcome["status"] = request()
        except Exception as error:
            outcome["error"] = error
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    dispatcher.pump(worker)
    worker.join(TIMEOUT)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["status"]


class ClaudeStandIn:
    """The real Claude provider over a scripted runtime: plans in, the prompts it was sent out."""

    def __init__(self, execute):
        from angelo_agent.providers import ClaudeProvider
        self.dispatcher = Dispatcher(execute)
        self.host = claude_protocol_fixtures.host()
        self.host.request_tool = self.dispatcher.request_tool
        self.provider = ClaudeProvider(self.host, {})
        self.provider.executable, self.provider.env = "claude-stand-in", {"FIXTURE": "only"}
        self.rounds, self.prompts = [], []
        self.process = claude_protocol_fixtures.NativeInitializeFixture(on_user=self._round)
        self._patches = [patch("angelo_agent.child_process.run", return_value=SimpleNamespace(
            returncode=0, stdout=claude_protocol_fixtures.SUPPORTED)),
            patch("angelo_agent.child_process.popen", return_value=self.process)]
        for item in self._patches:
            item.start()

    def _round(self, process, event):
        self.prompts.append(message_text(event))
        process.stdout.emit(self.rounds.pop(0) if self.rounds else plan(reply="No scripted round left"))

    def run(self, text, *rounds):
        """Status of one request whose model answers with ``rounds`` in order."""
        self.rounds, self.prompts = list(rounds), []
        self.dispatcher.calls.clear()
        return _run(self.dispatcher, lambda: self.provider.run_request(self.host, None, text, observation={}))

    def system_rows(self):
        return [data.get("text", "") for kind, data in self.host.events if kind == "message" and data.get("role") == "system"]

    def close(self):
        self.provider.close()
        for item in self._patches:
            item.stop()


class _ScriptedAppServer:
    """codex app-server's side: each ``turn/start`` plays one scripted turn, waiting for every tool answer."""

    def __init__(self, provider, turns):
        self.provider, self.turns = provider, list(turns)
        self.requests, self.answers = [], []
        self.answered = threading.Condition()

    def request(self, method, parameters, **_kwargs):
        self.requests.append((method, parameters))
        if method != "turn/start":
            return {}
        turn = "turn-%d" % len(self.requests)
        threading.Thread(target=self.play, args=(parameters["threadId"], turn, self.turns.pop(0)), daemon=True).start()
        return {"turn": {"id": turn}}

    def play(self, thread, turn, steps):
        deadline = time.monotonic() + TIMEOUT
        while self.provider.turn_id != turn and time.monotonic() < deadline:
            time.sleep(0.001)
        ids = {"threadId": thread, "turnId": turn}
        for index, step in enumerate(steps):
            if step[0] == "message":
                self.provider._event("item/completed", dict(ids, item={"type": "agentMessage", "id": "msg-%d" % index,
                                                                         "text": step[1], "phase": None}))
                continue
            expected = len(self.answers) + 1
            call = "%s-call-%d" % (turn, index)
            self.provider._request(call, "item/tool/call", dict(ids, callId=call, tool=step[1], arguments=step[2],
                                                                **({"namespace": step[3]} if len(step) > 3 else {})))
            with self.answered:  # The model reads a tool's answer before its next step.
                self.answered.wait_for(lambda: len(self.answers) >= expected, timeout=TIMEOUT)
        self.provider._event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": "completed"}})

    def respond(self, identity, payload):
        with self.answered:
            self.answers.append((identity, payload))
            self.answered.notify_all()

    def close(self):
        pass


class CodexStandIn:
    """The real Codex provider and Controller over a scripted app-server: tool calls in, their answers out."""

    def __init__(self, execute):
        from angelo_agent.controller import Controller
        from angelo_agent.providers import CodexProvider
        self.dispatcher = Dispatcher(execute)
        self.folder = tempfile.TemporaryDirectory()
        self.controller = Controller(self.folder.name)
        self.controller.set_document(("stand-in-document", 1))
        self.controller.state["native_id"] = "stand-in-thread"
        self.controller._begin_metrics()
        self.controller.busy, self.controller.cancelled = True, False
        self.context = self.controller.runtime_context()
        self.context.request_tool = self.dispatcher.request_tool
        self.provider = CodexProvider(self.context, {})
        self.provider.thread_id = "stand-in-thread"
        self.provider.bind_request(self.context)
        self.server = None

    def run(self, text, *turns):
        """Status of one request. A turn is a list of ``("tool", name, arguments[, namespace])`` and
        ``("message", text)`` steps; ``final`` builds the plan message that ends a turn."""
        self.dispatcher.calls.clear()
        self.server = self.provider.rpc = _ScriptedAppServer(self.provider, turns)
        status = _run(self.dispatcher, lambda: self.provider.run("stand-in-thread", text))
        self.controller.pump_events()
        return status

    def answers(self):
        """What the model received for each tool call, in order: ``(success, parsed JSON)``."""
        return [(payload["success"], json.loads(payload["contentItems"][0]["text"])) for _id, payload in self.server.answers]

    def answer_texts(self):
        return [payload["contentItems"][0]["text"] for _id, payload in self.server.answers]

    def close(self):
        self.provider.close()
        self.controller.close()
        self.folder.cleanup()


def final(reply=""):
    """The plan message that ends a Codex plan turn with no edit."""
    return ("message", json.dumps({"reply": reply, "operations": []}))
