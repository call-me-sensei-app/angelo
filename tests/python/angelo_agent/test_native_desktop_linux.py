# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux desktop services over the session bus (``desktop_linux``; doc/angelo/long_jobs.rst, "Away from the computer").

Covered with a recording stand-in bus driven by the real worker (``Service``):
- **Nothing waits on the main thread.** ``notify``, ``KeepAwake.hold`` and ``reduced_motion`` return at once, even
  when the bus takes seconds to answer.
- **Notifications.** A job's notification is one ``Notify`` call under Angelo's name. It is "unsupported" without a
  bus or a notification service.
- **Keep-awake.** A hold is one portal ``Inhibit`` with the Suspend flag, the reason and a ``handle_token``; releasing
  closes the request at the path that token names. A portal that times out is asked once per run of jobs, however
  many polls follow; the request it may still create is closed right behind the Inhibit; after the jobs end, the next
  job asks again. A bus with no portal is "unsupported", latched the same way.
- **Reduced motion.** The portal's ``reduced-motion`` first, then GNOME's ``enable-animations``. ``Read`` is asked
  only when the portal has no ``ReadOne``, and a timeout stops the reading. The answer is kept and refreshed on the
  worker.
- **Job notices.** ``job_desktop`` uses these on Linux.

On a Linux host with ``dbus-daemon``, ``GioBus`` talks to a private bus daemon of the test's own (with no service
folders): real GIO marshalling both ways, a remote error's name, and "unsupported" for both services. No real session
bus is touched.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import desktop_linux, desktop_linux_bus, desktop_os, motion_preference  # noqa: E402
from angelo_agent.desktop_linux_bus import Unavailable  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.
import machine_clock  # noqa: E402  "Returned at once" bounds, relative to this machine.

INSTANT_S = 0.2   # window: what "returns at once" allows a call that queues work, on a loaded CI machine.
BOTH = {("org.freedesktop.DBus", "NameHasOwner"): (True,)}


class FakeBus:
    """A bus that answers from a table: ``(interface, method, args)`` or ``(interface, method)``. An answer that is a
    callable is called (it may wait or raise)."""

    unique_name = ":1.7"

    def __init__(self, answers=None):
        self.calls, self.answers, self.timeouts = [], dict(answers or {}), []

    def call(self, destination, path, interface, method, signature=(), args=(), timeout_ms=0):
        self.calls.append((destination, path, interface, method, tuple(signature), tuple(args)))
        self.timeouts.append(timeout_ms)
        try:
            answer = self.answers.get((interface, method, tuple(args)))
        except TypeError:   # A dict argument (a{sv}): only the method's own answer.
            answer = None
        answer = answer if answer is not None else self.answers.get((interface, method))
        if callable(answer):
            answer = answer()
        if answer is None or isinstance(answer, Exception):
            raise answer or Unavailable("no such service", "org.freedesktop.DBus.Error.ServiceUnknown")
        return answer

    def methods(self, name):
        return [call for call in self.calls if call[3] == name]


def started_factory(factory):
    service = desktop_linux.Service(bus_factory=factory)
    service.start()
    assert service.wait_idle(CAP_S)
    return service


def started(bus):
    return started_factory(lambda: bus)


