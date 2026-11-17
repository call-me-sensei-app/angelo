# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Blender settings/forms/picker with synthetic API health, never billed."""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
import layout_double  # noqa: E402  (UILayout's own calls, shared by every suite drawing a page)

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-api-providers-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"  # Fixture keys must never reach a login Keychain.
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, console, chat_view, preview_branding, saved_keys_ui, shared_keys_ui, agent_runtime, provider_registry
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.provider_registry import CODING, GENAI, SPECS, HTTP_MODES, EXTERNAL_CODEX_MODES
from angelo_agent.api_connections import ApiConnections
from angelo_agent.chat_settings import normalize_models
from angelo_agent.chat_popovers import model_rows
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

KEY = "fixture-key-not-a-real-credential"
CODING_CONNECTIONS = 2 + len(EXTERNAL_CODEX_MODES)   # ChatGPT and Claude sign-ins, then one per API-key coding mode.
requests = []


def probe_url(mode):
    """As api_connections.probe_request builds it: a path under the base URL, or an absolute URL (fal's)."""
    probe = SPECS[mode]["probe"]
    return probe if probe.startswith("https://") else SPECS[mode]["base_url"] + probe


def transport(request):
    requests.append(request.full_url)
    # Several modes can share one probe URL (Gemini Images and the Gemini coding agent both list Google's models):
    # like the real endpoint, the answer lists the models of every mode behind that URL.
    modes = sorted(m for m in HTTP_MODES if SPECS[m]["probe"] and request.full_url == probe_url(m))
    assert modes and request.get_method() == "GET"
    mode, models = modes[0], [m for each in modes for m in SPECS[each]["models"]]
    if mode == "tripo_api":
        return {"code": 0, "data": {"balance": 100.25}}
    if mode == "meshy_api":
        return {"balance": 50}
    if SPECS[mode]["provider"] == "GOOGLE":   # Google's own model list shape.
        return {"models": [{"name": "models/" + m["id"]} for m in models]}
    return {"data": [{"id": m["id"]} for m in models]}


ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
ui._api_connections = ApiConnections(transport)
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
preview_branding.install()
bpy.context.preferences.view.show_splash = False
state = {"phase": "boot", "success": False, "checks": {}, "fixture": "Synthetic keys/catalogs, no real provider requests or inference"}
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]


class MemoryKeychain:
    """Shows the form's Keychain option without touching a real Keychain."""

    def read(self, account, interactive=False):
        return None

    def write(self, account, secret, label, interactive=False):
        raise AssertionError("This suite never saves a fixture key")

    def delete(self, account, interactive=False):
        return False


class Layout(layout_double.UILayoutDouble):
    RECORDS = ("labels", "operators")

    def __init__(self):
        self.labels, self.operators = [], []
    def prop(self, obj, name, **kwargs):
        self.labels.append(name)
    def label(self, text="", **kwargs):
        self.labels.append(text)
    def operator(self, name, **kwargs):
        self.operators.append((name, kwargs))
        return SimpleNamespace()


def save():
    (OUT / "api-providers.json").write_text(json.dumps(state, indent=2))


def preferences_window():
    return next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))


def open_settings():
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.angelo.settings()


def close(window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.window_close()   # Takes an open dialog with it.


def google(group):
    return next(r for r in ui.connections().records.values() if r["provider"] == "GOOGLE" and r["group"] == group
                and r["method"] == "API_KEY")


def connection_form(**attributes):
    """A stand-in of the connection form. An unset ``key_source`` reads ``NEW`` (a key of its own), as an unset enum
    reads its first item on a real operator: a Google form offers "API key from" once another Google connection exists."""
    return qa_operator.stand_in(ui.ANGELO_OT_new_connection, layout=Layout(),
                                **{"key_source": shared_keys_ui.NEW, **attributes})


def capture(file, window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / file))


def event(kind, unicode=""):
    return SimpleNamespace(type=kind, value='PRESS', ctrl=False, oskey=False, shift=False, alt=False, unicode=unicode)


class QA_OT_page_dialog_probe(bpy.types.Operator):
    """A props dialog like AI Enhance's Configure…: Blender calls cancel() whenever its popup goes away unconfirmed."""
    bl_idname = "qa.page_dialog_probe"
    bl_label = "Dialog probe"
    value: bpy.props.IntProperty(name="Value", default=1)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, "value")

    def cancel(self, context):
        state["probe_cancels"] = state.get("probe_cancels", 0) + 1

    def execute(self, context):
        return {'FINISHED'}


