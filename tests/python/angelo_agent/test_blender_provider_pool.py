# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Preferences/model-pool QA with fixture status/catalogs, no inference."""

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

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-provider-pool-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, console, chat_view, preview_branding
from angelo_agent.chat_settings import normalize_models

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
preview_branding.install()
bpy.context.preferences.view.show_splash = False
state = {"success": False, "phase": "boot", "checks": {},
         "fixture": "Synthetic status/catalogs; no sign-in, tokens or model calls"}
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]


class Layout:
    def __init__(self):
        self.labels, self.operators, self.props = [], [], []

    def row(self):
        return self

    def box(self):
        return self

    def separator(self):
        pass

    def label(self, text="", **kwargs):
        self.labels.append(text)

    def prop(self, data, name, **kwargs):
        # The AI Providers page also draws the local CLI access toggle.
        self.props.append(name)

    def operator(self, name, **kwargs):
        self.operators.append((name, kwargs))
        return SimpleNamespace()


def write():
    (OUT / "provider-pool.json").write_text(json.dumps(state, indent=2))


def capture(file, window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / file))


def tick():
    try:
        window = bpy.context.window_manager.windows[0]
        if state["phase"] == "boot":
            store = ui.connections()
            a = store.add("OPENAI", "SUBSCRIPTION", "ChatGPT", reuse_native=True)
            b = store.add("ANTHROPIC", "SUBSCRIPTION", "Claude", reuse_native=True)
            unavailable = store.add("OPENAI", "API_KEY", "Disconnected API key")
            ui._authentication.status.update(codex_subscription="Connected to ChatGPT", claude_subscription="Connected to Claude")
            gpt = normalize_models([{"model":"gpt-6.1-sol", "displayName":"GPT-6.1 Sol", "isDefault":True}])
            claude = normalize_models([{"value":"sonnet", "displayName":"Claude Sonnet", "isDefault":True}], "claude")
            ui.catalogs().entries.update({a["id"]:gpt, b["id"]:claude, unavailable["id"]:gpt})
            controller = ui.get_controller()
            controller.choose_connection(a["mode"], ui.connection_settings(a["mode"], record=a), "", a["id"], gpt, "gpt-6.1-sol")
            layout = Layout()
            ui.draw_ai_providers(layout, bpy.context)
            state["checks"]["no_use_or_selected_controls_in_preferences"] = not any(name == "angelo.connection_use" for name,_ in layout.operators) and not any(label in {"Use","Selected"} for label in layout.labels)
            state["checks"]["both_accounts_shown_available"] = layout.labels.count("Available in chat") == 2
            choices = ui.model_choices(controller)
            state["checks"]["both_connections_in_one_model_pool"] = {m["connection_id"] for m in choices} == {a["id"],b["id"]}
            state["checks"]["disconnected_profiles_not_offered"] = all(m["connection_id"] != unavailable["id"] for m in choices)
            chat_id, bootstrap_default = controller.state["id"], store.active
            bpy.ops.angelo.select_model(connection_id=b["id"], model="sonnet")
            state["checks"]["model_selection_stays_in_same_chat"] = controller.state["id"] == chat_id and controller.state["provider"] == "claude_subscription"
            state["checks"]["selection_does_not_activate_an_exclusive_global_account"] = store.active == bootstrap_default and {m["connection_id"] for m in ui.model_choices(controller)} == {a["id"],b["id"]}
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "layout"
            write()
            return .5
        if ui._layout_timer is not None:
            return .1
        if state["phase"] == "layout":
            area = next(a for a in window.screen.areas if console.is_chat(a))
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                chat_view.show_popover(bpy.context, "models")
            state["phase"] = "models"
            write()
            return .5
        if state["phase"] == "models":
            capture("all-connected-models.png", window)
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            state["phase"] = "preferences"
            write()
            return .5
        if state["phase"] == "preferences":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            capture("connections-not-exclusive.png", prefs)
            state["checks"]["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
            state["checks"]["no_chat_draw_error"] = chat_view.last_error is None
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            write()
            print("ANGELO_PROVIDER_POOL_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        write()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
