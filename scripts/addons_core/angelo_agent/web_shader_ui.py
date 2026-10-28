# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The material's Web Shader panel (Properties > Material): the shader, its file and its uniforms.

The uniforms are ordinary sliders and colour fields. The panel says plainly that the browser shows the real look
and that Blender shows the material's nodes as the stand-in; Live Web Preview is one click away.
"""

import bpy

from . import web_preview, web_shader
from .ui_text import iface_, n_, rpt_

STAND_IN = (n_("Your browser shows this look in Live Web Preview."),
            n_("Angelo shows the material's nodes as the stand-in."))
PROBLEMS = {"missing": n_("The shader file isn't there"), "tsl": n_("TSL web shaders arrive in 1.1; use a GLSL file"), "language": n_("Use a GLSL file (.glsl or .frag)"),
            "too_large": n_("The shader file is larger than 256 KB"), "unreadable": n_("The shader file can't be read")}
READ = n_("Read {count} uniforms from the shader file")
# The built-ins' uniforms by name (a file's uniforms are its own names, shown as they are).
LABELS = {"base_color": n_("Base Color"), "shadow_color": n_("Shadow Color"), "bands": n_("Bands"),
          "outline": n_("Outline"), "rim_color": n_("Rim Color"), "rim_power": n_("Rim Power"),
          "rim_strength": n_("Rim Strength"), "color": n_("Color"), "lines": n_("Scan Lines"), "speed": n_("Speed"),
          "opacity": n_("Opacity"), "flicker": n_("Flicker")}


class ANGELO_OT_web_shader_uniforms(bpy.types.Operator):
    bl_idname = "angelo.web_shader_uniforms"
    bl_label = "Read Uniforms"
    bl_description = "Read the uniforms the shader file declares, keeping the values of the ones it already had"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        material = getattr(context, "material", None)
        return material is not None and material.angelo_web_shader.kind == "FILE"

    def execute(self, context):
        settings = context.material.angelo_web_shader
        problem = web_shader.read_uniforms(settings, context.material)
        if problem:
            self.report({'WARNING'}, rpt_(PROBLEMS[problem]))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_(READ, count=len(settings.uniforms)))
        return {'FINISHED'}


class ANGELO_PT_web_shader(bpy.types.Panel):
    bl_label = "Web Shader"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return getattr(context, "material", None) is not None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        material = context.material
        settings = material.angelo_web_shader
        layout.prop(settings, "kind")
        if settings.kind == "NONE":
            return
        if settings.kind == "FILE":
            layout.prop(settings, "path")
            problem = web_shader.file_problem(settings, material) if settings.path else None
            if problem:
                layout.label(text=iface_(PROBLEMS[problem]), icon='INFO', translate=False)
            layout.operator(ANGELO_OT_web_shader_uniforms.bl_idname, icon='FILE_REFRESH')
        column = layout.column(align=True)
        for item in settings.uniforms:
            label = iface_(LABELS[item.name]) if settings.kind != "FILE" and item.name in LABELS else item.name
            column.prop(item, "color" if item.kind == "COLOR" else "value", text=label, translate=False)
        box = layout.box()
        for line, icon in zip(STAND_IN, ('WORLD', 'BLANK1')):
            box.label(text=iface_(line), icon=icon, translate=False)
        row = box.row()
        if web_preview.running():
            row.label(text=iface_("Live Web Preview is open"), icon='CHECKMARK', translate=False)
        else:
            row.operator("angelo.web_preview", icon='WORLD')


CLASSES = (ANGELO_OT_web_shader_uniforms, ANGELO_PT_web_shader)


def register():
    web_shader.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    web_shader.unregister()
