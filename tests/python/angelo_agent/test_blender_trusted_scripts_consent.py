# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The trusted-scripts consent dialog in the compiled app: default off, Escape cancels, Enter grants, a click revokes.

GUI with synthetic key events, Angelo's default chat startup, isolated state;
no provider, account, network or script execution. Covers the real registered
``angelo.trusted_scripts`` operator and its dialog:

* a fresh start has no consent, and the operator executed without its dialog
  (``EXEC_DEFAULT``) refuses before, after a cancel and after a revoke;
* Escape in the dialog leaves consent off, Enter grants it for this chat
  (screenshots of the warning both times and of the enabled state);
* invoking it again revokes consent, and a New Chat starts without it.

Pairs with ``test_blender_trusted_scripts`` (what consent lets run). Not Undo,
restoration or script review.

Run through ``run_native_suites.py --gui --include test_blender_trusted_scripts_consent``;
check ``trusted-consent-gui.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert not bpy.app.background, "The consent dialog needs a window"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The dialog belongs to the default chat.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-trusted-consent-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "screenshots": [],
          "scope": "The real native trust dialog only; no script, Undo or recovery clearance"}
STARTED = time.monotonic()
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False


def parts():
    from angelo_agent import console, ui
    window = ui.main_window()
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return {"window": window, "area": area, "region": next(r for r in area.regions if r.type == 'WINDOW')}


def invoke():
    with bpy.context.temp_override(**parts()):
        return bpy.ops.angelo.trusted_scripts('INVOKE_DEFAULT')


def execute_direct():
    """The operator without its dialog: it must refuse, never grant consent."""
    with bpy.context.temp_override(**parts()):
        try:
            return bpy.ops.angelo.trusted_scripts('EXEC_DEFAULT')
        except RuntimeError as error:  # Blender raises an ERROR report for a Python caller.
            if "Open the trust dialog for the current chat first" not in str(error):
                raise
            return {'CANCELLED'}


def key(kind):
    window = parts()["window"]
    window.event_simulate(type=kind, value='PRESS')
    window.event_simulate(type=kind, value='RELEASE')


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=parts()["window"]):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    REPORT["screenshots"].append(str(path))


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "trusted-consent-gui.json").write_text(json.dumps(REPORT, indent=2))
    print("ANGELO_TRUSTED_CONSENT_GUI", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import console, ui
    try:
        assert time.monotonic() - STARTED < 90, "The consent fixture timed out"
        phase = REPORT["phase"]
        if phase == "boot":
            if not ui.main_window() or not any(console.is_chat(a) for a in ui.main_window().screen.areas):
                return 0.2
            controller = ui.get_controller()
            controller.set_document(ui.document_key())
            REPORT["checks"]["default_off"] = not controller.busy and not controller.trusted_scripts
            REPORT["checks"]["direct_execute_before_dialog_refused"] = (execute_direct() == {'CANCELLED'}
                                                                         and not controller.trusted_scripts)
            assert invoke() == {'RUNNING_MODAL'}
            REPORT["phase"] = "cancel_dialog"
        elif phase == "cancel_dialog":
            capture("trusted-consent-cancel-warning.png")
            key('ESC')
            REPORT["phase"] = "after_cancel"
        elif phase == "after_cancel":
            controller = ui.get_controller()
            REPORT["checks"]["escape_leaves_consent_off"] = not controller.trusted_scripts
            REPORT["checks"]["direct_execute_after_cancel_refused"] = (execute_direct() == {'CANCELLED'}
                                                                        and not controller.trusted_scripts)
            assert invoke() == {'RUNNING_MODAL'}
            REPORT["phase"] = "confirm_dialog"
        elif phase == "confirm_dialog":
            capture("trusted-consent-confirm-warning.png")
            key('RET')
            REPORT["phase"] = "after_confirm"
        elif phase == "after_confirm":
            controller = ui.get_controller()
            REPORT["checks"]["enter_grants_consent"] = controller.trusted_scripts
            assert controller.trusted_scripts, "Enter in the dialog did not grant consent"
            capture("trusted-consent-enabled.png")
            assert invoke() == {'FINISHED'}
            REPORT["checks"]["invoking_again_revokes"] = not controller.trusted_scripts
            REPORT["checks"]["direct_execute_after_revoke_refused"] = (execute_direct() == {'CANCELLED'}
                                                                        and not controller.trusted_scripts)
            controller.new_chat(ui.document_key())
            REPORT["checks"]["new_chat_starts_without_consent"] = not ui.get_controller().trusted_scripts
            finish()
            return None
        return 0.5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.0)
