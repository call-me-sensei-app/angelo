# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``test_blender_settings_import.py``: one phase per window process (see that file).

Each phase is a generator of steps: a number waits that many seconds, an ``Until`` waits for a condition (or fails
the phase when it times out). Clicks are real pointer events (``Window.event_simulate``) on the cards' own hit
geometry, mirrored back in Arabic as a person's pointer would be. Checks are recorded, never asserted one by one,
so a receipt lists every one; anything unexpected ends the phase with its traceback in ``<phase>.json``.
"""

import json
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
OUT = Path(ARGS[ARGS.index("--fixture-root") + 1])
SANDBOX = ARGS[ARGS.index("--sandbox") + 1]
report = {"phase": PHASE, "checks": {}, "screenshots": [], "success": False}


class Until:
    def __init__(self, condition, what, limit=60):
        self.condition, self.what, self.limit = condition, what, limit


def check(name, condition):
    report["checks"][name] = bool(condition)


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    report["screenshots"].append(str(path))


def active_plan():
    from angelo_agent import card_host
    return card_host._active.plan if card_host._active else None


def card_ready(kind):
    from angelo_agent import card_host
    return card_host.is_active(kind) and card_host._active.plan is not None


def laid_out_with_live_fonts():
    """The active card's plan, as last drawn, equals a layout made now with Angelo's fonts as they are, at the same
    size, scale and pointer state, and the card font measures text. A plan laid out while a font reload left a font
    measuring 0 (dac637995dc) differs from it, whatever ``card_host`` puts in its cache key and in which order."""
    from angelo_agent import card_host
    state = card_host._active
    area = next((a for w in bpy.context.window_manager.windows for a in w.screen.areas
                 if a.as_pointer() == state.area), None) if state else None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if region is None or state.plan is None or card_host._view is None:
        return False
    view = card_host._view
    fresh = state.card.layout(view, region.width, region.height, bpy.context.preferences.system.ui_scale, state.ui)
    return fresh == state.plan and view.measure("M", 13, view._font) > 0


def card_actions():
    plan = active_plan()
    return [(a, v) for _, a, v in plan.hits] if plan else []


def plan_text():
    plan = active_plan()
    return " ".join(op[1].strip() for op in plan.ops if op[0] in {"text", "mono"}) if plan else ""


def _press(x, y):
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def click_card(action, value=None):
    """Click the visible card's own control, in window coordinates (mirrored back in a right-to-left language)."""
    from angelo_agent import canvas_rtl, card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in reversed(active.plan.hits) if a == action and (value is None or v == value))
    x = canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width)
    _press(int(region.x + x), int(region.y + rect[1] + rect[3] / 2))


def click_welcome(action):
    from angelo_agent import intro
    area = next(a for a in window().screen.areas if a.as_pointer() == intro._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action)
    from angelo_agent import canvas_rtl
    x = canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width)
    _press(int(region.x + x), int(region.y + rect[1] + rect[3] / 2))


