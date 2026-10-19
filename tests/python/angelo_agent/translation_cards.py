# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The notice cards for ``test_blender_translation``: the crash dialog, What's New and the update notice.

Built from the same fixtures as the card tests (``crash_fixtures``, ``update_fixtures``) and shown through
the real host (``card_host.request`` and ``pump``), so each language's screenshot is the card a person
sees. Nothing is sent, saved or opened: the crash dialog has no endpoint (as 1.0 ships) and every action
is a no-op; the update feed is signed by a throwaway key in memory.
"""

from datetime import datetime, timezone

import crash_fixtures as cfx
import update_fixtures as ufx
from angelo_agent import (card_host, crash_card, crash_flow, crash_log, crash_report, crash_state, release_notes,
                          update_card, update_feed, whatsnew_card)

KINDS = ("crash", "whatsnew", "update")


def _crash(folder):
    log = crash_log.parse(cfx.MAC, cfx.MAC_ROOTS)
    pending = crash_report.Pending(crash_state.fingerprint("angelo.crash.txt", 1, 1), "angelo.crash.txt",
                                   1_760_000_000 * 10 ** 9, log)
    system = crash_report.system_info(("Apple", "Apple M4 Pro", "1.2", "METAL"), "Darwin", "arm64", "26.3")
    app = crash_report.app_info("5.2.2 LTS", "311bbe4efa04", "2026-10-09")
    when = datetime(2026, 10, 25, tzinfo=timezone.utc)
    document = crash_report.build_document(log, system, app, pending.mtime_ns, when, "report-1")
    flow = crash_flow.CrashFlow(str(folder), pending, document, "", send=lambda *a: None)
    actions = type("Actions", (), {name: staticmethod(lambda *a: None)
                                   for name in ("save", "open_problem", "copy", "finished", "stop_automatic")})
    card = crash_card.CrashCard(flow, actions)
    card.expanded = True   # The exact report: English content in its own box, in every language.
    return card


def _whatsnew():
    releases = release_notes.load()
    return whatsnew_card.WhatsNewCard(releases[0], lambda url: None, lambda: None)


def _update():
    seed, keys = ufx.new_key()
    feed = update_feed.open_feed(ufx.envelope(ufx.payload(), seed), keys, datetime(2026, 10, 25, tzinfo=timezone.utc))
    return update_card.UpdateCard(update_feed.offer_for(feed, "1.0.0", "macos-arm64"), "1.0.0", lambda *a: None)


def show(kind, folder):
    """Queue the card of ``kind`` and show it now. True once it is the visible card."""
    card = {"crash": lambda: _crash(folder), "whatsnew": _whatsnew, "update": _update}[kind]()
    card_host.request(card)
    card_host.pump(lambda: True)
    return card_host.is_active(kind)


def plan():
    """The visible card's layout (region pixels, left to right as laid out), or None before its first draw."""
    state = card_host._active
    return state.plan if state is not None else None


def card_host_region():
    """The 3D view region the visible card is drawn in."""
    import bpy
    state = card_host._active
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.as_pointer() == state.area:
                return next(region for region in area.regions if region.type == 'WINDOW')
    raise LookupError("the card's area is gone")


def hover():
    state = card_host._active
    return state.ui.hover if state is not None else None


def close():
    """Dismiss the visible card; its record goes to the suite's own folder. Nothing waits behind it."""
    card_host.close("dismissed")
    card_host._queue.clear()
