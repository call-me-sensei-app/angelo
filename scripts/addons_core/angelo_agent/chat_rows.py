# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent progress in the chat transcript: work groups, step rows, the live row and host notices.

Moved out of ``chat_view.draw`` so the transcript's progress has one owner. Text comes from
``activity_text`` (plain language, never ids); the drawing primitives (``shape``, ``text``, ``stroke``,
fonts) stay chat_view's, so the canvas, its hit testing and right-to-left mirroring are unchanged.

API (chat_view calls these and nothing else):
    activity_block(message, role, identity, expanded, width, size, line_height, state) -> block tuple
    notice_block(message, identity, expanded, width, size, line_height) -> block tuple or None
    group_blocks(blocks, expanded, live) -> blocks with consecutive steps folded into work groups
    live_block(controller) -> block tuple: the live row, with a plan's provisional steps and a script being
        written under it
    ROLES: the block roles ``draw`` handles
    draw(role, message, rows, block_height, key, top, bottom, width, size, line_height, state)
Block tuples are chat_view's: (role, message, rows, height, key, bubble_width). While a turn runs, the live row's
icon and the hero block after the turn's request are the marble wait's (``marble_draw``).
"""

import json
import math
import time

from . import activity_text, live_row
from .live_row import code_caption, code_height  # noqa: F401  (the script well's size and caption, re-exported)
from .card_kit import AMBER, AMBER_SOFT, MUTED, TEAL, TEAL_SOFT, TEXT
from .ui_tokens import CORAL, CORAL_SOFT
from .legacy_names import current_tool_name
from .ui_text import iface_, n_, plural_

ROLES = frozenset({"activity", "thinking", "live", "activity_group", "progress_notice", "marble_hero", "worked"})
NEUTRAL_SOFT = (1, 1, 1, 0.07)
DETAIL_FILL = (0.075, 0.082, 0.098, 1)    # The collapsed Details well, one step darker than PANEL.
GUIDE = (1, 1, 1, 0.08)                   # The thread that ties an open group's steps to its header.
TONES = {"teal": (TEAL, TEAL_SOFT), "coral": (CORAL, CORAL_SOFT), "amber": (AMBER, AMBER_SOFT),
         "neutral": (MUTED, NEUTRAL_SOFT)}
ROW = 36          # A step or group row.
PLANNED = 28      # A provisional step under the live row (plan_steps).
LIVE = 44         # The live row itself.
INDENT = 18       # Steps inside an open group.
QUIET = 36        # A quiet notice's one line.
WORKED = 26       # "Worked for 1m 58s" under a finished turn.
_DELEGATE = "angelo_delegate"
_APPROVAL_TOOLS = {"angelo_scene_program", "angelo_apply_recipe", "angelo_expand_recipe", "angelo_accept_preview",
                   "angelo_discard_preview", "angelo_memory_update"}


def _view():
    from . import chat_view
    return chat_view


def _details(message, role, following=()):
    """The exact technical text behind a step (tool, arguments, result): shown only once it is opened.

    A thinking row shows its summary, or what the model's time led to when the runtime sent none
    (``live_progress.thinking_detail``; ``following`` are the rows after it)."""
    if role != "activity":
        from .live_progress import thinking_detail
        return thinking_detail(message, following)
    arguments = message.get("arguments", {})
    tool = current_tool_name(message.get("tool", ""))
    if tool == _DELEGATE:
        result = message.get("result", {})
        detail = iface_("Read-only question") + "\n" + arguments.get("task", "")
        return detail + "\n\n" + (result.get("reply") or result.get("error") or iface_("Waiting for the collaborator…"))
    if tool in _APPROVAL_TOOLS:
        from .agent_workspace_ui import control_lines
        from .approvals import approval_details
        detail = approval_details(tool, arguments)
        if message.get("result"):
            detail += "\n\n" + iface_("Result read back from Angelo:") + "\n" + "\n".join(control_lines(message["result"]))
        return detail
    if tool == "StructuredOutput":  # A plan: its operations, never the reply it carries.
        planned = arguments.get("operations", {key: value for key, value in arguments.items() if key != "reply"})
        return iface_("Planned operations:") + "\n" + json.dumps(planned, indent=2, ensure_ascii=False)[:12000]
    detail = tool + "\n" + (arguments.get("code") or json.dumps(arguments, indent=2, ensure_ascii=False))
    if message.get("result"):
        detail += "\n\n" + iface_("Result:") + "\n" + json.dumps(message["result"], indent=2, ensure_ascii=False)[:12000]
    return detail


def _following(message, count=4):
    """The transcript rows after ``message`` in the drawn chat (what a thinking row led to), or none."""
    from . import ui
    controller = ui._controller
    messages = controller.state["messages"] if controller is not None else ()
    index = next((i for i in range(len(messages) - 1, -1, -1) if messages[i] is message), None)
    return tuple(messages[index + 1:index + 1 + count]) if index is not None else ()


def activity_block(message, role, identity, expanded, width, size, line_height, state):
    from . import chat_cards
    from .studio_chat import has_preview
    view = _view()
    following = _following(message) if role == "thinking" and identity in expanded else ()
    detail = _details(message, role, following) if identity in expanded else ""
    monospace = role == "activity" and current_tool_name(message.get("tool")) != _DELEGATE
    rows = view.wrap(detail, max(40, width - 64 - INDENT), size - 1, view._mono if monospace else view._font) if detail else ()
    preview_height = 196 if has_preview(message) else 0
    card_height = chat_cards.attached_height(message, width - 32, view._flow_wrap, line_height, state)
    if card_height:  # A Generation Flow run or a background job, below its row.
        message = dict(message, _flow_card=card_height)
        preview_height += card_height + 12
    if preview_height:  # A row with its own card or picture is never folded away (group_blocks).
        message = dict(message, _attached=True)
    return (role, message, rows, ROW + len(rows) * line_height + (20 if rows else 0) + preview_height, identity, 0)


_KINDS = {"teal": "success", "amber": "warn", "coral": "error", "neutral": "info"}


def _notice_layout(payload, top, width, scale, draw, ui=None):
    from . import ui_kit_status
    message = payload["message"]
    actions = ((("restore", message["restore_checkpoint"], iface_("Undo restore"), "secondary"),)
               if message.get("restore_checkpoint") else ())
    return ui_kit_status.notice(_view(), 16, top - 6, width - 32, scale, ui, draw, _KINDS[payload["tone"]],
                                payload["headline"], payload["body"], actions, payload["details"], payload["opened"],
                                "notice_details")


def notice_block(message, identity, expanded, width, size, line_height):
    """A host notice as the kit's inline notice, or None when it is not one of progress's notices.

    A quiet notice (a plan that simply completed) is one muted line instead: it repeats after every edit.
    """
    view_text = activity_text.notice_view(message)
    if view_text is None:
        return None
    if message.get("notice") and message.get("job"):  # From the live record while Angelo still has it.
        from .job_chat import progress_text, title
        from .job_manager import shared
        jobs = shared()
        record = jobs.record(message["job"]) if jobs is not None else None
        if record is not None:
            view_text = activity_text.job_view(message, title(record), progress_text(record), record["status"])
    from .console import display_text
    opened = ("notice", identity) in expanded
    payload = dict(view_text, message=message, details=display_text(message), opened=opened, scale=size / 14)
    if view_text.get("quiet"):
        view = _view()
        rows = view.wrap(payload["details"], max(40, width - 64 - INDENT), size - 2, view._mono) if opened else ()
        payload["rows"] = rows
        return ("progress_notice", payload, (), QUIET + (len(rows) * (line_height - 2) + 20 if rows else 0), identity, 0)
    height, _ = _notice_layout(payload, 0, width, payload["scale"], False)
    return ("progress_notice", payload, (), height + 12, identity, 0)


def live_block(controller, width=None, size=None):
    """The live row under the transcript while a turn runs, with the provisional steps of a plan being written.

    Those rows (``plan_steps``) are carried on the status and drawn muted with a "Planned" chip, never a done
    one; the label then drops its names, which the rows show. A script being written shows its newest lines in a
    well under them (``script_overlay``), as tall as ``SHOWN_LINES`` lines from its first line on, so the row
    does not grow line by line. A voice turn shows its own status instead.
    The header is a status line, "• 1m 9s · 562 tokens · Thinking" (``live_row.status_line``, a marble mark, so it
    ticks without a full draw), and, when the turn has gone quiet or stalled, a muted line under it
    (``live_progress``), wrapped to ``width`` at ``size`` when they are given.
    """
    from . import live_progress, plan_progress, plan_steps, script_overlay
    voice = controller.voice and controller.status != "Connecting"
    planned, more = ((), 0) if voice else plan_steps.rows(controller.status)
    script = None if voice else script_overlay.shown(controller)
    facts = live_progress.facts(controller)
    now = time.monotonic()
    label = iface_(controller.voice_status) if voice else live_progress.phase(
        facts._replace(status=plan_progress.split(controller.status)[0]) if planned or script else facts)
    found = None if voice else live_progress.note(facts, now)
    note_rows = ()
    if found:
        view = _view()
        note_rows = tuple(text for text, _ in view.wrap(live_progress.personal(found[1]), max(40, width - 72),
                                                        (size or 14) - 1, view._font)) if width else (found[1],)
    header = LIVE + len(note_rows) * live_row.NOTE_LINE + (4 if note_rows else 0)
    # The steps and the well sit under the header (``_draw_planned``, ``live_row.script_well``).
    height = header + (len(planned) + bool(more)) * PLANNED + (6 if planned else 0) + (code_height() if script else 0)
    return ("live", {"text": live_progress.personal(label or iface_("Thinking")), "planned": tuple(planned),
                     "more": more, "script": script, "header": header, "started": None if voice else facts.started,
                     "note": note_rows, "stalled": bool(found and found[0] == live_progress.STALL)},
            (), height, "live", 0)


def planned_label(kind, name, count):
    """A provisional step's text: the model's own name (data), else its exact action ("Mesh bend", as the
    approval card words it) or, for a tool of its own, the tool's title."""
    if not name:
        name = (activity_text.step_title({"tool": kind}) if kind.startswith("angelo_")
                else kind.replace("_", " ").capitalize())
    return iface_("{name} ×{count}", name=name, count=count) if count > 1 else name


