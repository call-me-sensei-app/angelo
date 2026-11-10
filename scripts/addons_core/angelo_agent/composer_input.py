# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's keys, clicks and drags: between the chat's input handler (``chat_view``) and the editing itself
(``composer_edit``, with the per-platform table ``composer_keys`` and the scroll ``composer_scroll``).

API:
    key(event, state, line, window_manager, scale, platform=None, session=None, controller=None) -> bool   a key press while it edits
    insert(state, line, text, kind=None, session=None, controller=None)     typed text, a newline (Shift+Enter)
    press(state, line, x, y, now, double_time, extend=False) -> int         a click: caret, word, paragraph
    drag(state, line, x, y)                                                 a drag-select; past an edge it scrolls
    end_drag(views)                                                         the button went up

``state`` is the chat's view state (``chat_view._views[area]``), ``line`` its ``ConsoleLine`` (the draft). Pointer
``x`` is unmirrored (the text is a left-to-right field in every language) and ``y`` is in layout units, as drawn.
The selection is ``state["selection"]`` (sorted), the caret is always one of its ends; ``state["composer_upstream"]``
is ``(draft, caret)`` while the caret is upstream (``composer_edit.Draft``), so a draft written elsewhere never
inherits it. ``session`` is the chat's id. The draft's Undo history belongs to the draft itself, its Console line
and chat (``_history``): it moves with the line (maximize, full screen), another chat in the same area never undoes
into this one's text, and two areas showing one chat at once (a split: each Console has its own line) keep their own.
A draft copied to another line (pop-out, dock, the chat toggle, Swap Areas) starts its own, as a draft written
anywhere else does. The composer's scroll goes with the chat (``adopt``).
"""

import time
from collections import OrderedDict

from . import composer_edit, composer_keys, composer_scroll
from .chat_composer import column_at, column_x, cursor_position, set_draft, vertical_caret
from .composer_edit import Draft

AUTOSCROLL_SECONDS = 0.06   # A drag held past an edge scrolls a row this often.
CLICK_SLOP = 4   # Layout units a second click may land from the first and still count as a double-click.
KEPT_CHATS = 64   # Chats' scrolls, and (line, chat) draft Undos, kept at once (the least recent goes first).
SWEEP_S = 0.25   # How often ``sweep`` looks for areas no chat shows now.
_SWEPT = [float("-inf")]   # When it last looked.
_HISTORIES = OrderedDict()   # (draft line pointer, chat id) -> that draft's History: it moves with the line.
_SCROLLS = OrderedDict()   # Chat id -> its composer's scroll keys as last drawn (``adopt``).
SCROLL_KEYS = ("composer_first", "composer_top", "composer_layout", "composer_seen", "composer_caret_shown")
# What belongs to one chat's editing only: dropped when another chat shows in the area.
EDIT_KEYS = ("composer_drag", "composer_kill", "composer_carry", "composer_bar_drag", "composer_clicks",
             "composer_upstream", "composer_goal", "selection")
VERTICAL = frozenset({"row_up", "row_down", "page_up", "page_down"})   # Moves that keep a goal x (``key``).
_FAILED = set()   # EXTERNAL entries that raised (reported once).


def _rows(state, value):
    """The draft's rows for ``value`` now: the composer's own wrapping (``draft_wrap``, kept by its draw), so two keys
    between draws never read rows of an older draft."""
    rewrap = state.get("draft_wrap")
    rows = rewrap(value) if rewrap else state.get("draft_lines")
    return tuple(rows) if rows else ((value, 0),)


def _draft(state, line):
    value, caret = line.body, cursor_position(line)
    selection = state.get("selection")
    upstream = state.get("composer_upstream") == (value, caret)
    if not selection or selection[0] == selection[1] or selection[1] > len(value) or caret not in selection:
        return Draft(value, caret, None, upstream)   # No selection, or one a write elsewhere left (caret moved off it).
    return Draft(value, caret, selection[1] if caret == selection[0] else selection[0], upstream)


def _store(state, line, draft):
    set_draft(line, draft.value, draft.caret)
    selection = draft.selection
    state["selection"] = (max(0, selection[0]), min(len(line.body), selection[1])) if selection else None
    state["composer_upstream"] = (line.body, line.current_character) if draft.upstream else None


def _kept(table, key, make):
    """``table[key]``, made when missing; the most recent ``KEPT_CHATS`` keys stay."""
    if key not in table:
        table[key] = make()
    table.move_to_end(key)
    while len(table) > KEPT_CHATS:
        table.popitem(last=False)
    return table[key]


def _line(line):
    pointer = getattr(line, "as_pointer", None)
    return pointer() if pointer else id(line)


def _drafts():
    """The chat draft line of every Console of every screen, shown or not (``drafts.chat_draft_line``: the native
    agent input, one lookup per space, also while the space shows Python; a stock binary's last chat line, R12-1)."""
    import bpy
    from . import live_windows
    from .drafts import chat_draft_line
    spaces = (space for screen in bpy.data.screens for area in live_windows.screen_areas(screen)
              for space in area.spaces if space.type == 'CONSOLE')
    return {_line(line) for line in map(chat_draft_line, spaces) if line is not None}


def _history(line, value, session):
    """The draft's Undo history: its Console line's in chat ``session``, so it moves with the line (break-it R9-1:
    an area's history was left behind when maximize moved the line, and Cmd/Ctrl+Z then undid the scene) and a
    split's two lines keep their own (R7-2). A line has only its own: a draft copied to a new line, or written over
    an old one, starts a new history, as any draft written outside the composer does, and Cmd/Ctrl+Z is Blender's
    until it is edited. Rounds 8-10 handed a history on to an equal draft on another line, and every rule for which
    one (the last edited, one out of view, the newest equal one) gave some draft the text of another (R8-3, R9-L1,
    R10-M1) or lost it to a prune (R11-H1)."""
    key = (_line(line), session)
    history = _HISTORIES.get(key)
    if history is None:
        history = _HISTORIES[key] = composer_edit.History()
    _kept(_HISTORIES, key, composer_edit.History)
    history.sync(value)   # A draft written elsewhere (sent, a voice transcript, restored) starts a new history.
    return history


def adopt(state, session):
    """Chat ``session`` shows in this area (``state``) now: the composer's scroll is that chat's as it was last
    drawn in any area (``keep``), and nothing of another showing's editing (a selection, a drag, kill text, a caret
    affinity) carries over. Called by the draw before it lays the rows out, so a chat's first frame shows its own
    rows. An area that becomes a chat again (``editor_types.select_console``: Return to Dock reuses the docked area,
    whose state ``chat_view._views`` keeps) forgets its session first (``rejoin``), so it takes the chat as the
    pop-out left it, never its own older rows (break-it R5-1); two areas showing one chat at once keep their own."""
    if state.get("composer_session") == session:
        return
    for name in SCROLL_KEYS + EDIT_KEYS:
        state.pop(name, None)
    state.update(_SCROLLS.get(session, {}))
    state["composer_session"] = session


def keep(state, session):
    """After the draw laid the rows out: the chat's scroll as this area shows it, for the next area to show it."""
    _kept(_SCROLLS, session, dict).update({name: state[name] for name in SCROLL_KEYS if name in state})


def rejoin(state):
    """The area stopped showing a chat, or shows one again (``editor_types.select_console``, ``sweep``): whatever it
    shows next is adopted afresh, its scroll the chat's as last drawn anywhere."""
    if state is not None:
        state.pop("composer_session", None)


def sweep(views, window_manager, now=None):
    """From the add-on's timer (``ui``), at most every ``SWEEP_S``: an area that shows no chat now (the native
    editor-type picker, ``editor_types.restore_previous_editor``, a workspace tab hidden, an area joined away) is
    rejoined and its picker closed, so when a chat shows there again it never brings back its own older rows or
    editing (break-it R7-3). Areas showing the chat now keep theirs, however many. Showing a chat again takes a
    person's action, so a quarter second never shows; the timer itself may tick every millisecond (R8-5). Not while
    a window is between screens (R9-L2): its chat may still show."""
    now = time.monotonic() if now is None else now
    from . import console, live_windows
    if now - _SWEPT[0] < SWEEP_S or not live_windows.settled(window_manager):
        return
    _SWEPT[0] = now
    shown = {area.as_pointer() for window in live_windows.every_window(window_manager)
             for area in live_windows.screen_areas(window.screen) if console.is_chat(area)}
    for pointer, state in views.items():
        if pointer not in shown and state.get("composer_session") is not None:
            rejoin(state)
            state.pop("popover", None)
    if _HISTORIES:   # Only drafts' histories stay (R10-2: a freed line's address came back with its old Undo).
        drafts = _drafts()
        for key in [key for key in _HISTORIES if key[0] not in drafts]:
            del _HISTORIES[key]


def takes_off_chat(event, platform=None):
    """Whether the focused chat acts on a key pressed with the pointer over another editor (``chat_view``'s modal
    takes it there as over the chat): Return, Escape and the field's keys whatever is held (``input_policy`` and
    ``composer_keys`` FIELD_KEYS), a key of its table or of another module's entry (``composer_keys.lookup``,
    ``composer_keys.bound``), Tab's focus move (``canvas_controls.key``), or a key that types (``typed``,
    ``dead_key``). A live view hands exactly these on to the chat and holds the rest (``session_rail_input``;
    break-it R6-3: Option and AltGr characters were dropped there, R6-4: Ctrl+digit was passed on, R7-1: only keys
    with text were asked about, so Cmd+Z, PageUp and the Emacs keys never reached the draft)."""
    from .input_policy import FIELD_KEYS
    tab = event.type == 'TAB' and not (event.ctrl or event.oskey or event.alt)
    return (tab or event.type in FIELD_KEYS or event.type in composer_keys.FIELD_KEYS
            or composer_keys.lookup(event, platform) is not None or composer_keys.bound(event)
            or typed(event, platform) or dead_key(event, platform))


def forget():
    """The add-on unregisters: no chat's Undo or scroll outlives it."""
    _HISTORIES.clear()
    _SCROLLS.clear()
    _FAILED.clear()


def _commit(state, line, history, before, edit):
    _store(state, line, edit.draft)
    history.record(before, Draft(line.body, cursor_position(line)), edit.kind)
    return True


def typed(event, platform=None):
    """Whether a key press types its character: printable, with no Cmd or Ctrl. On macOS Option types characters on
    most layouts ("@" is Option+L on a German Mac); on Windows and Linux Alt is a shortcut and AltGr types (Windows
    reports it as Ctrl+Alt; Linux's AltGr is the third level, ISO_Level3_Shift, which GHOST reports as no modifier:
    ``GHOST_SystemX11`` reads only Alt_L and Alt_R). 1.0.9 left Option characters to the Console keymap under the
    chat, which inserted them past the selection and the draft's Undo."""
    text = event.unicode
    if not text or not text.isprintable() or event.oskey:
        return False
    if composer_keys.platform_of(platform) == composer_keys.MAC:
        return not event.ctrl
    return event.ctrl == event.alt   # Neither, or both (AltGr on Windows).


def dead_key(event, platform=None):
    """A macOS dead key (Option+E, U, I, N, `): it starts an accent and types nothing yet. A text field keeps it, so
    it never reaches the editor under the pointer as a shortcut (Option+E would open Extrude)."""
    return (composer_keys.platform_of(platform) == composer_keys.MAC and event.alt and not (event.ctrl or event.oskey)
            and not event.unicode and (len(event.type) == 1 or event.type == 'ACCENT_GRAVE'))


def _insert_edit(state, draft, text, kind=None, controller=None):
    if controller is None:
        return composer_edit.insert(draft, text, kind)
    from .draft_overflow import edit
    return edit(controller, state, draft, text, kind)


def insert(state, line, text, kind=None, session=None, controller=None):
    """``text`` over the selection or at the caret, as one Undo step (a typed character joins its run)."""
    draft = _draft(state, line)
    history = _history(line, draft.value, session)
    return _commit(state, line, history, draft, _insert_edit(state, draft, text, kind, controller))


def external_function(name):
    """The add-on's function ``name`` ("module.function") for a ``composer_keys.EXTERNAL`` entry
    (``test_native_composer_field_keys`` resolves every entry)."""
    import importlib
    module, function = name.rsplit(".", 1)
    return getattr(importlib.import_module("." + module, __package__), function)


def _run_external(name, line, session):
    """An ``EXTERNAL`` entry's answer; one that fails leaves the key to the table (never to the Console under the
    chat) and says why once in the console."""
    try:
        return bool(external_function(name)(line, session))
    except Exception:
        if name not in _FAILED:
            _FAILED.add(name)
            import traceback
            traceback.print_exc()
        return False


def key(event, state, line, window_manager, scale=1.0, platform=None, session=None, controller=None):
    """One key press while the composer edits. True when it was the composer's; False passes it on (an unlisted
    key, or Undo with no draft history: the scene's Undo, as before)."""
    platform = composer_keys.platform_of(platform)
    draft = _draft(state, line)
    if any(_run_external(name, line, session) for name in composer_keys.external(event, draft)):
        return True   # Another module's entry took the key (composer_keys.EXTERNAL).
    found = composer_keys.lookup(event, platform)
    history = _history(line, draft.value, session)
    if found is None:
        if typed(event, platform):
            return _commit(state, line, history, draft, _insert_edit(state, draft, event.unicode, controller=controller))
        return dead_key(event, platform)
    action, extend = found
    if action in {"undo", "redo"}:
        if not history.active:
            return False   # The draft has nothing of its own to undo: the key is Blender's (the scene's Undo).
        restored = history.step(draft, action == "undo")
        if restored is not None:   # With its selection: Undo after typing over one selects the text it brings back.
            _store(state, line, restored)
            state["composer_goal"] = None
        return True   # At the end of the draft's history the key stops there, never undoing the scene.
    rows = _rows(state, draft.value)
    size, drawn = state.get("font_size", 14), state.get("text_scale", scale)
    goal = state.get("composer_goal")
    goal = [goal[2] if goal and goal[:2] == (draft.value, draft.caret) else None]   # Up and Down keep their x.

    def vertical(caret, up, current):
        if goal[0] is None:
            goal[0] = column_x(rows[current][0], caret - rows[current][1], size, drawn)
        return vertical_caret(rows, caret, up, size, drawn, current, goal[0])
    if controller is not None and action in {"paste", "yank"}:
        value = window_manager.clipboard if action == "paste" else state.get("composer_kill", "")
        if action == "paste":
            from . import clipboard_image
            if clipboard_image.paste(controller, value):
                return True   # A picture attachment leaves the text and its Undo history alone.
        value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        edit = _insert_edit(state, draft, value, "paste", controller) if value else composer_edit.Edit(draft)
    else:
        edit = composer_edit.apply(action, extend, draft, rows, vertical, platform,
                                   clipboard=window_manager.clipboard if action == "paste" else "",
                                   kill=state.get("composer_kill", ""))
    if edit.clipboard is not None:
        window_manager.clipboard = edit.clipboard
    if edit.kill is not None:
        state["composer_kill"] = edit.kill
    if edit.scroll:
        composer_scroll.scroll_to(state, edit.scroll)
        return True
    _commit(state, line, history, draft, edit)
    state["composer_goal"] = (line.body, cursor_position(line), goal[0]) if action in VERTICAL else None
    return True


# -- the pointer ----------------------------------------------------------------------------------------------------

def caret_at(state, value, x, y):
    """``(caret, upstream)`` under a pointer over the draft: its row as scrolled (``composer_scroll.row_at``), its
    column as drawn. A row wrapped at a space ends before it, as Up and Down do; past the end of a row wrapped inside
    a word the caret is upstream there. None before any draw."""
    if not state.get("composer_view"):
        return None
    rows = _rows(state, value)
    index = min(composer_scroll.row_at(state, y), len(rows) - 1)
    text, offset = rows[index]
    column = column_at(text, x, state.get("font_size", 14), state.get("text_scale"))
    if composer_edit.breaks_at_space(rows, index):
        column = min(column, len(text) - 1)
    return offset + column, composer_edit.at_row_end(rows, index, offset + column)


def glyph_at(state, value, x, y):
    """The index of the character under a pointer over the draft (a double-click selects its word), or None before
    any draw. Past a row's end, its last character, not the space or line end it breaks at."""
    if not state.get("composer_view") or not value:
        return None
    rows = _rows(state, value)
    index = min(composer_scroll.row_at(state, y), len(rows) - 1)
    text, offset = rows[index]
    size, scale = state.get("font_size", 14), state.get("text_scale")
    if not text:   # An empty line: the line end there.
        return min(offset, len(value) - 1)
    column = column_at(text, x, size, scale)
    past = column == len(text)
    if column > 0 and (past or x < column_x(text, column, size, scale)):
        column -= 1   # The nearest boundary is this glyph's right edge: the glyph is the one before it.
    if past and column > 0 and composer_edit.breaks_at_space(rows, index):
        column -= 1
    return offset + column


def double_click_seconds(context):
    """The person's double-click time (Preferences > Input), which a triple-click uses between clicks too."""
    try:
        return context.preferences.inputs.mouse_double_click_time / 1000
    except AttributeError:
        return 0.35   # Blender's default, where the context has no preferences (a test's stand-in).


def _unit(value, caret, count):
    if count == 2:
        return composer_edit.word_at(value, caret if caret < len(value) else caret - 1)
    if count >= 3:
        return composer_edit.paragraph_at(value, caret)
    return caret, caret


def press(state, line, x, y, now, double_time=0.35, extend=False):
    """A click in the draft: the caret there; a double-click selects its word, a triple-click its paragraph;
    Shift extends the selection. Returns the caret."""
    value = line.body
    caret, upstream = caret_at(state, value, x, y) or (cursor_position(line), False)
    last = state.get("composer_clicks")
    count = 1
    if (last and not extend and now - last[0] <= double_time and abs(last[1] - x) <= CLICK_SLOP
            and abs(last[2] - y) <= CLICK_SLOP):
        count = min(3, last[3] + 1)
    state["composer_clicks"] = (now, x, y, count)
    if extend:
        anchor = _draft(state, line).anchor
        anchor = cursor_position(line) if anchor is None else anchor
        state["composer_drag"] = {"unit": (anchor, anchor), "count": 1}
        _store(state, line, Draft(value, caret, anchor, upstream))
        return caret
    glyph = glyph_at(state, value, x, y) if count == 2 else None
    unit = composer_edit.word_at(value, glyph) if glyph is not None else _unit(value, caret, count)
    state["composer_drag"] = {"unit": unit, "count": count}
    _store(state, line, Draft(value, unit[1], unit[0], upstream and count == 1))
    return line.current_character


def _extend(state, line, x, y):
    info = state["composer_drag"]
    value = line.body
    pointed = caret_at(state, value, x, y)
    if pointed is None:
        return
    caret, upstream = pointed
    start, end = info["unit"]
    if info["count"] == 1:
        _store(state, line, Draft(value, caret, start, upstream))
    elif caret < start:   # By words or paragraphs: the whole unit under the pointer joins the first one.
        _store(state, line, Draft(value, _unit(value, caret, info["count"])[0], end))
    else:
        reach = _unit(value, max(start, caret - 1) if info["count"] == 2 else caret, info["count"])[1]
        _store(state, line, Draft(value, max(end, reach), start))


def drag(state, line, x, y, area_pointer=None):
    """A drag from a click in the draft. Past the shown rows' top or bottom the view scrolls a row at once and
    keeps scrolling while the button stays down there (``AUTOSCROLL_SECONDS``), the selection following."""
    info = state.get("composer_drag")
    if not info:
        return
    info["pointer"] = (x, y)
    if composer_scroll.drag_edge(state, y) and composer_scroll.scrolls(state) and not info.get("scrolling"):
        composer_scroll.step(state, composer_scroll.drag_edge(state, y))
        if area_pointer is not None:
            info["scrolling"] = True
            _start_autoscroll(area_pointer, info)
    _extend(state, line, x, y)


def end_drag(views):
    """The button went up, wherever it is: every drag in the chat's views ends (a drag-select, the composer's bar,
    the transcript's bar), so none sticks when the release lands outside the chat. True when a bar drag ended."""
    import time
    ended = False
    for state in views.values():
        state.pop("composer_drag", None)
        bar = state.pop("composer_bar_drag", None) is not None
        if bar:
            composer_scroll.shown(state)
        if state.pop("scroll_drag", None) is not None:
            from . import chat_scrollbar
            state["scroll_time"] = time.monotonic()
            chat_scrollbar._fade_later()
            bar = True
        if bar:   # The bar's look changes: a kept frame (marble_frames) may not be reused.
            state["input_serial"] = state.get("input_serial", 0) + 1
            ended = True
    return ended


def _start_autoscroll(area_pointer, drag_info):
    """One timer per drag: it ends with its own drag (``drag_info``), never runs on into the next one's."""
    import bpy

    def tick():
        from . import chat_view, console, live_windows
        state = chat_view._views.get(area_pointer, {})
        info = state.get("composer_drag")
        area = next((a for _, a in live_windows.window_areas(bpy.context.window_manager)
                     if a.as_pointer() == area_pointer and console.is_chat(a)), None)
        edge = composer_scroll.drag_edge(state, info["pointer"][1]) if info is drag_info and "pointer" in info else 0
        if not edge or area is None or not area.spaces.active.history:
            drag_info["scrolling"] = False
            return None
        composer_scroll.step(state, edge)
        _extend(state, area.spaces.active.history[-1], *info["pointer"])
        area.tag_redraw()
        return AUTOSCROLL_SECONDS
    bpy.app.timers.register(tick, first_interval=AUTOSCROLL_SECONDS)
