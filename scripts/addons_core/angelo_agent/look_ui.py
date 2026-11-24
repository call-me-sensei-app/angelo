# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera Look: a lens and film look on the scene's camera (doc/angelo/camera_look.rst).

One operator, ``angelo.camera_look``: invoked by a person it opens the Camera Look card (``look_card``); its redo
panel shows the preset, breathing, the camera view and Match Exposure; it also runs from its properties alone (EXEC_DEFAULT: the agent's
``camera_look`` operation, ``look_ops``): ``stages`` and ``camera_values`` as JSON override the preset's. CLEAN with
nothing else takes the look off and puts back what it replaced. Reached from the chat bar's + menu and F3.
"""

import json

import bpy

from . import look_spec, look_system
from .ui_text import iface_, n_

TITLE = n_("Camera Look")
WHY = n_("A lens and film look for the camera: vignette, glare, grain, bokeh and grade")
GLYPH = "look"
# The looks' names in the context "Angelo": Blender's own "Clean" is the verb (clean up), not a look without one.
PRESET_ITEMS = (
    ("CLEAN", n_("Clean", "Angelo"), n_("No look: takes an applied look off")),
    ("VINTAGE", n_("Vintage", "Angelo"), ""), ("ANAMORPHIC", n_("Anamorphic", "Angelo"), ""),
    ("DOCUMENTARY", n_("Documentary", "Angelo"), ""), ("WARM_PORTRAIT", n_("Warm Portrait", "Angelo"), ""),
    ("BLEACH_BYPASS", n_("Bleach Bypass", "Angelo"), ""), ("NOIR", n_("Noir", "Angelo"), ""))


def _json(text, what):
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        raise look_system.LookError("%s is JSON" % what, code="VALIDATION") from None
    if not isinstance(value, dict):
        raise look_system.LookError("%s is an object" % what, code="VALIDATION")
    return value


class ANGELO_OT_camera_look(bpy.types.Operator):
    bl_idname = "angelo.camera_look"
    bl_label = TITLE
    bl_description = WHY
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(name="Look", items=PRESET_ITEMS, default="CLEAN", translation_context="Angelo")
    stages: bpy.props.StringProperty(name="Stages", default="", options={"SKIP_SAVE", "HIDDEN"},
                                     description="JSON: values per stage over the preset's (look_spec.STAGES)")
    camera_values: bpy.props.StringProperty(name="Camera Values", default="", options={"SKIP_SAVE", "HIDDEN"},
                                            description="JSON: aperture_blades, aperture_rotation, aperture_ratio")
    breathing: bpy.props.BoolProperty(name="Focus Breathing", default=False,
                                      description="The focal length follows the focus distance; while it does, the "
                                                  "focal length is set by the look")
    breathing_amount: bpy.props.FloatProperty(name="Breathing", default=0.0, min=-0.5, max=0.5,
                                              description="0: the preset's")
    exposure: bpy.props.StringProperty(name="Exposure", default="", options={"SKIP_SAVE", "HIDDEN"},
                                       description="JSON: reference and setting, each f_stop, shutter, iso")
    match_exposure: bpy.props.BoolProperty(name="Match Exposure", default=False,
                                           description="Exposure follows the camera's f-stop, shutter and ISO, in "
                                                       "stops from the settings the scene is lit for")
    shutter: bpy.props.FloatProperty(name="Shutter", default=1.0 / 50.0, min=1.0 / 64000.0, max=3600.0,
                                     precision=4, unit="TIME_ABSOLUTE", description="The camera's shutter time")
    iso: bpy.props.FloatProperty(name="ISO", default=100.0, min=1.0, max=1000000.0, precision=0,
                                 description="The camera's ISO")
    reference_f_stop: bpy.props.FloatProperty(name="Lit For f-stop", default=5.6, min=0.5, max=128.0,
                                              description="The f-stop the scene's light is right for")
    reference_shutter: bpy.props.FloatProperty(name="Lit For Shutter", default=1.0 / 50.0, min=1.0 / 64000.0,
                                               max=3600.0, precision=4, unit="TIME_ABSOLUTE",
                                               description="The shutter time the scene's light is right for")
    reference_iso: bpy.props.FloatProperty(name="Lit For ISO", default=100.0, min=1.0, max=1000000.0, precision=0,
                                           description="The ISO the scene's light is right for")
    camera: bpy.props.StringProperty(name="Camera", default="", options={"SKIP_SAVE"},
                                     description="The camera object (empty: the scene's)")
    show_in_viewport: bpy.props.BoolProperty(name="Show in Viewport", default=False,
                                             description="The 3D views' camera view runs the compositor")

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, event):
        """A person's Camera Look is the card (look_card), which applies through ``look_system.apply`` as this
        operator's EXEC path does; this run changes nothing, so it pushes no Undo step."""
        from . import card_host, look_card
        if not card_host.is_active("camera_look"):
            camera = bpy.data.objects.get(self.camera) if self.camera else context.scene.camera
            camera = camera if camera is not None and camera.type == "CAMERA" else None
            applied = json.loads(context.scene.get(look_system.STATE, "{}")).get("preset")
            card_host.request(look_card.LookCard(camera.name if camera else None,
                                                 camera.data.dof.aperture_fstop if camera else 5.6, applied))
            card_host.pump(lambda: True)
        return {"CANCELLED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "preset")
        layout.prop(self, "breathing")
        layout.prop(self, "show_in_viewport")
        layout.prop(self, "match_exposure")
        if self.match_exposure:
            column = layout.column(align=True)
            for name in ("shutter", "iso", "reference_f_stop", "reference_shutter", "reference_iso"):
                column.prop(self, name)

    def run(self, context):
        scene = context.scene
        camera = bpy.data.objects.get(self.camera) if self.camera else scene.camera
        if camera is None or camera.type != "CAMERA":
            raise look_system.LookError("A look is a camera's; name a camera or set the scene's", code="VALIDATION")
        amount = 0.0
        if self.breathing:
            amount = self.breathing_amount or look_spec.PRESETS[self.preset].get("camera", {}).get("breathing", 0.0)
            if not amount:
                raise look_system.LookError("The %s look has no breathing; give breathing_amount" % self.preset,
                                            code="VALIDATION")
        exposure = _json(self.exposure, "exposure")
        if not exposure and self.match_exposure:   # The dialog's fields: the camera's f-stop against the reference.
            exposure = {"reference": {"f_stop": self.reference_f_stop, "shutter": self.reference_shutter,
                                      "iso": self.reference_iso},
                        "setting": {"shutter": self.shutter, "iso": self.iso}}
        return look_system.apply(scene, camera, self.preset, _json(self.stages, "stages"),
                                 _json(self.camera_values, "camera_values"), amount, self.show_in_viewport,
                                 screens=[context.screen] if self.show_in_viewport and context.screen else (),
                                 exposure=exposure or None)

    def execute(self, context):
        try:
            receipt = self.run(context)
        except look_spec.LookError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if receipt.get("group") is None:
            self.report({"INFO"}, iface_("No look on {camera}", camera=receipt["camera"]))
        else:
            from .look_card import PRESET_LABELS, STAGE_LABELS   # The card's names, translated as it shows them.
            self.report({"INFO"}, iface_("{look} look on {camera}: {stages}",
                                         look=iface_(PRESET_LABELS[receipt["preset"]]), camera=receipt["camera"],
                                         stages=", ".join(iface_(STAGE_LABELS[name]) for name in receipt["stages"])))
        return {"FINISHED"}


CLASSES = (ANGELO_OT_camera_look,)


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "camera_look", TITLE, WHY, glyph=GLYPH, order=25,   # In Render tools, after Bake maps.
        operator=ANGELO_OT_camera_look.bl_idname, owner="look_ui"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("camera_look")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
