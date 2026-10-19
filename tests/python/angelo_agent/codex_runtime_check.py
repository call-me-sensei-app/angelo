# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""New Codex chats through Angelo's own chat path: a shared app-server with warm-base forks against the old path.

Every chat is a new ``Controller`` on one ChatGPT subscription connection, as
the add-on makes one per chat, sending one message with ``Controller.send``;
its ``CodexProvider`` connects as in the product. After a first chat (``A0``)
starts the connection's shared app-server and its first completed turn warms
the base (``codex_runtime``), the arms alternate ``BAABBAABBA``:

* A (this change): the chat leases the shared app-server and forks the base.
* B (before it): sharing off (``codex_runtime.eligible`` False): an app-server
  and ``thread/start`` of its own, as every chat had before.

Per chat ``result.json`` records the connect time (``runtime_start_ms``), the
first text and total times, the receipt's usage (input, cached input, output,
model requests), its native thread and whether it was a fork; and the base's
warm-up usage, which is the extra subscription usage. Unbilled runs also
record the ``prompt_cache_key`` and ``session-id`` of every request.

Unbilled by default: the installed ``codex app-server`` against
``codex_prompt_probe``'s fake endpoint (a fresh profile, a dummy key, the
probe's own account and model checks). It adds what needs no model:

* ``tools``: a forked chat whose turn calls a host tool (the call reaches that
  chat and completes);
* ``concurrent``: two chats on the shared app-server at once, one of them
  answered ``SLOW_SECONDS`` late: the other's turn time;
* ``restart``: the app-server closes as at an app restart, the forked chat is
  reopened from disk and sends again: no error, a new thread whose first
  request carries the chat's earlier message (the handoff); the remembered
  base is resumed with no model request and the next new chat forks it;
* ``memory``: resident memory of the five B app-servers against the shared one.

``--authorize-billed-run`` sends the A/B arms only (twelve model requests: A0,
the warm-up and five chats per arm; at most two retries) to OpenAI through one
connection's dedicated Codex profile, with the product's own checks. A usage
limit or a sign-in problem stops the run. Usage::

    python3.13 codex_runtime_check.py OUT_DIR [--model gpt-6.1-sol]
        [--authorize-billed-run --connection-id ID --codex-home .../codex-profile/ID]
"""

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import codex_bases, codex_connect, codex_runtime, providers  # noqa: E402
from angelo_agent.chat_settings import clean_options  # noqa: E402
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from codex_cache_fork_check import MAX_RETRIES, STOP_WORDS, Stop, dedicated_profile  # noqa: E402
from codex_prompt_probe import FakeResponses, fake_command  # noqa: E402

ORDER = "BAABBAABBA"
DOC = ("codex-runtime-check", 1)
OBSERVATION = {"scene": "Scene", "object_count": 0, "objects": [], "truncated": False}
FIRST = 'Connection check %s. Make no change: answer with reply "OK" and no operations.'
LOOKUP = "[lookup] What is in the scene? Make no change."
EXEC_LOOKUP = 'const a = await tools.angelo_scene_search({query: "Cube"}); text(a);'
PLAN_OK = '{"reply":"OK","operations":[]}'
EMPTY_PLAN = '{"reply":"","operations":[]}'  # What a model answers the warm-up (a base needs exactly this).
SLOW_SECONDS = 3
TURN_SECONDS = 240
PROBE_CONNECTION = "c0de5c0d-e5c0-4de5-8c0d-e5c0de5c0de5"


class RoutedResponses(FakeResponses):
    """The fake endpoint, answering by request: a lookup calls one host tool; "[slow]" answers late."""

    def __init__(self, out):
        super().__init__(out, [])
        self.lock = threading.Lock()

    def reply(self, raw, headers=None):
        items = json.loads(raw).get("input") or []
        users = [index for index, item in enumerate(items) if item.get("role") == "user"]
        text = _text(items[users[-1]]) if users else ""
        called = any(item.get("type") == "custom_tool_call" for item in items[users[-1] if users else 0:])
        step = (("exec", EXEC_LOOKUP) if "[lookup]" in text and not called
                else ("message", EMPTY_PLAN if codex_bases.WARM_UP in text else PLAN_OK))
        if "[slow]" in text:
            time.sleep(SLOW_SECONDS)
        with self.lock:
            self.script.insert(0, step)
            return super().reply(raw, headers)

    def requests(self, since=0):
        return [{"index": index, "prompt_cache_key": json.loads(raw).get("prompt_cache_key"),
                 "session_id": headers["values"].get("session-id"), "thread_id": headers["values"].get("thread-id"),
                 "text": " ".join(_text(item) for item in json.loads(raw).get("input") or []
                                  if item.get("role") == "user")}
                for index, (raw, headers) in enumerate(zip(self.bodies, self.headers)) if index >= since]


def _text(item):
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    return " ".join(part.get("text", "") for part in content if isinstance(part, dict))


def probe_checks(rpc, controller, mode, external, models=None):
    """``codex_connect.checks``' requests on the fake profile, which has no account to match."""
    for method in ("account/read", "config/read", "model/list"):
        rpc.request(method, {"refreshToken": False} if method == "account/read" else {})
    return []


