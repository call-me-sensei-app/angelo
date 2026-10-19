# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the chat's marble wait costs the main thread per frame and per timer tick (ratchet operations).

Run in an isolated process, after other benchmarks and builds have stopped::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_chat_waiting.py -- [--samples 21]

While a turn plans, ``marble_host``'s timer ticks (at most 30 times a second while something moves, else at the
next scheduled change or every 0.1 s) and each chat redraw draws the hero and the live row's icon. Background
Blender has no GPU, so this measures the CPU side of both, which is all the Python that runs per frame:

* ``chat_waiting/hero_frame``: one hero draw's ``marble_wait.frame`` 0.2 s after a chisel tap (its burst in
  flight, dust, the shake) with the standing stone rebuilt, as on a frame in which a chip fell;
* ``chat_waiting/icon_frame``: the same for the 22 px icon;
* ``chat_waiting/reveal_frame``: the hero 0.3 s into the reveal, every remaining chip in flight (the heaviest);
* ``chat_waiting/timer_tick``: one tick's ``Tracker.update`` and ``next_frame`` on a 160-row chat whose status
  carries a plan's provisional rows;
* ``chat_waiting/first_wait_geometry``: the one-time work of a process's first wait (the mark triangulated, both
  chip sets built), caches cleared before each sample.

The GPU side (batches and draw calls) is measured in the compiled app by ``test_blender_chat_waiting`` as wall
time per draw. Samples count the main thread's instructions with the collector off (as
``benchmark_blender_view_scene_check`` does); the report (``chat-waiting.json``) has a ``ratchet`` section that
``test_blender_perf_ratchet`` keeps from growing.
"""

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import perf_counters  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-chat-waiting-benchmark"))
from angelo_agent import marble_carving, marble_mark, marble_wait, plan_progress, plan_steps  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN  # noqa: E402
from angelo_agent.plan_progress import PlanProgress  # noqa: E402

PLAN = ('{"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [%s]}}]}'
        % ", ".join('{"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Tower %d"}}' % k
                    for k in range(12)))


def parse(argv):
    parser = argparse.ArgumentParser(description="Chat marble-wait benchmark")
    parser.add_argument("--samples", type=int, default=21, help="measured calls per operation after one warmup")
    args = parser.parse_args(argv)
    if not 3 <= args.samples <= 99:
        parser.error("Use 3-99 samples")
    return args


def measure(function, samples, before=None):
    """One warmup and ``samples`` calls, each after ``before`` and a collection, with the collector off."""
    raw = []
    for index in range(samples + 1):
        if before:
            before()
        gc.collect()
        gc.disable()
        try:
            start = (perf_counters.read_thread_counts() or {}).get("instructions"), time.process_time_ns()
            wall = time.perf_counter_ns()
            function()
            wall = time.perf_counter_ns() - wall
            end = (perf_counters.read_thread_counts() or {}).get("instructions"), time.process_time_ns()
        finally:
            gc.enable()
        if index:
            raw.append({"wall_ms": wall / 1e6, "cpu_ms": (end[1] - start[1]) / 1e6,
                        "instructions": None if start[0] is None else end[0] - start[0]})
    walls = [sample["wall_ms"] for sample in raw]
    return {"samples_ms": [round(value, 4) for value in walls], "median_ms": perf_counters.describe(walls)["median"],
            "p95_ms": perf_counters.describe(walls)["p95"],
            "cpu_ms": perf_counters.describe([sample["cpu_ms"] for sample in raw]),
            "instructions": perf_counters.describe([sample["instructions"] for sample in raw]),
            "counter": "main thread"}


def carving_wait():
    """A wait 3 s into carving with a tap just landed: the frames after a real progress event."""
    wait = marble_wait.Wait(0.0)
    for step in range(1, 91):
        wait.advance(step / 30)
    wait.tap(3.0)
    wait.advance(3.2)
    return wait


def chat():
    progress = PlanProgress()
    progress.feed(PLAN)
    status = plan_progress.status(STATUS_PLAN, progress.detail(), plan_steps.encode(progress))
    messages = [{"role": "user" if k % 4 == 0 else "assistant", "text": "Row %d" % k} for k in range(159)]
    messages.append({"role": "user", "text": "Build a castle"})
    return SimpleNamespace(status=status, busy=True, voice=None, state={"id": "bench", "messages": messages},
                           _request_message_index=159)


def main():
    args = parse(fixtures.script_arguments())
    report = {"environment": fixtures.environment(), "samples": args.samples, "warmup": 1, "operations": {}}
    ratchet = fixtures.Ratchet()
    marble_wait.prepare()
    wait = carving_wait()
    revealed = carving_wait()
    revealed.reveal(5.0)
    controller, tracker = chat(), marble_wait.Tracker()
    tracker.update(controller, 1.0)

    def stale(block):
        return lambda: setattr(block, "_solid", None)

    def tick():
        tracker.update(controller, 1.5)
        tracker.waits[controller.state["id"]].next_frame(1.5)

    def cold():
        marble_mark.mesh.cache_clear()
        marble_carving.chips.cache_clear()

    cases = {
        "hero_frame": (lambda: marble_wait.frame(wait, wait.hero, 3.2, False), stale(wait.hero)),
        "icon_frame": (lambda: marble_wait.frame(wait, wait.icon, 3.2, True), stale(wait.icon)),
        "reveal_frame": (lambda: marble_wait.frame(revealed, revealed.hero, 5.3, False), None),
        "timer_tick": (tick, None),
        "first_wait_geometry": (lambda: ([marble_mark.mesh(name) for name in marble_mark.PART_NAMES],
                                         marble_wait.prepare()), cold),
    }
    drawn = marble_wait.frame(revealed, revealed.hero, 5.3, False)
    assert len(drawn.flying.indices) > 100 and drawn.solid is None, "The reveal frame has every chip in flight"
    assert marble_wait.frame(wait, wait.hero, 3.2, False).dust.indices, "The tap frame has dust"
    for name, (function, before) in cases.items():
        stats = measure(function, args.samples, before)
        report["operations"][name] = stats
        ratchet.add("chat_waiting/" + name, stats)
        print("CHAT_WAITING", name, json.dumps({"median_ms": stats["median_ms"], "instructions": (
            stats["instructions"] or {}).get("median")}), flush=True)
    report["ratchet"] = ratchet.section()
    report["success"] = True
    (OUT / "chat-waiting.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("CHAT_WAITING_BENCHMARK_DONE", json.dumps({"success": True, "results": str(OUT / "chat-waiting.json")}),
          flush=True)


main()