def preferences_window():
    return next((w for w in bpy.context.window_manager.windows
                 if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)


def folders():
    return {kind: Path(bpy.utils.user_resource(kind)) for kind in ("CONFIG", "SCRIPTS", "DATAFILES", "EXTENSIONS")}


def profile():
    return json.loads((OUT / SANDBOX / "profile.json").read_text())


def theme_is_light(named=True):
    """The light look is in the theme (and, in the session that chose it, the preset menu still names it)."""
    light = bpy.context.preferences.themes[0].user_interface.wcol_regular.inner[0] > 0.5
    return light and (not named or bpy.types.USERPREF_MT_interface_theme_presets.bl_label == "Angelo Light")


def check_agent(prefix=""):
    """Angelo's agent add-on is enabled and listed, its chat is open and draws, Director is on."""
    from angelo_agent import chat_view, console, director
    prefs = bpy.context.preferences
    chats = [a for a in window().screen.areas if console.is_chat(a)]
    check(prefix + "angelo_agent_enabled_and_listed", addon_utils.check("angelo_agent") == (True, True)
          and "angelo_agent" in prefs.addons and hasattr(bpy.types, "ANGELO_OT_open_chat"))
    check(prefix + "agent_chat_open_and_drawn",
          bool(chats) and bool(chat_view._views.get(chats[0].as_pointer(), {}).get("hits")))
    check(prefix + "director_mode", director.is_director(window()))


def check_imported(prefix=""):
    """Every kind of setting the fixture made in Blender's folder is in Angelo's, working."""
    prefs, places, made = bpy.context.preferences, folders(), profile()
    config = places["CONFIG"]
    saved = (config / "userpref.blend").read_bytes() if (config / "userpref.blend").is_file() else b""
    window_keys = bpy.context.window_manager.keyconfigs.user.keymaps["Window"].keymap_items
    check(prefix + "preference_undo_steps", prefs.edit.undo_steps == 77)
    check(prefix + "preference_python_tooltips", prefs.view.show_tooltips_python)
    check(prefix + "keymap_item_f7_search", any(k.idname == "wm.search_menu" and k.type == 'F7' for k in window_keys))
    check(prefix + "user_addon_enabled", addon_utils.check("switcher_tool") == (True, True)
          and hasattr(bpy.types, "SWITCHER_OT_hello"))
    entry = prefs.addons.get("switcher_tool")
    check(prefix + "user_addon_preferences", entry is not None and entry.preferences.greeting == "from Blender")
    check(prefix + "user_addon_config_folder", (config / "switcher_tool/settings.json").is_file())
    check(prefix + "mcp_extension_enabled", addon_utils.check("bl_ext.user_default.mcp") == (True, True)
          and hasattr(bpy.types, "MCP_FIXTURE_OT_ping"))
    check(prefix + "extension_user_folder", (places["EXTENSIONS"] / ".user/user_default/mcp/server.json").is_file())
    check(prefix + "asset_library", any(library.name == "Switcher Assets"
                                        and Path(library.path) == Path(made["library"])
                                        for library in prefs.filepaths.asset_libraries))
    check(prefix + "quick_favorite_saved", b"MESH_OT_primitive_monkey_add" in saved and b"Switcher Monkey" in saved)
    check(prefix + "operator_preset", any((Path(p) / "Switcher_Cube.py").is_file()
                                          for p in bpy.utils.preset_paths("operator/mesh.primitive_cube_add")))
    check(prefix + "app_template", any((Path(p) / "switcher_template").is_dir() for p in bpy.utils.app_template_paths()))
    check(prefix + "matcap", any(light.name == "switcher_matcap.png" for light in prefs.studio_lights))
    check(prefix + "startup_script_copied", (places["SCRIPTS"] / "startup/switcher_startup.py").is_file())
    bookmarks = (config / "bookmarks.txt").read_text() if (config / "bookmarks.txt").is_file() else ""
    recent = (config / "recent-files.txt").read_text() if (config / "recent-files.txt").is_file() else ""
    check(prefix + "bookmark", made["bookmarks"] in bookmarks)
    check(prefix + "recent_file", made["scene"] in recent)
    check(prefix + "start_up_scene_left_out", not (config / "startup.blend").exists()
          and "SwitcherMonkey" not in bpy.data.objects)
    check(prefix + "early_angelo_state_left_out", not (config / "angelo-agent/sessions/early.json").exists())
    check(prefix + "angelo_saved_in_preferences", b"angelo_agent" in saved)


def phase_card():
    from angelo_agent import intro, settings_import_card as words, settings_import_ui, splash_setup
    from angelo_agent.ui_text import iface_
    prefs = bpy.context.preferences
    yield Until(lambda: card_ready("setup"), "the setup card")
    yield 0.6
    from angelo_agent import card_host
    card = card_host._active.card
    check("card_found_blender_5_2", card.source is not None
          and (card.source.app, card.source.version_label) == ("Blender", "5.2"))
    check("card_says_from_blender_5_2", "from Blender 5.2" in plan_text())
    check("import_can_be_turned_on", ("import", None) in card_actions())
    capture("setup-card-en.png")
    click_card("language_menu")
    yield 0.5
    arabic = next(v for a, v in card_actions() if a == "language" and v.startswith("ar"))
    click_card("language", arabic)
    yield 0.8
    check("arabic_chosen", prefs.view.language.startswith("ar"))
    capture("setup-card-ar.png")
    click_card("look", "light")
    yield 0.8
    click_card("import")
    yield 0.6
    check("import_turned_on", card.use_blender)
    capture("setup-card-ar-light-import.png")
    click_card("continue")
    yield Until(lambda: settings_import_ui.last_outcome is not None, "the import")
    outcome = report["outcome"] = settings_import_ui.last_outcome
    check("outcome_from_blender_5_2", (outcome.get("app"), outcome.get("version")) == ("Blender", "5.2"))
    check("outcome_clean", not outcome.get("exception") and outcome["failed"] == [] and outcome["errors"] == [])
    check("outcome_says_startup_scripts_wait_for_a_restart", outcome.get("restart") is True)
    yield Until(lambda: intro.is_open() and "plan" in intro._open, "the welcome")
    yield 0.8
    capture("welcome-after-import-ar.png")
    report["sequence"] = list(splash_setup.sequence)
    check("setup_then_welcome_once", splash_setup.sequence[-2:] == ["setup", "welcome"] and intro.opened == 1)
    check_imported()
    check_agent()
    check("language_kept", prefs.view.language.startswith("ar"))
    check("look_kept", theme_is_light())
    click_welcome("start")
    yield Until(lambda: card_ready("notice"), "the result notice")
    yield 0.6
    notice = card_host._active.card
    check("notice_names_blender_5_2", notice.title == iface_(words.DONE_TITLE, app="Blender", version="5.2"))
    check("notice_is_calm", notice.icon == "check" and ("undo", None) not in card_actions())
    capture("import-notice-ar.png")
    view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in view.regions if r.type == 'WINDOW')
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=region.x + region.width // 3,
                            y=region.y + region.height // 2)
    with bpy.context.temp_override(window=window(), area=view, region=region):
        bpy.ops.wm.call_menu(name="SCREEN_MT_user_menu")
    yield 0.8
    capture("quick-favorites.png")
    window().event_simulate(type='ESC', value='PRESS', x=10, y=10)
    window().event_simulate(type='ESC', value='RELEASE', x=10, y=10)
    yield 0.3


