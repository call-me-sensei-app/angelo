# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Python twin of ``_bpy._angelo_guard``'s authority, for host tests only (the app always uses the C module).

``suite_isolation`` installs it as ``sys.modules["angelo_guard_twin"]`` in every host test, where ``grant_authority``
finds it because no ``_bpy`` exists. It follows the C core (bpy_angelo_guard_authority_core.cc) and its GTests: the
ledger widens only in a grant scope, values are exactly None/bool/int/float/str, a ticket is single use, and only an
exact approved text admits a script. What C decides about input (regions, the shield, presses) is the GUI suite's;
here ``press(idname, digest)`` mints a ticket as an aimed press would, and ``confine()`` stands for an agent script.

Two modes. **Strict** (``strict()``) is C exactly: no grant scope unless a test opens one, and a chat is in Auto only
as the ledger holds it. The tests of the guard's own rules use it (test_native_grant_operators, the always-allow
checkbox test); the in-app suites test the real C. **By default** a host test of other logic (a controller's
staging, a card's wording) runs as if the person were there and C agreed with the chat's Python state: a grant scope
is open on every thread and a chat's Auto is its Python state, so those tests test their own logic, not the guard.
Confinement (``confine()``) refuses in both modes.
"""

import contextlib
import threading
import time

TICKET_SECONDS = 5.0
DIALOG_SECONDS = 180.0
BROWSE_SECONDS = 300.0   # C's: a file browser's pick.
_STANDARD = {"approved_scripts": None, "trusted_scripts": False, "always_allow": False, "chat_clients": None,
             "script_files": False, "approved_calls": None, "approval_modes": "ASK"}
_lock = threading.RLock()
_here = threading.local()
_ledger = {}
_tickets = []
_unarmed = []   # C's record of the person's press on a control not yet armed (``unarmed``).
_regions = {}
_confined = [0]
_automatic = []   # Operators Angelo's own decision may run (allow_automatic).
_strict = [True]   # C exactly by default: a host test opens the scope its code would run in (strict).


def strict(on=True):
    """C exactly (see the module's docstring); ``strict(False)`` goes back to the default."""
    _strict[0] = bool(on)
PERSON, AUTOMATIC, HARNESS = "person", "automatic", "harness"


def reset():
    with _lock:
        _ledger.clear()
        _tickets.clear()
        _unarmed.clear()
        _regions.clear()   # Not _automatic: Angelo lists its operators once, as it loads (as in C).
        for name, narrow in _STANDARD.items():
            _ledger[name] = (narrow, {})


def _plain(value):
    if value is None or type(value) in (bool, float, str):
        return True
    if type(value) is int:
        if not -2 ** 63 <= value < 2 ** 63:
            raise ValueError("an int in the ledger fits 64 bits")
        return True
    raise TypeError("a ledger value is exactly None, bool, int, float or str")


def confined():
    return _confined[0] > 0


@contextlib.contextmanager
def confine():
    """Stands for an agent script running (the C confinement's predicate)."""
    _confined[0] += 1
    try:
        yield
    finally:
        _confined[0] -= 1


def _depth(kind):
    return getattr(_here, kind, 0)


def _scope_active():
    return not _strict[0] or any(_depth(kind) > 0 for kind in (PERSON, AUTOMATIC, HARNESS))


def granting():
    return _scope_active() and not confined()


def admitted(idname):
    if confined():
        return False
    if not _strict[0]:
        return True
    return _depth(PERSON) > 0 or _depth(HARNESS) > 0 or (_depth(AUTOMATIC) > 0 and idname in _automatic)


def _call(function, args, kwargs, kind=AUTOMATIC):
    setattr(_here, kind, _depth(kind) + 1)
    try:
        return function(*args, **kwargs)
    finally:
        setattr(_here, kind, _depth(kind) - 1)


def allow_automatic(idnames):
    if confined():
        raise PermissionError("Not while an agent script is running.")
    _automatic.extend(i for i in idnames if i not in _automatic)


def harness(function, *args, **kwargs):
    """Host tests stand in for the person (the app's needs --enable-event-simulate)."""
    if confined():
        raise PermissionError("Not while an agent script is running.")
    return _call(function, args, kwargs, HARNESS)


def peek(idname, digest):
    with _lock:
        now = time.monotonic()
        return not confined() and any(t[0] == idname and t[1] == digest and now - t[2] <= t[3] for t in _tickets)


def carry(idname, source, target, browse=False):
    if confined() or not take(idname, source):
        return False
    with _lock:
        _tickets.append((idname, target, time.monotonic(), BROWSE_SECONDS if browse else DIALOG_SECONDS, "dialog"))
    return True


def hold_dialog(idname, target, browse=False):
    if confined() or (_strict[0] and not (_depth(PERSON) > 0 or _depth(HARNESS) > 0)):
        return False
    with _lock:
        _tickets.append((idname, target, time.monotonic(), BROWSE_SECONDS if browse else DIALOG_SECONDS, "dialog"))
    return True


def hold_gesture(idname, target):
    """C's ``hold_gesture``: a gesture's next step, in the person's (or a harness's) scope only."""
    if confined() or (_strict[0] and not (_depth(PERSON) > 0 or _depth(HARNESS) > 0)):
        return False
    with _lock:
        _tickets.append((idname, target, time.monotonic(), TICKET_SECONDS, "gesture"))
    return True


def agent_changed():
    """C's ``agent_changed`` (its ``note_agent_run``): every dialog's held consent and gesture's hold ends."""
    with _lock:
        _tickets[:] = [t for t in _tickets if t[4] not in ("dialog", "gesture")]


def outside_work():
    """C's ``outside_work`` (``note_outside_work``): a dialog's held consent and a gesture's hold end, nothing else."""
    with _lock:
        _tickets[:] = [t for t in _tickets if t[4] not in ("dialog", "gesture")]


def focus(window, surface, idname, digest, moves=False):
    """The GUI suite proves the person's focus rule in C; here a report is taken as it comes."""
    return bool(idname)


def press(idname, digest):
    """A test's aimed press on a region for ``idname``/``digest``: one ticket."""
    with _lock:
        _tickets.append((idname, digest, time.monotonic(), TICKET_SECONDS, "press"))


def consent_regions(window, surface, regions):
    if confined():
        raise PermissionError("Not while an agent script is running.")
    if type(regions) is not tuple or any(type(r) is not tuple or len(r) != 7 for r in regions):
        raise TypeError("a region is (x, y, width, height, idname, digest, shield)")
    with _lock:
        _regions[(window, surface)] = regions
    return True


def unarmed_press(idname, digest, spot):
    """A test's press on ``idname``/``digest`` before it armed, at ``spot`` (window, x, y, width, height): C's
    record of it."""
    with _lock:
        _unarmed[:] = [(idname, digest, spot, time.monotonic())]


def unarmed(idname, digest):
    """C's ``unarmed``: that press, taken once while fresh (a Blender button's has no digest)."""
    if confined():
        return None
    with _lock:
        if _unarmed and _unarmed[0][0] == idname and _unarmed[0][1] in ("", digest) \
                and time.monotonic() - _unarmed[0][3] <= 1.0:
            return _unarmed.pop()[2]
    return None


def take(idname, digest):
    if confined():
        return False
    with _lock:
        now = time.monotonic()
        _tickets[:] = [t for t in _tickets if now - t[2] <= t[3]]
        for ticket in _tickets:
            if ticket[0] == idname and ticket[1] == digest:
                _tickets.remove(ticket)
                return True
    return False


def grant(idname, digest, function, *args, **kwargs):
    if confined():
        raise PermissionError("Not while an agent script is running.")
    if not take(idname, digest):
        raise PermissionError("Only your own click in Angelo can do this.")
    return _call(function, args, kwargs, PERSON)


def automatic(function, *args, **kwargs):
    if confined():
        raise PermissionError("Not while an agent script is running.")
    return _call(function, args, kwargs)


def define(name, narrow):
    if confined():
        raise PermissionError("Not while an agent script is running.")
    _plain(narrow)
    with _lock:
        if name in _ledger:
            return False
        _ledger[name] = (narrow, {})
        return True


def get(name, key, default=None):
    if name == "approval_modes" and not _strict[0]:
        return "AUTO_BOUNDED"   # By default a chat's Auto is its Python state (the module's docstring).
    with _lock:
        space = _ledger.get(name)
        return default if space is None or key not in space[1] else space[1][key]


def items(name):
    with _lock:
        space = _ledger.get(name)
        return tuple(sorted(space[1].items())) if space else ()


def set(name, key, value):  # noqa: A001 - the C module's name.
    _plain(value)
    if confined():   # Either direction, as C (the Fusion break-it review, 2026-11-15, G2).
        raise PermissionError("Not while an agent script is running.")
    with _lock:
        if name not in _ledger:
            raise KeyError(name)
        narrow, values = _ledger[name]
        if key in values and type(values[key]) is type(value) and values[key] == value:
            return False
        if not (type(value) is type(narrow) and value == narrow) and not granting():
            raise PermissionError("Not while an agent script is running." if confined()
                                  else "Only your own click in Angelo can do this.")
        values[key] = value
        return True


def remove(name, key):
    if confined():   # Narrowing, yet the person's consents are theirs to withdraw (G2).
        raise PermissionError("Not while an agent script is running.")
    with _lock:
        space = _ledger.get(name)
        return space is not None and space[1].pop(key, None) is not None


def admits(call_id, digest):
    """What ``run_confined`` asks in C: that exact text approved for that call, once."""
    with _lock:
        values = _ledger["approved_scripts"][1]
        if not call_id or not digest or values.get(call_id) != digest:
            return False
        del values[call_id]
        return True


reset()


def stand_in_for_the_person(module_globals):
    """For a host test module whose tests act as the person (their own click set the consent they then read): its
    ``setUpModule``/``tearDownModule`` hold a harness scope on the main thread for the whole module. A test that
    proves a refusal confines itself (``confine``) or runs on another thread."""
    def set_up():
        _here.harness = _depth(HARNESS) + 1

    def tear_down():
        _here.harness = _depth(HARNESS) - 1
    module_globals["setUpModule"], module_globals["tearDownModule"] = set_up, tear_down
