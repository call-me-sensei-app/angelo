# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script being written, drawn under the live row; never stored.

``script_stream.ScriptText`` reads a script's code as the model writes it
(a Claude plan's script step, or a direct ``angelo_execute_python`` call); the
provider's reader thread sends the newest lines as ``script_preview`` events,
at most twice a second (``claude_stream.THINKING_SECONDS``). Until the call
is complete the code is not a script that will run: the runtime's schema
check can refuse it, a later call can supersede it, the person can stop it,
and the host reviews and runs only the complete call. So the lines live here,
on the controller, under the request generation they belong to, and only the
chat draws them (``shown``). The transcript, its saves, handoffs and rebases
never contain them.

They are shown exactly as long as the status says a plan or call is being
written (``claude_stream.STATUS_PLAN``, the status that carries its
provisional steps, ``plan_steps``): when the plan arrives, the runtime
refuses it, the call ends or the turn finishes, the status changes and the
lines are gone with it; ``finished`` or a newer request drops them. The text
is redacted whole each time it is drawn, holding back a tail that could begin
the connection's key (``reply_overlay.drawable``), and only then are its lines
cut to ``script_stream.MAX_LINE`` characters, so no cut leaves part of a key
that redaction would no longer recognise.

Main thread only: the event pump writes and the chat draws.
"""

import re

from . import reply_overlay
from .script_stream import MAX_LINE

_SURROGATES = re.compile("[\ud800-\udfff]")  # A lone surrogate cannot be drawn: blf takes UTF-8.
# Off in 1.0.4: the A/B that would ship the well did not pass its pre-registered rule (plan-stream, 2026-10-22).
# With it off, nothing is drawn; the stream reader's previews are kept and dropped as before.
SHOWN = False
ATTRIBUTE = "_script_preview"
DRAWN = "_script_preview_drawn"  # (entry, key, lines): the lines drawn for one preview, redacted once.
EVENT = "script_preview"


def update(controller, kind, data):
    """Apply one chat event; True when it was a script preview, which then needs nothing else."""
    if kind == "finished":
        controller.__dict__.pop(ATTRIBUTE, None)
        controller.__dict__.pop(DRAWN, None)
        return False
    if kind != EVENT:
        return False
    lines = data.get("lines")
    text = data.get("text")
    if type(lines) is int and lines > 0 and isinstance(text, str):
        controller.__dict__[ATTRIBUTE] = (controller._request_generation, lines, text)
    else:
        controller.__dict__.pop(ATTRIBUTE, None)  # A new call started: its script has no code yet.
    return True


def shown(controller):
    """``(lines so far, [newest lines])`` while the live row says a plan or call is being written, else None."""
    from .claude_stream import STATUS_PLAN
    from .plan_progress import split
    entry = controller.__dict__.get(ATTRIBUTE)
    if not SHOWN or not entry or entry[0] != controller._request_generation or not getattr(controller, "busy", False):
        return None
    if split(getattr(controller, "status", ""))[0] != STATUS_PLAN:
        return None
    key = getattr(controller, "api_key", "") or ""
    cached = controller.__dict__.get(DRAWN)
    if cached is not None and cached[0] is entry and cached[1] == key:
        return cached[2]  # A redraw between previews (the marble animation's, at up to its frame rate).
    text = _SURROGATES.sub("\ufffd", reply_overlay.drawable(controller, entry[2]))  # Drawable text only.
    drawn = (entry[1], [line[:MAX_LINE] for line in text.split("\n")]) if text else None
    controller.__dict__[DRAWN] = (entry, key, drawn)
    return drawn
