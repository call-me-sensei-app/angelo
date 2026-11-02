# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu (the launcher, 1.0.8) as the GUI suites drive it: reads of a chat's canvas state, no Blender.

The menu's top level is a grid of tiles that never scrolls; a group (Generation Flows, the base meshes, Video, Render,
the engines) opens as a page of its own in the same popup, with a Back button, and only a page's rows may scroll;
nothing is remembered between openings. A suite reaches a row inside a group as a person does: it clicks the group's
tile, then, once the page is drawn, the row, wheel-scrolling the page while the row is below its fold. ``step`` says
which of those comes next from the last draw; each suite sends the click or wheel with its own input
(``event_simulate``, ``qa_input.press`` or the chat operator's modal), so one plan serves every suite. ``fold`` is the
launcher's promise as a record: every top-level feature has a hit wholly inside the popup, which fits its region and
does not scroll.

``view`` is a chat's canvas state (``chat_view._views[area]``): ``popover`` (kind ``"add"``: ``page``, ``query``,
``rows``, ``scroll``, ``layout`` with ``rect`` and ``max_scroll``), ``popup_hits`` [(rect, action, value)] and
``popup_rect``, all in region pixels, y up. Other popovers (the model list) have no pages: ``step`` treats them as a
plain list, so a suite uses one click helper for every popup.
"""

import time

KIND = "add"
GROUP, BACK = "popup_add_group", "popup_add_back"
SETTLE = 1.5   # Seconds a sent tile or Back click has to draw its page before it is sent again.
_sent = {}     # ("open" | "back", group) -> when it was sent.


def popup(view):
    return (view or {}).get("popover") or {}


def is_open(view):
    return popup(view).get("kind") == KIND


def page(view):
    """The group page the menu is on (``popover["page"]``), or None at the top level."""
    return popup(view).get("page") or None


def hits(view):
    return list((view or {}).get("popup_hits") or ())


def _matcher(matches):
    if matches is None:
        return lambda value: True
    if callable(matches):
        return matches
    return lambda value: value == matches


def hit(view, action, matches=None):
    """The rect of the drawn popup hit (action, value) whose value ``matches`` (a value or a test), else None."""
    test = _matcher(matches)
    return next((rect for rect, found, value in hits(view) if found == action and test(value)), None)


def drawn_page(view):
    """The page the last draw showed (the group whose Back it registered), else None: the top level or search."""
    return next((value for _, found, value in hits(view) if found == BACK), None)


def at_top(view):
    """The menu is open and its last draw was the top level: no page, no search, the tiles drawn."""
    return (is_open(view) and page(view) is None and not popup(view).get("query") and drawn_page(view) is None
            and any(found == GROUP for _, found, _ in hits(view)))


def on_page(view, group):
    """The menu is on ``group``'s page and its last draw showed it."""
    return is_open(view) and page(view) == group and drawn_page(view) == group


def tiles(view):
    """The (action, value) of every hit the last draw registered, in order: what a person can click."""
    return [(found, value) for _, found, value in hits(view)]


def group_of(matches):
    """The built-in group whose page lists the feature ``matches`` names (``chat_destinations.PARENTS``), else None."""
    if not isinstance(matches, str):
        return None
    from angelo_agent import chat_destinations
    return chat_destinations.PARENTS.get(matches)


def _recent(key, now):
    return now - _sent.get(key, -SETTLE) < SETTLE


def _send(key, now, kind, rect):
    if rect is None:
        return "missing", None
    _sent[key] = now
    return kind, rect


