# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded display pages independent of durable and provider transcripts.

An absent start follows the newest rows; older pages keep their absolute index
as messages arrive. Overlapping pages preserve a visible boundary row.
"""
from .ui_text import n_

FIRST, EARLIER, LATER, LATEST = n_("First"), n_("Earlier"), n_("Later"), n_("Latest")

PAGE_SIZE = 160
STEP = 120
BAR_HEIGHT = 30


def page(messages, state, session):
    if state.get("history_session") != session:
        state.update(history_session=session, history_start=None)
        state.pop("history_transition", None)
        state.pop("history_offsets", None)
        state.pop("history_scroll", None)
    latest = max(0, len(messages) - PAGE_SIZE)
    start = state.get("history_start")
    previous = state.get("history_range", (0, 0, 0))
    if (not state.get("history_transition") and state.get("history_scroll", 0) == 0
            and previous[1] == previous[2]):
        start = state["history_start"] = None
    if start is None and state.get("history_scroll", 0) > 0:
        start = state.get("history_range", (latest,))[0]
        state["history_start"] = start
    start = latest if start is None else max(0, min(start, latest))
    end = min(len(messages), start + PAGE_SIZE)
    state["history_range"] = (start, end, len(messages))
    state["history_before"] = start > 0 and _step_or_hidden(messages[start - 1])
    state["history_after"] = end < len(messages) and _step_or_hidden(messages[end])
    return messages[start:end]


def move(state, direction):
    start, end, count = state.get("history_range", (0, 0, 0))
    latest = max(0, count - PAGE_SIZE)
    target = {"first": 0, "earlier": max(0, start - STEP),
              "later": min(latest, start + STEP), "latest": latest}[direction]
    state["history_start"] = None if target == latest else target
    state["history_transition"] = direction
    state.pop("scroll_drag", None)


def offsets(blocks):
    position = sum(block[3] for block in blocks)
    result = {}
    for block in blocks:
        result[block[4]] = position
        position -= block[3]
    return result


def scroll(state, blocks, value, maximum, visible=None):
    current = offsets(blocks)
    direction = state.pop("history_transition", None)
    if direction in {"first", "latest"}:
        value = maximum if direction == "first" else 0
    elif direction or value > 0:
        previous = state.get("history_offsets", {})
        shared = current.keys() & previous.keys()
        if shared:
            # The shared row nearest the old viewport's top keeps its position.
            key = min(shared, key=lambda key: abs(previous[key] - value - state.get("history_visible", 0)))
            value += current[key] - previous[key]
            if visible is not None:
                value += state.get("history_visible", visible) - visible
        elif direction:  # Group boundaries/hidden rows need not share a visible block.
            value = 0 if direction == "earlier" else maximum
    state["history_offsets"] = current  # At most one bounded page; discarded with its editor.
    state["history_scroll"] = max(0, min(value, maximum))
    return state["history_scroll"]


def height(state):
    return BAR_HEIGHT if state.get("history_range", (0, 0, 0))[2] > PAGE_SIZE else 0


def draw(view, state, width, top):
    if not height(state):
        return
    from .ui_text import iface_
    start, end, count = state["history_range"]
    y = top - BAR_HEIGHT
    view.shape(0, y, width, BAR_HEIGHT, view.BG)
    labels = ((FIRST, "first", start > 0 or state.get("history_scroll", 0) < state.get("max_scroll", 0)), (EARLIER, "earlier", start > 0),
              (LATER, "later", end < count), (LATEST, "latest", end < count or state.get("history_scroll", 0) > 0))
    cell = width / 4
    for index, (label, direction, enabled) in enumerate(labels):
        x = index * cell
        view.text(iface_(label), x + 8, y + 10, 11, view.TEXT if enabled else view.MUTED)
        if enabled:
            state["hits"].append(((x, y, cell, BAR_HEIGHT), "history", direction))


def scroll_key(area, session):
    """The page and its pixel offset belong to the same editor, never another window."""
    return (area.as_pointer(), session)


def prepare_scroll(state, scrolls, area, session):
    """Read input's actual editor offset before selecting its page."""
    key = scroll_key(area, session)
    state["history_scroll"] = scrolls.get(key, 0)
    return key



def prune(views, scrolls, live_areas):
    for pointer in tuple(views):
        if pointer not in live_areas:
            views.pop(pointer)
    for key in tuple(scrolls):
        if not isinstance(key, tuple) or key[0] not in live_areas or views.get(key[0], {}).get("session") != key[1]:
            scrolls.pop(key)


def _step_or_hidden(message):
    return isinstance(message, dict) and (message.get("role") in {"activity", "thinking"}
                                         or message.get("withdrawn") is True)


def group_blocks(blocks, expanded, busy, state):
    """A page fragment never claims a whole-run total or another page's live tail."""
    from . import chat_rows
    from .ui_text import iface_
    start, end, count = state.get("history_range", (0, 0, 0))
    members = _group_members(blocks)
    previous = state.get("history_group_members", {})
    if state.get("history_group_session") != state.get("history_session"):
        expanded.difference_update(previous)
        previous = {}
    state["history_group_session"] = state.get("history_session")
    for key, identities in members.items():
        if any(old_key in expanded and identities.intersection(old_ids) for old_key, old_ids in previous.items()):
            expanded.add(key)
    expanded.difference_update(previous.keys() - members.keys())
    state["history_group_members"] = members
    grouped = chat_rows.group_blocks(blocks, expanded, busy and end == count)
    groups = [index for index, block in enumerate(grouped) if block[0] == "activity_group"]
    fragments = set()
    if groups and blocks:
        if state.get("history_before") and blocks[0][0] in {"activity", "thinking"}:
            fragments.add(groups[0])
        if state.get("history_after") and blocks[-1][0] in {"activity", "thinking"}:
            fragments.add(groups[-1])
    for index in fragments:
        role, header, rows, height, key, width = grouped[index]
        grouped[index] = (role, dict(header, title=iface_("More steps")), rows, height, key, width)
    state["history_fragments"] = len(fragments)
    return grouped


def _group_members(blocks):
    """Only this page's contiguous runs, matching chat_rows grouping boundaries."""
    members, run = {}, []
    def finish():
        if sum(block[0] == "activity" for block in run) >= 2:
            members[("group", run[0][4])] = frozenset(block[4] for block in run)
        run.clear()
    for block in blocks:
        if block[0] in {"activity", "thinking"} and not block[1].get("_attached"):
            run.append(block)
        else:
            finish()
    finish()
    return members
