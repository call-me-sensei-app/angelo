# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background canvases never preempt a native modal tool or its mouse grab, and no Angelo modal handler keeps what
is not its input (timers, window events, Blender's hover refresh, the quit key)."""

# The not-connected showcase takes input only over its own 3D view, so the chat beside it stays usable.
CANVAS_INPUTS = frozenset({"angelo.chat_input", "angelo.studio_input", "angelo.connect_hero",
                           "angelo.chat_launcher_input",   # Angelo in the corner: presses on its own disc only.
                           "angelo.flow_window_input",     # A flow window's bar: presses on the bar only.
                           "angelo.asset_browser_input",   # The Asset Browser's pills: presses on them only.
                           "angelo.asset_shelf_add_input",   # Generate on a shelf: presses on its own pill only.
                           "angelo.library_input",   # The library's pill and panel: presses and keys while focused.
                           "angelo.session_rail_input",   # The session rail and a live view (session_rail_input).
                           "angelo.render_region_input"})   # The render window's region pills: presses on them only.
# Angelo's own overlays that take only the events over themselves, pass every other one on and never hold a pointer
# grab: the welcome screen, notice cards (corner and modal, over the 3D view) and the AI Providers page. A canvas
# beside one stays usable (``canvas_blocking``), and the agent's tools that draw offscreen or change no layout
# (view captures, RENDERED looks, motion strips, sculpt, program jobs, the marble wait) never wait for them
# (``tool_running``). Layout changes and Blender's own window screenshot (``view_editor``, whose pixels would
# hold the overlay) still wait for them (``interaction_active``). Before 1.0.2 the chat counted them as a tool in progress: on a first
# launch, with the welcome or a What's New card open, the drawn chat passed every click and key on and did nothing.
PASSIVE_OVERLAYS = frozenset({"angelo.intro", "angelo.card_host", "angelo.providers_page_input",
                              "angelo.bake_maps_note",   # A texture set's progress report: one timer tick, nothing kept.
                              "angelo.web_export",   # Waiting for the World's picture: its timer, and Esc to cancel.
                              "angelo.markup_viewer"})   # A picture marked up over the 3D view (markup_overlay).
# Angelo's own modal operators that hold the window's input on purpose: an approval waits for its card's buttons,
# Return or Esc, and the chat must not take those keys. Every Angelo modal operator is in exactly one of
# CANVAS_INPUTS, PASSIVE_OVERLAYS and HOLDING_TOOLS (test_native_chat_start_states), so a new one is classified
# when it is written, never found later by a dead chat.
HOLDING_TOOLS = frozenset({"angelo.accept_native_preview",
                           "angelo.flow_menu",   # Director's Flows popup / Add step, while open: rows, arrows, Esc.
                           "angelo.render_region_draw",   # Dragging a region to render: holds the pointer until done.
                           "angelo.shape_cut",   # Drawing a Shape Cut: holds the pointer until it commits or cancels.
                           # UV Layout by hand: its mesh is mid-layout in Edit Mode; Esc stops, navigation passes.
                           "angelo.uv_layout_run"})

# Blender's auto-save waits while any modal operator runs, and the chat's input handler runs in every window that
# shows the chat: before 1.0.6 Angelo never auto-saved with the chat open. The canvases and overlays above only
# watch events and never leave data half-changed between them, so they carry the native MODAL_PASSIVE option
# (OPTYPE_MODAL_PASSIVE), which auto-save ignores; a real tool and HOLDING_TOOLS still make it wait.
PASSIVE_MODAL_OPTION = "MODAL_PASSIVE"


def _native_passive_option():
    """``{"MODAL_PASSIVE"}`` where Blender has the option, else empty: an add-on loaded on an older build (a test
    overlay on the 1.0.5 app) registers as before instead of failing on an unknown option."""
    try:
        import bpy
        items = bpy.types.Operator.bl_rna.properties["bl_options"].enum_items.keys()
    except (ImportError, AttributeError, KeyError, TypeError):
        return frozenset()
    return frozenset({PASSIVE_MODAL_OPTION}) if PASSIVE_MODAL_OPTION in items else frozenset()


def modal_options(identity, native=None):
    """The ``bl_options`` an Angelo modal operator adds for its class: MODAL_PASSIVE for the canvases and
    overlays, nothing for a holding tool. Operators pass their own ``bl_idname``, so the native option follows
    the one classification above and a new modal operator gets it right when it is classified."""
    native = _native_passive_option() if native is None else native
    return set(native) if identity in CANVAS_INPUTS | PASSIVE_OVERLAYS else set()

# The chat keeps keyboard focus for typing, like a text field, so a prompt
# typed with the pointer resting over the 3D view never fires G, X or Tab
# there. It keeps it until Escape (the draft stays), a click outside the
# composer or pointer use elsewhere (FOCUS_GESTURES); every Angelo-drawn text
# field ends editing on Escape the same way (test_native_field_escape).
# View navigation never edits the scene, so these keys always reach the
# editor under the pointer: numpad views, frame all/selected, the view pie and
# NDOF buttons. NUMPAD_ENTER is absent on purpose: it sends the message.
POINTER_KEYS = frozenset({"NUMPAD_" + str(digit) for digit in range(10)} | {
    "NUMPAD_PERIOD", "NUMPAD_PLUS", "NUMPAD_MINUS", "NUMPAD_SLASH", "NUMPAD_ASTERIX", "HOME", "ACCENT_GRAVE"})
# Using the pointer outside the chat moves keyboard focus there, as a left click already did.
FOCUS_GESTURES = frozenset({"MIDDLEMOUSE", "RIGHTMOUSE", "BUTTON4MOUSE", "BUTTON5MOUSE", "BUTTON6MOUSE", "BUTTON7MOUSE",
                            "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
                            "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM"})


# Never the person's input to an overlay (the welcome, a card, the showcase, a sheet, a canvas): timers belong to
# whoever set them and focus leaving the window is the window's. Blender passes these on whatever a handler returns
# (``wm_event_always_pass``); a handler that acted on them would act on something that was never meant for it.
NOT_INPUT = frozenset({"NONE", "WINDOW_DEACTIVATE"})
MODIFIERS = ("shift", "ctrl", "alt", "oskey")


def not_input(event):
    return event.type.startswith("TIMER") or event.type in NOT_INPUT


def refresh_move(event):
    """A pointer move without motion: Blender's own hover refresh (``WM_event_add_mousemove``), not the person.

    A quit waits for exactly this event: ``wm.quit_blender`` (a menu, a script, the system's quit) adds a handler in
    front of the window's modal handlers and one motionless move. A modal handler added after that (a welcome or card
    opening on a timer) sits in front of it, so a handler may use the move for its hover but must pass it on, or the
    quit waits until the pointer leaves. A simulated move (``Window.event_simulate``) has no motion either.
    """
    return event.type == 'MOUSEMOVE' and (event.mouse_x, event.mouse_y) == (
        getattr(event, "mouse_prev_x", None), getattr(event, "mouse_prev_y", None))


def bound(event, window_manager, keymap_name, idnames):
    """Whether the person's key map ``keymap_name`` runs one of ``idnames`` on this press (their own keys, never a
    hard-coded one)."""
    keyconfig = getattr(getattr(window_manager, "keyconfigs", None), "user", None)
    keymap = keyconfig.keymaps.get(keymap_name) if keyconfig is not None and event.value == 'PRESS' else None
    return keymap is not None and any(
        item.idname in idnames and item.active and item.type == event.type and item.value in {'PRESS', 'ANY'}
        and all(getattr(item, name) in (-1, int(getattr(event, name, False))) for name in MODIFIERS)
        for item in keymap.keymap_items)


def quit_shortcut(event, window_manager):
    """The person's quit key: the Window key map's Quit (Ctrl+Q by default, or the person's own)."""
    return bound(event, window_manager, "Window", {"wm.quit_blender"})


def overlay_passes(event, window_manager):
    """What every Angelo modal handler passes on wherever the pointer is: what is not input, and the quit key."""
    return not_input(event) or quit_shortcut(event, window_manager)


def follows_pointer(event_type):
    """A navigation key that goes to the editor under the pointer even while the chat has focus."""
    return event_type in POINTER_KEYS or event_type.startswith("NDOF_")


def moves_focus(event_type, value):
    """Pointer use outside the chat (orbit, pan, zoom, context menu) that takes keyboard focus with it."""
    return (event_type in FOCUS_GESTURES and value != "RELEASE") or event_type.startswith("NDOF_")


SENT_AWAY_PX = 24   # A deliberate move of the pointer, not the jitter of a hand resting on the mouse while typing.


def sent_away(operator, event, areas, is_chat):
    """After a send left the message box empty, the first deliberate pointer move outside the chat ends editing
    (``operator.release_keyboard``): the person has gone back to the 3D view, so G, Tab or Z there while Angelo
    works are the view's, never typed into the empty draft. Keys typed without moving the pointer (the next
    message, a steer) stay the chat's, so no word's first letter becomes a hotkey; a draft typed since the send
    keeps the focus, as before any send. ``operator.just_sent``: where the pointer was at the send.
    True when the chat lets go."""
    sent = getattr(operator, "just_sent", None)
    if not sent or event.type != "MOUSEMOVE":
        return False
    if abs(event.mouse_x - sent[0]) + abs(event.mouse_y - sent[1]) < SENT_AWAY_PX:
        return False
    operator.just_sent = None
    chat = next((a for a in areas if a.as_pointer() == operator.focused_area and is_chat(a)), None)
    history = chat.spaces.active.history if chat is not None else None
    if history and history[-1].body:
        return False
    operator.release_keyboard()
    return True


# Keys the chat's message field takes whatever the modifiers (chat_view): editing, the caret, sending and Esc.
FIELD_KEYS = frozenset({"RET", "NUMPAD_ENTER", "BACK_SPACE", "DEL", "LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW",
                        "DOWN_ARROW", "HOME", "END", "ESC"})


def edits_field(event):
    """Whether a key press would edit the chat's message field: its own keys, select all, copy, cut, paste, or what
    the Console keymap under the chat would type: a character with any modifier but Cmd (``console.insert`` takes
    TEXTINPUT whatever is held, and Ctrl+1 keeps its "1"; a Ctrl+letter's control character is no text), and Ctrl+Tab
    (``console.insert`` of a tab). Break-it R5-2: Ctrl+Tab and Ctrl+digit typed past the composer's selection."""
    if event.type in FIELD_KEYS:
        return True
    if event.oskey:
        return event.type in {"A", "C", "X", "V"}
    if event.ctrl:
        text = event.unicode or ""
        return event.type in {"A", "C", "X", "V", "TAB"} or (bool(text) and text.isprintable())
    return bool(event.unicode)


def operator_identity(operator):
    value = getattr(operator, "bl_idname", "")
    if not value:
        value = getattr(getattr(operator, "bl_rna", None), "identifier", "")
    return value.replace("_OT_", ".", 1).lower()


RENDER_OPERATOR = "render.render"


def _render_ended():
    """Whether no render job runs, so a render operator still on the modal stack is only waiting to leave.

    Blender's render operator stays modal until the first event after its job ended, and an unattended
    window gets none: without this, a finished render (a job, or angelo_view's RENDERED look) would keep
    every later tool waiting for an interaction that is over. Outside Blender the answer is no.
    """
    try:
        import bpy
        return not bpy.app.is_job_running("RENDER")
    except (ImportError, AttributeError, TypeError):
        return False


def _interactions(window, ignored):
    """``(identity, operator)`` of each running modal operator not in ``ignored``."""
    found = []
    for op in getattr(window, "modal_operators", ()):
        identity = operator_identity(op)   # One read each: the hold reads every window once per tick.
        if identity not in ignored:
            found.append((identity or "Blender interaction", op))
    if any(identity == RENDER_OPERATOR for identity, _ in found) and _render_ended():
        found = [(identity, op) for identity, op in found if identity != RENDER_OPERATOR]
    return found


def active_interactions(window):
    """Window.modal_operators is public RNA; no native memory or UI guessing.

    Chat/Studio inputs are passive window handlers, not foreground operations.
    Every other modal operator gets its events first, including Esc/confirm and
    drag releases. Do not cancel it or change its editor behind the user's back.
    A render operator whose job has ended is no interaction (``_render_ended``).
    """
    return [identity for identity, _ in _interactions(window, CANVAS_INPUTS)]


def interaction_active(window):
    return bool(active_interactions(window))


CORNER_CARDS = "angelo.card_host"


def _corner_card_only():
    """The notice host shows nothing that holds the layout: a non-modal corner card (the update notice, the chat
    hint) or no card at all (a closed card's operator ends at its next event). Its modal cards (a crash report,
    What's New) do hold it."""
    import sys
    host = sys.modules.get((__package__ or "") + ".card_host")
    if host is None:
        return False
    active = getattr(host, "_active", None)
    return active is None or not getattr(active.card, "modal", True)


def layout_blocking(window):
    """``interaction_active`` for moving areas: docking or showing the chat, changing the workspace mode.

    A corner card over the 3D view takes only presses on itself and lays itself out again at any size, so it never
    makes a layout change wait. Evidence: the chat hint (1.0.3) says to click Chat, and with it on screen Show
    refused ("Finish or cancel the active Blender tool"); since 1.0.0 an update notice did the same to the dock and
    to switching to Director. Everything else ``interaction_active`` counts still waits, and Blender's window
    screenshot (``view_editor``) keeps ``interaction_active``: the card's pixels would be in it."""
    ignored = CANVAS_INPUTS | ({CORNER_CARDS} if _corner_card_only() else frozenset())
    return bool(_interactions(window, ignored))


def canvas_blocking(window):
    """The interactions a canvas (the chat, Media Studio) leaves its events to: all but Angelo's
    passive overlays (``PASSIVE_OVERLAYS``). Returns each one's name as the person sees it, for the notice a click
    on the blocked canvas gets (``chat_view``); empty when the canvas may take its input."""
    names = []
    for identity, op in _interactions(window, CANVAS_INPUTS | PASSIVE_OVERLAYS):
        label = getattr(op, "name", "") or getattr(op, "bl_label", "") or identity
        if label not in names:
            names.append(label)
    return names


def tool_running(window):
    """Whether a real tool (not one of Angelo's passive overlays) holds ``window``: what the agent's offscreen
    captures, renders, sculpt and program jobs wait for. A welcome screen or notice card left open never makes a
    turn's tools refuse."""
    return bool(canvas_blocking(window))


def handler_running(window, identity):
    """Whether ``identity``'s modal handler really runs in ``window``. A Python ``modal`` that raises ends without
    its ``cancel``, so a set of started windows alone can claim a handler that is gone (the input then stays dead
    until a restart); ``Window.modal_operators`` is what Blender runs."""
    return any(operator_identity(op) == identity for op in getattr(window, "modal_operators", ()))


# The person's operation in progress: Blender's own rule for "not in a valid state to save" (``wm_autosave_timer``,
# wm_files.cc): a window holds a modal operator handler without MODAL_PASSIVE. It covers every editing tool whatever
# its flags (a transform or knife pushes an Undo step; a sculpt or paint stroke, a mask gesture, manages its own undo
# and carries neither UNDO flag), a render or bake the person started, and view navigation (a short pause while the
# person orbits). Never: one of Angelo's own operators, one Angelo started (``angelo_started``), a render whose job
# has ended (``_interactions``), or the operator a file browser is open for (choosing a file is no half-made edit).
# An add-on's Python modal holds once it has consumed the person's input (the runtime ``Operator.has_consumed_input``,
# kept for its life: one of its answers kept an input event; RUNNING_MODAL with PASS_THROUGH counts), or while no
# input has reached it yet (``has_answered_input``, set when one reaches it at all; invoke sets neither), whatever its
# flags say: an edit tool may declare
# only REGISTER, and an observer that runs all session (a key-display overlay) only passes input on, so it never
# holds. A build without the mark holds (fail closed). Blender's C operators keep autosave's rule (maintainer
# ruling 2026-11-13, break-it review 3 S1).
# Evidence (learn-l4, stock Blender 5.3.0): reading an object's edit
# mesh from Python while such a tool ran dropped the person's edit when it ended; break-it reviews 2026-11-13.
UNREADABLE_TOOL = "the tool"   # A window or operator that could not be read: a tool is assumed (fail closed).
_OWN = {}   # as_pointer() -> (identity, window as_pointer()) of each modal operator Angelo started, while it runs.
LAST_UNREADABLE = [None]   # The last operator read that raised (``operations``).
LAST_LIVE = [None]   # Every running modal's pointer at the last ``operations`` read.


def _pointer(op):
    return op.as_pointer() if hasattr(op, "as_pointer") else id(op)


def _modals(window_manager, identities=False):
    """``(window, operator)`` (with ``identities``: ``(window, identity, operator)``) of every running modal operator
    in every window, ended renders left out."""
    from . import live_windows   # The one walk over windows; a window between screens still runs its modals.
    return [(window, identity, op) if identities else (window, op)
            for window in live_windows.every_window(window_manager)
            for identity, op in _interactions(window, frozenset())]


def _python_defined(op):
    """An add-on's operator (a Python class names its own ``bl_idname``), not one of Blender's C operators."""
    return isinstance(getattr(type(op), "bl_idname", None), str)


def _consuming(op):
    """An add-on's modal the person's input is, or may be, going to: it consumed some (``has_consumed_input``), or no
    input has reached it yet (``has_answered_input`` False: a fresh modal holds until its first input or its end; Angelo
    never sends it one, review 7). Missing marks (an older build) hold. A modal that edits on timers while passing
    every input on never holds (break-it review 5, F4). Both are Known gaps (``agent_execution.rst``)."""
    return getattr(op, "has_consumed_input", True) or not getattr(op, "has_answered_input", False)


def _browsing(window_manager):
    """``as_pointer()`` of each operator a file browser is open for now (its fileselect handler)."""
    from . import live_windows
    return {_pointer(space.active_operator) for window in live_windows.windows(window_manager)
            for area in live_windows.areas(window) if area.type == "FILE_BROWSER"
            for space in (area.spaces.active,) if getattr(space, "active_operator", None) is not None}


def operations(window_manager):
    """``[(window, name, own)]`` of each operation in progress (see above), in window order: the person's
    (``own`` False) and the job-driven ones Angelo started (``own`` True, never a hold, ``edit_tool_hold._nudge``
    ends them with their job). The name is the operator's ``bl_label``. Raises what an unreadable window raises."""
    LAST_LIVE[0] = None   # A read that raises leaves nothing to reuse (angelo_started walks for itself then).
    from . import live_windows
    modals, unreadable = [], []
    for window in live_windows.every_window(window_manager):   # One unreadable window is that window's entry only.
        try:
            modals += [(window, identity, op) for identity, op in _interactions(window, frozenset())]
        except Exception as error:  # noqa: BLE001 - it holds (fail closed), as one entry (break-it review 4, S-C).
            unreadable.append((window, None, UNREADABLE_TOOL, False))
            LAST_UNREADABLE[0] = error
    live = LAST_LIVE[0] = {_pointer(op) for _window, _identity, op in modals}   # angelo_started's "before" (S8).
    for pointer in [pointer for pointer in _OWN if pointer not in live]:   # Ended: a later one may reuse it.
        _OWN.pop(pointer, None)
    found = list(unreadable)
    for window, identity, op in modals:
        if identity.startswith("angelo."):   # Angelo's own canvases first: a string test, no RNA read (S2).
            continue
        try:   # Every read of this operator (review 6, N1): one that raises is this operator's entry only.
            options, python = set(op.bl_options), _python_defined(op)
            if PASSIVE_MODAL_OPTION in options or python and not _consuming(op):
                continue   # Passive, or an add-on's modal that has only passed the person's input on.
            name = getattr(op, "bl_label", "") or getattr(op, "name", "") or identity
        except Exception as error:  # noqa: BLE001 - this one operator is unreadable: it holds (fail closed).
            found.append((window, op, UNREADABLE_TOOL, False))
            LAST_UNREADABLE[0] = error   # edit_tool_hold reports it once.
            continue
        own = _OWN.get(_pointer(op)) == (identity, _pointer(window))   # Its identity too: never a reused address.
        found.append((window, op, name, own))
    try:
        browsing = _browsing(window_manager) if any(not own for *_rest, own in found) else set()
    except Exception:  # noqa: BLE001 - no browser known: every operation holds (fail closed).
        browsing = set()
    return [(window, name, own) for window, op, name, own in found if op is None or _pointer(op) not in browsing]


def editing_tools(window_manager):
    """``[(window, name)]`` of each person's operation in progress (``operations``). An unreadable window manager,
    window or operator gives ``[(None, UNREADABLE_TOOL)]`` (fail closed); one without windows (background) none."""
    try:
        return [(window, name) for window, name, own in operations(window_manager) if not own]
    except Exception:  # noqa: BLE001 - freed or unreadable RNA: wait rather than read under a running tool.
        return [(None, UNREADABLE_TOOL)]


def editing_tool(window_manager):
    """The name of the person's operation in progress in any window (``editing_tools``), or None."""
    found = editing_tools(window_manager)
    return found[0][1] if found else None


def editing_tool_running(window_manager):
    """Whether the person's operation is in progress in any window (``editing_tools``)."""
    return bool(editing_tools(window_manager))


class angelo_started:
    """``with angelo_started(window_manager): ...`` around anything of Angelo's that may start a modal operator (a
    job's render or bake, an agent's script or tool): each modal operator new when it ends is Angelo's own, never the
    person's operation, for as long as it runs. Break-it review 2026-11-13 (M4): Angelo's own physics bake
    (``ptcache.bake_all``) otherwise held every chat as "the person's tool"."""

    def __init__(self, window_manager, before=None):
        self.window_manager, self.before = window_manager, before   # ``before``: this tick's read (no second walk).

    def __enter__(self):
        if self.before is None:
            try:
                self.before = {_pointer(op) for _window, op in _modals(self.window_manager)}
            except Exception:  # noqa: BLE001 - nothing known before: nothing is claimed after.
                self.before = None
        return self

    def __exit__(self, *_exc):
        if self.before is None:
            return False
        try:
            from . import live_windows   # Pointers only (no identity read); a modal that ended meanwhile is no claim.
            now = {_pointer(op) for window in live_windows.every_window(self.window_manager)
                   for op in window.modal_operators}
            if now <= self.before:   # No modal added (the usual case): nothing more to read.
                return False
            for window, op in _modals(self.window_manager):
                if _pointer(op) not in self.before:
                    _OWN[_pointer(op)] = (operator_identity(op), _pointer(window))
        except Exception:  # noqa: BLE001
            pass
        return False
