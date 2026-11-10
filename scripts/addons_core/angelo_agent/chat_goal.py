# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The goal chip in the composer and its card (popover kind ``"goal"``; doc/angelo/goal_mode.rst, "The chip and the
card").

``chip_parts`` and ``card_blocks`` say what is shown for a goal in each state, translated here at display time, with
no drawing (host tests read them); ``draw_chip`` and ``draw`` lay them out on the chat canvas with the kit's controls
and colours, as the usage card does; ``act`` and ``dispatch`` run the person's clicks through ``goal_controls``. The
chip is one full-width row at the top of the composer's chips (``chip_shown`` reserves it). No bpy at import.
"""

from . import goal_state as G, ui_kit_controls, ui_tokens
from .ui_text import iface_, n_

WIDTH, PAD, ROW = 360, 14, 46
PASS_AT = (0.8, 0.9, 0.95)
MINUTES = (15, 30, 60, 120)
STALL = (2, 3, 5)
SPEND_USD = (None, 2.0, 10.0, 25.0)
SPEND_TOKENS = (None, 1_000_000, 5_000_000)
PAUSED_TITLES = {G.PAUSED_BY_MESSAGE: n_("Paused for your message"),
                 G.PAUSED_BY_RESTART: n_("Paused: Angelo restarted"),
                 G.PAUSED_BY_AGENT: n_("Paused: the agent asks you"),
                 G.PAUSED_BY_IDLE: n_("Paused: nothing was continuing it")}


def _goal_and_pending(controller):
    from . import goal_loop, goal_store
    return goal_store.cached_goal(controller), goal_loop.pending_goal(controller)


def shown_goal(controller):
    """The goal the chip shows: one chosen in the chat bar and not sent yet, else the chat's held goal."""
    goal, pending = _goal_and_pending(controller)
    return pending if pending is not None else goal


def chip_shown(controller):
    return shown_goal(controller) is not None


def _percent(value):
    return int(round(value * 100))


def _latest_score(goal):
    scored = [entry["score"] for entry in goal["rounds"] if entry.get("score") is not None]
    return scored[-1] if scored else goal.get("best")


def _money(usd):
    return "$%.2f" % usd


def _tokens(count):
    from . import usage_view
    return usage_view.tokens(int(count))


def _spend_caption(goal):
    budget, spent = goal["budget"], goal["spent"]
    if budget.get("usd") is not None:
        return iface_("{spent} / {limit}", spent=_money(spent["usd"]), limit=_money(budget["usd"]))
    if budget.get("tokens") is not None:
        return iface_("{spent} / {limit} tokens", spent=_tokens(spent["tokens"]), limit=_tokens(budget["tokens"]))
    return iface_("{spent} tokens", spent=_tokens(spent["tokens"])) if spent["tokens"] else ""


def _end_title(goal):
    status = goal["status"]
    score = goal.get("ended_at_score")
    if status == G.PASSED:
        if goal["check"] == G.REFERENCE and score is not None:
            return iface_("Passed · {score}% from one camera", score=_percent(score))
        return iface_("Passed")
    titles = {G.STALLED: n_("Stopped: no progress"), G.BUDGET: n_("Stopped: budget used"),
              G.UNMEASURED: n_("Stopped: could not be measured"), G.DONE: n_("Ended by the agent"),
              G.BLOCKED: n_("Stopped: the agent is blocked"), G.FAILED: n_("Stopped: a round failed"),
              G.STOPPED: n_("Stopped")}
    title = iface_(titles.get(status, n_("Stopped")))
    if status in (G.DONE, G.BLOCKED, G.STALLED, G.BUDGET) and score is not None and goal["check"] == G.REFERENCE:
        title = iface_("{title} · {score}%", title=title, score=_percent(score))
    return title


