# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in ``codex app-server`` that plays scripted tool calls: the model of a worker chat in the native suites.

``hosted_worker_chat`` (design A′) puts a ``codex`` wrapper running this file first on the lead's PATH. The lead's
``RuntimeService`` launches it for a worker chat exactly as it launches the real runtime (its own command, its own
environment, its own process group) and relays its stdio to the chat's provider Python in the worker Blender. No
network, account, inference or paid call.

It answers what Angelo asks a Codex app-server (``initialize``, ``account/read``, ``config/read``, ``model/list``,
``thread/start``, ``thread/resume``, ``thread/fork``, ``turn/start``, ``turn/interrupt``) in codex 0.162's shapes.
Each ``turn/start`` plays the next scripted turn of its chat. The chat is named in its message (``tag``: the
suite's text carries ``[chat:<id>]``; chats of one Angelo share a working folder, so that cannot tell them apart), and
its folder ``$ANGELO_HOSTED_STAND_IN/<id>`` holds ``queued-<seq>.json`` files, each a list of steps, written by the
suite before it sends the message; a turn plays the oldest one:

* ``{"call": {"id", "tool", "arguments"}, "stop_if_failed": bool}``: one ``item/tool/call`` request, as a model's
  tool call, the next step only once it is answered; the answer the host gives the model is logged (``tool_result``,
  with ``sent_ns``/``answered_ns`` on the monotonic clock); a failed one ends the turn when ``stop_if_failed``;
* ``{"hold": name}``: the turn waits (``held`` logged) until the suite writes ``release-<name>``, or the turn is
  interrupted; used to keep a turn busy while the suite stops it, kills its worker or revokes its consent;
* ``{"sleep": seconds}``; ``{"reply": text}`` (the turn's closing message, a typed plan with no operations when the
  turn asks for one).

A missing turn file closes the turn with a reply and no call. Every request and result goes to ``log.jsonl`` in the
chat's folder (``turn_started``, ``request``, ``tool_result``, ``turn_completed``), the suite's view of what the model
saw. ``turn/interrupt`` ends the active turn ``interrupted``; the process exits when its stdin ends.
"""

import importlib.util
import io
import json
import os
import platform
import re
import sys
import threading
import time
import zipfile
from pathlib import Path

ENV = "ANGELO_HOSTED_STAND_IN"
# The Codex version it reports: the app's pinned agent-runtime version when the suite gives it. A worker verifies the
# version against the runtime it resolves itself, not against what the lead launched (FE report, defect D1).
VERSION = os.environ.get(ENV + "_VERSION") or "0.162.0"
MODELS = [{"model": "gpt-6.1-sol", "displayName": "GPT-6.1 Sol", "isDefault": True, "defaultReasoningEffort": "low",
           "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}],
           "inputModalities": ["text", "image"]}]
CALL_SECONDS = 600.0   # A call the host never answers: the suite has failed long before.


TAG = re.compile(r"\[chat:([0-9A-Za-z-]{1,64})\]")


def tag(chat):
    """The marker a suite's message carries so this runtime plays that chat's queue."""
    return "[chat:%s]" % chat


class Server:
    def __init__(self, root):
        self.root = Path(root)
        self.write_lock, self.lock = threading.Lock(), threading.Lock()
        self.answers, self.waiting = {}, {}
        self.folder = None
        self.threads = self.turns = 0
        self.active = None   # {"thread", "turn", "interrupted": Event}
        self.signed_in_with_key = False
        self.started = set()

    def refuse_fork(self, message, params):
        """The pinned runtime's ``cacheAffinityKey`` check and its answer for an unknown thread, as Angelo probes them
        (``cache_affinity.check``): True when refused."""
        key = params.get("cacheAffinityKey")
        if key is not None and not (isinstance(key, str) and 1 <= len(key) <= 64
                                    and all(33 <= ord(c) <= 126 for c in key)):
            why = "cacheAffinityKey must be 1-64 visible ASCII characters"
        elif params.get("threadId") not in self.started:
            why = "no rollout found for thread id %s" % params.get("threadId")
        else:
            return False
        self.send({"id": message["id"], "error": {"code": -32600, "message": why}})
        return True

    def send(self, message):
        with self.write_lock:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def log(self, kind, **fields):
        folder = self.folder or self.root / "unbound"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "log.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(fields, kind=kind, pid=os.getpid(), t=time.time()), default=str) + "\n")

    def note(self, method, **params):
        self.send({"method": method, "params": params})

    # Requests from the host --------------------------------------------------------------------------------------

    def handle(self, message):
        if "method" not in message:   # An answer to one of our item/tool/call requests.
            with self.lock:
                event = self.waiting.pop(message.get("id"), None)
                self.answers[message.get("id")] = message
            if event is not None:
                event.set()
            return
        method, params = message["method"], message.get("params") or {}
        if "id" not in message:
            return   # "initialized" and other notifications.
        self.log("request", method=method)
        if method == "turn/start":
            self.turns += 1
            threading.Thread(target=self.turn, args=(message, self.turns), daemon=True).start()
            return
        if method == "turn/interrupt":
            active = self.active
            if active is not None and active["turn"] == params.get("turnId"):
                active["interrupted"].set()
            self.send({"id": message["id"], "result": {}})
            return
        if method == "account/login/start":
            self.signed_in_with_key = True
        if method == "thread/fork" and self.refuse_fork(message, params):
            return
        if method in ("thread/start", "thread/fork"):
            self.threads += 1
            self.started.add("stand-in-%d-%d" % (os.getpid(), self.threads))
        account = {"type": "apiKey"} if self.signed_in_with_key else {"type": "chatgpt", "planType": "plus"}
        results = {"initialize": {"userAgent": "codex_cli_rs/%s (hosted stand-in)" % VERSION},
                   "account/read": {"account": account}, "account/login/start": {"type": "apiKey"},
                   "config/read": {"config": {}}, "model/list": {"data": MODELS},
                   "thread/start": {"thread": {"id": "stand-in-%d-%d" % (os.getpid(), self.threads)},
                                    "model": params.get("model") or MODELS[0]["model"]},
                   "thread/fork": {"thread": {"id": "stand-in-%d-%d" % (os.getpid(), self.threads)},
                                   "model": params.get("model") or MODELS[0]["model"]},
                   "thread/resume": {"thread": {"id": params.get("threadId")}}}
        if method in results:
            self.send({"id": message["id"], "result": results[method]})
        else:
            self.send({"id": message["id"], "error": {"code": -32601, "message": "Stand-in runtime has no " + method}})

    # One scripted turn -------------------------------------------------------------------------------------------

    def script(self, number):
        """The oldest queued turn of this chat (``queued-<seq>.json``, renamed ``played-<seq>.json`` once taken), so a
        chat reopened in another worker or a restarted runtime continues the suite's queue."""
        folder = self.folder or self.root / "unbound"
        queued = sorted(folder.glob("queued-*.json"), key=lambda path: int(path.stem.split("-")[1]))
        for path in queued:
            played = path.with_name(path.name.replace("queued-", "played-"))
            try:
                os.replace(path, played)
                return json.loads(played.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        return [{"reply": "Nothing scripted for turn %d." % number}]

    def call(self, active, number, index, step):
        identity = "stand-in-call-%d-%d" % (number, index)
        event = threading.Event()
        with self.lock:
            self.waiting[identity] = event
        call_id = str(step.get("id") or identity)
        sent_ns = time.monotonic_ns()   # The machine's one monotonic clock: comparable with the host's (macOS, Linux).
        self.send({"id": identity, "method": "item/tool/call", "params": {
            "threadId": active["thread"], "turnId": active["turn"], "callId": call_id,
            "tool": step["tool"], "arguments": step.get("arguments") or {}}})
        deadline = time.monotonic() + CALL_SECONDS
        while not event.wait(0.05):
            if active["interrupted"].is_set() or time.monotonic() > deadline:
                self.log("tool_unanswered", id=call_id, tool=step["tool"], turn=number)
                return False
        answered_ns = time.monotonic_ns()
        answer = self.answers.pop(identity, {})
        result = answer.get("result") or {}
        texts = [item.get("text") for item in result.get("contentItems") or () if item.get("type") == "inputText"]
        self.log("tool_result", id=call_id, tool=step["tool"], turn=number, success=result.get("success"),
                 text=texts[0] if texts else None, error=answer.get("error"), sent_ns=sent_ns,
                 answered_ns=answered_ns)
        return result.get("success") is True

    def turn(self, message, number):
        params = message.get("params") or {}
        thread, turn = params.get("threadId"), "stand-in-turn-%d-%d" % (os.getpid(), number)
        active = self.active = {"thread": thread, "turn": turn, "interrupted": threading.Event()}
        texts = [item.get("text") for item in params.get("input") or () if item.get("type") == "text"]
        found = TAG.search(texts[0] or "") if texts else None
        if found:   # One runtime serves one chat (a worker hosts one); its log follows it from its first turn.
            self.folder = self.root / found.group(1)
            self.folder.mkdir(parents=True, exist_ok=True)
        self.log("turn_started", turn=number, text=texts[0] if texts else None, plan="outputSchema" in params)
        self.send({"id": message["id"], "result": {"turn": {"id": turn, "status": "inProgress", "error": None,
                                                            "items": []}}})
        self.note("turn/started", threadId=thread, turn={"id": turn, "status": "inProgress", "error": None})
        reply, status = "Done.", "completed"
        for index, step in enumerate(self.script(number)):
            if active["interrupted"].is_set():
                break
            if "call" in step:
                if not self.call(active, number, index, step["call"]) and step.get("stop_if_failed"):
                    break   # As a model that stops at an error: nothing after it is asked.
            elif "hold" in step:
                self.log("held", name=step["hold"], turn=number)
                release = (self.folder or self.root) / ("release-" + str(step["hold"]))
                while not release.exists() and not active["interrupted"].wait(0.02):
                    pass
            elif "sleep" in step:
                active["interrupted"].wait(float(step["sleep"]))
            elif "reply" in step:
                reply = str(step["reply"])
        if active["interrupted"].is_set():
            status = "interrupted"
        else:
            text = json.dumps({"reply": reply, "operations": []}) if "outputSchema" in params else reply
            item = {"type": "agentMessage", "id": "msg-" + turn, "text": text}
            self.note("item/started", threadId=thread, turnId=turn, item=dict(item, text=""))
            self.note("item/agentMessage/delta", threadId=thread, turnId=turn, itemId=item["id"], delta=text)
            self.note("item/completed", threadId=thread, turnId=turn, item=item)
        self.note("turn/completed", threadId=thread, turn={"id": turn, "status": status, "error": None,
                                                           "durationMs": 10})
        self.log("turn_completed", turn=number, status=status)
        self.active = None

    def serve(self):
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                self.handle(message)
        return 0


def main(arguments):
    if "app-server" in arguments:
        return Server(os.environ.get(ENV) or Path.home() / ".hosted-stand-in").serve()
    if arguments[-2:] == ["login", "status"]:
        print("Logged in using ChatGPT")
        return 0
    if arguments == ["--version"]:
        print("codex-cli " + VERSION)
        return 0
    print("hosted stand-in codex: unsupported arguments", file=sys.stderr)
    return 2


def install(directory, python=None, windows=None):
    """A ``codex`` in ``directory`` that runs this file with ``python``; returns the folder for PATH.

    The kind the lead's own launch path starts (``cli_runtime.find_executable``, ``runtime_command``): a ``#!/bin/sh``
    wrapper on macOS and Linux; on Windows a ``codex.exe``, because Angelo starts a non-shim batch file only when every
    argument is a plain token, and ``codex app-server``'s ``-c key="value"`` overrides are not (``cli_windows``). The
    exe is pip's console-script launcher (``launcher_exe``) followed by a shebang naming ``python`` and a zip whose
    ``__main__.py`` runs this file: the launcher starts ``python <exe> <arguments>`` in its own kill-on-close job, as
    every pip-installed command on Windows.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    python, script = str(python or sys.executable), str(Path(__file__).resolve())
    if not (os.name == "nt" if windows is None else windows):
        wrapper = directory / "codex"
        wrapper.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (python, script))
        wrapper.chmod(0o755)
        return directory
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("__main__.py", "import runpy\nrunpy.run_path(%r, run_name='__main__')\n" % script)
    shebang = '#!"%s"\n' % python if " " in python else "#!%s\n" % python
    (directory / "codex.exe").write_bytes(launcher_exe() + shebang.encode("utf-8") + archive.getvalue())
    return directory


def launcher_exe(machine=None):
    """The 64-bit console launcher pip ships for its Windows commands (``pip/_vendor/distlib/t64[-arm].exe``), read from
    this Python's own pip on every platform; a missing one fails the install loudly, never a skip."""
    machine = (machine or platform.machine()).lower()
    spec = importlib.util.find_spec("pip")
    if spec is None or not spec.origin:
        raise RuntimeError("The Windows stand-in codex needs pip's launcher, and this Python has no pip")
    name = "t64-arm.exe" if machine in ("arm64", "aarch64") else "t64.exe"
    path = Path(spec.origin).parent / "_vendor" / "distlib" / name
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RuntimeError("The Windows stand-in codex needs pip's launcher %s: %s" % (path, error)) from None
    if data[:2] != b"MZ":
        raise RuntimeError("%s is not a Windows executable" % path)
    return data


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
