# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual crash test: SIGKILL a process that saves forever, then reload after every kill.

    python kill_loop.py <angelo checkout> <trials> [<Angelo binary>]

Without a binary the writer is host Python; with one it is the app (``--background --factory-startup``, the
add-on bundled in it, a sandboxed home: CFFIXED_USER_HOME). Every save that returned (printed ACK) must be
on disk and the ledger never closed. Measured over 200 host kills and 12 stream-app kills:
no acknowledged ID lost, no corruption (a kill cannot tear one write(); torn tails need power loss).
"""

import os
import random
import signal
import subprocess
import sys
import tempfile
import time
import uuid

WRITER = '''
import sys
argv = sys.argv[sys.argv.index("--") + 1:]
if argv[0] != "-":
    sys.path.insert(0, argv[0] + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore
from angelo_agent.invocations import reserve_invocation
from angelo_agent import session_writer
session_writer.COMPACT_MIN_BYTES = 20000  # Compactions happen often in this run.
store = SessionStore(argv[1], argv[2])
state = store.load()
n = len(state["invocations"]["entries"])
while True:
    n += 1
    reserve_invocation(state, "%s/call-%d" % (state["id"], n), "%064x" % n, "doc")
    state["results"]["%s/call-%d" % (state["id"], n)] = {"document": "d", "phase": "queued", "result": {"n": n}}
    state["messages"].append({"role": "activity", "call_id": "call-%d" % n, "text": "R\\u00e9gl\\u00e9 " * 20})
    store.save(state)
    print("ACK %d" % n, flush=True)
    if n % 7 == 0:
        store.compact_if_due(state)
'''

checkout, trials = sys.argv[1], int(sys.argv[2])
binary = sys.argv[3] if len(sys.argv) > 3 else None
sys.path.insert(0, checkout + "/scripts/addons_core")
from angelo_agent import session_journal  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

work = tempfile.mkdtemp()
script = os.path.join(work, "writer.py")
with open(script, "w", encoding="utf-8") as handle:
    handle.write(WRITER)
os.makedirs(os.path.join(work, "home"))
env = dict(os.environ, CFFIXED_USER_HOME=os.path.join(work, "home"))
rng, problems = random.Random(1), []
for trial in range(trials):
    if trial % 8 == 0:  # A ledger holds 4096 IDs.
        folder, sid, acknowledged = tempfile.mkdtemp(dir=work), str(uuid.uuid4()), 0
    command = ([binary, "--background", "--factory-startup", "--python", script, "--", "-", folder, sid] if binary
               else [sys.executable, script, "--", checkout, folder, sid])
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    for line in child.stdout:
        if line.startswith("ACK"):
            acknowledged = int(line.split()[1])
            break
    time.sleep(rng.uniform(0.0, 0.3))
    child.send_signal(signal.SIGKILL)
    for line in child.communicate()[0].splitlines():
        if line.startswith("ACK"):
            acknowledged = int(line.split()[1])
    try:
        session_journal.read(SessionStore(folder, sid).path)
    except ValueError as error:
        problems.append((trial, str(error)))
        continue
    loaded = SessionStore(folder, sid).load()
    missing = [n for n in range(1, acknowledged + 1) if "%s/call-%d" % (sid, n) not in loaded["invocations"]["entries"]]
    if loaded["invocations"]["closed"] or missing:
        problems.append((trial, loaded["invocations"]["closed"], missing[:3]))
print("%d kills, problems: %s" % (trials, problems[:5] or "none"))
