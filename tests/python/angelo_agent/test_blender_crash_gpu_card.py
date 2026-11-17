# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The crash dialog after a crash in the graphics driver, in a real window (``crash_gpu``; updates_and_reports.rst,
"A crash in the graphics driver").

A crash log whose crashed thread runs through ``vulkan-1.dll`` is left where Angelo's crash handler writes, and
Angelo's own scan finds it. Where this launch draws with Vulkan (Windows), the dialog must say why and offer
**Restart with OpenGL**; a real pointer click must answer the crash, choose OpenGL in the saved preferences and ask
for a relaunch (recorded here, so the run ends normally). Where it draws with Metal or OpenGL, the same crash shows
the dialog without the offer: the control. Screenshots either way. Run in a window: ``--factory-startup`` without
``--background``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Run in a window"
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import crash_fixtures as cfx  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-crash-gpu-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "phase": "setup", "screenshots": []}
bpy.context.preferences.view.show_splash = False
LOG = cfx.WINDOWS.replace("nvoglv64.dll        :0x00007FFA11223344  Symbols not available",
                          "vulkan-1.dll        :0x00007FFA11223344  vkQueueSubmit")
relaunches = []


def check(name, passed):
    state["checks"][name] = bool(passed)


def write():
    (OUT / "crash-gpu-card.json").write_text(json.dumps(state, indent=2))


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def labels(plan):
    return [op[2] for op in plan.ops if op[0] == "button"]


def click(action):
    """A real pointer click on the card's own control (``card_host``'s hit geometry)."""
    from angelo_agent import canvas_rtl, card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in reversed(active.plan.hits) if a == action)
    x = int(region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width))
    y = int(region.y + rect[1] + rect[3] / 2)
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def steps():
    from angelo_agent import card_host, crash_gpu, crash_ui
    backend = (crash_ui.gpu_info() or ("", "", "", ""))[3]
    state["backend"] = backend
    planted = Path(crash_ui.temp_base()) / "angelo.crash.txt"
    planted.write_text(LOG, encoding="utf-8")
    scans = crash_ui.scans
    crash_ui.start_scan()
    for _ in range(100):   # As app_notices.tick does, which stays off while automation runs without the chat.
        if crash_ui.poll() or crash_ui.scans > scans:
            break
        yield 0.2
    planted.unlink()   # Found and recorded: the runner must not take it for a crash of this run.
    state["cards_before"] = []
    for _ in range(100):   # Cards ahead of it (Angelo's start panel) are closed as a person would close them.
        if card_host.is_active("crash") and card_host._active.plan is not None:
            break
        card = card_host.active_card()
        if card is not None and card.kind != "crash":
            state["cards_before"].append(card.kind)
            card_host.close("close")
        card_host.pump(lambda: True)   # As app_notices.tick does.
        yield 0.2
    check("crash_dialog_shown", card_host.is_active("crash"))
    if not card_host.is_active("crash"):
        return
    yield 0.6
    plan = card_host._active.plan
    texts = " ".join(str(op[1]) for op in plan.ops if op[0] in {"text", "mono"})
    offered = "Restart with OpenGL" in labels(plan)
    state["labels"] = labels(plan)
    capture("crash-dialog.png")
    if backend != "VULKAN":   # The control: Metal or OpenGL never offer it.
        check("no_offer_without_vulkan", not offered)
        return
    check("offers_restart_with_opengl", offered)
    check("says_it_stopped_in_the_driver_on_vulkan", "Vulkan" in texts)
    crash_gpu.relaunch = lambda bpy_module, *args, **kwargs: relaunches.append(True)   # Recorded: the run goes on.
    before = bpy.context.preferences.system.gpu_backend
    click("opengl")
    yield 1.0
    prefs = bpy.context.preferences
    check("the_click_answered_the_crash", not card_host.is_active("crash"))
    check("opengl_chosen", prefs.system.gpu_backend == 'OPENGL')
    check("preferences_saved", not prefs.is_dirty)
    check("a_relaunch_was_asked_for", relaunches == [True])
    capture("after-restart-choice.png")
    prefs.system.gpu_backend = before   # This isolated profile's own choice back, saved.
    bpy.ops.wm.save_userpref()


def run():
    generator = steps()

    def tick():
        try:
            return next(generator)
        except StopIteration:
            state["phase"] = "complete"
            state["success"] = bool(state["checks"]) and all(state["checks"].values())
        except Exception:
            state["error"] = traceback.format_exc()
        write()
        print("ANGELO_CRASH_GPU_CARD", json.dumps({"success": state["success"], "backend": state.get("backend")}),
              flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(qa_boot.gated(tick), first_interval=2)


run()
