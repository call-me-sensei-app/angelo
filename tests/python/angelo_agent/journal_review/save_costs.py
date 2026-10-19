# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual measurement: what one save costs on a real long chat.

    python save_costs.py <angelo checkout> <a chat file named <uuid>.json>

Measured with host Python on a 4.6 MB chat of 1,268 rows: load 41 ms, first save after a load 16 ms,
append 0.27 ms median, rewrite after invalidate 15-17 ms, compaction on a switch 15-17 ms, and 21 ms for a
switch with nothing new (a whole rewrite). The previous build, which rewrote on every save, saved the same
chat (compacted, 2.5 MB) in 13.7 ms.
"""
import os, sys, json, shutil, tempfile, time, uuid, statistics, threading, random
root, chat = sys.argv[1], sys.argv[2]
rc7 = len(sys.argv) > 3 and sys.argv[3] == "rc7"
sys.path.insert(0, root if root.endswith("addons_core") else root + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore
from angelo_agent.invocations import reserve_invocation

def ms(f):
    t = time.perf_counter(); f(); return (time.perf_counter() - t) * 1000

folder = tempfile.mkdtemp()
sid = os.path.basename(chat)[:-5]
store = SessionStore(folder, sid)
shutil.copy(chat, store.path)
size = os.path.getsize(store.path)
t = time.perf_counter(); state = store.load(); load_ms = (time.perf_counter() - t) * 1000
rows = len(state["messages"])
out = {"file_mb": round(size / 1e6, 2), "rows": rows, "ledger": len(state["invocations"]["entries"]), "load_ms": round(load_ms, 1)}

def edit(n):
    key = "%s/rev-%d" % (state["id"], n)
    reserve_invocation(state, key, "%064x" % n, "doc")
    state["results"][key] = {"document": "d", "phase": "queued", "result": {}}
    state["messages"].append({"role": "activity", "call_id": "rev-%d" % n, "tool": "angelo_scene_program", "text": "",
                              "status": "completed", "arguments": {"mode": "APPLY"}, "result": {"success": True}})

if hasattr(store, "seal"):
    store.seal(len(state["messages"]))
first = ms(lambda: store.save(state))  # first save after a load
appends = []
for n in range(30):
    edit(n)
    appends.append(ms(lambda: store.save(state)))
out["first_save_after_load_ms"] = round(first, 1)
out["save_median_ms"] = round(statistics.median(appends), 2)
out["save_max_ms"] = round(max(appends), 2)
if hasattr(store, "invalidate"):
    rew = []
    for n in range(5):
        store.invalidate(); edit(100 + n)
        rew.append(ms(lambda: store.save(state)))
    out["rewrite_after_invalidate_ms"] = [round(x, 1) for x in rew]
    comp = [ms(lambda: store.compact(state)) for _ in range(5)]
    out["compact_switch_ms"] = [round(x, 1) for x in comp]
    # A switch away from a chat already compacted with nothing new: still a whole rewrite?
    store.compact(state)
    out["compact_unchanged_ms"] = round(ms(lambda: store.compact(state)), 1)
    out["last_write"] = store.last_write
print(json.dumps(out))
