# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``test_blender_settings_import.py``'s ``crashing`` group (doc/angelo/app_identity.rst, "A start
that did not finish"): Blender's folder enables Switcher Tool and Crash On Load, which crashes natively as it loads in
these launches (``ANGELO_QA_CRASH_ON_LOAD``).

* ``crashing-import``: the setup card's import. Its receipt records the steps up to Continue and is written before the
  click; the import then enables the copied add-ons and the process ends in the crash (the host checks the exit).
* ``crashing-safe``: the next launch. Every third-party add-on is left off, and one notice names them. Cards ahead of
  it (the start panel or setup card, the crash report of the launch that crashed) are closed as a person would close
  them; each is recorded.
* ``crashing-restart``: the launch after. No notice, nothing left off again, the add-ons still off. What opens is
  recorded: the welcome again, since the safe launch's was never answered.

Imported by ``test_blender_settings_import.py`` beside ``settings_import_phases``, whose ``PHASES`` it adds to.
"""

import json
from pathlib import Path

import addon_utils
import bpy

from settings_import_phases import (OUT, PHASE, PHASES, Until, capture, card_ready, check, check_agent, click_card,
                                    report)

THIRD_PARTY = ["crash_on_load", "switcher_tool"]   # As Blender's preferences list them (the fixture's order).
NAMES = ("Crash On Load", "Switcher Tool")


def all_off():
    prefs = bpy.context.preferences
    return all(module not in prefs.addons and not addon_utils.check(module)[1] for module in THIRD_PARTY)


def start_records():
    import angelo_safe_start
    folder = Path(bpy.utils.user_resource('CONFIG')) / angelo_safe_start.FOLDER
    return sorted(path.name for path in folder.glob("*" + angelo_safe_start.SUFFIX)) if folder.is_dir() else []


def phase_crashing_import():
    yield Until(lambda: card_ready("setup"), "the setup card")
    yield 0.6
    check("the_crashing_addon_has_not_loaded_yet", not addon_utils.check("crash_on_load")[1])
    click_card("import")
    yield 0.4
    from angelo_agent import card_host
    check("import_turned_on", card_host._active.card.use_blender)
    capture("crashing-setup-card.png")
    report["clicked_continue"] = True
    (OUT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))   # The crash writes nothing more.
    click_card("continue")
    yield Until(lambda: False, "the native crash (this launch must not get here)", limit=120)


def phase_crashing_safe():
    import angelo_safe_start
    from angelo_agent import card_host, safe_start_notice as notice
    yield Until(lambda: notice.shown is not None, "the safe-start notice", limit=180)
    yield 1.0
    report["cards_before_the_notice"] = []

    def notice_on_screen():
        if card_host.is_active("safe_start"):
            return True
        card = card_host.active_card()
        if card is not None:
            report["cards_before_the_notice"].append(card.kind)
            capture("crashing-before-%d-%s.png" % (len(report["cards_before_the_notice"]), card.kind))
            card_host.close("close")
        return False
    yield Until(notice_on_screen, "the notice on screen", limit=60)
    yield 1.0
    check("every_third_party_addon_was_left_off", sorted(angelo_safe_start.left_off()) == THIRD_PARTY)
    check("they_are_off", all_off())
    check("the_notice_names_them", all(name in notice.shown[1] for name in NAMES))
    check("the_notice_says_how_to_turn_one_back_on", "Preferences" in notice.shown[1])
    check("the_notice_is_on_screen", card_host.is_active("safe_start"))
    check("this_start_completed", start_records() == [])
    check_agent()
    capture("crashing-safe-notice.png")


def phase_crashing_restart():
    import angelo_safe_start
    from angelo_agent import card_host, intro, safe_start_notice as notice
    # What follows Angelo's start-up: the welcome again (the safe launch's was never answered) or a card.
    yield Until(lambda: addon_utils.check("angelo_agent")[1] and intro.startup_done()
                and (intro.is_open() or card_host.active_card() is not None), "Angelo's start-up", limit=120)
    report["welcome_open"] = intro.is_open()
    report["card"] = card_host.active_card().kind if card_host.active_card() else None
    yield 8.0   # Past the start's completion: a notice would have shown by now.
    check("nothing_left_off_again", angelo_safe_start.left_off() == [])
    check("no_safe_start_notice", notice.shown is None and not card_host.is_active("safe_start")
          and not card_host.queued("safe_start"))
    check("they_stay_off", all_off())
    check("this_start_completed", start_records() == [])
    check_agent()
    capture("crashing-restart.png")


PHASES.update({"crashing-import": phase_crashing_import, "crashing-safe": phase_crashing_safe,
               "crashing-restart": phase_crashing_restart})
