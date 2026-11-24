# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux desktop's own services a background job and the marble animation use, over the session D-Bus.

* ``notify``: a desktop notification (``org.freedesktop.Notifications.Notify``), what GNOME, KDE Plasma, XFCE,
  Cinnamon, MATE and every notification daemon (dunst, mako, swaync) serve. Shown under Angelo's name.
* ``InhibitSuspend``: idle suspend is held off while a job runs, through the desktop portal
  (``org.freedesktop.portal.Inhibit.Inhibit`` with the Suspend flag), which GNOME and KDE map to their own power
  managers. The display may still sleep, and a suspend the person asks for (the lid, the menu) still happens.
* ``reduced_motion``: the desktop's reduced-motion setting through the portal's settings
  (``org.freedesktop.appearance`` ``reduced-motion``, else GNOME's ``enable-animations``).

**The main thread never waits on the bus.** One worker thread (``Service``) opens its own connection
(``desktop_linux_bus.GioBus``), learns once which services are there (running or D-Bus activatable), and does every
call. The main thread queues work and reads answers the worker left: ``notify`` posts when the notification service
is known to be there; a hold is a ``Hold`` whose outcome the worker fills in; reduced motion is the last answer read
(refreshed every ``MOTION_SECONDS``). A bus that stalls, a portal that never answers, or a connection that never
opens therefore costs the worker time, never the interface.

**Holds.** Inhibit carries a ``handle_token``, so the worker knows the request's object path
(``/org/freedesktop/portal/desktop/request/<sender>/<token>``) before any answer. When Inhibit times out, the worker
sends Close for that path at once: the bus delivers a connection's calls in order, so a portal that answers late
creates the request and then closes it. A portal can still answer its backend after the Close (the protocol does
not order the two), so the guarantee is the connection's: every inhibition ends with the worker's connection, at the
latest when Angelo quits, and a crash never leaves the computer awake. Releasing closes the request the same way. ``KeepAwake`` holds
at most once per run of jobs: a hold that fails or is unsupported is not tried again until the jobs end.

Without a session bus (SSH, a container) or without the service, each answers "unsupported". The host tests drive
``Service`` with a recording stand-in bus; on Linux they also talk to a private bus daemon of their own.
"""

import itertools
import os
import queue
import threading
import time

from . import guard_work
from .desktop_linux_bus import GioBus, Unavailable

NOTIFICATIONS = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications")
PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")
INHIBIT_SUSPEND = 4          # org.freedesktop.portal.Inhibit flags: 1 logout, 2 user switch, 4 suspend, 8 idle.
CALL_TIMEOUT_MS = 25000      # The worker's bound per call (D-Bus's own default); the main thread never waits on it.
SETTING_TIMEOUT_MS = 2000    # A settings read: on timeout the worker stops asking, until the next refresh.
MOTION_SECONDS = 30.0        # How long a reduced-motion answer is kept before the worker reads it again.
UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"
_tokens = itertools.count(1)


def _session_bus_configured(environ):
    runtime = environ.get("XDG_RUNTIME_DIR")
    return bool(environ.get("DBUS_SESSION_BUS_ADDRESS") or (runtime and os.path.exists(os.path.join(runtime, "bus"))))


def request_path(unique_name, token):
    """The portal request object path for a call from ``unique_name`` (``:1.42``) with ``handle_token`` ``token``."""
    return "%s/request/%s/%s" % (PORTAL_PATH, unique_name.lstrip(":").replace(".", "_"), token)


class Service:
    """One worker thread and its bus. ``state``: ``starting``, ``ready`` or ``unavailable``; ``present``: which of
    the two services the bus has (known once ``ready``)."""

    def __init__(self, bus_factory=None, environ=None):
        self._factory = bus_factory or GioBus
        self._environ = os.environ if environ is None else environ
        self._jobs = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._thread = None
        self.bus, self.state, self.present = None, "starting", {}
        self.motion, self.motion_read_at, self._motion_queued = None, None, False

    def submit(self, work):
        """Run ``work(bus)`` on the worker, in order. Never waits. Queued as a ``guard_work`` item, so work a confined
        script queued runs confined on the worker too (the class rule for every cross-thread consumer)."""
        item = guard_work.work(work)
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="angelo-desktop-bus", daemon=True)
                self._thread.start()
        self._jobs.put(item)

    def start(self):
        self.submit(lambda bus: None)

    def wait_idle(self, timeout):
        """Tests: wait until the worker has done everything queued so far. True when it has."""
        done = threading.Event()
        self.submit(lambda bus: done.set())
        return done.wait(timeout)

    def _run(self):
        run_work = guard_work.run   # Fixed for this loop's life: rebinding the module name changes nothing here.
        try:
            if self._factory is GioBus and not _session_bus_configured(self._environ):
                raise Unavailable("no session bus")
            self.bus = self._factory()
            self.present = {name: _service_present(self.bus, name) for name in (NOTIFICATIONS[0], PORTAL)}
            self.state = "ready"
        except Unavailable:
            self.state = "unavailable"
        except Exception as error:   # noqa: BLE001 - a broken GIO is "unavailable", never a crash in a thread.
            print("Angelo: the desktop bus could not open:", type(error).__name__, error)
            self.state = "unavailable"
        while True:
            item = self._jobs.get()
            try:
                run_work(item, self.bus)
            except Exception as error:   # noqa: BLE001 - one failed call never stops the worker.
                print("Angelo: a desktop bus call failed:", type(error).__name__, error)

    def available(self, name):
        """True when the bus has ``name``; False when it has not, or when there is no bus; None while still starting."""
        if self.state == "starting":
            return None
        return self.state == "ready" and bool(self.present.get(name))


def _service_present(bus, name):
    """Whether ``name`` runs on the bus or the bus can start it (D-Bus activation, as the portal usually is)."""
    try:
        if bus.call(*BUS, "NameHasOwner", ("s",), (name,), timeout_ms=CALL_TIMEOUT_MS) == (True,):
            return True
        (names,) = bus.call(*BUS, "ListActivatableNames", timeout_ms=CALL_TIMEOUT_MS)
    except (Unavailable, ValueError):
        return False
    return name in names


_service = None
_service_lock = threading.Lock()


class _Refused:
    """What a caller on a confined thread gets before the worker exists: no bus, nothing queued."""
    state, present, motion, motion_read_at, _motion_queued = "unavailable", {}, None, None, True

    def available(self, name):
        return False

    def submit(self, work):
        pass


def service():
    """The process's ``Service``. Its worker thread starts at add-on registration on the main thread (``__init__``),
    or on the first call from an unconfined thread; a thread made while a script is confined would run confined for
    its whole life (the guard's thread trampoline), refusing every later bus call. So a confined caller before the
    worker exists gets ``_Refused`` (no bus) and nothing is started."""
    global _service
    with _service_lock:
        if _service is None:
            from . import script_confinement
            if script_confinement.confined():
                return _Refused()
            _service = Service()
            _service.start()
        return _service


def notify(title, body, app_name="Angelo", service_=None):
    """Hand a desktop notification to the worker. Returns ``posted`` when the bus has a notification service, else
    ``unsupported`` (no bus, no service, or the bus not open yet). Never waits."""
    owner = service_ or service()
    if not owner.available(NOTIFICATIONS[0]):
        return "unsupported"

    def post(bus):
        bus.call(*NOTIFICATIONS, "Notify", ("s", "u", "s", "s", "s", "as", "a{sv}", "i"),
                 (app_name, 0, "", title, body, [], {"urgency": ("y", 1)}, -1), timeout_ms=CALL_TIMEOUT_MS)
    owner.submit(post)
    return "posted"


class Hold:
    """One requested hold. ``outcome``: None while pending, ``held`` once the portal answered, else ``failed`` or
    ``unsupported``; any error at all fails it (a refusal, a bad value), never a hold left reading as held."""

    def __init__(self, token):
        self.token, self.path, self.outcome = token, None, None


class InhibitSuspend:
    """``KeepAwake``'s Linux backend. ``hold`` returns a ``Hold`` at once; the worker asks the portal. Construction
    raises OSError when the bus is known to have no portal (``KeepAwake`` says "unsupported")."""

    def __init__(self, service_=None):
        self.service = service_ or service()
        if self.service.available(PORTAL) is False:
            raise OSError("no session bus or no desktop portal on it")

    def hold(self, reason):
        hold = Hold("angelo_keep_awake_%d" % next(_tokens))

        def inhibit(bus):
            if bus is None or not self.service.present.get(PORTAL):
                hold.outcome = "unsupported"
                return
            try:
                hold.path = request_path(bus.unique_name, hold.token)
                bus.call(PORTAL, PORTAL_PATH, "org.freedesktop.portal.Inhibit", "Inhibit", ("s", "u", "a{sv}"),
                         ("", INHIBIT_SUSPEND, {"reason": ("s", reason), "handle_token": ("s", hold.token)}),
                         timeout_ms=CALL_TIMEOUT_MS)
                hold.outcome = "held"
            except Unavailable as error:
                hold.outcome = "failed"
                if error.timed_out:   # A late answer would create the request: close it right behind the Inhibit.
                    _close(bus, hold.path)
            except BaseException:
                hold.outcome = "failed"   # Refused, a bad value, anything: nothing is held, and it says so.
                raise
        self.service.submit(inhibit)
        return hold

    def outcome(self, hold):
        return hold.outcome

    def release(self, hold):
        self.service.submit(lambda bus: _close(bus, hold.path) if bus is not None and hold.path else None)


def _close(bus, path):
    try:
        bus.call(PORTAL, path, "org.freedesktop.portal.Request", "Close", timeout_ms=CALL_TIMEOUT_MS)
    except Unavailable:
        pass   # Not created, or gone already: nothing is held.


def read_setting(bus, namespace, key):
    """A portal setting's value: ``ReadOne`` (settings version 2), else the older ``Read`` only when the portal has no
    ``ReadOne``. Raises ``Unavailable`` on a timeout (the caller stops asking)."""
    try:
        (value,) = bus.call(PORTAL, PORTAL_PATH, "org.freedesktop.portal.Settings", "ReadOne", ("s", "s"),
                            (namespace, key), timeout_ms=SETTING_TIMEOUT_MS)
        return value
    except Unavailable as error:
        if error.timed_out:
            raise
        if error.remote != UNKNOWN_METHOD:
            return None
    try:
        (value,) = bus.call(PORTAL, PORTAL_PATH, "org.freedesktop.portal.Settings", "Read", ("s", "s"),
                            (namespace, key), timeout_ms=SETTING_TIMEOUT_MS)
        return value
    except Unavailable as error:
        if error.timed_out:
            raise
        return None


def read_reduced_motion(bus):
    """True, False, or None (can't tell), read on the worker: the portal key first, then GNOME's."""
    try:
        value = read_setting(bus, "org.freedesktop.appearance", "reduced-motion")
        if isinstance(value, int) and not isinstance(value, bool):
            return value == 1   # 0 no preference, 1 reduced motion.
        value = read_setting(bus, "org.gnome.desktop.interface", "enable-animations")
    except Unavailable:
        return None
    return (not value) if isinstance(value, bool) else None


def reduced_motion(service_=None, clock=time.monotonic):
    """The last answer the worker read (None until it has one), asking it to read again when the answer is older than
    ``MOTION_SECONDS``. Never waits."""
    owner = service_ or service()
    now = clock()
    if not owner._motion_queued and (owner.motion_read_at is None or now - owner.motion_read_at > MOTION_SECONDS):
        owner._motion_queued = True

        def read(bus):
            try:
                if owner.available(PORTAL):
                    owner.motion = read_reduced_motion(bus)
                owner.motion_read_at = clock()
            finally:
                owner._motion_queued = False
        owner.submit(read)
    return owner.motion
