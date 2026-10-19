# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Codex thread and request of a live-eval trial, for the returning-user variant (``--request-log``).

``install`` wraps ``protocol.JsonRpcProcess`` inside the app before the first
provider starts, as the Object Mode pilot's adapter does, and appends one JSON
line per thread call (``thread/start``, ``thread/fork``, ``thread/resume``,
``turn/start``) and per thread event (``thread/started``, ``turn/started``,
``turn/completed``, ``thread/tokenUsage/updated``) to ``requests.jsonl`` in the
trial's QA folder. A row holds methods, thread and turn IDs, the fork source
and its flags, whether a turn is a base warm-up (``codex_bases.WARM_UP``) and
each request's input, cached and output tokens: never a prompt, an argument, an
account or a credential. ``summary`` turns the file into per-thread first and
total requests, which is what the cold-start and returning-user columns read.
"""

import json
import threading
import time
from pathlib import Path

CALLS = frozenset({"thread/start", "thread/fork", "thread/resume", "turn/start"})
EVENTS = frozenset({"thread/started", "turn/started", "turn/completed", "thread/tokenUsage/updated"})


def _thread(result):
    thread = (result or {}).get("thread") if isinstance(result, dict) else None
    return thread.get("id") if isinstance(thread, dict) else None


def call_row(method, params, result, warm_up_text):
    params = params or {}
    row = {"t": time.monotonic_ns(), "kind": "call", "method": method,
           "thread": _thread(result) or params.get("threadId")}
    if method in ("thread/fork", "thread/resume"):
        row["source"] = params.get("threadId")
        row.update({key: params[key] for key in ("experimentalPredictionMode", "ephemeral", "excludeTurns")
                    if key in params})
    if method == "turn/start":
        texts = [item.get("text") for item in params.get("input") or [] if isinstance(item, dict)]
        row["warm_up"] = warm_up_text in texts
    return row


def event_row(method, params):
    params = params or {}
    row = {"t": time.monotonic_ns(), "kind": "event", "method": method,
           "thread": params.get("threadId") or _thread(params), "turn": params.get("turnId")}
    turn = params.get("turn")
    if isinstance(turn, dict):
        row.update(turn=turn.get("id"), status=turn.get("status"))
    if method == "thread/tokenUsage/updated":
        last = (params.get("tokenUsage") or {}).get("last") or {}
        row["request"] = {"input": last.get("inputTokens"), "cached": last.get("cachedInputTokens"),
                          "output": last.get("outputTokens")}
    return row


def install(path):
    """Log every Codex thread call and event of this app process to ``path`` (JSON lines)."""
    from angelo_agent import codex_bases, protocol
    path, lock = Path(path), threading.Lock()
    original_init, original_request = protocol.JsonRpcProcess.__init__, protocol.JsonRpcProcess.request

    def write(row):
        with lock, path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")

    def observed_init(instance, *args, **kwargs):
        previous = kwargs.get("on_event")

        def on_event(method, params):
            if method in EVENTS:
                write(event_row(method, params))
            if previous is not None:
                previous(method, params)
        kwargs["on_event"] = on_event
        return original_init(instance, *args, **kwargs)

    def observed_request(instance, method, params=None, *args, **kwargs):
        result = original_request(instance, method, params, *args, **kwargs)
        if method in CALLS:
            write(call_row(method, params, result, codex_bases.WARM_UP))
        return result
    protocol.JsonRpcProcess.__init__, protocol.JsonRpcProcess.request = observed_init, observed_request


def summary(path):
    """Per thread in order of appearance: how it began (start, fork of a base, resume), whether it is a warm-up,
    and its requests' tokens (the first request separately)."""
    threads, order = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines() if Path(path).is_file() else []:
        row = json.loads(line)
        thread = row.get("thread")
        if not thread:
            continue
        if thread not in threads:
            threads[thread] = {"thread": thread, "began": None, "source": None, "warm_up": False, "requests": []}
            order.append(thread)
        entry = threads[thread]
        if row["kind"] == "call" and row["method"] in ("thread/start", "thread/fork", "thread/resume"):
            entry["began"] = entry["began"] or row["method"].split("/")[1]
            entry["source"] = entry["source"] or row.get("source")
        elif row["kind"] == "call" and row["method"] == "turn/start" and row.get("warm_up"):
            entry["warm_up"] = True
        elif row.get("request"):
            entry["requests"].append(row["request"])
    for entry in threads.values():
        first = entry["requests"][0] if entry["requests"] else None
        entry["first_request"] = first
        entry["first_cached_share"] = (round(first["cached"] / first["input"], 3)
                                       if first and first.get("input") else None)
    return [threads[thread] for thread in order]