class Executor:
    """Host tool results: every lookup finds an empty scene."""

    def __init__(self):
        self.calls, self.window = [], "window"

    def supports_native_job(self, name):
        return False

    def precondition(self, name, arguments):
        return "unchanged"

    def execute(self, name, arguments):
        self.calls.append(name)
        return {"objects": [], "success": True}


class Chats:
    """New chats on one connection, each a Controller of its own, as the add-on makes them."""

    def __init__(self, folder, settings, connection, model):
        self.folder, self.settings, self.connection, self.model = folder, settings, connection, model
        self.executor, self.open, self.forks = Executor(), [], []

    def new(self, session_id=None):
        chat = Controller(str(self.folder), session_id=session_id or str(uuid.uuid4()))
        chat.set_document(DOC)
        chat.select("codex_subscription", self.settings, "", self.connection, [])
        chat.state["options"] = clean_options({"model": self.model, "effort": "low", "approval": "AUTO_BOUNDED"})
        self.open.append(chat)
        return chat

    def send(self, chat, text):
        chat.send(text, dict(OBSERVATION))
        return time.monotonic()

    def finish(self, chats, started):
        """Pump the chats until each finishes; one record per chat."""
        done, deadline = {}, time.monotonic() + TURN_SECONDS
        while len(done) < len(chats) and time.monotonic() < deadline:
            for chat in chats:
                if id(chat) in done:
                    continue
                chat.pump_events()
                apply = lambda call, chat=chat: chat.execute_tool(call, self.executor, chat.document)  # noqa: E731
                chat.pump_tools(self.executor, chat.document, budget=ToolDispatchBudget(seconds=60),
                                apply_mutation=apply)
                if not chat.busy and not (chat._worker and chat._worker.is_alive()):
                    chat.pump_events()
                    done[id(chat)] = self.record(chat, started[chats.index(chat)])
            time.sleep(.005)
        return [done.get(id(chat)) or {"session": chat.state["id"], "status": "timeout"} for chat in chats]

    def turn(self, chat, text):
        return self.finish([chat], [self.send(chat, text)])[0]

    def record(self, chat, started):
        receipt = chat.telemetry.receipts[-1] if chat.telemetry.receipts else {}
        phases = receipt.get("phases") or {}
        return {"session": chat.state["id"], "native": chat.state.get("native_id"),
                "forked": chat.state.get("native_id") in self.forks, "status": receipt.get("status"),
                "wall_s": round(time.monotonic() - started, 3),
                "phases": {name: phases.get(name) for name in ("runtime_start_ms", "first_text_ms", "total_ms")},
                "usage": receipt.get("usage"), "counts": {name: (receipt.get("counts") or {}).get(name)
                                                          for name in ("tool_completions", "provider_turn_requests")},
                "errors": [str(m.get("text"))[:300] for m in chat.state["messages"] if m.get("role") == "error"]}

    def close(self):
        for chat in self.open:
            chat.close()
        self.open = []


def runtime_of(chats):
    return next(iter(codex_runtime._POOL.values()), None)


