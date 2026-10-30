# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A fresh chat stays a welcome when a file opens after startup; a chat with work says another document changed it.

1.0.5 screenshot review row 22: on a first launch where a file opened after startup (from the Finder), the fresh,
unconnected chat showed "Document changed. Start New Chat before issuing scene commands." and its connect cards
collapsed to the reconnect row. Real default startup (the Director layout and its chat, no connection), then a
real ``wm.open_mainfile`` of a saved copy: the chat must hold and draw no such line and still draw its welcome.
Then, held out, with a message in the chat: reopening the same file keeps the chat on it (the document identity,
``document_identity``: the same id at the same path in the same file), and opening another document still says the
document changed (the guard stays). The other document is saved by a separate background Blender, so it carries an id
of its own (a copy of the open document would carry its id and be offered instead, ``document_offer``).

Run through ``run_native_suites.py --gui --include test_blender_fresh_chat_document``; check
``fresh-chat-document.json`` for ``success`` and ``phase == "complete"``. No provider, account or network.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-fresh-chat-document-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup and its chat, as a person launches it.
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "fresh-chat-document.json"
SAVED = OUT / "opened-later.blend"
OTHER = OUT / "another-document.blend"
NOTICE = "Document changed"
WELCOME = ("welcome_connect", "welcome_team", "welcome_api_key")
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": []}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    return (area, next(r for r in area.regions if r.type == 'WINDOW')) if area else (None, None)


def view():
    from angelo_agent import chat_view
    area, _ = chat()
    return chat_view._views.get(area.as_pointer(), {}) if area else {}


def drawn_texts():
    """Redraw once and return every text run the chat drew."""
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    return runs


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def chat_state(label):
    from angelo_agent import ui
    controller = ui.get_controller()
    runs = drawn_texts()
    held = [str(m.get("text")) for m in controller.state["messages"] if NOTICE in str(m.get("text"))]
    welcome = sorted({action for _, action, _ in view().get("hits", []) if action in WELCOME})
    state[label] = {"held": held, "drawn": [run for run in runs if NOTICE in run], "welcome": welcome,
                    "messages": len(controller.state["messages"]), "invalid": controller.document_invalid}
    return held, [run for run in runs if NOTICE in run], welcome


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FRESH_CHAT_DOCUMENT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 150, "Fresh-chat fixture timed out in " + state["phase"]
        from angelo_agent import director, ui
        checks, phase = state["checks"], state["phase"]
        if phase == "boot":
            area, _ = chat()
            if not qa_boot.started() or area is None or not view().get("hits"):
                return .2
            held, drawn, welcome = chat_state("at_startup")
            capture("fresh-0-startup.png")
            checks["a_fresh_chat_starts_as_a_welcome"] = not held and not drawn and bool(welcome)
            bpy.ops.wm.save_as_mainfile(filepath=str(SAVED), copy=True)
            import subprocess
            subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr",
                            "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % str(OTHER)],
                           capture_output=True, timeout=120, check=True)
            state["phase"] = "open"
            return .3
        if phase == "open":   # A file opened after startup, as from the Finder.
            bpy.ops.wm.open_mainfile(filepath=str(SAVED), load_ui=False)
            state.update(phase="opened", opened_at=time.monotonic())
            return .5
        if phase == "opened":
            area, _ = chat()
            if area is None or not view().get("hits"):
                return .2
            if time.monotonic() - state["opened_at"] < 1.5:   # Let the load's own timers (layout, chat) settle.
                return .2
            held, drawn, welcome = chat_state("after_opening_a_file")
            capture("fresh-1-after-opening-a-file.png")
            checks["a_fresh_chat_holds_no_document_changed_line"] = not held
            checks["a_fresh_chat_draws_no_document_changed_line"] = not drawn
            checks["a_fresh_chat_still_draws_its_welcome"] = bool(welcome)
            # Held out: a chat with a person's message keeps its turns bound to their document.
            controller = ui.get_controller()
            controller.emit("message", {"role": "user", "text": "Make a chair"})
            controller.pump_events()
            controller.version += 1
            state["phase"] = "open_with_history"
            return .3
        if phase == "open_with_history":
            bpy.ops.wm.open_mainfile(filepath=str(SAVED), load_ui=False)
            state.update(phase="opened_with_history", opened_at=time.monotonic())
            return .5
        if phase == "opened_with_history":
            if time.monotonic() - state["opened_at"] < 1.5:
                return .2
            held, drawn, _ = chat_state("with_history_after_reopening")
            capture("fresh-2-chat-with-history-after-reopening.png")
            checks["reopening_the_same_file_keeps_a_chat_with_a_message_on_it"] = (
                not held and not drawn and not ui.get_controller().document_invalid)
            bpy.ops.wm.open_mainfile(filepath=str(OTHER), load_ui=False)
            state.update(phase="opened_another", opened_at=time.monotonic())
            return .5
        if phase == "opened_another":
            if time.monotonic() - state["opened_at"] < 1.5:
                return .2
            held, drawn, _ = chat_state("with_history_after_opening_another")
            capture("fresh-3-chat-with-history-after-opening-another.png")
            checks["a_chat_with_a_message_still_says_the_document_changed"] = len(held) == 1 and bool(drawn)
            finish()
            return None
        return .2
    except Exception as error:   # noqa: BLE001  (the receipt carries it)
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
