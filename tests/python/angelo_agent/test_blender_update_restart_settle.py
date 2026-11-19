# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restart to Update's hand-over keeps the document as Blender settles it, with Blender's real save timing (fails
first: the Windows end-to-end update 1.0.11 -> 2.0.0, 2026-11-18).

``bpy.data.is_dirty`` reads True until Blender handles a save's notifier, after the save call. The hand-over records
the document's stamp and the installer's readiness compares it again; a stamp taken right after the save no longer
matched, so every Save and Restart stood the installer down ("The update could not start"). Host tests replaced the
stamp with a constant and hid it. Here, in the running app (default Director startup), each way into the hand-over
records the stamp ``launch`` is given and the document's stamp half a second later:

* Save on a modified, titled file: the hand-over starts once, its stamp reads the file saved, and it still matches;
* Don't Save on a modified file: the hand-over starts once and its stamp (modified) still matches;
* an edit right after the save: the hand-over never starts, and the person is told Angelo did not restart;
* an edit while a clean file settles, and an edit after Don't Save (the maintainer's audit of 2.0.1, P1: the edit
  became the stamp the installer kept, so it was treated as accepted): the hand-over never starts, and the person is
  asked again (the save card).

``launch`` itself is a recorder: no installer runs and Angelo does not quit. Run through
``run_native_suites.py --gui --include test_blender_update_restart_settle``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-update-restart-settle-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The default Director startup, as people get it.
os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "update-restart-settle.json"
DOCUMENT = OUT / "document" / "castle.blend"
LATER_S = 0.5   # How long after the hand-over its stamp is read again: many event-loop passes.
state = {"phase": "boot", "success": False, "checks": {}, "details": {}}
started = time.monotonic()
launched, notices = [], []
CASES = ("clean_then_edit", "save", "dont_save", "edit_after_save", "dont_save_then_edit")   # Clean first: just saved.
EDITED = ("edit_after_save", "clean_then_edit", "dont_save_then_edit")   # An edit lands while it settles.


def check(name, ok, detail=None):
    state["checks"][name] = bool(ok)
    if detail is not None:
        state["details"][name] = detail


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def window():
    return bpy.context.window_manager.windows[0]


def in_view():
    """The context the card's button runs in: the window and its 3D view."""
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=window(), area=area, region=region)


def restart():
    from angelo_agent import update_restart
    return update_restart


def stamp():
    return json.loads(json.dumps(restart()._document_stamp()))   # Plain lists: compared and reported as JSON.


def modify():
    """A real edit, as the person makes one: an undo step, at which Blender marks the file modified
    (``ED_undo_push``: ``WM_file_tag_modified``). An operator run from a timer pushes none of its own."""
    with in_view():
        bpy.ops.mesh.primitive_cube_add(location=(len(bpy.data.objects) * 2.5, 0, 0))
        bpy.ops.ed.undo_push(message="Add Cube")


def finish(error=None):
    if error is not None:
        state["error"] = "".join(traceback.format_exception(error))
    else:
        state["phase"] = "complete"
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_UPDATE_RESTART_SETTLE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def install_recorders():
    module = restart()

    def record_launch(offer, ready):
        launched.append({"case": state["case"], "stamp": stamp(), "at": time.monotonic()})
    module.launch = record_launch   # Read by the hand-over at call time: no installer, no quit.
    module._notice = lambda title, body, *args, **kwargs: notices.append({"case": state["case"], "title": title})


def begin(case):
    """Make the file modified, then answer the save card as the person does; a clean file goes straight in, as
    ``begin`` sends one, and is edited while it settles."""
    state["case"], state["phase"], state["since"] = case, "dirty", time.monotonic()
    if case == "clean_then_edit":
        check("clean_then_edit_starts_clean", not bpy.data.is_dirty)
        with in_view():
            restart()._launch_settled(SimpleNamespace(), SimpleNamespace(), saved=False)
        bpy.app.timers.register(lambda: modify() and None, first_interval=0.05)   # While it settles.
        state["phase"], state["since"] = "waiting", time.monotonic()
        return
    modify()


def answer(case):
    choice = "dont_save" if case in ("dont_save", "dont_save_then_edit") else "save"
    with in_view():
        restart()._after_prompt(choice, SimpleNamespace(), SimpleNamespace())
    if case in ("edit_after_save", "dont_save_then_edit"):
        bpy.app.timers.register(lambda: modify() and None, first_interval=0.05)   # After the save's notifier.
    state["phase"], state["since"] = "waiting", time.monotonic()


def judge(case):
    mine = [entry for entry in launched if entry["case"] == case]
    titles = [entry["title"] for entry in notices if entry["case"] == case]
    if case == "edit_after_save":
        check("edit_after_save_never_hands_over", not mine, mine)
        check("edit_after_save_says_angelo_did_not_restart", "Angelo did not restart" in titles, titles)
        return
    if case in ("clean_then_edit", "dont_save_then_edit"):
        from angelo_agent import card_host
        asked = card_host.is_active("update_save") or bool(card_host.queued("update_save"))
        check("%s_never_hands_over" % case, not mine, mine)
        check("%s_asks_again" % case, asked, {"active": getattr(card_host.active_card(), "kind", None)})
        card_host.withdraw("update_save")
        if card_host.is_active("update_save"):
            card_host.close("cancel")
        return
    check("%s_hands_over_once" % case, len(mine) == 1, mine)
    if len(mine) == 1:
        later = stamp()
        check("%s_stamp_still_matches" % case, mine[0]["stamp"] == later, {"at_launch": mine[0]["stamp"], "later": later})
        if case == "save":
            check("save_stamp_reads_the_file_saved", mine[0]["stamp"][1] is False, mine[0]["stamp"])
            check("save_wrote_the_file", DOCUMENT.stat().st_mtime > state["saved_at"] - 1, str(DOCUMENT))
    check("%s_no_notice" % case, not titles, titles)


def tick():
    try:
        assert time.monotonic() - started < 240, "Update restart settle fixture timed out in " + state["phase"]
        from angelo_agent import director
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
            with in_view():
                bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))
            install_recorders()
            state["cases"] = list(CASES)
            state["phase"], state["since"] = "saved", time.monotonic()
            return .1
        if state["phase"] == "saved":
            # The same timing this suite is about: Blender marks the save at its next notifier pass, which would
            # undo an edit made before it, so the first edit waits until the file reads saved.
            if bpy.data.is_dirty:
                assert time.monotonic() - state["since"] < 10, "the first save never read as saved"
                return .1
            begin(state["cases"].pop(0))
            return .3
        case = state["case"]
        if state["phase"] == "dirty":
            if not bpy.data.is_dirty:
                assert time.monotonic() - state["since"] < 10, "the edit never marked the file modified"
                return .1
            state["saved_at"] = time.time()
            answer(case)
            return .1
        if state["phase"] == "waiting":
            settle = getattr(__import__("angelo_agent.update_restart_handoff", fromlist=["SETTLE_S"]), "SETTLE_S", 5.0)
            mine = [entry for entry in launched if entry["case"] == case]
            waited = time.monotonic() - state["since"]
            if case not in EDITED and mine and time.monotonic() - mine[0]["at"] < LATER_S:
                return .1   # Read the stamp again only after many event-loop passes.
            if not mine and waited < (settle + 1.5 if case in EDITED else 8):
                return .1
            judge(case)
            if state["cases"]:
                begin(state["cases"].pop(0))
                return .3
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
