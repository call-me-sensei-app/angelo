# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual measurement: what opening a long chat on an API-key connection costs, in
``ui.get_controller``'s order (load, set_document and its save, the key's binding), then the next save.

    python open_cost.py <angelo checkout or extracted add-on tree> <a chat file named <uuid>.json>

Measured with host Python on a 2.5 MB chat of 1,268 rows from a 500-edit replay: in a build whose binding
invalidates the writer, the next save is a second whole rewrite (about 15 ms), where a build without that
invalidation appends (0.3 ms); the binding itself scans every row (about 15 ms) in both.
"""

import os
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, sys.argv[1] + "/scripts/addons_core")
from angelo_agent import session_journal  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402

KEY = "sk-test-fake-0123456789abcdef"  # Not a credential.
source = sys.argv[2]
rows = []
for trial in range(5):
    folder, chat = tempfile.mkdtemp(), os.path.basename(source)[:-5]
    os.makedirs(os.path.join(folder, "sessions"))
    shutil.copy(source, os.path.join(folder, "sessions", chat + ".json"))
    rewrites = []
    real = session_journal.rewrite
    with patch.object(session_journal, "rewrite", lambda *a, **k: rewrites.append(1) or real(*a, **k)):
        start = time.perf_counter()
        controller = Controller(folder, session_id=chat)
        controller.set_document(("open-cost", 1))
        opened = time.perf_counter()
        controller.api_key = KEY
        bound = time.perf_counter()
        controller.emit("message", {"role": "user", "text": "One more change"})
        controller._last_save = 0
        controller.pump_events()
        saved = time.perf_counter()
    rows.append((1e3 * (opened - start), 1e3 * (bound - opened), 1e3 * (saved - bound), len(rewrites),
                 controller.store.last_write))
    controller.close()
for row in rows:
    print("open %.0f ms, bind %.1f ms, next save %.1f ms; rewrites %d; next save was a%s %s"
          % (row[0], row[1], row[2], row[3], "n" if row[4] == "append" else "", row[4]))
