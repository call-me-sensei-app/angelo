# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's items dropped anywhere, and the same actions as menu items (the agent calls them too).

``angelo.media_drop`` is what Angelo's drop boxes run (``ASSET_OT_angelo_media_drop`` in C, one undo step for the
whole drop): the drop's target, the relative asset identifiers of what was dragged, and the object or collection
under the pointer. The menu actions take Media Studio ids and do what the drops do, at the 3D cursor or on the
selection: Add to Scene, Apply to Selected, Add to Chat. Placing is ``media_place``'s; an item not written to the
Asset Browser library yet is written first (``studio_drag.ensure_written``), and with the library turned off a
model comes in through Blender's glTF import and an image's material is made from its picture: never a box.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from mathutils import Vector

from . import media_place as place
from .studio_state import error_text
from .ui_text import n_, rpt_

TARGETS = ("VIEW3D", "OUTLINER", "IMAGE", "CHAT", "STUDIO")   # The last two: angelo.media_drop_ui.


def _ids(text):
    return [part.strip() for part in text.replace(",", "\n").splitlines() if part.strip()]


class _Drop:
    """A drop that ``ASSET_OT_angelo_media_drop`` hands over: its target, the drop's library and relative asset
    identifiers, and what it was over (an object, a collection, or the scene's own collection)."""
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    library: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    items: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    object: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    collection: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    into: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        return self.run(context, event)

    def execute(self, context):
        return self.run(context, None)

    def run(self, context, event):
        if self.target != "STUDIO":   # The grid's own drag landed elsewhere: a later drop on it is never "back".
            from . import studio_drag
            studio_drag.forget_source()
        try:
            items = place.from_identifiers(self.items, self.library)
            if not items:
                raise ValueError(n_("This Media Studio item is not in the Asset Browser library yet"))
            place.check_files(items)   # All or nothing: nothing changes when an item's file is gone.
            count = self.place(context, event, items)
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
        return {'FINISHED'} if count else {'CANCELLED'}

    def place(self, context, event, items):
        models = [i for i in items if i.kind == "3D"]
        images = [i for i in items if i.kind == "IMAGE"]
        obj = bpy.data.objects.get(self.object) if self.object else None
        if self.target == "VIEW3D":
            point, across, rotation = place.view3d_point(context, event)
            added = place.add_models(context, models, point, across, select=False) if models else []
            count = len(added)
            if images and obj is not None:
                count += place.apply_materials([obj], images)
            elif images:   # Beside the models, if any: above them.
                lift = Vector((0.0, 0.0, place.REFERENCE_SIZE)) if models else Vector()
                refs = place.add_references(context, images, point + lift, rotation, across, select=False)
                added += refs
                count += len(refs)
            place._select(context, added)
            return count
        if self.target == "OUTLINER":
            if images:
                if obj is None:
                    raise ValueError(n_("Drop an image on an object to give it the material"))
                return place.apply_materials([obj], images)
            return len(place.add_models(context, models, context.scene.cursor.location.copy(),
                                        collection_name=self.collection, into=self.into))   # SCENE: collection names it
        if self.target == "IMAGE":
            return len(place.open_images(context, images))
        return 0


class ANGELO_OT_media_drop(_Drop, Operator):
    bl_idname = "angelo.media_drop"
    bl_label = "Drop Media Studio Items"
    bl_description = "Add Media Studio's items where they were dropped"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}   # The drop's one undo step.


class ANGELO_OT_media_drop_ui(_Drop, Operator):
    """A drop that changes only the interface (the chat's attachments, Media Studio's references): no undo step."""
    bl_idname = "angelo.media_drop_ui"
    bl_label = "Drop Media Studio Items"
    bl_description = "Add Media Studio's items where they were dropped"
    bl_options = {'INTERNAL'}

    def place(self, context, event, items):
        if self.target == "CHAT":
            return place.attach_to_chat(items)
        if self.target == "STUDIO":
            return place.add_studio_references(context.area, [i for i in items if i.kind == "IMAGE"], event)
        return 0


def _prepared(identities):
    """Items for a menu action: written to the library first when it is on (``studio_drag.ensure_written``)."""
    from . import studio_drag
    written = studio_drag.ensure_written(identities)
    return place.from_ids(identities), written


class ANGELO_OT_media_add_to_scene(Operator):
    bl_idname = "angelo.media_add_to_scene"
    bl_label = "Add to Scene"
    bl_description = "Add Media Studio's items at the 3D cursor: models standing there, images as reference images"
    bl_options = {'REGISTER', 'UNDO'}

    item_ids: StringProperty(name="Media Studio items", description="Media Studio ids, one per line or comma")

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.view_layer is not None

    def execute(self, context):
        try:
            items, written = _prepared(_ids(self.item_ids))
            point = context.scene.cursor.location.copy()
            models = [i for i in items if i.kind == "3D"]
            images = [i for i in items if i.kind == "IMAGE"]
            count = 0
            if models:
                count += len(place.add_models(context, models, point) if written else place.import_glbs(context, models, point))
            if images:
                lift = Vector((0.0, 0.0, place.REFERENCE_SIZE)) if models else Vector()
                count += len(place.add_references(context, images, point + lift))
            if not count:
                raise ValueError(n_("Nothing to add: these Media Studio items have no files"))
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_("Added to the scene"))
        return {'FINISHED'}


class ANGELO_OT_media_apply_to_selected(Operator):
    bl_idname = "angelo.media_apply_to_selected"
    bl_label = "Apply to Selected"
    bl_description = "Give the selected objects the Media Studio image's material, as dropping it on them does"
    bl_options = {'REGISTER', 'UNDO'}

    item_ids: StringProperty(name="Media Studio items", description="Media Studio ids, one per line or comma")

    @classmethod
    def poll(cls, context):
        if not any(o.type == 'MESH' for o in context.selected_objects):
            cls.poll_message_set(n_("Select the objects to give the material"))
            return False
        return True

    def execute(self, context):
        try:
            items, written = _prepared(_ids(self.item_ids))
            images = [i for i in items if i.kind == "IMAGE"]
            if not images:
                raise ValueError(n_("Apply to Selected takes images; add a model with Add to Scene"))
            targets = [o for o in context.selected_objects if o.type == 'MESH']
            count = (place.apply_materials(targets, images) if written
                     else place.apply_picture_materials(targets, images))
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
        if count:
            self.report({'INFO'}, rpt_("Material applied"))
        return {'FINISHED'} if count else {'CANCELLED'}


class ANGELO_OT_media_add_to_chat(Operator):
    bl_idname = "angelo.media_add_to_chat"
    bl_label = "Add to Chat"
    bl_description = "Attach Media Studio's items to the chat's next message, as dropping them on the chat does"

    item_ids: StringProperty(name="Media Studio items", description="Media Studio ids, one per line or comma")

    def execute(self, context):
        try:
            count = place.attach_to_chat(place.from_ids(_ids(self.item_ids)))
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
        if count:
            self.report({'INFO'}, rpt_("Added to the chat"))
        return {'FINISHED'} if count else {'CANCELLED'}


classes = (ANGELO_OT_media_drop, ANGELO_OT_media_drop_ui, ANGELO_OT_media_add_to_scene, ANGELO_OT_media_apply_to_selected,
           ANGELO_OT_media_add_to_chat)
