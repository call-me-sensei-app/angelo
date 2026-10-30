# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session rail: one tile per concurrent session down the left of the full-screen view (or across its top or bottom,
``session_rail_strip``), the main one first.

Evidence: the maintainer (2026-10-24): "thumbnail of each concurrent session on the left and the active one is the
one showing on full screen. And these thumbnail are screenshot of current state so user can follow along." Each
tile shows the session's current picture (``rail_pictures``), its name, its live status line (the chat's own live
row: phase, clock, current step) and a pending approval; the active session's tile is ringed in teal. A click makes
that session the active one: its chat in the chat area and its scene full screen (``switch``).

This module is pure layout and tile states (host-tested with plain values); ``session_rail_draw`` draws a
``Layout`` with the card_kit palette, ``session_rail_input`` takes its clicks. Sizes are pixels at a UI scale of 1,
multiplied by ``scale``; coordinates are region pixels, y up.
"""

from dataclasses import dataclass, field

WIDTH = 188            # Tile width.
MARGIN = 12            # From the view's edges and from a toolbar drawn over it.
PAD = 8                # Inside the rail panel.
GAP = 8                # Between tiles.
PICTURE_RATIO = 0.625  # Picture height per width (16:10, the pictures' own shape: rail_pictures.SIZE).
MIN_PICTURE = 48       # Below this a tile drops its picture and shows its lines only (a short view).
TEXT_BLOCK = 64        # Name, status and counts lines under the picture, and the progress bar under them.
HEADER = 26            # The rail's own label row.
INSET = 6              # The picture inside its tile.
MORE = HEADER          # A column's "+N more" row, the header's height (a strip's cell is as wide as its words).
CONTROL = 20           # A header button: the paged arrows, minimize.
MINIMIZE_KEY = ("minimize",)   # What session_rail_draw.hover holds over the minimize button (its hit's value: None).

# Tile kinds, in the order a status shows them: what the session needs from the artist comes first.
APPROVAL, MERGING, WORKING, ENDED, FAILED, IDLE = "approval", "merging", "working", "ended", "failed", "idle"
MERGED, DONE, REPLIED = "merged", "done", "replied"
QUEUED, STARTING = "queued", "starting"   # A part waiting for a free place, or its worker starting (``worker_queue``).
KINDS = (APPROVAL, MERGING, MERGED, WORKING, FAILED, ENDED, DONE, REPLIED, STARTING, QUEUED, IDLE)
CLOSABLE = frozenset({ENDED, MERGED})   # Always closable; any tile whose worker ended or merged is too (Tile.closable).


@dataclass
class Tile:
    """One session's tile as drawn: what it says and where. ``picture`` is a region rect or None."""

    session: str
    title: str
    role: str                  # "Main" or the worker's short label.
    status: str                # The live status line, already translated (``ui_text``).
    kind: str = IDLE
    active: bool = False
    rect: tuple = (0, 0, 0, 0)
    picture: tuple = None
    title_at: tuple = (0, 0)
    status_at: tuple = (0, 0)
    detail_at: tuple = (0, 0)  # The counts line ("3 of 7 steps · 43% · 1m 9s"), under the status line.
    badge: tuple = None        # The pending-approval pill, when one is pending.
    close: tuple = None        # An ended or merged worker's dismiss button (its chat and part file stay).
    progress: tuple = None     # (steps done, steps planned) of the worker's own plan, when it gave one.
    bar: tuple = None          # Where the progress bar goes, when there is progress.
    status_head: str = ""      # What the session does (the step it is on): the status line when there are counts.
    status_keep: str = ""      # Its counts and clock, on a line of their own (break-it P1-8 P8: one line held only
                               # about 30 characters, so a step title hid them).
    step: str = ""             # The step its worker is on: committed, unlike a live label streamed while it thinks.
    closable: bool = False     # Its worker has ended or merged, whatever the tile says (a crash that failed its turn).


