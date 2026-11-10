# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The model picker's operators: choose a model, refresh the list, a native Claude model ID, reasoning effort, Fast.

The chat's composer opens the model popover (``chat_view.show_popover(context, "models")``, drawn by
``chat_popovers``); ``ANGELO_MT_models`` is Blender's own menu for contexts without a chat canvas. The chat list and
connections live in ``ui`` (imported when an operator runs, since ``ui`` registers these classes).
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator

from . import chat_popovers, chat_view
from .chat_settings import clean_options, selected_model
from .display_text import error_message
from .ui_kit import paragraph
from .ui_text import iface_, rpt_


class ANGELO_OT_model_picker(Operator):
    bl_idname = "angelo.model_picker"
    bl_label = "Choose Model"
    bl_description = "Models advertised by the selected provider runtime, not a hard-coded OpenAI model list"

    def invoke(self, context, event):
        from . import ui
        if not ui.connection_available() and not ui.ready_connections():
            return bpy.ops.angelo.settings()
        if not ui.get_controller().busy and ui.connection_available():
            ui.prepare_controller()
        controller = ui.get_controller()
        ui.refresh_connected_models()
        if not controller.models and not controller.models_loading:
            controller.refresh_models()
        if chat_view.show_popover(context, "models"):
            return {'FINISHED'}
        # Non-chat contexts retain Blender's accessible native menu fallback.
        # The composer never uses this combined stock menu.
        return bpy.ops.wm.call_menu('INVOKE_DEFAULT', name="ANGELO_MT_models")

    def draw(self, context):
        from . import ui
        controller = ui.get_controller()
        layout = self.layout
        layout.label(text="Model")
        if controller.busy:
            layout.label(text="Model and effort changes apply to the next turn.")
        current = clean_options(controller.state.get("options"))["model"]
        if controller.models_loading:
            layout.label(text="Loading models from your connection…")
        from .chat_popovers import model_rows
        for row in model_rows(ui.model_choices(controller), current, controller.selection_connection_id):
            if row["kind"] == "provider":  # Provider and model names are data; the empty row is translated.
                layout.separator()
                layout.label(text=row["name"], translate=False)
                continue
            if row["kind"] == "empty":
                layout.label(text=row["name"], translate=False)
                continue
            operator = layout.operator("angelo.select_model", text=row["name"] + (" · " + row["subtitle"] if row["kind"] != "default" and row["subtitle"] else ""), icon='CHECKMARK' if row["selected"] else 'NONE', translate=False)
            if isinstance(row["value"], tuple):
                operator.connection_id, operator.model = row["value"]
            else:
                operator.model = row["value"]
        if controller.models_error:
            for line in chat_view.wrap(controller.models_error[:400], 330, 12, chat_view._font):
                layout.label(text=line[0])
        layout.separator()
        layout.operator("angelo.refresh_models", text="Refresh available models", icon='FILE_REFRESH')
        if controller.selection_provider.startswith("claude_"):
            layout.operator("angelo.custom_model", text="Enter native model ID…")
        layout.separator()
        model = selected_model(controller)
        efforts = model["efforts"] if model and model["efforts"] else ["low", "medium", "high"]
        layout.label(text="Reasoning effort · independent of Fast mode")
        for effort in efforts:
            layout.operator("angelo.set_effort", text=chat_popovers.effort_name(effort), translate=False, icon='CHECKMARK' if effort == clean_options(controller.state.get("options"))["effort"] else 'NONE').effort = effort


class ANGELO_MT_models(Menu):
    bl_label = "Model & Reasoning"

    def draw(self, context):
        ANGELO_OT_model_picker.draw(self, context)


