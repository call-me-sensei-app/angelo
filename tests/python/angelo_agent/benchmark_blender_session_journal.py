# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session-length costs outside an edit's checks: conversation saves and the snapshot worker's probe.

Run the Angelo binary --background --factory-startup --python-exit-code 1
--python this_file. Unbilled; no model, scene or UI timer. Main-thread
retired instructions (``benchmark_fixtures``), each operation the median of
ten samples.

* ``session``: 500 edits through the product ``SessionStore``, each saved as
  the controller saves it (doc/angelo/performance.rst, "Per-edit cost over a
  long session"): the request's user row, the provider thread's admission
  save (a result record, a mutation ID and the call's row), staging, commit
  start, completion (the row's ~5 KB result, a new task memory) and the
  turn's end (which compacts once the records outgrow the base). The edit
  saves and the admission save of edits 41-50 and 491-500 are ratchet
  operations, and the run fails when edit 500's exceed edit 50's by more than
  ``FLAT_LIMIT``: a save must cost the request and what changed, never the
  conversation.
* ``probe``: the snapshot worker's lock-free inventory probe of an unchanged
  history of 100 and 1,000 stored snapshots (``inventory(probe=True)``, which
  re-measures only changed records). It still stats every record, so it is
  linear in the history; the two operations pin its per-record cost.
"""

import json
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
from snapshot_fixtures import build_history  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-session-journal-benchmark"))
os.environ.pop("ANGELO_JOURNAL_VERIFY", None)  # The product's saves: a native suite's runner turns verification on.
from angelo_agent import checkpoint_inventory, checkpoint_inventory_cache, task_memory  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

EDITS = 500
WINDOW = 10  # Edits in each measured window, ending at edit 50 and edit 500.
FLAT_LIMIT = 1.10  # Edit 500's median / edit 50's, saves and admission alike.
HISTORIES = (100, 1000)
DOCUMENT = ("benchmark-document", 1)


def instructions():
    counts = fixtures.perf_counters.read_thread_counts() or fixtures.perf_counters.read_rusage()
    return counts["instructions"] if counts else 0


def result(index):
    """An APPLY program's result, the size of the replay's (about 5 KB of revisions and checks)."""
    return {"success": True, "status": "committed", "scene_changed": True,
            "live_revisions": {"objects": [{"name": "Eval Cube", "handle": "h%04d" % index,
                                            "object_revision": "%064x" % (index * 7919)}]},
            "verification": {"status": "complete", "checks": [{"kind": "transform", "observed": [index, 0.5, 1.0],
                                                               "expected": [index, 0.5, 1.0], "passed": True}] * 12},
            "changes": {"modified": ["Eval Cube"], "fields": ["location", "scale"] * 40}}


class Session:
    """One chat saved exactly as the controller's request path saves it."""

    def __init__(self, folder):
        self.store = SessionStore(folder, str(uuid.uuid4()))
        self.state = self.store.new("claude_subscription")
        self.state["task_memory"] = task_memory.new_memory(str(Path(folder).resolve()))
        self.store.save(self.state)

    def save(self, turn_end=False):
        """Main-thread instructions of one save, as the controller makes it."""
        self.state["updated"] = time.time()
        started = instructions()
        if turn_end:
            self.store.compact_if_due(self.state)  # The turn's end compacts once the records outgrow the base.
        else:
            self.store.save(self.state)
        return instructions() - started

    def edit(self, index):
        """Returns (instructions of the edit's main-thread saves, of its admission save) for one edit."""
        state = self.state
        self.store.seal(len(state["messages"]))
        state["messages"].append({"role": "user", "text": "Move 'Eval Cube' to %d 0 0" % index,
                                  "checkpoint": "c%d" % index})
        main = self.save()  # The request's row.
        key = state["id"] + "/call-%d" % index
        reserve_invocation(state, key, "%064x" % (index + 1), repr(DOCUMENT))
        state["results"][key] = {"document": repr(DOCUMENT), "phase": "queued", "result": {"interrupted": True}}
        state["messages"].append({"role": "activity", "call_id": key, "tool": "angelo_scene_program", "text": "",
                                  "arguments": {"mode": "APPLY", "operations": [{"kind": "transform"}]},
                                  "status": "queued"})
        admission = self.save()  # A provider thread's, under the controller lock.
        for phase in ("staging", "commit_started"):
            state["results"][key] = {"document": repr(DOCUMENT), "phase": phase, "result": {"interrupted": True}}
            main += self.save()
        state["results"][key] = {"document": repr(DOCUMENT), "phase": "completed", "result": result(index)}
        while len(state["results"]) > 256:
            state["results"].pop(next(iter(state["results"])))
        state["messages"][-1] = dict(state["messages"][-1], status="completed", result=result(index))
        self.state = state = task_memory.on_verified_result(state, DOCUMENT, call_id=key, tool="angelo_scene_program",
                                                            result=result(index))
        main += self.save()  # Completion.
        state["messages"].append({"role": "assistant", "text": "Moved."})
        return main + self.save(turn_end=True), admission


def session_costs(report, ratchet, problems):
    session = Session(OUT / "session")
    costs = [session.edit(index) for index in range(1, EDITS + 1)]
    windows = {"at_50": costs[50 - WINDOW:50], "at_500": costs[EDITS - WINDOW:EDITS]}
    medians = {}
    for name, window in windows.items():
        medians["edit_saves_" + name] = statistics.median(main for main, _ in window)
        medians["admission_save_" + name] = statistics.median(admission for _, admission in window)
        for kind in ("edit_saves_", "admission_save_"):
            ratchet.add("conversation_journal/" + kind + name, {"instructions": {"median": medians[kind + name]}})
    ratios = {kind: medians[kind + "_at_500"] / medians[kind + "_at_50"] for kind in ("edit_saves", "admission_save")}
    report["session"] = dict(medians, edits=EDITS, journal_bytes=session.store.path.stat().st_size,
                             ratios={kind: round(value, 4) for kind, value in ratios.items()}, limit=FLAT_LIMIT,
                             edit_save_instructions=[main for main, _ in costs],
                             admission_instructions=[admission for _, admission in costs])
    for kind, ratio in ratios.items():
        if ratio > FLAT_LIMIT:
            problems.append("%s grew over the session: edit 500 took %.3fx edit 50" % (kind, ratio))
    print("SESSION_JOURNAL", json.dumps({key: value for key, value in report["session"].items()
                                         if not key.endswith("instructions")}), flush=True)


def probe_costs(report, ratchet):
    checkpoint_inventory_cache.RACY_SECONDS = 0  # The fixture's records are seconds old; the product waits 2 s.
    for count in HISTORIES:
        store = CheckpointStore(build_history(OUT / ("history-%d" % count), count), str(uuid.UUID(int=0)))
        checkpoint_inventory.inventory(store, probe=True)  # Measures every record once.
        samples = []
        for _ in range(WINDOW + 1):
            started = instructions()
            probe = checkpoint_inventory.inventory(store, probe=True)
            samples.append(instructions() - started)
        assert len(probe["checkpoints"]) == count and not probe["issues"], probe["issues"][:3]
        median = statistics.median(samples[1:])
        ratchet.add("snapshot_worker/probe_unchanged_%04d" % count, {"instructions": {"median": median}})
        report["probe"]["history_%d" % count] = {"instructions_median": median, "per_record": round(median / count)}
        print("SESSION_JOURNAL_PROBE", count, json.dumps(report["probe"]["history_%d" % count]), flush=True)
    checkpoint_inventory_cache.forget()


ratchet = fixtures.Ratchet()
report = dict(fixtures.environment(), method="main-thread retired instructions, median of ten samples",
              session={}, probe={})
problems = []
session_costs(report, ratchet, problems)
probe_costs(report, ratchet)
report["ratchet"] = ratchet.section()
report["problems"] = problems
report["success"] = not problems
(OUT / "session-journal.json").write_text(json.dumps(report, indent=2, allow_nan=False))
print("SESSION_JOURNAL_BENCHMARK_DONE", json.dumps({"success": report["success"], "problems": problems,
                                                   "results": str(OUT / "session-journal.json")}), flush=True)
if problems:
    sys.exit(1)
