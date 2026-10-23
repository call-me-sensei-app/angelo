# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's item menu: a right-click on a grid item (or the selection) offers what dragging it does (1.0.5).

For the keyboard and the agent, every drop has a menu item: ``media_menus.draw_actions`` (Add to Scene, Apply to
Selected, Add to Chat, the Asset Browser's and the Asset Shelf's menu items too) and, for images, Open in Image
Editor, which a drop on the Image Editor does. Each runs ``media_place``'s code, the drops' own, as one undo step.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator

from .ui_text import n_, rpt_

menu_ids = []   # The items the menu was opened for (the right-clicked item, or the selection holding it).


def items_for(state, identity):
    """The right-clicked item, or the whole selection when it is part of it (as a drag takes it)."""
    selected = list(state.get("selected_ids") or [])
    return selected if identity in selected else [identity]


def kinds(identities):
    from . import studio
    assets = studio.library().assets
    return [(i, assets[i]["kind"]) for i in identities if assets.get(i, {}).get("status") == "ready"]


class ANGELO_OT_media_open_image(Operator):
    bl_idname = "angelo.media_open_image"
    bl_label = "Open in Image Editor"
    bl_description = "Open Media Studio's images in the Image Editor, as dropping them on it does"
    bl_options = {'REGISTER', 'UNDO'}

    item_ids: StringProperty(name="Media Studio items", description="Media Studio ids, one per line or comma")

    def execute(self, context):
        from . import media_place as place
        identities = [p.strip() for p in self.item_ids.replace(",", "\n").splitlines() if p.strip()]
        images = [item for item in place.from_ids(identities) if item.kind == "IMAGE"]
        if not images:
            self.report({'WARNING'}, rpt_("Only images open in the Image Editor"))
            return {'CANCELLED'}
        editor = _image_editor(context)
        if editor is None:
            self.report({'WARNING'}, rpt_("Open an Image Editor to show the image in"))
            return {'CANCELLED'}
        with context.temp_override(area=editor, space_data=editor.spaces.active):
            shown = place.open_images(bpy.context, images)
        return {'FINISHED'} if shown else {'CANCELLED'}


def _image_editor(context):
    """This window's Image Editor (the largest), or None."""
    screen = context.window.screen if context.window else None
    areas = [a for a in (screen.areas if screen else ()) if a.type == 'IMAGE_EDITOR']
    return max(areas, key=lambda a: a.width * a.height, default=None)


class ANGELO_MT_studio_item(Menu):
    bl_idname = "ANGELO_MT_studio_item"
    bl_label = "Media Studio Item"

    def draw(self, context):
        from . import media_menus
        found = kinds(menu_ids)
        if not found:
            return
        media_menus.draw_actions(self.layout, found)
        if all(kind == "IMAGE" for _identity, kind in found):
            self.layout.operator(ANGELO_OT_media_open_image.bl_idname, icon='IMAGE').item_ids = "\n".join(
                identity for identity, _kind in found)


def open_menu(state, identity):
    """Media Studio's right-click on a grid item."""
    menu_ids[:] = items_for(state, identity)
    bpy.ops.wm.call_menu(name=ANGELO_MT_studio_item.bl_idname)


LABELS = (n_("Media Studio Item"), n_("Open in Image Editor"))   # Static labels Blender translates (bl_label).
CLASSES = (ANGELO_OT_media_open_image, ANGELO_MT_studio_item)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
