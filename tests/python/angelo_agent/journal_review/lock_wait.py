# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual measurement: how long a main-thread row change (job_ui._finished) waits for the lock.

    python lock_wait.py <angelo checkout> <a chat file named <uuid>.json> append|invalidate

A provider thread saves under the lock in a loop (``append``: every save appends; ``invalidate``: every tenth
is a whole rewrite, as after a key redaction). Measured on a 2.5 MB chat from a 500-edit replay,
load average 6-8: append p99 0.36 ms, max 3.5 ms; with rewrites p99 21 ms, max 51 ms. In the previous build
every save was a rewrite (13.7 ms median on the same chat).
"""
import os, sys, json, shutil, tempfile, time, threading, random, statistics
root, chat, mode = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore
from angelo_agent.invocations import reserve_invocation
folder = tempfile.mkdtemp(); sid = os.path.basename(chat)[:-5]
store = SessionStore(folder, sid); shutil.copy(chat, store.path)
state = store.load(); store.seal(len(state["messages"])); store.save(state)
lock, stop = threading.RLock(), threading.Event()
holds = []
def provider():
    n = 0
    while not stop.is_set():
        n += 1
        with lock:
            t = time.perf_counter()
            key = "%s/p-%d" % (state["id"], n)
            reserve_invocation(state, key, "%064x" % n, "doc")
            state["results"][key] = {"document": "d", "phase": "admitted", "result": {}}
            if mode == "invalidate" and n % 10 == 0:
                store.invalidate()  # e.g. a key redaction on the main thread just before this admission
            store.save(state)
            holds.append((time.perf_counter() - t) * 1000)
        time.sleep(0.002)
worker = threading.Thread(target=provider); worker.start()
rng = random.Random(3); waits = []
for i in range(400):
    time.sleep(rng.random() * 0.01)
    t = time.perf_counter()
    with lock:  # job_ui._finished
        state["messages"].append({"role": "system", "notice": True, "text": "Job %d finished" % i, "job": "j%d" % i})
    waits.append((time.perf_counter() - t) * 1000)
stop.set(); worker.join()
waits.sort(); holds.sort()
q = lambda v, p: round(v[min(len(v) - 1, int(p * len(v)))], 2)
print(json.dumps({"mode": mode, "provider_saves": len(holds), "hold_p50": q(holds, .5), "hold_max": round(holds[-1], 1),
                  "main_wait_p50": q(waits, .5), "main_wait_p99": q(waits, .99), "main_wait_max": round(waits[-1], 1)}))
