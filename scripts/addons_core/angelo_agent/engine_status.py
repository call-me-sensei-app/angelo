# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an installed render engine can render yet, and its vendor's own sign-in, in Angelo's own card.

Contract: doc/angelo/render_engines.rst, "Setting up an installed engine". Most engine add-ons render as soon as they
are on, and nothing more is shown. A commercial engine also needs its vendor's licence: a sign-in service on the
machine, a signed-in account, and a licence its render server fetches. For those, ``VENDORS`` names, by add-on
module, what Angelo needs: how to tell the vendor's sign-in service answers (without importing the add-on), how to
read whether the add-on holds a licence and which edition, the add-on's **own** operators that open the vendor's
sign-in window and switch to its free edition, and how to restart the vendor's sign-in service. Angelo never asks for
or sees the account's credentials.

Angelo checks the vendor's service **before** turning the add-on on and before asking it anything, so the vendor's own
error dialogs ("sign-in service not running") never appear: while the service does not answer, Angelo's card says so
with Retry (and Restart, where Angelo can restart it) and holds the set-up until it answers. The service is checked, and
restarted, on a worker thread: a menu or card draw and an operator read the last answer and never wait for a socket or
``launchctl``; until a first answer, an engine reads as starting and a set-up waits in the card. The card
(``card_host``, the 3D view's corner, never modal) then follows the engine: **starting** for ``GRACE`` seconds, **sign
in** while it holds no licence (Sign in to <vendor>, and Use the free edition), **ready** with its edition. A licence
Angelo cannot read (the add-on turned off, its internals changed) closes the card: the add-on's own UI remains.
``row_note`` puts the same state in the switcher's row. A vendor's own words (its engine and vendor names) are never
translated; Angelo's are.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import bpy

from . import card_kit as kit, live_windows
from .ui_text import iface_, n_

SERVICE, STARTING, SIGN_IN, READY = "service", "starting", "sign_in", "ready"   # i18n: engine states, never shown
FREE, PAID = "free", "paid"   # i18n: editions, never shown
GRACE = 20.0   # Seconds a server may take to start and fetch a licence before the card asks for a sign-in.
WIDTH = 400
SERVICE_TITLE = n_("{vendor}'s sign-in service isn't answering")
SERVICE_WHY = n_("{engine} needs it to get a licence. It was installed with {engine}; restart it, or start it "
                 "again from {vendor}'s installer.")
STARTING_TITLE = n_("Starting {engine}")
STARTING_WHY = n_("Its render server is starting and asking {vendor} for a licence. You can keep working.")
SIGN_IN_TITLE = n_("{engine} needs a {vendor} sign-in")
SIGN_IN_WHY = n_("Sign in with your {vendor} account in {vendor}'s own window. Without a paid licence, the free "
                 "edition renders too.")
SIGN_IN_ONLY = n_("Sign in with your {vendor} account in {vendor}'s own window.")   # A vendor with no free edition.
READY_TITLE = n_("{engine} is ready")
READY_FREE = n_("Signed in to {vendor} · Free edition. F12 and render jobs use it.")
READY_PAID = n_("Signed in to {vendor} · Paid licence. F12 and render jobs use it.")
ROW_SERVICE = n_("{vendor}'s sign-in service isn't answering: choose it to retry")
ROW_SIGN_IN = n_("Needs a {vendor} sign-in: choose it to sign in")
SIGN_IN_BUTTON = n_("Sign in to {vendor}")
FREE_BUTTON = n_("Use the free edition")
RETRY, RESTART, CLOSE = n_("Retry"), n_("Restart it"), n_("Close")
WARN, ERROR = "warn", "error"   # i18n: notice kinds (ui_kit_status), never shown


class Vendor:
    """What Angelo needs from one commercial engine add-on and its vendor's sign-in service."""

    def __init__(self, vendor, read, sign_in, free=None, service=None, restart=None):
        self.vendor = vendor     # The vendor's name, as it writes it.
        self.read = read         # () -> (licensed, edition): the add-on's own state; it is on.
        self.sign_in = sign_in   # The add-on's operator that opens the vendor's sign-in window.
        self.free = free         # The add-on's operator that switches to its free edition, or None.
        self.service = service   # () -> whether the vendor's sign-in service answers (no add-on import), or None.
        self.restart = restart   # () -> restart that service, or None where Angelo cannot.
        # Both run on a worker thread (``_check``), never Blender's: they touch no ``bpy``.


def answers(port_file, default, timeout=0.3):
    """Whether a local service answers on the port its port file names (``default`` without one)."""
    try:
        port = int(Path(port_file).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        port = default
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


# V-Ray for Blender (Chaos). Its render server asks Chaos' Unified Login Agent, a per-user service on 127.0.0.1 whose
# port is in ~/.chaos/UnifiedLogin/run/port (default 30301). Its licence and edition are the add-on's own: its native
# module's hasLicense() and its community_edition preference, which its licence window's "Switch to Community
# Edition" and its vray.switch_license_type operator set. vray.show_account_status opens Chaos' account window.
def _chaos_service():
    return answers(Path(os.path.expanduser("~/.chaos/UnifiedLogin/run/port")), 30301)


def _chaos_restart():
    if sys.platform == "darwin":   # Its launch agent, com.chaos.ula; -k restarts it, and it writes its port again.
        subprocess.run(["launchctl", "kickstart", "-k", "gui/%d/com.chaos.ula" % os.getuid()], timeout=10,
                       check=False, capture_output=True)


def _read_vray():
    from vray_blender.bin import VRayBlenderLib as vray
    free = bool(bpy.context.preferences.addons["vray_blender"].preferences.community_edition)
    return bool(vray.hasLicense()), FREE if free else PAID


VENDORS = {"vray_blender": Vendor("Chaos", _read_vray, "vray.show_account_status", "vray.switch_license_type",
                                  _chaos_service, _chaos_restart if sys.platform == "darwin" else None)}


_answered = {}   # module -> (monotonic time, whether its vendor's service answered), written by ``_check``.
_checking = {}   # module -> the worker thread checking (or restarting) its vendor's service.
_restarting = set()   # Modules whose vendor's service a worker is restarting: no answer until it is checked again.
_lock = threading.Lock()
FRESH = 2.0


def _check(module, vendor, restart):
    """On a worker thread: restart the vendor's service when asked, then record whether it answers."""
    if restart:
        try:
            vendor.restart()
        except (OSError, subprocess.SubprocessError) as error:
            print("Angelo: engine status: restart:", error)
    try:
        answered = bool(vendor.service())
    except Exception as error:   # A check that cannot run reads as not answering, never as a crash.
        print("Angelo: engine status: service:", module, error)
        answered = False
    with _lock:
        if _checking.get(module) is not threading.current_thread():   # A restart superseded it: its answer is old.
            return
        del _checking[module]
        _answered[module] = (time.monotonic(), answered)
        _restarting.discard(module)


def _start(module, vendor, restart=False):
    with _lock:
        if module in _checking and not restart:
            return
        if restart:
            _restarting.add(module)
        thread = _checking[module] = threading.Thread(target=_check, args=(module, vendor, restart), daemon=True,
                                                      name="Angelo engine service")   # i18n: a thread name, never shown
    thread.start()


def service_answers(module, fresh=False):
    """Whether ``module``'s vendor sign-in service answered when last checked (True without one; None before a first
    answer and while it restarts). Never waits: an answer older than ``FRESH`` s (or ``fresh``) starts a check on a
    worker thread, and the caller reads its result on a later call."""
    vendor = VENDORS.get(module)
    if vendor is None or vendor.service is None:
        return True
    with _lock:
        seen, restarting = _answered.get(module), module in _restarting
    if fresh or seen is None or time.monotonic() - seen[0] > FRESH:
        _start(module, vendor)
    return None if seen is None or restarting else seen[1]


def answered_now(module):
    """Whether ``module``'s vendor service answered within the last ``FRESH`` s (True without one)."""
    vendor = VENDORS.get(module)
    if vendor is None or vendor.service is None:
        return True
    with _lock:
        seen = _answered.get(module)
        return seen is not None and seen[1] and module not in _restarting and time.monotonic() - seen[0] <= FRESH


def restart_service(module):
    """Restart ``module``'s vendor service on a worker thread, then check it again; False where Angelo cannot."""
    vendor = VENDORS.get(module)
    if vendor is None or vendor.restart is None or vendor.service is None:
        return False
    _start(module, vendor, restart=True)
    return True


def settle(timeout=10.0):
    """Wait for the checks in flight (the tests' and the QA suite's: Angelo itself never waits on one)."""
    with _lock:
        threads = list(_checking.values())
    for thread in threads:
        thread.join(timeout)


def state(module, started, on=True, now=None):
    """``(state, edition)`` of the engine add-on ``module`` (``on``: enabled), chosen at ``started`` (monotonic);
    ``(None, None)`` when its licence cannot be read. An add-on without a vendor entry is ready at once."""
    vendor = VENDORS.get(module)
    if vendor is None:
        return READY, None
    answered = service_answers(module)
    if answered is None:   # Not checked yet, or restarting: nothing of the add-on is asked meanwhile.
        return STARTING, None
    if not answered:
        return SERVICE, None
    if not on:
        return STARTING, None
    try:
        licensed, edition = vendor.read()
    except Exception as error:   # An add-on that changed its internals: its own UI remains.
        print("Angelo: engine status:", module, error)
        return None, None
    if licensed:
        return READY, edition
    elapsed = (time.monotonic() if now is None else now) - started
    return (STARTING if elapsed < GRACE else SIGN_IN), edition


def row_note(module, on=True):
    """The switcher row's line for an engine add-on (``on``: enabled) that cannot render yet, as ``(template,
    fields)``, or None."""
    vendor = VENDORS.get(module)
    if vendor is None:
        return None
    found, _edition = state(module, time.monotonic() - GRACE, on=on)
    fields = {"vendor": vendor.vendor}
    return {SERVICE: (ROW_SERVICE, fields), SIGN_IN: (ROW_SIGN_IN, fields)}.get(found)


def module_of(engine):
    """The add-on module of a registered engine identifier, or None for Blender's own."""
    from . import render_extensions
    from .render_engines import engine_classes
    if engine not in render_extensions.addon_engines():
        return None
    cls = next((cls for cls in engine_classes() if cls.bl_idname == engine), None)
    return cls.__module__.split(".")[0] if cls is not None else None


class EngineStatusCard(kit.BaseCard):
    kind, priority, cancel_action, default_action, outside_dismisses, modal = \
        "engine_status", 40, "close", None, False, False

    def __init__(self, module, engine, pending=None):
        self.module, self.engine, self.vendor = module, engine, VENDORS[module]
        self.pending = pending   # The switcher's set-up row, held while the vendor's service does not answer.
        self.started, self.shown, self.error = time.monotonic(), None, ""
        self._version = 0

    @property
    def animated(self):
        return self.shown is not None and self.shown[0] == STARTING

    def version(self):
        return (self._version, self.shown, self.error)

    def refresh(self, host=None, fresh=False):
        if fresh:
            service_answers(self.module, fresh=True)
        if self.pending is not None and answered_now(self.module):   # Only on an answer: never the vendor's dialog.
            self._set_up(host)
        found = state(self.module, self.started, on=self.pending is None)
        changed = found != self.shown
        self.shown = found
        if found[0] is None and host is not None:
            host.close("unreadable")
        return changed

    def _set_up(self, host):
        from . import engine_setup, render_engines
        row, self.pending = self.pending, None
        try:
            added = engine_setup.set_up(row["module"], row["name"], row["source"])
        except engine_setup.SetupError as error:
            self.error = iface_(error.template, **error.fields)
            return
        window = host.window() if host is not None else bpy.context.window
        scene = window.scene if window is not None else bpy.context.scene
        render_engines.apply(scene, added[0])
        self.engine, self.started = render_engines.current_label(scene), time.monotonic()

    def layout(self, view, region_w, region_h, scale, ui):
        from .update_card import corner_plan
        if self.shown is None:
            self.refresh()
        return kit.fit(lambda size: corner_plan(self._card(view, size, region_w, ui), region_w, region_h),
                       region_w, region_h, scale)

    def _heading(self, b, icon, title, subtitle):
        s, top = b.s, b.y
        b.icon(icon, b.pad, b.y, 40)
        text_x = b.pad + 54 * s
        text_w = b.width - text_x - b.pad - 24 * s
        b.paragraph(title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(subtitle, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + 40 * s) + 14 * s

    def _card(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        names = {"engine": self.engine, "vendor": self.vendor.vendor}
        code, edition = self.shown or (STARTING, None)
        close = ("close", None, iface_(CLOSE), "ghost", True)
        if self.error:
            self._heading(b, "alert", iface_(SERVICE_TITLE if code == SERVICE else SIGN_IN_TITLE, **names), "")
            b.notice(ERROR, self.error)
            b.gap(12)
            b.buttons([close])
        elif code == SERVICE:
            self._heading(b, "alert", iface_(SERVICE_TITLE, **names), iface_(SERVICE_WHY, **names))
            buttons = [close, ("retry", None, iface_(RETRY), "secondary" if self.vendor.restart else "primary", True)]
            if self.vendor.restart:
                buttons.append(("restart", None, iface_(RESTART), "primary", True))
            b.buttons(buttons)
        elif code == STARTING:
            self._heading(b, "spark", iface_(STARTING_TITLE, **names), iface_(STARTING_WHY, **names))
            b.chip("running")
            b.gap(14)
            b.buttons([close])
        elif code == SIGN_IN:
            self._heading(b, "alert", iface_(SIGN_IN_TITLE, **names),
                          iface_(SIGN_IN_WHY if self.vendor.free else SIGN_IN_ONLY, **names))
            buttons = [close]
            if self.vendor.free and edition != FREE:
                buttons.append(("free", None, iface_(FREE_BUTTON), "secondary", True))
            buttons.append(("sign_in", None, iface_(SIGN_IN_BUTTON, **names), "primary", True))
            b.buttons(buttons)
        else:
            self._heading(b, "check", iface_(READY_TITLE, **names),
                          iface_(READY_FREE if edition == FREE else READY_PAID, **names))
            b.buttons([("close", None, iface_(CLOSE), "primary", True)])
        b.close_button("close")
        return b

    def act(self, action, value, host):
        if action == "sign_in" and self.shown and self.shown[0] == SIGN_IN:
            _call(self.vendor.sign_in, host)   # The vendor's own window; the card follows its outcome.
        elif action == "free" and self.shown and self.shown[0] == SIGN_IN:
            if state(self.module, self.started)[1] != FREE:   # Its operator toggles: never back to paid.
                _call(self.vendor.free, host, invoke=True)   # Its own confirmation when a render would stop.
                self.started = time.monotonic()   # Its server restarts with the free edition.
        elif action == "restart" and self.vendor.restart:
            restart_service(self.module)   # A worker restarts it and checks it again; the card polls the answer.
        elif action in {"close", "dismiss"}:
            host.close("closed")
            return
        self.refresh(host, fresh=action != "restart")   # Retry (and every click) checks the service again.
        self._version += 1
        host.redraw()

    def opened(self, host):
        host.after(1.0, lambda: _poll(self, host))


def _call(idname, host, invoke=False):
    group, name = idname.split(".")
    operator = getattr(getattr(bpy.ops, group), name)
    window = host.window() if host is not None else None
    area = next((a for a in live_windows.areas(window) if a.type == 'VIEW_3D'), None) if window is not None else None
    try:
        if window is not None:
            with bpy.context.temp_override(window=window, area=area):
                operator('INVOKE_DEFAULT' if invoke else 'EXEC_DEFAULT')
        else:
            operator('EXEC_DEFAULT')
    except (AttributeError, RuntimeError, TypeError) as error:
        print("Angelo: engine status:", idname, error)


def _poll(card, host):
    if card.refresh(host):
        card._version += 1
        host.redraw()
    return 0.25 if card.pending is not None else 1.0   # A held set-up goes on soon after its service answers.


def show(module, engine, pending=None):
    """Open the card for ``engine`` (from add-on ``module``) when its vendor needs anything; True when shown."""
    from . import card_host
    if module not in VENDORS or card_host.is_active(EngineStatusCard.kind):
        return False
    card = EngineStatusCard(module, engine, pending)
    card.refresh()
    if card.shown[0] is None or (card.shown[0] == READY and pending is None and card.shown[1] is None):
        return False
    card_host.request(card)
    card_host.pump(lambda: True)
    return True


def hold_set_up(row):
    """Before a set-up: unless the vendor's sign-in service answered within ``FRESH`` s (the switcher's rows check it
    as the menu draws), hold it in the card (True), which checks it again and sets it up once it answers, instead of
    turning the add-on on, whose own error dialog would appear."""
    if answered_now(row["module"]):
        return False
    service_answers(row["module"], fresh=True)
    return show(row["module"], row["name"], pending=row)


def after_set_up(window, module, engine):
    """After the switcher set an engine up: its vendor's status, when it has one."""
    return show(module, engine)


def after_choice(scene):
    """After the switcher chose an installed engine: the card only while that engine is not ready."""
    module = module_of(scene.render.engine)
    if module is None or module not in VENDORS or state(module, time.monotonic() - GRACE)[0] in (READY, None):
        return False
    from .render_engines import current_label
    return show(module, current_label(scene))