class NotifyTests(unittest.TestCase):
    def test_one_notification_under_angelos_name(self):
        bus = FakeBus({**BOTH, ("org.freedesktop.Notifications", "Notify"): (7,)})
        service = started(bus)
        self.assertEqual(desktop_linux.notify("Render finished", "4 min · shot.png", service_=service), "posted")
        self.assertTrue(service.wait_idle(CAP_S))
        (destination, path, interface, method, signature, args), = bus.methods("Notify")
        self.assertEqual((destination, path), ("org.freedesktop.Notifications", "/org/freedesktop/Notifications"))
        self.assertEqual(signature, ("s", "u", "s", "s", "s", "as", "a{sv}", "i"))
        self.assertEqual(args[0], "Angelo")
        self.assertEqual(args[3:5], ("Render finished", "4 min · shot.png"))
        self.assertEqual(args[5], [], "no actions: nothing to answer")

    def test_no_service_or_no_bus(self):
        absent = started(FakeBus({("org.freedesktop.DBus", "NameHasOwner"): (False,),
                                  ("org.freedesktop.DBus", "ListActivatableNames"): (["a.b"],)}))
        self.assertEqual(desktop_linux.notify("a", "b", service_=absent), "unsupported")

        def no_bus():
            raise Unavailable("no session bus")
        self.assertEqual(desktop_linux.notify("a", "b", service_=started_factory(no_bus)), "unsupported")

    def test_the_main_thread_never_waits_on_the_bus(self):
        bus = FakeBus({**BOTH, ("org.freedesktop.Notifications", "Notify"): lambda: time.sleep(1.0) or (1,)})
        service = started(bus)
        window = machine_clock.start(self)
        status = desktop_linux.notify("a", "b", "Angelo", service)
        window.assert_within(INSTANT_S, "notify waited on a bus that takes a second to answer")
        self.assertEqual(status, "posted")
        self.assertTrue(service.wait_idle(CAP_S))


class HoldTests(unittest.TestCase):
    def awake(self, bus):
        service = started(bus)
        return desktop_os.KeepAwake("linux", backend=desktop_linux.InhibitSuspend(service)), service

    def test_hold_and_release_by_the_tokens_path(self):
        bus = FakeBus({**BOTH, ("org.freedesktop.portal.Inhibit", "Inhibit"): ("/x",),
                       ("org.freedesktop.portal.Request", "Close"): ()})
        awake, service = self.awake(bus)
        self.assertTrue(awake.hold("A render is running"))
        self.assertTrue(service.wait_idle(CAP_S))
        (_d, _p, _i, _m, signature, args), = bus.methods("Inhibit")
        self.assertEqual(signature, ("s", "u", "a{sv}"))
        self.assertEqual(args[:2], ("", desktop_linux.INHIBIT_SUSPEND))
        token = args[2]["handle_token"][1]
        self.assertEqual(args[2]["reason"], ("s", "A render is running"))
        self.assertEqual((awake.status, awake.held), ("held", True))
        awake.release()
        self.assertTrue(service.wait_idle(CAP_S))
        (_d, path, interface, _m, _s, _a), = bus.methods("Close")
        self.assertEqual(path, "/org/freedesktop/portal/desktop/request/1_7/" + token)
        self.assertEqual(desktop_linux.INHIBIT_SUSPEND, 4, "the portal's Suspend flag; never logout or idle")

    def test_a_portal_that_times_out_is_asked_once_per_run_of_jobs(self):
        def times_out():
            time.sleep(0.3)
            raise Unavailable("Timeout was reached", timed_out=True)
        bus = FakeBus({**BOTH, ("org.freedesktop.portal.Inhibit", "Inhibit"): times_out,
                       ("org.freedesktop.portal.Request", "Close"): ()})
        awake, service = self.awake(bus)
        for _poll in range(20):   # A job's polls, four a second: the first asks, the rest never wait.
            window = machine_clock.start(self)
            awake.hold("A render is running")
            window.assert_within(INSTANT_S, "a poll waited on the portal")
        self.assertTrue(service.wait_idle(CAP_S))
        for _poll in range(5):
            self.assertFalse(awake.hold("A render is running"))
        self.assertEqual(awake.status, "failed")
        self.assertEqual(len(bus.methods("Inhibit")), 1, "latched until the jobs end")
        token = bus.methods("Inhibit")[0][5][2]["handle_token"][1]
        closes = [call[1] for call in bus.methods("Close")]
        self.assertEqual(closes, ["/org/freedesktop/portal/desktop/request/1_7/" + token],
                         "a late request is closed right behind the timed-out Inhibit")
        awake.release()   # The run of jobs ended; the next job asks again.
        awake.hold("Another render")
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertEqual(len(bus.methods("Inhibit")), 2)

    def test_no_portal_is_unsupported_and_latched(self):
        bus = FakeBus({("org.freedesktop.DBus", "NameHasOwner"): (False,),
                       ("org.freedesktop.DBus", "ListActivatableNames"): (["org.freedesktop.Notifications"],)})
        service = started(bus)
        with self.assertRaises(OSError):
            desktop_linux.InhibitSuspend(service)
        with mock.patch.object(desktop_linux, "service", return_value=service):
            awake = desktop_os.KeepAwake("linux")
            self.assertFalse(awake.hold("x"))
            self.assertEqual(awake.status, "unsupported")
        activatable = started(FakeBus({("org.freedesktop.DBus", "NameHasOwner"): (False,),
                                       ("org.freedesktop.DBus", "ListActivatableNames"): ([desktop_linux.PORTAL],)}))
        desktop_linux.InhibitSuspend(activatable)   # Activatable counts: the bus starts it.

    def test_a_hold_asked_before_the_bus_is_open_settles_later(self):
        gate = threading.Event()

        def slow_bus():
            gate.wait(CAP_S)
            raise Unavailable("no session bus")
        service = desktop_linux.Service(bus_factory=slow_bus)
        service.start()
        awake = desktop_os.KeepAwake("linux", backend=desktop_linux.InhibitSuspend(service))
        self.assertTrue(awake.hold("x"), "requested; the worker answers later")
        gate.set()
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertEqual((awake.status, awake.held), ("unsupported", False))


