# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plan card under an ``angelo_plan`` ``PROPOSE`` row, and plan mode's chip in the composer
(doc/angelo/plan_mode.rst, "The card and chips").

``layout`` is pure: the card's ops (``rect``, ``text``, ``hit``) for a plan in a state, as ``job_chat.card_layout``'s
are, so host tests read every string, colour and hit. ``chat_cards`` asks here for the card's height and draws its ops;
clicks on its ``plan_*`` hits come back to ``dispatch``. Go is the one control that grants: its hit value is
``(call_id, approach, setting)``, so the press's ticket covers exactly the approach and the setting the person sees,
and its dispatch runs the grant operator ``angelo.plan_go`` in that press's scope. No bpy at import.
"""

from . import planning_state as S
from . import planning_text as T
from . import ui_tokens as tk
from .ui_text import iface_

PAD, GAP, BUTTON_H, PILL_H = 16, 8, 32, 26
SETTINGS = ("ASK", "AUTO_BOUNDED")
GO, EDIT, THEN, CHIP = "plan_go", "plan_edit", "plan_then", "plan_chip"


def _wrap_lines(wrap, text, width, size):
    return [value.rstrip() for value, _ in wrap(text, width, size)] or [""]


def _button(ops, rect, label, action, value, primary):
    if primary:
        ops.append(("rect", *rect, tk.TEAL, 8))
        ops.append(("text", label, rect[0] + 14, rect[1] + 10, 12.5, tk.INK, rect[2] - 20))
    else:
        ops.append(("rect", *rect, tk.LINE, 8))
        ops.append(("rect", rect[0] + 1, rect[1] + 1, rect[2] - 2, rect[3] - 2, tk.PANEL_HOVER, 7))
        ops.append(("text", label, rect[0] + 14, rect[1] + 10, 12.5, tk.TEXT, rect[2] - 20))
    ops.append(("hit", rect, action, value))


def _go_button(ops, rect, lines, value):
    """Go with this · <setting>, on as many lines as it takes: its label is what the press does, never cut."""
    x, y, width, height = rect
    ops.append(("rect", x, y, width, height, tk.TEAL, 8))
    for row, line in enumerate(lines):
        ops.append(("text", line, x + 14, y + height - 22 - row * 16, 12.5, tk.INK, width - 20))
    ops.append(("hit", rect, GO, value))


def _pill(ops, x, y, label, measure, chosen, action, value):
    """One segment of the Then control: the chosen one teal-rimmed and tinted, the other a quiet outline."""
    width = measure(label, 11.5) + 22
    ops.append(("rect", x, y, width, PILL_H, tk.TEAL if chosen else tk.LINE, PILL_H / 2))
    ops.append(("rect", x + 1, y + 1, width - 2, PILL_H - 2, tk.TEAL_TINT if chosen else tk.PANEL, PILL_H / 2 - 1))
    ops.append(("text", label, x + 11, y + 8, 11.5, tk.TEAL if chosen else tk.MUTED, width - 14))
    ops.append(("hit", (x, y, width, PILL_H), action, value))
    return width


def layout(plan, call_id, state, setting, x, top, width, wrap, line_height, measure, chosen=None):
    """``(ops, height)`` of the card for ``plan`` in ``state`` (``planning_state.card_state``), drawn from ``top``
    down. ``setting``: the Then control's choice (Ask or Auto); ``chosen``: the approach the person went with."""
    inner = max(60, width - 2 * PAD)
    ops, y = [], top - PAD - 12
    ops.append(("rect", x + PAD, y + 2, 10, 10, tk.TEAL, 3))   # Plan mode's mark: a teal tile, as its chip's.
    ops.append(("text", iface_(T.CARD_TITLE, "Angelo"), x + PAD + 18, y, 13.5, tk.TEXT, inner - 18))
    for line in _wrap_lines(wrap, plan["summary"], inner, 12.5):
        y -= line_height
        ops.append(("text", line, x + PAD, y, 12.5, tk.SUBTITLE, inner))
    live = state == S.LIVE
    for index, approach in enumerate(plan["approaches"]):
        y -= GAP + 6
        ops.append(("rect", x + PAD, y, inner, 1, tk.LINE, 0))
        y -= line_height + 2
        label = iface_(T.APPROACH, number=index + 1)
        ops.append(("text", label.upper(), x + PAD, y, 10.5, tk.MUTED, inner))
        if index == plan["recommended"]:
            chip = iface_(T.RECOMMENDED)
            cw = measure(chip, 10.5) + 16
            cx = x + PAD + measure(label.upper(), 10.5) + 10
            ops.append(("rect", cx, y - 5, cw, 20, tk.TEAL_SOFT, 10))
            ops.append(("text", chip, cx + 8, y, 10.5, tk.TEAL, cw - 10))
        for line in _wrap_lines(wrap, approach["title"], inner, 14):
            y -= line_height + 2
            ops.append(("text", line, x + PAD, y, 14, tk.TEXT, inner))
        for number, step in enumerate(approach["steps"], 1):
            prefix = "%d." % number
            for row, line in enumerate(_wrap_lines(wrap, step, inner - 22, 12)):
                y -= line_height
                if row == 0:
                    ops.append(("text", prefix, x + PAD, y, 12, tk.MUTED, 20))
                ops.append(("text", line, x + PAD + 22, y, 12, tk.TEXT, inner - 22))
        for risk in approach["risks"]:
            for row, line in enumerate(_wrap_lines(wrap, risk, inner - 22, 12)):
                y -= line_height
                if row == 0:
                    ops.append(("rect", x + PAD + 4, y + 3, 7, 7, tk.AMBER, 3.5))
                ops.append(("text", line, x + PAD + 22, y, 12, tk.SUBTITLE, inner - 22))
        y -= line_height
        ops.append(("text", iface_(T.ESTIMATE, estimate=approach["estimate"]), x + PAD, y, 11.5, tk.MUTED, inner))
        if live:
            go, edit = iface_(T.GO_LABELS.get(setting, T.GO_ASK)), iface_(T.EDIT)   # Go names its setting (M1).
            gw, ew = min(inner, max(110, measure(go, 12.5) + 28)), max(70, measure(edit, 12.5) + 28)
            lines = _wrap_lines(wrap, go, gw - 28, 12.5) if measure(go, 12.5) + 28 > gw else [go]
            gh = BUTTON_H + (len(lines) - 1) * 16   # Never elided: the setting it switches to is always read (N3 nit).
            y -= GAP + gh
            _go_button(ops, (x + PAD, y, gw, gh), lines, (call_id, index, setting))
            ex, ey = x + PAD + gw + GAP, y + gh - BUTTON_H
            if ex + ew > x + PAD + inner:   # A long translation: Edit goes below Go.
                y -= GAP + BUTTON_H
                ex, ey = x + PAD, y
            _button(ops, (ex, ey, min(ew, inner), BUTTON_H), edit, EDIT, (call_id, index), False)
    if plan["questions"]:
        y -= GAP + 6
        ops.append(("rect", x + PAD, y, inner, 1, tk.LINE, 0))
        y -= line_height + 2
        ops.append(("text", iface_(T.QUESTIONS).upper(), x + PAD, y, 10.5, tk.MUTED, inner))
        for question in plan["questions"]:
            for row, line in enumerate(_wrap_lines(wrap, question, inner - 22, 12)):
                y -= line_height
                if row == 0:
                    ops.append(("rect", x + PAD + 5, y + 4, 5, 5, tk.TEAL, 2.5))
                ops.append(("text", line, x + PAD + 22, y, 12, tk.TEXT, inner - 22))
    y -= GAP + 6
    ops.append(("rect", x + PAD, y, inner, 1, tk.LINE, 0))
    if live:
        y -= GAP + PILL_H
        then = iface_(T.THEN)
        ops.append(("text", then, x + PAD, y + 8, 11.5, tk.MUTED, inner))
        px = x + PAD + measure(then, 11.5) + 10
        for value in SETTINGS:
            label = iface_(T.SETTINGS[value])
            if px > x + PAD and px + measure(label, 11.5) + 22 > x + PAD + inner:
                y -= GAP + PILL_H
                px = x + PAD
            px += _pill(ops, px, y, label, measure, value == setting, THEN, (call_id, value)) + 6
        for line in _wrap_lines(wrap, iface_(T.FOOTER), inner, 11):
            y -= line_height - 2
            ops.append(("text", line, x + PAD, y, 11, tk.MUTED, inner))
    else:
        status = (iface_(T.CHOSEN, number=(chosen or 0) + 1,
                         title=plan["approaches"][chosen]["title"] if isinstance(chosen, int)
                         and 0 <= chosen < len(plan["approaches"]) else "")
                  if state == S.CHOSEN else iface_(T.SUPERSEDED if state == S.SUPERSEDED else T.ENDED))
        for line in _wrap_lines(wrap, status, inner, 12):
            y -= line_height
            ops.append(("text", line, x + PAD, y, 12, tk.TEAL if state == S.CHOSEN else tk.MUTED, inner))
    height = top - y + PAD
    return [("rect", x, top - height, width, height, tk.PANEL, 10)] + ops, height


