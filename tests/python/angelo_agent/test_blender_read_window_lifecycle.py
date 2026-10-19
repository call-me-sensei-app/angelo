# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real GUI Window removal refuses old native read contexts before borrowing.

Run through run_native_suites.py --gui --include test_blender_read_window_lifecycle.
The runner supplies factory startup, disabled autoexec and isolated user/state
directories. Success requires read-window-lifecycle.json with success exactly
true and phase exactly complete, plus a clean process/log. No provider or user
document is used. It drives the native address-read ABI directly.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
from _bpy import _angelo_read as native

assert not bpy.app.background, "Use the GUI isolated native suite runner"
assert "--factory-startup" in sys.argv and "--disable-autoexec" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

ROOT = ("scene", (), ())
EMPTY = ((), ())
WINDOW_EXPIRED = {"status": "EXPIRED", "reason": "WINDOW"}
CLOSED = {"status": "EXPIRED", "reason": "CLOSED"}
report = {"success": False, "phase": "boot", "checks": {}, "results": {},
          "paid_requests": 0, "user_projects_opened": 0, "binary_path": bpy.app.binary_path}
original = bpy.context.window
assert original is not None
original_key = original.as_pointer()
initial_keys = {item.as_pointer() for item in bpy.context.window_manager.windows}
owned_keys = set()
capsules = []
old_context = original_context = replacement_context = None
closed_key = replacement_key = None
started = time.monotonic()
finished = False


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition:
        raise AssertionError(name + (": " + repr(detail)[:1600] if detail is not None else ""))


def detached(value):
    if type(value) in (type(None), bool, int, float, str):
        return
    if type(value) is tuple:
        for item in value:
            detached(item)
        return
    if type(value) is dict:
        check("exact_transport_keys", all(type(key) is str for key in value))
        for item in value.values():
            detached(item)
        return
    raise AssertionError("Live/non-exact native payload: " + type(value).__name__)


def observe(name, operation, *arguments):
    result = operation(*arguments)
    detached(result)
    report["results"][name] = result
    return result


def live_keys():
    return {item.as_pointer() for item in bpy.context.window_manager.windows}


def live_window(key):
    return next(item for item in bpy.context.window_manager.windows if item.as_pointer() == key)


def begin(window):
    context = native.begin(window, 2.0)
    capsules.append(context)
    return context


def positive(name, context, expected_frame):
    before = (original.scene.session_uid, original.scene.frame_current, bpy.data.is_dirty)
    state = observe(name + "_check", native.check, context)
    check(name + "_live", state == {"status": "OK", "budget_exhausted": False}, state)
    root = observe(name + "_root", native.describe, context, ROOT, EMPTY, None)
    # A node record: ("NODE", address, type, (exists, id_type, original, bound_scene, self), ...).
    check(name + "_bound_scene", root.get("status") == "OK" and root.get("kind") == "STRUCT"
          and root["payload"]["node"][0] == "NODE" and root["payload"]["node"][3][3] is True, root)
    declaration = observe(name + "_member", native.describe, context, ROOT, EMPTY, "frame_current")
    check(name + "_declared", declaration.get("status") == "OK"
          and declaration["payload"]["property"] is not None, declaration)
    fingerprint = declaration["payload"]["property"]["fingerprint"]
    address = ("scene", (("attribute", "frame_current"),), ())
    value = observe(name + "_fetch", native.fetch, context, address, (((1, fingerprint),), ()), fingerprint)
    check(name + "_scalar", value.get("status") == "OK" and value["payload"]["value"] == expected_frame, value)
    check(name + "_read_pure", before == (original.scene.session_uid, original.scene.frame_current, bpy.data.is_dirty))


def open_owned():
    before = live_keys()
    with bpy.context.temp_override(window=original):
        check("window_new_finished", "FINISHED" in bpy.ops.wm.window_new())
    created = live_keys() - before
    # Record all created windows before asserting, so failure cleanup owns them.
    owned_keys.update(created)
    check("one_extra_real_window", len(created) == 1, sorted(created))
    return created.pop()


