# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saved API keys through the real AI Providers operators, with a fake Keychain.

Run with ``--background --factory-startup``. The add-on keeps the Keychain off
in background mode (and ANGELO_DISABLE_KEYCHAIN=1 is set), which this suite
checks first; it then injects an in-memory fake backend, so no login Keychain
is read, written or prompted. Connection checks use a local fixture transport:
no network, provider, inference or billing.
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-saved-keys-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, saved_keys_ui
from angelo_agent.api_connections import ApiConnections
from angelo_agent.keychain_macos import KeychainError
from angelo_agent.provider_registry import SPECS
from angelo_agent.saved_keys import account_name, profile_id

KEY, OTHER = "test-key-not-real-0001", "test-key-not-real-0002"
report = {"checks": {}, "success": False, "fixture": "Fake Keychain backend and fixture catalogs; no login Keychain"}


# The fake stands in for the macOS Keychain (its errors are OSStatus codes), so the store's wording is
# pinned to macOS and the checks below read the same on every platform.
FAKE_PLATFORM = "darwin"


class FakeKeychain:
    def __init__(self):
        self.items, self.calls, self.unreadable, self.gate = {}, [], set(), None

    def read(self, account, interactive=False):
        self.calls.append(("read", account, interactive))
        if self.gate is not None:
            self.gate.wait(10)
        if account in self.unreadable and not interactive:
            raise KeychainError(-25308)
        return self.items.get(account)

    def write(self, account, secret, label, interactive=False):
        self.calls.append(("write", account, interactive))
        self.items[account] = secret
        return True  # Read back by this build.

    def delete(self, account, interactive=False):
        self.calls.append(("delete", account, interactive))
        return self.items.pop(account, None) is not None

    def erase(self, account, interactive=False):
        raise AssertionError("This build owns every fixture item")


def transport(request):
    mode = next(m for m in SPECS if SPECS[m].get("probe") and request.full_url == SPECS[m]["base_url"] + SPECS[m]["probe"])
    return {"data": [{"id": m["id"]} for m in SPECS[mode]["models"]]}


class Layout:
    def __init__(self):
        self.labels = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self if name in {"row", "box", "column"} else SimpleNamespace()

    def label(self, text="", **kwargs):
        self.labels.append(text)


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def pump_until(condition, limit=5.0):
    longest, end = 0.0, time.monotonic() + limit
    while time.monotonic() < end:
        started = time.perf_counter()
        ui.pump()
        longest = max(longest, time.perf_counter() - started)
        if condition():
            return longest
        time.sleep(0.01)
    raise AssertionError("condition not reached within %.1fs" % limit)


def idle():
    return not saved_keys_ui._vault._pending and not ui._api_connections.running


def restart(fake):
    """Disable/enable the add-on (keys leave memory), then reopen the same fake Keychain."""
    addon_utils.disable("angelo_agent", default_set=False)
    check_once("memory_cleared_on_unregister", not ui._memory_keys)
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    ui._api_connections = ApiConnections(transport)
    ui._authentication.start = lambda *args, **kwargs: None
    saved_keys_ui.start(ui, background=False, backend=fake, platform=FAKE_PLATFORM)


def check_once(name, condition):
    if name not in report["checks"]:
        check(name, condition)