@dataclass
class Layout:
    panel: tuple = (0, 0, 0, 0)
    header_at: tuple = (0, 0)
    tiles: list = field(default_factory=list)
    hits: list = field(default_factory=list)   # [(rect, action, value)] as every Angelo canvas exposes them.
    scale: float = 1.0
    compact: bool = False
    paged: bool = False                 # More tiles than fit, even compact: the main tile, then a window of workers.
    first: int = 0                      # The first worker shown (paged).
    hidden: tuple = (0, 0)              # Workers hidden above and below the window.
    attention: tuple = (False, False)   # A hidden worker waits for approval above, below.
    arrows: tuple = None                # (up, down) rects in the header, paged; (left, right) on a strip.
    orientation: str = "VERTICAL"       # The column, or "HORIZONTAL": a strip across the top or bottom.
    minimize: tuple = None              # The header's minimize button: ("minimize", None), hit before every tile.
    more: tuple = (None, None)          # "+N more" lines before and after the shown workers, where any are hidden.
    page: int = 0                       # Workers a paged rail shows at once: a "+N more" click moves that many.
    header_end: float = 0.0             # Where the header's buttons start: its session count ends before them.
    step: float = TEXT_BLOCK + GAP      # Interface pixels (scale 1) per session along the rail: a swipe's unit.


def layout(states, size, scale, left_inset=0, top_inset=0, bottom_inset=0, first=0, keep=None, right_inset=0):
    """Where every tile goes in a region of ``size`` (width, height). ``states``: ``Tile``s without rects, in rail
    order, the main session first. The rail is as tall as its tiles; pictures shrink to fit a short view, then go
    (compact); when even compact tiles would leave the view (many workers, a short window, a large interface scale),
    the rail pages: the main tile stays, then as many workers as fit from the ``first``, moved so the worker at
    ``keep`` (an index among the workers) shows, with arrows in the header and a "+N more" line where workers are
    hidden (``page``; audit P2: tiles ran off the view, unreachable). ``left_inset`` and ``top_inset``: pixels at the
    region's top the rail stays below (the headers drawn over the view, its text info); ``bottom_inset`` and
    ``right_inset``: pixels at its bottom and right other regions cover (a shelf, the sidebar). A view that cannot hold
    the least rail at ``scale`` gets it at the largest smaller scale that does (``fit``): the panel never leaves the
    view (Windows T4 at 3x, 2026-10-25: a paged column ran 8 px off a 502 px view)."""
    width, height = size
    count = len(states)
    result = Layout(scale=scale)
    if not count:
        return result
    s = fit(scale, (height - top_inset - bottom_inset, width - left_inset - right_inset), least(count))
    if s <= 0:
        return result
    result.scale = s
    tile_w = WIDTH * s
    x = left_inset + MARGIN * s
    room = height - top_inset - bottom_inset - 2 * MARGIN * s - 2 * PAD * s - HEADER * s
    available = room - (count - 1) * GAP * s
    picture_h = min((tile_w - 2 * INSET * s) * PICTURE_RATIO, available / count - TEXT_BLOCK * s - INSET * s)
    compact = picture_h < MIN_PICTURE * s
    pinned, shown, lines = states, [], (False, False)
    if compact:
        picture_h = 0.0
        if count > 1 and count * TEXT_BLOCK * s > available:
            pinned, shown, lines = page(states, room, (TEXT_BLOCK + GAP) * s, GAP * s, (MORE + GAP) * s, first, keep,
                                        result)
    tile_h = TEXT_BLOCK * s + (picture_h + INSET * s if not compact else 0.0)
    items = sequence(pinned, shown, lines)
    extents = [tile_h if kind == "tile" else MORE * s for kind, _value in items]
    panel_h = 2 * PAD * s + HEADER * s + sum(extents) + (len(items) - 1) * GAP * s
    top = height - top_inset - MARGIN * s
    result.panel = (x - PAD * s, top - panel_h, tile_w + 2 * PAD * s, panel_h)
    result.header_at = (x + 2 * s, top - PAD * s - 17 * s)
    result.compact = compact
    controls_at(result, x + tile_w, top - PAD * s - HEADER * s + 3 * s, s)   # Before the tiles': hit() takes the first.
    y = top - PAD * s - HEADER * s
    for (kind, value), extent in zip(items, extents):
        if kind == "tile":
            place_tile(result, value, (x, y - extent, tile_w, extent), None if compact else picture_h, s)
        else:
            more_at(result, value, (x, y - extent, tile_w, extent))
        y -= extent + GAP * s
    return result