def close_owned(key):
    check("close_only_owned_extra", key in owned_keys and key != original_key)
    # No closed Window wrapper is returned or inspected after this operation.
    with bpy.context.temp_override(window=live_window(key)):
        result = bpy.ops.wm.window_close()
    check("window_close_finished", "FINISHED" in result, result)


def stale(name):
    check(name + "_check", observe(name + "_check", native.check, old_context) == WINDOW_EXPIRED)
    check(name + "_describe", observe(name + "_describe", native.describe,
                                    old_context, ROOT, EMPTY, None) == WINDOW_EXPIRED)


def write():
    (OUT / "read-window-lifecycle.json").write_text(json.dumps(report, indent=2))


def finish(error=None):
    global finished
    if finished:
        return
    finished = True
    if error:
        report["error"] = error
    try:
        for key in tuple(owned_keys & live_keys()):
            close_owned(key)
        check("owned_windows_cleaned", not (owned_keys & live_keys()))
        check("original_window_preserved", original_key in live_keys() and initial_keys <= live_keys())
        for context in capsules:
            native.close(context)
            native.close(context)
            check("cleanup_capsules_closed", native.check(context) == CLOSED)
    except Exception:
        report["cleanup_error"] = traceback.format_exc()
    report["success"] = not error and "cleanup_error" not in report and all(report["checks"].values())
    report["phase"] = "complete" if report["success"] else "failed"
    write()
    print("ANGELO_READ_WINDOW_LIFECYCLE", json.dumps({"success": report["success"],
                                                  "phase": report["phase"]}), flush=True)
    with bpy.context.temp_override(window=original):
        bpy.ops.wm.quit_blender()


def tick():
    global old_context, original_context, replacement_context, closed_key, replacement_key
    if finished:
        return None
    try:
        if time.monotonic() - started > 30:
            raise TimeoutError("Window lifecycle phases exceeded 30 seconds")
        phase = report["phase"]
        if phase == "boot":
            check("single_factory_window", len(initial_keys) == 1)
            closed_key = open_owned()
            report["phase"] = "opened"
        elif phase == "opened":
            extra = live_window(closed_key)
            old_context = begin(extra)
            positive("extra_before_close", old_context, extra.scene.frame_current)
            del extra  # Release our wrapper before the real WM close route.
            original_context = begin(original)
            positive("original_before_close", original_context, original.scene.frame_current)
            close_owned(closed_key)
            report["phase"] = "closed"
        elif phase == "closed":
            if closed_key in live_keys():
                return .05
            check("closed_window_confirmed_removed", closed_key not in live_keys())
            stale("after_confirmed_removal")
            surviving = observe("original_after_close", native.check, original_context)
            check("original_context_still_live", surviving.get("status") == "OK", surviving)
            replacement_key = open_owned()
            report["phase"] = "replacement"
        elif phase == "replacement":
            replacement = live_window(replacement_key)
            replacement_context = begin(replacement)
            positive("replacement_fresh", replacement_context, replacement.scene.frame_current)
            del replacement
            stale("after_replacement")  # Old address reuse must never revive the old context.
            native.close(old_context)
            native.close(old_context)
            check("old_close_repeat_safe", observe("old_closed", native.check, old_context) == CLOSED)
            check("old_closed_describe", observe("old_closed_describe", native.describe,
                  old_context, ROOT, EMPTY, None) == CLOSED)
            close_owned(replacement_key)
            report["phase"] = "cleanup"
        elif phase == "cleanup":
            if replacement_key in live_keys():
                return .05
            check("replacement_confirmed_removed", replacement_key not in live_keys())
            check("replacement_context_expired", observe("replacement_removed", native.check,
                                                         replacement_context) == WINDOW_EXPIRED)
            surviving = observe("original_after_both", native.check, original_context)
            check("original_context_survives_both", surviving.get("status") == "OK", surviving)
            positive("original_fresh_after_both", begin(original), original.scene.frame_current)
            finish()
            return None
        write()
        return .05
    except Exception:
        finish(traceback.format_exc())
        return None


def watchdog():
    if not finished:
        finish("Window lifecycle watchdog expired after 35 seconds")
    return None


write()
bpy.app.timers.register(tick, first_interval=.1)
bpy.app.timers.register(watchdog, first_interval=35)
