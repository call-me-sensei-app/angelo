# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The phases of ``test_blender_first_launch_frames.py``, one window process each (Blender only; see that file).

Steps and helpers are ``first_launch_frames_probe``'s. Every phase starts sampling frames at the first main-loop turn
unless it must act first (``finder`` captures that frame, then opens a file as the Finder would).
"""

import os
import sys

import bpy

import splash_frames
from first_launch_frames_probe import (BLEND, EXPECTED_FAILURE, Sample, Until, after_launch, analyse, capture,
                                       card_actions, card_point, card_ready, check, click_card, control_rect,
                                       finish_welcome, frame, key, light, mod, no_form, own_frames, report, sequence,
                                       window)


def first_launch_checks(prefix=""):
    """The splash showed its art (Angelo's stand-ins or menus drew it, nothing else) over Angelo's cover, never the
    stock Layout, and the setup card followed."""
    no_form(prefix)
    own_frames(prefix)
    first = report["frames"][0]
    check(prefix + "first_frame_is_the_splash_art", first["form_rows"] < splash_frames.FORM_ROWS and (
        (first["stand_in_draws_pending"] or 0) > 0 or (first["splash_drawn"] or 0) > 0))
    check(prefix + "sequence_starts_with_the_splash", sequence()[:2] == ["splash", "setup"])


def phase_warm():
    """A first launch: the splash's art, the setup card over the showcase, the Look control, Continue, the welcome."""
    check("stand_ins_own_the_splash_before_the_add_on", set(report["at_python"]["menus"].values())
          == {"angelo_default_layout"})
    yield Sample(5.0)
    analyse()
    yield Until(lambda: card_ready("setup"), "the setup card")
    first_launch_checks()
    yield from showcase_under_card()
    yield from look_control()
    yield from finish_welcome()


def showcase_under_card():
    from angelo_agent import connect_hero
    yield Until(connect_hero.is_shown, "the showcase", limit=20)
    yield 0.4

    def order():
        names = [op.bl_idname.lower() for op in window().modal_operators]
        hero = next((i for i, n in enumerate(names) if "connect_hero" in n), None)
        card = next((i for i, n in enumerate(names) if "card_host" in n), None)
        return names, hero, card
    names, hero, card = order()
    report["modal_order_at_card"] = names
    if hero is None or card is None or hero > card:
        # The showcase opened first: show it again, so its input handler sits above the card's (the order that
        # took the card's clicks before this fix).
        connect_hero._release()
        yield Until(connect_hero.is_shown, "the showcase again", limit=10)
        yield 0.4
        names, hero, card = order()
    report["modal_order_tested"] = names
    check("showcase_input_handler_above_the_card", hero is not None and card is not None and hero < card)
    check("card_paints_the_showcase_beneath", connect_hero._yields(connect_hero._state["area"]))
    path = capture("showcase-under-card")
    x, y = card_point("continue")
    report["continue_pixel"] = colour = splash_frames.pixel_at(path, x, y)
    check("card_drawn_over_the_showcase", all(abs(c - t * 255) < 40 for c, t in zip(colour, (0.169, 0.769, 0.749))))
    click_card("look", "light")
    yield Until(light, "the light look from a click over the showcase", limit=5)
    click_card("look", "dark")
    yield Until(lambda: not light(), "the dark look again", limit=5)
    check("card_takes_clicks_with_the_showcase_above", True)


def look_control():
    from angelo_agent import card_host
    yield 0.6
    for language in ("en", "ar"):
        if language == "ar":
            click_card("language_menu")
            yield 0.5
            arabic = next(v for a, v in card_actions() if a == "language" and v.startswith("ar"))
            click_card("language", arabic)
            yield Until(lambda: bpy.context.preferences.view.language.startswith("ar") and card_ready("setup"),
                        "Arabic")
            yield 0.8
        for look in ("dark", "light"):
            if look == "light":
                click_card("look", "light")
                yield Until(light, "the light look", limit=5)
                yield 0.6
            name = "look-%s-%s" % (look, language)
            report["rects"][name] = control_rect("look")
            capture(name)
            check(name + "_selected", card_host._active.card.effects.look() == look)
        click_card("look", "dark")
        yield Until(lambda: not light(), "the dark look", limit=5)
        yield 0.4
    click_card("language_menu")
    yield 0.5
    click_card("language", "en_US")
    yield Until(lambda: bpy.context.preferences.view.language == "en_US", "English")
    yield 0.6


def phase_second():
    """The second launch: the add-on loads first, the start panel follows; Help > Splash Screen; Preferences Themes."""
    from_python = report["at_python"]["menus"]
    check("add_on_owns_the_splash_menus_before_it_opens", set(from_python.values()) == {"angelo_agent.splash_setup"})
    yield Sample(3.0)
    analyse()
    yield Until(lambda: card_ready("start"), "the start panel")
    no_form()
    layout = mod("angelo_default_layout")
    check("stand_ins_not_needed", not any(cls.is_registered for cls in layout.STAND_INS))
    check("sequence_is_splash_then_start", sequence() == ["splash", "start"])
    capture("start-panel")
    key('ESC')
    yield Until(lambda: not mod("angelo_agent.card_host").is_active(), "the start panel to close")
    yield 0.5
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.splash('INVOKE_DEFAULT')   # Help > Splash Screen.
    yield Sample(2.0, tag="help")
    analyse()
    yield Until(lambda: card_ready("start"), "the start panel from Help > Splash Screen")
    no_form("help_splash_")
    check("help_splash_shows_the_start_panel", sequence() == ["splash", "start", "start"])
    capture("help-splash")
    key('ESC')
    yield Until(lambda: not mod("angelo_agent.card_host").is_active(), "the start panel to close")
    yield from preferences_themes()


def preferences_themes():
    from angelo_agent import setup_card
    files = sorted(name for folder in bpy.utils.preset_paths("interface_theme") for name in os.listdir(folder)
                   if name.endswith(".xml"))
    report["theme_presets"] = files
    check("theme_presets_are_angelo_dark_and_light", {"Angelo_Dark.xml", "Angelo_Light.xml"} <= set(files)
          and not any(name.startswith("Blender_") for name in files))
    area = max(window().screen.areas, key=lambda a: a.width * a.height)
    kept = area.ui_type
    bpy.context.preferences.active_section = 'THEMES'
    area.ui_type = 'PREFERENCES'
    for look in ("dark", "light"):
        if look == "light":
            yield from preset_first_frame("light")
        yield Until(lambda: area.type == 'PREFERENCES', "Preferences in the largest area", limit=10)
        yield 1.0
        report["preferences_area_" + look] = {"area": area.type, "card": card_kind()}
        filepath = bpy.context.preferences.themes[0].filepath
        label = bpy.path.display_name(os.path.basename(filepath)) if filepath else "Angelo Dark"
        report["preferences_label_" + look] = label
        check("preferences_names_the_%s_look" % look, label == ("Angelo Dark" if look == "dark" else "Angelo Light"))
        path = capture("preferences-themes-" + look)
        # A theme reloads Blender's fonts: the chat's connect cards still draw their text (pixels, each card).
        try:
            report["chat_text_" + look] = chat_text_pixels(path)
        except Exception as error:   # Recorded as a failed check; the rest of the phase still reports.
            report["chat_text_" + look] = {"ok": False, "error": repr(error)}
        check("chat_cards_show_text_in_the_%s_look" % look, report["chat_text_" + look]["ok"])
    yield from preset_first_frame("dark")
    area.ui_type = kept
    yield 0.5


def preset_first_frame(look):
    """Apply a look's theme preset as Preferences > Themes does, and capture the very next frame: the chat's connect
    cards draw their text in it (Blender reloads its interface fonts for a preset; a font Angelo's chat loaded must
    survive that, not come back at font_watch's next poll)."""
    from angelo_agent import setup_card
    with bpy.context.temp_override(window=window()):
        bpy.ops.script.execute_preset(filepath=setup_card.look_preset_path(bpy, look),
                                      menu_idname="USERPREF_MT_interface_theme_presets")
    yield 0.02   # One main-loop turn: the preset's notifiers (and the font reload) are handled, then a frame drawn.
    path = capture("preferences-themes-%s-first-frame" % look)
    try:
        report["chat_text_first_frame_" + look] = chat_text_pixels(path)
    except Exception as error:   # Recorded as a failed check; the rest of the phase still reports.
        report["chat_text_first_frame_" + look] = {"ok": False, "error": repr(error)}
    check("chat_cards_show_text_one_frame_after_the_%s_preset" % look, report["chat_text_first_frame_" + look]["ok"])


def chat_text_pixels(path):
    """Text pixels (any channel >= 150: the cards' text and links; the dark card and its hairline stay under 40)
    inside each of the chat's welcome rows, from its drawn hit geometry; with the font IDs, for diagnosis."""
    import blf
    import popup_pixels
    from angelo_agent import chat_view, console, font_watch
    area = next(a for a in window().screen.areas if console.is_chat(a))
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rgb = popup_pixels.load(path)
    k = rgb.shape[1] / window().width
    rows = {}
    for rect, action, value in chat_view._views.get(area.as_pointer(), {}).get("hits", []):
        if not str(action).startswith("welcome"):
            continue
        x, y = int((region.x + rect[0]) * k), int((region.y + rect[1]) * k)
        box = rgb[max(0, y):y + int(rect[3] * k), max(0, x):x + int(rect[2] * k)]
        rows["%s:%s" % (action, value)] = int((box.max(axis=2) >= 150).sum()) if box.size else 0
    files = getattr(chat_view, "font_files", {})
    if chat_view._font:
        blf.size(chat_view._font, 12)
    fonts = {"font": chat_view._font, "mono": chat_view._mono, "reloads": font_watch.checks["reloads"],
             "files": files, "measures": blf.dimensions(chat_view._font, "M")[0] if chat_view._font else None}
    return {"rows": rows, "fonts": fonts, "ok": len(rows) >= 3 and all(count >= 30 for count in rows.values())}


def card_kind():
    host = mod("angelo_agent.card_host")
    return host._active.card.kind if host and host._active else None


def phase_cold():
    """A first launch with an empty bytecode folder: the splash shows its art, never Blender's form.

    The host checks the folder was empty or missing before launch (``bytecode_folder_empty_before_launch``).
    Here: this launch used that folder; the shipped-bytecode loader reads the shipped file for every module it
    served (asked again with a fresh empty folder, it compiles none: ``cold_bytecode.probe_loader``); and Angelo's
    own add-on loaded through that loader. What the launch compiled is recorded, not judged: files loaded straight
    from their path outside the import system (a windowed launch's keyconfig presets) compile by design. Shipped
    bytecode (e80af48d1e5) made the old proxy, more than 200 compiled files, stale."""
    yield Sample(6.0)
    analyse()
    yield Until(lambda: card_ready("setup"), "the setup card", limit=90)
    first_launch_checks()
    import cold_bytecode
    prefix = sys.pycache_prefix
    audit = cold_bytecode.audit(prefix, app_root=os.path.dirname(sys.prefix))
    addon = [name for name in audit["loaded_shipped"] if name.startswith("angelo_agent")]
    report["cold"] = dict(audit, pycache_prefix=prefix, loaded_shipped=len(audit["loaded_shipped"]), addon=addon[:5],
                          recompiled_shipped=len(audit["recompiled_shipped"]))
    wanted = os.environ.get("PYTHONPYCACHEPREFIX", "")
    check("cold_launch_used_its_own_bytecode_folder", bool(prefix and wanted) and os.path.isabs(prefix)
          and os.path.realpath(prefix) == os.path.realpath(wanted))
    report["cold"]["loader_compiled"] = compiled = cold_bytecode.probe_loader(app_root=os.path.dirname(sys.prefix))
    check("cold_launch_shipped_loader_reads_every_module_it_serves", compiled == [])
    check("angelo_add_on_loaded_from_shipped_bytecode", "angelo_agent" in audit["loaded_shipped"])


def startup_card_scheduled():
    layout, intro = mod("angelo_default_layout"), mod("angelo_agent.intro")
    return bool(layout and intro and not layout._startup and intro._timer is not None
                and bpy.app.timers.is_registered(intro._timer))


def open_file():
    """What the Finder's open-document event does (GHOST_kEventOpenMainFile)."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.open_mainfile('INVOKE_DEFAULT', filepath=BLEND, display_file_selector=False)


def phase_finder():
    """A .blend opened from the Finder at first launch, after the startup card was scheduled and before it showed."""
    frame("first")
    yield Until(startup_card_scheduled, "the startup card to be scheduled", limit=30, poll=0.01)
    report["opened_while_the_card_was_scheduled"] = startup_card_scheduled()
    open_file()
    yield Sample(4.0)
    analyse()
    yield Until(lambda: card_ready("setup"), "the setup card after the file opened")
    check("file_opened", bpy.data.filepath == BLEND)
    check("opened_while_the_card_was_scheduled", report["opened_while_the_card_was_scheduled"])
    first_launch_checks()
    yield from finish_welcome()


def phase_finder_late():
    """A .blend opened from the Finder while the setup card shows: the card comes back once the file is open."""
    yield Sample(1.0)
    yield Until(lambda: card_ready("setup"), "the setup card")
    open_file()
    yield Until(lambda: bpy.data.filepath == BLEND, "the file to open")
    yield Sample(2.0, tag="opened")
    analyse()
    yield Until(lambda: card_ready("setup"), "the setup card again")
    no_form()
    own_frames()   # The opened file's stock layout never shows: Angelo's cover until its layout is back.
    check("setup_card_returns_after_the_file_opened", sequence()[:3] == ["splash", "setup", "setup"])
    capture("setup-after-file-opened")
    yield from finish_welcome(("splash", "setup", "setup", "welcome"))


def phase_cli():
    """A .blend on the command line at first launch: the splash's art, then the setup card, with the file open."""
    yield Sample(4.0)
    analyse()
    yield Until(lambda: card_ready("setup"), "the setup card")
    check("file_opened", bpy.data.filepath == BLEND)
    first_launch_checks()


def phase_opted_out():
    """Angelo's startup turned off the way automation does it (``ANGELO_DISABLE_DEFAULT_CHAT``, ``--factory-startup``,
    an empty profile): the splash still shows its art alone. Then the add-on is turned on and off, as in Preferences,
    and the splash opened again with no preferences saved: Blender's own Quick Setup class owns the menu again and
    draws the art alone too."""
    check("stand_ins_in_place", set(report["at_python"]["menus"].values()) == {"angelo_default_layout"})
    yield Sample(3.0)
    analyse()
    no_form()
    check("the_splash_showed_its_art", sum(splash_frames.stand_in_draws.values()) > 0)
    check("angelos_startup_never_ran", mod("angelo_agent.splash_setup") is None)
    capture("opted-out-splash")
    w = window()
    w.workspace = w.workspace   # Closes the splash (Blender frees a window's popups when a workspace is set).
    yield 0.5
    import addon_utils
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    check("the_add_on_took_the_menus_over", set(splash_frames.menu_owners().values()) == {"angelo_agent.splash_setup"})
    addon_utils.disable("angelo_agent", default_set=False)
    check("blenders_own_classes_own_the_menus_again", set(splash_frames.menu_owners().values()) == {"bl_operators.wm"})
    check("still_no_preferences_saved", not os.path.isfile(
        os.path.join(bpy.utils.user_resource('CONFIG'), "userpref.blend")))
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.splash('INVOKE_DEFAULT')   # Help > Splash Screen.
    yield Sample(2.0, tag="add-on-disabled")
    analyse()
    check("blenders_own_quick_setup_class_drew", splash_frames.stock_draws["WM_MT_splash_quick_setup"] > 0)
    no_form("add_on_disabled_", tag="add-on-disabled", blenders_menus=True)
    capture("add-on-disabled-splash")


class FIXTURE_MT_form_row(bpy.types.Menu):
    """The pixel check's control: Blender's former Quick Setup row, "Mouse Select" with its selected radio button,
    laid out as that form did (``bl_operators/wm.py`` before Angelo emptied it)."""
    bl_idname = "WM_MT_splash_quick_setup"
    bl_label = "Fixture form row"

    def draw(self, context):
        split = self.layout.split(factor=0.20)   # The form's left margin.
        split.label()
        col = split.split(factor=0.73).column()   # Its content width.
        col.use_property_split = True
        col.use_property_decorate = False
        col.row().prop(context.window_manager.keyconfigs.active.preferences, "select_mouse", text="Mouse Select",
                       expand=True)


def _pixel_control_setup():
    current = getattr(bpy.types, FIXTURE_MT_form_row.bl_idname, None)
    if current is not None:
        bpy.utils.unregister_class(current)
    bpy.utils.register_class(FIXTURE_MT_form_row)


def phase_pixel_control():
    """Control: the fixture's own menu draws a form row in the splash, and the pixel check finds it, so its passes in
    the other phases are not blind (Angelo's startup is turned off so nothing replaces the fixture's menu)."""
    check("fixture_menu_in_place", splash_frames.menu_owners()["WM_MT_splash_quick_setup"] == __name__)
    yield Sample(2.0)
    analyse()
    check("pixel_check_finds_the_form_row", any(f["form_rows"] >= splash_frames.FORM_ROWS for f in report["frames"]))
    capture("pixel-control")


def _failed_start_setup():
    """Angelo's scheduled start fails: enabling the add-on raises (the fixture's one expected error)."""
    import addon_utils

    def enable(*_args, **_kwargs):
        report["timings"]["start_failed"] = after_launch()
        raise RuntimeError(EXPECTED_FAILURE)
    addon_utils.enable = enable


def phase_failed_start():
    """A start that ends without Angelo's add-on: the splash keeps its art alone, never Blender's form."""
    yield Sample(3.0)
    analyse()
    layout = mod("angelo_default_layout")
    failed = report["timings"].get("start_failed")
    check("the_start_failed", failed is not None and mod("angelo_agent.splash_setup") is None
          and not layout.startup_pending())
    check("frames_after_the_failure", failed is not None and any(f["after_launch"] > failed for f in report["frames"]))
    check("the_splash_showed_its_art", sum(splash_frames.stand_in_draws.values()) > 0)
    no_form()
    capture("failed-start-splash")


AT_PYTHON = {"pixel-control": _pixel_control_setup, "failed-start": _failed_start_setup}


PHASES = {"warm": phase_warm, "second": phase_second, "cold": phase_cold, "finder": phase_finder,
          "finder-late": phase_finder_late, "cli": phase_cli, "opted-out": phase_opted_out,
          "pixel-control": phase_pixel_control, "failed-start": phase_failed_start}
