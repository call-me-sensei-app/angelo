# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runs inside the older app of the end-to-end update (``update_e2e_macos.py run``); never in a shipped app.

It trusts the run's throwaway feed key, sends the downloads to the run's loopback server (the feed itself names
downloads.angelo.studio, as a real one does), saves a file, leaves an unsaved change in it, then does what a
person does with real window events: waits for "Angelo <version> is ready", presses Restart to Update, sees
"Save changes before restarting?" and presses Save and Restart. The app then quits through the update; the
harness checks what follows. Every step is written to ``$ANGELO_QA_DIR/update-e2e-driver.json`` at once, so the
report survives the quit.
"""

import base64
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_crash_gui as gui  # noqa: E402

QA = Path(os.environ["ANGELO_QA_DIR"])
REPORT = QA / "update-e2e-driver.json"
state = {"phase": "start", "checks": {}, "events": [], "cachedir": bpy.app.cachedir}
started = time.monotonic()


def note(text):
    state["events"].append([round(time.monotonic() - started, 1), text])
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def check(name, value):
    state["checks"][name] = bool(value)
    note(("PASS " if value else "FAIL ") + name)
    return value


def _has_zone_identifier(path):
    """Whether ``path`` has the NTFS stream a browser or other downloader writes: the Mark of the Web."""
    try:
        with open(str(path) + ":Zone.Identifier", "rb"):
            return True
    except FileNotFoundError:
        return False


def prepare():
    from angelo_agent import update_download, update_feed
    key_id, key = os.environ["ANGELO_E2E_KEY_ID"], base64.b64decode(os.environ["ANGELO_E2E_KEY"])
    update_feed.load_keys = lambda path=update_feed.KEYS_FILE: {key_id: key}   # This run's key only.
    base, original = os.environ["ANGELO_E2E_DOWNLOADS"], update_download.fetch

    def fetch(expected, target, **options):
        url = expected.url.replace("https://downloads.angelo.studio", base, 1)
        return original(update_download.Expected(url, expected.sha256, expected.size), target, **options)
    update_download.fetch = fetch
    note("prepared: feed key trusted, downloads from %s" % base)


def snapshot():
    """What the update machinery is doing, for the report while the driver waits."""
    from angelo_agent import card_host, update_ready, update_ui
    job = update_ready._job
    active = card_host.active_card()
    return {"active": active and active.kind, "plan": bool(card_host._active and card_host._active.plan),
            "queued": [c.kind for c in card_host.queued()], "check": update_ui.last and update_ui.last.kind,
            "offer": update_ui.offer and update_ui.offer.release.version,
            "job": job and {k: job[k] for k in ("version", "phase", "done", "total")},
            "job_result": job and repr(job["result"]), "ready": list(update_ready._ready),
            "failed": {k: v[:2] for k, v in update_ready._failed.items()}, "wait": update_ready._wait,
            "support": update_ready._support_reason}


def waiting(name, condition, limit):
    """``gui.until`` that also records the update's state every five seconds."""
    began, last = time.monotonic(), 0.0
    while not condition():
        assert time.monotonic() - began < limit, "Timed out waiting for " + name
        if time.monotonic() - last > 5:
            last = time.monotonic()
            state["last_snapshot"] = snapshot()
            note("waiting for %s" % name)
        yield


def view3d():
    window = gui.window()
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def scenario():
    from angelo_agent import card_host, update_ready
    path = os.environ["ANGELO_E2E_FILE"]
    bpy.ops.wm.save_as_mainfile(filepath=path)
    # Blender marks the file saved when it handles the save's notifier, after this callback: an edit made in the
    # same callback would be marked saved with it. A person's edit comes later, so the driver waits too.
    yield from gui.settle(4)
    check("file_saved_first", bpy.data.filepath == path and not bpy.data.is_dirty)
    window, area, region = view3d()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(1, 2, 3))
    bpy.context.view_layer.objects.active.name = "Unsaved change"
    bpy.ops.ed.undo_push(message="Unsaved change")   # As the person's own edit would: the file is now modified.
    yield from gui.settle(4)
    check("an_unsaved_change", bpy.data.is_dirty)
    note("waiting for the update to be ready")
    yield from waiting("the ready card", lambda: gui.active("update")
                       and update_ready.phase(card_host.active_card().offer) == "ready", limit=1500)
    yield from gui.settle(3)
    gui.park_pointer()
    yield from gui.settle(2)
    gui.capture("e2e-1-ready.png")
    state["wait_reason"] = update_ready.wait_reason()
    if sys.platform == "win32":
        # The installer Angelo downloaded itself carries no Mark of the Web (a browser's Zone.Identifier stream), the
        # mark SmartScreen keys its check on: with it, an unsigned build's update would ask again. Recorded here, with
        # the install that follows proving no prompt stopped it (nothing in this run could answer one).
        staged = [Path(ready.payload) for ready in update_ready._ready.values()]
        state["staged_installers"] = [str(path) for path in staged]
        check("staged_installer_has_no_mark_of_the_web",
              staged and all(path.is_file() and not _has_zone_identifier(path) for path in staged))
    if not check("ready_card_offers_restart", "Restart to Update" in gui.labels()
                 and gui.button_state("Restart to Update")[3]):
        raise AssertionError("Restart to Update is disabled: " + state["wait_reason"])
    check("ready_card_says_ready", "is ready" in gui.text())
    check("still_unsaved_when_restart_is_pressed", bpy.data.is_dirty)
    gui.click("restart")
    yield from gui.until("the save prompt", lambda: gui.active("update_save"), limit=30)
    yield from gui.settle(2)
    gui.capture("e2e-2-save-prompt.png")
    check("save_prompt_names_the_file", Path(path).name in gui.text())
    check("save_prompt_offers_three_choices", gui.labels() == ["Cancel", "Don't Save", "Save and Restart"])
    note("pressing Save and Restart")
    state["phase"] = "restarting"
    gui.click("save")
    yield from gui.until("the file saved", lambda: not bpy.data.is_dirty, limit=30)
    check("saved_before_restart", bpy.data.objects.get("Unsaved change") is not None and not bpy.data.is_dirty)
    from angelo_agent import input_policy, work_guard
    state["after_save"] = {"reasons": work_guard.reasons(), "modal": [
        input_policy.operator_identity(op) for op in gui.window().modal_operators]}
    note("after save: %s" % state["after_save"])
    yield from gui.until("the restart card", lambda: gui.active("update") and update_ready.phase(
        card_host.active_card().offer) == "restarting", limit=30)
    gui.capture("e2e-3-restarting.png")
    check("restarting_card_shown", True)
    note("waiting for Angelo to quit")
    while True:   # The update quits the app; the harness takes over.
        yield


def tick(generator=[]):
    try:
        if not generator:
            prepare()
            generator.append(scenario())
            return 1.0
        assert time.monotonic() - started < 1800, "The driver timed out in " + state["phase"]
        next(generator[0])
        return 0.25
    except StopIteration:
        return None
    except Exception as error:
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        note("driver failed")
        return None


bpy.app.timers.register(tick, first_interval=2.0, persistent=True)