# The chat canvas (chat_cards) ----------------------------------------------------------------------------------------

def _shown():
    """The shown chat, or None (the canvas draws the shown chat's rows)."""
    import sys
    ui = sys.modules.get(__package__ + ".ui")
    return ui.get_controller() if ui is not None else None


def _card(message, state, x, top, width, wrap, line_height):
    from . import planning_controls
    plan = S.plan_of(message)
    controller = _shown()
    if plan is None or controller is None:
        return None
    call_id = message.get("call_id")
    shown, chosen = S.card_state(controller.state["messages"], call_id, planning_controls.is_planning(controller))
    from .chat_settings import stored_options
    preset = stored_options(controller.state.get("options")).get("plan_return", "ASK")
    setting = state.setdefault("plan_then", {}).get(call_id, preset)
    from . import chat_cards
    return layout(plan, call_id, shown, setting, x, top, width, wrap, line_height, chat_cards._measure, chosen)


def attached_height(message, width, wrap, line_height, state):
    card = _card(message, state, 16, 0, width, wrap, line_height)
    return card[1] if card else 0


def draw_attached(message, state, x, top, width, wrap, line_height):
    from . import chat_view
    card = _card(message, state, x, top, width, wrap, line_height)
    if card is None:
        return
    for op in card[0]:
        if op[0] == "rect":
            chat_view.shape(*op[1:7])
        elif op[0] == "text":
            _, value, tx, ty, size, color, limit = op
            chat_view.text(chat_view.elide(value, max(8, limit), size), tx, ty, size, color)
        elif op[0] == "hit":   # canvas_controls draws hover, pressed and focus; C ticketed it (grant_guard).
            state["hits"].append(op[1:])


