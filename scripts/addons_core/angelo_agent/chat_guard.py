# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No single row, card or report stops the chat from drawing, and none fails out of sight.

The Director capture (2026-10-25, Sol desk_chair on 9060a812b84): one malformed change report raised on every draw
of the lead's chat, so the whole native chat stopped after its header and the console's plain text showed instead,
cards and all gone, in Director and manual alike. The chat now builds, counts and draws each row and card on its own:
one that raises is drawn plainly (who wrote it and up to ``LINES`` lines of what it says; an approval keeps its answer),
the error is logged once per kind, and every other row draws as designed. Each draw's failures become
``chat_view.last_error`` (``begin_draw``, ``errors``), so the GUI suites that read it still fail on a row that raises
(break-it REVIEW-CHAT-GUARD H1). The Changes popup does the same per report.
"""

import math
import os
import sys

LINES = 3          # A plain row shows at most this many lines of what it says.
LOG_LIMIT = 256    # Kinds of failure logged once each; past it, none more (never a log per frame).
SPOKEN_BY = {"activity": "tool", "thinking": "assistant"}   # Whose words a row is, by the console's names.
APPROVALS = frozenset({"approval", "flow_approval", "approval_plain"})   # Rows that answer a pending request.
_logged = set()
_drawing = []      # This draw's failures, as text.


def begin_draw():
    _drawing.clear()


def errors():
    """This draw's failures as ``chat_view.last_error`` holds them, or None when every part drew as designed."""
    return "; ".join(_drawing)[:600] or None


def note(what, error):
    """``what`` failed with ``error``: kept for this draw, and logged once per kind (its type and innermost line)."""
    _drawing.append("%s: %s: %s" % (what, type(error).__name__, str(error)[:120]))
    frame = error.__traceback__
    while frame is not None and frame.tb_next is not None:
        frame = frame.tb_next
    where = "%s:%d" % (os.path.basename(frame.tb_frame.f_code.co_filename), frame.tb_lineno) if frame else "?"
    key = (what, type(error).__name__, where)
    if key not in _logged and len(_logged) < LOG_LIMIT:
        _logged.add(key)
        print("Angelo: %s could not be drawn as designed (%s: %s at %s); drawn plainly" % (   # i18n: a log line
            what, type(error).__name__, str(error)[:200], where), file=sys.stderr)


def call(what, function, *args, default=None):
    """``function(*args)``, or ``default`` when it raises (noted)."""
    try:
        return function(*args)
    except Exception as error:   # noqa: BLE001 - one part never stops the chat.
        note(what, error)
        return default


def card(what, build, fallback=None):
    """A card built on its own: ``build()``, or when it raises ``fallback(error)`` (a plain card that notes the error)
    or, without one, nothing (noted)."""
    try:
        return build()
    except Exception as error:   # noqa: BLE001
        if fallback is None:
            note(what, error)
            return None
        try:
            return fallback(error)
        except Exception as failed:   # noqa: BLE001 - both noted: the cause first (REVIEW-CHAT-GUARD-2 I2).
            note(what, error)
            note(what, failed)
            return None


def checked(blocks, width, size, line_height):
    """``blocks`` with every one in the shape the draw loop unpacks (six fields, a height that is a number); one that
    is not is a plain row in its place, so a builder's slip never stops the loop (REVIEW-CHAT-GUARD M2)."""
    kept = []
    for block in blocks if isinstance(blocks, (list, tuple)) else ():
        try:
            role, message, rows, height, identity, bubble = block
            if not isinstance(role, str) or isinstance(height, bool) or not isinstance(height, (int, float)) \
                    or not (math.isfinite(height) and height >= 0) or not isinstance(bubble, (int, float)):
                raise TypeError("a chat block of the wrong shape")
            iter(rows)
            kept.append(block)
        except Exception as error:   # noqa: BLE001
            message = block[1] if isinstance(block, (list, tuple)) and len(block) > 1 else None
            kept.append(fallback_block(message, ("plain", len(kept)), error, width, size, line_height))
    return kept


def _title(call_):
    try:
        from .chat_titles import tool_title   # It reads the call's name as "tool" (REVIEW-CHAT-GUARD-2 N1).
        return tool_title({"role": "activity", "tool": getattr(call_, "name", ""), "arguments": {}})
    except Exception:   # noqa: BLE001
        return str(getattr(call_, "name", ""))


def parts(message, rows=()):
    """``(label, body)``: what a row or card says, plainly, whatever its shape. The label is the console's translated
    name for who wrote it; the body is the block's laid-out rows, else the row's own display text or stored words, else
    the title of its tool (an approval or a flow card)."""
    label = body = ""
    role = message.get("role") if isinstance(message, dict) else None
    try:
        from . import console
        from .ui_text import iface_
        name = console.ROLE_NAMES.get(SPOKEN_BY.get(role, role))
        label = iface_(name) if name else ""
    except Exception:   # noqa: BLE001
        pass
    try:
        body = " ".join(text for text in (str(row[0]).strip() for row in rows if isinstance(row, (list, tuple)) and row)
                        if text)
    except Exception:   # noqa: BLE001
        body = ""
    if not body and isinstance(message, dict):
        try:
            from . import console
            body = console.display_text(message) if role else ""
        except Exception:   # noqa: BLE001 - its own text is what failed; its stored words still show.
            body = ""
        body = body or next((message[key] for key in ("text", "title", "label", "name")
                             if isinstance(message.get(key), str) and message[key].strip()), "")
        if not body and message.get("call") is not None:
            body = _title(message["call"])
    return label, str(body)


