# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first chat of a launch, with and without its opening readied when the connection is ready: unbilled.

Each trial is one launch (a new process pool) of one ChatGPT subscription
connection against ``codex_prompt_probe``'s fake endpoint (the installed
``codex app-server``, a fresh ``CODEX_HOME``, a dummy key). The chat is made
at launch, as the add-on makes the active chat; ``DELAY`` seconds later (a
person typing) it sends one message with ``Controller.send``. Arms:

* ``before``: nothing happens until the message (the path before this change):
  the message starts the app-server; with no base ready it starts a thread,
  and the base is resumed or warmed after it.
* ``after``: ``codex_opening.prepare`` at launch, as a loaded model
  catalog calls it: the app-server starts and the base is resumed or warmed.

Two cases: ``stored`` (an earlier launch remembered a base in this state
folder) and ``none`` (a new state folder: nothing remembered). Recorded per
trial: connect (``runtime_start_ms``), first text and request total of the
chat's receipt, whether it was a fork, whether its first request's
``prompt_cache_key`` was the base's, and every model request the fake endpoint
received until the launch settled (the chat's and the warm-ups'), with the
warm-up receipts the state folder holds. ``--warm-up-seconds`` makes the fake
answer a warm-up that late, as a model would (a message during the warm-up).

Usage::

    python3.13 codex_opening_check.py OUT_DIR [--trials 3] [--delay 3] [--warm-up-seconds 0]