def _edit_text(plan, index):
    """The approach as the message box holds it for the person to edit (the plan's own words)."""
    approach = plan["approaches"][index]
    return "\n".join([approach["title"]] + ["%d. %s" % (n, step) for n, step in enumerate(approach["steps"], 1)])


def _drafts(area, write=None):
    """The draft of the chat editor ``area`` whose card was pressed: read (``write`` None) or set to ``write``
    (text, caret). Only that editor's: another chat's draft is never taken or cleared (break-it review nit)."""
    from . import console
    from .editor_types import read_chat_draft, write_chat_draft
    spaces = [area.spaces.active] if area is not None and console.is_chat(area) else []
    if write is not None:
        for space in spaces:
            write_chat_draft(space, write)
        return None
    return next((str(draft[0]) for draft in map(read_chat_draft, spaces) if draft and str(draft[0]).strip()), "")


def dispatch(context, controller, state, action, value):
    """A plan card's or chip's click, on the main thread. Returns whether it did what its control says."""
    from . import grant_guard, planning_controls
    if action == THEN:
        call_id, setting = value
        state.setdefault("plan_then", {})[call_id] = setting if setting in SETTINGS else "ASK"
        return True
    if action == EDIT:
        call_id, index = value
        live_id, plan = S.live_plan(controller.state["messages"], planning_controls.is_planning(controller))
        if live_id != call_id or not 0 <= index < len(plan["approaches"]):
            return False
        current = _drafts(context.area)
        text = _edit_text(plan, index) if not current.strip() else current.rstrip() + "\n\n" + _edit_text(plan, index)
        _drafts(context.area, (text, len(text)))   # The caret is a character index (drafts).
        controller.set_options(plan_draft=S.edit_mark(call_id, plan, index))   # The draft's provenance (S2, N2b).
        return True
    if action == GO:   # The grant operator, in this press's own C scope (grant_guard.by_click opened it).
        call_id, index, setting = value
        import bpy
        grant_guard.clicked(bpy.ops.angelo).plan_go(
            'EXEC_DEFAULT', session_id=controller.state["id"], call_id=call_id, approach=index, approval=setting)
        return True   # A refusal is the operator's to say (planning_ui), once.
    if action == CHIP + "_stop":
        import bpy
        grant_guard.clicked(bpy.ops.angelo).plan_stop('EXEC_DEFAULT', session_id=controller.state["id"])
        return True
    if action == CHIP + "_revise":   # Shielded (it ends a goal): only in the scope of the person's own armed press.
        from . import grant_authority, grant_hints
        if not grant_authority.granting():
            grant_hints.refused("canvas." + action)   # Their press before it armed: the control shows the hint.
            return False
        planning_controls.revise(controller)
        return True
    if action == CHIP + "_dismiss":
        controller._plan_chip_dismissed = value
        return True
    return False