def phase_restart():
    from angelo_agent import splash_setup
    prefs = bpy.context.preferences
    yield Until(lambda: card_ready("start"), "the start panel")
    yield 0.6
    check("start_panel_not_setup", "setup" not in splash_setup.sequence)
    check_imported("persisted_")
    check_agent()
    check("startup_script_ran", hasattr(bpy.types, "SWITCHER_OT_startup_marker"))
    check("language_kept", prefs.view.language.startswith("ar"))
    check("look_kept", theme_is_light(named=False))
    capture("restart-ar.png")


def phase_none():
    from angelo_agent import card_host, intro, setup_card, splash_setup
    from angelo_agent.ui_text import iface_
    yield Until(lambda: card_ready("setup"), "the setup card")
    yield 0.6
    card = card_host._active.card
    check("no_source_found", card.source is None and card.source_reason == "none")
    check("row_explains", iface_(setup_card.IMPORT_NONE) in plan_text())
    check("row_cannot_be_turned_on", ("import", None) not in card_actions())
    check("menu_entry_disabled", not bpy.ops.angelo.import_blender_settings.poll())
    capture("setup-card-none.png")
    click_card("continue")
    yield Until(lambda: splash_setup.preferences_saved() and intro.is_open() and "plan" in intro._open, "the welcome")
    check_agent()
    click_welcome("start")   # The welcome holds every event over the 3D view, quitting's included.
    yield Until(lambda: not intro.is_open(), "the welcome to close")


def phase_broken():
    from angelo_agent import card_host, intro, settings_import_ui
    yield Until(lambda: card_ready("setup"), "the setup card")
    yield 0.6
    click_card("import")
    yield 0.4
    click_card("continue")
    yield Until(lambda: settings_import_ui.last_outcome is not None, "the import")
    outcome = report["outcome"] = settings_import_ui.last_outcome
    check("broken_addon_named", outcome["failed"] == ["Broken Tool"] and not outcome.get("exception"))
    check("good_addon_loaded", addon_utils.check("switcher_tool") == (True, True))
    yield Until(lambda: intro.is_open() and "plan" in intro._open, "the welcome")
    check_agent()
    click_welcome("start")
    yield Until(lambda: card_ready("notice"), "the result notice")
    yield 0.6
    notice = card_host._active.card
    check("notice_names_the_broken_addon_calmly", "Broken Tool" in notice.body and notice.icon == "alert")
    capture("import-notice-broken.png")


