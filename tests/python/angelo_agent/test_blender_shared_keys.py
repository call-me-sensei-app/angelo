# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One Google key for Gemini Images and the Gemini coding agent, through the real AI Providers operators.

Run with ``--background --factory-startup``. A fake Keychain backend stands in for the OS store (no login Keychain
is read, written or prompted) and connection checks use a fixture transport (no network, inference or billing).
The pinned runtime is made to declare the Gemini wire for this run only, so the Gemini coding connection is offered
as a build whose runtime has it would offer it. Checks: the form offers "API key from" only when a source exists;
a linked connection gets the source's key, keeps no saved key and says whose key it uses; replacing the source's
key changes both; a restart restores both from the source's one saved item; removing the source strands the
linked connection, which then needs its own key; entering a key on it makes it its own again.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-shared-keys-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import agent_runtime, ui, saved_keys_ui, shared_keys_ui  # noqa: E402
from angelo_agent.api_connections import ApiConnections  # noqa: E402
from angelo_agent.provider_registry import SPECS  # noqa: E402
from angelo_agent.saved_keys import account_name, profile_id  # noqa: E402
from types import SimpleNamespace  # noqa: E402

FAKE_PLATFORM = "darwin"   # The fake is a macOS Keychain, as in test_blender_saved_keys.py.


class FakeKeychain:
    """An in-memory stand-in for the OS key store (the shape test_blender_saved_keys.py uses)."""

    def __init__(self):
        self.items, self.calls = {}, []

    def read(self, account, interactive=False):
        self.calls.append(("read", account, interactive))
        return self.items.get(account)

    def write(self, account, secret, label, interactive=False):
        self.calls.append(("write", account, interactive))
        self.items[account] = secret
        return True

    def delete(self, account, interactive=False):
        self.calls.append(("delete", account, interactive))
        return self.items.pop(account, None) is not None

    def erase(self, account, interactive=False):
        raise AssertionError("This build owns every fixture item")


class Layout:
    """Collects the labels AI Providers draws."""

    def __init__(self):
        self.labels = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self if name in {"row", "box", "column"} else SimpleNamespace()

    def label(self, text="", **kwargs):
        self.labels.append(text)


KEY, OTHER, THIRD = "AIza-test-key-not-real-0001", "AIza-test-key-not-real-0002", "AIza-test-key-not-real-0003"
report = {"checks": {}, "success": False, "fixture": "Fake Keychain backend and fixture catalogs; no login Keychain"}
agent_runtime.PIN["wire_apis"] = list(agent_runtime.PIN.get("wire_apis", ())) + ["gemini"]   # This run only.


def transport(request):
    """Google's model list, by resource name (both Google modes share one probe URL)."""
    models = [m["id"] for mode in ("gemini_images_api", "gemini_api") for m in SPECS[mode]["models"]]
    return {"models": [{"name": "models/" + identity} for identity in models]}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def pump_until(condition, limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        ui.pump()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached within %.1fs" % limit)


def idle():
    return not saved_keys_ui._vault._pending and not ui._api_connections.running


def labels():
    layout = Layout()
    ui.draw_ai_providers(layout, bpy.context)
    return layout.labels


def records():
    return ui.connections().records


def form_items(connection_id=""):
    """The "API key from" choices the form shows for a new (or this) Gemini coding connection."""
    operator = type("Form", (), {"group": "CODING", "provider": "GOOGLE", "connection_id": connection_id})()
    return [(identity, name) for identity, name, _ in shared_keys_ui.key_source_items(operator, bpy.context)]


def restart(fake):
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
    fake = FakeKeychain()
    saved_keys_ui.start(ui, background=False, backend=fake, platform=FAKE_PLATFORM)
    profile = profile_id(ui.connection_directory())

    check("no_choice_before_a_source_exists", [i for i, _ in form_items()] == [shared_keys_ui.NEW])
    __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', group="GENAI", provider="GOOGLE", method="API_KEY",
                                   connection_name="Studio Gemini", api_key=KEY)
    images = next(r for r in records().values() if r["mode"] == "gemini_images_api")
    pump_until(idle)
    check("source_key_saved", fake.items.get(account_name(images["id"], profile)) == KEY)
    items = form_items()
    check("form_offers_the_images_key", [i for i, _ in items] == [shared_keys_ui.NEW, images["id"]]
          and "Studio Gemini" in items[1][1])

    result = __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', group="CODING", provider="GOOGLE", method="API_KEY",
                                            connection_name="Carbon", key_source=images["id"])
    coding = next(r for r in records().values() if r["mode"] == "gemini_api")
    pump_until(idle)
    check("linked_without_typing_a_key", 'FINISHED' in result and coding["key_from"] == images["id"])
    check("linked_connection_has_the_key", ui._memory_keys.get(coding["id"]) == KEY)
    check("linked_connection_has_no_saved_item", account_name(coding["id"], profile) not in fake.items)
    check("linked_connection_is_verified", ui.connection_ready(records()[coding["id"]]))
    check("ai_providers_says_whose_key", "Uses the API key of Studio Gemini" in labels())
    check("no_key_in_connection_metadata", KEY not in ui.connections().path.read_text())

    __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', connection_id=images["id"], api_key=OTHER)
    pump_until(idle)
    check("replacing_the_source_key_changes_both",
          ui._memory_keys.get(images["id"]) == OTHER and ui._memory_keys.get(coding["id"]) == OTHER)

    restart(fake)
    pump_until(lambda: ui._memory_keys.get(coding["id"]) == OTHER)
    check("restart_restores_both_from_one_saved_item",
          ui._memory_keys.get(images["id"]) == OTHER and len(fake.items) == 1)

    bpy.ops.angelo.connection_remove('EXEC_DEFAULT', connection_id=images["id"])
    pump_until(idle)
    coding = records()[coding["id"]]
    check("removing_the_source_strands_the_linked_connection",
          coding["key_from"] is None and not ui._memory_keys.get(coding["id"]))
    check("stranded_connection_asks_for_a_key", not ui.connection_ready(coding))
    check("stranded_form_offers_no_source", [i for i, _ in form_items(coding["id"])] == [shared_keys_ui.NEW])
    refused = __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', connection_id=coding["id"])
    check("no_key_no_link_is_refused_or_kept_keyless", not ui._memory_keys.get(coding["id"]) or 'CANCELLED' in refused)
    __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', connection_id=coding["id"], api_key=THIRD)
    pump_until(idle)
    check("own_key_after_stranding", ui._memory_keys.get(records()[coding["id"]]["id"]) == THIRD
          and fake.items.get(account_name(coding["id"], profile)) == THIRD)
    check("no_scene_changes", sorted(o.name for o in bpy.context.scene.objects) == ["Camera", "Cube", "Light"])
    addon_utils.disable("angelo_agent", default_set=False)
    report["success"] = all(report["checks"].values())


try:
    main()
except Exception as error:
    report.update(success=False, error=str(error), traceback=traceback.format_exc())
    print(report["traceback"], flush=True)
finally:
    (OUT / "shared-keys.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_SHARED_KEYS", json.dumps({"success": report["success"]}), flush=True)
if not report["success"]:
    sys.exit(1)
