# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make Video from This Shot: one click from Director (the Render menu, View > Cameras and the chat bar's + menu)
renders the active camera's shot (``shot_capture``) and opens it in Media Studio's Video tab.

Two entries, one per way a video model follows the 3D scene:

* **Make Video from This Shot** (``mode`` FRAMES): the first and last frame, for image-to-video;
* **Video from This Camera Move** (``mode`` MOTION): the move as an MP4, for reference-to-video.

The click needs no dialog: the frames take ``settings`` (aspect, size). While the parts render, their cards show
progress and Cancel in the chat; when they end, a "ready" card with an Open Video tab button (and why a part is missing), and
the shot opens in the Video tab (``studio_video_ui.show_shot``, v108-fal-video) with the shot's aspect chosen there.
Nothing is sent to a model or billed until the person confirms Generate. A build without the Video tab adds the frames
to Media Studio's library instead.

The tab's own "From the current camera" entry renders one frame through ``camera_source`` (the tab's
``set_camera_source`` contract): ``source(which, aspect, deliver)`` starts the render job and returns at once;
``deliver(path)`` or ``deliver(None, reason)`` follows on the main thread when it ended. ``which`` is ``first`` or
``last``; a last frame's aspect is "" (the first frame decides the shape), so it takes the aspect the last first
frame used. Strings are translated at display time.
"""

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from . import shot_capture
from .ui_text import iface_, n_, notice

# The frames' aspect and short side; the aspect also follows the last first frame the Video tab asked for.
settings = {"aspect": "16:9", "short_side": shot_capture.FRAME_SIDE}
LABEL = n_("Make Video from This Shot")
WHY = n_("A video that starts and ends on this camera's view")
MOTION_LABEL = n_("Video from This Camera Move")
MOTION_WHY = n_("A video whose camera follows this camera's move")
READY = n_("Your shot from {camera} is ready in Media Studio's Video tab")
NOT_MADE = n_("Not made for this shot: {part} ({reason})")
NOT_OPENED = n_("Media Studio couldn't take the shot: {reason}")
NO_FRAME = n_("The camera frame wasn't rendered; its card in the chat says why")
PART_NAMES = {"first": n_("first frame"), "last": n_("last frame"), "motion": n_("camera move (MP4)")}
MODES = {"FRAMES": ("first", "last"), "MOTION": ("motion",)}


class ANGELO_OT_shot_to_video(Operator):
    bl_idname = "angelo.shot_to_video"
    bl_label = LABEL
    bl_description = WHY
    bl_options = {'REGISTER'}

    # FRAMES: first and last frame (image-to-video); MOTION: the camera move's MP4 (reference-to-video). The
    # entries set it and nothing draws it, so its items carry no text to translate.
    mode: EnumProperty(items=(("FRAMES", "", ""), ("MOTION", "", "")), default="FRAMES",
                       options={'SKIP_SAVE', 'HIDDEN'})   # Set by each entry, never drawn.

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return scene is not None and scene.camera is not None

    @classmethod
    def description(cls, context, properties):
        return iface_(MOTION_WHY if properties.mode == "MOTION" else WHY)

    def execute(self, context):
        from . import live_target, ui
        controller = ui.get_controller()
        session, held = controller.state["id"], live_target.window_ref(context.window)
        try:
            shot = shot_capture.start(context.window, controller, aspect=settings["aspect"],
                                      short_side=settings["short_side"], parts=MODES[self.mode],
                                      on_done=lambda finished: shot_ready(finished, session, held))
        except shot_capture.ShotError as error:
            self.report({'ERROR'}, iface_(error.args[0], **error.fields))
            return {'CANCELLED'}
        self.report({'INFO'}, iface_("Rendering the shot from {camera}", camera=shot.camera))
        return {'FINISHED'}


def _window(held):
    """The window the click came from while it is open (``held``: a ``live_target.window_ref``), else the first."""
    from . import live_windows
    return held() or live_windows.first(bpy.context.window_manager)


def shot_ready(shot, session, held, tab=None):
    """The finished shot: a chat row naming it, then the Video tab (or, without one, the library) takes it."""
    # Where to find it, as a card with an Open button (chat_rows), never the workspace folder's path.
    rows = [dict(notice(READY, camera=shot.camera), open_studio="video")] if shot.files else []
    from .display_text import Text
    # One row for each part it lacks; a part's name is a translatable sentence of its own (notice).
    rows += [notice(NOT_MADE, part=Text(PART_NAMES[part]), reason=shot.errors[part])
             for part in shot_capture.PARTS if part in shot.errors]
    tab = tab if tab is not None else _video_tab()
    if shot.files and tab is not None and hasattr(tab, "show_shot"):
        try:
            state = tab.show_shot(_window(held), first=_path(shot, "first"), last=_path(shot, "last"),
                                  video=_path(shot, "motion"), name=shot.camera)
            if hasattr(tab, "choose_options"):   # The rendered shape is the video's.
                tab.choose_options(state, aspect=shot.aspect)
        except (RuntimeError, ValueError) as error:
            rows.append(notice(NOT_OPENED, reason=str(error)[:200]))
    elif shot.files:
        _to_library(shot)
    _post(session, rows)


def _path(shot, part):
    path = shot.files.get(part)
    return str(path) if path is not None else None


def _to_library(shot):
    """No Video tab in this build: the frames become Media Studio images, inputs like any other."""
    try:
        from .flow_host import library
        store = library()
        for part in ("first", "last"):
            if part in shot.files:
                asset = store.import_image(shot.files[part])
                asset.update(prompt="Shot %s · %s · %s" % (part, shot.camera, shot.aspect),
                             lineage={"source": "shot", "part": part, "camera": shot.camera})
                store.update(asset)
    except Exception as error:   # noqa: BLE001 - the files stay in the workspace whatever the library says.
        print("Angelo: shot frames not added to the library (%s)" % type(error).__name__)


def _post(session, rows):
    from . import ui
    controller = ui._controllers.get(session)
    if controller is None or not rows:
        return
    with controller.lock:
        controller.state["messages"].extend(dict(row, role="system", notice=True) for row in rows)
    controller.version += 1
    try:
        controller.save()
    except OSError:
        pass
    from . import job_ui
    job_ui.redraw()


def camera_source(which, aspect, deliver, *, window=None, controller=None):
    """The Video tab's camera hook: render the active camera's ``which`` frame ("first" or "last") as a job and
    return at once; ``deliver(path)`` or ``deliver(None, reason)`` (an ``n_`` sentence) once it ended."""
    if which == "first" and aspect:
        settings["aspect"] = aspect   # A last frame ("" aspect) takes the first frame's shape.
    if window is None:
        from . import live_windows
        window = bpy.context.window or live_windows.first(bpy.context.window_manager)
    if controller is None:
        from . import ui
        controller = ui.get_controller()

    def done(shot):
        path = shot.files.get(which)
        deliver(str(path)) if path is not None else deliver(None, NO_FRAME)
    try:
        shot_capture.start(window, controller, aspect=aspect or settings["aspect"],
                           short_side=settings["short_side"], parts=(which,), on_done=done, broadcast=False)
    except shot_capture.ShotError as error:
        deliver(None, error.args[0])


def _video_tab():
    """Media Studio's Video tab module (v108-fal-video), or None on a build without it."""
    try:
        from . import studio_video_ui
    except ImportError:
        return None
    return studio_video_ui if callable(getattr(studio_video_ui, "set_camera_source", None)) else None


