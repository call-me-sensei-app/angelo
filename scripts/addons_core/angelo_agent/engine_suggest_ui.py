# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Suggesting the engine live link when Godot, Unity or Unreal Engine runs, and keeping what the agent is told.

* **The watcher** (``tick``, a main-thread timer) starts one ``engine_detect`` scan at a time on a worker thread
  and reads its result ``COLLECT`` seconds later (a scan takes about a millisecond), so a running engine is acted on
  at once, not at the next scan; the main thread does no detection itself. It scans every ``INTERVAL`` seconds while
  an engine is left to suggest, then every ``QUIET_INTERVAL`` seconds only so the agent's facts stay current. A scan
  that finds what the last one found changes nothing: no card, no redraw, no bytes for the agent. Never in a
  background session or an automation run.
* **The card** (``SuggestionCard``) is a corner notice drawn with Angelo's card kit, translated, never modal: it says
  which engine runs and what the link does, with **Not now** and **Link a project…** (a folder picker for that engine's project; the
  plugin is copied into the chosen project and the link turns on, as the label says). One suggestion waits at a
  time (a queued card of the same kind would replace it); it is withdrawn if its engine closes or the link turns on
  meanwhile, and checked again as it shows. An engine counts as suggested (``engine_suggest.Memory``) only once its
  card has shown, so a card that never showed never uses up a suggestion; nothing connects without the click.
* **The agent** reads ``agent_note()`` in each request's observation (``engines``), sent only when it changed for that
  conversation; the ``FEATURES`` lookup reads ``facts()``. Only engine names leave this module.

doc/angelo/engine_link.rst, "Engine detection and the suggestion", is the contract.
"""

import os
import threading
import time

import bpy

from . import engine_detect, engine_suggest, update_card
from .ui_text import iface_, n_

INTERVAL, QUIET_INTERVAL, FIRST, COLLECT = 10.0, 60.0, 5.0, 0.1
TITLE = n_("{engine} is open")   # {engine}: the engine's name ("Unity"), never translated.
BODY = n_("Angelo can link to it: choose Edit in Angelo on a model in {engine}, change it here, and {engine} updates "
          "it. Linking adds Angelo Link to the project you choose and turns the live link on.")
NOT_NOW = n_("Not now")
LINK = n_("Link a project…")   # The title names the engine; no article to agree with it.

_lock = threading.Lock()
_scan = None            # The worker thread of the scan in progress.
_inbox = []             # Finished scans: (running, installed or None).
_running = frozenset()  # The last finished scan's engines.
_installed = None       # Installed engines, read once a launch (None until the first scan finished).
_memory = None
_next_scan = 0.0        # time.monotonic() of the next scan.


def _memory_file():
    from .ui import state_directory
    return os.path.join(state_directory(), engine_suggest.FILE_NAME)


def memory():
    global _memory
    if _memory is None:
        _memory = engine_suggest.Memory(_memory_file())
    return _memory


def _scan_once(with_installed):
    """A worker thread's scan; engine ids only are kept."""
    try:
        running = engine_detect.running()
        installed = engine_detect.installed() if with_installed else None
    except Exception:   # Detection is a convenience: a failure is "nothing found", never an error.
        running, installed = frozenset(), None
    with _lock:
        _inbox.append((running, installed))


def _start_scan():
    global _scan
    if _scan is not None and _scan.is_alive():
        return
    _scan = threading.Thread(target=_scan_once, args=(_installed is None,), daemon=True, name="AngeloEngineDetect")
    try:
        _scan.start()
    except RuntimeError:   # No thread to detect on (``thread_start``): nothing found this time, the timer goes on.
        _scan = None


def _link_state():
    from . import engine_link, engine_link_ui
    return engine_link.running(), engine_link_ui.remembered()


def _off():
    from . import app_notices
    return bpy.app.background or app_notices.automation_off()


def tick():
    """The watcher (see the module's description). Returns the next interval, or None to stop."""
    global _running, _installed, _next_scan
    if _off():
        return None
    with _lock:
        finished, _inbox[:] = list(_inbox), []
    for running, installed in finished:
        _running = running
        if installed is not None:
            _installed = installed
    if finished:
        _consider()
    now = time.monotonic()
    if _scan is not None and _scan.is_alive():
        return COLLECT
    if now >= _next_scan:
        _start_scan()
        _next_scan = now + (QUIET_INTERVAL if memory().done() else INTERVAL)
        return COLLECT
    return max(COLLECT, _next_scan - now)