def tick():
    try:
        window = bpy.context.window_manager.windows[0]
        if state["phase"] == "boot":
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            store = ui.connections()
            gpt = store.add("OPENAI", "SUBSCRIPTION", "ChatGPT", reuse_native=True)
            claude = store.add("ANTHROPIC", "SUBSCRIPTION", "Claude", reuse_native=True)
            ui._authentication.status.update(codex_subscription="Connected to ChatGPT", claude_subscription="Connected to Claude")
            gpt_models = normalize_models([{"model":"gpt-6.1-sol", "displayName":"GPT-6.1 Sol", "isDefault":True}])
            claude_models = normalize_models([{"value":"default", "displayName":"Default (recommended)"}, {"value":"sonnet", "displayName":"Sonnet"}], "claude")
            ui.catalogs().entries.update({gpt["id"]:gpt_models, claude["id"]:claude_models})
            controller = ui.get_controller()
            controller.choose_connection(gpt["mode"], ui.connection_settings(gpt["mode"], record=gpt), "", gpt["id"], gpt_models, "")
            for mode in sorted(HTTP_MODES):
                spec = SPECS[mode]
                result = __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', group=spec["group"], provider=spec["provider"],
                    method="API_KEY", connection_name=spec["name"], api_key=KEY)
                assert 'FINISHED' in result, mode
            state["phase"] = "checks"
            save()
            return .1
        if state["phase"] == "checks":
            if ui._api_connections.running:
                return .1
            store, controller = ui.connections(), ui.get_controller()
            genai = len([m for m in HTTP_MODES if SPECS[m]["group"] == GENAI])   # Every media provider, as added.
            state["checks"]["coding_and_genai_groups_are_separate"] = len([r for r in store.records.values() if r["group"] == CODING]) == CODING_CONNECTIONS and len(ui.generation_connections()) == genai
            layout = Layout()
            ui.draw_ai_providers(layout, bpy.context)
            state["checks"]["both_groups_draw_in_native_preferences"] = "Coding agents" in layout.labels and "Media generation" in layout.labels   # provider_registry titles
            state["checks"]["no_exclusive_provider_buttons"] = not any(name == "angelo.connection_use" for name,_ in layout.operators)
            probes = len([m for m in HTTP_MODES if SPECS[m]["probe"]])   # One free check per mode that has one.
            state["checks"]["nonbillable_checks_only"] = len(requests) == probes and all("generations" not in u for u in requests)
            state["checks"]["all_genai_service_bindings_created"] = all(ui.generation_binding(r["id"]).model_ids for r in ui.generation_connections())
            seedream = next(r for r in store.records.values() if r["mode"] == "seedream_api")
            state["checks"]["seedream_unverified_is_not_mislabeled_connected"] = not ui.generation_binding(seedream["id"]).verified and "deferred" in ui.connection_status(seedream)
            state["checks"]["no_keys_in_saved_metadata"] = KEY not in store.path.read_text()
            state["checks"]["keychain_off_for_automation"] = not saved_keys_ui.available()
            form = connection_form(connection_id="", group=GENAI, provider="GOOGLE", method="API_KEY",
                                   reuse_existing=True, advanced=False)
            ui.ANGELO_OT_new_connection.draw(form, bpy.context)
            disabled_form = form.layout.labels
            saved_keys_ui.start(ui, background=False, backend=MemoryKeychain())
            form.layout = Layout()
            ui.ANGELO_OT_new_connection.draw(form, bpy.context)
            state["checks"]["form_offers_remember_in_keychain"] = ("remember_key" in form.layout.labels
                and "remember_key" not in disabled_form and saved_keys_ui.unavailable_reason() == ""
                and any("ANGELO_DISABLE_KEYCHAIN" in text for text in disabled_form))
            choices = ui.model_choices(controller)
            state["checks"]["every_coding_provider_offered"] = {m["provider"] for m in choices} == {"codex_subscription", "claude_subscription"} | EXTERNAL_CODEX_MODES
            state["checks"]["genai_never_enters_chat_model_pool"] = not any(m["provider"] in HTTP_MODES - EXTERNAL_CODEX_MODES for m in choices)
            rows = model_rows(choices, "", controller.selection_connection_id)
            state["checks"]["one_default_not_two_recommended_options"] = sum(r["kind"] == "default" for r in rows) == 1 and not any(r["name"] == "Default (recommended)" for r in rows)
            state["checks"]["generic_provider_groups"] = sum(r["kind"] == "provider" for r in rows) == CODING_CONNECTIONS
            kimi = next(r for r in store.records.values() if r["mode"] == "kimi_api")
            conversation = controller.state["id"]
            __import__("angelo_agent.grant_guard").grant_guard.stand_in().select_model(connection_id=kimi["id"], model="kimi-k3")
            state["checks"]["model_routes_key_and_keeps_shared_session"] = controller.state["id"] == conversation and controller.state["connection_id"] == kimi["id"] and controller.api_key == KEY
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "layout"
            save()
            return .5
        if not qa_boot.started():
            return .1
        area = next(a for a in window.screen.areas if console.is_chat(a))
        region = next(r for r in area.regions if r.type == 'WINDOW')
        if state["phase"] == "layout":
            with bpy.context.temp_override(window=window, area=area, region=region):
                chat_view.show_popover(bpy.context, "models")
            state["phase"] = "models"
            return .4
        if state["phase"] == "models":
            capture("five-coding-provider-model-picker.png", window)
            with bpy.context.temp_override(window=window, area=area, region=region):
                view = chat_view._views[area.as_pointer()]
                for character in "MiniMax":
                    chat_view.popover_event(bpy.context, ui.get_controller(), view, event('TEXTINPUT', character), 0, 0)
                state["checks"]["native_picker_search_filters_provider"] = [r["id"] for r in view["popover"]["rows"] if r["kind"] == "model"] == ["MiniMax-M3"]
            state["phase"] = "search"
            return .4
        if state["phase"] == "search":
            capture("provider-model-search.png", window)
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            state["phase"] = "preferences"
            return .4
        if state["phase"] == "preferences":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            from angelo_agent import providers_page_host as page
            # The page's input starts from a timer after its first frame; a loaded machine takes longer than 0.4 s.
            state["page_waits"] = state.get("page_waits", 0) + 1
            if prefs.as_pointer() not in page._modals and state["page_waits"] < 30:
                return .2
            capture("coding-and-generative-connections.png", prefs)
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            drawn = page.states_drawn[-1] if page.states_drawn else {}
            plan = page.plan_for(region)
            state["checks"]["canvas_page_draws_both_groups"] = {"Coding agents", "Media generation"} <= set(drawn.get("sections", ()))
            state["checks"]["canvas_page_rows_and_chips"] = "ChatGPT" in drawn.get("rows", ()) and "Available in chat" in drawn.get("chips", ())
            state["checks"]["canvas_page_controls_hit_testable"] = plan is not None and {"add", "edit", "menu", "toggle"} <= {h[1] for h in plan.hits}
            state["checks"]["canvas_page_input_running"] = prefs.as_pointer() in page._modals
            # The hero is the raven banner when the art is present (an undownloaded LFS asset: the drawn mark).
            from angelo_agent import providers_page_model
            state["checks"]["canvas_page_hero_art"] = plan is not None and (
                any(op[0] == "paint" for op in plan.ops) if providers_page_model.banner_ready()
                else any(op[0] == "mark" for op in plan.ops))
            state["hero_banner"] = providers_page_model.banner_ready()
            state["images_at_open"] = sorted(image.name for image in bpy.data.images)
            # The page shows the art while its input runs: read with no datablock, the float copy gone once drawn.
            from angelo_agent import welcome_screen
            art = welcome_screen.BANNER_PICTURE
            state["checks"]["page_holds_its_art_while_open"] = art.failed or (
                art.ready and providers_page_model.PAGE in art.shown_by())
            state["checks"]["drawn_art_keeps_no_float_copy"] = not art.holds_pixels()
            with bpy.context.temp_override(window=prefs, area=area, region=region):
                for _ in range(3):
                    bpy.ops.view2d.scroll_down(page=True)
            state["phase"] = "page_lower"
            return .8
        if state["phase"] == "page_lower":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            capture("page-lower-sections.png", prefs)
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            import view_scroll   # Back to the page's top (view2d.scroll_up ignores a distance: view_scroll.py).
            view_scroll.up(prefs, area, region)
            bpy.context.preferences.view.language = "ar_EG"
            state["phase"] = "page_rtl"
            return .6
        if state["phase"] == "page_rtl":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            capture("page-arabic.png", prefs)
            bpy.context.preferences.view.language = "en_US"
            bpy.context.preferences.view.ui_scale = 1.7
            state["phase"] = "page_narrow_wait"
            return .6
        if state["phase"] == "page_narrow_wait":   # Language and scale changes reach the screen a frame later.
            state["phase"] = "page_narrow"
            return .6
        if state["phase"] == "page_narrow":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            capture("page-narrow-large-scale.png", prefs)
            bpy.context.preferences.view.ui_scale = 1.0
            from angelo_agent import providers_page_host as page, providers_sheet, ui_kit_field
            sheet = providers_sheet.ConnectionSheet(group=GENAI, provider="GOOGLE", method="API_KEY")
            page.open_sheet(sheet)
            sheet.act("field", "key", page._SheetHost(sheet))
            ui_kit_field.paste(sheet.key, "fake-sheet-key-0000")   # An obviously fake value: never a real key.
            state["sheet_records"] = sorted(ui.connections().records)
            state["phase"] = "sheet_typed"
            return .6
        if state["phase"] == "sheet_typed":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            from angelo_agent import providers_page_host as page, ui_kit_field
            sheet = page._sheet
            capture("sheet-add-key-masked.png", prefs)
            drawn = [op for op in (page._sheet_plan.ops if page._sheet_plan else ()) if op[0] in {"text", "mono"}]
            state["checks"]["sheet_open_over_preferences"] = sheet is not None and page._sheet_plan is not None
            state["checks"]["sheet_key_is_masked"] = (ui_kit_field.masked(sheet.key) != sheet.key.text
                                                      and not any("fake-sheet-key" in str(op[1]) for op in drawn))
            state["checks"]["sheet_one_primary"] = sum(1 for op in page._sheet_plan.ops if op[0] == "button" and op[3] == "primary") == 1
            sheet.key.text = ""
            ui_kit_field.paste(sheet.key, KEY)
            # The sheet as a form: its Save stands in for the person through the harness
            # (test_blender_grant_surfaces saves a key by the person's real presses and keys).
            __import__("angelo_agent.grant_guard").grant_guard.harness(sheet.act, "submit", None, page._SheetHost(sheet))
            state["checks"]["sheet_clears_the_key_on_submit"] = sheet.key.text == ""
            state["phase"] = "sheet_checked"
            return .8
        if state["phase"] == "sheet_checked":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            from angelo_agent import providers_page_host as page
            capture("sheet-after-connect.png", prefs)
            capture("sheet-after-connect.png", prefs)
            state["checks"]["sheet_shows_the_live_check"] = page._sheet is not None and page._sheet.status is not None
            created = sorted(set(ui.connections().records) - set(state["sheet_records"]))
            state["checks"]["sheet_creates_one_connection"] = len(created) == 1
            state["checks"]["no_keys_in_saved_metadata_after_sheet"] = KEY not in ui.connections().path.read_text()
            bpy.context.preferences.view.language = "ar_EG"
            state["phase"] = "sheet_rtl"
            return .6
        if state["phase"] == "sheet_rtl":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            from angelo_agent import providers_page_host as page
            capture("sheet-arabic.png", prefs)
            bpy.context.preferences.view.language = "en_US"
            page.close_sheet("cancel")
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            created = sorted(set(ui.connections().records) - set(state["sheet_records"]))
            state["menu_record"] = created[0] if created else ""
            if created:
                with bpy.context.temp_override(window=prefs, area=area, region=region):
                    for _ in range(8):
                        bpy.ops.view2d.scroll_up(page=True)
                    for _ in range(2):
                        bpy.ops.view2d.scroll_down(page=True)
                page.act("menu", created[0], prefs, area, region)
                page.act("menu_remove", created[0], prefs, area, region)
            state["phase"] = "menu_confirm"
            return .6
        if state["phase"] == "menu_confirm":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            from angelo_agent import providers_page_host as page
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            capture("menu-remove-confirm.png", prefs)
            state["phase"] = "menu_confirm_shown"
            return .5
        if state["phase"] == "menu_confirm_shown":   # Screenshots show the frame before: capture once more.
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            from angelo_agent import providers_page_host as page
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            capture("menu-remove-confirm.png", prefs)
            menu = page._menu or {}
            state["checks"]["menu_confirms_in_place"] = bool(menu.get("confirm")) and menu.get("plan") is not None and any(
                h[1] == "menu_remove_confirm" for h in menu["plan"].hits)
            state["checks"]["menu_remove_needs_the_confirmation"] = state["menu_record"] in ui.connections().records
            page.act("menu_remove_confirm", None, prefs, area, region)
            state["checks"]["menu_remove_removes"] = state["menu_record"] not in ui.connections().records
            with bpy.context.temp_override(window=prefs, area=area, region=region):
                for _ in range(10):
                    bpy.ops.view2d.scroll_up(page=True)
            state["phase"], state["shots"] = "hero_shots", 0
            return .8
        if state["phase"] == "hero_shots":
            # The top of the page (hero, team, coding agents) in English, then Arabic. A Preferences screenshot can
            # show an earlier frame, so each state is captured again a beat later and the last capture is kept.
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            area.tag_redraw()
            shot = state["shots"]
            capture("page-top-en.png" if shot < 3 else "page-top-ar.png", prefs)
            state["shots"] = shot + 1
            if shot == 2:
                bpy.context.preferences.view.language = "ar_EG"
            if shot < 5:
                return .7
            bpy.context.preferences.view.language = "en_US"
            # Draws, scrolls, a UI-scale relayout, a language switch, the sheet and the menu: no image datablock added
            # or removed (the art is read with imbuf). Another surface letting the art go leaves the page its art.
            from angelo_agent import providers_page_model, welcome_screen
            state["checks"]["page_never_adds_or_removes_images"] = sorted(i.name for i in bpy.data.images) == state["images_at_open"]
            art = welcome_screen.BANNER_PICTURE
            held = art.ready
            art.show("qa_other_surface")
            art.hide("qa_other_surface")
            state["checks"]["another_surface_closing_keeps_the_page_art"] = art.ready == held
            with bpy.context.temp_override(window=prefs):
                bpy.ops.wm.window_close()
            state["phase"] = "reopen"
            return .5
        if state["phase"] == "reopen":
            from angelo_agent import welcome_screen
            art = welcome_screen.BANNER_PICTURE   # Preferences closed: the art went with the page (no other surface).
            state["checks"]["closing_preferences_lets_the_art_go"] = bool(art.shown_by()) or not art.ready
            window = bpy.context.window_manager.windows[0]
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            state["phase"] = "reopened"
            return .8
        if state["phase"] == "reopened":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            from angelo_agent import providers_page_host as page, providers_page_model
            capture("page-reopened.png", prefs)
            state["checks"]["reopen_adds_or_removes_no_images"] = sorted(i.name for i in bpy.data.images) == state["images_at_open"]
            plan = page.plan_for(region)
            state["checks"]["reopened_page_keeps_its_hero"] = plan is not None and any(
                op[0] == ("paint" if providers_page_model.banner_ready() else "mark") for op in plan.ops)
            # A dialog opened over the page (as AI Enhance's Configure… is) stays open across redraws and a scroll.
            bpy.utils.register_class(QA_OT_page_dialog_probe)
            with bpy.context.temp_override(window=prefs, area=area, region=region):
                bpy.ops.qa.page_dialog_probe('INVOKE_DEFAULT')
            state["probe_cancels"], state["probe_steps"] = 0, 0
            state["phase"] = "probe"
            return .4
        if state["phase"] == "probe":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            from angelo_agent import preferences_ui
            step = state["probe_steps"]
            preferences_ui.redraw()
            with bpy.context.temp_override(window=prefs):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            if step == 2:
                with bpy.context.temp_override(window=prefs, area=area, region=region):
                    bpy.ops.view2d.scroll_down(page=True)
            if step == 3:
                capture("dialog-over-page.png", prefs)
            state["probe_steps"] = step + 1
            if step < 5:
                return .3
            state["checks"]["dialog_over_page_stays_open"] = state["probe_cancels"] == 0
            state["checks"]["page_never_adds_or_removes_images_with_a_dialog"] = sorted(i.name for i in bpy.data.images) == state["images_at_open"]
            with bpy.context.temp_override(window=prefs):
                bpy.ops.wm.window_close()   # Takes the dialog with it: cancel() runs, so the probe sees closing.
            state["phase"] = "probe_closed"
            return .4
        if state["phase"] == "probe_closed":
            state["checks"]["dialog_close_is_detected"] = state["probe_cancels"] == 1
            bpy.utils.unregister_class(QA_OT_page_dialog_probe)
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.angelo.settings()
            state["phase"] = "probe_reopened"
            return .8
        if state["phase"] == "probe_reopened":
            prefs = next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            with bpy.context.temp_override(window=prefs, area=area):
                __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection(
                    'INVOKE_DEFAULT', group=GENAI, provider="GOOGLE", method="API_KEY")
            state["phase"] = "form"
            return .4
        if state["phase"] == "form":
            prefs = preferences_window()
            capture("new-generative-api-key-form.png", prefs)
            # One Google key for Images and the coding agent (shared_keys): the Edit form of the coding connection,
            # first as it opens (its own key), then with the Images connection's key chosen.
            coding = google("CODING")
            images = google("GENAI")
            state["shared"] = {"coding": coding["id"], "images": images["id"]}
            form = connection_form(connection_id=coding["id"], group=CODING, provider="GOOGLE", method="API_KEY",
                                   reuse_existing=True, advanced=False)
            ui.ANGELO_OT_new_connection.draw(form, bpy.context)
            state["checks"]["coding_form_offers_the_images_key"] = (
                "key_source" in form.layout.labels and "api_key" in form.layout.labels
                and any(item[0] == images["id"] for item in shared_keys_ui.key_source_items(form, bpy.context)))
            close(prefs)
            state["phase"] = "shared_open"
            return .4
        if state["phase"] in ("shared_open", "shared_chosen_open"):
            open_settings()
            state["phase"] = state["phase"].replace("_open", "_form")
            return .8
        if state["phase"] in ("shared_form", "shared_chosen_form"):
            prefs = preferences_window()
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            # key_source's items depend on group and provider, which RNA assigns first (definition order).
            choice = {} if state["phase"] == "shared_form" else {"key_source": state["shared"]["images"]}
            with bpy.context.temp_override(window=prefs, area=area):
                __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection(
                    'INVOKE_DEFAULT', connection_id=state["shared"]["coding"], group=CODING, provider="GOOGLE",
                    method="API_KEY", **choice)
            state["phase"] = state["phase"].replace("_form", "_capture")
            return .4
        if state["phase"] in ("shared_capture", "shared_chosen_capture"):
            prefs = preferences_window()
            capture("gemini-key-from-own.png" if state["phase"] == "shared_capture" else "gemini-key-from-images.png", prefs)
            close(prefs)
            state["phase"] = "shared_chosen_open" if state["phase"] == "shared_capture" else "shared_link"
            return .4
        if state["phase"] == "shared_link":
            store = ui.connections()
            coding, images = (store.records[state["shared"][k]] for k in ("coding", "images"))
            result = __import__("angelo_agent.grant_guard").grant_guard.stand_in().new_connection('EXEC_DEFAULT', connection_id=coding["id"], group=CODING,
                                                   provider="GOOGLE", method="API_KEY", key_source=images["id"])
            label = saved_keys_ui.storage_label(coding, bool(ui._memory_keys.get(coding["id"])))
            state["checks"]["coding_connection_uses_the_images_key"] = (
                'FINISHED' in result and coding.get("key_from") == images["id"]
                and ui._memory_keys.get(coding["id"]) == ui._memory_keys.get(images["id"]) == KEY
                and label is not None and images["name"] in str(label))
            state["checks"]["linked_key_never_saved_in_metadata"] = KEY not in store.path.read_text()
            # A build whose agent runtime lacks the Gemini wire never offers the coding connection (provider_registry
            # .available): the pin's wire list decides, and the Images connection stays offered either way.
            pinned = agent_runtime.PIN.get("wire_apis")
            offered = {item[0] for item in provider_registry.provider_choice_items(CODING)}
            agent_runtime.PIN["wire_apis"] = []
            try:
                without = {item[0] for item in provider_registry.provider_choice_items(CODING)}
                images_without = {item[0] for item in provider_registry.provider_choice_items(GENAI)}
            finally:
                agent_runtime.PIN["wire_apis"] = pinned
            state["checks"]["google_coding_offered_only_with_the_wire"] = (
                "GOOGLE" in offered and "GOOGLE" not in without and "GOOGLE" in images_without)
            open_settings()
            state["phase"] = "shared_page"
            return .8
        if state["phase"] == "shared_page":
            capture("ai-providers-shared-google-key.png", preferences_window())
            state["checks"]["no_chat_draw_error"] = chat_view.last_error is None
            state["checks"]["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            save()
            print("ANGELO_API_PROVIDERS_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