def chip_parts(controller, goal=None, pending=None, waiting=None):
    """``{"title", "caption", "buttons": [(label, action)], "close": action, "tone"}`` for the goal shown, or None."""
    from . import goal_loop
    if goal is None and pending is None:
        goal, pending = _goal_and_pending(controller)
        waiting = goal_loop.waiting_reason(controller)
    shown = pending if pending is not None else goal
    if shown is None:
        return None
    budget, spent = shown["budget"], shown["spent"]
    minutes = int(round(spent["working_ms"] / 60_000))
    parts = {"buttons": [], "tone": "teal"}
    if pending is not None:
        parts["title"] = iface_("Until it matches the picture") if shown["check"] == G.REFERENCE else \
            iface_("Until it's done")
        caption = [iface_("Goal", "Angelo"), iface_("{minutes} min", minutes=budget["minutes"])]
        parts["close"] = "discard"
    elif shown["status"] == G.PROPOSED:
        parts["title"] = iface_("Proposed: keep going until it's done")
        caption = [iface_("Goal", "Angelo"), iface_("{minutes} min", minutes=budget["minutes"])]
        parts.update(buttons=[(iface_("Start", "Angelo"), "start")], close="stop")
    elif shown["status"] == G.PAUSED:
        parts["title"] = iface_(PAUSED_TITLES.get(shown.get("paused_by"), n_("Paused")))
        caption = [iface_("Goal", "Angelo"), iface_("{used} / {limit} min", used=minutes, limit=budget["minutes"])]
        parts.update(buttons=[(iface_("Resume"), "resume")], close="stop", tone="amber")
    elif shown["status"] == G.ACTIVE:
        score, rounds = _latest_score(shown), len(shown["rounds"]) + 1
        if waiting == goal_loop.WAITING_DRAFT:
            parts["title"] = iface_("Waiting while you write a message")
        elif G.judged(shown):
            parts["title"] = iface_("Agent judges · round {round}", round=rounds)
        elif shown["check"] == G.REFERENCE and score is not None:
            parts["title"] = iface_("{score}% of {pass_at}% · round {round}", score=_percent(score),
                                    pass_at=_percent(shown["pass_at"]), round=rounds)
        elif shown["check"] == G.SCENE and score is not None:
            parts["title"] = iface_("{score} checks pass · round {round}", score=int(score), round=rounds)
        else:
            parts["title"] = iface_("Measuring · round {round}", round=rounds)
        caption = [iface_("Goal", "Angelo"), iface_("{used} / {limit} min", used=minutes, limit=budget["minutes"])]
        if shown.get("measure_request") is not None:
            parts["buttons"] = [(iface_("Allow new measure"), "allow")]
        parts["close"] = "stop"
    else:
        parts["title"] = _end_title(shown)
        caption = [iface_("Goal", "Angelo"), iface_("{count} rounds", count=len(shown["rounds"])),
                   iface_("{minutes} min", minutes=minutes)]
        parts.update(close="dismiss", tone="muted" if shown["status"] != G.PASSED else "teal")
    spend = _spend_caption(shown)
    if spend and pending is None:
        caption.append(spend)
    parts["caption"] = " · ".join(caption)
    return parts


# Drawing: the chip -------------------------------------------------------------------------------------------------

def _pill(view, x, y, label, fill, color):
    width = view.measure(label, 11) + 16
    view.shape(x, y, width, 22, fill, 11)
    view.text(label, x + 8, y + 7, 11, color)
    return width


def draw_chip(view, state, controller, x, y, width):
    """The chip at ``(x, y)``, ``width`` wide and 40 tall; registers its hits."""
    parts = chip_parts(controller)
    if parts is None:
        return
    edge = {"teal": ui_tokens.TEAL, "amber": ui_tokens.AMBER, "muted": view.LINE}[parts["tone"]]
    view.shape(x, y, width, 40, edge, 9)
    view.shape(x + 1, y + 1, width - 2, 38, ui_tokens.TEAL_TINT, 8)
    view.shape(x + 10, y + 12, 16, 16, edge, 8)   # A target: the goal's glyph, as in the + menu.
    view.shape(x + 12, y + 14, 12, 12, ui_tokens.TEAL_TINT, 6)
    view.shape(x + 15, y + 17, 6, 6, edge, 3)
    right = x + width - 30
    for label, action in reversed(parts["buttons"]):
        pill = view.measure(label, 11) + 16
        right -= pill + 6
        _pill(view, right, y + 9, label, ui_tokens.TEAL_SOFT, ui_tokens.TEAL)
        state["hits"].append(((right, y + 9, pill, 22), "goal_action", action))
    text_width = max(40, right - (x + 34) - 6)
    view.text(view.elide(parts["title"], text_width, 12), x + 34, y + 23, 12)
    view.text(view.elide(parts["caption"].upper(), text_width, 10), x + 34, y + 8, 10,
              edge if parts["tone"] != "muted" else view.MUTED)
    view.icon("close", x + width - 12, y + 25)
    state["hits"].append(((x + width - 26, y + 13, 26, 27), "goal_action", parts["close"]))
    state["goal_anchor"] = (x, y, width, 40)
    state["hits"].append(((x, y, max(1, right - x), 40), "goal", None))   # The rest opens the card.


# The card ----------------------------------------------------------------------------------------------------------

def _segments(values, label):
    return [(value, label(value)) for value in values]