def group_blocks(blocks, expanded, live):
    """Fold each run of two or more consecutive steps into a work group; an open group keeps its steps.

    Thinking rows between steps join the run; a step carrying a card or picture ends it and stays in view. ``live`` says the transcript's last run is still working:
    its newest step stays visible under the group so the person sees what is happening now.
    """
    output, index = [], 0
    while index < len(blocks):
        if blocks[index][0] not in {"activity", "thinking"}:
            output.append(blocks[index])
            index += 1
            continue
        end = index
        while end < len(blocks) and blocks[end][0] in {"activity", "thinking"} and not blocks[end][1].get("_attached"):
            end += 1
        if end == index:  # A step with a card (a job, a flow run, a picture) stands on its own.
            output.append(blocks[index])
            index += 1
            continue
        run = blocks[index:end]
        steps = [block for block in run if block[0] == "activity"]
        if len(steps) < 2:
            output.extend(run)
            index = end
            continue
        running = live and end == len(blocks)
        key = ("group", run[0][4])
        opened = key in expanded
        messages = [block[1] for block in steps]
        header = {"title": activity_text.group_title(messages, running),
                  "status": activity_text.group_status(messages, running), "opened": opened, "live": running}
        output.append(("activity_group", header, (), ROW + 4, key, 0))
        shown = run if opened else ([steps[-1]] if running else [])
        output.extend((role, dict(message, _grouped=True), rows, height, identity, bubble)
                      for role, message, rows, height, identity, bubble in shown)
        index = end
    return worked(output, live)


