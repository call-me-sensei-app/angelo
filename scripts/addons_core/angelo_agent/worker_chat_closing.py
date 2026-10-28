# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a worker chat is, on Angelo's side, while its ``chat.close`` is under way (design A', contract C10).

A chat is **closing** from before its ``chat.close`` is sent (``worker_chat_lifecycle.close_chat``) until that close
ended: answered, refused or lost. Its worker may still host it then, or may have closed it already, so closing is a
state of its own, neither hosted nor handed back, and every reader of where a chat is hosted treats it as one:

* ``state``: ``HOSTED``, ``CLOSING`` or None (handed back, or never opened);
* a merge waits for the close, bounded and off the main thread (``when_closed``), then holds nothing for a chat that
  was handed back (``worker_merge_gate``); a park's batch end waits for the park (``worker_resume``), and a message to
  a parking chat waits for its relaunch (``worker_mirror_park``);
* an op its worker answered "this worker hosts no such chat" (``not_hosted``, read from the refusal's details, never
  its words) for a chat that is closing or was handed back meanwhile reached a chat that had closed: it changed
  nothing there, so a merge's hold or release counts it as nothing to hold, never as a refusal. The same answer
  for a chat Angelo still counts as hosted, and not closing, stays a refusal (fail closed).

Evidence: Mac 2 under load (3c351c9, parallel_resume@hosted): a merge right after a park sent ``chat.hold`` while the
park's close was under way, and was refused WRITER_BUSY ("This worker hosts no such chat"). Standard library only, no
bpy; any thread.
"""

from . import worker_chat_lifecycle as lifecycle

HOSTED, CLOSING = "hosted", "closing"


def state(session):
    """``HOSTED`` (its worker hosts it), ``CLOSING`` (its ``chat.close`` is under way) or None (handed back)."""
    with lifecycle._lock:
        if session not in lifecycle._hosted:
            return None
        return CLOSING if session in lifecycle._closing else HOSTED


def when_closed(session, timeout):
    """Any thread but Blender's main one: wait, at most ``timeout`` seconds, for a close of ``session`` under way to
    end. True when none is under way any more (it ended, or none was)."""
    with lifecycle._lock:
        done = lifecycle._closing.get(session)
    return done is None or done.wait(max(0.0, timeout))


def not_hosted(error):
    """Whether ``error`` (a ``ChatOpFailed``) is its worker's own answer that it hosts no such chat (it closed there,
    or never opened), by the refusal's details, never its words."""
    record = getattr(error, "record", None)
    details = record.get("details") if isinstance(record, dict) else None
    if details is None:
        return False
    from .worker_values import decode
    try:
        details = decode(details)
    except (TypeError, ValueError):
        return False
    return isinstance(details, dict) and details.get("not_hosted") is True


def closed_meanwhile(session, worker, error):
    """Whether ``error`` answered an op of ``session`` sent to ``worker`` that found the chat closed there: its worker
    said it hosts no such chat, and Angelo knows the chat is closing, or no longer hosted on ``worker``."""
    if not not_hosted(error):
        return False
    return state(session) == CLOSING or lifecycle.hosted(session) is not worker
