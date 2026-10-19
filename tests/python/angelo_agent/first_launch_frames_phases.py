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
from first_launch_frames_probe import (BLEND, Sample, Until, analyse, capture, card_actions, card_point, card_ready,
                                       check, click_card, control_rect, finish_welcome, frame, key, light, mod, no_form,
                                       report, sequence, window)


def first_launch_checks(prefix=""):
    """The splash showed its art (Angelo's stand-ins or menus drew it, nothing else) and the setup card followed."""
    no_form(prefix)
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
            with bpy.context.temp_override(window=window()):
                bpy.ops.script.execute_preset(filepath=setup_card.look_preset_path(bpy, "light"),
                                              menu_idname="USERPREF_MT_interface_theme_presets")
        yield Until(lambda: area.type == 'PREFERENCES', "Preferences in the largest area", limit=10)
        yield 1.0
        report["preferences_area_" + look] = {"area": area.type, "card": card_kind()}
        filepath = bpy.context.preferences.themes[0].filepath
        label = bpy.path.display_name(os.path.basename(filepath)) if filepath else "Angelo Dark"
        report["preferences_label_" + look] = label
        check("preferences_names_the_%s_look" % look, label == ("Angelo Dark" if look == "dark" else "Angelo Light"))
        capture("preferences-themes-" + look)
    with bpy.context.temp_override(window=window()):
        bpy.ops.script.execute_preset(filepath=setup_card.look_preset_path(bpy, "dark"),
                                      menu_idname="USERPREF_MT_interface_theme_presets")
    area.ui_type = kept
    yield 0.5


def card_kind():
    host = mod("angelo_agent.card_host")
    return host._active.card.kind if host and host._active else None


def phase_cold():
    """A first launch with no compiled Python: the splash shows its art for longer, never Blender's form."""
    yield Sample(6.0)
    analyse()
    yield Until(lambda: card_ready("setup"), "the setup card", limit=90)
    first_launch_checks()
    prefix = sys.pycache_prefix
    compiled = sum(len(files) for _, _, files in os.walk(prefix)) if prefix and os.path.isdir(prefix) else 0
    report["cold"] = {"pycache_prefix": prefix, "compiled_files": compiled}
    check("python_started_cold", compiled > 200)


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
    """Control: with Angelo's startup turned off the splash is Blender's own form, and both checks find it."""
    check("stand_ins_in_place", set(report["at_python"]["menus"].values()) == {"angelo_default_layout"})
    yield Sample(2.0)
    analyse()
    check("blenders_form_drew", splash_frames.form_draws["WM_MT_splash_quick_setup"] > 0)
    check("pixel_check_finds_the_form", any(f["form_rows"] >= splash_frames.FORM_ROWS for f in report["frames"]))


PHASES = {"warm": phase_warm, "second": phase_second, "cold": phase_cold, "finder": phase_finder,
          "finder-late": phase_finder_late, "cli": phase_cli, "opted-out": phase_opted_out}