class Review1C3Tests(unittest.TestCase):
    """Re-review of 1c31d63dead: NIT-2 (a queued hold is pending, not held, until the portal answers), NIT-3 (an error
    reading the bus's unique name fails the hold), NIT-4 (registration calls ``desktop_linux.service()``)."""

    def test_a_hold_is_pending_until_the_portal_answers(self):
        gate = threading.Event()
        bus = FakeBus({**BOTH, ("org.freedesktop.portal.Inhibit", "Inhibit"): lambda: gate.wait(CAP_S) and ("/x",),
                       ("org.freedesktop.portal.Request", "Close"): ()})
        service = started(bus)
        awake = desktop_os.KeepAwake("linux", backend=desktop_linux.InhibitSuspend(service))
        self.assertTrue(awake.hold("x"), "asked: the caller need not ask again")
        self.assertEqual((awake.status, awake.held), ("pending", False), "nothing is held before the portal answers")
        gate.set()
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertEqual((awake.status, awake.held), ("held", True))
        awake.release()
        self.assertEqual((awake.status, awake.held), ("idle", False))

    def test_an_unreadable_unique_name_fails_the_hold(self):
        class NoName(FakeBus):
            @property
            def unique_name(self):
                raise AttributeError("no unique name")
        bus = NoName({**BOTH, ("org.freedesktop.portal.Inhibit", "Inhibit"): ("/x",)})
        service = started(bus)
        awake = desktop_os.KeepAwake("linux", backend=desktop_linux.InhibitSuspend(service))
        awake.hold("x")
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertEqual((awake.status, awake.held), ("failed", False))
        self.assertEqual(bus.methods("Inhibit"), [], "nothing asked without the request's path")

    def test_registration_starts_the_worker(self):
        """``register()``'s Linux GUI branch calls ``desktop_linux.service()``: run it, not read its text."""
        source = (Path(desktop_linux.__file__).parent / "__init__.py").read_text(encoding="utf-8")
        register = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "register")
        calls = [node for node in ast.walk(register) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "service"
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "desktop_linux"]
        self.assertEqual(len(calls), 1, "register() calls desktop_linux.service() once")


