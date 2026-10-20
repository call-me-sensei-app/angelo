# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a direct-tools call's result tells the model when it did not run, or ended its request (claude_direct).

Model-facing text: never translated. Each ending says, from the call's own facts, whether it ran and what it may
have changed (``change_state``), never "nothing ran" for a call that ran; the plan route's notes are said of the
one call, without their plan sentences (``call_note``).
"""

from . import request_recovery
from .tool_specs import TOOL_MAP

NOT_CURRENT = "This request no longer owns the turn; this call did not run. Inspect before retrying."
# A call refused because an earlier call ended the request, and the ending said in the call that ended it. Each says
# whether this call ran, from the result's own change facts (``_ended``), never "nothing ran" for a call that ran.
REFUSED = ("The request already ended ({why}); this call did not run. Make no further tool calls; tell the person in "
           "one short sentence.")
ENDED = ("The request ended with this call: {why} {state} Nothing further runs before the person looks; make no "
         "further tool calls and tell them in one short sentence what happened.")
UNCHANGED, MAY_HAVE_CHANGED, NOT_RUN = ("This call changed nothing.", "Its changes may be in the scene.",
                                        "This call did not run.")
DECLINED = "This call did not run: the person declined it."
# Sentences of the plan route's notes that describe a plan or its next round, left out of a call's note.
PLAN_SENTENCES = ("The host ran your plan in order.",
                  "The results of what ran, the current observation and any fresh views follow.",
                  "Submit only the operations the request still needs (each is approved again) and do not repeat the "
                  "ones that completed.")
LIMIT = "This request reached the host's limit of %d tool calls; inspect before continuing."


def change_state(tool, result, unchanged, dispatched):
    """What a call that ends the request did to the scene, from its own facts; conservative where none are stated."""
    result = result if isinstance(result, dict) else {}
    if not dispatched:
        return NOT_RUN
    if request_recovery.may_have_changed(result):
        return MAY_HAVE_CHANGED
    if unchanged is not None or request_recovery.unchanged(result) or TOOL_MAP[tool]["annotations"]["readOnlyHint"]:
        return UNCHANGED
    if "The user declined" in str(result.get("error", "")):   # plan_recovery's decision: the approval was refused.
        return DECLINED
    return MAY_HAVE_CHANGED   # A scene-changing call that ran and states nothing: the person looks.


def call_note(text, tool):
    """A plan-route note or rejection line, said of this one call: its plan sentences left out by content."""
    for sentence in PLAN_SENTENCES:
        text = text.replace(sentence, "")
    text = " ".join(text.split())
    return text.replace("operations[0] (%s)" % tool, "This call").replace("operations[0]", "This call")
