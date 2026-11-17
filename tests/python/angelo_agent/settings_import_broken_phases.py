# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``test_blender_settings_import.py``'s ``broken`` group (doc/angelo/app_identity.rst, "A start
that did not finish"): Blender's folder enables Switcher Tool and Broken Tool, whose ``register`` sets up a property
group with a runtime pointer property, an operator and a menu entry, then raises.

* ``broken``: the setup card's import. The import's notice counts one add-on that failed; it stays enabled (nothing
  is turned off by itself) and what it set up stays usable; the failed add-ons' card names it, why, and **Turn off**.
* ``broken-restart``: the next start names it again on that card; **Turn off** (a real click) removes it from the
  preferences, as the Add-ons list's checkbox would, saves them, and the row then says it is off.
* ``broken-turned-off``: the start after: it stays off (the save kept it), nothing of it loads, no card names it.

Imported by ``test_blender_settings_import.py`` beside ``settings_import_phases``, whose ``PHASES`` it adds to.
"""

from pathlib import Path

import addon_utils
import bpy

from settings_import_phases import (PHASES, Until, capture, card_actions, card_ready, check, check_agent, click_card,
                                    click_welcome, report)


def broken_leftovers(prefix):
    """What Broken Tool set up before it raised is still there and safe to use, as in stock Blender: the property
    group under its runtime pointer property (reading and writing it, and a redraw of every window, would touch freed
    memory had it been undone) and its operator."""
    scene = bpy.context.scene
    props = getattr(scene, "broken_tool", None)
    check(prefix + "its_pointer_property_still_reads", props is not None and abs(props.strength - 0.5) < 1e-6)
    if props is not None:
        props.strength = 0.75
        check(prefix + "and_writes", abs(scene.broken_tool.strength - 0.75) < 1e-6)
    check(prefix + "its_operator_stays_registered", hasattr(bpy.types, "BROKEN_TOOL_OT_poke"))
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def failures_card_on_screen(seen=None):
    """True once the failed add-ons' card is the active one; closes any card ahead of it, as a person would."""
    from angelo_agent import card_host
    if card_host.is_active("addon_failures"):
        return True
    card = card_host.active_card()
    if card is not None:
        if seen is not None:
            seen.append(card.kind)
        card_host.close("close")
    return False


def failures_row(module):
    from angelo_agent import safe_start_notice as notice
    return next((row for row in (notice.shown_failures or (None, [], 0))[1] if row[0] == module), None)


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
    check("broken_addon_cause_known", "fails to register (fixture)" in (outcome.get("causes") or {}).get(
        "Broken Tool", ""))
    check("good_addon_loaded", addon_utils.check("switcher_tool") == (True, True))
    yield Until(lambda: intro.is_open() and "plan" in intro._open, "the welcome")
    check_agent()
    click_welcome("start")
    yield Until(lambda: card_ready("notice"), "the result notice")
    yield 0.6
    notice = card_host._active.card
    # It only counts the add-on that failed: the failures card names it with its cause (never named twice).
    check("notice_counts_the_failed_addon", "Add-ons that failed as they loaded: 1." in notice.body
          and "Broken Tool" not in notice.body and notice.icon == "alert")
    capture("import-notice-broken.png")
    # The import's enabling settles (angelo_addon_failures): nothing is turned off; its own card names it with Turn off.
    yield Until(failures_card_on_screen, "the failed add-ons' card", limit=60)
    yield 0.6
    row = failures_row("broken_tool")
    check("failures_card_names_it_and_why", row is not None and row[1] == "Broken Tool"
          and "fails to register (fixture)" in row[2])
    check("failures_card_offers_turn_off", ("turn_off", "broken_tool") in card_actions())
    check("broken_addon_still_enabled", "broken_tool" in bpy.context.preferences.addons
          and not addon_utils.check("broken_tool")[1])
    broken_leftovers("after_the_import_")
    capture("import-failures-card.png")
    card_host.close("close")   # Left on: the next start must name it again.


def phase_broken_restart():
    import angelo_safe_start
    from angelo_agent import card_host
    yield Until(lambda: failures_row("broken_tool") is not None, "the failed add-ons' card", limit=180)
    yield 1.0
    yield Until(failures_card_on_screen, "the failed add-ons' card on screen", limit=60)
    yield 0.6
    check_agent()
    row = failures_row("broken_tool")
    check("failures_card_names_it_again_and_why", row[1] == "Broken Tool" and "fails to register (fixture)" in row[2])
    check("nothing_left_off", angelo_safe_start.left_off() == [])
    check("good_addon_loaded", addon_utils.check("switcher_tool") == (True, True))
    broken_leftovers("at_start_")
    capture("broken-restart-failures-card.png")
    bpy.context.preferences.is_dirty = False
    click_card("turn_off", "broken_tool")
    yield Until(lambda: "broken_tool" not in bpy.context.preferences.addons, "Turn off", limit=10)
    yield 0.6
    check("turn_off_removes_it_from_the_preferences", "broken_tool" not in bpy.context.preferences.addons)
    check("turn_off_saved_the_preferences", not bpy.context.preferences.is_dirty)
    check("the_row_says_it_is_off", ("turn_off", "broken_tool") not in card_actions())
    capture("broken-restart-turned-off.png")


def phase_broken_turned_off():
    from angelo_agent import card_host, intro, safe_start_notice as notice
    yield Until(lambda: addon_utils.check("angelo_agent")[1] and intro.startup_done(), "Angelo's start-up", limit=120)
    yield 8.0   # Past the start's completion: a card would have shown by now.
    check("it_stays_off", "broken_tool" not in bpy.context.preferences.addons
          and not addon_utils.check("broken_tool")[1])
    check("nothing_of_it_loaded", not hasattr(bpy.types.Scene, "broken_tool"))
    check("no_card_names_it", notice.shown_failures is None and not card_host.is_active("addon_failures")
          and not card_host.queued("addon_failures"))
    check("good_addon_loaded", addon_utils.check("switcher_tool") == (True, True))
    check_agent()
    capture("broken-turned-off.png")
    report["start_cost"] = start_cost()


def start_cost():
    """What the wrapper adds to a start, in this app: building the shipped check once, then the first and the cached
    lookup for every add-on Blender knows (each enable does one), and the two .loading writes per add-on of the
    person's (the record's own file)."""
    import angelo_safe_start as safe
    import tempfile
    import time
    import angelo_start_records as records
    modules = [module.__name__ for module in addon_utils.modules(refresh=False)]
    saved, safe._state["watched"] = safe._state["watched"], None
    try:
        began = time.perf_counter()
        safe._watched(modules[0])
        built = time.perf_counter() - began
        began = time.perf_counter()
        own = [module for module in modules if safe._watched(module)]
        first = time.perf_counter() - began
        began = time.perf_counter()
        for module in modules:
            safe._watched(module)
        cached = time.perf_counter() - began
    finally:
        safe._state["watched"] = saved
    held = records.begin(Path(tempfile.mkdtemp()) / "angelo-start")
    began = time.perf_counter()
    for index in range(100):
        held.note_loading(["addon_%d" % index])
        held.note_loading([])
    writes = (time.perf_counter() - began) / 100
    held.release()
    return {"addons_known": len(modules), "persons_own": len(own), "shipped_check_built_ms": built * 1e3,
            "first_lookup_all_ms": first * 1e3, "cached_lookup_all_ms": cached * 1e3,
            "loading_writes_per_persons_addon_us": writes * 1e6}


PHASES.update({"broken": phase_broken, "broken-restart": phase_broken_restart,
               "broken-turned-off": phase_broken_turned_off})