def worked(blocks, live):
    """``blocks`` with "Worked for 1m 58s" after each finished turn: before the next request, or at the end when no
    turn runs. The time is the controller's, kept on the turn's request row (``worked_ms``); display only."""
    output, pending = [], None
    for block in blocks:
        if block[0] == "user":
            if pending:
                output.append(pending)
            ms = block[1].get("worked_ms")
            pending = (("worked", {"ms": ms}, (), WORKED, ("worked", block[4]), 0)
                       if type(ms) is int and ms >= 0 else None)
        output.append(block)
    if pending and not live:
        output.append(pending)
    return output


# Drawing ---------------------------------------------------------------------------------------------------------

def _fill(x, y, width, height, color, radius=0):
    """A rounded rectangle that blends: blf leaves blending off after every text it draws."""
    import gpu
    gpu.state.blend_set('ALPHA')
    _view().shape(x, y, width, height, color, radius)


def _circle(x, y, radius, color, start=0, sweep=360, thickness=1.2):
    import gpu
    gpu.state.blend_set('ALPHA')
    view, steps = _view(), max(8, int(sweep / 20))
    for i in range(steps):
        a, b = math.radians(start + sweep * i / steps), math.radians(start + sweep * (i + 1) / steps)
        view.stroke(x + radius * math.cos(a), y + radius * math.sin(a), x + radius * math.cos(b),
                    y + radius * math.sin(b), color, thickness)


