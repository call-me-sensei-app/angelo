# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for a person's render engine add-on (V-Ray, BlendLuxCore, Octane's add-on) in GUI suites.

It lives in the test folder, outside the shipped scripts, so Angelo tells it from Blender's own engines exactly as it
tells a real add-on (``render_extensions.addon_engines``). Its final render fills the image with ``COLOUR``; it draws
no viewport. Registering it is what enabling an add-on does.
"""

import bpy

IDNAME, LABEL = "ANGELO_QA_ENGINE", "QA Flat Engine"
COLOUR = (0.8, 0.3, 0.1)


class QAFlatEngine(bpy.types.RenderEngine):
    bl_idname, bl_label = IDNAME, LABEL
    bl_use_preview = False

    def render(self, depsgraph):
        scale = depsgraph.scene.render.resolution_percentage / 100.0
        width = int(depsgraph.scene.render.resolution_x * scale)
        height = int(depsgraph.scene.render.resolution_y * scale)
        result = self.begin_result(0, 0, width, height)
        result.layers[0].passes["Combined"].rect = [(*COLOUR, 1.0)] * (width * height)
        self.end_result(result)


def register():
    bpy.utils.register_class(QAFlatEngine)


def unregister():
    bpy.utils.unregister_class(QAFlatEngine)
