# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why a worker chat's request did not go through, as the person reads it, and its support code (design A′, C3).

A worker's answer carries its own words: a refusal's English message (``worker_values.encode_error``), a transport's
("no answer within 2 s"), or the lead's own check ("its worker could not send precondition"). None of them is
translatable, and most are internal. A person reads one of the plain, translatable reasons below instead, chosen by
the answer's kind (not answered in time, its worker ended, the consent lease lost, never sent because the chat is not
open in a worker) or by the refusal code the worker sent (``refusal_codes``, a closed set: prose never decides).

Each reason has one **support code** (``WRK-*``, ``SUPPORT``), shown beside it: the same in every language, so a
person can quote it and support can tell the case from it (doc/angelo/agent_integration.rst lists them). The refusal
code and the raw words go to the log line ``explain`` prints, with that support code, never to the person.

A reason is a ``display_text.Text``: as a field of a ``ui_text.notice`` it is kept as a nested recipe, so the row is
translated whole at display time. Every reason reads as a clause inside every refusal template (``(... {why};
support code {code})``) without repeating that template's own words (pinned by ``test_native_worker_reasons``).
Standard library only and no ``bpy``.
"""

from .display_text import Text
from .ui_text import n_

TIMED_OUT = n_("its worker did not answer in time")
GONE = n_("its worker has ended")
CONSENT = n_("its worker did not confirm in time what this chat may do without asking")
CHANGED = n_("the chat or its part changed first")
ENDED_FIRST = n_("the chat's turn had already ended")
TURNED_DOWN = n_("it was already turned down")
NOT_OPEN = n_("the chat is not open in its worker")
MOVED_ON = n_("the chat in its worker had moved on")
READ_ONLY = n_("the chat is set to Read only")
NOT_VALID = n_("its worker found it invalid or too large")
UNSUPPORTED = n_("this version of its worker cannot do it")
BUSY = n_("its part was busy with another change")
RECOVERY = n_("its part needs to be recovered first")
FAILED = n_("something went wrong in its worker")
NO_CONNECTION = n_("this chat's connection can't be used now: it was removed, signed out or not allowed")

BY_CODE = {"STALE_SETTING": CHANGED, "STALE_REVISION": CHANGED, "STALE_DEPENDENCY": CHANGED, "STALE_CONTEXT": CHANGED,
           "FRAME_CHANGED": CHANGED, "EXPIRED": CHANGED, "CANCELLED": ENDED_FIRST, "DECLINED": TURNED_DOWN,
           "OWNERSHIP": MOVED_ON, "READ_ONLY": READ_ONLY, "VALIDATION": NOT_VALID, "CHECK_FAILED": NOT_VALID,
           "UNSUPPORTED": UNSUPPORTED, "LIMIT_EXCEEDED": NOT_VALID, "WRITER_BUSY": BUSY,
           "RECOVERY_REQUIRED": RECOVERY}   # INTERNAL_ERROR, CLEANUP_FAILED, UNKNOWN and anything new: FAILED.

# One support code per reason, stable across releases and languages (doc/angelo/agent_integration.rst).
SUPPORT = {TIMED_OUT: "WRK-NO-ANSWER", GONE: "WRK-ENDED", CONSENT: "WRK-CONSENT", CHANGED: "WRK-CHANGED",
           ENDED_FIRST: "WRK-TURN-ENDED", TURNED_DOWN: "WRK-TURNED-DOWN", NOT_OPEN: "WRK-NOT-OPEN",
           MOVED_ON: "WRK-MOVED-ON", READ_ONLY: "WRK-READ-ONLY", NOT_VALID: "WRK-INVALID",
           UNSUPPORTED: "WRK-UNSUPPORTED", BUSY: "WRK-BUSY", RECOVERY: "WRK-RECOVERY", FAILED: "WRK-INTERNAL",
           NO_CONNECTION: "WRK-NO-CONNECTION"}
BY_SUPPORT = {code: chosen for chosen, code in SUPPORT.items()}
APPROVAL_LOST = "WRK-APPROVAL-LOST"   # An Approve the worker never received (``worker_mirror_settle``).


def explain(answer, code="UNKNOWN", log=None):
    """``(Text, support code)`` of why ``answer`` (a worker op's answer dict, ``{"error", "timed_out"?, "gone"?,
    "consent"?, "not_sent"?}``) did not go through, given its refusal ``code``. ``log`` names the op for the log line
    that keeps the raw words and the refusal code."""
    answer = answer if isinstance(answer, dict) else {}
    if answer.get("consent") is True:
        chosen = CONSENT
    elif answer.get("gone") is True:
        chosen = GONE
    elif answer.get("timed_out") is True:
        chosen = TIMED_OUT
    elif answer.get("not_sent") is True and code == "UNKNOWN":
        chosen = NOT_OPEN   # The lead never sent it: no worker hosts the chat now (``ChatOpFailed.sent``).
    else:
        chosen = BY_CODE.get(code, FAILED)
    support = SUPPORT[chosen]
    if log is not None:
        print("Angelo: a worker chat's %s did not go through: %s (%s, %s)" % (log, _raw(answer)[:300], code, support))
    return Text(chosen), support


def reason(answer, code="UNKNOWN", log=None):
    """The ``Text`` of ``explain`` alone."""
    return explain(answer, code, log)[0]


def answer_of(error):
    """The answer dict an op whose send raised ``error`` stands for (``explain``'s input): its kind, and its refusal
    code under ``error_code`` (``refusal_codes``; UNKNOWN for anything not classified)."""
    from .refusal_codes import refusal_code
    from .worker_chat_lifecycle import ChatOpFailed
    return {"error": str(error) or type(error).__name__, "error_code": refusal_code(error),
            "timed_out": getattr(error, "timed_out", False) is True, "gone": getattr(error, "gone", False) is True,
            "not_sent": isinstance(error, ChatOpFailed) and getattr(error, "sent", None) is False}


def of_error(error, log=None):
    """``(Text, support code)`` for an exception the lead raised or decoded (``ChatOpFailed``, ``RefusalError``...)."""
    answer = answer_of(error)
    return explain(answer, answer["error_code"], log)


def _raw(answer):
    error = answer.get("error")
    if isinstance(error, dict):
        return "%s: %s" % (error.get("type"), error.get("message"))
    return str(error or "no answer")  # i18n: a log line, never shown