def _lines(x, y, color, segments, thickness=1.2):
    view = _view()
    for (x1, y1), (x2, y2) in segments:
        view.stroke(x + x1, y + y1, x + x2, y + y2, color, thickness)


def kind_icon(kind, x, y, color=MUTED):
    """A 12 px stroke icon per kind of work, centred on (x, y), in chat_view's stroke style."""
    view = _view()
    if kind in {"read", "look"}:  # An eye.
        _circle(x, y + 4.5, 7, color, 220, 100)
        _circle(x, y - 4.5, 7, color, 40, 100)
        _circle(x, y, 2.2, color)
    elif kind == "search":
        _circle(x - 1.5, y + 1.5, 4.2, color)
        _lines(x, y, color, [((1.6, -1.6), (5.5, -5.5))], 1.6)
    elif kind == "create":
        _circle(x, y, 6, color)
        _lines(x, y, color, [((-3, 0), (3, 0)), ((0, -3), (0, 3))], 1.4)
    elif kind == "edit":
        view.icon("pencil", x, y, color)
    elif kind == "delete":
        _lines(x, y, color, [((-5, 4), (5, 4)), ((-2, 4), (-2, 6)), ((-2, 6), (2, 6)), ((2, 6), (2, 4)),
                             ((-4, 3), (-3, -6)), ((-3, -6), (3, -6)), ((3, -6), (4, 3))])
    elif kind == "material":
        _circle(x, y, 6, color)
        _circle(x - 1.5, y + 1.5, 2.6, color, 100, 120)
    elif kind == "render":
        _lines(x, y, color, [((-6, -4), (6, -4)), ((6, -4), (6, 4)), ((6, 4), (-6, 4)), ((-6, 4), (-6, -4)),
                             ((-2, 4), (-1, 6)), ((-1, 6), (2, 6)), ((2, 6), (3, 4))])
        _circle(x, y, 2.4, color)
    elif kind == "script":
        _lines(x, y, color, [((-3, 4), (-6, 0)), ((-6, 0), (-3, -4)), ((3, 4), (6, 0)), ((6, 0), (3, -4)),
                             ((1, 5), (-1, -5))])
    elif kind == "generate":  # A four-point sparkle.
        _lines(x, y, color, [((0, 6), (1.5, 1.5)), ((1.5, 1.5), (6, 0)), ((6, 0), (1.5, -1.5)), ((1.5, -1.5), (0, -6)),
                             ((0, -6), (-1.5, -1.5)), ((-1.5, -1.5), (-6, 0)), ((-6, 0), (-1.5, 1.5)), ((-1.5, 1.5), (0, 6))])
    elif kind == "file":
        _lines(x, y, color, [((-6, -5), (6, -5)), ((6, -5), (6, 3)), ((6, 3), (0, 3)), ((0, 3), (-2, 5)),
                             ((-2, 5), (-6, 5)), ((-6, 5), (-6, -5))])
    elif kind == "job":
        _circle(x, y, 6, color)
        _lines(x, y, color, [((0, 0), (0, 3.5)), ((0, 0), (2.8, -1.6))])
    elif kind == "consult":
        _lines(x, y, color, [((-6, 5), (6, 5)), ((6, 5), (6, -2)), ((6, -2), (-1, -2)), ((-1, -2), (-4, -5)),
                             ((-4, -5), (-4, -2)), ((-4, -2), (-6, -2)), ((-6, -2), (-6, 5))])
    elif kind == "memory":
        _lines(x, y, color, [((-4, 6), (4, 6)), ((4, 6), (4, -6)), ((4, -6), (0, -2.5)), ((0, -2.5), (-4, -6)),
                             ((-4, -6), (-4, 6))])
    elif kind == "sculpt":
        _circle(x, y, 6, color)
        _circle(x, y, 2.5, color)
    elif kind == "plan":  # A short list.
        for row in (4, 0, -4):
            _fill(x - 6, y + row - 1, 2, 2, color, 1)
            _lines(x, y, color, [((-2, row), (6, row))])
    elif kind == "check":
        _circle(x, y, 6, color)
        view.icon("check", x, y, color)
    else:  # Scene work: a cube.
        _lines(x, y, color, [((0, 6), (5.5, 3)), ((5.5, 3), (5.5, -3)), ((5.5, -3), (0, -6)), ((0, -6), (-5.5, -3)),
                             ((-5.5, -3), (-5.5, 3)), ((-5.5, 3), (0, 6)), ((-5.5, 3), (0, 0)), ((5.5, 3), (0, 0)),
                             ((0, 0), (0, -6))])


