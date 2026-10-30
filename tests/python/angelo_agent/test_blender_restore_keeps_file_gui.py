# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The window half of "the document after a Restore": title bar, the Save question, the close dialog, the update card.

``test_blender_restore_keeps_file`` proves the rules in the background; what needs a window is proven here, in the
compiled app's default layout (Director), with real native Restores and simulated key presses:

1. after Restore the title bar names the person's file, never ``recovered-<uuid>.blend``;
2. File > Save shows the question popup and writes nothing until its Save is pressed; Save then writes the file;
3. the close-file dialog (File > New over a restored document) saves the person's file with its Save;
4. Restart to Update's save card: Save and Restart asks the same question, and the restart continues after it;
5. the quit prompt's Save writes the person's file as Angelo quits (checked after Python ends, in ``atexit``).

GUI ``--factory-startup --enable-event-simulate``; private state, accounts and temp folders; no provider, account,
network or installer (the restart's launch is a stand-in). Check ``restore-keeps-file-gui.json`` for ``success`` and
``phase == "complete"``; the screenshots beside it are the vision half.
"""

import atexit
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and "--enable-event-simulate" in sys.argv and not bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-restore-keeps-file-gui"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot and macos_window, beside this suite.
import qa_boot  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
(OUT / "runtime-temp").mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / "runtime-temp")

REPORT_PATH = OUT / "restore-keeps-file-gui.json"
report = {"checks": {}, "facts": {}, "phase": "boot", "success": False, "binary": bpy.app.binary_path}
state = {"phase": "boot", "started": time.monotonic(), "waited": 0.0}
STOOL = OUT / "project" / "stool.blend"
TIMEOUT = 240.0


def check(name, condition):
    report["checks"][name] = bool(condition)


def native():
    import _bpy
    return _bpy._angelo_checkpoint


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def window():
    return bpy.context.window_manager.windows[0]


def write_report(phase=None, error=None):
    if phase:
        report["phase"] = phase
    if error:
        report["error"] = error
    report["success"] = (report["phase"] == "complete" and bool(report["checks"]) and all(report["checks"].values())
                         and "error" not in report)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")


def screenshot(name):
    win = window()
    area = max(win.screen.areas, key=lambda item: item.width * item.height)
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    report["facts"].setdefault("screenshots", []).append(name)


def titles():
    """The app's visible window titles (AppKit), and a picture of the main window's frame with its title bar."""
    if sys.platform != "darwin":
        return None
    import macos_window
    found = [(handle, title) for handle, title, visible in macos_window.windows() if visible and title]
    return found


def frame(name):
    found = titles()
    if found:
        import macos_window
        macos_window.capture_frame(found[0][0], OUT / name)
        report["facts"].setdefault("screenshots", []).append(name)


def press(key="RET"):
    win = window()
    win.event_simulate(type=key, value="PRESS")
    win.event_simulate(type=key, value="RELEASE")


def invoke(operator, **properties):
    win = window()
    area = max(win.screen.areas, key=lambda item: item.width * item.height)
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        return operator("INVOKE_DEFAULT", **properties)


def make_project():
    STOOL.parent.mkdir(parents=True, exist_ok=True)
    win = window()
    paths = bpy.context.preferences.filepaths
    preview, paths.file_preview_type = paths.file_preview_type, 'NONE'   # A camera thumbnail drawn from a timer
    try:                                                                   # binds no framebuffer on Metal (setup only).
        with bpy.context.temp_override(window=win):
            bpy.ops.wm.save_as_mainfile(filepath=str(STOOL))   # The default scene, saved as the person's file.
            bpy.ops.wm.open_mainfile(filepath=str(STOOL), load_ui=False)
    finally:
        paths.file_preview_type = preview


def request_and_restore(label, name):
    """A chat request with a checkpoint, the agent's edit (a sphere), then a real native Restore of that request."""
    controller = ui.get_controller()
    point = ui.capture_checkpoint(controller)
    controller.state["messages"].append({"role": "user", "text": "Add a sphere", "checkpoint": point["id"],
                                         "checkpoint_scope": "before_request"})
    win = window()
    with bpy.context.temp_override(window=win):
        bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 1))
        bpy.context.active_object.name = name
        saved = digest(STOOL)
        before = len(controller.state["messages"])
        outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=point["id"])
    notice = next((item for item in controller.state["messages"][before:]
                   if item.get("restore_source_checkpoint") == point["id"]), {})
    check(label + "_restore_finished", outcome == {"FINISHED"} and notice.get("restore_state") == "complete")
    check(label + "_keeps_the_persons_file", bpy.data.filepath == str(STOOL) and name not in bpy.data.objects)
    check(label + "_file_untouched_and_question_open", digest(STOOL) == saved and native().restored_save_pending())
    return saved


