# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Auto's rule for a step that is not a script, as a reason code: ``None`` runs it with no card, any other code names
the rule that makes it ask. No ``bpy``.

``Controller._auto_bounded`` is ``bounded_reason(...) is None``, so the card's line saying why it asks in Auto
(``approval_wait.auto_reason``) is selected by the very code that made the step ask, never re-derived from a tool
list. Every code the rule can return in Auto has its line in ``LINES``, or is listed in ``NO_LINE`` with the reason
its card needs none (pinned by ``test_native_approval_wait``): a new branch of the rule cannot ship a card that says
nothing, or the wrong thing, about why it asks.

The card reads ``(msgid, fields)`` (``approval_wait.auto_reason``): a code's line and ``line_fields``, or a pair a
branch of the rule returns itself, for a reason it computed with its own fields. The maintainer's Auto ruling of
2026-11-05 (renders and exports to new files in the project's folder run in Auto; text files, overwrites, deletes and
writes anywhere else still ask; design ``auto_outputs``) lands as such a branch: ``None`` for an allowed decision, its
stored decision's ``(msgid, fields)`` when denied, and ``UNDECIDED`` (no line) before it has decided. A text file asks
by its own code (``WRITE_FILE``, ruled the same day).
"""

from . import render_extensions
from .chat_settings import clean_options
from .engine_link_tool_spec import asks_only
from .tool_specs import BOUNDED_MUTATIONS
from .ui_text import n_

NOT_AUTO = "not_auto"                      # The chat is not in Auto: Ask asks for every edit, Read only refuses.
OUTSIDE = "outside_auto"                   # A kind of step Auto never runs on its own (not a bounded edit).
ADDON_ENGINE = "addon_engine"              # A scene program that sets an add-on's render engine (render_extensions).
WORKER_START_CAP = "worker_start_cap"      # Past Auto's worker starts for one request (worker_sessions).
WORKER_MERGE_AGAIN = "worker_merge_again"  # A merge that places a part merged before (worker_merge).
WRITE_FILE = "write_file"                  # A text file: it always asks (the maintainer, 2026-11-05).
UNDECIDED = "undecided"                    # A branch whose decision is not made yet: its own reason comes later.
SCRIPT_EXCLUDED = "script_excluded"        # A script naming preferences or Angelo's add-on (trusted_scripts).

LINES = {
    OUTSIDE: n_("Approve for me never runs this kind of step on its own, so it asks for your approval."),
    ADDON_ENGINE: n_("This edit switches the render engine to one an installed add-on provides, which runs that "
                     "add-on's code, so it asks even in Approve for me."),
    WORKER_START_CAP: n_("Approve for me starts workers without asking up to a limit of {count} for each of your "
                         "messages. This start goes past it, so it asks."),
    WORKER_MERGE_AGAIN: n_("This merge adds another copy of a part already merged into your scene, so it asks even "
                           "in Approve for me."),
    WRITE_FILE: n_("A text file can hold anything, so writing one always asks."),
    SCRIPT_EXCLUDED: n_("This script refers to preferences or to Angelo's own add-on, so it always asks, even when "
                        "scripts are allowed."),
}
NO_LINE = {NOT_AUTO: "only Auto explains why a card asks; Ask asks for every edit",   # i18n: for developers
           UNDECIDED: "no decision yet, so no reason to state; the card waits for the decision's own"}   # i18n: for developers


def bounded_reason(controller, call, mode=None):
    """``None``: Auto runs this bounded edit without a card. Else the code of the rule that makes it ask.

    A worker start only up to the parts one request may start so (``worker_sessions.auto_start_allowed``: billed
    worker sessions, break-it E2E-2 M3), and a merge unless its ``again`` places a part merged before
    (``worker_merge.auto_merge_allowed``, E2E-20 R5-M-2). In Ask only the worker-done event's merge runs."""
    mode = mode or clean_options(controller.state.get("options"))["approval"]
    if mode in {"ASK", "AUTO_BOUNDED"} and asks_only(call.name, getattr(call, "arguments", None)):
        return None   # It only raises the person's own request card (engine_link_tool_spec.asks_only).
    if mode != "AUTO_BOUNDED":
        from . import worker_resume
        if mode == "ASK" and call.name == "angelo_workers_merge" and worker_resume.event_merge(controller,
                                                                                               call.arguments):
            return None
        return NOT_AUTO
    if call.name == "angelo_library_import":
        return None   # One Undo step; a free Fab asset joins the library too, a paid one is refused (maintainer, 2026-11-09).
    if call.name not in BOUNDED_MUTATIONS:
        return WRITE_FILE if call.name == "angelo_write_file" else OUTSIDE
    if call.name == "angelo_workers_start":
        from .worker_sessions import auto_start_allowed
        return None if auto_start_allowed(controller, call.arguments) else WORKER_START_CAP
    if call.name == "angelo_workers_merge":
        from .worker_merge import auto_merge_allowed
        return None if auto_merge_allowed(controller, call.arguments) else WORKER_MERGE_AGAIN
    if call.name == "angelo_scene_program" and render_extensions.engine_edits(call.arguments):
        return ADDON_ENGINE   # Setting one starts its code, so Auto asks first, as a render with it does.
    return None


def line_fields(code):
    """The placeholders ``LINES[code]`` names, filled from the rule's own limits."""
    if code == WORKER_START_CAP:
        from .worker_sessions import MAX_AUTO_STARTS
        return {"count": MAX_AUTO_STARTS}
    return {}