def wait_ready(seconds=180):
    """The shared app-server's base: wait until its warm-up has finished, then its record."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        runtime = runtime_of(None)
        bases = list(runtime.bases.values()) if runtime else []
        if bases and all(base["state"] != "warming" for base in bases):
            base = bases[0]
            return {key: base.get(key) for key in ("state", "thread", "total", "usage", "attempts")}
        time.sleep(.05)
    return None


def rss_kb(pids):
    values = []
    for pid in pids:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        values.append(int(out) if out.isdigit() else None)
    return values


def checked(record, retries):
    """Raise Stop for a usage limit or a sign-in failure; True when a failed turn may be retried."""
    if record.get("status") == "completed" and not record.get("errors"):
        return False
    text = json.dumps(record.get("errors") or record.get("status")).lower()
    if any(word in text for word in STOP_WORDS):
        raise Stop("Stopped on a usage-limit or sign-in error: %s" % text[:300])
    if retries[0] >= MAX_RETRIES:
        raise Stop("Stopped: a chat failed and no retries are left: %s" % text[:300])
    retries[0] += 1
    return True


def ab(chats, result, retries, b_pids):
    """A0, the warm-up, then the alternating arms."""
    def run(arm, number):
        while True:
            chat = chats.new()
            context = patch.object(codex_runtime, "eligible", return_value=False) if arm == "B" else ExitStack()
            with context:
                record = dict(chats.turn(chat, FIRST % number), arm=arm)
            result["records"].append(record)
            if arm == "B" and chat.provider is not None and getattr(chat.provider.rpc, "process", None):
                b_pids.append(chat.provider.rpc.process.pid)
            if not checked(record, retries):
                return chat
    first = run("A0", 0)
    result["warm_up"] = wait_ready()
    for number, arm in enumerate(ORDER, 1):
        run(arm, number)
    return first


def extras(chats, fake, result):
    """Unbilled only: a host tool in a fork, two chats at once, a restart, memory."""
    since = len(fake.bodies)
    chat = chats.new()
    record = chats.turn(chat, LOOKUP)
    result["tools"] = dict(record, executed=list(chats.executor.calls),
                           requests=[{k: r[k] for k in ("prompt_cache_key", "session_id", "thread_id")}
                                     for r in fake.requests(since)])
    slow, quick = chats.new(), chats.new()
    started = [chats.send(slow, "[slow] A long answer."), None]
    started[1] = chats.send(quick, "A quick answer while another chat waits.")
    result["concurrent"] = dict(zip(("slow", "quick"), chats.finish([slow, quick], started)))
    forked = next(record for record in result["records"] if record["arm"] == "A" and record["forked"])
    old = next(chat for chat in chats.open if chat.state["id"] == forked["session"])
    old.close()
    chats.open.remove(old)
    base = (result.get("warm_up") or {}).get("thread")
    codex_runtime.close_all()  # An app restart: the shared app-server and its forks are gone.
    since = len(fake.bodies)
    reopened = chats.new(forked["session"])
    record = chats.turn(reopened, "After a restart: say OK again.")
    first = next((r for r in fake.requests(since) if r["thread_id"] == record["native"]), {})
    remembered = wait_ready()
    fresh = chats.turn(chats.new(), FIRST % "after the restart")
    fresh_request = next((r for r in fake.requests(since) if r["thread_id"] == fresh["native"]), {})
    result["restart"] = dict(record, previous_native=forked["native"],
                             first_request_has_earlier_message=FIRST.split(" %s")[0] in first.get("text", ""),
                             first_request_session_id=first.get("session_id"),
                             # The remembered base, resumed with no model request, serves the next new chat.
                             base_resumed=bool(remembered) and remembered.get("thread") == base,
                             base_requests=sum(r["thread_id"] == base for r in fake.requests(since)),
                             new_chat=dict(fresh, prompt_cache_key=fresh_request.get("prompt_cache_key"),
                                           key_is_base=fresh_request.get("prompt_cache_key") == base))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--authorize-billed-run", action="store_true")
    parser.add_argument("--connection-id", default="")
    parser.add_argument("--codex-home", default="")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("OUT_DIR must be new or empty")
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    executable = providers.find_executable("codex", CODEX_BUNDLE if Path(CODEX_BUNDLE).exists() else "")
    settings = {"codex_path": executable, "model": args.model, "reasoning": "low", "auth_directory": str(out / "state")}
    patches, fake = [], None
    if args.authorize_billed_run:
        if not args.connection_id or not args.codex_home:
            parser.error("a billed run needs --connection-id and --codex-home")
        profile = dedicated_profile(args.codex_home, args.connection_id)
        settings.update(reuse_codex_login=True, connection_id=args.connection_id, native_profile=profile)
        connection = args.connection_id
    else:
        fake = RoutedResponses(out)
        command = fake_command(executable, out, fake.port)
        original = providers.CodexProvider._thread_parameters

        def parameters(self, models, *, voice=False):
            values, selected = original(self, models, voice=voice)
            return dict(values, modelProvider="angelo_probe"), selected
        patches = [patch.object(providers, "codex_command", return_value=command),
                   patch.object(codex_connect, "checks", probe_checks),
                   patch.object(providers.CodexProvider, "_thread_parameters", parameters)]
        settings.update(connection_id=PROBE_CONNECTION)
        connection = PROBE_CONNECTION
    fork = codex_runtime.Runtime.fork
    chats = Chats(out / "angelo", settings, connection, args.model)

    def recorded_fork(runtime, variant):
        made = fork(runtime, variant)
        if made:
            chats.forks.append(made[0]["thread"]["id"])
        return made
    result = {"billed": args.authorize_billed_run, "model": args.model, "connection_id": args.connection_id or None,
              "order": ORDER, "started_unix": time.time(), "records": [], "stopped": None}
    retries, b_pids = [0], []
    with ExitStack() as stack:
        for value in patches + [patch.object(codex_runtime.Runtime, "fork", recorded_fork)]:
            stack.enter_context(value)
        try:
            ab(chats, result, retries, b_pids)
            runtime = runtime_of(chats)
            result["memory_kb"] = {"B_processes": rss_kb(b_pids),
                                   "shared_process": rss_kb([runtime.rpc.process.pid]) if runtime else None}
            if fake is not None:
                extras(chats, fake, result)
        except Stop as stop:
            result["stopped"] = str(stop)
        except Exception as error:  # Recorded with whatever ran; the person decides on a rerun.
            result["stopped"] = "%s: %s" % (type(error).__name__, str(error)[:300])
        finally:
            chats.close()
            codex_runtime.close_all()
    if fake is not None:
        result["requests"] = [{k: r[k] for k in ("index", "prompt_cache_key", "session_id", "thread_id")}
                              for r in fake.requests()]
    result["summary"] = summarize(result)
    result["retries"] = retries[0]
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({"stopped": result["stopped"], "summary": result["summary"]}, indent=1))
    return 1 if result["stopped"] else 0


def summarize(result):
    def median(values):
        values = [value for value in values if isinstance(value, (int, float))]
        return round(statistics.median(values), 1) if values else None

    arms = {}
    for arm in ("A0", "A", "B"):
        rows = [row for row in result["records"] if row["arm"] == arm and row.get("status") == "completed"]
        usage = [row.get("usage") or {} for row in rows]
        arms[arm] = {"chats": len(rows), "forked": sum(row["forked"] for row in rows),
                     "connect_ms_median": median([row["phases"]["runtime_start_ms"] for row in rows]),
                     "first_text_ms_median": median([row["phases"]["first_text_ms"] for row in rows]),
                     "total_ms_median": median([row["phases"]["total_ms"] for row in rows]),
                     "input_tokens": [u.get("input_tokens") for u in usage],
                     "cached_input_tokens": [u.get("cached_input_tokens") for u in usage],
                     "errors": sum(bool(row["errors"]) for row in rows)}
    memory = result.get("memory_kb") or {}
    return {"arms": arms, "warm_up": result.get("warm_up"),
            "memory_mb": {"B_five_processes": round(sum(v for v in memory.get("B_processes") or [] if v) / 1024, 1),
                          "shared_process": round(sum(v for v in memory.get("shared_process") or [] if v) / 1024, 1)}}


if __name__ == "__main__":
    sys.exit(main())
