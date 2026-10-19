# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How prediction-mode forks of a warm base thread behave across app-server processes: unbilled.

The installed ``codex app-server`` runs against ``codex_prompt_probe``'s local
fake Responses endpoint (fresh ``CODEX_HOME`` and ``HOME``, dummy key). Every
process shares that one profile, as the chats of one Angelo connection do, and
each chat runs in its own process, as each Angelo chat does. Recorded in
``summary.json``:

* ``cross_process``: a base warmed in one process, resumed and forked
  (``experimentalPredictionMode``, ephemeral) in another: the fork's
  ``prompt_cache_key`` and ``session-id``, the local time of
  ``thread/resume`` + ``thread/fork`` against ``thread/start``, and the
  fork's reported token totals (whether they include the base's warm-up);
* ``concurrent``: two processes forking the same base at once;
* ``persisted_copy``: a plain, persisted fork of an ephemeral fork after one
  turn, resumed in a new process with its whole history;
* ``lost_fork``: what ``thread/resume`` answers for an ephemeral fork in a
  new process;
* ``persisted_prediction``: whether a prediction-mode fork may be persisted;
* ``same_process``: one long-lived process holding the base, as a shared
  per-connection runtime would: a chat forked there, ``thread/resume`` of that
  ephemeral fork in the same process after its turn (a chat switched away and
  back), a second fork while the first is mid-turn, a base warm-up turn running
  alongside a chat's turn, and N in-process ``thread/start`` against
  ``thread/fork`` times (``--timing N``, default 5);
* ``timing`` (``--timing N``): a new chat's thread, measured from ``initialize``
  through the connection reads Angelo makes (``account/read``, ``config/read``,
  ``model/list``) to a ready thread, in N fresh processes per path,
  alternating: ``thread/start``; ``thread/resume`` of the base then
  ``thread/fork``; and the resume started right after ``initialize``, alongside
  those reads.

Usage::

    python3.13 codex_fork_probe.py OUT_DIR [--codex PATH] [--timing N]
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.codex_plan import output_schema  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import codex_instructions, find_executable  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools  # noqa: E402
from codex_prompt_probe import FakeResponses, fake_command  # noqa: E402

ALLOWED = frozenset(TOOL_MAP) - TEAM_NAMES
EMPTY_PLAN = '{"reply":"","operations":[]}'
MODEL = "gpt-6.1-sol"
PREDICTION = {"experimentalPredictionMode": True, "ephemeral": True, "excludeTurns": True}


class App:
    """One app-server process on the shared fake profile."""

    def __init__(self, command, env, workspace):
        self.events, self.lock, self.workspace = [], threading.Lock(), workspace
        self.rpc = JsonRpcProcess(command, env=env, cwd=workspace, on_event=self._event, on_request=self._request)
        self.rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                        "capabilities": {"experimentalApi": True}})
        self.rpc.notify("initialized")

    def _event(self, method, params):
        if not method.startswith("account/"):
            with self.lock:
                self.events.append((method, params))

    def _request(self, identity, method, params):
        self.rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by the probe"}})

    def timed(self, method, params):
        started = time.perf_counter()
        try:
            return self.rpc.request(method, params), round((time.perf_counter() - started) * 1000, 1)
        except Exception as error:  # The probe reports what the runtime answered.
            return {"error": str(error)[:300]}, round((time.perf_counter() - started) * 1000, 1)

    def start(self):
        return self.timed("thread/start", {
            "cwd": self.workspace, "modelProvider": "angelo_probe", "sandbox": "read-only", "approvalPolicy": "on-request",
            "serviceTier": "default", "model": MODEL,
            "baseInstructions": codex_instructions(short_identifiers=True, deferred=True, plan=True, team=False),
            "dynamicTools": codex_tools(ALLOWED, short_identifiers=True, defer=True)})

    def turn(self, thread, text):
        with self.lock:
            seen = len(self.events)
        turn = self.rpc.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}],
                                               "effort": "low", "model": MODEL, "outputSchema": output_schema(ALLOWED)})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with self.lock:
                if any(method == "turn/completed" for method, _ in self.events[seen:]):
                    break
            time.sleep(.05)
        with self.lock:
            totals = [params["tokenUsage"] for method, params in self.events[seen:]
                      if method == "thread/tokenUsage/updated" and params.get("threadId") == thread]
        return turn["turn"]["id"], totals

    def close(self):
        self.rpc.process.stdin.close()  # End of input ends the app-server.
        try:
            self.rpc.process.wait(30)
        except Exception:
            self.rpc.process.terminate()  # The probe's own child only.