class ANGELO_OT_refresh_models(Operator):
    bl_idname = "angelo.refresh_models"
    bl_label = "Refresh Model List"

    def execute(self, context):
        from . import ui
        try:
            if not ui.get_controller().busy and ui.connection_available():
                ui.prepare_controller()
            if ui.ready_connections():
                ui.refresh_connected_models(force=True)
            else:
                ui.get_controller().refresh_models()
        except Exception as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_select_model(Operator):
    bl_idname = "angelo.select_model"
    bl_label = "Select Agent Model"
    model: StringProperty(options={'SKIP_SAVE'})
    connection_id: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        from . import ui
        return next((m["description"] for m in ui.model_choices(ui.get_controller()) if m["id"] == properties.model and (not properties.connection_id or m.get("connection_id") == properties.connection_id)), "Use the connection's default model")

    def execute(self, context):
        from . import ui
        controller = ui.get_controller()
        identity = self.connection_id or controller.selection_connection_id
        if identity:
            try:
                ui.choose_model_connection(controller, identity, self.model)
            except (RuntimeError, ValueError) as error:
                self.report({'ERROR'}, error_message(error))
                return {'CANCELLED'}
            return {'FINISHED'}
        if self.model and not any(m["id"] == self.model for m in controller.selection_models):
            self.report({'ERROR'}, rpt_("Refresh the models available to this connection"))
            return {'CANCELLED'}
        try:
            options = clean_options(controller.state.get("options"))
            model = next((m for m in controller.selection_models if m["id"] == self.model), next((m for m in controller.selection_models if m["default"]), None))
            effort = options["effort"]
            if model and model["efforts"] and effort not in model["efforts"]:
                effort = model["default_effort"] if model["default_effort"] in model["efforts"] else model["efforts"][0]
            controller.set_options(model=self.model, fast=options["fast"] and bool(model and model["fast_tier"]), effort=effort)
        except RuntimeError as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_custom_model(Operator):
    bl_idname = "angelo.custom_model"
    bl_label = "Native Claude Model ID"
    model: StringProperty(name="Model", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        self.layout.prop(self, "model")
        paragraph(self.layout, iface_("Claude Code validates access; no automatic fallback."), 330)

    def execute(self, context):
        from . import ui
        controller = ui.get_controller()
        if not controller.selection_provider.startswith("claude_") or not self.model.strip():
            return {'CANCELLED'}
        try:
            controller.set_options(model=self.model.strip(), fast=False)
        except RuntimeError as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_set_effort(Operator):
    bl_idname = "angelo.set_effort"
    bl_label = "Set Reasoning Effort"
    effort: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import ui
        try:
            ui.get_controller().set_options(effort=self.effort)
        except RuntimeError as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_toggle_fast(Operator):
    bl_idname = "angelo.toggle_fast"
    bl_label = "Fast Mode"
    bl_description = "Use the provider's paid lower-latency tier, when supported. This does not lower reasoning effort"

    def invoke(self, context, event):
        from . import ui
        controller = ui.get_controller()
        if clean_options(controller.state.get("options"))["fast"]:
            return self.execute(context)
        model = selected_model(controller)
        if not model or not model["fast_tier"]:
            self.report({'INFO'}, rpt_("Choose a model with an available Fast tier in the model selector first"))
            return bpy.ops.angelo.model_picker('INVOKE_DEFAULT')
        return context.window_manager.invoke_props_dialog(self, width=420, confirm_text=iface_("Enable Fast"))

    def draw(self, context):
        from . import ui
        paragraph(self.layout, iface_("Fast mode uses the provider's lower-latency service tier."), 370)
        paragraph(self.layout, iface_("It may consume additional quota or incur higher charges."), 370)
        if ui.get_controller().selection_provider.startswith("claude_"):
            paragraph(self.layout, iface_("Claude subscriptions: paid usage credits, not included usage."), 370)
        paragraph(self.layout, iface_("The provider controls eligibility and actual latency."), 370)

    def execute(self, context):
        from . import ui
        controller = ui.get_controller()
        options = clean_options(controller.state.get("options"))
        model = selected_model(controller)
        if not options["fast"] and (not model or not model["fast_tier"]):
            return {'CANCELLED'}
        try:
            controller.set_options(fast=not options["fast"])
        except RuntimeError as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (ANGELO_OT_model_picker, ANGELO_MT_models, ANGELO_OT_refresh_models, ANGELO_OT_select_model,
           ANGELO_OT_custom_model, ANGELO_OT_set_effort, ANGELO_OT_toggle_fast)