def _lines(body, width, size):
    """Up to ``LINES`` wrapped lines of ``body``; the last says it goes on."""
    body = " ".join(body.split())[:600]
    try:
        from . import chat_view
        rows = [str(row[0]) for row in chat_view.wrap(body, max(40, width), size, chat_view._font)]
    except Exception:   # noqa: BLE001
        rows = [body[:160]] if body else []
    if len(rows) > LINES:
        rows = rows[:LINES - 1] + [rows[LINES - 1].rstrip() + " …"]
    return rows


def fallback_block(message, identity, error, width, size, line_height):
    """A row that could not be built: its label and a few lines of what it says, as a quiet notice row."""
    note("A chat row", error)   # i18n: a log line, never drawn
    label, body = parts(message)
    rows = [(value, 0) for value in ([label] if label else []) + _lines(body, width - 60, size)]
    return ("notice", message if isinstance(message, dict) else {}, rows, max(1, len(rows)) * line_height + 24,
            identity, 0)


def approval_block(call_, width, size, line_height, error):
    """An approval card that could not be built: what the real card says before its Details (``approval_review.
    preview_text``: title, body, usage label, warning) with Decline, and Approve only when that text was built and
    approving needs nothing else the real card holds (``_approvable``), so a request is never left without an answer
    and nothing is approved unseen (REVIEW-CHAT-GUARD M1, -2 N1)."""
    note("An approval card", error)   # i18n: a log line, never drawn
    try:
        from . import approval_review, chat_view
        rows = chat_view.wrap(approval_review.preview_text(call_), max(40, width - 64), size, chat_view._font)
        approvable = _approvable(call_)
    except Exception:   # noqa: BLE001 - its words could not be built either: its tool's name, and Decline only.
        rows, approvable = [(_title(call_), 0)], False
    return ("approval_plain", {"call": call_, "approvable": approvable}, rows, len(rows) * line_height + 72,
            call_.id, 0)


def _approvable(call_):
    """Whether Approve may be offered on a plain card: never when approving needs what only the real card holds, a
    program's shown review (``approval_review.approve_current_review``), a script's code (``ui.ANGELO_OT_approve``
    reveals it first) or a flow run's selection."""
    from . import approval_review, flow_chat
    return not (call_.name in approval_review._PROGRAM_TOOLS or call_.name == "angelo_execute_python"
                or flow_chat.is_run_review(call_))


def draw_approval(state, call_, approvable, rows, top, bottom, size, line_height):
    """A plain approval card: its rows as far as its rectangle goes, then Approve (when ``approvable``) and Decline."""
    from .chat_card_draw import BUTTON_Y, _button
    from .chat_view import TEXT, text
    from .ui_text import iface_
    x = 32   # The answer first: a row that cannot be drawn never takes it away (REVIEW-CHAT-GUARD-3 L1).
    if approvable:
        x = _button(state, x, bottom + BUTTON_Y, iface_("Approve"), "approve", call_.id, "primary")
    _button(state, x, bottom + BUTTON_Y, iface_("Decline"), "decline", call_.id, "secondary")
    room = max(1, int((top - bottom - 64) // line_height))
    for index, row in enumerate(list(rows)[:room]):
        call("A plain approval row", text, str(row[0]).rstrip(), 32, top - line_height * (index + 1), size, TEXT)


def shown_approvals(controller, hits):
    """``hits`` as drawn, after every transcript row drew (each card's own way, its plain card, or ``draw_plain`` after
    it raised): each Approve the person can click records the call it shows (``worker_consent_lead.drew``), so a
    worker-hosted chat's Approve sends the digest of the card that was on screen, whichever path drew it (A′, C6)."""
    from .worker_consent_lead import drew
    for _rect, action, value in hits:
        if action == "approve":
            call("An Approve's drawn card", drew, controller, value)
    return hits


def saved_state():
    """The matrix and scissor the transcript's rows draw under, to put back after one that raised (M5)."""
    import gpu
    return gpu.matrix.get_model_view_matrix().copy(), tuple(gpu.state.scissor_get())


def draw_plain(state, saved, hits_at, role, message, rows, key, error, top, bottom, width, size, line_height,
               controller):
    """A row that raised while it was drawn: what it registered dropped (L4), the drawing state put back (M5), its
    rectangle painted over (L3), then its label and as many lines of what it says as its rectangle holds (L6: two runs,
    no new text; -2 N4). A pending request's card keeps its answer: Decline, and Approve as ``_approvable`` (-2 N2)."""
    note("A chat row", error)   # i18n: a log line, never drawn
    try:
        del state["hits"][hits_at:]
        state["message_rects"].pop(key, None)
    except Exception:   # noqa: BLE001
        pass
    try:
        import gpu
        from .chat_view import BG, MUTED, TEXT, measure, shape, text
        gpu.matrix.load_matrix(saved[0])
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(*saved[1])
        gpu.state.blend_set('ALPHA')
        shape(0, bottom, width, top - bottom, BG)
        call_ = controller.pending.get(key) if role in APPROVALS else None
        if call_ is not None:
            approvable = message.get("approvable", True) if role == "approval_plain" else role == "approval"
            draw_approval(state, call_, approvable and _approvable(call_), rows or [(_title(call_), 0)], top, bottom,
                          size, line_height)
            return
        label, body = parts(message, rows)
        x = 40
        if label:
            text(label, x, top - line_height, size, MUTED)
            x += measure(label, size) + 8
        room = max(1, int((top - bottom - 8) // line_height))
        for index, value in enumerate(_lines(body, width - x - 20, size)[:room]):
            text(value, x, top - line_height * (index + 1), size, TEXT)
    except Exception:   # noqa: BLE001 - nothing more to draw for this row; the others still draw.
        pass
