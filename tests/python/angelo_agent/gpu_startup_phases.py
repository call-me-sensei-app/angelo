# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``test_blender_gpu_startup_fallback.py``: one phase per window process (see that file).

The script runs after Blender's start-up and before its main loop, while the record of a Vulkan launch says scripts
run; the record is read then, and again once frames have been presented. Checks are recorded, never asserted one by
one, so a receipt lists every one; anything unexpected ends the phase with its traceback in ``<phase>.json``. A
phase that ends its own process (``dies``, ``crashes``) writes its receipt first and dies inside its first frame's
draw: a 3D view's draw callback runs before that frame is presented. ``crashes-script`` and ``crashes-thread`` crash
natively here, while this script runs: on the main thread, and on a thread of their own.
"""

import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

import bpy
import gpu

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
OUT = Path(ARGS[ARGS.index("--fixture-root") + 1])
sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_crash  # noqa: E402  (a real native fault, on every platform)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_waits  # noqa: E402

CONFIG = Path(bpy.utils.user_resource('CONFIG'))
RECORD = CONFIG / "angelo-gpu-startup.txt"
LIMIT = 60.0 * qa_waits.timeout_scale()
MINE = "vulkan starting %d" % os.getpid()
MINE_IN_SCRIPTS = MINE + " scripts"
FAILED = "vulkan failed"
# Set out with Vulkan: a record until the first frame.
WATCHED = ("first", "after-scripts", "again", "held", "after-script-crash")
BESIDE = ("beside", "beside-drawing")                   # Started while another launch holds the record.
ENDS_ITSELF = {"dies": lambda: os._exit(3), "crashes": native_crash.crash}   # Inside the first frame's draw.


def crash_on_another_thread():
    thread = threading.Thread(target=native_crash.crash, name="qa-gpu-startup-crash")
    thread.start()
    thread.join()   # Never returns: the process ends, or waits under its crash dialog.


# While this script runs: on the main thread (the script's own crash), and on another (it may be the GPU's).
CRASHES_IN_ITS_SCRIPT = {"crashes-script": native_crash.crash, "crashes-thread": crash_on_another_thread}
report = {"phase": PHASE, "checks": {}, "observed": {}, "success": False, "app_pid": os.getpid()}
started = [None]
drawing = {"waited": False}


def record():
    try:
        return RECORD.read_text().strip() or None   # An emptied record is no record.
    except OSError:
        return None


def check(name, condition):
    report["checks"][name] = bool(condition)


def system():
    return bpy.context.preferences.system


def observe(moment):
    report["observed"][moment] = {"record": record(), "backend": gpu.platform.backend_type_get(),
                                  "preference": system().gpu_backend,
                                  "preferences_unsaved": bpy.context.preferences.is_dirty}
    return report["observed"][moment]


def write_receipt(error=None):
    report["failed_checks"] = sorted(name for name, ok in report["checks"].items() if not ok)
    report["success"] = error is None and bool(report["checks"]) and not report["failed_checks"]
    if error is not None:
        report.update(error=repr(error), traceback=traceback.format_exc())
    report.update(backend=gpu.platform.backend_type_get(), preference=system().gpu_backend)
    (OUT / (PHASE + ".json")).write_text(json.dumps(report, indent=2))


def finish(error=None):
    write_receipt(error)
    bpy.ops.wm.quit_blender()


def wait_for(path):
    deadline = time.monotonic() + LIMIT
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    return path.is_file()


def in_first_draw():
    """A 3D view's draw callback: runs inside a frame's draw, before the frame is presented."""
    if PHASE in ENDS_ITSELF:
        ENDS_ITSELF[PHASE]()
    if PHASE == "held" and not drawing["waited"]:
        drawing["waited"] = True
        report["observed"]["in the first draw"] = {"record": record()}
        (OUT / "held-drawing").write_text(str(os.getpid()))
        check("the host let the first frame go on", wait_for(OUT / "release-drawing"))


def drawn():
    """Checks once this launch has presented frames."""
    seen = observe("after frames")
    if PHASE in WATCHED:
        check("draws with Vulkan", seen["backend"] == 'VULKAN')
        check("the preference says Vulkan", seen["preference"] == 'VULKAN')
        check("the record is gone once a frame was presented", seen["record"] is None)
        if PHASE == "first":
            bpy.ops.wm.save_userpref()   # The file the host later puts back says Vulkan.
        if PHASE == "held":
            check("the record no longer said scripts ran inside the first draw",
                  report["observed"].get("in the first draw", {}).get("record") == MINE)
    elif PHASE in BESIDE:
        check("draws with Vulkan beside a launch that is still starting", seen["backend"] == 'VULKAN')
        check("the other launch's record is still there", seen["record"] == report["observed"]["before"]["record"])
    elif PHASE == "command-line":
        check("--gpu-backend vulkan is not overridden", seen["backend"] == 'VULKAN')
        check("the record still says the start failed", seen["record"] == FAILED)
    else:
        check("draws with OpenGL", seen["backend"] == 'OPENGL')
        check("the preference says OpenGL", seen["preference"] == 'OPENGL')
        check("the record says the start failed", seen["record"] == FAILED)
    if PHASE in ("fallback", "after-crash", "after-thread-crash"):
        check("the changed preference is unsaved, so the save on exit keeps it", seen["preferences_unsaved"])
    if PHASE == "fallback":
        try:   # For a person to look at: Blender's alert that Vulkan failed. Not a check.
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
                bpy.ops.screen.screenshot(filepath=str(OUT / "fallback-alert.png"))
            report["screenshot"] = str(OUT / "fallback-alert.png")
            report["screenshot_written"] = (OUT / "fallback-alert.png").is_file()
        except Exception as error:
            report["screenshot_error"] = repr(error)
    if PHASE == "stays":
        # A preference file that says Vulkan, read inside the session as an import of earlier settings reads one.
        shutil.copy2(OUT / "userpref-says-vulkan.blend", CONFIG / "userpref.blend")
        bpy.ops.wm.read_userpref()
        read = observe("after reading a file that says Vulkan")
        check("a file that says Vulkan, read inside the session, leaves OpenGL", read["preference"] == 'OPENGL')
        check("and the record still says the start failed", read["record"] == FAILED)
    if PHASE == "imported":
        system().gpu_backend = 'VULKAN'   # The person chooses Vulkan again in Preferences > System.
        chosen = observe("after choosing Vulkan")
        check("choosing Vulkan removes the record at once", chosen["record"] is None)
        check("the preference says Vulkan again", chosen["preference"] == 'VULKAN')
        bpy.ops.wm.save_userpref()


def tick():
    try:
        if started[0] is None:
            started[0] = time.monotonic()
        if PHASE in WATCHED:
            # Its own frames, as the main loop presents them: the record goes with the first one.
            if record() is not None and time.monotonic() - started[0] < LIMIT:
                return 0.1
        else:
            # Frames presented now, so the checks read what a presented frame leaves of the record.
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        drawn()
        finish()
    except Exception as error:
        finish(error)
    return None


def start():
    try:
        before = observe("before")
        if PHASE in WATCHED or PHASE in ENDS_ITSELF or PHASE in CRASHES_IN_ITS_SCRIPT:
            check("the record names this launch and says its scripts run", before["record"] == MINE_IN_SCRIPTS)
        elif PHASE in BESIDE:
            check("the record names another launch that is still starting",
                  (before["record"] or "").startswith("vulkan starting ") and not before["record"].startswith(MINE))
        else:
            check("the record says the start failed", before["record"] == FAILED)
        if PHASE in CRASHES_IN_ITS_SCRIPT:
            write_receipt()   # Nothing runs after the crash.
            CRASHES_IN_ITS_SCRIPT[PHASE]()
            return
        if PHASE in ENDS_ITSELF or PHASE == "held":
            bpy.types.SpaceView3D.draw_handler_add(in_first_draw, (), 'WINDOW', 'POST_PIXEL')
        if PHASE in ENDS_ITSELF:
            write_receipt()   # Nothing runs after its first draw.
            return
        if PHASE == "held":   # Stay before the main loop until the host has run the launch beside this one.
            (OUT / "held-waiting").write_text(str(os.getpid()))
            check("the host let this launch go on", wait_for(OUT / "release-held"))
    except Exception as error:
        finish(error)
        return
    bpy.app.timers.register(qa_boot.gated(tick), first_interval=0.2)
