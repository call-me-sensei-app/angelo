# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual crash test: SIGKILL a process that rewrites its chat (a switch or close) in a loop.

    python kill_compact.py <angelo checkout> <kills> <scratch folder>

Every acknowledged mutation ID must survive, no chat may load unverified or be kept as unreadable, and a
temporary file a kill left must be removed by the next load once stale. Measured over 150 kills:
22 leftover temporaries (all removed), no problem.
"""
import os, random, signal, subprocess, sys, tempfile, time, uuid
WRITER = r'''
import sys
sys.path.insert(0, sys.argv[1] + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore
from angelo_agent.invocations import reserve_invocation
store = SessionStore(sys.argv[2], sys.argv[3])
state = store.load()
n = len(state["invocations"]["entries"])
if not state["messages"]:
    state["messages"].extend({"role": "assistant", "text": "pad %d " % i + "x" * 3000} for i in range(400))
while True:
    n += 1
    reserve_invocation(state, "%s/call-%d" % (state["id"], n), "%064x" % n, "doc")
    state["messages"].append({"role": "activity", "call_id": "call-%d" % n, "text": "Réglé " * 20})
    store.save(state) if n % 2 else store.compact(state)
    print("ACK %d" % n, flush=True)
'''
checkout, trials = sys.argv[1], int(sys.argv[2])
sys.path.insert(0, checkout + "/scripts/addons_core")
from angelo_agent import session_journal
from angelo_agent.sessions import SessionStore
work = tempfile.mkdtemp(dir=sys.argv[3])
script = os.path.join(work, "writer.py"); open(script, "w").write(WRITER)
rng, problems, temps_seen, kills = random.Random(5), [], 0, 0
for trial in range(trials):
    if trial % 10 == 0:
        folder, sid, acknowledged = tempfile.mkdtemp(dir=work), str(uuid.uuid4()), 0
    child = subprocess.Popen([sys.executable, script, checkout, folder, sid], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    for line in child.stdout:
        if line.startswith("ACK"):
            acknowledged = int(line.split()[1]); break
    time.sleep(rng.uniform(0.0, 0.25))
    child.send_signal(signal.SIGKILL); kills += 1
    for line in child.communicate()[0].splitlines():
        if line.startswith("ACK"):
            acknowledged = int(line.split()[1])
    sessions = os.path.join(folder, "sessions")
    temps = [n for n in os.listdir(sessions) if n.endswith(".tmp")]
    temps_seen += len(temps)
    # Age them past discard_stale's 60 s so the next load must remove them.
    for name in temps:
        os.utime(os.path.join(sessions, name), (time.time() - 120, time.time() - 120))
    loaded = SessionStore(folder, sid).load()
    left = [n for n in os.listdir(sessions) if n.endswith(".tmp")]
    kept = [n for n in os.listdir(sessions) if n.endswith(".unreadable")]
    missing = [n for n in range(1, acknowledged + 1) if "%s/call-%d" % (sid, n) not in loaded["invocations"]["entries"]]
    if loaded["invocations"]["closed"] or missing or kept or left:
        problems.append((trial, loaded["invocations"]["closed"], missing[:3], kept, left))
print("%d kills mid save/compact, %d leftover temporaries seen (all aged and removed by the next load), problems: %s"
      % (kills, temps_seen, problems[:5] or "none"))
