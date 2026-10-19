# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual repro: an earlier build's own SessionStore replaces a chat that holds records.

    python earlier_build.py <angelo checkout> <earlier build's scripts/addons_core>

e.g. <build folder>/bin/Angelo.app/Contents/Resources/5.2/scripts/addons_core. This build writes a chat
with five requests and mutation IDs (records, as any chat the app left without closing); the earlier build
opens and saves it (closing the chat or quitting saves); this build then reloads it. Measured with
the previous build: the earlier build loads 1 row with its ledger closed, and afterwards no row and no mutation ID
survive.
"""

import subprocess
import sys
import tempfile
import uuid

checkout, earlier = sys.argv[1], sys.argv[2]
sys.path.insert(0, checkout + "/scripts/addons_core")
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

with tempfile.TemporaryDirectory() as folder:
    store = SessionStore(folder, str(uuid.uuid4()))
    state = store.new("claude_subscription")
    store.save(state)
    for index in range(5):
        state["messages"].append({"role": "user", "text": "Request %d" % index})
        reserve_invocation(state, state["id"] + "/c%d" % index, "%064x" % index, "doc")
        store.save(state)
        store.compact_if_due(state)  # A turn's end.
    print("this build: %d rows, %d IDs, last write %s" % (len(state["messages"]), len(state["invocations"]["entries"]),
                                                         store.last_write))
    code = ("import sys; sys.path.insert(0, %r)\nfrom angelo_agent.sessions import SessionStore\n"
            "s = SessionStore(%r, %r); st = s.load()\n"
            "print('earlier build: loaded %%d rows, ledger closed %%s' %% (len(st['messages']), st['invocations']['closed']))\n"
            "s.save(st)") % (earlier, folder, state["id"])
    print(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip())
    again = SessionStore(folder, state["id"]).load()
    print("this build again: rows %r, %d IDs" % ([m.get("text", "")[:40] for m in again["messages"]],
                                                 len(again["invocations"]["entries"])))
