# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""3D models made from a dropped photo, placed where the photo landed when they are ready (1.0.8).

``photo_drop`` starts the generation (a normal Media Studio job) and hands its ``Placement`` here. While it runs, the
3D view shows the drop point with the job's progress. When Media Studio's record is ready the model comes in as Add to
Scene brings a Studio model in (``media_drop._prepared``, then ``media_place.add_models`` with the library on, or
its glTF import with it off), standing at the drop point and selected, as one Undo step.

It is placed only into the document and scene it was dropped in: after another file was opened, the scene was
removed or no window shows it, a notice says the model is ready in Media Studio instead. It waits while a tool is
running or the scene is in another mode than Object, so nothing lands under a person's drag or in Edit Mode. A failed
or stopped generation says so in a notice with Media Studio's reason. Placements live in this session only: after a
restart the model is still in Media Studio.
"""

from dataclasses import dataclass, field

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector

from .photo_drop_choices import READY, decide
from .ui_text import iface_, n_

WATCH_SECONDS = 0.25
MAKING = n_("Making a 3D model · {progress}%")
PLACING = n_("Ready: placing it when you are back in Object Mode")
ELSEWHERE_TITLE = n_("Your 3D model is ready")
ELSEWHERE = n_("It is in Media Studio: the scene it was dropped in isn't open any more.")
FAILED_TITLE = n_("The 3D model wasn't made")
UNDO_PLACE = n_("Place 3D model")   # Undo History, translated where Blender shows it.
COULD_NOT_PLACE = n_("It is in Media Studio, but couldn't be placed: {reason}")

_pending = {}       # Media Studio record id -> Placement
_document = [0]     # Bumped whenever a file loads: a placement from before belongs to another document.
_handler = None


@dataclass
class Placement:
    asset_id: str
    scene_name: str            # For reading only; the scene is found by its pointer.
    scene: int                 # The scene's pointer when the photo landed.
    point: tuple
    across: tuple
    name: str                  # The photo's file name, for notices.
    document: int = field(default_factory=lambda: _document[0])
    waiting: bool = False      # Ready, held until the scene is in Object Mode and no tool runs.


def pending():
    return dict(_pending)


def track(placement):
    _pending[placement.asset_id] = placement
    if not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=WATCH_SECONDS, persistent=True)
    _ensure_draw()
    _redraw()


def _watch():
    if not _pending:
        return None
    from . import studio
    library = studio.library()
    for identity, placement in list(_pending.items()):
        try:
            _step(identity, placement, library.assets.get(identity))
        except Exception as error:   # One placement's failure never stops the timer the others wait on.
            _pending.pop(identity, None)
            _notice("alert", iface_(ELSEWHERE_TITLE), iface_(COULD_NOT_PLACE, reason=str(error)))
    _redraw()
    return WATCH_SECONDS if _pending else None


def _step(identity, placement, asset):
    window = _window_for(placement)
    able = window is not None and _can_place(window)
    verdict = decide(placement, asset, _document[0], window is not None, able)
    placement.waiting = verdict == "wait" and bool(asset) and asset.get("status") == READY
    if verdict == "wait":
        return
    _pending.pop(identity, None)
    if verdict == "place":
        _place(placement, window)
    elif verdict == "elsewhere":
        _notice("check", iface_(ELSEWHERE_TITLE), iface_(ELSEWHERE))
    elif verdict == "failed":   # Media Studio's error: a translatable template, or provider detail as sent.
        error = asset.get("error") or ""
        _notice("alert", iface_(FAILED_TITLE), iface_(error) if error else "")


def _window_for(placement):
    """A window showing the placement's scene, found by its pointer (a scene renamed meanwhile is still it; a file
    load, which can reuse the address, is ``decide``'s document check), or None."""
    from . import live_windows
    if not any(scene.as_pointer() == placement.scene for scene in bpy.data.scenes):
        return None
    return next((w for w in live_windows.windows(bpy.context.window_manager, temporary=False)
                 if w.scene.as_pointer() == placement.scene), None)


def _can_place(window):
    """Object Mode and no real tool running (a notice card, the chat hint or the welcome screen left open never
    holds a made model back: ``input_policy.tool_running``)."""
    from . import input_policy
    view_layer = window.view_layer
    active = view_layer.objects.active if view_layer else None
    in_object_mode = active is None or active.mode == 'OBJECT'
    return in_object_mode and not input_policy.tool_running(window)


def _place(placement, window):
    from . import live_windows, media_drop, media_place as place
    from .studio_state import error_text
    area = max((a for a in live_windows.areas(window) if a.type == 'VIEW_3D'),
               key=lambda a: a.width * a.height, default=None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            items, written = media_drop._prepared([placement.asset_id])
            models = [i for i in items if i.kind == "3D"]
            point, across = Vector(placement.point), Vector(placement.across)
            added = (place.add_models(bpy.context, models, point, across) if written
                     else place.import_glbs(bpy.context, models, point))
            if not added:
                raise ValueError(n_("Media Studio has no model file for it"))
            bpy.ops.ed.undo_push(message=UNDO_PLACE)   # A timer's change pushes none: one step to undo it.
    except (OSError, ValueError, RuntimeError) as error:
        _notice("alert", iface_(ELSEWHERE_TITLE), iface_(COULD_NOT_PLACE, reason=error_text(error)))


def _notice(icon, title, body):
    from . import app_notices, card_host, update_card
    card_host.request(update_card.NoticeCard(icon, title, body, lifetime=12))
    card_host.pump(app_notices.ready)


# -- the drop point while the model is made --------------------------------------------------------------------
def _ensure_draw():
    global _handler
    if _handler is None and not bpy.app.background:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def _redraw():
    from . import live_windows
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def label(placement, asset):
    """The text beside the drop point (translated), or "" when nothing shows."""
    if asset is None or placement.document != _document[0]:
        return ""
    if placement.waiting:
        return iface_(PLACING)
    return iface_(MAKING, progress=int(asset.get("progress") or 0))


def _draw():
    if not _pending:
        return
    import gpu
    from bpy_extras import view3d_utils
    from . import card_kit as kit, chat_view, studio
    from .photo_drop_card import marker
    context = bpy.context
    scene, region, rv3d = context.scene, context.region, context.region_data
    if scene is None or region is None or rv3d is None:
        return
    library, scale = studio.library(), context.preferences.system.ui_scale
    for placement in list(_pending.values()):
        if placement.scene != scene.as_pointer():
            continue
        text = label(placement, library.assets.get(placement.asset_id))
        spot = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(placement.point))
        if not text or spot is None:
            continue
        x, y = spot
        gpu.state.blend_set('ALPHA')
        for op in marker(x, y, scale):
            if op[0] == "shape":
                chat_view.shape(*op[1], op[2], op[3])
            else:
                op[1](op[2])
        size, pad = 12 * scale, 10 * scale
        width = chat_view.measure(text, size) + 2 * pad
        left, height = x + 22 * scale, 26 * scale
        chat_view.shape(left, y - height / 2, width, height, kit.CARD, height / 2)
        chat_view.text(text, left + pad, y - 0.36 * size, size, kit.TEXT)
    gpu.state.blend_set('NONE')


@persistent
def _load_pre(*_args):
    _document[0] += 1


def register():
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    global _handler
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    _pending.clear()
