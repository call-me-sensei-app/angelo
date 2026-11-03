# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scene of ``test_blender_update_crash``: a newer release line beside the person's own version, in the real card.

Up to date and with an update of the own line, each with the feed's recommendation off and on. The suite's app is not
installed, so this scene says it can replace itself; the real preparation is the end-to-end update
(updates_and_reports.rst). Update's click is recorded at ``_start``, where a download would begin.
"""

import bpy

import update_crash_gui as gui
import update_fixtures as ufx
from update_crash_gui import active, capture, click, labels, park_pointer, settle, text, until, window
from update_crash_scenes import state_directory

LINE = "9.1.0"


def _line(update_feed, recommended):
    item = ufx.payload(version=LINE)["latest"]
    item["downloads"] = {update_feed.platform_key(): {
        "url": "https://downloads.angelo.studio/%s/Angelo-%s.dmg" % (LINE, LINE), "sha256": "a" * 64, "size": 460_000_000}}
    item["recommended"] = recommended
    return item


def _newer_patch(running):
    major, minor, patch = running.split(".")
    return "%s.%s.%d" % (major, minor, int(patch) + 1)


def _check(fx, feed_version, item, name):
    from angelo_agent import update_ui
    fx.set_feed(feed_version, lines=[item])
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.check_for_updates()
    yield from until("the card, " + name, lambda: active("update"))
    park_pointer()
    yield from settle(3)
    capture("update-lines-%s.png" % name)
    return update_ui.offer


def line_choice(fx):
    from angelo_agent import app_version, update_feed, update_ready, update_state, update_ui
    c = fx.checks
    running = app_version.current()
    stay = "Stay on Angelo " + update_feed.line_of(running)
    support, start, started = update_ready._support_reason, update_ready._start, []
    update_ready._support_reason, update_ready._start = "", started.append

    yield from _check(fx, running, _line(update_feed, False), "up-to-date")
    shown = text()
    c["up_to_date_shows_both_sides"] = "You're up to date" in shown and "A newer version" in shown
    c["up_to_date_asks_update_or_stay"] = labels() == ["Release notes", "Update", stay, "Later"]
    c["own_version_comes_first"] = shown.index("You're up to date") < shown.index("A newer version")
    c["a_choice_starts_nothing_by_itself"] = started == [] and update_ready._job is None
    click("later")
    yield from settle()

    yield from _check(fx, running, _line(update_feed, True), "up-to-date-recommended")
    shown = text()
    c["recommended_says_so_and_leads"] = ("We recommend moving" in shown and "(recommended)" in shown
                                          and shown.index("A newer version") < shown.index("You're up to date"))
    c["recommended_keeps_both_options"] = "You're up to date" in shown and stay in labels()
    c["recommended_starts_nothing"] = started == [] and update_ready._job is None
    click("skip")
    yield from settle()
    saved = update_state.load(state_directory())
    c["stay_closes_it_and_hides_the_status_item"] = not gui.host().is_active("update") and update_ui.offer is None
    c["stay_is_remembered_for_the_line"] = saved.skipped_line == "9.1"

    patch = _newer_patch(running)
    yield from _check(fx, patch, _line(update_feed, False), "with-update")
    shown = text()
    c["an_update_stands_beside_the_newer_line"] = "An update to your version" in shown and "A newer version" in shown
    c["the_update_and_the_choice_each_have_a_button"] = "Update" in labels() and stay in labels()
    c["the_own_update_may_start_the_newer_line_may_not"] = LINE not in [o.release.version for o in started]
    started.clear()
    click("skip")
    yield from settle()
    c["stay_beside_an_update_keeps_the_update"] = (update_ui.offer is not None and update_ui.offer.later is None
                                                   and update_ui.offer.release.version == patch)
    update_ready.forget(patch)

    yield from _check(fx, patch, _line(update_feed, True), "with-update-recommended")
    shown = text()
    c["recommended_leads_beside_an_update"] = shown.index("A newer version") < shown.index("An update to your version")
    started.clear()
    click("accept")
    yield from settle()
    c["update_starts_the_newer_line_once"] = [offer.release.version for offer in started] == [LINE]
    c["update_is_remembered_for_the_line"] = update_state.load(state_directory()).accepted_line == "9.1"
    click("later")
    yield from settle()
    update_ready._support_reason, update_ready._start = support, start
