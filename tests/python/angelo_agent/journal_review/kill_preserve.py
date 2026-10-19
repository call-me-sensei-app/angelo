# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual crash test: SIGKILL a process that keeps an unreadable chat again and again, with and
without hard links (``session_kept.preserve``: a link, or an exclusive empty placeholder replaced by a copy).

    python kill_preserve.py <angelo checkout> <kills per mode> <scratch folder>

Every kept name must hold the whole file or be an empty placeholder, the chat must never change, and a later
load must name a whole copy. Measured over 80 kills per mode: no partial or wrong copy, the chat
unchanged, a later load named a whole copy; without hard links almost every kill mid-copy left an empty
placeholder ``.unreadable`` (never removed) and a temporary file (removed by a load once stale).
"""
import os, random, signal, subprocess, sys, tempfile, time, uuid
WRITER = r'''
import os, sys
sys.path.insert(0, sys.argv[1] + "/scripts/addons_core")
from pathlib import Path
from unittest.mock import patch
from angelo_agent import session_kept
path = Path(sys.argv[2]); links = sys.argv[3] == "1"
stamps = ("20261015T12%04dZ" % n for n in range(100000))
with patch.object(session_kept.time, "strftime", lambda *a: next(stamps)), \
        (patch.object(session_kept.os, "link", side_effect=OSError(1, "no links")) if not links else open(os.devnull)):
    while True:
        name = session_kept.preserve(path)
        os.unlink(path.parent / name)   # so the next pass keeps (copies or links) again
'''
checkout, trials, scratch = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sys.path.insert(0, checkout + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore
from angelo_agent import session_kept
from angelo_agent.invocations import reserve_invocation
work = tempfile.mkdtemp(dir=scratch); script = os.path.join(work, "w.py"); open(script, "w").write(WRITER)
rng = random.Random(9)
for links in ("0", "1"):
    folder = tempfile.mkdtemp(dir=work); sid = str(uuid.uuid4())
    store = SessionStore(folder, sid); state = store.new("claude_subscription"); store.save(state)
    state["messages"].extend({"role": "user", "text": "Request %d " % i + "x" * 2000} for i in range(1500)); store.save(state)
    with store.path.open("ab") as h: h.write(b'{"seq": 9, "projects": {}}\n')
    original = store.path.read_bytes()
    bad, placeholders, temps = [], 0, 0
    for trial in range(trials):
        child = subprocess.Popen([sys.executable, script, checkout, str(store.path), links], stderr=subprocess.DEVNULL)
        passes = 0
        time.sleep(0.05 + rng.uniform(0, 0.4)); child.send_signal(signal.SIGKILL); child.wait()
        for kept in session_kept.kept_copies(store.path):
            data = kept.read_bytes()
            if data == b"":
                placeholders += 1
            elif data != original:
                bad.append(kept.name)
        temps += len([n for n in os.listdir(store.path.parent) if n.endswith(".tmp")])
        if store.path.read_bytes() != original:
            bad.append("chat changed")
    loaded = SessionStore(folder, sid).load()
    named = [m.get("i18n", [None, {}])[1].get("file") for m in loaded["messages"] if "kept the file" in m.get("text", "")]
    final = [(k.name == named[0], k.stat().st_size) for k in session_kept.kept_copies(store.path) if k.name in named]
    print("links=%s: %d kills (%d passes in the last); partial or wrong copies %s; empty placeholders seen %d; temporaries seen %d; "
          "a later load names %s (whole copy: %s)" % (links, trials, passes, bad[:3] or "none", placeholders, temps, named,
          [size == len(original) for _, size in final]))
