# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer: an attached picture full size over the main 3D view, marked up and commented, then sent to
Angelo's agent (2.1.0, doc/angelo/attachment_markup.rst).

``open_reference(controller, reference)`` opens it on a picture of the chat (a composer chip, a sent message's
picture, the + menu's picks), by its reference record only: never a path of anyone's making. One viewer at a time.

* **Showing**: a POST_PIXEL handler on the 3D view draws it (``markup_draw``), and one modal operator per open
  viewer (``angelo.markup_viewer``, a passive overlay in ``input_policy``) takes the events over that view; the chat
  and the rest of the window stay usable. Nothing is read in a draw: the chip's small picture shows at once
  (``studio_tile_previews``), and a timer reads the picture at ``DISPLAY_SIDE`` and swaps it in.
* **Editing**: every event goes to the session (``markup_tools``); the chrome's hits come from ``markup_layout``.
  The document is saved as a draft (``markup_store``) at most once a second while it changes, and on close, so
  closing never loses marks and reopening the picture brings them back.
* **Sending**: the picture is read at ``markup_message.flat_size``, flattened with the marks on the GPU
  (``markup_flatten``), encoded on a worker (``view_png``), written as a new private reference, frozen with its markup
  (``markup_store.freeze``) and sent through the person's own send (``console.send_message``: held in the tray while a
  turn runs, like any message). The original leaves the composer, which keeps everything else.
"""

import time
import traceback
from dataclasses import dataclass, field

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import canvas_rtl, chat_view, input_policy, keyboard_owner, live_windows, markup_doc as md, markup_draw
from . import markup_input, overlay_holds
from . import markup_send
from . import markup_layout as ml, markup_store, markup_tools as mt, markup_view as mv, scroll_input, ui_tokens
from . import picture_size, studio_tile_previews as tiles
from .input_policy import modal_options
from .ui_text import iface_, n_

IDENTITY = "angelo.markup_viewer"
DISPLAY_SIDE = 2048
SAVE_S = 1.0
TIP_S = 0.6
SEND_FAILED = n_("This picture couldn't be sent: {reason}")   # A failed send's reason (``say``).
LOAD_FAILED = n_("This picture couldn't be opened: {reason}")
MARKED_NAME = n_("{name} (marked up)")


@dataclass
class Viewer:
    controller: object
    reference: dict
    session: object
    window: int
    area: int
    path: str = ""   # The picture's checked file, found at open: a draw never touches the disk.
    token: object = field(default_factory=object)
    handler: object = None   # The running modal's own mark: one made again ahead of the chat's retires the old one.
    palette: bool = False
    ui: dict = field(default_factory=lambda: {"hover": None, "pressed": None, "focus": None, "tip": False})
    hovered_at: float = 0.0
    texture: object = None
    plan: dict = None
    plan_key: tuple = None
    saved: tuple = None
    saved_at: float = 0.0
    sending: bool = False
    error: str = ""
    edit_model: dict = None   # The image model a Remove BG or Erase card named (markup_edit_host.card).
    card: tuple = None        # An open question: (title, lines, run label).
    working: str = ""         # What a running edit or resize is doing, shown above the bar.
    keyboard: bool = True     # The viewer holds the keyboard (markup_input): a press on it takes it, elsewhere not.
    sent: bool = False        # Sent (markup_send): its draft is never saved again.
    waited: float = 0.0


_active = None


def covers(area_pointer):
    """Whether the viewer is open over this 3D view: the view's own chrome (the library search, the shelf's pills)
    steps aside under it (``chat_launcher._covered``)."""
    return _active is not None and _active.area == area_pointer
_handlers = {}
_pending = None
events = []   # Fixtures: (kind, detail), newest last, bounded.


def _note(kind, detail=""):
    events.append((kind, detail))
    del events[:-64]


def is_open():
    return _active is not None


def current():
    """The open viewer's state, or None."""
    return _active


def say(state, text):
    """A failure's sentence, already translated, shown above the bar until the next action."""
    state.error, state.working = text, ""
    redraw()


def redraw():
    _redraw()


def _area(window):
    areas = [a for a in live_windows.areas(window) if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height, default=None)