def step(view, action=None, matches=None, group=None, now=None):
    """A person's next step towards the popup row (``action``, ``matches``), or towards ``group``'s page with no
    ``action``. ``group`` is the page the row is on (default: the feature's built-in group, ``group_of``; None for a
    top-level row). Returns (kind, rect):

    ``"click"`` the row's rect; ``"there"`` (no ``action``: the page is drawn); ``"open"`` the group's tile and
    ``"back"`` another page's Back button, to click; ``"scroll"`` / ``"scroll_up"`` the popup's rect, to wheel down or up
    over (up when the row is listed above what is drawn); ``"wait"`` while
    the last draw is not yet the one a sent click asked for; ``"missing"`` when the menu does not offer it, even
    scrolled to its end."""
    now = time.monotonic() if now is None else now
    if not hits(view):
        return "wait", None
    menu = is_open(view)
    group = group if group is not None else (group_of(matches) if menu else None)
    shown = drawn_page(view) if menu else None
    _sent.pop(("open", shown), None)   # What was clicked is drawn: a later click may be sent again.
    for key in [key for key in _sent if key[0] == "back" and key[1] != shown]:
        _sent.pop(key)
    if menu and shown is not None and shown != group:   # Another page (or a top-level row asked for): Back first.
        if _recent(("back", shown), now):
            return "wait", None
        return _send(("back", shown), now, "back", hit(view, BACK, shown))
    if menu and group and shown != group:
        if page(view) == group or _recent(("open", group), now):   # Clicked: its page is not drawn yet.
            return "wait", None
        return _send(("open", group), now, "open", hit(view, GROUP, group))
    if action is None:
        return "there", None
    rect = hit(view, action, matches)
    if rect is not None:
        return "click", rect
    state, geometry = popup(view), popup(view).get("layout") or {}
    box = geometry.get("rect") or (view or {}).get("popup_rect")
    # Above what is drawn (a page opens with its chosen row centred): wheel up; else down while the list goes on.
    rows, test = state.get("rows") or [], _matcher(matches)
    target = next((i for i, row in enumerate(rows) if row.get("action") == action and test(row.get("value"))), None)
    drawn = [i for i, row in enumerate(rows) if row.get("action") and hit(view, row["action"], row.get("value"))]
    if target is not None and drawn and target < min(drawn) and state.get("scroll", 0) > 0:
        return "scroll_up", box
    if state.get("scroll", 0) < geometry.get("max_scroll", 0):
        return "scroll", box
    return "missing", None


def miss(view, action, matches=None, group=None):
    """What a suite records when ``step`` says missing: what the menu offered, where, and how far it scrolled."""
    state = popup(view)
    return {"action": action, "matches": repr(matches), "group": group, "page": page(view),
            "drawn_page": drawn_page(view), "offered": [[found, repr(value)] for found, value in tiles(view)],
            "rows": [repr(row.get("value")) for row in state.get("rows") or ()], "scroll": state.get("scroll"),
            "max_scroll": (state.get("layout") or {}).get("max_scroll")}


def reach(view_fn, act, action=None, matches=None, group=None, tries=40):
    """``step`` until the row is clicked (or the page is drawn), for a suite whose input and redraw are synchronous:
    ``act(kind, rect)`` sends the click ("click", "open", "back") or one wheel step ("scroll" down, "scroll_up") and
    redraws, and does
    nothing but redraw on "wait". Returns the kinds taken; raises with what the menu offered when it is missing."""
    taken = []
    for _ in range(tries):
        kind, rect = step(view_fn(), action, matches, group)
        taken.append(kind)
        if kind == "missing":
            raise AssertionError("the + menu offers no %s %r: %r" % (action or "page", matches if action else group,
                                                                    miss(view_fn(), action, matches, group)))
        if kind == "there":
            return taken
        act(kind, rect)
        if kind == "click":
            return taken
    raise AssertionError("the + menu never reached %s %r in %d steps: %r" % (action or "page", matches or group, tries,
                                                                           taken))


def inside(rect, box, slack=0.5):
    """``rect`` (x, y, w, h) is non-empty and lies wholly inside ``box``, to ``slack`` px."""
    x, y, w, h = rect
    bx, by, bw, bh = box
    return (w > 0 and h > 0 and x >= bx - slack and y >= by - slack and x + w <= bx + bw + slack
            and y + h <= by + bh + slack)


