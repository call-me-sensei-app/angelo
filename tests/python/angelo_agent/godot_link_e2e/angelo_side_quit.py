# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first Angelo of test_blender_godot_link_e2e.py's restart run, inside ``Angelo --background --python``: it
links, then quits the way Angelo's exit does (``engine_link._exit_pre``) once the Godot driver writes
``angelo-quit-please``. A second, ordinary Angelo (``angelo_side.py``) then answers the driver's next request."""

import os
import sys
import time
from pathlib import Path

import addon_utils
OUT = Path(os.environ["ANGELO_QA_DIR"])
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import engine_link
engine_link.register()
engine_link.start()
started = time.monotonic()
while time.monotonic() - started < 120:
    engine_link._tick()
    if engine_link.pending() is not None:
        engine_link.accept(None)
    if (OUT / "angelo-quit-please").exists() and engine_link.session() is not None:
        time.sleep(0.3)
        engine_link._tick()
        engine_link._exit_pre()   # What quitting Angelo runs.
        print("stopped like a quit")
        break
    time.sleep(0.03)
sys.exit(0)
