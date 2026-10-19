# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual probe: kept copies and a bound key.

    python kept_key.py <angelo checkout> <a chat file named <uuid>.json>

A: five opens of an unreadable chat that held the key, each binding it and crashing before a save, keep one
copy and leave no piece of the key. B (NOTE): a key streamed in two pieces with a save between, before it was
bound, then the chat unreadable (damage, or a later build's record before its own rewrite), then bound: the
kept copy keeps the 20-character prefix (15 six-character pieces) and ``redact_kept`` reports nothing held. C:
rewrites when a chat opens and the key is bound. D: binding with a 2.5 MB kept copy holding the key costs
about 27 ms on the calling thread (row scan and the copy's redaction).
"""
from pathlib import Path
import glob, json, os, sys, tempfile, time, uuid
from unittest.mock import patch
sys.path.insert(0, sys.argv[1] + "/scripts/addons_core")
from angelo_agent import session_journal, session_kept
from angelo_agent.controller import Controller
from angelo_agent.sessions import SessionStore
from angelo_agent.invocations import reserve_invocation
KEY = "sk-test-fake-0123456789abcdef"
def pieces(data):
    key = KEY.encode()
    return len({key[s:s + 6] for s in range(len(key) - 5)} & {data[s:s + 6] for s in range(len(data) - 5)})
def kept(path):
    return [p for p in session_kept.kept_copies(path)]
stamps = ("20261015T11%04dZ" % n for n in range(10000))
newsecond = patch.object(session_kept.time, "strftime", lambda *a: next(stamps))

# A. A chat that held the key (pasted before binding), unreadable; five opens that bind the key and crash before
#    the first save (the app binds the connection's key as it opens a chat: ui.get_controller).
folder = tempfile.mkdtemp(); sid = str(uuid.uuid4())
store = SessionStore(folder, sid); state = store.new("claude_subscription")
state["messages"].append({"role": "user", "text": "my key " + KEY}); store.save(state)
with store.path.open("ab") as h: h.write(b'{"seq": 9, "projects": {}}\n')
with newsecond:
    for _ in range(5):
        c = Controller(folder, session_id=sid); c.set_document(("k", 1)); c.api_key = KEY  # ... then a crash
print("A five opens binding the key, each crashing before its first save: %d kept copies, key pieces in them %s, in the chat %d"
      % (len(kept(store.path)), [pieces(p.read_bytes()) for p in kept(store.path)], pieces(store.path.read_bytes())))

# B. A streamed prefix saved before the key was complete (key not yet bound), then the chat became unreadable.
folder = tempfile.mkdtemp()
sid = str(uuid.uuid4()); c = Controller(folder, session_id=sid); c.set_document(("k", 1))
c.emit("delta", {"item": "r", "text": "Echo: " + KEY[:20]}); c._last_save = 0; c.pump_events(); c.save()
c.emit("delta", {"item": "r", "text": KEY[20:] + " ok"}); c.pump_events(); c.save()
path = c.store.path
with path.open("ab") as h: h.write(b'{"seq": 99, "projects": {}}\n')   # e.g. a later build's record, or damage
c = Controller(folder, session_id=sid); c.set_document(("k", 1)); c.api_key = KEY; c.save()
print("B streamed prefix before binding, kept, then bound: chat %d pieces; kept copies %s (held: %s)"
      % (pieces(path.read_bytes()), [pieces(p.read_bytes()) for p in kept(path)], c.store.redact_kept(KEY)))

# C. Rewrites when the app opens a long chat with a key (load, set_document, bind, first save, second save).
src = sys.argv[2]
folder = tempfile.mkdtemp(); sid = os.path.basename(src)[:-5]
os.makedirs(os.path.join(folder, "sessions")); import shutil; shutil.copy(src, os.path.join(folder, "sessions", sid + ".json"))
count = [0]; real = session_journal.rewrite
def counted(*a, **k):
    count[0] += 1; return real(*a, **k)
with patch.object(session_journal, "rewrite", counted):
    t = time.perf_counter(); c = Controller(folder, session_id=sid); c.set_document(("k", 1)); opened = time.perf_counter() - t
    after_open = count[0]
    t = time.perf_counter(); c.api_key = KEY; bound = time.perf_counter() - t
    c.save(); c.save()
print("C open %.0f ms (%d rewrites), bind %.1f ms; rewrites after two saves: %d" % (1e3 * opened, after_open, 1e3 * bound, count[0]))

# D. Main-thread cost of redacting a large kept copy that holds the key, at the open that binds it.
folder = tempfile.mkdtemp(); sid2 = str(uuid.uuid4())
data = open(src, "rb").read()
data = data.replace(b'"text": "', b'"text": "' + KEY.encode() + b' ', 50)
os.makedirs(os.path.join(folder, "sessions"))
p = os.path.join(folder, "sessions", sid2 + ".json")
raw = json.loads(data.split(b"\n", 1)[0]); raw["id"] = sid2
open(p, "wb").write(json.dumps(raw).encode() + b'\n{"seq": 1, "projects": {}}\n')
c = Controller(folder, session_id=sid2); c.set_document(("k", 1))
t = time.perf_counter(); c.api_key = KEY; bound = time.perf_counter() - t
from pathlib import Path
ks = kept(Path(p))
print("D bind with a %.1f MB kept copy holding the key: %.0f ms on the calling thread; kept pieces after: %s"
      % (sum(x.stat().st_size for x in ks) / 1e6, 1e3 * bound, [pieces(x.read_bytes()) for x in kept(Path(p))]))