"""

import argparse
import json
import statistics
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import codex_bases, codex_connect, codex_opening, codex_runtime, providers  # noqa: E402
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.telemetry import TelemetryStore  # noqa: E402
from codex_prompt_probe import fake_command  # noqa: E402
from codex_runtime_check import (FIRST, PROBE_CONNECTION, Chats, RoutedResponses, probe_checks,  # noqa: E402
                                 wait_ready)

MODEL = "gpt-6.1-sol"


class SlowWarmUps(RoutedResponses):
    """The fake endpoint, answering a warm-up ``seconds`` late (a model's time)."""

    def __init__(self, out, seconds):
        super().__init__(out)
        self.seconds = seconds

    def reply(self, raw, headers=None):
        text = raw.decode() if isinstance(raw, bytes) else raw
        if self.seconds and codex_bases.WARM_UP in text:
            time.sleep(self.seconds)
        return super().reply(raw, headers)


def _last_user_text(raw):
    """The newest user message of a request: a fork's request repeats the warm-up exchange before it."""
    users = [item for item in json.loads(raw).get("input") or [] if item.get("role") == "user"]
    content = users[-1].get("content") if users else None
    return " ".join(part.get("text", "") for part in content if isinstance(part, dict)) if isinstance(
        content, list) else ""


def settle(seconds=60):
    """Every base of the launch out of ``warming`` (or ``seconds``)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        runtimes = list(codex_runtime._POOL.values())
        if not any(base["state"] == "warming" for runtime in runtimes for base in runtime.bases.values()):
            return
        time.sleep(.05)


def trial(out, fake, settings, arm, case, delay, number, forks):
    folder = out / ("%s-%s-%d" % (case, arm, number))
    if case == "stored":  # An earlier launch warmed and remembered this variant's base here.
        seed = Chats(folder, settings, PROBE_CONNECTION, MODEL).new()
        codex_opening.prepare(seed).join(120)
        wait_ready()
        settle()
        seed.close()
        codex_runtime.close_all()
        TelemetryStore.shared(folder).receipts.clear()
    chats = Chats(folder, settings, PROBE_CONNECTION, MODEL)
    since, launched = len(fake.bodies), time.monotonic()
    chat = chats.new()  # The active chat at launch.
    if arm == "after":
        codex_opening.prepare(chat)
    time.sleep(max(0, delay - (time.monotonic() - launched)))
    at_send = [{"state": base["state"], "phase": base.get("phase")} for runtime in codex_runtime._POOL.values()
               for base in runtime.bases.values()]
    record = chats.turn(chat, FIRST % number)
    settle()
    chats.close()
    requests = fake.requests(since)
    first = next((r for r in requests if r["thread_id"] == record["native"]), {})
    bases = [base.get("thread") for runtime in codex_runtime._POOL.values() for base in runtime.bases.values()]
    codex_runtime.close_all()
    warm_ups = [r for r in TelemetryStore.shared(folder).receipts if r.get("kind") == "warm_up"]
    return dict(record, arm=arm, case=case, delay_s=delay, forked=record["native"] in forks, bases_at_send=at_send,
                first_request_key_is_base=bool(first) and first.get("prompt_cache_key") in bases,
                model_requests=len(requests), warm_up_requests=sum(_last_user_text(raw) == codex_bases.WARM_UP
                                                                   for raw in fake.bodies[since:]),
                warm_up_receipts=[{"status": r["status"], "usage": r.get("usage")} for r in warm_ups])


def summarize(records):
    def median(values):
        values = [v for v in values if isinstance(v, (int, float))]
        return round(statistics.median(values), 1) if values else None
    table = {}
    for case in ("stored", "none"):
        for arm in ("before", "after"):
            rows = [r for r in records if r["case"] == case and r["arm"] == arm and r.get("status") == "completed"]
            table["%s/%s" % (case, arm)] = {
                "chats": len(rows), "forked": sum(r["forked"] for r in rows),
                "connect_ms": median([r["phases"]["runtime_start_ms"] for r in rows]),
                "first_text_ms": median([r["phases"]["first_text_ms"] for r in rows]),
                "total_ms": median([r["phases"]["total_ms"] for r in rows]),
                "model_requests_per_launch": [r["model_requests"] for r in rows],
                "warm_ups_per_launch": [r["warm_up_requests"] for r in rows],
                "warm_up_receipts_per_launch": [len(r["warm_up_receipts"]) for r in rows],
                "first_request_cached_key": sum(r["first_request_key_is_base"] for r in rows)}
    return table


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--delay", type=float, default=3.0, help="seconds from launch to the first message")
    parser.add_argument("--warm-up-seconds", type=float, default=0.0)
    parser.add_argument("--cases", default="stored,none")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("OUT_DIR must be new or empty")
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    executable = providers.find_executable("codex", CODEX_BUNDLE if Path(CODEX_BUNDLE).exists() else "")
    settings = {"codex_path": executable, "model": MODEL, "reasoning": "low", "auth_directory": str(out / "state"),
                "connection_id": PROBE_CONNECTION}
    fake = SlowWarmUps(out, args.warm_up_seconds)
    command = fake_command(executable, out, fake.port)
    original = providers.CodexProvider._thread_parameters

    def parameters(self, models, *, voice=False):
        values, selected = original(self, models, voice=voice)
        return dict(values, modelProvider="angelo_probe"), selected
    fork, forks = codex_runtime.Runtime.fork, []

    def recorded_fork(runtime, variant):
        made = fork(runtime, variant)
        if made:
            forks.append(made[0]["thread"]["id"])
        return made
    records = []
    with ExitStack() as stack:
        for value in (patch.object(providers, "codex_command", return_value=command),
                      patch.object(codex_connect, "checks", probe_checks),
                      patch.object(providers.CodexProvider, "_thread_parameters", parameters),
                      patch.object(codex_runtime.Runtime, "fork", recorded_fork)):
            stack.enter_context(value)
        for number in range(args.trials):
            for case in args.cases.split(","):
                for arm in (("before", "after") if number % 2 == 0 else ("after", "before")):
                    records.append(trial(out, fake, settings, arm, case, args.delay, number, forks))
    result = {"billed": False, "delay_s": args.delay, "warm_up_seconds": args.warm_up_seconds,
              "records": records, "summary": summarize(records)}
    (out / "result.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