def menu_item(self, context):
    layout = self.layout
    layout.operator(ANGELO_OT_shot_to_video.bl_idname, text=iface_(LABEL), translate=False).mode = "FRAMES"
    layout.operator(ANGELO_OT_shot_to_video.bl_idname, text=iface_(MOTION_LABEL), translate=False).mode = "MOTION"


def _destinations():
    from . import chat_destinations as cd
    return (cd.Feature("shot_to_video", LABEL, WHY, glyph="video", order=16, section=cd.CREATE,
                       operator=ANGELO_OT_shot_to_video.bl_idname, properties=(("mode", "FRAMES"),),
                       owner="shot_capture_ui"),
            cd.Feature("shot_motion", MOTION_LABEL, MOTION_WHY, glyph="video", order=17, section=cd.CREATE,
                       operator=ANGELO_OT_shot_to_video.bl_idname, properties=(("mode", "MOTION"),),
                       owner="shot_capture_ui"))


MENUS = ("TOPBAR_MT_render", "VIEW3D_MT_view_cameras")


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_shot_to_video)
    for feature in _destinations():
        chat_destinations.register(feature)
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(menu_item)
    tab = _video_tab()
    if tab is not None:   # The Video tab's "From the current camera" entry.
        tab.set_camera_source(camera_source)


def unregister():
    from . import chat_destinations
    tab = _video_tab()
    if tab is not None:
        tab.set_camera_source(None)
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.remove(menu_item)
    for feature in ("shot_to_video", "shot_motion"):
        chat_destinations.unregister(feature)
    shot_capture.clear()
    bpy.utils.unregister_class(ANGELO_OT_shot_to_video)