def _spinner(x, y, color, radius=4.5):
    _circle(x, y, radius, (color[0], color[1], color[2], 0.25), 0, 360, 1.4)
    _circle(x, y, radius, color, -(time.monotonic() * 300) % 360, 100, 1.6)


_CHIP = {"running": ("running", None), "queued": ("preview", n_("Queued")), "planned": ("preview", n_("Planned")),
         "needs_you": ("needs_you", None), "declined": ("preview", n_("Declined")), "stopped": ("preview", n_("Stopped")),
         "failed": ("failed", None), "reverted": ("needs_you", n_("Reverted")), "preview": ("preview", None),
         "done": ("done", None)}


def chip(right, y, status, scale=1.0):
    """The kit's status chip ending at ``right``, vertically centred on ``y``; returns its width."""
    import gpu
    from . import ui_kit_status
    view = _view()
    kind, label = _CHIP.get(status, ("done", None))
    label = iface_(label) if label else None
    width, height = ui_kit_status.chip_size(view, kind, scale, label)
    gpu.state.blend_set('ALPHA')
    ui_kit_status.draw_chip(view, (right - width, y - height / 2, width, height), scale, None, kind, label,
                            time.monotonic())
    return width


def _title(view, label, x, right, y, size, color):
    view.text(view.elide(label, max(20, right - x), size), x, y, size, color)


def draw(role, message, rows, block_height, key, top, bottom, width, size, line_height, state):
    view = _view()
    if role == "progress_notice":
        return _draw_notice(view, message, key, top, bottom, width, size, line_height, state)
    if role == "worked":
        from .live_progress import elapsed
        _title(view, iface_("Worked for {time}", time=elapsed(message["ms"] / 1000)), 16, width - 16,
               top - WORKED / 2 - (size - 1) * 0.36, size - 1, MUTED)
        return
    if role == "marble_hero":
        from . import marble_draw
        return marble_draw.draw_hero(message, top, bottom, width, state)
    centre = top - ROW / 2
    indent = INDENT if message.get("_grouped") else 0
    if indent:
        _fill(25, bottom, 1, block_height, GUIDE)
    x = 16 + indent
    right = width - 16
    if role == "live":
        from . import marble_draw
        if not marble_draw.draw_icon(x + 8, centre, state):  # The carved raven; a voice turn keeps the spinner.
            _spinner(x + 7, centre, TEAL, 5)
        live_row.status_line(view, message, x + 24, right, centre, size, state)
        live_row.draw_note(view, message, top - LIVE + 6, width, size)
        _draw_planned(view, message, top, bottom, width, size, state)
        if message.get("script"):
            live_row.script_well(view, message, top, bottom, width, size, state)
        return
    if role == "activity_group":
        if message["live"]:
            _spinner(x + 7, centre, TEAL, 5)
        else:
            kind_icon("work", x + 7, centre, MUTED)
        right -= chip(right, centre, message["status"]) + 10
        view.icon("chevron" if message["opened"] else "next", right - 6, centre, MUTED)
        _title(view, message["title"], x + 24, right - 18, centre - size * 0.36, size, TEXT)
        state["hits"].append(((16, top - ROW, width - 32, ROW), "expand", key))
        _note(state, key, role, message["title"], message["status"])
        return
    if role == "activity":
        status = activity_text.step_status(message)
        kind_icon(activity_text.step_kind(message), x + 7, centre,
                  TONES[activity_text.status_tone(status)][0] if status in {"failed", "needs_you"} else MUTED)
        right -= chip(right, centre, status) + 10
        label = activity_text.step_title(message)
        _note(state, key, role, label, status)
    else:  # Thinking: a disclosure, no chip.
        opened = bool(rows)
        view.icon("chevron" if opened else "next", x + 7, centre, MUTED)
        label = (iface_("Thinking") if message.get("status") == "running"
                 else iface_("Thought for {seconds}s", seconds=max(1, round(message.get("duration_ms", 0) / 1000))))
    _title(view, label, x + 24, right, centre - size * 0.36, size, MUTED if role == "thinking" else TEXT)
    state["hits"].append(((16 + indent, top - ROW, width - 32 - indent, ROW), "expand", key))
    _draw_attachments(view, message, rows, block_height, top, bottom, width, size, line_height, state, role, indent)


