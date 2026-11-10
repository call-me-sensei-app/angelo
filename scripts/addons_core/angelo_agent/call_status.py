# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A status the host shows for one call's work ends with that call. No ``bpy``.

The host names its own work on the live row ("Working in Angelo", "Waiting for a render", "Generating media", ...:
``chat_titles.LIVE``). A runtime that reports no status of its own between steps (Codex) left such a status in
place after the call ended, so the row named work that was over until the next model event: the same class as the
stale "Waiting for your approval" of 1.0.9, which ``approval_wait`` now derives from the card itself. The controller
records the status on the call that owns it (``call.shown_status = self.status = "..."``, written so at each site
that the literal-status scan of ``test_native_live_progress`` keeps seeing it); ``settle``, when that call finishes,
gives the row to the newest other pending call that still names its own work (a media job still generating), else
back to the model's thinking, unless something else has set the status since.
"""

from .claude_stream import STATUS_THINKING


def settle(controller, call):
    """``call`` has finished: its status, still shown, becomes another pending call's own, or the model's again."""
    shown = getattr(call, "shown_status", "")
    if not shown or controller.status != shown or not getattr(controller, "busy", False):
        return
    others = [other.shown_status for other in tuple(controller.pending.values())
              if other is not call and getattr(other, "shown_status", "")]
    controller.status = others[-1] if others else STATUS_THINKING