# The composer's chip -------------------------------------------------------------------------------------------------

def chip_parts(controller):
    """What plan mode's chip shows, or None: ``(title, [(label, action, value)], close action, close value)``."""
    from . import planning_controls
    if planning_controls.is_planning(controller):
        if getattr(controller, "_plan_ended_goal", False):
            return iface_(T.CHIP_PLANNING_GOAL_ENDED), [], CHIP + "_stop", None
        return iface_(T.CHIP_PLANNING), [], CHIP + "_stop", None
    chosen = _chosen(controller)
    if chosen is None or getattr(controller, "_plan_chip_dismissed", None) == chosen[0]:
        return None
    return (iface_(T.CHIP_PLAN, title=chosen[1]), [(iface_(T.REVISE), CHIP + "_revise", None)], CHIP + "_dismiss",
            chosen[0])


def _chosen(controller):
    """``planning_state.chosen`` of the chat's rows, read once per change of them (the chip is asked three times a
    frame in every chat; break-it review nit 7). The rows are appended, replaced at the end or swapped for a new list:
    the list, its length and its last row say when to read again."""
    messages = controller.state.get("messages")
    key = (len(messages), messages[-1]) if messages else (0, None)
    cached = getattr(controller, "_plan_chosen", None)
    if cached is None or cached[0] is not messages or cached[1][0] != key[0] or cached[1][1] is not key[1]:
        cached = (messages, key, S.chosen(messages))
        controller._plan_chosen = cached
    return cached[2]


def chip_shown(controller):
    return chip_parts(controller) is not None


def draw_chip(view, state, controller, x, y, width):
    """The chip at ``(x, y)``, ``width`` wide and 40 tall, as the goal chip is drawn; registers its hits."""
    parts = chip_parts(controller)
    if parts is None:
        return
    title, buttons, close, close_value = parts
    view.shape(x, y, width, 40, tk.TEAL, 9)
    view.shape(x + 1, y + 1, width - 2, 38, tk.TEAL_TINT, 8)
    for row in range(3):   # Plan mode's glyph: a short checklist.
        view.shape(x + 11, y + 12 + row * 6, 3, 3, tk.TEAL, 1.5)
        view.shape(x + 17, y + 12.5 + row * 6, 9, 2, tk.TEAL, 1)
    right = x + width - 30
    for label, action, value in reversed(buttons):
        pill = view.measure(label, 11) + 16
        right -= pill + 6
        view.shape(right, y + 9, pill, 22, tk.TEAL_SOFT, 11)
        view.text(label, right + 8, y + 16, 11, tk.TEAL)
        state["hits"].append(((right, y + 9, pill, 22), action, value))
    view.text(view.elide(title, max(40, right - (x + 34) - 6), 12), x + 34, y + 15, 12)
    view.icon("close", x + width - 12, y + 20)
    state["hits"].append(((x + width - 26, y + 7, 26, 27), close, close_value))