class ReviewD67Tests(unittest.TestCase):
    """Re-review of d67b4e25575: SHOULD-3 (any error fails a hold), SHOULD-4 (the worker is never born confined),
    SHOULD-5 (the worker runs only guard_work items, confined when their maker was), NIT-5 (no GVariant before a bad
    value is refused)."""

    def test_any_error_fails_the_hold(self):
        for error in (PermissionError("refused"), ValueError("bad value"), AttributeError("no unique name")):
            def raises(error=error):
                raise error
            bus = FakeBus({**BOTH, ("org.freedesktop.portal.Inhibit", "Inhibit"): raises})
            service = started(bus)
            awake = desktop_os.KeepAwake("linux", backend=desktop_linux.InhibitSuspend(service))
            with self.subTest(type(error).__name__):
                awake.hold("x")
                self.assertTrue(service.wait_idle(CAP_S))
                self.assertEqual((awake.status, awake.held), ("failed", False))

    def test_a_confined_first_caller_starts_nothing(self):
        from angelo_agent import script_confinement
        with mock.patch.object(desktop_linux, "_service", None), \
                mock.patch.object(script_confinement, "confined", return_value=True), \
                mock.patch.object(desktop_linux, "Service", side_effect=AssertionError("a worker born confined")):
            refused = desktop_linux.service()
            self.assertEqual(desktop_linux.notify("a", "b"), "unsupported")
            self.assertIsNone(desktop_linux.reduced_motion())
            with self.assertRaises(OSError):
                desktop_linux.InhibitSuspend()
            self.assertIsNone(desktop_linux._service, "nothing kept for later callers")
        self.assertEqual(refused.available(desktop_linux.PORTAL), False)

    def test_only_guard_work_items_run_and_confined_work_stays_confined(self):
        from angelo_agent import guard_work, script_confinement
        ran = []
        service = started(FakeBus(dict(BOTH)))
        service._jobs.put(lambda bus: ran.append("raw"))   # Not a WorkItem: refused by guard_work.run.
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertEqual(ran, [], "a raw callable on the queue never runs")
        made = []
        record = lambda *a: made.append(a) or guard_work._PythonWorkItem(*a)   # noqa: E731
        with mock.patch.object(guard_work, "WorkItem", side_effect=record), \
                mock.patch.object(script_confinement, "confined", return_value=True):
            service.submit(lambda bus: None)
        self.assertEqual(len(made), 1, "submit queues a guard_work item")
        self.assertTrue(service.wait_idle(CAP_S))

    def test_a_bad_value_is_refused_before_any_gvariant_is_made(self):
        made = []

        class Glib:
            def __getattr__(self, name):
                return lambda *args: made.append(name) or 1
        bus = desktop_linux_bus.GioBus.__new__(desktop_linux_bus.GioBus)
        bus.glib = bus.gio = Glib()
        with self.assertRaises(UnicodeEncodeError):
            bus.call(*desktop_linux.NOTIFICATIONS, "Notify", ("s", "s"), ("ok", "\udc80"))
        self.assertEqual([name for name in made if name.startswith("g_variant_new")], [])


