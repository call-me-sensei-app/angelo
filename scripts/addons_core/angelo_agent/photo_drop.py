# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A photo dropped on the 3D view from Finder or Explorer (1.0.8): Angelo's drop card, and what each choice does.

Contract: doc/angelo/photo_drop.rst. Blender's file drop (``WM_OT_drop_import_file``) runs the one file handler whose
``poll_drop`` takes the 3D view: ``ANGELO_FH_photo_drop`` for every image Blender reads, in a perspective or
orthographic view (a camera view keeps Blender's camera background drop). Blender's own handler for images there
(``VIEW3D_FH_empty_image``) would make the drop a stock two-item menu, so it is set aside while Angelo is enabled and
its job is the card's **Add as a picture**; movies keep it through ``ANGELO_FH_movie_empty`` (movies only). Disabling
Angelo gives the stock handler back.

The card (``photo_drop_card``) stands at the drop point. Its choices reuse what Media Studio's drops already do:

* **Make a 3D model**: Media Studio's image-to-3D (Meshy or Tripo, the person's key or the team's) through
  ``studio_ops.start_job`` with Studio's 3D defaults; the record lands in Media Studio like any generation and
  ``photo_drop_jobs`` places the model at the drop point when it is ready.
* **Use as a reference**: the chat's next message, as a drop on the chat attaches it (``ReferenceStore``).
* **Use as a material** on the object it landed on, and **Add as a picture** (a reference image standing at the
  drop point): ``media_place``'s picture material and reference image, one Undo step each, the picture packed once.

The chat bar's + menu offers the same card for a picked file (**Photo to 3D model…**), standing at the 3D cursor.
"""

import hashlib
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import FileHandler, Operator, OperatorFileListElement

from . import photo_drop_choices as choices
from .studio_state import error_text
from .ui_text import n_

MENU_LABEL = n_("Photo to 3D model…")
MENU_WHY = n_("Pick a photo: Angelo makes it a 3D model where you are working")
PREVIEW_SIZE = 256
UNDO_MATERIAL, UNDO_PICTURE = n_("Photo as material"), n_("Photo as picture")   # Undo History names.
_last_model = []   # The last photo drop's model key: the next card offers it first after Media Studio's choice.
_stock = None      # Blender's VIEW3D_FH_empty_image while Angelo stands in for it.


def _image_extensions():
    return ";".join(sorted(bpy.path.extensions_image))


def _movie_extensions():
    return ";".join(sorted(set(bpy.path.extensions_movie) - set(bpy.path.extensions_image)))


def _view_takes_drop(context):
    space = context.space_data
    return bool(space and space.type == 'VIEW_3D' and space.region_3d
                and space.region_3d.view_perspective in {'PERSP', 'ORTHO'})


class ANGELO_FH_photo_drop(FileHandler):
    bl_idname = "ANGELO_FH_photo_drop"
    bl_label = "Angelo Photo Drop"
    bl_import_operator = "angelo.photo_drop"
    bl_file_extensions = _image_extensions()

    @classmethod
    def poll_drop(cls, context):
        return _view_takes_drop(context)


# Blender's own 3D view drop for movies (an image empty playing it), unchanged while Angelo takes images.
class ANGELO_FH_movie_empty(FileHandler):
    bl_idname = "ANGELO_FH_movie_empty"
    bl_label = "Add empty image"
    bl_import_operator = "OBJECT_OT_empty_image_add"
    bl_file_extensions = _movie_extensions()

    @classmethod
    def poll_drop(cls, context):
        return _view_takes_drop(context)


class ANGELO_OT_photo_drop(Operator):
    bl_idname = "angelo.photo_drop"
    bl_label = "Photo to 3D Model"
    bl_description = ("Choose what a photo becomes: a 3D model placed where it was dropped, a chat reference, a "
                      "material or a picture in the scene")
    bl_options = {'INTERNAL'}   # The card's choices make their own Undo steps; opening it changes nothing.

    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: CollectionProperty(type=OperatorFileListElement, options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.webp", options={'HIDDEN', 'SKIP_SAVE'})

    def _paths(self):
        return [str(Path(self.directory) / item.name) for item in self.files if item.name] or (
            [self.filepath] if self.filepath else [])

    def invoke(self, context, event):
        paths = self._paths()
        if not paths:   # The + menu: pick a file first.
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return self._open(context, paths, event)

    def execute(self, context):
        paths = self._paths()
        if not paths:
            return {'CANCELLED'}
        return self._open(context, paths, None)

    def _open(self, context, paths, event):
        try:
            open_card(context, paths, event)
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def open_card(context, paths, event):
    """The card for ``paths`` over the 3D view dropped on (``event``), or for a picked file over the window's
    largest 3D view at the 3D cursor."""
    from bpy_extras import view3d_utils
    from . import card_host, live_windows, media_place as place
    from .photo_drop_card import PhotoDropCard
    window = context.window
    area = context.area
    if event is None or area is None or area.type != 'VIEW_3D':
        areas = [a for a in live_windows.areas(window) if a.type == 'VIEW_3D']
        area = max(areas, key=lambda a: a.width * a.height, default=None)
    region = _window_region(area, event)
    if area is None or region is None:
        raise RuntimeError(n_("Open a 3D view to place the photo"))
    path = Path(paths[0])
    if not path.is_file():
        raise ValueError(n_("Drop a photo file, not a folder"))
    rv3d = region.data
    shown_in = next(r for r in area.regions if r.type == 'WINDOW')   # The one region card_host draws and hit-tests.
    with context.temp_override(window=window, area=area, region=region):
        if event is not None:
            point, across, rotation, hit = place.view3d_hit(bpy.context, event)
            spot = (event.mouse_x - region.x, event.mouse_y - region.y)
        else:   # A picked file: at the 3D cursor, on the active object when it is selected.
            point, across, rotation, _hit = place.view3d_hit(bpy.context, None)
            active = bpy.context.active_object
            hit = active if active is not None and active.select_get() else None
            spot = view3d_utils.location_3d_to_region_2d(region, rv3d, point) or (region.width / 2, region.height / 2)
    if rotation is None:
        rotation = rv3d.view_rotation.to_euler()
    if region.as_pointer() != shown_in.as_pointer():   # Quad View: the card stands in the first quadrant, unmarked.
        spot = None
    drop = Drop(path, window, area, region, context.scene, point, across, rotation, hit)
    preview = Preview(path)
    card = PhotoDropCard(facts(path, hit), tuple(spot) if spot is not None else None, drop.run, len(paths),
                         area.as_pointer(), preview.painter, preview.release, busy=_chat_busy)
    card_host.request(card)
    card_host.pump(lambda: True)
    if card_host.active_card() is not card:   # Behind a dialog that must be answered first: never later, elsewhere.
        card_host.withdraw(card.kind)
        raise RuntimeError(n_("Finish what is on screen, then drop the photo again"))
    return card


def _chat_busy():
    from . import ui
    return bool(ui.get_controller().busy)


def _window_region(area, event):
    """The 3D view's main region under the pointer (one of four in Quad View), else its largest."""
    regions = [r for r in area.regions if r.type == 'WINDOW'] if area else []
    if event is not None:
        x, y = event.mouse_x, event.mouse_y
        under = next((r for r in regions if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height), None)
        if under is not None:
            return under
    return max(regions, key=lambda r: r.width * r.height, default=None)


def _target(obj):
    """The object's name when it can take a picture material (``media_place.apply_picture_materials``), else ""."""
    usable = (obj is not None and obj.type == 'MESH' and obj.library is None and obj.data is not None
              and obj.data.library is None)
    return obj.name if usable else ""


def photo_type(path):
    """The file's MIME type when the chat takes it (PNG, JPEG or WebP, by its bytes), else ""."""
    from .references import MIME, image_type
    try:
        with open(path, "rb") as handle:
            return MIME[image_type(handle.read(16))]
    except (OSError, ValueError):
        return ""


def facts(path, obj):
    from . import studio, ui
    from .studio_generation import MODEL_REFERENCE_MIMES
    from .studio_state import model_options
    models = model_options("3D", ui.generation_connections(), ui.generation_health())
    preferred = [_studio_choice(studio)] + list(_last_model)
    mime = photo_type(path)
    return choices.Facts(name=Path(path).name, model_photo=mime in MODEL_REFERENCE_MIMES, chat_photo=bool(mime),
                         models=models, model=choices.choose(models, [k for k in preferred if k]),
                         chat_busy=_chat_busy(), target=_target(obj))


def _studio_choice(studio):
    """Media Studio's chosen 3D model in any open Studio, as a ``choices.key``."""
    for state in list(studio._views.values()):
        chosen = (state.get("models") or {}).get("3D")
        if chosen:
            return "%s|%s" % tuple(chosen)
    return ""


def photo_id(path):
    """The photo's identity in the file it lands in (``studio_assets_landing``): the same bytes land, and are packed,
    once however often they are dropped."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "photo-" + digest.hexdigest()[:24]


class Drop:
    """Where one photo landed, and the card's choices for it (``run``)."""

    def __init__(self, path, window, area, region, scene, point, across, rotation, hit):
        self.path, self.point, self.across, self.rotation = path, point.copy(), across.copy(), rotation.copy()
        self.window, self.area, self.region = window.as_pointer(), area.as_pointer(), region.as_pointer()
        self.scene_name, self.scene = scene.name, scene.as_pointer()
        self.target = _target(hit)

    def run(self, action, card):
        """Do ``action`` for the card; an error text for the card, or None."""
        from .studio_generation import GenerationError
        try:
            if action == choices.CONNECT:
                bpy.ops.angelo.flow_connect()
            elif action == choices.MAKE_3D:
                self.make_model(card.facts.model)
            elif action == choices.REFERENCE:
                self.attach()
            else:
                self.in_scene(action)
        except (OSError, ValueError, RuntimeError, KeyError, GenerationError) as error:
            return error_text(error)
        return None

    def make_model(self, model):
        from . import photo_drop_jobs, studio, studio_ops
        from .studio_agent import generation_options
        from .studio_generation import validate_generation
        if model is None:
            raise ValueError(n_("Connect Meshy or Tripo in AI Providers first"))
        studio.library()
        references = studio._references.import_images([str(self.path)], [])
        options = generation_options(model["mode"], model["id"], {})   # Media Studio's 3D defaults.
        validate_generation(model["mode"], model["id"], "", options, references)
        asset = studio_ops.start_job("3D", "", model, "", options, references)
        _last_model[:] = [choices.key(model)]
        photo_drop_jobs.track(photo_drop_jobs.Placement(asset["id"], self.scene_name, self.scene, tuple(self.point),
                                                        tuple(self.across), self.path.name))

    def attach(self):
        from . import chat_toggle, live_windows, ui
        controller = ui.get_controller()
        if controller.busy:
            raise ValueError(n_("Wait for the chat's turn to finish"))
        controller.attachments.extend(controller.references.import_images([str(self.path)], controller.attachments))
        controller.version += 1

        def show(window):   # A hidden chat is shown so the attachment is where the message is written. Once the
            # card has closed: its modal operator would make the dock refuse ("finish the active tool").
            if window is not None and chat_toggle.chat_state(
                    window, live_windows.every_window(bpy.context.window_manager))[0] == "hidden":
                chat_toggle.show(window)
            return None
        step = live_windows.deferred(lambda: bpy.context.window_manager, self.window, show, settle=True)
        bpy.app.timers.register(step, first_interval=0.1)

    def in_scene(self, action):
        from . import live_windows, media_place as place
        window = live_windows.find(bpy.context.window_manager, self.window)
        area = next((a for a in live_windows.areas(window) if a.as_pointer() == self.area), None) if window else None
        region = next((r for r in area.regions if r.as_pointer() == self.region), None) if area else None
        if region is None or window.scene.as_pointer() != self.scene:
            raise RuntimeError(n_("The 3D view the photo was dropped on has closed"))
        item = place.Item("IMAGE", photo_id(self.path), "", name=self.path.stem, picture=str(self.path))
        with bpy.context.temp_override(window=window, area=area, region=region):
            if bpy.context.mode != 'OBJECT' and bpy.context.active_object is not None:
                # As Blender's own Add leaves Edit Mode: an edit-mode Undo step would not hold the new data.
                bpy.ops.object.mode_set(mode='OBJECT')
            if action == choices.MATERIAL:
                obj = bpy.data.objects.get(self.target) if self.target else None
                if obj is None or not place.apply_picture_materials([obj], [item]):
                    raise ValueError(n_("Drop it on an object to make it that object's material"))
                message = UNDO_MATERIAL
            else:
                place.add_references(bpy.context, [item], self.point, self.rotation, self.across)
                message = UNDO_PICTURE
            bpy.ops.ed.undo_push(message=message)   # A script's operator pushes none: one step to undo the choice.


class Preview:
    """The card's thumbnail: a private, downscaled copy of the photo, freed when the card closes or a choice runs."""

    def __init__(self, path):
        self.path, self.entry = path, None

    def painter(self):
        import gpu
        try:
            image = bpy.data.images.load(str(self.path), check_existing=False)
        except RuntimeError:   # Not an image Blender reads: the card says so when a choice needs it.
            return None
        try:
            width, height = image.size
            if not width or not height or width * height > 64 * 1024 * 1024:
                raise ValueError
            if max(width, height) > PREVIEW_SIZE:
                factor = PREVIEW_SIZE / max(width, height)
                image.scale(max(1, int(width * factor)), max(1, int(height * factor)))
            self.entry = (image, gpu.texture.from_image(image), (width, height))
        except (ValueError, RuntimeError):
            bpy.data.images.remove(image)
            return None
        return self.paint

    def paint(self, rect):
        from . import canvas_rtl
        if self.entry is None:
            return
        _image, texture, (width, height) = self.entry
        x, y, w, h = rect
        fit = min(w / width, h / height)
        dw, dh = width * fit, height * fit
        with canvas_rtl.upright(x + (w - dw) / 2, dw) as left:
            _draw_texture(texture, (left, y + (h - dh) / 2, dw, dh))

    def release(self):
        from .studio_previews import release_preview
        entry, self.entry = self.entry, None
        release_preview(entry, bpy.data.images)


_shader = None


def _draw_texture(texture, rect):
    global _shader
    import gpu
    from gpu_extras.batch import batch_for_shader
    if _shader is None:   # The texture samples linear (an sRGB picture is decoded): back to sRGB as it is drawn.
        _shader = gpu.shader.from_builtin('IMAGE_SCENE_LINEAR_TO_REC709_SRGB')
    x, y, w, h = rect
    batch = batch_for_shader(_shader, 'TRIS', {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                                               "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]},
                             indices=[(0, 1, 2), (0, 2, 3)])
    gpu.state.blend_set('ALPHA')
    _shader.bind()
    _shader.uniform_sampler("image", texture)
    batch.draw(_shader)


CLASSES = (ANGELO_OT_photo_drop, ANGELO_FH_photo_drop, ANGELO_FH_movie_empty)


def register():
    global _stock
    from . import chat_destinations, photo_drop_jobs
    _stock = getattr(bpy.types, "VIEW3D_FH_empty_image", None)
    if _stock is not None:   # Its images are the card's Add as a picture; its movies ANGELO_FH_movie_empty's.
        bpy.utils.unregister_class(_stock)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    photo_drop_jobs.register()
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "photo_to_3d", MENU_LABEL, MENU_WHY, glyph="studio", order=12, section=chat_destinations.CREATE,
        operator="angelo.photo_drop", owner="photo_drop"))


def unregister():
    global _stock
    from . import chat_destinations, photo_drop_jobs
    chat_destinations.unregister("photo_to_3d")
    photo_drop_jobs.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _stock is not None and getattr(bpy.types, "VIEW3D_FH_empty_image", None) is None:
        bpy.utils.register_class(_stock)
    _stock = None
    _last_model.clear()
