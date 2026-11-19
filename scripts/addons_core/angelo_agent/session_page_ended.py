# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A live session page that ended after an error says so, to the app and to the person (``session_page``).

Before 2.0.1's fix a page whose build failed ended quietly: the app kept a dead link and nothing told it or the
person. Each such end is recorded per conversation until a new link starts: the app reads it in its state
(``live_page_ended``: why, and that its next message starts a new page; ``external_agent``), and the conversation
says it once (``FAILED``). Every catch is also reported to ``swallowed_errors``, so a test fails on it. No bpy.
"""

from . import swallowed_errors
from .ui_text import n_

FAILED = n_("This session's live page stopped after an error, so its link no longer opens. The app's next message "
            "starts a new one.")
_ended = {}   # conversation -> why its page ended after an error (English, for the app).


def failed(identity, why, error, say):
    """``identity``'s page ended after ``error`` (``why``: in words); ``say(identity, template)``: its notice."""
    swallowed_errors.report("a live session page ended after an error", error)
    if identity not in _ended:
        say(identity, FAILED)
    _ended[identity] = "%s (%s)" % (why, type(error).__name__)


def started(identity):
    """A new link for ``identity``: what ended before is over."""
    _ended.pop(identity, None)


def ended(identity):
    """What the app reads when ``identity``'s page ended after an error, or None."""
    if identity not in _ended:
        return None
    return {"why": "This session's live page ended: " + _ended[identity],
            "next": "The next angelo_send starts a new one"}