def _eligible(engine):
    """``engine`` may be suggested now: its editor runs, the link is off and was never used, and it hasn't been."""
    link_on, link_used = _link_state()
    return engine in _running and memory().next_suggestion(frozenset({engine}), link_on, link_used) == engine


def _consider():
    """One suggestion at a time: drop a waiting card that no longer applies, then offer the next engine."""
    from . import card_host
    if any(not _eligible(card.engine) for card in card_host.queued(SuggestionCard.kind)):
        card_host.withdraw(SuggestionCard.kind)
    if card_host.queued(SuggestionCard.kind) or card_host.is_active(SuggestionCard.kind):
        return
    link_on, link_used = _link_state()
    engine = memory().next_suggestion(_running, link_on, link_used)
    if engine is not None:
        _suggest(engine)


class SuggestionCard(update_card.NoticeCard):
    kind, priority = "engine_suggest", 20   # Below every other corner card: a suggestion waits its turn.

    def __init__(self, engine):
        name = engine_detect.DISPLAY[engine]
        super().__init__("link", iface_(TITLE, engine=name), iface_(BODY, engine=name),
                         [("later", iface_(NOT_NOW), "ghost"), ("link", iface_(LINK), "primary")],
                         self._answer)
        self.engine = engine

    def opened(self, host):
        """Shown: checked once more (it may have waited behind other cards) and only then counted as suggested."""
        if not _eligible(self.engine):
            host.after(0.0, lambda: host.close("stale"))
            return
        memory().mark(self.engine)
        super().opened(host)

    def _answer(self, action, host):
        if action == "close":
            return   # NoticeCard closes it; the suggestion was already used up when it was shown.
        from . import live_target
        window = host.window()
        host.close(action)
        if action != "link":
            return
        from .splash_setup import after_close
        engine, held = self.engine.upper(), live_target.window_ref(window)

        def pick():
            target = held()
            if target is None:
                return   # The window closed or a file load replaced it meanwhile: nothing to open the picker in.
            with bpy.context.temp_override(window=target):
                bpy.ops.angelo.godot_link_install('INVOKE_DEFAULT', engine=engine)
        after_close(window, pick)   # A file browser must not open inside the card's own modal operator.


def _suggest(engine):
    from . import app_notices, card_host
    card_host.request(SuggestionCard(engine))   # Counted as suggested only when it shows (SuggestionCard.opened).
    card_host.pump(app_notices.ready)
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.5)


def _pump():
    """Show the waiting card once the window allows (another card or an interaction may hold it back)."""
    from . import app_notices, card_host
    waiting = card_host.queued(SuggestionCard.kind)
    if waiting and not _eligible(waiting[0].engine):
        card_host.withdraw(SuggestionCard.kind)   # Its engine closed or the link turned on while it waited.
        return None
    if not waiting:
        return None
    card_host.pump(app_notices.ready)
    return 0.5


def facts():
    """What the ``FEATURES`` lookup says about engines on this computer: names only."""
    from . import engine_link
    shown = [engine_detect.DISPLAY[engine] for engine in engine_detect.ENGINES]
    return {"running": [name for engine, name in zip(engine_detect.ENGINES, shown) if engine in _running],
            "installed": [name for engine, name in zip(engine_detect.ENGINES, shown) if engine in (_installed or ())],
            "live_link_on": engine_link.running()}


def agent_note():
    """The observation's ``engines`` value for this moment (``engine_suggest.Reported`` decides whether it's sent)."""
    from . import engine_link
    return engine_suggest.note(_running, engine_link.running())


def register():
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=FIRST, persistent=True)


def unregister():
    for timer in (tick, _pump):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)


def for_request(controller):
    """The ``engines`` observation for ``controller``'s next request, or None when its agent knows it already."""
    reported = getattr(controller, "_engines_reported", None)
    if reported is None:
        reported = controller._engines_reported = engine_suggest.Reported()
    return reported.take(agent_note())