def place_tile(result, state, rect, picture_h, s):
    """``state``'s tile at ``rect`` (any orientation): its picture ``picture_h`` tall (None: compact), its lines, its
    approval badge, progress bar and dismiss button; its hits go after those before it, its dismiss before its own."""
    x, y, tile_w, tile_h = rect
    tile = Tile(**{**state.__dict__, "rect": rect})
    top = y + tile_h
    if picture_h is not None:
        tile.picture = (x + INSET * s, top - INSET * s - picture_h, tile_w - 2 * INSET * s, picture_h)
    text_top = y + TEXT_BLOCK * s
    tile.title_at = (x + 10 * s, text_top - 19 * s)
    tile.status_at = (x + 22 * s, text_top - 37 * s)
    tile.detail_at = (x + 22 * s, text_top - 52 * s)
    if tile.progress is not None:
        tile.bar = (x + 10 * s, y + 5 * s, tile_w - 20 * s, 3 * s)
    if tile.kind == APPROVAL and tile.picture is not None:
        px, py, pw, ph = tile.picture
        tile.badge = (px + 6 * s, py + ph - 26 * s, min(pw - 12 * s, 96 * s), 20 * s)
    if tile.kind in CLOSABLE or tile.closable:
        size = 20 * s
        tile.close = (x + tile_w - size - 6 * s, top - size - 6 * s, size, size)
        result.hits.append((tile.close, "dismiss", tile.session))   # Before the tile's own: hit() takes the first.
    result.tiles.append(tile)
    result.hits.append((rect, "switch", tile.session))
    return tile


def more_at(result, side, rect):
    """The "+N more" line for the workers hidden on ``side`` (0 before the shown ones, 1 after) at ``rect``: a click
    on it moves a page that way (the maintainer, 2026-10-25: "a clear '+N more' line at the panel's edge")."""
    result.more = tuple(rect if index == side else old for index, old in enumerate(result.more))
    result.hits.append((rect, "page", (-1, 1)[side] * max(1, result.page)))   # Its own hover, apart from an arrow's.


def sequence(pinned, shown, lines):
    """A rail's items in order: ``("tile", state)`` for the pinned tiles, ``("more", 0)`` for the workers hidden
    before the shown ones, the shown ones, ``("more", 1)`` for those hidden after (``lines``: which sides have one)."""
    return ([("tile", state) for state in pinned] + ([("more", 0)] if lines[0] else [])
            + [("tile", state) for state in shown] + ([("more", 1)] if lines[1] else []))


def controls_at(result, right, y, s):
    """The header's buttons, ``CONTROL`` square on row ``y`` and ending at ``right``: minimize last, a paged rail's two
    arrows before it, each hit before every tile (``hit`` takes the first); ``header_end`` is where they start."""
    size, gap = CONTROL * s, 4 * s
    result.minimize = (right - size, y, size, size)
    result.hits.append((result.minimize, "minimize", None))
    end = right - size - gap
    if result.paged:
        result.arrows = ((end - 2 * size - gap, y, size, size), (end - size, y, size, size))
        result.hits += [(result.arrows[0], "scroll", -1), (result.arrows[1], "scroll", 1)]
        end -= 2 * size + 2 * gap
    result.header_end = end


def fit(scale, room, least):
    """The scale a rail is laid out at: the person's ``scale``, or the largest smaller one at which its least panel
    (``least``: (height, width) at a scale of 1, its margins in) fits ``room`` (height, width) pixels; 0 when there is
    no room. Every size of a rail is its scale times a constant, so the least panel then fits exactly (pure)."""
    return max(0.0, min([scale] + [have / need for have, need in zip(room, least) if need > 0]))