class MotionTests(unittest.TestCase):
    APPEARANCE = ("org.freedesktop.appearance", "reduced-motion")
    GNOME = ("org.gnome.desktop.interface", "enable-animations")

    def settings(self, values, methods=("ReadOne", "Read")):
        answers = {}
        for (namespace, key), value in values.items():
            for method in methods:
                answers[("org.freedesktop.portal.Settings", method, (namespace, key))] = (value,)
        if "ReadOne" not in methods:
            answers[("org.freedesktop.portal.Settings", "ReadOne")] = Unavailable(
                "no method", desktop_linux.UNKNOWN_METHOD)
        return FakeBus(answers)

    def test_the_portal_key_first_then_gnome(self):
        read = desktop_linux.read_reduced_motion
        self.assertTrue(read(self.settings({self.APPEARANCE: 1, self.GNOME: True})))
        self.assertFalse(read(self.settings({self.APPEARANCE: 0, self.GNOME: False})))
        self.assertTrue(read(self.settings({self.GNOME: False})))
        self.assertFalse(read(self.settings({self.GNOME: True})))
        self.assertTrue(read(self.settings({self.APPEARANCE: 1}, methods=("Read",))), "an older portal: Read")
        self.assertIsNone(read(FakeBus()))

    def test_read_only_when_readone_is_missing_and_a_timeout_stops(self):
        bus = FakeBus({("org.freedesktop.portal.Settings", "ReadOne"): Unavailable("Timeout", timed_out=True)})
        self.assertIsNone(desktop_linux.read_reduced_motion(bus))
        self.assertEqual([call[3] for call in bus.calls], ["ReadOne"], "one timeout: nothing more is asked")
        missing = FakeBus({("org.freedesktop.portal.Settings", "ReadOne"): Unavailable(
            "not found", "org.freedesktop.portal.Error.NotFound")})
        desktop_linux.read_reduced_motion(missing)
        self.assertNotIn("Read", [call[3] for call in missing.calls], "a missing key is not a missing method")

    def test_the_answer_is_read_on_the_worker_and_kept(self):
        bus = self.settings({self.GNOME: False})
        bus.answers.update(BOTH)
        service = started(bus)
        now = [100.0]
        clock = lambda: now[0]   # noqa: E731
        window = machine_clock.start(self)
        first = desktop_linux.reduced_motion(service, clock)
        window.assert_within(INSTANT_S, "reduced motion waited on the bus")
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertTrue(desktop_linux.reduced_motion(service, clock))
        reads = len(bus.methods("ReadOne"))
        now[0] += 5
        desktop_linux.reduced_motion(service, clock)
        self.assertTrue(service.wait_idle(CAP_S))
        self.assertEqual(len(bus.methods("ReadOne")), reads, "kept for MOTION_SECONDS")
        self.assertIsNone(first, "nothing known before the worker's first read")
        with mock.patch.object(desktop_linux, "reduced_motion", return_value=True):
            self.assertTrue(motion_preference.os_reduces_motion("linux"))
        with mock.patch.object(desktop_linux, "reduced_motion", return_value=None):
            self.assertFalse(motion_preference.os_reduces_motion("linux"), "can't tell: the app's own setting")


class JobNotificationTests(unittest.TestCase):
    def test_linux_posts_through_the_desktop_when_the_native_call_cannot(self):
        from angelo_agent import job_desktop
        native = mock.Mock(desktop_notify=mock.Mock(return_value="unsupported"),
                           desktop_attention=mock.Mock(return_value="unsupported"))
        with mock.patch.object(job_desktop, "announced", return_value=True), \
                mock.patch.object(job_desktop, "_preference", return_value=True), \
                mock.patch.object(job_desktop, "_desktop_allowed", return_value=True), \
                mock.patch.object(job_desktop, "notification", return_value=("id", "Render finished", "4 min")), \
                mock.patch.object(job_desktop, "_native", return_value=native), \
                mock.patch.object(job_desktop.sys, "platform", "linux"), \
                mock.patch.object(desktop_linux, "notify", return_value="posted") as posted:
            self.assertEqual(job_desktop._ended({}), "posted")
        posted.assert_called_once_with("Render finished", "4 min")
        native.desktop_attention.assert_not_called()


class ConstantsTests(unittest.TestCase):
    def test_what_ctypes_loads_is_immutable(self):
        self.assertIsInstance(desktop_linux_bus.LIBRARIES, tuple)
        self.assertTrue(all(isinstance(pair, tuple) for pair in desktop_linux_bus.LIBRARIES))


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("dbus-daemon"),
                     environment_skip("a Linux host with dbus-daemon (the VM CI image)"))
