# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The launcher's tools for Angelo without its window (doc/angelo/mcp_headless_window.rst), mixed into
``external_mcp.Server`` beside ``AgentTools``: ``angelo_open_file`` starts a hidden-window Angelo on a file
(``headless_window.HiddenWindow``), ``angelo_file_status`` / ``angelo_close_file`` / ``angelo_show`` /
``angelo_answer_approval`` (decline only) act on it; every other agent tool then reaches it as it reaches a window.
Pure standard library."""

import json

HIDDEN_NAMES = frozenset({"angelo_open_file", "angelo_file_status", "angelo_close_file", "angelo_show",
                          "angelo_answer_approval"})
NEEDS_PERSON = ("needs_person: only the person approves, in Angelo. Tell your human what waits and call angelo_show; "
                "Angelo's window comes up with the card for them to press.")
HIDDEN_NEXT = ("Angelo runs on this file with its window hidden. Use angelo_send and angelo_read with Angelo's agent "
               "as "
               "with the window. A call that needs the person's approval is shown in angelo_read's state.approval: "
               "tell your human and call angelo_show, which brings Angelo's window up so the person answers it there "
               "(you may decline it with angelo_answer_approval). Changes are saved to a new file next to the source "
               "as Angelo works and when the session ends (angelo_close_file, idle, a cap); the source is never "
               "written.")
SHOWN_NEXT = ("Angelo's window is up on the working copy of this file. It is the person's window now: they answer "
              "approvals there, and your calls go to it as to any Angelo window. Its changes save next to the source.")


def _result(value, error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": value, "isError": error}


def coded(code, text):
    return _result({"code": code, "message": text}, error=True)


def access_refusal(environ=None):
    """Why no app may start Angelo without its window now, else None: the computer's kill switch. The person's own
    switch and the organization's and computer's policy are Angelo's to apply, in the hidden app itself: with app
    access off it ends at once (``headless_window_app``), and the start says so."""
    from . import product_env
    value, variable = product_env.lookup("DISABLE_LOCAL_CLI", environ)
    if value == "1":
        return "Access for external apps is off on this computer (%s=1)" % variable
    return None


class HiddenTools:
    """``self.request``, ``self.state`` and ``self.client`` are ``external_mcp.Server``'s."""

    @property
    def hidden(self):
        """This launcher's hidden-window Angelo (one at a time), made at first use; ended here once past its time cap
        whatever the app does."""
        if getattr(self, "_hidden", None) is None:
            from .headless_window import HiddenWindow
            self._hidden = HiddenWindow(self.client, self.state, status=self._window_status)
        elif self._hidden.overdue():
            self._hidden.close()
        return self._hidden

    def _window_status(self):
        value = self.request({"command": "status"}, 5.0)
        return value.get("result") if value.get("code") == "ok" and isinstance(value.get("result"), dict) else None

    def end_sessions(self):
        """The launcher is ending: a hidden app ends with it (it stops its turn and saves first)."""
        if getattr(self, "_hidden", None) is not None:
            self._hidden.close()

    def hidden_tool(self, name, arguments):
        from .external_clients import AppRegistry
        from .headless_window import HeadlessUnavailable
        if name == "angelo_open_file":
            if AppRegistry(self.state).key(self.client) is None:
                return coded("not_connected", "This app is not connected to Angelo: connect it in Angelo first.")
            refusal = access_refusal()
            if refusal:
                return coded("refused", refusal)
            try:
                ready = self.hidden.open(arguments.get("file"), arguments.get("caps"))
            except HeadlessUnavailable as error:
                return coded(error.code, str(error))
            except Exception as error:  # noqa: BLE001 - a file the app named that can't be opened, said plainly.
                return coded("invalid", str(error))
            return _result(dict(ready, next=HIDDEN_NEXT))
        if not self.hidden.opened:
            return coded("no_file", "No file is open without Angelo's window (angelo_open_file starts one); with the "
                         "window, the person works and answers in Angelo.")
        if name == "angelo_file_status":
            return _result({"file": str(self.hidden.source), "hidden": True})
        if name == "angelo_close_file":
            was = str(self.hidden.source)
            self.hidden.close()
            return _result({"closed": was, "next": "Calls go to Angelo's window."})
        if name == "angelo_show":
            try:
                source = self.hidden.show()
            except HeadlessUnavailable as error:
                return coded(error.code, str(error))
            return _result({"shown": True, "file": str(source), "next": SHOWN_NEXT})
        if arguments.get("answer") != "decline":   # angelo_answer_approval: an app's model never approves (review M1).
            return coded("needs_person", NEEDS_PERSON)
        card = arguments.get("card")
        try:
            self.hidden.decline(card)
        except (ValueError, HeadlessUnavailable) as error:
            return coded("invalid", str(error))
        import time
        read = False
        for _ in range(4):   # The decline is applied on the app's next tick: read (a long poll) until the card is gone.
            value = self.agent_request({"action": "read", "detail": "brief", "wait": 3.0}, 3.0)
            if value.get("code") != "ok":
                time.sleep(1.0)   # Busy (the app's call rate) or unread is not declined: read again shortly.
                continue
            read = True
            state = value.get("result") or {}   # The state is the read's top level.
            if (state.get("approval") or {}).get("card") != card:
                return _result({"declined": True, "turn": state.get("turn"), "approval": state.get("approval")})
        if not read:   # Sent, but no read got through: never claimed either way (review 5, 5).
            return coded("unknown", "The decline was sent, but Angelo didn't answer a read in time; read again to see "
                         "whether the card is still waiting.")
        return coded("not_declined", "That card is still waiting: it changed or the decline didn't reach it; read "
                     "again for its current card.")