def open_reference(controller, reference, keyboard=True):
    """Open the viewer on one of the chat's pictures (a record of ``controller.references``). True when shown.
    ``keyboard``: whether it starts with the keyboard (False when the agent opened it: the person may be typing)."""
    global _pending
    if not str(reference.get("mime", "")).startswith("image/"):
        return False
    path = controller.references.checked_path(reference)   # This chat's own private picture, else ValueError.
    size = picture_size.picture_size(path)   # Past any metadata a camera writes first (S9).
    doc = markup_store.load_draft(controller.references, reference["id"]) or md.MarkupDoc(reference["id"], size)
    from . import ui
    window = ui.main_window()
    area = _area(window) if window is not None else None
    if area is None:
        return False
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    from .chat_launcher import insets as covered
    view = mv.View(size, (0, 0, region.width, region.height), _margins(region, "view", covered(area.regions, region)))
    session = mt.Session(doc, view)
    close("replaced")
    _pending = Viewer(controller, dict(reference), session, window.as_pointer(), area.as_pointer(), str(path),
                      keyboard=keyboard)
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.angelo.markup_viewer('INVOKE_DEFAULT')
    if _active is None:
        _pending = None
        return False
    if keyboard:   # Opened with the keyboard: the chat gives it up, even as its own menu closes (S1).
        keyboard_owner.take(window)
    tiles.previews().texture(path)   # The chip's small picture, queued if it isn't ready.
    if not bpy.app.timers.is_registered(_load):
        bpy.app.timers.register(_load, first_interval=0.0)
    _note("opened", reference["id"])
    return True


def open_attachment(controller, index):
    """A composer chip's picture (``chat_composer``'s hit)."""
    if 0 <= index < len(controller.attachments):
        return open_reference(controller, controller.attachments[index])
    return False


def _margins(region, mode, insets=(0, 0, 0)):
    plan = ml.layout(region.width, region.height, chat_view.ui_scale(), mode, False, _measure, insets=insets)
    return plan["margins"]


def _measure(text, size):
    return chat_view.measure(text, size)


def _load():
    """Read the picture at ``DISPLAY_SIDE`` once (a timer, never a draw) and swap it in for the small one."""
    state = _active
    if state is None or state.texture is not None:
        return None
    try:
        path = state.controller.references.checked_path(state.reference)
        pixels, width, height = tiles.decode(path, DISPLAY_SIDE)
        state.texture = tiles.upload(pixels, width, height)
    except (OSError, RuntimeError, ValueError) as error:   # Translated, never Python's wording or a path (S2).
        from .markup_edit_host import reason
        state.error = iface_(LOAD_FAILED, reason=reason(error))
    _redraw()
    return None


def _save(state, force=False):
    if state.sent:
        return
    doc = state.session.doc
    key = (doc.history_size, doc.can_redo, len(doc.marks), len(doc.comments), doc.instructions)
    if key == state.saved or (not force and time.monotonic() - state.saved_at < SAVE_S):
        return
    try:
        if doc.empty:
            markup_store.discard_draft(state.controller.references, doc.image)
        else:
            markup_store.save_draft(state.controller.references, doc)
        state.saved, state.saved_at = key, time.monotonic()
    except (OSError, ValueError) as error:
        _note("draft_failed", str(error))


def close(how="done"):
    """Close the viewer, keeping its marks as a draft."""
    global _active
    state, _active = _active, None
    if state is None:
        return
    try:
        _save(state, force=True)
    except Exception:   # The viewer is closed whatever its draft's save does.
        traceback.print_exc()
    _note("closed", how)
    _redraw_area(state)


FAILED_CLOSED = n_("The picture viewer closed after an error. Your marks are kept: open the picture again to go on.")


def _failed(error):
    """An exception in the modal: the viewer closes (draft kept, covers() ends), the chat says so, the log has why."""
    traceback.print_exception(error)
    state = _active
    close("failed")
    if state is not None:
        from .ui_text import notice
        try:
            state.controller.emit("message", {"role": "system", **notice(FAILED_CLOSED)})
        except Exception:   # noqa: BLE001  The chat's notice is a courtesy; the viewer is already closed.
            traceback.print_exc()
    return {'CANCELLED'}


def _redraw_area(state):
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.as_pointer() == state.area:
            area.tag_redraw()


def _redraw():
    if _active is not None:
        _redraw_area(_active)


