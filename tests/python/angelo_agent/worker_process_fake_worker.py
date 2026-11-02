# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in worker Blender for ``test_native_worker_process_ended``: a real process on the real channel.

Usage: ``worker_process_fake_worker.py ADDONS CHANNEL MODE``. It says ready, then answers each request ``ok``
until one asks it to ``die``: it exits at once, mid-call, without answering. ``MODE`` ``linger``: before exiting it
starts a process that inherits its end of the connection and outlives it, so the connection stays open (a script's
``os.system("... &")``). ``stubborn``: it refuses ``chat.close`` and keeps running after its connection closes, so
only a kill ends it (a wedged worker). ``held_crash PRIVATE``: a SIGSEGV runs a handler that, as Blender's crash
handler does, first writes ``part.crash.txt`` to its Blender temporary folder (``<private>/t``, which ``worker_main``
sets), then stays (its full record, or macOS's ReportCrash holding the exiting process) until it is killed.
"""

import os
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
from angelo_agent import worker_channel  # noqa: E402

if sys.argv[3] == "held_crash":
    import signal
    from pathlib import Path

    folder = Path(sys.argv[4]) / "t"   # ``worker_crash_evidence.blender_temp``, made at start as worker_main does.
    folder.mkdir(exist_ok=True)

    def crashing(signum, frame):
        (folder / "part.crash.txt").write_text("# Signal 11 (SIGSEGV)\n# backtrace\n", encoding="utf-8")   # First.
        while True:   # Held while crashing: only a kill ends it now.
            os.system("sleep 60")
    signal.signal(signal.SIGSEGV, crashing)

endpoint = worker_channel.connect(sys.argv[2])
endpoint.send({"seq": 0, "ready": {"ok": True, "pid": os.getpid()}})
while True:
    try:
        frame = endpoint.receive()
    except Exception:  # noqa: BLE001 - its connection closed.
        if sys.argv[3] != "stubborn":
            raise
        while True:   # Wedged: it never exits by itself.
            os.system("sleep 60")
    request = frame.get("request") or {}
    if sys.argv[3] == "stubborn" and request.get("op") == "chat.close":
        endpoint.send({"seq": frame["seq"], "response": {"ok": False, "error": "stuck"}})
        continue
    if request.get("op") == "die":
        if sys.argv[3] == "linger":
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             pass_fds=(int(sys.argv[2][3:]),))
        os._exit(7)
    endpoint.send({"seq": frame["seq"], "response": {"ok": True}})
