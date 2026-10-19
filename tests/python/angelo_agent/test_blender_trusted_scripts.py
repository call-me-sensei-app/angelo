# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trusted scripts in the compiled app: exact review runs once, chat consent runs, revoked or read-only never runs.

GUI, factory startup, isolated state; no provider, account or network. A
stand-in conversation (its own ``Controller``) queues ``angelo_execute_python``
calls through the real admission, ``pump_tools``, the real ``angelo.approve``
operator, ``BlenderExecutor`` and request snapshots:

* without consent a script waits for approval; an exact-code review then runs
  it once (a second Approve finds nothing) with ``execution_mode``
  ``trusted_unconfined`` and ``confined`` false, after a request snapshot;
* with chat consent (``set_trusted_scripts``, which the native dialog grants;
  ``test_blender_trusted_scripts_consent`` drives that dialog) a script runs
  through the automatic route and says ``consent: chat_session``;
* a script queued under consent that is revoked before dispatch waits for
  approval instead, and a Read-only chat refuses one: neither changes the scene.

This is an execution, admission and snapshot-presence smoke. It is not Undo,
restoration, the consent dialog or the script review card (those are
``test_blender_trusted_scripts_consent``, ``test_blender_chat_cards``,
``test_blender_turn_recovery`` and ``test_blender_checkpoint_restore_ui``).

Run through ``run_native_suites.py --gui --include test_blender_trusted_scripts``;
check ``trusted-scripts.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background, "Trusted scripts need a window for the executor and the approval operator"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-trusted-scripts-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {},
          "scope": "Execution, admission and snapshot presence; not Undo, restoration or the consent dialog"}
KEY = "_angelo_trust_smoke"


def check(name, value):
    REPORT["checks"][name] = bool(value)


def smoke():
    from angelo_agent import ui
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.controller import Controller, ToolDispatchBudget
    bpy.context.preferences.use_preferences_save = False
    bpy.context.preferences.edit.use_global_undo = True
    window = ui.main_window()
    assert window and any(area.type == 'VIEW_3D' for area in window.screen.areas), "The fixture needs a 3D viewport"
    (OUT / "chat").mkdir(exist_ok=True)
    controller = Controller(str(OUT / "chat"))
    try:
        controller.busy = True
        controller.set_document(ui.document_key())
        controller.checkpoint_factory = lambda: ui.capture_checkpoint(controller)
        ui._controllers[controller.state["id"]] = controller
        executor = BlenderExecutor(window)

        def queue(identity, value):
            replies = []
            controller.request_tool(identity, "angelo_execute_python", {
                "description": "Isolated trusted script smoke",
                "code": 'scene["%s"] = %d\nresult = scene["%s"]' % (KEY, value, KEY)}, replies.append)
            return next(c for c in controller.pending.values() if c.id.endswith("/" + identity)), replies

        def apply(call):
            with bpy.context.temp_override(window=window):
                return bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=controller.state["id"])

        def pump():
            controller.pump_tools(executor, ui.document_key(), apply_mutation=apply, budget=ToolDispatchBudget(seconds=60))

        call, replies = queue("manual", 11)
        pump()
        check("unreviewed_script_waits_for_approval", not controller.trusted_scripts and not replies
              and KEY not in window.scene)
        controller.review_script(call)  # Stands in for the person's exact-code review on the card.
        apply(call)
        first = replies[0] if replies else {}
        check("reviewed_script_runs", first.get("success") and window.scene.get(KEY) == 11)
        check("result_says_unconfined", first.get("execution_mode") == "trusted_unconfined"
              and first.get("confined") is False and first.get("consent") == "exact_code_review")
        apply(call)
        check("a_second_approve_runs_nothing", len(replies) == 1)
        check("request_snapshot_present", bool(controller.request_checkpoint))
        controller.set_trusted_scripts(True)  # The native consent dialog's confirmation (its own suite).
        call, replies = queue("trusted", 22)
        pump()
        check("chat_consent_runs_automatically", replies and replies[0].get("success")
              and replies[0].get("consent") == "chat_session" and window.scene.get(KEY) == 22)
        call, replies = queue("revoked", 33)
        controller.set_trusted_scripts(False)
        pump()
        check("revoked_before_dispatch_waits", not replies and window.scene.get(KEY) == 22)
        controller.decline_tool(call.id)
        call, replies = queue("read-only", 44)
        controller.set_options(approval="READ_ONLY")
        pump()
        check("read_only_refuses", replies and "error" in replies[0] and window.scene.get(KEY) == 22)
    finally:
        ui._controllers.pop(controller.state["id"], None)
        controller.close()


def run():
    try:
        from angelo_agent import ui
        ui._authentication.start = lambda *args, **kwargs: None
        ui.refresh_connected_models = lambda *args, **kwargs: None
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
        smoke()
        REPORT.update(success=bool(REPORT["checks"]) and all(REPORT["checks"].values()), phase="complete")
    except Exception:
        REPORT.update(success=False, phase="failed", error=traceback.format_exc())
    (OUT / "trusted-scripts.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_TRUSTED_SCRIPTS", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"],
                                                "failed": sorted(k for k, v in REPORT["checks"].items() if not v)}),
          flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1.0)