def fold(view, region_size):
    """Every feature in ``chat_destinations.TOP_LEVEL`` in view at once, as the last draw left the menu: a hit with its
    entry's action and the feature's id lying wholly inside the popup, the popup wholly inside its region
    (``region_size``: width, height) and its layout's ``max_scroll`` 0 (nothing below a fold), on the top level. A
    record for the suite's JSON; ``ok`` is the verdict. A feature the menu does not list (unregistered, or offered
    under another action) is ``missing``."""
    from angelo_agent import chat_destinations
    state, box = popup(view), (view or {}).get("popup_rect")
    geometry = state.get("layout") or {}
    offered = {entry["value"]: entry["action"] for entry in chat_destinations.entries()}
    features, missing, outside = {}, [], []
    for feature_id in chat_destinations.TOP_LEVEL:
        action = offered.get(feature_id)
        rect = hit(view, action, feature_id) if action else None
        within = bool(rect and box and inside(rect, box))
        features[feature_id] = {"action": action, "rect": [round(v, 1) for v in rect] if rect else None,
                                "inside": within}
        if rect is None:
            missing.append(feature_id)
        elif not within:
            outside.append(feature_id)
    width, height = region_size
    fits = bool(box) and inside(box, (0, 0, width, height))
    try:
        from angelo_agent import chat_add_layout
        two_columns = chat_add_layout.columns(width) == 2
    except (ImportError, AttributeError):
        two_columns = None
    record = {"region": [int(width), int(height)], "popup_rect": [round(v, 1) for v in box] if box else None,
              "popup_inside_region": fits, "two_columns": two_columns, "page": page(view),
              "drawn_page": drawn_page(view), "query": state.get("query") or "", "scroll": state.get("scroll", 0),
              "max_scroll": geometry.get("max_scroll"), "features": features, "missing": missing, "outside": outside}
    record["ok"] = (at_top(view) and fits and geometry.get("max_scroll") == 0 and not missing and not outside
                    and two_columns is not False)
    return record


def recorded_draw(redraw):
    """Run ``redraw()`` and return every text the + menu drew: (text, rect, clip), rect in the canvas's units (x, the
    baseline's descent below to the cap height above) and clip the scissor rect in force while the test was on, else
    None. Records only while ``chat_add_menu.draw`` runs, so the chat behind the menu is not counted."""
    import gpu
    from angelo_agent import chat_add_menu, chat_view
    records, live = [], {"on": False, "clip": None, "drawing": False}
    text, scissor_set, test_set, draw = chat_view.text, gpu.state.scissor_set, gpu.state.scissor_test_set, \
        chat_add_menu.draw

    def recording_text(value, x, y, size=14, *args, **kwargs):
        if live["drawing"] and str(value).strip():
            width = chat_view.measure(str(value), size)
            records.append((str(value), (x, y - 0.25 * size, width, 1.05 * size), live["clip"] if live["on"] else None))
        return text(value, x, y, size, *args, **kwargs)

    def recording_scissor(x, y, w, h):
        scale = chat_view._scale or 1.0
        live["clip"] = (x / scale, y / scale, w / scale, h / scale)
        return scissor_set(x, y, w, h)

    def recording_test(on):
        live["on"] = bool(on)
        return test_set(on)

    def recording_draw(*args, **kwargs):
        live["drawing"] = True
        try:
            return draw(*args, **kwargs)
        finally:
            live["drawing"] = False
    chat_view.text, chat_add_menu.draw = recording_text, recording_draw
    gpu.state.scissor_set, gpu.state.scissor_test_set = recording_scissor, recording_test
    try:
        redraw()
    finally:
        chat_view.text, chat_add_menu.draw = text, draw
        gpu.state.scissor_set, gpu.state.scissor_test_set = scissor_set, test_set
    return records


def _overlap(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, top = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x, y, right - x, top - y) if right > x and top > y else None


def crossings(view, records, slack=1.0):
    """Texts whose visible part (their rect, clipped while a clip was on) is not inside one band of the menu: the bar,
    a page's header (Back and its pinned toggles), the list, the strip or the footer's links. A row drawn over the
    header or the hint crosses a band; one clipped to its list does not. Returns [(text, visible rect)]."""
    geometry = popup(view).get("layout") or {}
    x, y, w, h = geometry["rect"]
    lx, ly, lw, lh = geometry["list_rect"]
    area = geometry.get("area") or geometry["list_rect"]
    sx, sy, sw, sh = geometry["strip"]
    search = geometry["search"]
    bands = [(search[1], search[1] + search[3]),            # the bar
             (area[1] + area[3], ly + lh),                  # a page's header (empty on the top level)
             (area[1], area[1] + area[3]),                  # the list
             (sy, sy + sh),                                 # the strip
             (y, sy)]                                       # the footer's links
    found = []
    for value, rect, clip in records:
        visible = _overlap(rect, clip) if clip else rect
        if visible is None or _overlap(visible, (x, y, w, h)) is None:
            continue
        bottom, top = visible[1], visible[1] + visible[3]
        if not any(low - slack <= bottom and top <= high + slack for low, high in bands if high > low):
            found.append((value, [round(v, 1) for v in visible]))
    return found


