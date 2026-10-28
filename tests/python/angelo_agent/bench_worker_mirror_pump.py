# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker chat's mirror costs the lead's main thread per pump, against the chat's size. No Blender.

Run with Blender's bundled Python from this folder::

    python3.13 -B bench_worker_mirror_pump.py [--earlier 0,16,64,256] [--steps 12] [--trace]

A worker ``Controller`` writes a chat holding ``N`` earlier large results (about 80 KB each, a long edit's geometry
delta), their activity rows and mutation IDs, compacted to one base line. Then ``--steps`` edits each append a new
result and row and save, as a worker does, and the mirror pumps the journal frame. Per pump the trace records the
milliseconds, frames drained and their kinds, the new bytes, whether the read was a tail or a whole read, and the
split between opening the file (``open_shared``, ``stat_of``), reading and parsing it (``MirrorStore._journal``
less its opens), cleaning (the rows', results', ledger's and task memory's checks) and the rest (the reload's row
compare and swap, the pump). The timers wrap the product functions here only: nothing is logged by the product.

The figure that matters is the median pump against ``N``: the main thread's work per frame must follow the new
records, not the chat (doc/angelo/parallel_agents_hosting.rst, "Mirror read cost").
"""

import argparse
import random
import statistics
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (session_journal, sessions, worker_consent_lead, worker_mirror,  # noqa: E402
                          worker_mirror_journal, worker_mirror_ops)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.worker_mirror import MirrorController  # noqa: E402

KIB = 80


def large_result(index, kib=KIB):
    """A completed edit's result record: a summary and a geometry delta of about ``kib`` KB."""
    rng = random.Random(index)
    vertices = [[round(rng.uniform(-5, 5), 4) for _ in range(3)] for _ in range(kib * 1024 // 30)]
    return {"document": "('doc', 1)", "invocation_digest": "%064x" % index,
            "result": {"ok": True, "summary": "made part %d" % index,
                       "delta": {"geometry": {"Part%d" % index: {"vertices": vertices}}}}}


def add_call(state, identity, record):
    state["results"][identity] = record
    state["invocations"]["entries"][identity] = {"digest": "%064x" % (hash(identity) & (2 ** 255)),
                                                 "document": "%064x" % 7}
    state["messages"].append({"role": "activity", "call_id": identity, "tool": "angelo_scene_program",
                              "arguments": {"steps": [1, 2, 3]}, "result": {"ok": True}, "status": "completed",
                              "text": "Made a part"})
    state["messages"].append({"role": "assistant", "text": "Next I will shape the part. " * 8})


class Split:
    """Seconds spent in each wrapped part since the last ``take``."""

    def __init__(self):
        self.parts = {}

    def wrap(self, name, function):
        def timed(*args, **kwargs):
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.parts[name] = self.parts.get(name, 0.0) + time.perf_counter() - start
        return timed

    def take(self):
        parts, self.parts = self.parts, {}
        return parts


def run(earlier, steps):
    temporary = tempfile.TemporaryDirectory()
    directory, session = Path(temporary.name), str(uuid.uuid4())
    worker = Controller(directory, session_id=session)
    for index in range(earlier):
        add_call(worker.state, "call-%d" % index, large_result(index))
    worker.save()
    worker.store.compact(worker.state)
    worker_mirror_ops.bind(spawn=lambda function: function())
    worker_consent_lead.reset()
    mirror = MirrorController(directory, session, lambda *_args, **_kwargs: {"ok": True})
    router = worker_mirror.EventRouter()
    router.open(mirror)
    split, trace = Split(), []
    store = mirror.store._store
    with ExitStack() as stack:
        for module, name, label in ((session_journal, "open_shared", "open"), (session_journal, "stat_of", "open"),
                                    (sessions, "clean_memory", "clean")):
            stack.enter_context(mock.patch.object(module, name, split.wrap(label, getattr(module, name))))
        for name, label in (("_journal", "read"), ("_rows", "clean"), ("_results", "clean"), ("_ledger", "clean")):
            stack.enter_context(mock.patch.object(store, name, split.wrap(label, getattr(store, name))))
        size = store.path.stat().st_size
        for step in range(steps):
            add_call(worker.state, "new-%d" % step, large_result(10_000 + step, kib=KIB // 2))
            worker.save()
            router({"session": session, "kind": "journal", "t": 0.0, "data": {
                "seq": step + 1, "path": str(store.path), "records": worker.store._writer.records}})
            whole, kinds = store.whole_reads, [kind for kind, _data in list(mirror.events.queue)]
            split.take()
            start = time.perf_counter()
            mirror.pump_events()
            total = time.perf_counter() - start
            parts = split.take()
            parts["read"] = parts.get("read", 0.0) - parts.get("open", 0.0)
            parts["rest"] = total - sum(parts.values())
            new_size = store.path.stat().st_size
            trace.append({"ms": total * 1000, "frames": len(kinds), "kinds": kinds, "new_bytes": new_size - size,
                          "read": "whole" if store.whole_reads > whole else "tail",
                          **{name + "_ms": seconds * 1000 for name, seconds in parts.items()}})
            size = new_size
        if mirror.state != Controller(directory, session_id=session).state:
            raise AssertionError("the mirror's state is not a load of the file")
    worker_mirror_ops.reset()
    worker_consent_lead.reset()
    temporary.cleanup()
    return trace


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--earlier", default="0,16,64,256")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--trace", action="store_true", help="print every pump, not only the summary")
    arguments = parser.parse_args()
    print("%6s %9s %9s %7s %7s %7s %7s %7s" % ("N", "median", "max", "open", "read", "clean", "rest", "kB/pump"))
    for earlier in [int(value) for value in arguments.earlier.split(",")]:
        trace = run(earlier, arguments.steps)
        if arguments.trace:
            for pump in trace:
                print("   ", {key: round(value, 3) if type(value) is float else value for key, value in pump.items()})

        def median(key):
            return statistics.median(pump.get(key, 0.0) for pump in trace)
        print("%6d %7.2fms %7.2fms %7.2f %7.2f %7.2f %7.2f %7.0f" % (
            earlier, median("ms"), max(pump["ms"] for pump in trace), median("open_ms"), median("read_ms"),
            median("clean_ms"), median("rest_ms"), median("new_bytes") / 1024))


if __name__ == "__main__":
    main()
