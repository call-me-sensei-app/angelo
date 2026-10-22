# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Debug mode: the model's thinking summaries in a diagnostic run's chat (doc/angelo/debug_mode.rst).

Claude Code 2.1.301 sends no thinking text in print mode unless asked: its
requests carry ``display: "updates"`` and every thinking block arrives empty.
``--thinking-display summarized`` asks the API for summarized thinking, which
``claude_stream`` already turns into thinking rows that the chat and its
session journal keep. The ``showThinkingSummaries`` setting is not used: print
mode never reads it as a display request (it only drops ``updates``), and the
measurement in doc/angelo/performance.rst ("Thinking summaries") saw no
thinking text with it.

Only a test harness turns it on, by setting ``Controller.debug_thinking`` in
the app; it is never saved, has no preference or environment variable, and a
timed or benchmark-claim run never sets it. Codex already streams its public
reasoning summaries, so nothing changes there.

``SHOWN_TO_EVERYONE`` is the same display for every chat, debug mode or not:
on Claude it is the only thing that can show the model's work before its plan
or call starts streaming (51-101 s of "Thinking" on the recorded Opus 5.5
one-shots of the 1.0.0 scene campaign; the first summary text arrives about
2.4 s after the request, doc/angelo/performance.rst "Thinking summaries").
It is on (maintainer, 2026-10-21: "Yes, if it costs nothing"). The
pre-registered probe found no cost: calls-2+ cache read share 97.8% against
97.7% with the display omitted, and no break in caching when a session
switches; $0.1207 against $0.1218 list per trial, and per-call wall 6.69
against 7.09 s median on Opus 5.5 at low effort. The first thinking text came
after a median 4.1 s, against none. Long thinks of a minute or more are
checked on whole tasks by the plan-stream A/B (doc/angelo/performance.rst,
"Streamed progress"), whose control arm is a build with this constant off. A
run with it on is a real-user run, not a diagnostic one: debug mode still
marks diagnostic runs, and on Claude it adds nothing more. Display only either
way: the model thinks the same.

Standard library only.
"""

# The display Claude Code asks the API for in debug mode.
DISPLAY = "summarized"
# Every chat's Claude sessions ask for that display (see above). Off for 1.0.4 (maintainer, 2026-10-22): the
# whole-task Opus A/B measured +11-14% cost and +8-10% wall, against "only if it costs nothing". Debug mode still asks.
SHOWN_TO_EVERYONE = False


def enabled(context):
    """Whether this request's context (``routing.RuntimeContext`` or a test double) is in debug mode."""
    return getattr(context, "debug_thinking", False) is True


def claude_arguments(context):
    """Claude Code launch arguments for the thinking display: summarized for every chat, or in debug mode only."""
    return ["--thinking-display", DISPLAY] if enabled(context) or SHOWN_TO_EVERYONE is True else []
