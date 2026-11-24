# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ways into the picture viewer (``markup_overlay``) and its place in the chat bar's + menu (2.1.0,
doc/angelo/attachment_markup.rst).

* A picture chip in the composer: a click on it opens it (``chat_composer``'s ``open_reference`` hit, through
  ``angelo.markup_picture`` with the chip's index).
* The + menu's **Mark up a picture…** (beside Attach): the newest picture in the composer, or, with none, the 3D view
  as it is now, captured into the composer as a picture of its own and opened. The capture is the 3D view drawn
  offscreen from its own view, shading and overlays (``view_rows``): the scene the person sees, never the interface
  drawn over it (the library search, the shelves, Angelo's bars) nor another editor.
"""

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

from .ui_text import iface_, n_

MENU_LABEL = n_("Mark up a picture…")
MENU_WHY = n_("Draw and comment on a picture to show the agent what you mean")
CHIP_LABEL = n_("Mark up this picture")
VIEW_NAME = n_("3D view")
NO_VIEW = n_("Open a 3D view to mark up what it shows")
BUSY = n_("Finish what you're doing in the 3D view first")
RENDERED_VIEW = n_("Switch the 3D view to Solid or Material Preview to mark it up")
OTHER_SCENE = n_("This chat's scene isn't the one shown: switch back to it to mark up its 3D view")
DRAWN_ENGINES = {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"}   # Drawn by the viewport itself.


def capture_view(controller, window):
    """The largest 3D view of ``window`` as a new private picture of the chat (its record), or ValueError with one of
    this module's English constants (translated where the person sees it, never for the agent: S5)."""
    from . import live_windows, owned_paths, view_editor
    from .picture_budget import encode_within
    from .view_png import MAX_DIMENSION
    areas = [a for a in live_windows.areas(window) if a.type == 'VIEW_3D']
    area = max(areas, key=lambda a: a.width * a.height, default=None)
    if area is None:
        raise ValueError(NO_VIEW)
    from . import document_scene   # Every capture, the person's + menu's too: never another document's scene (S5).
    if document_scene.refusal(controller, window) is not None:
        raise ValueError(OTHER_SCENE)
    if view_editor.interaction_active(window):
        raise ValueError(BUSY)
    if not _drawable(window, area):   # Never a screenshot: it would carry the interface drawn over the view (S1).
        raise ValueError(RENDERED_VIEW)
    reference = controller.references.new_observation(iface_(VIEW_NAME))
    try:
        rows = view_rows(window, area)
        factor = -(-max(rows.shape[:2]) // MAX_DIMENSION)
        if factor > 1:
            rows = (view_editor.box_downscale(rows.astype("float32"), factor) + 0.5).astype("uint8")
        data, _width, _height = encode_within(rows.tobytes(), rows.shape[1], rows.shape[0],
                                              controller.references.image_limit)   # Within the chat's picture limit.
        with open(owned_paths.require_writable(reference["path"]), "wb") as handle:
            handle.write(data)
        reference["bytes"] = len(data)
    except Exception:
        controller.references.discard_observation(reference)
        raise
    return reference


def _drawable(window, area):
    """Whether an offscreen draw shows this view as it is: every look but Rendered with an engine of its own (Cycles
    or an add-on's), whose offscreen draw would start a render of its own; such a view is refused (S1)."""
    space = area.spaces.active
    return space.shading.type != 'RENDERED' or window.scene.render.engine in DRAWN_ENGINES


def view_rows(window, area):
    """The 3D view drawn offscreen at its own size, view and projection, with its own shading and overlays: RGBA rows,
    bottom row first. Region handlers that draw the interface over the view (pixel space) are not part of it."""
    import gpu
    import numpy
    from .view_viewport import window_region
    space, region = area.spaces.active, window_region(area)
    view, width, height = space.region_3d, region.width, region.height
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        offscreen.draw_view3d(window.scene, window.view_layer, space, region, view.view_matrix,
                              view.window_matrix, do_color_management=True)
        buffer = offscreen.texture_color.read()
        buffer.dimensions = width * height * 4
        rows = numpy.frombuffer(bytes(buffer), dtype=numpy.uint8).reshape(height, width, 4).copy()
    finally:
        offscreen.free()
    rows[..., 3] = 255
    return rows


def reference_record(controller, reference_id):
    """This chat's picture ``reference_id`` (an attachment, a Look image, a view) as the viewer opens it, by its id
    only (``references.attached_image``: this chat's own regular picture, never a path); ValueError otherwise."""
    path = controller.references.attached_image(reference_id)
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        path.suffix.lower())
    if mime is None:
        raise ValueError("Not a PNG, JPEG or WebP picture")
    return {"id": reference_id, "path": str(path), "mime": mime, "kind": "image", "name": path.name}


def newest_picture(controller):
    return next((r for r in reversed(controller.attachments) if str(r.get("mime", "")).startswith("image/")), None)


class ANGELO_OT_markup_picture(Operator):
    bl_idname = "angelo.markup_picture"
    bl_label = "Mark Up a Picture"
    bl_description = "Draw and comment on a picture to show the agent what you mean"
    bl_options = {'INTERNAL'}

    index: IntProperty(default=-1, options={'SKIP_SAVE'})   # A composer chip's; -1: the + menu's choice.

    def execute(self, context):
        from . import markup_message, markup_overlay, ui
        controller = ui.get_controller()
        try:
            if self.index >= 0:
                shown = markup_overlay.open_attachment(controller, self.index)
            else:
                picture = newest_picture(controller)
                if picture is None:
                    if controller.busy:
                        raise ValueError(BUSY)
                    markup_message.room(controller)
                    picture = capture_view(controller, ui.main_window())
                    controller.attachments.append(picture)
                    controller.version += 1
                shown = markup_overlay.open_reference(controller, picture)
        except (OSError, RuntimeError, ValueError) as error:
            from .markup_edit_host import reason   # Translated, never Python's wording or a path (N3).
            self.report({'WARNING'}, reason(error))
            return {'CANCELLED'}
        return {'FINISHED'} if shown else {'CANCELLED'}

    def invoke(self, context, event):
        return self.execute(context)


def register():
    from . import chat_destinations, markup_overlay
    markup_overlay.register()
    bpy.utils.register_class(ANGELO_OT_markup_picture)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu, beside Attach.
        "markup", MENU_LABEL, MENU_WHY, glyph="markup", order=2, section=chat_destinations.ATTACH,
        operator="angelo.markup_picture", placement="bar", owner="markup_ui"))


def unregister():
    from . import chat_destinations, markup_overlay
    chat_destinations.unregister("markup")
    bpy.utils.unregister_class(ANGELO_OT_markup_picture)
    markup_overlay.unregister()