def recorded_shapes(redraw):
    """Run ``redraw()`` and return every filled shape and stroke the + menu drew, in order: (kind, rect, colour), rect
    the shape's box (a stroke's, widened by its thickness). Records only while ``chat_add_menu.draw`` runs."""
    from angelo_agent import chat_add_menu, chat_view
    shapes, live = [], {"drawing": False}
    shape, stroke, draw = chat_view.shape, chat_view.stroke, chat_add_menu.draw

    def recording_shape(x, y, width, height, color, radius=0):
        if live["drawing"]:
            shapes.append(("shape", (x, y, width, height), tuple(color)))
        return shape(x, y, width, height, color, radius)

    def recording_stroke(x1, y1, x2, y2, color=chat_view.MUTED, thickness=1.2):
        if live["drawing"]:
            half = thickness / 2
            shapes.append(("stroke", (min(x1, x2) - half, min(y1, y2) - half, abs(x2 - x1) + thickness,
                                      abs(y2 - y1) + thickness), tuple(color)))
        return stroke(x1, y1, x2, y2, color, thickness)

    def recording_draw(*args, **kwargs):
        live["drawing"] = True
        try:
            return draw(*args, **kwargs)
        finally:
            live["drawing"] = False
    chat_view.shape, chat_view.stroke, chat_add_menu.draw = recording_shape, recording_stroke, recording_draw
    try:
        redraw()
    finally:
        chat_view.shape, chat_view.stroke, chat_add_menu.draw = shape, stroke, draw
    return shapes


def _contains(outer, inner, slack=0.5):
    return (outer[0] - slack <= inner[0] and outer[1] - slack <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2] + slack and inner[1] + inner[3] <= outer[1] + outer[3] + slack)


def invisible(shapes, ground, threshold=0.03):
    """Shapes and strokes that cannot be seen: each is composited (its alpha) over the colour beneath it, the last
    earlier shape whose box holds it (else ``ground``), and is invisible when the result differs from that colour by
    less than ``threshold`` in every channel: a cut-out drawn in translucent teal over a teal rim, a teal stroke on a
    teal disc (1.0.8: web, link, feedback and bake maps glyphs read as solid blocks). Returns [(kind, rect, colour)]."""
    seen, found = [], []
    for kind, rect, color in shapes:
        if rect[2] <= 0 or rect[3] <= 0:
            continue
        # What lies under it: for a shape, the last earlier shape holding all of it; for a stroke, the one holding its
        # middle (a stroke may cross a rim into its opening, as a globe's meridians do, and is seen there).
        probe = rect if kind == "shape" else (rect[0] + rect[2] / 2, rect[1] + rect[3] / 2, 0, 0)
        beneath = next((composite for box, composite in reversed(seen) if _contains(box, probe)), tuple(ground[:3]))
        alpha = color[3] if len(color) > 3 else 1.0
        result = tuple(c * alpha + b * (1 - alpha) for c, b in zip(color[:3], beneath))
        if kind == "shape":
            seen.append((rect, result))
        if rect[2] * rect[3] >= 4 and max(abs(r - b) for r, b in zip(result, beneath)) < threshold:
            found.append((kind, [round(v, 1) for v in rect], [round(c, 3) for c in color]))
    return found


def overflowing(view, records, slack=1.0):
    """Texts whose drawn extent (before any clip) runs past the popup's sides: a clip would hide the cut silently."""
    x, _, w, _ = popup(view).get("layout", {}).get("rect") or (view or {}).get("popup_rect") or (0, 0, 0, 0)
    return [(value, [round(v, 1) for v in rect]) for value, rect, _ in records
            if rect[0] < x - slack or rect[0] + rect[2] > x + w + slack]
