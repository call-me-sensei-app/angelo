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

Standard library only.
"""

# The display Claude Code asks the API for in debug mode.
DISPLAY = "summarized"


def enabled(context):
    """Whether this request's context (``routing.RuntimeContext`` or a test double) is in debug mode."""
    return getattr(context, "debug_thinking", False) is True


def claude_arguments(context):
    """Claude Code launch arguments for debug mode: the summarized thinking display, or none."""
    return ["--thinking-display", DISPLAY] if enabled(context) else []
