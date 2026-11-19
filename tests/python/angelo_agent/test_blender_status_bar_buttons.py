# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A GUI proof finds a button the status bar draws, as it finds a header's (``_angelo_jobs.region_buttons``,
``qa_input.region_button``).

The status bar and the top bar are a window's global areas, which no screen holds; the button walk read only the
screen's areas, so every proof of the status bar's "<app> is waiting for your approval" notice fell back to opening
the chat directly and never pressed the notice (2.0.1 candidate proofs, 3b2b492bb4a). Here the notice is shown for a
waiting app (``external_ui.waiting``, the one input it is drawn from) and must be listed with its drawn place, in the
window's bottom bar. Nothing is pressed: the session it names is a stand-in.

Factory-startup GUI with event simulation, isolated state; no provider, account, network or paid call. Run through
``run_native_suites.py --gui --include test_blender_status_bar_buttons``; ``status-bar-buttons.json`` has
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-status-bar-buttons-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "accounts"))
REPORT = OUT / "status-bar-buttons.json"
state = {"phase": "startup", "checks": {}, "details": {}}
started = time.monotonic()   # From the gate's first open call on (``tick`` resets it).


def finish():
    state["success"] = state["phase"] == "complete" and bool(state["checks"]) and all(state["checks"].values())
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STATUS_BAR_BUTTONS", json.dumps(state, default=str), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    global started
    try:
        if state["phase"] == "startup":
            started = time.monotonic()
        from angelo_agent import external_ui
        if time.monotonic() - started > 60:   # Whether it was drawn, for the report: the walk, or the notice.
            state["details"]["shown"] = dict(external_ui._shown)
            raise AssertionError("the notice was never listed in 60 s")
        import qa_input
        window = bpy.context.window_manager.windows[0]
        if state["phase"] == "startup":
            external_ui.waiting = lambda *args, **kwargs: [("claude-code", "status-bar-probe")]   # An app waits.
            state["phase"] = "shown"
            return 0.3
        rect = qa_input.region_button(window, "angelo.external_app_open")
        if rect is None:
            return 0.2
        state["details"]["notice"] = rect
        state["checks"]["the_status_bar_notice_is_listed"] = rect[2] > 0 and rect[3] > 0
        # The status bar is the window's bottom bar: the notice lies in its lowest strip, never in an editor.
        bottom = min((area.y for area in window.screen.areas), default=0)
        state["checks"]["it_lies_in_the_bottom_bar"] = rect[1] + rect[3] <= bottom + 1
        from _bpy import _angelo_jobs
        state["details"]["blocks"] = sorted({block for pointer, block, *_ in _angelo_jobs.region_buttons()
                                             if pointer == window.as_pointer()})[:40]
        state["phase"] = "complete"
        finish()
        return None
    except Exception as error:   # noqa: BLE001 - the report says why.
        state.update(error=repr(error), traceback=traceback.format_exc())
        finish()
        return None


addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.app.timers.register(qa_boot.gated(tick), first_interval=0.5)