def _draw():
    state = _active
    area, region = bpy.context.area, bpy.context.region
    if state is None or area is None or region is None or area.as_pointer() != state.area:
        return
    if region.as_pointer() != next(r for r in area.regions if r.type == 'WINDOW').as_pointer():
        return   # Quad View: drawn once, over the region the operator hit-tests.
    scale = chat_view.ui_scale()
    session = state.session
    from .chat_launcher import insets as covered   # The toolbar, sidebar and shelf regions drawn over the view.
    insets = covered(area.regions, region)
    session.view.margins = tuple(float(m) for m in _margins(region, session.mode, insets))
    session.view.set_region((0, 0, region.width, region.height))
    if session.confirm is None:
        state.card = None
    lines = len(state.card[1]) if state.card else None
    key = (region.width, region.height, scale, session.mode, state.palette, lines, insets)
    if state.plan is None or key != state.plan_key:
        state.plan, state.plan_key = ml.layout(region.width, region.height, scale, session.mode, state.palette,
                                               _measure, card=lines, insets=insets), key
    texture = state.texture
    if texture is None:
        preview = tiles.previews().texture(state.path)
        texture = preview.texture if preview is not None else None
    state.ui["tip"] = state.ui["hover"] is not None and time.monotonic() - state.hovered_at > TIP_S
    notice = ((state.error, True) if state.error else (iface_(session.notice[0], **session.notice[1]), True)
              if session.notice else (state.working, False) if state.working else None)
    with ui_tokens.themed():   # Drawn in the theme's variant (ui_kit.rst).
        markup_draw.draw(session, state.plan, texture, scale, state.ui, time.monotonic(), (region.width, region.height),
                         notice=notice, busy=state.sending or bool(state.working), card=state.card)


def _act(state, action, value):
    from . import markup_edit_host
    session = state.session
    state.error = ""
    if action == "ask":
        try:
            card = markup_edit_host.card(state, value)
        except ValueError as error:
            return say(state, markup_edit_host.reason(error))
        session.ask(value)
        state.card = card if session.confirm else None
    elif action == "answer":
        _effect(state, session.answer(value))
    elif action == "clear":
        session.clear_erase()
    elif action == "scale":
        session.set_scale(value)
    elif action == "apply_resize":
        try:
            markup_edit_host.resize(state)
        except (OSError, RuntimeError, ValueError) as error:
            say(state, iface_(markup_edit_host.FAILED, reason=markup_edit_host.reason(error)))
    elif action == "mode":
        session.set_mode(value)
        state.palette = False
    elif action == "tool":
        session.set_tool(value)
        state.palette = False
    elif action == "palette":
        state.palette = not state.palette
    elif action == "colour":
        session.set_colour(value)
    elif action == "width":
        session.set_width(value)
    elif action in ("undo", "redo"):   # As the keys do: the marks' history, or Erase's brush strokes.
        session.key("Z", command=True, shift=action == "redo")
    elif action == "instructions":
        session.focus_instructions()
    elif action == "send":
        markup_send.send(state)
    elif action == "close":
        close()


def _effect(state, effect):
    """What a key or an answer asks of the viewer beyond redrawing."""
    from . import markup_edit_host
    if effect in ("close", "ask_discard"):
        close()   # Unsent marks stay a draft: reopening the picture brings them back.
    elif effect == "send":
        markup_send.send(state)
    elif effect == "run":
        state.card = None
        try:
            markup_edit_host.run(state, state.session.confirmed)
        except (OSError, RuntimeError, ValueError) as error:
            say(state, iface_(markup_edit_host.FAILED, reason=markup_edit_host.reason(error)))