def _draw_planned(view, message, top, bottom, width, size, state):
    """A plan's provisional steps under the live row: muted, each with a "Planned" chip, tied to the row."""
    planned, more = message.get("planned") or (), message.get("more", 0)
    labels = [planned_label(kind, name, count) for _, kind, name, count in planned]
    _note(state, "live", "live", message["text"], tuple(labels) + ((more,) if more else ()))
    if not planned:
        return
    header = message.get("header", LIVE)
    _fill(25, bottom + 6, 1, top - header + 4 - bottom - 6, GUIDE)
    x, right = 16 + INDENT, width - 16
    for index, ((kin, _, _, _), text) in enumerate(zip(planned, labels)):
        centre = top - header + 4 - PLANNED * index - PLANNED / 2
        kind_icon(kin, x + 7, centre, MUTED)
        end = right - chip(right, centre, "planned", 0.9) - 10
        _title(view, text, x + 24, end, centre - (size - 1) * 0.36, size - 1, MUTED)
    if more:
        centre = top - header + 4 - PLANNED * len(planned) - PLANNED / 2
        _title(view, plural_(more, "{count} more step", "{count} more steps"), x + 24, right,
               centre - (size - 1) * 0.36, size - 1, MUTED)


def _draw_attachments(view, message, rows, block_height, top, bottom, width, size, line_height, state, role, indent):
    from . import chat_cards
    from .studio_chat import has_preview, draw_preview
    preview_height = 196 if has_preview(message) else 0
    if preview_height:
        draw_preview(message, (28 + indent, bottom + 12, min(320, width - 56 - indent), 180))
    if message.get("_flow_card"):  # Below the expandable detail rows, like a Studio preview.
        chat_cards.draw_attached(message, state, 16, bottom + 12 + message["_flow_card"], width - 32, view._flow_wrap,
                                 line_height)
        preview_height += message["_flow_card"] + 12
    if rows:
        _fill(16 + indent + 8, bottom + 12 + preview_height, width - 40 - indent, block_height - ROW - 8 - preview_height,
                   DETAIL_FILL, 8)
        y = top - ROW - 18
        for value, _ in rows:
            view.text(value.rstrip(), 36 + indent, y, size - 1, MUTED, view._mono if role == "activity" else view._font)
            y -= line_height


def _note(state, key, role, title, status):
    """What a progress row last drew, by its key: read by the GUI fixtures, never by the add-on."""
    drawn = state.setdefault("progress_drawn", {})
    if len(drawn) > 512:
        drawn.clear()
    drawn[key] = (role, title, status)


def _draw_notice(view, payload, key, top, bottom, width, size, line_height, state):
    _note(state, key, "progress_notice", payload["headline"], payload["tone"])
    if payload.get("quiet"):
        return _draw_quiet(view, payload, key, top, bottom, width, size, line_height, state)
    import gpu
    gpu.state.blend_set('ALPHA')
    _, hits = _notice_layout(payload, top, width, payload["scale"], True, state)
    for rect, action, value in hits:
        state["hits"].append((rect, "expand", ("notice", key)) if action == "notice_details" else (rect, action, value))


def _draw_quiet(view, payload, key, top, bottom, width, size, line_height, state):
    """One muted line: a teal check, the headline and its note; Details open under it."""
    centre = top - QUIET / 2
    color = TONES[payload["tone"]][0]
    _circle(23, centre, 6, color, thickness=1.3)
    if payload["tone"] == "teal":
        view.icon("check", 23, centre, color)
    else:
        _fill(22.3, centre - 0.5, 1.4, 4, color)
        _fill(22.3, centre - 3.6, 1.4, 1.4, color)
    label = payload["headline"] + (" · " + payload["body"] if payload["body"] else "")
    _title(view, label, 40, width - 40, centre - (size - 1) * 0.36, size - 1, MUTED)
    view.icon("chevron" if payload["opened"] else "next", width - 26, centre, MUTED)
    state["hits"].append(((16, top - QUIET + 2, width - 32, QUIET - 4), "expand", ("notice", key)))
    if payload["rows"]:
        _fill(32, bottom + 6, width - 48, top - QUIET - bottom - 6, DETAIL_FILL, 8)
        y = top - QUIET - 18
        for value, _ in payload["rows"]:
            view.text(value.rstrip(), 44, y, size - 2, MUTED, view._mono)
            y -= line_height - 2
