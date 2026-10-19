# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual repro: unlocked row appends racing a provider thread's locked saves.

    python race_stress.py <angelo checkout> <trials>                                   # host Python
    Angelo --background --factory-startup --python race_stress.py -- - <trials>        # the app's bundled add-on

The main thread appends rows without the controller lock at random moments (job_ui._finished) while a provider
thread saves continuously under it (journal_tool), at Python's default 5 ms switch interval. A journal that no
longer loads reopens empty with its mutation IDs closed. Measured with unlocked appends: a corrupt
journal in 3-4 of 40 host trials and 1 of 60 stream-app trials; a build that saves the whole file every time, 0 of
40.
"""

import random
import sys
import tempfile
import threading
import time
import uuid

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
if argv[0] != "-":
    sys.path.insert(0, argv[0] + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore  # noqa: E402


def activity(index):
    return {"role": "activity", "call_id": "c%d" % index, "tool": "angelo_scene_program", "text": "",
            "status": "completed",
            "arguments": {"mode": "APPLY", "operations": [{"kind": "transform", "target": "Cube.%03d" % i,
                                                          "parameters": {"location": [i, 2.5, 0.25]}} for i in range(12)]},
            "result": {"success": True, "changes": {"modified": [{"name": "Cube.%03d" % i, "fields": ["location"]}
                                                                for i in range(40)]}}}


rng = random.Random(7)
corrupt = appends = 0
trials = int(argv[1])
start = time.time()
for trial in range(trials):
    with tempfile.TemporaryDirectory() as folder:
        store = SessionStore(folder, str(uuid.uuid4()))
        state = store.new("claude_subscription")
        lock, stop = threading.RLock(), threading.Event()
        state["messages"].extend(activity(i) for i in range(70))
        store.save(state)

        def provider():
            count = 0
            while not stop.is_set():
                with lock:
                    state["results"]["k%d" % count] = {"document": "d", "phase": "queued", "result": {}}
                    store.save(state)
                count += 1
        worker = threading.Thread(target=provider)
        worker.start()
        for index in range(40):
            time.sleep(rng.random() * 0.004)
            state["messages"].append({"role": "system", "notice": True, "text": "job %d finished" % index})
            appends += 1
        stop.set()
        worker.join()
        try:
            with lock:
                store.save(state)
        except ValueError:
            pass
        loaded = SessionStore(folder, state["id"]).load()
        if loaded["invocations"]["closed"] or len(loaded["messages"]) != len(state["messages"]):
            corrupt += 1
print("switch interval %.4f s: %d corrupt journals in %d trials (%d unlocked appends), %.0f s"
      % (sys.getswitchinterval(), corrupt, trials, appends, time.time() - start))
