# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in worker Blender for ``test_native_worker_process_ended``: a real process on the real channel.

Usage: ``worker_process_fake_worker.py ADDONS CHANNEL MODE``. It says ready, then answers each request ``ok``
until one asks it to ``die``: it exits at once, mid-call, without answering. ``MODE`` ``linger``: before exiting it
starts a process that inherits its end of the connection and outlives it, so the connection stays open (a script's
``os.system("... &")``).
"""

import os
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
from angelo_agent import worker_channel  # noqa: E402

endpoint = worker_channel.connect(sys.argv[2])
endpoint.send({"seq": 0, "ready": {"ok": True, "pid": os.getpid()}})
while True:
    frame = endpoint.receive()
    request = frame.get("request") or {}
    if request.get("op") == "die":
        if sys.argv[3] == "linger":
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             pass_fds=(int(sys.argv[2][3:]),))
        os._exit(7)
    endpoint.send({"seq": frame["seq"], "response": {"ok": True}})
