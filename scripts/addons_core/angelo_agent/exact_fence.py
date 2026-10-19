# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One synchronous span of exact reads: what it computes once, it never computes again.

An edit used to run nine full exact checks of each written object, and each
check walked the object's whole dependency closure several times (the
fingerprint, the undeclared-dependency search, the evaluation preflight, stage
validation), reading every material and node tree again each time.

A *fence* is a span of main-thread code that only reads Blender data: no stage
evaluation (``view_layer.update`` runs drivers and depsgraph handlers), kernel,
operator, hydration or suspension, ID creation, deletion or write, and no
event processing, runs inside it. Nothing in the document can change between
two reads in one fence, so inside it a datablock's dependency links, an
object's mutable closure, its fingerprint, its custom-link admission and its
object revision are computed once and reused: a repeated check compares the
same exact content it would have read again. The memo belongs to the fence and
dies with it; it is never a cache across time, and nothing is ever reused
because a pointer, a count or a depsgraph flag looks unchanged
(``doc/angelo/agent_execution.rst``, "Exact fences").

Fences are opened only around audited read-only spans (bind, verify, stage
validation reads, and the two commit fences: the fresh precondition with the
verify after it, and the preparation fence with the commit's own checks).
Every callback-capable entry point calls ``end`` first, so a memo cannot
outlive a step that could change data even if a span were drawn too wide.
Blender's Python API is single-threaded: a fence trusts that no other thread
writes Blender data while the main thread holds the interpreter, as every
exact check does.
"""

import contextlib
import functools
import threading

_current = None
_MAIN = threading.main_thread().ident


class Fence:
    """The memo of one fence: links per datablock, closures, and full per-object checks."""

    __slots__ = ("links", "closures", "checks", "tables")

    def __init__(self):
        self.links = {}  # ID pointer -> [(holder, property, ID)] (program_dependencies._embedded_links).
        self.closures = {}  # Object pointer -> its mutable dependency closure.
        self.checks = {}  # (check name, *identity) -> the check's complete result.
        self.tables = {}  # (table name, StructRNA pointer) -> the declarations a walker reads.


def _main_thread():
    return threading.get_ident() == _MAIN


@contextlib.contextmanager
def fence():
    """Share exact reads until this block ends or a callback-capable step calls ``end``.

    A nested fence joins the open one. Off the main thread nothing is shared.
    """
    global _current
    if _current is not None or not _main_thread():
        yield
        return
    _current = Fence()
    try:
        yield
    finally:
        _current = None


def reads_only(function):
    """Run a function that only reads Blender data in one exact fence, or the caller's."""
    @functools.wraps(function)
    def fenced(*args, **kwargs):
        with fence():
            return function(*args, **kwargs)
    return fenced


def end():
    """A callback-capable step begins: nothing read before it may be reused after it."""
    global _current
    _current = None


def current():
    """The open fence's memo, or None (off the main thread there is never one)."""
    return _current if _current is not None and _main_thread() else None


def table(name, rna, build):
    """``build(rna)`` once per RNA struct type per fence: the property declarations a walker reads.

    The exact walks read every value of every struct, but most of their time
    went to re-reading each type's property declarations (identifier, type,
    read-only flag) at every struct they visited. A type's declarations change
    only when Python registers or removes a property, which no code inside a
    fence does. Values are always read fresh.
    """
    memo = current()
    if memo is None:
        return build(rna)
    try:
        key = (name, rna.as_pointer())
    except AttributeError:  # Host-test stand-ins have no pointer; they are stable objects.
        key = (name, id(rna))
    found = memo.tables.get(key)
    if found is None:
        found = memo.tables[key] = build(rna)
    return found


def check(name, key, compute, *, replay=None):
    """``compute()`` once per open fence for (name, key); the stored result after that.

    ``replay(result)`` runs on every reuse, for checks that charge a caller's
    budget (the fingerprint): it charges exactly what computing it again
    would, or returns False when that would exhaust the budget, and the check
    is then computed again so that it fails exactly as it always did. Without
    a fence ``compute()`` simply runs. Failures are never stored: a refused
    check raises again when repeated.
    """
    memo = current()
    if memo is None:
        return compute()
    entry = (name,) + tuple(key)
    if entry in memo.checks:
        result = memo.checks[entry]
        if replay is None or replay(result):
            return result
        return compute()
    result = compute()
    if current() is memo:  # The computation itself did not end the fence.
        memo.checks[entry] = result
    return result


class HandOff:
    """A fenced step one caller hands to another: ProgramJob.commit's preparation fence, which a fencing
    backend's commit runs in its final checks. It records that it ran, so a skipped one is caught."""

    __slots__ = ("function", "ran")

    def __init__(self, function):
        self.function, self.ran = function, False

    def __call__(self):
        result = self.function()
        self.ran = True
        return result


def require_ran(handed, error):
    """Before a first live write: a received HandOff ran, or ``error`` (INTERNAL_ERROR) is raised."""
    if not getattr(handed, "ran", True):
        raise error("Commit reached its first live write without the controller's preparation fence",
                    code="INTERNAL_ERROR")


def require_declared(cls, method, flag, base):
    """At class creation: the class ``cls.<method>`` resolves to, or one before it in the MRO, declares ``flag``.

    ``base``'s own method needs none. Resolving across the whole MRO also
    catches a mixin listed before ``base`` whose method replaces it.
    """
    mro = cls.__mro__
    owner = next(index for index, klass in enumerate(mro) if method in vars(klass))
    if mro[owner] is not base and not any(flag in vars(klass) for klass in mro[:owner + 1]):
        raise TypeError("%s resolves %s to %s: declare %s there (and run pending_before_apply)"
                        % (cls.__name__, method, mro[owner].__name__, flag))