def least(count):
    """The least column of ``count`` sessions, (height, width) at a scale of 1, margins in: one compact tile, and with
    more than one session room for the main tile and a worker (Windows T4 at 2x: fitted to one tile and its lines,
    it paged over the main tile), or for a "+N more" line on either side, whichever is more."""
    along = max(TEXT_BLOCK + 2 * (MORE + GAP), 2 * TEXT_BLOCK + GAP) if count > 1 else TEXT_BLOCK
    return 2 * MARGIN + 2 * PAD + HEADER + along, 2 * MARGIN + WIDTH


def top_inset(regions, region):
    """Pixels at the top of ``region`` that regions aligned to the top cover (region overlap: the header and the tool
    header with the active tool's settings, stacked); the rail starts below them. Headers flipped to the bottom, the
    toolbar and the sidebar cover nothing here (break-it P1-9 Q1: telling headers by their shape put the rail below
    the view). Regions are anything with type/alignment/x/y/width/height."""
    top = 0
    for other in regions:
        if other is region or getattr(other, "alignment", "") != 'TOP' or other.width <= 1 or other.height <= 1:
            continue
        if (other.x < region.x + region.width and other.x + other.width > region.x
                and other.y < region.y + region.height and other.y + other.height > region.y):
            top = max(top, region.y + region.height - other.y)
    return top


def bottom_inset(regions, region):
    """Pixels at the bottom of ``region`` that regions aligned to the bottom cover, stacked (an asset shelf and the tab
    strip above it, a header flipped to the bottom): the rail's tiles stay above them (break-it P1-16 L1). Regions are
    anything with type/alignment/x/y/width/height (pure)."""
    bottom = 0
    for other in regions:
        if other is region or getattr(other, "alignment", "") != 'BOTTOM' or other.width <= 1 or other.height <= 1:
            continue
        if (other.x < region.x + region.width and other.x + other.width > region.x
                and other.y < region.y + region.height and other.y + other.height > region.y):
            bottom = max(bottom, other.y + other.height - region.y)
    return bottom


def text_inset(height, text_end):
    """Pixels from the top of a 3D view's region of ``height`` down to the lowest point of its own text info, so the
    rail starts below whatever Blender drew there: the view's name or FPS, the active object, the grid unit, the render
    engine's info, Performance, Statistics, as the person's preferences and the view show them. ``text_end`` is
    Blender's own answer (``_bpy._angelo_live.text_info_end``: region pixels from the bottom, the visible top when it
    drew none, so it is below the headers over the view too); None before the view's first draw: 0. Break-it P1-13
    U1/U2: a reserve counted in Python missed lines Blender draws and counted the display's density twice (pure)."""
    return 0 if text_end is None else max(0, height - text_end)


def steady_inset(kept, settings, inset, held):
    """``(settings, inset)`` to keep for a view whose text info reaches ``inset`` down this frame. Its text changes
    with each frame's content (a selection's statistics, an axis view's grid unit, a shader compile), and a rail that
    followed it jumped and dropped a click between press and release (break-it P1-14 V1). So the rail keeps the
    deepest text since the view's text ``settings`` last changed: down at once for more text and up only when the
    settings change, and not at all while ``held`` (the pointer on a tile, a press pending: P1-15 L2), which the next
    frame without it catches up on. Content that shrinks for good (a compile line gone) leaves a gap below the text
    until the settings change: never text behind the rail (pure)."""
    if kept is None:
        return settings, inset
    if held:
        return kept
    return (settings, inset) if kept[0] != settings else (settings, max(kept[1], inset))