def main():
    ui._authentication.start = lambda *args, **kwargs: None
    ui.refresh_connected_models = lambda *args, **kwargs: None
    ui._api_connections = ApiConnections(transport)
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    check("keychain_off_in_background_automation", not saved_keys_ui.available()
          and "ANGELO_DISABLE_KEYCHAIN" in saved_keys_ui.unavailable_reason())
    fake = FakeKeychain()
    saved_keys_ui.start(ui, background=False, backend=fake, platform=FAKE_PLATFORM)
    profile = profile_id(ui.connection_directory())

    result = bpy.ops.angelo.new_connection('EXEC_DEFAULT', group="CODING", provider="XAI", method="API_KEY",
                                            connection_name="Grok QA", api_key=KEY)
    grok = next(r for r in ui.connections().records.values() if r["mode"] == "grok_api")
    account = account_name(grok["id"], profile)
    pump_until(idle)
    check("remember_is_on_by_default", 'FINISHED' in result and fake.items.get(account) == KEY)
    check("saved_automatically_without_prompt", all(call[2] is False for call in fake.calls))
    check("no_key_in_connection_metadata", KEY not in ui.connections().path.read_text())
    check("typed_key_verified", ui.connection_ready(grok))

    restart(fake)
    longest = pump_until(lambda: ui.connection_ready(ui.connections().records[grok["id"]]))
    grok = ui.connections().records[grok["id"]]
    check("restart_restores_and_verifies_key", ui._memory_keys.get(grok["id"]) == KEY)
    check("restored_key_has_same_billing_label",
          "API key" in next(t for t in _draw() if t.startswith("Grok / xAI")))
    check("storage_label_says_saved", "Key saved in Keychain" in _draw())
    check("no_prompting_operations_at_startup", all(call[2] is False for call in fake.calls))
    report["longest_restart_pump_ms"] = round(longest * 1000, 3)
    check("main_thread_pump_stays_fast", longest < 0.25)

    bpy.ops.angelo.new_connection('EXEC_DEFAULT', connection_id=grok["id"], api_key=OTHER, remember_key=False)
    pump_until(idle)
    check("memory_only_replacement_deletes_saved_key", account not in fake.items and ui._memory_keys[grok["id"]] == OTHER)
    check("memory_only_is_labelled", "Key in memory only · enter it again after restarting" in _draw())
    bpy.ops.angelo.new_connection('EXEC_DEFAULT', connection_id=grok["id"], remember_key=True)
    pump_until(idle)
    check("remember_current_key_without_retyping", fake.items.get(account) == OTHER)

    bpy.ops.angelo.new_connection('EXEC_DEFAULT', group="CODING", provider="ANTHROPIC", method="API_KEY",
                                   connection_name="Claude QA", api_key=KEY)
    claude = next(r for r in ui.connections().records.values() if r["mode"] == "claude_api")
    claude_account = account_name(claude["id"], profile)
    pump_until(idle)
    fake.unreadable.add(claude_account)
    restart(fake)
    pump_until(lambda: saved_keys_ui.can_load(claude, False))
    check("approval_needed_is_actionable", "Keychain approval needed" in ui.connection_status(claude)
          and not ui.connection_ready(claude))
    before = len(fake.calls)
    bpy.ops.angelo.connection_load_key('EXEC_DEFAULT', connection_id=claude["id"])
    pump_until(lambda: ui.connection_ready(claude))
    check("explicit_load_is_the_only_prompting_call",
          [c[2] for c in fake.calls[before:]] == [True] and ui.connection_status(claude) == "API key configured")

    fake.unreadable.discard(claude_account)
    fake.gate = threading.Event()
    restart(fake)
    for operation in saved_keys_ui._vault._pending:
        operation.deadline = time.monotonic() + 0.3
    longest = pump_until(lambda: ui.connection_status(claude) == "Keychain didn't respond · re-enter the key")
    report["longest_blocked_pump_ms"] = round(longest * 1000, 3)
    check("unanswered_keychain_times_out_without_blocking", longest < 0.25 and not ui._memory_keys.get(claude["id"]))
    fake.gate.set()
    time.sleep(0.1)
    ui.pump()
    check("late_keychain_result_is_ignored", not ui._memory_keys.get(claude["id"]))

    bpy.ops.angelo.connection_remove('EXEC_DEFAULT', connection_id=grok["id"])
    pump_until(idle)
    check("removing_connection_deletes_saved_key", account not in fake.items and grok["id"] not in ui.connections().records)
    check("no_scene_changes", sorted(o.name for o in bpy.context.scene.objects) == ["Camera", "Cube", "Light"])
    addon_utils.disable("angelo_agent", default_set=False)
    report["success"] = all(report["checks"].values())


def _draw():
    layout = Layout()
    ui.draw_ai_providers(layout, bpy.context)
    return layout.labels


try:
    main()
except Exception as error:
    report.update(success=False, error=str(error), traceback=traceback.format_exc())
    print(report["traceback"], flush=True)
finally:
    (OUT / "saved-keys.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_SAVED_KEYS", json.dumps({"success": report["success"]}), flush=True)
if not report["success"]:
    sys.exit(1)
