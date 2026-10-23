# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scenes of ``test_blender_update_crash``, in the order the app shows its cards after a launch.

Each scene is a generator over the fixture namespace ``fx`` (``checks``, ``links``, ``server``, ``reports``,
``set_feed``, ``plant_crash``, ``closed_port``) and yields whenever Blender must handle the events sent so
far. Colours are the card palette (``card_kit``) as the screenshot stores them, 0-255.
"""

import os
import time
from urllib.parse import parse_qs, urlsplit

import bpy

import crash_fixtures as cfx
import update_crash_gui as gui
from update_crash_gui import (active, brightness, button_rect, button_state, capture, card, center, click, close_to,
                              fill_of, hit_rect, key, labels, move_to, park_pointer, press_at, release_at, ring_of,
                              same_pixels, settle, text, until, window)

TEAL, TEAL_HOVER, TEAL_PRESSED = (43, 196, 191), (61, 217, 209), (31, 153, 148)
FOCUS, CARD = (158, 235, 230), (23, 25, 30)


def state_directory():
    from angelo_agent.ui import state_directory as folder
    return folder()


def focus():
    return gui.host()._active.ui.focus if gui.host()._active.keyboard else None


def no_canary(value):
    return not [c for c in cfx.CANARIES if c.lower() in value.lower()]


def crash_without_endpoint(fx):
    """As 1.0 ships: no endpoint, so Send report is disabled; save and the website are the ways forward."""
    from angelo_agent import card_host, crash_state
    c = fx.checks
    yield from until("the crash dialog", lambda: active("crash"))
    park_pointer()
    yield from settle()
    rest = capture("crash-01-unavailable.png")
    plan = gui.host()._active.plan
    c["crash_dialog_comes_first"] = card_host.history[:1] == ["crash"]
    c["unavailable_dialog_buttons"] = labels() == ["Report on the website", "Save report…", "Close", "Send report"]
    c["unavailable_dialog_says_sending_is_not_available"] = "Sending crash reports from Angelo is not available yet." in text()
    c["unavailable_dialog_has_no_remember_choice"] = not [op for op in plan.ops if op[0] == "checkbox"]
    send = button_rect("Send report")
    c["send_is_disabled"] = button_state("Send report") == (False, False, False, False) and "send" not in [
        action for _, action, _ in plan.hits]
    disabled = fill_of(rest, send)
    c["disabled_send_is_a_dim_teal_not_the_live_one"] = (brightness(disabled) < brightness(TEAL) - 150
                                                         and disabled[1] > disabled[0] + 25)
    c["the_disabled_label_stays_readable"] = gui.peak(rest, send) > brightness(disabled) + 150
    move_to(*center(send))
    yield from settle()
    hovered = capture("crash-02-disabled-send-under-pointer.png")
    c["disabled_send_ignores_the_pointer"] = same_pixels(rest, hovered, send) and gui.host()._active.cursor == "DEFAULT"
    press_at(*center(send))
    release_at(*center(send))
    yield from settle()
    c["disabled_send_cannot_send"] = active("crash") and card().flow.phase == "ask" and fx.server.count("/crash") == 0

    # Hover, pressed and a press released elsewhere, on a secondary button.
    save = hit_rect("save")
    base = fill_of(rest, save)
    move_to(*center(save))
    yield from settle()
    hover = fill_of(capture("crash-03-save-hover.png"), save)
    press_at(*center(save))
    yield from settle()
    pressed = fill_of(capture("crash-04-save-pressed.png"), save)
    c["secondary_rest_hover_pressed_are_distinct"] = brightness(pressed) + 25 < brightness(base) < brightness(hover) - 25
    away = (center(save)[0], center(save)[1] + 160)
    move_to(*away)
    release_at(*away)
    yield from settle()
    c["a_press_released_elsewhere_does_nothing"] = active("crash") and gui.file_browser() is None

    # Keyboard: Tab walks the enabled controls only and wraps; the focus ring is drawn.
    seen, ring = [], None
    for _ in range(5):
        key('TAB')
        yield from settle()
        seen.append(focus())
        if seen[-1] == ("save", None):
            ring = ring_of(capture("crash-05-focus-on-save.png"), save)
    c["tab_skips_the_disabled_send_and_wraps"] = seen == [("toggle", None), ("problem", None), ("save", None),
                                                          ("dismiss", None), ("toggle", None)]
    c["focus_ring_is_drawn_only_when_focused"] = (ring is not None and close_to(ring, FOCUS, 30)
                                                  and close_to(ring_of(rest, save), CARD, 12))
    key('RET')   # Focus is on the disclosure.
    yield from settle()
    capture("crash-06-report-shown.png")
    c["return_opens_the_focused_disclosure"] = card().expanded and "report" in gui.host()._active.plan.scroll
    c["the_report_shown_has_no_private_data"] = no_canary(card().flow.text)

    # Report on the website: the page with system, graphics and version, never the stack.
    click("problem")
    yield from settle()
    link = fx.links[-1] if fx.links else ""
    query = parse_qs(urlsplit(link).query)
    c["report_on_the_website_opens_the_report_page"] = link.startswith("https://angelo.studio/support/report-a-bug?")
    c["the_website_link_carries_no_stack"] = (set(query) <= {"os", "gpu", "version"} and "0x" not in link
                                              and "_ZN" not in link and no_canary(link))

    # Save report…: the real File Browser, then the exact report on disk.
    click("save")
    yield from until("the File Browser", lambda: gui.file_browser() is not None, 20)
    yield from settle(3)
    browser_window, area = gui.file_browser()
    capture("crash-07-save-file-browser.png", browser_window)
    params = area.spaces.active.params
    c["save_proposes_a_dated_report_file"] = (params.filename.startswith("Angelo-crash-report-")
                                              and params.filename.endswith(".json"))
    target = gui.OUT / "saved-crash-report.json"
    try:
        params.directory = str(gui.OUT).encode()
    except TypeError:
        params.directory = str(gui.OUT)
    params.filename = target.name
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=browser_window, area=area, region=region):
        bpy.ops.file.execute()
    yield from until("the saved report", target.exists, 20)
    yield from settle()
    c["the_saved_file_is_exactly_the_report"] = target.read_text(encoding="utf-8") == card().flow.text + "\n"
    c["saving_sent_nothing"] = fx.server.count("/crash") == 0
    click("dismiss")
    yield from until("the dialog to close", lambda: not gui.host().is_active("crash"))
    c["the_crash_is_recorded_as_saved"] = [v["status"] for v in crash_state.load(state_directory()).handled.values()] == ["saved"]
    c["consent_is_still_ask"] = crash_state.load(state_directory()).consent == "ask"


def whats_new(fx):
    # Expectations come from the notes this build ships and its own version, never from one release's text: a
    # release may have sections ("Show everything") or only highlights, and the version moves with every release.
    from angelo_agent import app_version, release_notes, update_state
    c = fx.checks
    running = app_version.current()
    notes, _remember = release_notes.whats_new("0.9.0", running)   # The fixture's last-seen version.
    yield from until("What's New", lambda: active("whatsnew"))
    yield from settle()
    capture("whatsnew-1.png")
    c["whats_new_comes_after_the_crash_dialog"] = gui.host().history[:2] == ["crash", "whatsnew"]
    c["whats_new_title"] = "What's new in Angelo %s" % app_version.display(running) in text()
    c["the_shipped_notes_are_the_running_versions"] = notes is not None and notes.version == running
    toggles = [hit for hit in gui.host()._active.plan.hits if hit[1] == "toggle"]
    if notes is not None and notes.sections:
        click("toggle")
        yield from settle()
        capture("whatsnew-2-everything.png")
        c["whats_new_lists_the_full_notes_in_a_scrolling_box"] = "notes" in gui.host()._active.plan.scroll
    else:
        c["highlights_only_notes_offer_no_show_everything"] = not toggles
    click("online")
    yield from settle()
    c["read_online_opens_the_release_notes_page"] = fx.links[-1:] == ["https://angelo.studio/releases"]
    key('RET')   # Got it is the default action.
    yield from until("What's New to close", lambda: not gui.host().is_active("whatsnew"))
    c["the_last_seen_version_is_remembered"] = update_state.load(state_directory()).last_seen_version == running


def update_notice(fx):
    from angelo_agent import app_version, card_kit, update_state, update_ui
    c = fx.checks
    yield from until("the update notice", lambda: active("update"))
    park_pointer()
    yield from settle(3)
    rest = capture("update-1-notice.png")
    plan = gui.host()._active.plan
    c["card_order_crash_whatsnew_update"] = gui.host().history[:3] == ["crash", "whatsnew", "update"]
    c["update_notice_buttons"] = labels() == ["Release notes", "Download", "Skip this version", "Later"]
    area, region, _ = gui.card_region()
    cx, cy, cw, ch = plan.card
    c["the_notice_sits_in_the_3d_views_bottom_right_corner"] = abs(cx + cw - (region.width - 20)) < 6 and abs(cy - 20) < 6
    c["the_notice_is_not_modal"] = not [op for op in plan.ops if op[0] == "scrim"]
    c["the_status_bar_offers_the_update"] = update_ui.offer is not None and update_ui.offer.release.version == "1.1.0"
    request = next(r for r in fx.server.requests if r.path == "/updates/v1/stable.json")
    c["the_feed_request_carries_only_the_product_user_agent"] = (
        sorted(set(request.headers) - {"host", "accept-encoding", "connection", "accept"}) == ["user-agent"]
        and request.headers["user-agent"].startswith("Angelo/%s (" % app_version.current()))

    # The viewport keeps working beside the notice: a middle-button drag orbits the view.
    view = area.spaces.active.region_3d
    before = (tuple(view.view_rotation), view.view_distance, view.view_camera_zoom, view.view_perspective)
    start = (int(region.x + region.width * 0.35), int(region.y + region.height * 0.6))
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=start[0], y=start[1])
    window().event_simulate(type='MIDDLEMOUSE', value='PRESS', x=start[0], y=start[1])
    for step in range(1, 7):
        window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=start[0] + 18 * step, y=start[1] + 6 * step)
    window().event_simulate(type='MIDDLEMOUSE', value='RELEASE', x=start[0] + 108, y=start[1] + 36)
    yield from settle(3)
    after = (tuple(view.view_rotation), view.view_distance, view.view_camera_zoom, view.view_perspective)
    c["the_viewport_still_orbits_beside_the_notice"] = after != before and active("update")
    c["the_notice_ignores_points_outside_its_card"] = card_kit.hit_test(plan, start[0] - region.x, start[1] - region.y) is None

    click("notes")
    yield from settle()
    c["release_notes_button_opens_the_notes_link"] = fx.links[-1] == "https://angelo.studio/releases#1.1.0"
    download = hit_rect("download")
    park_pointer()
    yield from settle()
    rest = fill_of(capture("update-2-download-rest.png"), download)
    move_to(*center(download))
    yield from settle()
    hover = fill_of(capture("update-3-download-hover.png"), download)
    press_at(*center(download))
    yield from settle()
    pressed = fill_of(capture("update-4-download-pressed.png"), download)
    c["primary_rest_hover_pressed_match_the_palette"] = (close_to(rest, TEAL) and close_to(hover, TEAL_HOVER)
                                                        and close_to(pressed, TEAL_PRESSED))
    release_at(*center(download))
    yield from settle()
    c["download_opens_the_installer_link_in_the_browser"] = fx.links[-1].endswith("Angelo-1.1.0-macos-arm64.dmg")
    c["download_closes_the_notice"] = not gui.host().is_active("update")
    saved = update_state.load(state_directory())
    c["download_counts_as_later_for_three_days"] = saved.snoozed_version == "1.1.0" and saved.snoozed_until > time.time() + 2 * 86400
    c["the_status_item_remains_after_later"] = update_ui.offer is not None
    yield from settle(2)
    capture("update-5-status-bar.png")

    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.update_show()
    yield from until("the notice from the status item", lambda: active("update"))
    yield from settle()
    click("skip")
    yield from settle()
    c["skip_hides_the_notice_and_the_status_item"] = not gui.host().is_active("update") and update_ui.offer is None
    c["skip_is_remembered_for_that_version"] = update_state.load(state_directory()).skipped_version == "1.1.0"


def _manual_check(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.check_for_updates()
    yield from until(name, lambda: active("notice"))
    yield from settle()


def manual_checks(fx):
    from angelo_agent import app_version, crash_upload, managed_policy
    c = fx.checks
    fx.set_feed(app_version.current())   # The feed offers exactly the running version: up to date.
    yield from _manual_check("the up-to-date notice")
    capture("check-1-up-to-date.png")
    c["manual_check_says_up_to_date"] = card().title == "Angelo is up to date" and (
        "Angelo " + app_version.display(app_version.current())) in card().body
    click("close")
    yield from settle()
    feed_url = os.environ["ANGELO_UPDATE_FEED_URL"]
    os.environ["ANGELO_UPDATE_FEED_URL"] = fx.closed_port()
    yield from _manual_check("the offline notice")
    capture("check-2-offline.png")
    c["manual_check_offline_is_a_calm_notice_with_try_again"] = (
        card().title == "Could not check for updates" and labels() == ["Close", "Try again"])
    click("close")
    yield from settle()
    os.environ["ANGELO_UPDATE_FEED_URL"] = feed_url
    managed_policy.reset(managed_policy.combine([("fixture", {"DisableUpdateCheck": True, "DisableCrashReporting": True})]))
    before = len(fx.server.requests)
    yield from _manual_check("the managed notice")
    capture("check-3-managed.png")
    c["the_machine_policy_stops_even_a_manual_check"] = card().title == "Update checks are off" and len(fx.server.requests) == before
    c["the_machine_policy_removes_the_crash_endpoint"] = crash_upload.endpoint("https://console.angelo.studio/api/v1/crash-reports") == ""
    click("close")
    yield from settle()
    managed_policy.reset()


def crash_with_endpoint(fx):
    """Once an endpoint is announced: the exact report, keyboard focus, remember, and the send itself."""
    from angelo_agent import crash_state, crash_ui
    c = fx.checks
    os.environ["ANGELO_CRASH_REPORT_URL"] = fx.server.base + "/crash"
    fx.plant_crash(60)
    crash_ui.start_scan()
    yield from until("the second crash scan", crash_ui.poll)
    yield from until("the crash dialog", lambda: active("crash"))
    park_pointer()
    yield from settle()
    rest = capture("crash-10-ask.png")
    c["ask_dialog_buttons"] = labels() == ["Report on the website", "Save report…", "Don't send", "Send report"]
    send = hit_rect("send")
    move_to(*center(send))
    yield from settle()
    hover = capture("crash-11-send-hover.png")
    press_at(*center(send))
    yield from settle()
    pressed = capture("crash-12-send-pressed.png")
    c["send_rest_hover_pressed_match_the_palette"] = (close_to(fill_of(rest, send), TEAL)
                                                     and close_to(fill_of(hover, send), TEAL_HOVER)
                                                     and close_to(fill_of(pressed, send), TEAL_PRESSED))
    away = (center(send)[0], center(send)[1] + 180)
    move_to(*away)
    release_at(*away)
    yield from settle()
    key('RET')
    yield from settle()
    c["nothing_is_sent_by_a_release_elsewhere_or_a_stray_return"] = fx.server.count("/crash") == 0 and active("crash")
    key('TAB')
    yield from settle()
    capture("crash-13-focus-on-disclosure.png")
    c["tab_focuses_the_disclosure_first"] = focus() == ("toggle", None)
    key('RET')
    yield from settle()
    key('TAB', shift=True)
    yield from settle()
    send = hit_rect("send")   # The report box opened above it: the button moved.
    focused = capture("crash-14-focus-on-send.png")
    c["shift_tab_wraps_to_send_and_never_sends_by_itself"] = focus() == ("send", None) and fx.server.count("/crash") == 0
    c["the_focus_ring_surrounds_send"] = close_to(ring_of(focused, send), FOCUS, 30)

    box = gui.host()._active.plan.scroll["report"]
    _, region, _ = gui.card_region()
    bx, by, bw, bh = box["rect"]
    inside = (int(region.x + bx + bw / 2), int(region.y + by + bh / 2))
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=inside[0], y=inside[1])
    for _ in range(4):
        window().event_simulate(type='WHEELDOWNMOUSE', value='PRESS', x=inside[0], y=inside[1])
    yield from settle()
    scrolled = capture("crash-15-report-scrolled.png")
    c["the_report_box_scrolls"] = gui.host()._active.ui.scroll.get("report", 0) > 0 and not same_pixels(
        focused, scrolled, (region.x + bx, region.y + by, bw, bh))

    box_rect = hit_rect("remember")
    fill = (box_rect[0] + 4, box_rect[1] + box_rect[3] / 2)   # Inside the box, left of the check mark.
    unticked = gui.color_at(scrolled, *fill, radius=0)
    click("remember")
    yield from settle()
    ticked = gui.color_at(capture("crash-16-remember-ticked.png"), *fill, radius=0)
    c["remember_ticks_visibly"] = card().flow.remember is True and brightness(ticked) > brightness(unticked) + 150
    click("send")
    yield from settle()
    c["send_starts_the_upload"] = card().flow.phase in {"sending", "sent"}
    yield from until("the report to be sent", lambda: card().flow.phase == "sent")
    yield from settle()
    capture("crash-17-sent.png")
    flow = card().flow
    sent = next(r for r in fx.server.requests if r.path == "/crash")
    c["exactly_one_report_reached_the_endpoint"] = len(fx.reports) == 1 and fx.server.count("/crash") == 1
    c["the_bytes_sent_are_the_bytes_shown"] = sent.body == flow.text.encode("ascii")
    c["the_report_is_the_stack_and_environment_only"] = set(fx.reports[0]) == {
        "schema", "report_id", "created", "crashed_at", "app", "crashed_build", "system", "crash", "omitted_log_lines"}
    c["the_report_holds_no_canary"] = no_canary(sent.body.decode())
    c["no_credentials_cookies_or_identity_were_sent"] = not ({"cookie", "authorization"} & set(sent.headers))
    c["the_thank_you_shows_the_report_id"] = ("Report ID: " + flow.report_id) in text()
    c["remembered_choice_is_always"] = crash_state.load(state_directory()).consent == "always"
    click("dismiss")
    yield from until("the dialog to close", lambda: not gui.host().is_active("crash"))
    statuses = sorted(v["status"] for v in crash_state.load(state_directory()).handled.values())
    c["the_crash_is_recorded_as_sent"] = statuses == ["saved", "sent"]


def automatic_send_and_policy(fx):
    """``always``: the next crash is sent without a dialog and a notice says so. The policy stops it all."""
    from angelo_agent import crash_report, crash_state, crash_ui, managed_policy
    c = fx.checks
    shown_before = len(gui.host().history)
    fx.plant_crash(30)
    crash_ui.start_scan()
    yield from until("the third crash scan", crash_ui.poll)
    yield from until("the sent notice", lambda: active("notice"))
    park_pointer()
    yield from settle()
    capture("auto-1-sent-notice.png")
    c["always_sends_without_a_dialog"] = len(fx.reports) == 2 and gui.host().history[shown_before:] == ["notice"]
    c["a_notice_says_the_report_was_sent"] = card().title == "Crash report sent" and labels() == [
        "Ask me next time", "See what was sent"]
    click("view")
    yield from until("the review", lambda: active("crash") and card().flow.phase == "review")
    yield from settle()
    capture("auto-2-review.png")
    c["the_sent_notice_does_not_come_back_behind_the_review"] = not gui.host().queued("notice")
    c["the_review_is_exactly_what_was_sent"] = card().flow.text == crash_report.render(fx.reports[-1])
    c["the_review_offers_to_ask_again"] = labels() == ["Copy report", "Ask me each time", "Close"]
    click("stop")
    yield from until("the review to close", lambda: not gui.host().is_active("crash"))
    c["ask_me_each_time_restores_the_question"] = crash_state.load(state_directory()).consent == "ask"

    handled = dict(crash_state.load(state_directory()).handled)
    managed_policy.reset(managed_policy.combine([("fixture", {"DisableCrashReporting": True})]))
    fx.plant_crash(20)
    crash_ui.start_scan()
    yield from until("the fourth crash scan", crash_ui.poll)
    yield from settle(6)
    c["the_policy_shows_and_sends_nothing"] = (not gui.host().is_active("crash") and not gui.host().queued("crash")
                                               and len(fx.reports) == 2)
    c["the_policy_records_nothing"] = crash_state.load(state_directory()).handled == handled
    managed_policy.reset()


def preferences_and_themes(fx):
    from angelo_agent import crash_state, update_state
    c = fx.checks
    wm = bpy.context.window_manager
    wm.angelo_update_automatic = False
    wm.angelo_crash_consent = 'never'
    c["preferences_write_the_two_choices"] = (update_state.load(state_directory()).automatic is False
                                              and crash_state.load(state_directory()).consent == "never")
    wm.angelo_update_automatic = True
    wm.angelo_crash_consent = 'ask'

    middle = (window().width // 2, window().height // 2)
    move_to(*middle)
    yield from settle()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.call_menu(name="TOPBAR_MT_help")
    yield from settle(3)
    capture("help-menu.png")
    window().event_simulate(type='ESC', value='PRESS', x=middle[0], y=middle[1])
    window().event_simulate(type='ESC', value='RELEASE', x=middle[0], y=middle[1])
    yield from settle()

    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    area.ui_type = 'PREFERENCES'
    bpy.context.preferences.active_section = 'SYSTEM'
    yield from settle(3)
    capture("prefs-1-dark.png")
    from angelo_agent import font_watch, update_ui
    # A theme preset reloads Blender's interface fonts; Angelo's own stay loaded under the same IDs and measure text
    # (blenfont keeps a script's fonts), so font_watch's fallback has nothing to do.
    reloads, before = font_watch.checks["reloads"], gui.chat_fonts()
    preset = bpy.utils.preset_find("Angelo_Light", "interface_theme", ext=".xml")
    with bpy.context.temp_override(window=window()):
        bpy.ops.script.execute_preset(filepath=preset, menu_idname="USERPREF_MT_interface_theme_presets")
    yield from settle(3)
    capture("prefs-2-light.png")
    after = gui.chat_fonts()
    fx.state["fonts_across_the_preset"] = {"before": before, "after": after}
    c["a_theme_preset_keeps_angelos_fonts"] = (
        {k: f for k, (f, _) in after.items()} == {k: f for k, (f, _) in before.items()}
        and all(width > 0 for _, width in list(before.values()) + list(after.values())))
    c["a_theme_preset_needs_no_font_fallback"] = font_watch.checks["reloads"] == reloads
    area.ui_type = 'VIEW_3D'
    # The fallback for anything that still frees one: a font of Angelo's really freed is loaded again and measures.
    freed = gui.free_a_chat_font()
    fx.state["freed_font"] = freed
    c["a_font_of_angelos_was_freed"] = bool(freed and freed["freed"])
    if c["a_font_of_angelos_was_freed"]:
        yield from until("the freed font to be loaded again", lambda: font_watch.checks["reloads"] > reloads, 10)
    fx.state["fonts_after_the_fallback"] = gui.chat_fonts()
    c["a_freed_font_is_loaded_again_and_measures"] = all(
        width > 0 for _, width in fx.state["fonts_after_the_fallback"].values())
    fx.set_feed("1.2.0")
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.check_for_updates()
    yield from until("the 1.2 notice", lambda: active("update"))
    park_pointer()
    yield from settle(3)
    light = capture("light-1-update-notice-and-top-bar.png")
    c["a_newer_version_is_offered_after_a_skipped_one"] = card().offer.release.version == "1.2.0"
    c["the_card_still_has_text_after_the_theme_change"] = gui.bright_pixels(light, gui.card_rect()) > 200
    c["the_top_bar_offers_the_update_in_director"] = update_ui.offer is not None and update_ui.offer.release.version == "1.2.0"
    click("later")
    yield from settle()
    # Manual mode shows Blender's status bar: the pending update is there, beside the version.
    from angelo_agent import director, ui
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.workspace_mode(mode="MANUAL")
    yield from until("the Manual layout", lambda: not director.is_director(window()) and director._timer is None
                     and ui._layout_timer is None, 30)
    yield from settle(4)
    capture("light-2-manual-status-bar.png")
    c["manual_mode_shows_the_status_bar"] = window().screen.show_statusbar and update_ui.offer is not None
    with bpy.context.temp_override(window=window()):
        bpy.ops.preferences.reset_default_theme()
    yield from settle(2)
    c["preferences_panel_drew_without_error"] = True