def fit_below_text(headers, text, count, height, scale, bottom=0):
    """The rail's top inset: below the headers over the view and its text info, but never so low that its tiles,
    compact, leave a view of ``height`` whose bottom ``bottom`` pixels another region covers (an asset shelf): then the
    rail covers the text's last lines rather than put tiles out of reach (break-it P1-14 V5, P1-15 L4). A rail of more
    than two tiles can page (``layout``), so it needs room for the main tile, one worker and their "+N more" lines
    only (pure)."""
    s = scale
    lines = 2 * (MORE + GAP) if count > 2 else 0
    count = min(count, 2)
    compact = 2 * PAD * s + HEADER * s + count * TEXT_BLOCK * s + (count - 1) * GAP * s + lines * s
    return max(headers, min(text, height - bottom - 2 * MARGIN * s - compact))


def ordered(sessions, is_worker, number):
    """``sessions`` as the rail stands them: the lead first, then the workers by their launch numbers (``number(s)``;
    they join in the order they become ready, so Worker 2 could stand above Worker 1, break-it P1-11 S7). A stable
    sort keeps the join order where numbers tie (pure)."""
    workers = sorted((s for s in sessions if is_worker(s)), key=number)
    return [s for s in sessions if not is_worker(s)] + workers


def page(states, room, unit, gap, line, first, keep, result):
    """``(pinned, shown, lines)`` of a paged rail in ``room`` pixels along it (a column's height, a strip's width):
    ``unit`` per tile with its gap, ``line`` per "+N more" line with its gap. The main tile stays (``pinned``) while
    two tiles fit with both lines (decided once, so it never comes and goes as the rail scrolls), else while two fit
    without them, the lines then gone and the header's arrows paging (Windows T4 at 2x); then as many workers as fit
    from ``first``, moved so the worker at ``keep`` (an index among the workers) shows. A rail with room for one tile
    only pages over every session, the main one too (break-it P1-18 L2). Each side that hides tiles keeps room for
    its line (``lines``: before, after); three rounds settle it. ``result`` learns what is hidden before and after,
    whether a hidden worker there waits for approval and the page a line's click moves (pure)."""
    both, bare = (max(1, int((room + gap - lines) / unit + 1e-6)) for lines in (2 * line, 0))   # Two fit in two.
    if both < 2 <= bare:   # The main tile before the "+N more" lines.
        both, line = bare, None
    pinned = states[:1] if both >= 2 else []
    pool = states[len(pinned):]
    sides = (False, False)
    for _round in range(3):
        slots = max(1, int((room + gap - (sides[0] + sides[1]) * (line or 0)) / unit + 1e-6))
        shown = max(1, min(len(pool), slots - 1)) if pinned else 1   # Unpinned: one session at a time.
        at = first
        if keep is not None:
            kept = keep + (0 if pinned else 1)
            at = min(max(at, kept - shown + 1), kept)
        at = max(0, min(at, len(pool) - shown))
        wanted = (sides[0] or at > 0, sides[1] or at + shown < len(pool))
        if wanted == sides:
            break
        sides = wanted
    above, below = pool[:at], pool[at + shown:]
    result.paged, result.first, result.hidden = True, at, (len(above), len(below))
    result.page = max(1, min(shown, both - len(pinned)))
    result.attention = (any(t.kind == APPROVAL for t in above), any(t.kind == APPROVAL for t in below))
    return pinned, pool[at:at + shown], (bool(above) and line is not None, bool(below) and line is not None)


def hover_key(found):
    """What ``session_rail_draw.hover`` holds for the hit ``found``: its value (a session, an arrow's step), the hit
    itself for a "+N more" line (a page of one is an arrow's step too, and only the one under the pointer lifts), or
    ``MINIMIZE_KEY`` for the minimize button, whose value is None as nothing under the pointer is (pure)."""
    if found is None:
        return None
    return MINIMIZE_KEY if found[0] == "minimize" else tuple(found) if found[0] == "page" else found[1]