def same_process(app, count):
    """A shared runtime: the base warmed and kept loaded in the process its chats fork from."""
    process = app()
    try:
        base = thread_id(process.start()[0])
        process.turn(base, "Session start.")
        chat = thread_id(process.timed("thread/fork", dict(PREDICTION, threadId=base))[0])
        process.turn(chat, "First message.")
        resumed, resume_ms = process.timed("thread/resume", {"threadId": chat, "excludeTurns": True})
        result = {"resume_loaded_fork_error": resumed.get("error"), "resume_loaded_fork_ms": resume_ms}
        # A second chat forks while the first is mid-turn, and a fresh base warms alongside it.
        outcome = {}

        def busy():
            outcome["first"] = process.turn(chat, "Second message.")[0]
        worker = threading.Thread(target=busy)
        worker.start()
        second = thread_id(process.timed("thread/fork", dict(PREDICTION, threadId=base))[0])
        other_base = thread_id(process.start()[0])
        outcome["second"] = process.turn(second, "Other chat.")[0] if second else None
        outcome["warm_up"] = process.turn(other_base, "Session start.")[0] if other_base else None
        worker.join(60)
        result["concurrent_turns_completed"] = {name: bool(value) for name, value in outcome.items()}
        starts, forks = [], []
        for _ in range(count):
            starts.append(process.start()[1])
            forks.append(process.timed("thread/fork", dict(PREDICTION, threadId=base))[1])
        result["thread_start_ms"], result["thread_fork_ms"] = starts, forks
        result["median_ms"] = {"start": sorted(starts)[len(starts) // 2], "fork": sorted(forks)[len(forks) // 2]}
        return result
    finally:
        process.close()


def _connection_reads(process):
    for method, params in (("account/read", {"refreshToken": False}), ("config/read", {}), ("model/list", {})):
        process.timed(method, params)


def timing(app, base, count):
    """Milliseconds from after ``initialize`` to a ready chat thread, per path, in fresh processes."""
    rows = {"start": [], "resume_then_fork": [], "resume_alongside_reads": []}
    for _ in range(count):
        for path in rows:
            process = app()
            try:
                began = time.perf_counter()
                if path == "resume_alongside_reads":
                    resumed = {}
                    worker = threading.Thread(target=lambda: resumed.update(
                        result=process.timed("thread/resume", {"threadId": base, "excludeTurns": True})[0]))
                    worker.start()
                    _connection_reads(process)
                    worker.join(60)
                    made = process.timed("thread/fork", dict(PREDICTION, threadId=base))[0]
                else:
                    _connection_reads(process)
                    if path == "start":
                        made = process.start()[0]
                    else:
                        process.timed("thread/resume", {"threadId": base, "excludeTurns": True})
                        made = process.timed("thread/fork", dict(PREDICTION, threadId=base))[0]
                rows[path].append(round((time.perf_counter() - began) * 1000, 1) if thread_id(made) else None)
            finally:
                process.close()
    return {path: {"ms": values, "median": sorted(v for v in values if v is not None)[len(values) // 2]
                   if all(v is not None for v in values) else None} for path, values in rows.items()}


def thread_id(result):
    return (result.get("thread") or {}).get("id")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--codex", default="")
    parser.add_argument("--timing", type=int, default=0, help="fresh processes per path for the timing section")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    executable = args.codex or find_executable("codex", CODEX_BUNDLE if Path(CODEX_BUNDLE).exists() else "")
    fake = FakeResponses(out, [("message", EMPTY_PLAN)] * 64)
    command, env = fake_command(executable, out, fake.port)
    workspace = str(out / "workspace")
    summary = {}

    def request_keys(since):
        return [{"prompt_cache_key": json.loads(raw).get("prompt_cache_key"), **headers["values"]}
                for raw, headers in zip(fake.bodies[since:], fake.headers[since:])]

    def app():
        return App(command, env, workspace)

    # A base warmed in one process.
    first = app()
    base = thread_id(first.start()[0])
    first.turn(base, "Session start.")
    first.close()

    # Resumed and forked in another (a new chat in its own process), against a fresh thread/start.
    second = app()
    started, start_ms = second.start()
    resumed, resume_ms = second.timed("thread/resume", {"threadId": base, "excludeTurns": True})
    forked, fork_ms = second.timed("thread/fork", dict(PREDICTION, threadId=base))
    fork = thread_id(forked)
    since = len(fake.bodies)
    _turn, totals = second.turn(fork, "First message.")
    summary["cross_process"] = {
        "base": base, "fork": fork, "resume_error": resumed.get("error"), "fork_error": forked.get("error"),
        "thread_start_ms": start_ms, "thread_resume_ms": resume_ms, "thread_fork_ms": fork_ms,
        "requests": request_keys(since), "fork_token_usage": totals}

    # A persisted copy of the ephemeral fork after its turn, and what a new process can resume.
    copied, copy_ms = second.timed("thread/fork", {"threadId": fork, "excludeTurns": True})
    copy = thread_id(copied)
    second.close()
    third = app()
    lost, _ms = third.timed("thread/resume", {"threadId": fork, "excludeTurns": True})
    reloaded, reload_ms = third.timed("thread/resume", {"threadId": copy, "excludeTurns": True}) if copy else ({}, None)
    since = len(fake.bodies)
    if copy and not reloaded.get("error"):
        third.turn(copy, "Second message.")
    history = [len(json.loads(raw).get("input") or []) for raw in fake.bodies[since:]]
    summary["lost_fork"] = {"resume_error": lost.get("error")}
    summary["persisted_copy"] = {"copy": copy, "copy_error": copied.get("error"), "plain_fork_ms": copy_ms,
                                 "resume_error": reloaded.get("error"), "resume_ms": reload_ms,
                                 "requests": request_keys(since), "input_items": history}
    persisted, _ms = third.timed("thread/fork", {"threadId": base, "experimentalPredictionMode": True,
                                                 "ephemeral": False, "excludeTurns": True})
    summary["persisted_prediction"] = {"error": persisted.get("error"), "thread": thread_id(persisted)}
    third.close()

    # Two chats forking the same base at once, each in its own process.
    results = {}

    def concurrent(name):
        process = app()
        try:
            process.timed("thread/resume", {"threadId": base, "excludeTurns": True})
            made, ms = process.timed("thread/fork", dict(PREDICTION, threadId=base))
            identity = thread_id(made)
            if identity:
                process.turn(identity, "Concurrent message %s." % name)
            results[name] = {"fork": identity, "error": made.get("error"), "fork_ms": ms}
        finally:
            process.close()

    since = len(fake.bodies)
    workers = [threading.Thread(target=concurrent, args=(name,)) for name in ("a", "b")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(120)
    summary["concurrent"] = dict(results, requests=request_keys(since))
    summary["same_process"] = same_process(app, max(args.timing, 5))
    if args.timing:
        summary["timing"] = timing(app, base, args.timing)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