class ANGELO_OT_markup_viewer(Operator):
    bl_idname = "angelo.markup_viewer"   # IDENTITY, written out: the inventories read it as a literal.
    bl_label = "Mark Up Picture"
    bl_description = "Shows a picture of the chat full size to mark it up and comment on it for Angelo"
    bl_options = {'INTERNAL'} | modal_options(bl_idname)

    def invoke(self, context, event):
        global _active, _pending
        state, _pending = _pending, None
        area = next((a for a in live_windows.areas(context.window) if state and a.as_pointer() == state.area), None)
        if state is None or area is None:
            return {'CANCELLED'}
        space = type(area.spaces.active)
        if space not in _handlers:
            _handlers[space] = space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
        _active, self.token = state, state.token
        state.handler = self.handler = object()
        context.window_manager.modal_handler_add(self)
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if input_policy.overlay_passes(event, context.window_manager):   # Timers, window events and the quit key.
            return {'PASS_THROUGH'}
        try:
            return self._modal(context, event)
        except Exception as error:   # Never a dead scrim (break-it of 2298d608dd1, M2): close cleanly and say so.
            return _failed(error)

    def _modal(self, context, event):
        state = _active
        if state is None or state.token is not self.token or state.handler is not self.handler:
            return {'FINISHED', 'PASS_THROUGH'}
        if input_policy.overlay_passes(event, context.window_manager) or context.window.as_pointer() != state.window:
            return {'PASS_THROUGH'}
        area = next((a for a in live_windows.areas(context.window) if a.as_pointer() == state.area), None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        from . import ui   # Its editor changed, or its chat is no longer the window's (S7, S8): closed, draft kept.
        held = {'RUNNING_MODAL'} if markup_input.is_key(event) else {'PASS_THROUGH'}   # A key it was over: never on (N1).
        if region is None or area.type != 'VIEW_3D' or ui.get_controller() is not state.controller:
            close("lost")
            return held
        if state.plan is None:
            return held
        over = (region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height)
        session = state.session
        markup_input.owns(state, event, over, context.window)
        routed = markup_input.route(state, event, _act, _effect, lambda: context.window_manager.clipboard, redraw,
                                    over=over, chat_has_keyboard=keyboard_owner.chat_has_it(context.window, True),
                                    chat_takes=keyboard_owner.chat_takes(context.window, event, True))
        if routed is not None:   # A key: one owner per window; none reaches the 3D view under the viewer (M1, M2).
            area.tag_redraw()
            return routed
        if not over and not session.pressing:
            return {'PASS_THROUGH'}   # The chat and the rest of the window stay usable.
        x = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)   # The chrome mirrors; the picture never.
        px, y = event.mouse_x - region.x, event.mouse_y - region.y
        hit = ml.hit_at(state.plan, x, y)
        if event.type == 'MOUSEMOVE':
            if hit != state.ui["hover"]:
                state.ui["hover"], state.hovered_at = hit, time.monotonic()
            session.move(px, y)
        elif event.type in {'LEFTMOUSE', 'MIDDLEMOUSE'} and event.value == 'PRESS':
            state.ui["pressed"] = hit if event.type == 'LEFTMOUSE' else None
            if hit is None or event.type == 'MIDDLEMOUSE':
                state.palette = False
                session.press(px, y, event.type)
        elif event.type in {'LEFTMOUSE', 'MIDDLEMOUSE'} and event.value == 'RELEASE':
            pressed, state.ui["pressed"] = state.ui["pressed"], None
            if pressed is not None and pressed == hit:
                _act(state, *pressed)
            else:
                session.release(px, y, event.type)
        elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            session.wheel(px, y, 1 if event.type == 'WHEELUPMOUSE' else -1)
        elif event.type == 'TRACKPADPAN':
            session.view.pan(*scroll_input.gesture_travel(event))   # Every surface reads a gesture there.
        elif event.type == 'TRACKPADZOOM':
            session.wheel(px, y, scroll_input.gesture_travel(event)[0] / 40.0)
        if _active is state:
            _save(state)
        area.tag_redraw()
        return {'RUNNING_MODAL'}


@persistent
def _load_pre(*_args):
    close("lost")


def come_forward(window):
    """The viewer's handler made again ahead of a chat handler just made (``overlay_holds.keep_ahead``): the old one
    ends at its next event. The viewer's state, its token included, is kept (a send in flight still lands)."""
    global _pending
    state = _active
    if state is None or window.as_pointer() != state.window:
        return
    area = next((a for a in live_windows.areas(window) if covers(a.as_pointer())), None)
    if area is None:
        return
    _pending = state
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.angelo.markup_viewer('INVOKE_DEFAULT')


def hides_scene(window):
    """Whether the viewer is open over a 3D view of ``window`` (``overlay_holds.COVERS_WINDOW``)."""
    return _active is not None and window.as_pointer() == _active.window


def covers_point(window, x, y):
    """Whether the viewer is drawn over the editor at window pixels (x, y) (``overlay_holds.COVERS``)."""
    state = _active
    if state is None or window.as_pointer() != state.window:
        return False
    area = next((a for a in live_windows.areas(window) if covers(a.as_pointer())), None)
    return area is not None and area.x <= x < area.x + area.width and area.y <= y < area.y + area.height


def register():
    overlay_holds.COVERS["markup"] = covers_point   # The chat hands nothing into the 3D view under the viewer (M1).
    overlay_holds.AHEAD["markup"] = come_forward   # And stays ahead of a chat handler made again (S1).
    overlay_holds.COVERS_WINDOW["markup"] = hides_scene   # Its Undo never reaches the scene it hides (N2).
    bpy.utils.register_class(ANGELO_OT_markup_viewer)
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    close("unregistered")
    overlay_holds.COVERS.pop("markup", None)
    overlay_holds.AHEAD.pop("markup", None)
    overlay_holds.COVERS_WINDOW.pop("markup", None)
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    for space, handler in _handlers.items():
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    bpy.utils.unregister_class(ANGELO_OT_markup_viewer)