def control_keys(result):
    """The hover keys of a rail's own buttons (its arrows, "+N more" lines and minimize): the pointer on one holds the
    rail still, as on a tile (``session_rail_draw``); before any layout, the arrows' and minimize's."""
    if result is None:
        return (-1, 1, MINIMIZE_KEY)
    steps = sorted({value for _rect, action, value in result.hits if action == "scroll"})
    pages = sorted({(action, value) for _rect, action, value in result.hits if action == "page"})
    return tuple(steps) + tuple(pages) + (MINIMIZE_KEY,)


NO_REPLY = "(No reply of its own: its turn ended; its part holds what it built, unchecked.)"  # i18n: lead model reads
STOPPED = "(Its turn was stopped before it finished.)"  # i18n: the lead model reads it, never a person
NOT_SENT = "(Its brief never reached its agent, so nothing ran: start the part again.)"  # i18n: lead model reads
TURN_FAILED = "(Its turn failed: %s)"  # i18n: the lead model reads it, never a person (never FAILED: a tile kind)
COMPLETED = frozenset({"completed", "escalated"})   # A provider's finished statuses that completed the turn.
PROBLEM_LIMIT = 600   # Characters of a failure's own row, so the lead's status keeps "(Its turn failed:".


def turn_end(messages, working=False, outcome=None, reply=True):
    """``(end, reply)`` of a chat's newest turn: the rows after its newest brief (a ``user`` row that is not a
    preview's Accept) up to where it finished. ``outcome`` is the controller's ``turn_outcome``, recorded once as the
    turn ended (its provider's status, a person's Stop, a brief never sent, its row count), never live fields that later
    actions rewrite (break-it P1-20 M2). ``end`` is "" while it works or before that brief's turn ended, else
    "completed", "stopped" (the person's Stop, or its worker's end: ``worker_routing.end_turn``), "not_sent", or
    "failed" for anything else, a provider's failure "interrupted" included (P1-20 M1). A completed turn's reply is its
    newest assistant text with the host's verified account of what ran after it, the account alone, or ``NO_REPLY``; a
    failed one's is its error row, else its last system row before it finished (P1-20 M3). ``reply`` False skips
    building the text (the rail's draw) (pure)."""
    rows = list(messages or ())
    brief = next((index for index in range(len(rows) - 1, -1, -1) if rows[index].get("role") == "user"
                  and rows[index].get("request_kind") != "native_preview_accept"), None)
    if working or brief is None or outcome is None or outcome[3] <= brief:   # Not ended since this brief.
        return "", ""
    status, stopped, not_sent, finished = outcome
    turn = rows[brief + 1:finished]
    if not_sent:
        return "not_sent", NOT_SENT
    if stopped:
        return "stopped", STOPPED
    if status not in COMPLETED:
        if not reply:
            return "failed", ""
        texts = [(m.get("role"), (m.get("model_text") or m.get("text") or "").strip())   # The lead is a model.
                 for m in turn if not m.get("host_summary")]
        problem = next((text for role, text in reversed(texts) if role == "error" and text), "") or next(
            (text for role, text in reversed(texts) if role == "system" and text), "")
        return "failed", TURN_FAILED % ((problem or str(status))[:PROBLEM_LIMIT])
    if not reply:
        return "completed", ""
    said = next((index for index in range(len(turn) - 1, -1, -1) if turn[index].get("role") == "assistant"
                 and (turn[index].get("text") or "").strip()), None)
    after = turn[said + 1:] if said is not None else turn   # The host's account of what ran after the reply.
    account = next(((m.get("text") or "").strip() for m in reversed(after) if m.get("role") == "system"
                    and m.get("host_summary") is True and (m.get("text") or "").strip()), "")
    account = "\n".join(line for line in account.splitlines() if not line.lstrip().startswith("- "))
    text = "\n\n".join(part for part in ((turn[said]["text"].strip() if said is not None else ""), account) if part)
    return "completed", text or NO_REPLY


def keep_in_view(active, active_id, shown, paged):
    """The worker index a paged rail brings into view: the active worker after a switch to it, or as the rail starts
    to page; None while the artist's own scroll should stay (break-it P1-18 L1) (pure)."""
    return active if active_id is not None and (active_id != shown or not paged) else None