class RealBusTests(unittest.TestCase):
    """GIO through ctypes against a private bus daemon of the test's own (never the person's session)."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        # Its own configuration with no service folders: nothing installed on the host (a notification daemon, the
        # desktop portal) can be activated, so the bus holds exactly the daemon itself.
        config = Path(cls.folder.name, "bus.conf")
        config.write_text('<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" '
                          '"http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd"><busconfig>'
                          '<type>session</type><listen>unix:path=%s</listen><auth>EXTERNAL</auth>'
                          '<policy context="default"><allow send_destination="*" eavesdrop="true"/>'
                          '<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>'
                          % os.path.join(cls.folder.name, "bus"), encoding="utf-8")
        cls.daemon = subprocess.Popen(["dbus-daemon", "--config-file=%s" % config, "--nofork", "--print-address"],
                                      stdout=subprocess.PIPE, text=True)
        cls.address = cls.daemon.stdout.readline().strip()
        try:
            cls.bus = desktop_linux_bus.GioBus(address=cls.address)
        except Unavailable as error:
            cls.tearDownClass()
            raise unittest.SkipTest(environment_skip("GIO is not usable here: %s" % error))

    @classmethod
    def tearDownClass(cls):
        cls.daemon.terminate()
        cls.daemon.wait(CAP_S)
        cls.daemon.stdout.close()
        cls.folder.cleanup()

    def test_the_session_bus_is_found_as_the_app_finds_it(self):
        """Re-review NIT-3: GioBus() with no address, the production path (g_dbus_address_get_for_bus_sync)."""
        with mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": self.address}):
            bus = desktop_linux_bus.GioBus()
        self.assertEqual(bus.call(*desktop_linux.BUS, "NameHasOwner", ("s",), ("org.freedesktop.DBus",)), (True,))

    def test_marshalling_both_ways(self):
        dbus = desktop_linux.BUS
        self.assertTrue(self.bus.unique_name.startswith(":"))
        (identity,) = self.bus.call(*dbus, "GetId")
        self.assertRegex(identity, r"^[0-9a-f]{32}$")
        self.assertEqual(self.bus.call(*dbus, "NameHasOwner", ("s",), ("org.freedesktop.DBus",)), (True,))
        (names,) = self.bus.call(*dbus, "ListNames")
        self.assertIn("org.freedesktop.DBus", names)
        (reply,) = self.bus.call(*dbus, "RequestName", ("s", "u"), ("studio.angelo.Test", 4))
        self.assertEqual(reply, 1, "DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER")

    def test_a_remote_errors_name_and_missing_services(self):
        with self.assertRaises(Unavailable) as caught:
            self.bus.call(*desktop_linux.BUS, "NoSuchMethod")
        self.assertEqual(caught.exception.remote, desktop_linux.UNKNOWN_METHOD)
        self.assertFalse(caught.exception.timed_out)
        service = started_factory(lambda: desktop_linux_bus.GioBus(address=self.address))
        self.assertEqual(desktop_linux.notify("Render finished", "4 min", service_=service), "unsupported")
        with self.assertRaises(OSError):   # This bus has no desktop portal.
            desktop_linux.InhibitSuspend(service)

    def test_every_argument_type_marshals_and_a_bad_value_leaks_nothing(self):
        """The Notify argument list (as, a{sv} with a byte, v, i) reaches the bus intact: the daemon itself answers
        that nobody owns the name, which it can only do after parsing the whole message."""
        with self.assertRaises(Unavailable) as caught:
            self.bus.call(*desktop_linux.NOTIFICATIONS, "Notify", ("s", "u", "s", "s", "s", "as", "a{sv}", "i"),
                          ("Angelo", 0, "", "t", "b", ["x"], {"urgency": ("y", 1), "a": ("b", True)}, -1))
        self.assertIn("org.freedesktop.Notifications", str(caught.exception))
        with self.assertRaises(UnicodeEncodeError):   # Refused before any GVariant is made.
            self.bus.call(*desktop_linux.NOTIFICATIONS, "Notify", ("s", "s"), ("ok", "\udc80"))


if __name__ == "__main__":
    unittest.main()