def phase_broken_restart():
    yield Until(lambda: card_ready("start"), "the start panel")
    yield 0.6
    check_agent()
    check("broken_addon_listed_not_loaded", "broken_tool" in bpy.context.preferences.addons
          and not addon_utils.check("broken_tool")[1])
    check("good_addon_loaded", addon_utils.check("switcher_tool") == (True, True))
    capture("broken-restart.png")


def phase_menu():
    from angelo_agent import card_host, intro, settings_import_apply as apply, settings_import_ui, splash_setup
    prefs = bpy.context.preferences
    yield Until(lambda: card_ready("setup"), "the setup card")
    yield 0.6
    click_card("continue")   # An existing Angelo user: saved preferences, no import.
    yield Until(lambda: splash_setup.preferences_saved() and intro.is_open() and "plan" in intro._open, "the welcome")
    click_welcome("start")
    yield Until(lambda: not intro.is_open(), "the welcome to close")
    yield 1.0
    prefs.edit.undo_steps = 55   # Angelo's own setting, which the import replaces and its undo puts back.
    bpy.ops.wm.save_userpref()
    check("menus_offer_the_import", all(settings_import_ui.draw_menu in getattr(bpy.types, name).draw._draw_funcs
                                        for name in settings_import_ui.MENUS))
    check("menu_entry_enabled", bpy.ops.angelo.import_blender_settings.poll())
    check("no_undo_before_an_import", not bpy.ops.angelo.undo_settings_import.poll())
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.userpref_show()   # Chosen from Preferences > Save & Load, as a person would.
    yield Until(preferences_window, "the Preferences window")
    yield 0.4
    prefs_window = preferences_window()
    area = next(a for a in prefs_window.screen.areas if a.type == 'PREFERENCES')
    with bpy.context.temp_override(window=prefs_window, area=area):
        bpy.ops.angelo.import_blender_settings()
    yield Until(lambda: card_ready("settings_import"), "the confirmation card")
    yield 0.6
    check("preferences_window_gives_way_to_the_card", preferences_window() is None)
    text = plan_text()
    check("confirmation_says_where_from", "From Blender 5.2" in text and "Application Support/Blender/5.2" in text)
    check("confirmation_says_what_it_replaces_and_keeps", all(w in text for w in ("Replaces", "Keeps", "Quick Favorites")))
    capture("menu-confirm-en.png")
    language = prefs.view.language
    prefs.view.language = next(i.identifier for i in prefs.view.bl_rna.properties["language"].enum_items
                               if i.identifier.startswith("ar"))
    yield 1.0
    capture("menu-confirm-ar.png")
    prefs.view.language = language
    yield 0.8
    click_card("confirm")
    yield Until(lambda: settings_import_ui.last_outcome is not None, "the import")
    outcome = report["outcome"] = settings_import_ui.last_outcome
    check("outcome_clean", not outcome.get("exception") and outcome["failed"] == [] and outcome["errors"] == [])
    yield Until(lambda: card_ready("notice"), "the result notice")
    yield 0.6
    check_imported()
    check_agent()
    check("language_kept", prefs.view.language == language)
    check("notice_offers_undo", ("undo", None) in card_actions())
    # The theme put back reloads Blender's fonts under the notice: the plan on screen is the one live fonts lay out.
    check("notice_laid_out_with_live_fonts", laid_out_with_live_fonts())
    check("menu_offers_undo", bpy.ops.angelo.undo_settings_import.poll())
    capture("menu-notice.png")
    click_card("undo")
    yield Until(lambda: card_ready("settings_import") and card_host._active.card.mode == "undo", "the undo card")
    yield 0.6
    capture("menu-undo-confirm.png")
    settings_import_ui.last_outcome = None
    click_card("confirm")
    yield Until(lambda: (settings_import_ui.last_outcome or {}).get("undo"), "the undo")
    yield 0.6
    undone = report["undo"] = settings_import_ui.last_outcome
    places = folders()
    window_keys = bpy.context.window_manager.keyconfigs.user.keymaps["Window"].keymap_items
    check("undo_clean", undone["errors"] == [])
    check("undo_restores_angelos_preference", prefs.edit.undo_steps == 55)
    check("undo_removes_the_addon", not addon_utils.check("switcher_tool")[1]
          and not (places["SCRIPTS"] / "addons/switcher_tool").exists())
    check("undo_removes_the_keymap_item", not any(k.type == 'F7' and k.idname == "wm.search_menu" for k in window_keys))
    check("undo_removes_the_extension", not (places["EXTENSIONS"] / "user_default/mcp").exists())
    check("undo_backup_gone", not apply.can_undo())
    check_agent("after_undo_")
    capture("menu-undone.png")


