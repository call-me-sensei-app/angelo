# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An in-app suite standing in for the person on a grant entry (``controller.approve_tool`` and the other entries
``grant_guard.entry`` guards): C's harness scope (doc/angelo/grant_operators.rst, "Authority in C"), which exists only
in an app started with --enable-event-simulate. A suite that uses it, or ``grant_guard.stand_in()``, declares
``harness=True`` in ``native_suite_manifest`` and the runner passes the flag, BACKGROUND included
(``native_suite_harness``)."""


def as_person(function, *args, **kwargs):
    """``function(*args, **kwargs)`` as the person's own action would run it."""
    from angelo_agent import grant_authority
    return grant_authority.GUARD.harness(function, *args, **kwargs)


SCRIPT = "angelo_execute_python"


class Reviewed:
    """A ``BlenderExecutor`` whose scripts run as the app runs one the person approved on its card.

    C runs a script only when ``approved_scripts`` holds its exact text for its call, once (``run_confined``), and
    the app asks for the call its chat is running (``script_admission.prepare``). A suite that drives the executor
    directly has no chat, so each script it runs gets a call of its own: its exact text approved as the person (C's
    harness scope), then run for that call. A call a chat already runs (the executor's ``studio_context``) is the
    chat's, untouched."""

    def __init__(self, executor, chat_id="qa-reviewed"):
        object.__setattr__(self, "_executor", executor)
        object.__setattr__(self, "_chat_id", chat_id)
        object.__setattr__(self, "_count", [0])

    def __getattr__(self, name):
        return getattr(self._executor, name)

    def __setattr__(self, name, value):
        setattr(self._executor, name, value)

    def execute(self, name, arguments, *args, **kwargs):
        executor = self._executor
        if name != SCRIPT or getattr(executor, "studio_context", None) is not None:
            return executor.execute(name, arguments, *args, **kwargs)
        from types import SimpleNamespace
        from angelo_agent import grant_authority
        self._count[0] += 1
        call = SimpleNamespace(name=SCRIPT, id="qa-script-%d" % self._count[0], arguments=arguments, automatic=False)
        as_person(grant_authority.approve_script, self._chat_id, call.id, arguments.get("code", ""))
        executor.studio_context = SimpleNamespace(state={"id": self._chat_id}, executing_call=call,
                                                  script_consent_kind=lambda _call: None)
        try:
            return executor.execute(name, arguments, *args, **kwargs)
        finally:
            executor.studio_context = None


def reviewing(executor_class):
    """``executor_class`` (``BlenderExecutor``) whose instances are ``Reviewed``."""
    def make(*args, **kwargs):
        return Reviewed(executor_class(*args, **kwargs))
    return make


def choose_approval(controller, mode, options=None):
    """The person's approval choice (Ask, Read only, Auto) for ``controller``'s chat as their own click makes it.

    C's ledger comes first (``approval_modes``, through the harness: Auto widens, so only the person may set it),
    then the chat's options: ``options`` when given, else the chat's own with ``approval`` replaced. Options set alone
    make a chat C does not hold in Auto: its Auto edits ask, and the worker chats it starts inherit only what C holds
    (``approval_modes.inherit_mode``)."""
    from angelo_agent import approval_modes
    as_person(approval_modes.record, controller.state["id"], mode)
    base = (controller.state.get("options") or {}) if options is None else options
    controller.state["options"] = dict(base, approval=mode)
