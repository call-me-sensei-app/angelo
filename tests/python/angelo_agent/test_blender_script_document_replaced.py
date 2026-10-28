# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An approved script that replaces the open document, in a background Blender: a clean refusal, never a crash.

Evidence: in a background Blender (the headless MCP child, a parallel worker) a script that loads a file frees the
window manager and the window the executor holds. Up to 1.0.5 the executor then read the scene back through that
window (``blender_tools``: ``self.window.view_layer.update()``) and the process crashed, a use-after-free (the
parallel-agents spike: 3 crashes in 4 trials, exit 139). ``script_document`` now ends such a script with
``DOCUMENT_REPLACED`` and reads nothing back.

Each case is one approved ``angelo_execute_python`` call through Angelo's own route in this background process:
controller, the person's review (``review_script``), ``angelo.approve`` and the executor. Before each case the
document is a saved file. Cases that replace it: ``read_homefile``, ``open_mainfile`` of another file,
``open_mainfile`` without its UI, ``revert_mainfile``, a load under a computed operator name, a load followed by a
raise, a load followed by a parent-inverse statement (the host's mid-script hook, ``script_prepare``), a load followed
by the namespace's ``scene`` and by the ``kit`` (what the script can reach of the host), a load after
clearing ``load_post`` (fails closed) and ``read_factory_settings``. Controls that do not: a plain script (succeeds),
a raise with no load (the usual rollback), a load of a missing file the script catches (load_pre fired, nothing
replaced), and a plain script after ``read_factory_settings``.
``ANGELO_QA_CASES`` (comma-separated) runs only the named cases: a crash repro on a build without the fix.

Writes ``script-document-replaced.json`` in ``ANGELO_QA_DIR``; exits 1 on any failed check.
"""

import json
import os
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-script-document-"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _prepare_preferences, _register  # noqa: E402

DOCUMENT, OTHER = OUT / "document.blend", OUT / "other.blend"
LOAD = "bpy.ops.wm.read_homefile(use_empty=True)\n"
# name -> (code, replaces the document). read_factory_settings resets the preferences too, so it runs last.
FRESH_AFTER_LOAD = ("scene = bpy.context.scene\nparent = bpy.data.objects.new('P', None)\n"
                    "scene.collection.objects.link(parent)\nparent.constraints.new('COPY_LOCATION')\n"
                    "child = bpy.data.objects.new('C', None)\nscene.collection.objects.link(child)\n"
                    "child.parent = parent\nchild.matrix_parent_inverse = parent.matrix_world.inverted()\n")
# name -> (code, replaces the document). read_factory_settings resets the preferences too, so a plain script after
# it shows what it leaves behind.
CASES = {
    "plain": ("import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Kept', None))\n"
              "result = {'objects': len(bpy.data.objects)}\n", False),
    "raise_without_load": ("raise ValueError('no load')\n", False),
    "failed_load": ("import bpy\ntry:\n    bpy.ops.wm.open_mainfile(filepath='/nonexistent/missing.blend')\n"
                    "except RuntimeError:\n    pass\n"
                    "bpy.context.scene.collection.objects.link(bpy.data.objects.new('AfterFailedLoad', None))\n",
                    False),
    "read_homefile": ("import bpy\n" + LOAD + "result = {'objects': len(bpy.data.objects)}\n", True),
    "open_mainfile": ("import bpy\nbpy.ops.wm.open_mainfile(filepath=%r)\n" % str(OTHER), True),
    "open_mainfile_without_ui": ("import bpy\nbpy.ops.wm.open_mainfile(filepath=%r, load_ui=False)\n" % str(OTHER),
                                 True),
    "revert_mainfile": ("import bpy\nbpy.ops.wm.revert_mainfile()\n", True),
    "computed_name": ("import bpy\ngetattr(bpy.ops.wm, 'read_' + 'homefile')(use_empty=True)\n", True),
    "load_then_raise": ("import bpy\n" + LOAD + "raise ValueError('after the load')\n", True),
    "load_then_parent_inverse": ("import bpy\n" + LOAD + FRESH_AFTER_LOAD, True),
    "load_then_old_scene": ("import bpy\n" + LOAD + "scene.collection.objects.link(bpy.data.objects.new('X', None))\n",
                            True),
    "load_then_kit": ("import bpy\n" + LOAD + "kit.box('B', (0, 0, 0), (1, 1, 1))\n", True),
    "cleared_handlers_then_load": ("import bpy\nbpy.app.handlers.load_post.clear()\n" + LOAD, True),
    "read_factory_settings": ("import bpy\nbpy.ops.wm.read_factory_settings(use_empty=True)\n", True),
    "plain_after_factory_settings": ("import bpy\nbpy.context.scene.collection.objects.link("
                                     "bpy.data.objects.new('KeptAfterFactory', None))\n", False),
}
checks = {}
report = {"cases": {}, "checks": checks}


def apply_mutation(target, call):
    with bpy.context.temp_override(window=ui.main_window()):
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])


def run_case(name, code):
    """One approved script on the saved document; the tool result, or None when none came within a minute."""
    bpy.ops.wm.open_mainfile(filepath=str(DOCUMENT), load_ui=False, use_scripts=False)
    controller = ui.Controller(str(OUT / "state"), session_id=str(uuid.uuid4()))
    controller.state["options"] = dict(controller.state.get("options") or {}, approval="ASK")
    controller.set_document(ui.document_key())
    ui._controllers[controller.state["id"]] = controller
    controller.open_external_read()
    answers = []
    call_id = "q-" + name
    controller.request_tool(call_id, "angelo_execute_python", {"description": name, "code": code}, answers.append)
    key, approved, deadline = controller.state["id"] + "/" + call_id, False, time.monotonic() + 60
    try:
        while not answers and time.monotonic() < deadline:
            controller.pump_events()
            pump_controller_tools([controller], 0, BlenderExecutor(ui.main_window()), ui.document_key(), apply_mutation)
            call = controller.pending.get(key)
            if call is not None and call.approval and not approved and not call.executing:
                controller.review_script(call)   # The person's Review of the exact code.
                approved = True
                apply_mutation(controller, call)
            time.sleep(0.005)
    finally:
        ui._controllers.pop(controller.state["id"], None)
    return answers[0] if answers else None


def main():
    _prepare_preferences(bpy)
    _register(bpy, ui)
    bpy.ops.wm.save_as_mainfile(filepath=str(OTHER))
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Original", None))
    bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))
    wanted = [c for c in os.environ.get("ANGELO_QA_CASES", "").split(",") if c] or list(CASES)
    for name in wanted:
        code, replaces = CASES[name]
        print("ANGELO_SCRIPT_DOCUMENT case", name, flush=True)
        handlers = list(bpy.app.handlers.load_post)   # A case that clears them must not break the next one.
        result = run_case(name, code) or {}
        if name.startswith("cleared_handlers"):
            bpy.app.handlers.load_post[:] = handlers
        row = {k: result.get(k) for k in ("success", "error_code", "document_replaced", "main_data_restored",
                                          "raised", "script_error", "scene_changed")}
        report["cases"][name] = row
        checks[name + "_answered"] = bool(result)
        if replaces:
            checks[name + "_refused_as_document_replaced"] = (row["error_code"] == "DOCUMENT_REPLACED"
                                                              and row["document_replaced"] is True
                                                              and row["main_data_restored"] is False
                                                              and not row["success"])
        else:
            checks[name + "_is_not_a_replacement"] = row["error_code"] != "DOCUMENT_REPLACED" and not row["document_replaced"]
        window = ui.main_window()   # The live window and its view layer read after the case: the process is sound.
        checks[name + "_process_reads_its_document"] = window is not None and bool(window.view_layer.name)
    for name in ("plain", "failed_load", "plain_after_factory_settings"):
        if name in report["cases"]:
            checks[name + "_succeeds"] = report["cases"][name]["success"] is True
    if "load_then_raise" in report["cases"]:
        checks["load_then_raise_names_the_error"] = report["cases"]["load_then_raise"]["raised"] == "ValueError"


try:
    main()
except Exception:   # Reported as a failed check, never a silent pass.
    checks["no_error"] = False
    report["error"] = traceback.format_exc()
report["success"] = bool(checks) and all(checks.values())
(OUT / "script-document-replaced.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_DOCUMENT", json.dumps({"success": report["success"]}), flush=True)
sys.exit(0 if report["success"] else 1)
