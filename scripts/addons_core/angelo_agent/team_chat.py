# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account in the chat: the welcome card's "Your team" row (``first_run.connect_options(team=…)``).

Part of the team account's main-thread glue (``team_account_ui``, which
re-exports these names). ``chat_welcome`` draws the row first, in the same
style as the other connect options, and routes its click (``welcome_team``) to
``welcome_action``. Label and note are message ids (``ui_text.n_``) that the
card translates where it draws them. A sign-in in progress adds ``busy`` (the
card shows the row as running) and ``cancel`` (the action of its Cancel
button, ``welcome_team_cancel``). Signed in, the row says which reason keeps
the team from the chat (``team_access``); its value is the console page that
fixes it for an admin. Once a team coding connection is usable there is no row:
the chat starts on that connection and says so (``ready_line``). When the managed policy allows only team
connections, ``chat_welcome`` keeps this row alone (``team_only_options``).
"""

import bpy

from . import managed_policy, team_access, team_account_ui
from .provider_registry import is_coding
from .team_view import OFFLINE, RESTORING, SIGNED_OUT, SIGNING_IN
from .ui_text import n_

CHAT_SIGNING_IN = n_("Finish signing in to your Angelo team in the browser.")
RECHECK_AFTER = 30.0  # Seconds: the shortest time between re-checks a focus change or a new chat asks for.
CODING_ORDER = ("claude_team", "codex_team")  # A chat starts on Claude Code when the team offers both.
_away = False        # A window lost focus; the next event in it re-checks the team's providers.
WELCOME_LABEL = n_("Your team")
WELCOME_NOTES = {"signed_out": n_("Sign in with your organization account"),
                 "signing_in": n_("Finish signing in in your browser"),
                 "restoring": n_("Restoring your team sign-in…"),
                 "offline": n_("Couldn't reach Angelo · retrying every minute"),
                 "ended": n_("Signed out of your team · sign in again")}


def coding_ready():
    return any(is_coding(r) and team_account_ui.ready(r) for r in team_account_ui._records.values())


def access():
    """``team_access.access`` for the current account, or None when not signed in."""
    account = team_account_ui._account
    view = account.view() if account is not None else None
    return team_access.access(view, coding_ready(), managed_policy.current().admin_contact) if view else None


def ready_line(record):
    """The chat's line for a chat on a usable team connection, or None for any other connection."""
    state = access() if record and record.get("method") == "TEAM" else None
    return state["note"] if state and state["state"] == team_access.READY else None


def welcome_row():
    """The row, or None when a team coding connection is usable (the chat starts on it) or there is no account."""
    account = team_account_ui._account
    if account is None:
        return None
    view = account.view()
    if view.signed_in:
        state = access()
        if state["state"] == team_access.READY:
            return None
        row = {"label": state["title"], "note": state["note"], "action": "welcome_team", "value": state["page"]}
        if state["busy"]:
            row.update(busy=True, cancel=None)
        return row
    if view.status == SIGNING_IN:
        note = "signing_in"
    elif view.status == RESTORING:
        note = "restoring"
    elif view.status == OFFLINE:
        note = "offline"
    else:
        note = "ended" if view.status == SIGNED_OUT and view.message else "signed_out"
    row = {"label": WELCOME_LABEL, "note": WELCOME_NOTES[note], "action": "welcome_team", "value": ""}
    if note in {"signing_in", "restoring"}:  # In progress: the card shows a spinner, and Cancel while signing in.
        row.update(busy=True, cancel="welcome_team_cancel" if note == "signing_in" else None)
    return row


def team_only_options(rows):
    """The card's rows the managed policy allows: only the team's when personal connections are off."""
    return [row for row in rows if row.get("id") == "team"] if managed_policy.current().team_only else rows


def welcome_action(page=""):
    """The row's click: sign in (the browser opens; the note says so); for an admin, the console page that fixes
    the reason shown (``page``: "people" or "keys"); else AI Providers, where the team's state is."""
    account = team_account_ui._account
    if account is not None and account.view().status == SIGNED_OUT:
        return bpy.ops.angelo.team_sign_in()
    if account is not None and page in {"people", "keys"} and account.view().signed_in:
        return team_account_ui.open_in_browser("%s/orgs/%s/%s" % (account.console_url, account.view().org_id, page))
    return bpy.ops.angelo.settings()


def recheck():
    """Re-read the team's providers soon, at most every ``RECHECK_AFTER`` seconds: a seat or key change in the
    console shows without a restart (focus returning, a chat opening; each token refresh re-reads them too)."""
    account = team_account_ui._account
    return bool(account is not None and account.refresh_providers(older_than=RECHECK_AFTER))


def note_window_event(event_type):
    """Called by the chat's input handler for each window event: focus coming back re-checks the team."""
    global _away
    if event_type == 'WINDOW_DEACTIVATE':
        _away = True
    elif _away:
        _away = False
        recheck()


def bind_team_chats(ui):
    """A chat with no connection starts on the team's when one is usable (Claude Code first), so a signed-in
    person with keys sees a ready chat, not the personal Connect options. A chat on a
    personal connection is left alone; a chat on a team connection that stopped working or went away (the team
    dropped that provider) moves to another."""
    if not hasattr(ui, "choose_model_connection"):
        return
    records = team_account_ui._records
    ready = sorted((r for r in records.values() if is_coding(r) and ui.connection_ready(r)),
                   key=lambda r: CODING_ORDER.index(r["mode"]) if r["mode"] in CODING_ORDER else len(CODING_ORDER))
    if not ready:
        return
    for controller in ui._controllers.values():
        current = controller.selection_connection_id
        record = records.get(current) or (ui.find_connection(current) if current else None)
        # Kept: a busy chat, a personal connection (usable or not), or a team connection that still works.
        if controller.busy or (record and (record.get("method") != "TEAM" or ui.connection_ready(record))):
            continue
        try:
            ui.choose_model_connection(controller, ready[0]["id"])
        except RuntimeError:
            continue