def phase_upgrade():
    from angelo_agent import card_host, intro, settings_import_ui
    yield Until(lambda: card_ready("setup"), "the setup card")
    yield 0.6
    card = card_host._active.card
    check("card_found_angelo_5_1", card.source is not None
          and (card.source.app, card.source.version_label) == ("Angelo", "5.1"))
    text = plan_text()
    check("card_says_earlier_angelo_5_1", "Use my earlier Angelo settings" in text and "from Angelo 5.1" in text)
    capture("setup-card-upgrade.png")
    click_card("import")
    yield 0.4
    click_card("continue")
    yield Until(lambda: settings_import_ui.last_outcome is not None, "the import")
    outcome = report["outcome"] = settings_import_ui.last_outcome
    check("blenders_own_import_ran", outcome.get("app") == "Angelo" and outcome.get("reloaded_layout")
          and not outcome.get("exception"))
    yield Until(lambda: intro.is_open() and "plan" in intro._open, "the welcome", limit=90)
    yield 0.6
    prefs = bpy.context.preferences
    check("preference_undo_steps", prefs.edit.undo_steps == 77)
    check("user_addon_enabled", addon_utils.check("switcher_tool") == (True, True))
    report["start_up_scene_loaded"] = "SwitcherMonkey" in bpy.data.objects   # Blender's import: when unchanged.
    check_agent()
    from angelo_agent import ui
    check("chat_follows_the_reloaded_file", not any("Document changed" in str(message.get("text"))
                                                    for message in ui.get_controller().state["messages"]))
    click_welcome("start")
    yield Until(lambda: card_ready("notice"), "the result notice")
    yield 0.6
    check("notice_names_angelo_5_1", "Angelo 5.1" in card_host._active.card.title)
    capture("import-notice-upgrade.png")


PHASES = {"card": phase_card, "restart": phase_restart, "none": phase_none, "broken": phase_broken,
          "broken-restart": phase_broken_restart, "menu": phase_menu, "upgrade": phase_upgrade}


def finish(error=None):
    if error:
        report["error"] = error.replace("Traceback (most recent", "Trace (most recent")   # The host scans for it.
    report["success"] = not error and bool(report["checks"]) and all(report["checks"].values())
    report["failed_checks"] = [name for name, ok in report["checks"].items() if not ok]
    (OUT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_SETTINGS_IMPORT_PHASE", json.dumps({"phase": PHASE, "success": report["success"]}), flush=True)
    bpy.ops.wm.quit_blender()


def start():
    steps, state = PHASES[PHASE](), {"until": None, "since": 0.0}
    started = time.monotonic()

    def tick():
        try:
            assert time.monotonic() - started < 200, "Phase timed out"
            until = state["until"]
            if until is not None:
                if not until.condition():
                    assert time.monotonic() - state["since"] < until.limit, "Timed out waiting for " + until.what
                    return 0.2
                state["until"] = None
            step = next(steps)
            if isinstance(step, Until):
                state.update(until=step, since=time.monotonic())
                return 0.2
            return step
        except StopIteration:
            finish()
        except Exception:
            finish(traceback.format_exc())
        return None

    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)   # Blender's import may reload the file.
