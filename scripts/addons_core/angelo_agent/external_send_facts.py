# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a waiting send's answer keeps of the send itself: one definition for the host and the launcher.

A send's answer is the conversation's state at the send plus a few facts about the send. A send that waits is
answered later, after the conversation changed (``external_agent``'s parked send in the host, ``external_mcp_agent``'s
long poll in the launcher), and the state there is the latest read's alone: the send's earlier approval and needs (a
hidden session's relayed card included), queued, not_sent, goal or live page are never kept over it (2.0.1 reviews of
ffe232b8253 and b3726b1c11f: a card the person answered during the wait was still offered). ``SEND_FACTS`` is a
whitelist, so a live field added to the state later is never kept stale. The live page's link is state too
(``live_page``: in every read while the page is live), so a page that ended for any reason (an error, Stop sharing,
access off, a move) is never offered once a read no longer says it is live. Pure standard library: the launcher runs
it without Blender.
"""

SEND_FACTS = ("delivered", "task", "cursor", "moved")   # How the send went, its task, its cursor, its move.


def facts(sent):
    """``sent``'s own facts (a send's answer, or None)."""
    return {key: sent[key] for key in SEND_FACTS if key in (sent or {})}


def answer(sent, read):
    """A waiting send's answer: ``read`` (the latest state, with rows) over ``sent``'s own facts."""
    return dict(facts(sent), **read)


def with_live_page(state, controller):
    """``state`` with the conversation's live page link while its page is live (``session_page``, when this build has
    it); ``state`` itself."""
    try:
        from . import session_page
    except ImportError:
        return state
    url = session_page.page_url(controller)
    if url:
        state["live_page"] = url
    return state