def card_blocks(goal, pending=False, paid=False):
    """The card's blocks, top to bottom: ``(kind, height, data)``."""
    blocks = [("title", 30, iface_("Goal", "Angelo")),
              ("objective", 0, goal["objective"] or iface_("Your next message"))]
    status = goal["status"] if not pending else G.PENDING
    editable = status in (G.PENDING, G.PROPOSED, G.ACTIVE, G.PAUSED)
    if pending:
        blocks.append(("segment", ROW, ("check", iface_("How it's checked"), [
            (G.REFERENCE, iface_("Match the picture")), (G.JUDGED, iface_("Agent judges"))], goal["check"])))
    else:
        what = {G.REFERENCE: n_("Outline of the attached picture, from one camera"), G.SCENE: n_("Scene checks"),
                G.JUDGED: n_("The agent judges")}[goal["check"] if not G.judged(goal) else G.JUDGED]
        if goal["check"] == G.REFERENCE and G.judged(goal):
            what = n_("The agent judges until it sets a camera for the picture")
        blocks.append(("row", 24, (iface_("How it's checked"), iface_(what))))
        measure = goal.get("measure") or {}
        if goal.get("measure_request") is not None:
            blocks.append(("action", 34, (iface_("The agent asks to change how this is measured"),
                                          iface_("Allow"), "allow")))
        elif measure.get("locked") and editable:
            blocks.append(("action", 34, (iface_("Measurement locked after its first round"),
                                          iface_("Let the agent change it"), "unlock")))
    if goal["check"] == G.REFERENCE and editable:
        blocks.append(("segment", ROW, ("pass_at", iface_("Pass at"),
                                        _segments(PASS_AT, lambda v: "%d%%" % _percent(v)), goal["pass_at"])))
    if editable:
        budget = goal["budget"]
        blocks.append(("segment", ROW, ("minutes", iface_("Working time"),
                                        _segments(MINUTES, lambda v: iface_("{minutes} min", minutes=v)),
                                        budget["minutes"])))
        if paid:
            blocks.append(("segment", ROW, ("usd", iface_("Spend"), _segments(
                SPEND_USD, lambda v: iface_("Off") if v is None else _money(v)), budget.get("usd"))))
        else:
            blocks.append(("segment", ROW, ("tokens", iface_("Tokens"), _segments(
                SPEND_TOKENS, lambda v: iface_("Off") if v is None else _tokens(v)), budget.get("tokens"))))
        blocks.append(("segment", ROW, ("stall_rounds", iface_("Rounds without progress"),
                                        _segments(STALL, str), budget["stall_rounds"])))
    for entry in goal["rounds"][-6:]:
        seconds = int(entry.get("working_ms", 0) // 1000)
        spent = _tokens(entry.get("tokens") or 0)
        if paid and entry.get("usd") is not None:
            spent = iface_("{tokens}, {money}", tokens=spent, money=_money(entry["usd"]))
        score = entry.get("score")
        measured = ("%d%%" % _percent(score)) if score is not None and goal["check"] == G.REFERENCE else \
            str(int(score)) if score is not None else "—"
        blocks.append(("round", 20, iface_("Round {round} · {minutes}m {seconds}s · {spent} · {measured}",
                                           round=entry["n"], minutes=seconds // 60, seconds=seconds % 60,
                                           spent=spent, measured=measured)))
    buttons = []
    if status == G.PROPOSED:
        buttons.append((iface_("Start", "Angelo"), "start"))
    if status == G.PAUSED:
        buttons.append((iface_("Resume"), "resume"))
    if status in (G.PROPOSED, G.ACTIVE, G.PAUSED):
        buttons.append((iface_("Stop goal"), "stop"))
    elif pending:
        buttons.append((iface_("Remove"), "discard"))
    else:
        buttons.append((iface_("Dismiss"), "dismiss"))
    blocks.append(("buttons", 38, buttons))
    return blocks


def draw(view, state, controller, width, height):
    """The open card (``chat_view.draw_popover``, kind ``"goal"``)."""
    import gpu
    from . import goal_probes
    goal, pending = _goal_and_pending(controller)
    shown = pending if pending is not None else goal
    if shown is None:
        state.pop("popover", None)
        return
    wrap = lambda value, w, size: [line[0] if isinstance(line, tuple) else line
                                   for line in view._flow_wrap(value, w, size)]
    inner = min(WIDTH, width - 16) - 2 * PAD
    blocks = []
    for kind, block_height, data in card_blocks(shown, pending is not None, goal_probes.paid(controller)):
        if kind == "objective":
            lines = wrap(data, inner, 12)[:3]
            block_height, data = 18 * len(lines) + 6, lines
        blocks.append((kind, block_height, data))
    total = sum(block[1] for block in blocks) + 2 * PAD
    ax, ay, aw, ah = state.get("goal_anchor", (16, 120, width - 32, 40))
    w = inner + 2 * PAD
    x = max(8, min(ax, width - w - 8))
    y = min(ay + ah + 8, max(8, height - total - 8))
    h = min(total, height - y - 8)
    state["popup_rect"] = (x, y, w, h)
    view.shape(x, y, w, h, view.LINE, 15)
    view.shape(x + 1, y + 1, w - 2, h - 2, view.POPOVER, 14)
    old_scissor = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(*view._scissor(x + 1, y + 1, w - 2, h - 2))
    try:
        top, left = y + h - PAD, x + PAD
        for kind, block_height, data in blocks:
            bottom = top - block_height
            _draw_block(view, state, kind, data, left, inner, top, bottom)
            top = bottom
    finally:
        gpu.state.scissor_set(*old_scissor)
        gpu.state.scissor_test_set(False)


def _draw_block(view, state, kind, data, left, inner, top, bottom):
    if kind == "title":
        view.text(data, left, bottom + 8, 15)
    elif kind == "objective":
        for index, line in enumerate(data):
            view.text(line, left, top - 14 - index * 18, 12, view.MUTED)
    elif kind == "row":
        label, value = data
        view.text(label, left, bottom + 6, 11, view.MUTED)
        view.text(view.elide(value, inner - view.measure(label, 11) - 12, 12),
                  left + view.measure(label, 11) + 12, bottom + 6, 12)
    elif kind == "action":
        note, label, action = data
        width = view.measure(label, 11) + 16
        view.text(view.elide(note, inner - width - 10, 11), left, bottom + 12, 11, view.MUTED)
        _pill(view, left + inner - width, bottom + 6, label, ui_tokens.TEAL_SOFT, ui_tokens.TEAL)
        state["popup_hits"].append(((left + inner - width, bottom + 6, width, 22), "popup_goal_act", action))
    elif kind == "segment":
        field, label, segments, selected = data
        view.text(label, left, top - 12, 11, view.MUTED)
        seg_w = min(inner, ui_kit_controls.segmented_width(view, 1.0, segments))
        rect = (left, bottom + 2, seg_w, 26)
        ui_kit_controls.draw_segmented(view, rect, 1.0, None, segments, selected, "popup_goal_set")
        for hit_rect, action, value in ui_kit_controls.segmented_hits(rect, segments, "popup_goal_set"):
            state["popup_hits"].append((hit_rect, action, (field, value)))
    elif kind == "round":
        view.text(view.elide(data, inner, 11), left, bottom + 5, 11, view.MUTED)
    elif kind == "buttons":
        x = left
        for index, (label, action) in enumerate(data):
            width = view.measure(label, 12) + 24
            primary = index == 0 and action in ("start", "resume")
            view.shape(x, bottom + 6, width, 28, ui_tokens.TEAL if primary else view.LINE, 14)
            view.text(label, x + 12, bottom + 15, 12, view.BG if primary else view.TEXT)
            state["popup_hits"].append(((x, bottom + 6, width, 28), "popup_goal_act", action))
            x += width + 8


# Clicks ------------------------------------------------------------------------------------------------------------

def act(controller, action):
    """A click on the chip's pills or close glyph, or the card's buttons."""
    from . import goal_controls
    handlers = {"start": goal_controls.start, "resume": goal_controls.resume, "stop": goal_controls.stop,
                "allow": goal_controls.allow_measure, "unlock": goal_controls.unlock, "dismiss": goal_controls.dismiss,
                "discard": goal_controls.discard_pending}
    handler = handlers.get(action)
    if handler is not None:
        try:
            handler(controller)
        except goal_controls.HeldElsewhere:   # Start or Resume while another chat holds this document's goal.
            from .goal_text import held_elsewhere_notice
            controller.emit("message", {"role": "system", **held_elsewhere_notice()})
    controller.version = getattr(controller, "version", 0) + 1


def dispatch(context, controller, state, action, value):
    """A click inside the open card."""
    from . import goal_controls
    if action == "popup_goal_act":
        act(controller, value)
        if value in ("dismiss", "discard"):
            state.pop("popover", None)
    elif action == "popup_goal_set" and isinstance(value, tuple):
        goal_controls.set_option(controller, *value)
        controller.version = getattr(controller, "version", 0) + 1
    if context.area:
        context.area.tag_redraw()
