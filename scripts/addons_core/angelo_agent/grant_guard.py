# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Only the person grants (doc/angelo/grant_operators.rst): every operator and setting that grants, widens or consents
to anything takes effect only from the person's own click, never from a script, an add-on or a keymap entry.

Found by the Copy setup break-it review (2026-11-14, M1): any Python, an agent script that runs without review
included, could call ``bpy.ops.angelo.external_app_copy_setup(app="other")`` or the existing Connect and give an
outside app access with every default level. The re-review (r2) showed that any guard Python holds can be undone by
Python, so the person's presence is now C's to say (grant_authority, doc/angelo/grant_operators.rst "Authority in
C"). For every such operator (``grant``):

* **Never while an agent script runs** (``script_running``), and **never in background mode** (no person there)
  unless Angelo's own decision opened a C grant scope (``automatic``: the chat's Auto policy, a headless session, a
  worker's approval route).
* **Only with the person**: a C grant scope their press's ticket opened on one of Angelo's own surfaces
  (``ticketed``: the chat canvas, card hosts, the AI Providers page and its sheet), or the ticket C minted for their
  real press on Blender's own button for this operator (``UI_TICKET``). Keys, keymap items, a direct ``bpy.ops`` call
  and anything Python holds give neither.
* An operator that only narrows (Disconnect, Ask before changes) is not a grant; one that does both says when it
  widens (``widens``), and only that is guarded.

Settings that widen (``ANGELO_Preferences.allow_local_cli``, ``helper_standing_consent``, the agents' executables)
change only in a C grant scope or from the Preferences editor (``setting``). The confinement
(doc/angelo/script_confinement.rst) keeps an agent script from Angelo's files and operators. This module needs no
``bpy`` at import, so it is host-testable."""

import threading
import time

from . import grant_hints   # A press made before its control armed: a hint, never an error.
from .ui_text import n_, rpt_

UI_TICKET = "ui"   # The digest of a ticket C mints for a real press on Blender's own button for an operator.
SCRIPT_TOOLS = frozenset({"angelo_execute_python", "angelo_run_script_file"})
REGISTRY = {}          # bl_idname -> whether Angelo's own decisions use it too (``auto``), of every guarded one.
# Operators that only narrow, yet change the person's accounts, sign-ins or consents: never agent code's, in either
# direction (the G2 ruling; Devin's Phase B review, 2026-11-15). C refuses them while a script runs, as it does the
# grant operators (script_confinement_blender: its denied-operator list), and the functions behind them are entries.
# The person and Angelo's own code use them without a scope: they grant nothing.
ACCOUNT_OPERATORS = frozenset({
    "angelo.team_sign_out", "angelo.team_cancel_sign_in", "angelo.cancel_signin", "angelo.quick_connect_cancel",
    "angelo.connection_remove", "angelo.external_app_disconnect", "angelo.external_app_forget_scripts"})
_lock = threading.Lock()

NOT_YOURS = n_("Only your own click in Angelo can do this, not an agent script or a keymap entry.")
SCRIPT_RUNS = n_("Not while an agent script is running: only your own click in Angelo can do this.")
BACKGROUND = n_("Not in background mode: only your own click in Angelo's window can do this.")


class Refused(PermissionError):
    """An entry point's refusal (``entry``): told apart from a policy's or a file's ``PermissionError``, so it is
    reported as what it is (Copy setup break-it r2, N1). Its message is ``SCRIPT_RUNS``."""

    def __init__(self):
        super().__init__(SCRIPT_RUNS)


_executing = [0]   # Agent scripts executing in this process now (``agent_script``).
_here = threading.local()   # ... and on this thread (``inside_agent_code``).


class agent_script:
    """``with grant_guard.agent_script(): exec(code, namespace)``: the one place agent code runs
    (``BlenderExecutor``, for every tool that runs a script, in the window, a worker or a headless session) marks it,
    so ``script_running`` is true for exactly as long as agent code executes. The confinement (doc/angelo/
    grant_operators.rst) reads the same signal."""

    def __enter__(self):
        _executing[0] += 1
        _here.depth = getattr(_here, "depth", 0) + 1
        return self

    def __exit__(self, *_exc):
        _executing[0] -= 1
        _here.depth -= 1
        return False


def script_running():
    """THE signal that an agent script is running: one executing now in this process (``agent_script``), or any
    chat's call of a script-running tool (``SCRIPT_TOOLS``) marked executing that runs in this process. A worker
    chat's script runs in its worker Blender (a chat bound to its worker, ``remote_executor``, or a hosted chat's
    mirror, ``hosted_in_worker``), where that process's own guard holds it: it does not hold Angelo's grants here.
    Every guard and the confinement read this; ``trusted_scripts_ui.agent_script_running`` is it. The confinement's
    stack check comes first: a script cannot clear it, while it could zero ``_executing``
    (doc/angelo/script_confinement.rst)."""
    from . import script_confinement
    if script_confinement.confined() or _executing[0] > 0:
        return True
    try:
        from . import ui
        controllers = list(ui._controllers.values())
    except Exception:   # noqa: BLE001 - before the chat module exists nothing runs.
        return False
    return any(getattr(call, "executing", False) and call.name in SCRIPT_TOOLS
               for controller in controllers if not _runs_in_a_worker(controller)
               for call in list(controller.pending.values()))


def _runs_in_a_worker(controller):
    return getattr(controller, "remote_executor", None) is not None or \
        getattr(controller, "hosted_in_worker", False) is True


def _background():
    try:
        import bpy
        return bool(bpy.app.background)
    except Exception:   # noqa: BLE001 - outside Blender (host tests): never background.
        return False


def admission(idname):
    """``(reason, how)`` for a call of guarded operator ``idname`` now: ``reason`` None when it may grant, else why
    not (English; reported translated by the operator). The person is present only as C says: a grant scope that
    admits ``idname`` (their ticketed click; Angelo's own decision only for its listed operators, ``auto``), or the
    ticket their real press on Blender's own button for it minted (``how`` is ``UI_TICKET``: take it to run)."""
    if script_running():
        return SCRIPT_RUNS, None
    authority = _authority()
    if not authority.available():
        return NOT_YOURS, None
    if authority.GUARD.admitted(idname):
        return None, "scope"
    if authority.GUARD.peek(idname, UI_TICKET):
        return None, UI_TICKET
    if _background():
        return BACKGROUND, None
    return NOT_YOURS, None


def refusal(idname):
    return admission(idname)[0]


def grant(auto=False):
    """Guard an operator that grants (doc/angelo/grant_operators.rst says what each one grants): its ``invoke`` opens
    a dialog only for the person, and its ``execute`` runs only for them (``refusal``), in a C grant scope. An
    operator may define ``widens(self, context)``: False when this call only narrows (not guarded)."""
    def wrap(cls):
        execute = getattr(cls, "execute", None) if isinstance(cls, type) else None
        if execute is None:   # A host test's stand-in for an operator class (a Mock): nothing to guard there.
            return cls
        cls.__annotations__ = dict(cls.__dict__.get("__annotations__", {}))
        invoke = cls.__dict__.get("invoke")
        if invoke is not None and not callable(getattr(cls, "consent_target", None)):   # What its held OK binds.
            raise TypeError("%s has an invoke: it declares consent_target (None when it reads nothing more)" % cls)
        from . import grant_gestures   # Here: it imports this module.
        browse = getattr(cls, "grant_browse", False) is True   # Its invoke opens a file browser: a pick takes longer.

        def guarded_invoke(self, context, event):
            if invoke is None:
                return self.execute(context)
            if hasattr(self, "widens") and not self.widens(context):
                return invoke(self, context, event)   # Narrowing: open to anything.
            reason, how = admission(cls.bl_idname)
            # The instance, and what its dialog acts on as it opens (grant_gestures.dialog_target): its OK is for that.
            dialog = "dialog:%d:%s" % (getattr(self, "as_pointer", lambda: id(self))(),
                                       grant_gestures.target_key(self, context))
            guard = _authority().GUARD
            if reason is None:   # Its dialog keeps the person's consent in C for its confirm (this instance only).
                held = guard.carry(cls.bl_idname, UI_TICKET, dialog, browse) if how == UI_TICKET else \
                    guard.hold_dialog(cls.bl_idname, dialog, browse)
                reason = None if held or how == "scope" else NOT_YOURS
            if reason is not None:
                if reason != NOT_YOURS or not grant_hints.refused(cls.bl_idname):
                    self.report({'ERROR'}, rpt_(reason))
                return {'CANCELLED'}
            self._dialog = dialog
            return invoke(self, context, event)

        def guarded_execute(self, context):
            body = getattr(type(self), "_granted_execute", execute)   # A grouped sibling carries the wrapped one.
            if hasattr(self, "widens") and not self.widens(context):
                return body(self, context)   # Narrowing: open to anything, an agent script too, in no grant scope.
            dialog, self._dialog = getattr(self, "_dialog", None), None   # Single use.
            reason, how = admission(cls.bl_idname)
            if dialog is not None and not dialog.endswith(":" + grant_gestures.target_key(self, context)):
                self.report({'ERROR'}, rpt_(grant_gestures.TARGET_CHANGED))   # Changed while it was open.
                return {'CANCELLED'}
            if reason is None and how == "scope":
                return body(self, context)   # In the scope that admits it.
            try:   # The person's press on its button, or on its dialog's opening: run in their C scope.
                if dialog is not None and not script_running():
                    return _authority().GUARD.grant(cls.bl_idname, dialog, body, self, context)
                if reason is None:
                    return _authority().GUARD.grant(cls.bl_idname, UI_TICKET, body, self, context)
            except PermissionError:
                reason = reason or NOT_YOURS
            if (reason or NOT_YOURS) != NOT_YOURS or not grant_hints.refused(cls.bl_idname):
                self.report({'ERROR'}, rpt_(reason) if reason else rpt_(NOT_YOURS))
            return {'CANCELLED'}

        # The operator's own body (and its invoke) reach nothing agent code can call: sealed in C, callable only from
        # these wrappers' own code, which judge first (the Fusion break-it review, 2026-11-15, G3, as a class).
        seal = getattr(_authority().GUARD, "seal", None)   # None: no C module (a host test's stand-in).
        if seal is not None:
            execute = seal(execute, guarded_execute.__code__)
            if invoke is not None:
                invoke = seal(invoke, guarded_invoke.__code__)
        cls._granted_execute = execute   # The operator's own body, run only once the person is there.
        cls.execute, cls.invoke = guarded_execute, guarded_invoke
        REGISTRY[cls.bl_idname] = auto
        register_in_c(cls.bl_idname, auto)
        return cls
    return wrap


def register_in_c(idname, auto=False):
    """C's list of grant operators (add-only): an agent script can't run one (the confinement's operator gate) and
    its own drawing makes no button for one (``BPY_angelo_guard_draw_refuses``). One that Angelo's own decision
    runs (``auto``: the candidate's Approve and its step, exactly) is listed for automatic scopes too."""
    guard = _authority().GUARD
    if guard is None:
        return
    try:
        if getattr(guard, "add_denied_operators", None) is not None:
            guard.add_denied_operators((idname,))
        if auto:
            guard.allow_automatic((idname,))
    except PermissionError as error:   # Only while an agent script runs, which never imports Angelo's modules.
        print("Angelo: %s was not listed as a grant operator:" % idname, error)


def _authority():
    from . import grant_authority
    return grant_authority


ENTRIES = {}   # "module.qualname" of every function behind a grant, wrapped by ``entry`` (the audit test reads it).


def inside_agent_code():
    """Whether agent code is executing right now (``agent_script``): what an entry point refuses. Not a chat's
    script call being marked executing, which the executor's own run of that script is. The confinement's stack
    check counts too (a script cannot clear it; doc/angelo/script_confinement.rst)."""
    from . import script_confinement
    return script_confinement.confined() or getattr(_here, "depth", 0) > 0


PLAIN = (bool, int, float, str, bytes, type(None))   # Values that answer the same each time they are read.


def _plain(value):
    return type(value) in PLAIN or (type(value) is tuple and all(type(item) in PLAIN for item in value))


def entry(function, widens=None):
    """``function`` as the entry point behind a grant: refused while agent code runs (``inside_agent_code``), so a
    script that skips the guarded operator and calls what it calls grants nothing (coordinator, share-link break-it
    class). Wrap at the end of the module that defines it (``name = grant_guard.entry(name)``): every caller, in the
    module and importing it, gets the guard.

    ``widens`` limits the refusal to calls that widen: the names of the parameters whose truth widens (any of them
    true), or ``widens(arguments)`` over the call's bound arguments (defaults applied; ``self`` is the instance).
    Inside agent code every argument it reads must be a plain built-in value (``PLAIN``), read once, and the body
    gets that same value: a value that answers differently when read again is refused (Copy setup break-it r2,
    S1). A ``widens`` that raises refuses too."""
    import functools
    import inspect
    signature = inspect.signature(function)
    names = (widens,) if isinstance(widens, str) else widens
    if names is not None and not callable(names):
        missing = set(names) - set(signature.parameters)
        if missing:
            raise TypeError("%s has no parameter %s" % (function.__qualname__, ", ".join(sorted(missing))))

    @functools.wraps(function)
    def guarded(*args, **kwargs):
        if not inside_agent_code():
            return function(*args, **kwargs)
        if names is None:
            raise Refused()
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if callable(names):
                plain = {key: value for key, value in bound.arguments.items() if key != "self"}
                kinds = {key: signature.parameters[key].kind for key in plain}
                for key, value in plain.items():
                    items = value.values() if kinds[key] is inspect.Parameter.VAR_KEYWORD else (
                        value if kinds[key] is inspect.Parameter.VAR_POSITIONAL else (value,))
                    if not all(_plain(item) for item in items):
                        raise TypeError(key)
                wider = names(bound.arguments)
            else:
                if not all(_plain(bound.arguments[name]) for name in names):
                    raise TypeError(names)
                wider = any(bool(bound.arguments[name]) for name in names)
        except Exception:   # noqa: BLE001 - an argument that is no plain value, or a rule that fails: refused.
            raise Refused() from None
        if wider is not False:
            raise Refused()
        return function(*bound.args, **bound.kwargs)
    # Nothing leads from the entry to what it guards (the Fusion break-it review, 2026-11-15, G3): functools.wraps'
    # __wrapped__ is dropped (the signature stays readable as a copy, which holds no function), and the closure's
    # ``function`` becomes the original sealed in C: agent code can read a closure, but can call what it holds only
    # through this wrapper's own code, which judges the call first.
    del guarded.__wrapped__
    guarded.__signature__ = signature
    ENTRIES["%s.%s" % (function.__module__, function.__qualname__)] = guarded
    seal = getattr(_authority().GUARD, "seal", None)   # None: no C module (a host test's stand-in).
    if seal is not None:
        function = seal(function, guarded.__code__)   # noqa: F841 - the closure's cell, read by ``guarded``.
    return guarded


def entry_methods(cls, *names, widens=None):
    """``entry`` for methods of ``cls``, in place (every instance gets the guard)."""
    for name in names:
        setattr(cls, name, entry(getattr(cls, name), widens))


def guard(*classes, auto=False):
    """Guard ``classes`` (``grant``), at the end of the module that defines them: a statement rather than a decorator,
    so a class read from its source alone (host tests run an operator's own body that way) is just its body."""
    for cls in classes:
        grant(auto=auto)(cls)


class _Operators:
    """``bpy.ops.angelo`` as Angelo's own automatic decision calls it: each guarded operator in a C grant scope
    (grant_authority.automatic: refused while an agent script runs); any other operator as it is."""

    def __init__(self, operators=None):
        self._operators = operators

    def __getattr__(self, name):
        if self._operators is None:
            import bpy
            self._operators = bpy.ops.angelo
        operator = getattr(self._operators, name)
        if "angelo." + name not in REGISTRY:
            return operator
        return lambda *args, **properties: _authority().automatic(operator, *args, **properties)


def clicked(operators=None):
    """The operators as one of Angelo's click paths calls them: as they are. The person's ticket (``ticketed``) has
    already opened the grant scope they run in; outside one, what grants refuses."""
    if operators is None:
        import bpy
        operators = bpy.ops.angelo
    return operators


def automatic(operators=None):
    """The operators of Angelo's own automatic decision (the chat's Auto policy, a headless or worker session)."""
    return _Operators(operators)


class _Harness(_Operators):
    def __getattr__(self, name):
        operator = super().__getattr__(name)
        if "angelo." + name not in REGISTRY:
            return operator
        target = getattr(self._operators, name)
        return lambda *args, **properties: _authority().GUARD.harness(target, *args, **properties)


def stand_in():
    """The operators as a test or evaluation harness runs them, standing in for the person who would click: C's
    harness scope, which exists only in an app started with --enable-event-simulate (never a shipped one)."""
    return _Harness(None)


def setting_allowed(value, current, name):
    """Whether widening setting ``name`` may change from ``current`` to ``value`` now: narrowing always; widening only
    in a C grant scope (the person's ticketed click on one of Angelo's surfaces) or with the ticket C minted for their
    real press on its own checkbox or field (``person_set``), never while an agent script runs or in background."""
    if not _widens(value, current):
        return True
    if script_running() or _background():
        return False
    authority = _authority()
    return (authority.available() and authority.GUARD.admitted("prop." + name)) or person_set(name)


def person_set(name):
    """Whether the person's real press on Blender's own button for property ``name`` is changing it now (its C
    ticket, taken once)."""
    authority = _authority()
    return authority.available() and authority.GUARD.take("prop." + name, UI_TICKET)


def _widens(value, current):
    if isinstance(value, bool) or isinstance(current, bool):
        return bool(value) and not bool(current)
    return value != current   # A path to a program Angelo runs: any change widens.


_accepted = {}   # Preference name -> the value it last took with the person (``setting``, ``remember``).
WIDENING = ("allow_local_cli", "helper_standing_consent", "reuse_codex_login", "codex_path", "claude_path")


def remember(prefs):
    """The widening settings as saved: what a refused change goes back to (``ui.preferences`` calls it once)."""
    if prefs is not None and not _accepted:
        for name in WIDENING:
            _accepted[name] = getattr(prefs, name, None)


def setting(name):
    """``update`` for an ``ANGELO_Preferences`` property that widens (add-on preferences hold no ID properties, so
    the property stays a stored one): a change ``setting_allowed`` refuses is put back to the value it last took
    with the person, and narrowing always stands."""
    def update(prefs, _context):
        value = getattr(prefs, name)
        current = _accepted.get(name, value)
        if value == current or setting_allowed(value, current, name):
            _accepted[name] = value
            return
        setattr(prefs, name, current)   # Updates again with the value it had: taken as unchanged.
        grant_hints.refused("prop." + name) or print("Angelo: %s was not changed: only your own click in Angelo "
                                                     "can change it." % name)
    return update


# Angelo's own surfaces (doc/angelo/grant_operators.rst, "Authority in C"): every control a surface draws is a consent
# region in C, "<prefix><action>" with the digest of its value; a click on one runs in the grant scope its press's
# ticket opens, and without one (the keyboard, a press that minted nothing) it runs outside any, so whatever it reaches
# that grants refuses (``refusal``). No list of granting actions to keep: the guarded operators and entries are the
# inventory. An approval's own controls (``SHIELDED``) are armed only after the shield and a move into them.
SHIELDED = frozenset({"approve", "review", "script_always_allow", "script_file_allow", "confirm", "accept"})
MAX_REGIONS = 256


def value_digest(value):
    """The digest a control's value is ticketed by (the same at draw and at dispatch)."""
    import hashlib
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def canvas_regions(window, region, surface, hits, scale, rtl, prefix="canvas.", shielded=SHIELDED, focus=None,
                   popover=None, focus_moves=False):
    """Tell C where ``surface`` draws its controls now: ``hits`` are ``(rect, action, value)`` laid out unscaled in
    ``region`` (mirrored in a right-to-left language), and which has keyboard focus (``focus``: ``(action, value)``
    or None). C takes the focus as the person's only right after their own Tab or press on it (the maintainer's
    keyboard ruling); a key on it then mints its ticket. ``popover`` False: no popover is open on the surface, so
    one the person opened (``opened``) has closed. ``focus_moves``: the focus is a control a movement key acts on
    (a slider), the only kind an arrow mints for. Nothing while an agent script runs."""
    grant_authority = _authority()
    if not grant_authority.available() or script_running():
        return
    regions = []
    for (x, y, width, height), action, value in hits[:MAX_REGIONS]:
        left = region.width - (x + width) * scale if rtl else x * scale
        regions.append((int(region.x + left), int(region.y + y * scale), int(width * scale), int(height * scale),
                        prefix + action, value_digest(value), action in shielded))
    try:
        if popover is False and getattr(grant_authority.GUARD, "close_surface", None) is not None:
            grant_authority.GUARD.close_surface(window.as_pointer(), surface)
        grant_authority.GUARD.consent_regions(window.as_pointer(), surface, tuple(regions))
        action, value = focus if focus else ("", None)
        grant_authority.GUARD.focus(window.as_pointer(), surface, prefix + action if action else "",
                                    value_digest(value) if action else "", bool(focus_moves and action))
    except (PermissionError, ValueError, TypeError) as error:
        print("Angelo: a surface's controls were not registered:", error)


def said_refusal(error):
    """Whether ``error`` is a grant's refusal of input no person made (an operator's report of ``NOT_YOURS`` or
    ``SCRIPT_RUNS``, as shown or translated, or the guard's own ``PermissionError``): printed as one line, so a
    surface's input handler reports it as what it is, never as a fault, and keeps going."""
    from .ui_text import rpt_
    text = str(error)
    if not isinstance(error, (RuntimeError, PermissionError)) or not any(
            reason in text for reason in (NOT_YOURS, SCRIPT_RUNS, rpt_(NOT_YOURS), rpt_(SCRIPT_RUNS))):
        return False
    print("Angelo: input no person made was not taken as a grant:", text.strip().splitlines()[-1][:200])
    return True


def opened(window, surface):
    """A popover ``surface`` shows, opened by the person: C takes it as theirs only in the scope of their own ticketed
    press or key (a popover code opened is never theirs); until it closes its focus is theirs and its arrows, Home
    and End act (the coordinator's keyboard ruling, 2026-11-16)."""
    grant_authority = _authority()
    if grant_authority.available() and getattr(grant_authority.GUARD, "open_surface", None) is not None:
        grant_authority.GUARD.open_surface(window.as_pointer(), surface)


def by_click(action, value, function, *args, **kwargs):
    """``function(*args, **kwargs)`` for a click on the chat canvas's ``action`` (``ticketed``)."""
    return ticketed("canvas.", action, value, function, *args, **kwargs)


def ticketed(prefix, action, value, function, *args, **kwargs):
    """``function(*args, **kwargs)`` in the grant scope of the person's own press on ``prefix + action`` (its C
    ticket), or outside any when there is none: then whatever it reaches that grants refuses."""
    grant_authority, idname, digest = _authority(), prefix + action, value_digest(value)
    if grant_authority.available() and grant_authority.GUARD.peek(idname, digest):
        return grant_authority.GUARD.grant(idname, digest, function, *args, **kwargs)
    return grant_hints.answer(idname, digest, function, *args, **kwargs)   # No ticket: before it armed, a hint.


def harness(function, *args, **kwargs):
    """A test or evaluation harness standing in for the person (as ``stand_in``): ``function`` in C's harness scope,
    only in an app started with --enable-event-simulate."""
    return _authority().GUARD.harness(function, *args, **kwargs)