def title_names_the_file(label):
    found = titles()
    if found is None:
        return
    names = [title for _handle, title in found]
    report["facts"][label + "_titles"] = names
    check(label + "_title_names_the_persons_file", any("stool.blend" in title for title in names))
    check(label + "_title_never_names_the_working_copy", not any("recovered-" in title for title in names))


def tick():
    try:
        return step()
    except Exception:
        write_report(error=traceback.format_exc())
        bpy.ops.wm.quit_blender()
        return None


def wait(seconds):
    state["until"] = time.monotonic() + seconds


def step():
    phase = state["phase"]
    if time.monotonic() - state["started"] > TIMEOUT:
        write_report(error="timed out in phase %s (%s)" % (phase, qa_boot.waiting_on()))
        bpy.ops.wm.quit_blender()
        return None
    if time.monotonic() < state.get("until", 0):
        return 0.1
    if state.pop("settle", False) and not qa_boot.started():   # A load rebuilds Angelo's tabs: act once settled.
        state["settle"] = True
        return 0.2
    report["phase"] = phase

    if phase == "boot":
        if not qa_boot.started():
            return 0.2
        with bpy.context.temp_override(window=window()):
            bpy.ops.angelo.workspace_mode(mode="DIRECTOR")   # The default view a person sees.
        state["phase"] = "project"
        state["settle"] = True
        wait(0.5)
    elif phase == "project":
        from angelo_agent import director
        report["facts"]["mode"] = director.preferred_mode()
        report["facts"]["layout"] = [area.type for area in window().screen.areas]
        check("runs_in_director", director.preferred_mode() == "DIRECTOR")
        make_project()
        state["phase"] = "restore"
        state["settle"] = True
        wait(0.5)
    elif phase == "restore":
        state["saved"] = request_and_restore("first", "AgentSphere")
        state["phase"] = "title"
        state["settle"] = True
        wait(0.5)
    elif phase == "title":
        title_names_the_file("after_restore")
        screenshot("01-after-restore-director.png")
        frame("01-after-restore-title-bar.png")
        invoke(bpy.ops.wm.save_mainfile)   # File > Save: the question.
        state["phase"] = "question"
        wait(0.8)
    elif phase == "question":
        screenshot("02-save-question.png")
        check("question_writes_nothing_while_open", digest(STOOL) == state["saved"]
              and native().restored_save_pending())
        press("RET")   # The question's Save (its default button).
        state["phase"] = "question_saved"
        wait(1.0)
    elif phase == "question_saved":
        check("question_save_writes_the_persons_file", digest(STOOL) != state["saved"]
              and bpy.data.filepath == str(STOOL) and not native().restored_save_pending() and not bpy.data.is_dirty)
        check("question_save_never_wrote_a_recovery_name", "recovered-" not in bpy.data.filepath)
        title_names_the_file("after_save")
        screenshot("03-after-question-save.png")
        state["saved"] = request_and_restore("second", "AgentCone")
        state["phase"] = "close_open"
        state["settle"] = True
        wait(0.5)
    elif phase == "close_open":
        bpy.data.objects[0].location.z += 0.5   # An unsaved change, so the close dialog asks.
        invoke(bpy.ops.wm.read_homefile)   # File > New: the close-file dialog.
        state["phase"] = "close_dialog"
        wait(0.8)
    elif phase == "close_dialog":
        screenshot("04-close-dialog.png")
        check("close_dialog_writes_nothing_while_open", digest(STOOL) == state["saved"])
        press("RET")   # Its Save (the default button), then File > New goes on.
        state["phase"] = "close_saved"
        wait(1.5)
    elif phase == "close_saved":
        check("close_dialog_save_writes_the_persons_file", digest(STOOL) != state["saved"])
        check("close_dialog_then_file_new", bpy.data.filepath == "" and not native().restored_save_pending())
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.open_mainfile(filepath=str(STOOL), load_ui=False)
        state["phase"] = "director_again"
        state["settle"] = True
        wait(0.5)
    elif phase == "director_again":
        # File > New read --factory-startup's home file and its stock layout: back to Director for the rest.
        with bpy.context.temp_override(window=window(), screen=window().screen):
            bpy.ops.angelo.workspace_mode(mode="DIRECTOR")
        state["phase"] = "third_restore"
        state["settle"] = True
        wait(0.5)
    elif phase == "third_restore":
        state["saved"] = request_and_restore("third", "AgentTorus")
        state["phase"] = "update_open"
        state["settle"] = True
        wait(0.5)
    elif phase == "update_open":
        check("still_in_director", [area.type for area in window().screen.areas] == report["facts"]["layout"])
        update_card()
        state["phase"] = "update_card"
        wait(0.8)
    elif phase == "update_card":
        screenshot("05-update-save-card.png")
        from angelo_agent import update_restart
        from angelo_agent import card_host
        card = card_host.active_card()
        check("update_save_card_is_shown", card_host.is_active("update_save"))
        card.act("save", None, SimpleNamespace(close=card_host.close))   # Its Save and Restart, as a click runs it.
        report["facts"]["update_watch"] = {key: value for key, value in (update_restart._watch or {}).items()
                                           if key in ("asking", "saved_path", "failed")}
        state["phase"] = "update_question"
        wait(0.8)
    elif phase == "update_question":
        screenshot("06-update-save-question.png")
        check("update_question_writes_nothing_while_open", digest(STOOL) == state["saved"] and not state["launched"])
        press("RET")
        state["phase"] = "update_saved"
        wait(1.5)
    elif phase == "update_saved":
        from angelo_agent import update_restart
        report["facts"]["update_after"] = {"launched": state["launched"], "pending": native().restored_save_pending(),
                                           "dirty": bpy.data.is_dirty, "filepath": bpy.data.filepath,
                                           "watch": {key: value for key, value in (update_restart._watch or {}).items()
                                                     if key in ("asking", "saved_path", "failed")}}
        check("update_save_writes_the_persons_file_then_restarts", digest(STOOL) != state["saved"]
              and not native().restored_save_pending() and state["launched"]
              and state["launched"][0] == str(STOOL))
        state["saved"] = request_and_restore("fourth", "AgentMonkey")
        state["phase"] = "quit_open"
        state["settle"] = True
        wait(0.5)
    elif phase == "quit_open":
        bpy.data.objects[0].location.z += 0.5
        invoke(bpy.ops.wm.quit_blender)   # The quit prompt.
        state["phase"] = "quit_prompt"
        wait(0.8)
    elif phase == "quit_prompt":
        screenshot("07-quit-prompt.png")
        title_names_the_file("quit_prompt")
        check("quit_prompt_writes_nothing_while_open", digest(STOOL) == state["saved"])
        state["quit_expected"] = state["saved"]
        write_report(phase="quitting")
        atexit.register(after_quit)
        press("RET")   # Its Save: Angelo saves the person's file, then quits.
        return None
    return 0.1


def update_card():
    """Restart to Update's save card for the restored document, with the installer launch as a stand-in."""
    from angelo_agent import card_host, update_restart
    state["launched"] = []
    state["offer"] = SimpleNamespace(release=SimpleNamespace(version="9.9.9"))
    state["ready"] = SimpleNamespace(version="9.9.9")
    update_restart.launch = lambda offer, ready: state["launched"].append(bpy.data.filepath)
    card_host.request(update_restart.SaveCard(Path(bpy.data.filepath).name, 0,
                                              lambda choice: update_restart._after_prompt(choice, state["offer"],
                                                                                          state["ready"])))
    card_host.pump(lambda: True)   # Shown now, as ``app_notices`` pumps it.


def after_quit():
    """After the quit prompt's Save: Python ends after Blender freed its data, so only the file is read."""
    check("quit_prompt_save_writes_the_persons_file", digest(STOOL) != state.get("quit_expected"))
    write_report(phase="complete")


bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