def pan_steps(so_far, travel, unit):
    """``(steps, remainder)``: trackpad travel added to what has not yet made a step (a change of direction starts
    again), a step per ``unit``, as Blender turns a pan into list scrolling (``ui_pan_to_scroll``) (pure)."""
    so_far = travel if so_far * travel < 0 else so_far + travel
    steps = int(so_far / unit)
    return steps, so_far - steps * unit


def hit(result, x, y):
    """The ``(action, value)`` under region pixel (x, y), or None."""
    for (rx, ry, rw, rh), action, value in result.hits:
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return action, value
    return None


def covers(result, x, y):
    """Whether region pixel (x, y) is on the rail at all (its panel): no click there reaches the view behind."""
    px, py, pw, ph = result.panel
    return pw > 0 and px <= x < px + pw and py <= y < py + ph


def fraction(progress):
    """The share of a worker's plan done, 0-1, from ``(done, planned)``; None without a plan."""
    if not progress or progress[1] <= 0:
        return None
    return max(0.0, min(1.0, progress[0] / progress[1]))


def progress_text(progress):
    """"3 of 7 steps · 43%": the worker's own plan, counted as it reports each step."""
    from .ui_text import iface_, plural_
    share = fraction(progress)
    if share is None:
        return ""
    done, planned = progress
    return iface_("{steps} · {percent}%", steps=plural_(planned, "{done} of {count} step", "{done} of {count} steps",
                                                         done=min(done, planned)), percent=round(share * 100))


def status(kind, live_label="", clock="", step="", progress=None):
    """A tile's status line, translated at display time, from the chat's live row (``live_progress``): what the
    session is doing and for how long, and how far its own plan is. ``live_label``, ``clock`` and ``step`` arrive
    already as display text."""
    from .ui_text import join
    return join([part for part in status_parts(kind, live_label, clock, step, progress) if part])


def status_parts(kind, live_label="", clock="", step="", progress=None):
    """``(head, keep)`` of a tile's status line: what the session does, and the counts and clock that a narrow tile
    keeps whole while it cuts the head ("" when there are none)."""
    from .ui_text import iface_, join
    if kind == WORKING:
        # The step it is on first, as it starts ("Carving the mane"), then how far its plan is and for how long.
        return (step or live_label or iface_("Working", "Angelo"),
                join([part for part in (progress_text(progress), clock) if part]))
    return _state_words(kind), ""


def _state_words(kind):
    from .ui_text import iface_
    if kind == APPROVAL:
        return iface_("Waiting for your approval")
    if kind == MERGING:
        return iface_("Merging into your scene")
    if kind == MERGED:
        return iface_("Merged into your scene")
    if kind == DONE:
        return iface_("Done, waiting for merge")
    if kind == REPLIED:
        return iface_("Finished its turn")
    if kind == ENDED:
        return iface_("Ended", "Angelo")
    if kind == FAILED:
        return iface_("Stopped with an error")
    if kind == QUEUED:
        return iface_("Waiting for a free place")
    if kind == STARTING:
        return iface_("Starting", "Angelo")
    return iface_("Idle", "Angelo")


def queue_text(place):
    """A queued part's counts line: how many running workers must be done before it starts (its place in line)."""
    from .ui_text import plural_
    if not place:
        return ""
    return plural_(place, "Starts once {count} running worker is done", "Starts once {count} running workers are done")


def tile_kind(busy, approval, ended, failed, merging=False, merged=False, done=False, replied=False):
    """The one state a tile shows: an approval first, then merging, merged (the part is in the scene whatever came
    before), working, failed, ended, done (every step of its own plan reported done), replied (its turn ended
    otherwise: a question, a part it could not build, a stop), idle."""
    if approval:
        return APPROVAL
    if merging:
        return MERGING
    if merged:
        return MERGED
    if busy:
        return WORKING
    if failed:
        return FAILED
    if ended:
        return ENDED
    if done:
        return DONE
    if replied:
        return REPLIED
    return IDLE
