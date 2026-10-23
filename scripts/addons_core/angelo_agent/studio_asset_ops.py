# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's asset operators: the enhancer settings, an asset's details and actions, importing a 3D result and exporting.

Split from ``studio.py`` (its 500-line limit); ``studio`` owns the state and re-exports these names."""

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import studio as core   # Rebound state (_jobs) is read through the module.
from .provider_registry import PROVIDER_NAMES, is_coding
from .studio_state import KIND_LABELS, error_text, model_label, status_title
from .ui_text import iface_, n_, rpt_
from .studio_input import state_for
from .studio import (  # noqa: E402 (studio imports this module after defining these)
    _enum_cache, attach, library, redraw)


_enhancer_items, _enhancer_model_items, _folder_items = [], [], []


def enhancer_accounts(self, context):
    global _enhancer_items
    from . import ui
    _enhancer_items = [("AUTO_GEMINI", n_("Gemini · shared Nano Banana key"), n_("Use the sole configured Gemini image account; never a separate key"))]
    for record in ui.connection_records().values():
        if record["mode"] in {"gemini_images_api", "gemini_images_team"} or is_coding(record):
            _enhancer_items.append((record["id"], iface_("{account} · {provider}", account=record["name"], provider=PROVIDER_NAMES[record["provider"]]), n_("Use this exact named connection")))
    key = ("enhancer_accounts", tuple(_enhancer_items))
    return _enum_cache.setdefault(key, _enhancer_items)


def enhancer_models(self, context):
    global _enhancer_model_items
    from . import ui
    record = ui.find_connection(self.connection)
    if not record or record["mode"] in {"gemini_images_api", "gemini_images_team"}:
        # Explicit custom text-model field covers future Gemini models without
        # falsely advertising an unverified entitlement or changing keys.
        _enhancer_model_items = [("gemini-3.8-flash", n_("Gemini Flash 3.8 (Default)"), n_("Uses the same Gemini API key as Nano Banana")),
                                 ("CUSTOM", n_("Other Gemini text model…"), n_("Enter its exact API model ID"))]
    else:
        models = ui.catalogs().entries.get(record["id"], [])
        _enhancer_model_items = [(m["id"], m["name"], n_("Connected native model")) for m in models]
        if not _enhancer_model_items:
            _enhancer_model_items = [("UNAVAILABLE", n_("Refresh models in Coding Agent"), n_("No model catalog available yet"))]
    key = ("enhancer_models", tuple(_enhancer_model_items))
    return _enum_cache.setdefault(key, _enhancer_model_items)


def enhancer_account_changed(self, context):
    self.model = enhancer_models(self, context)[0][0]


class ANGELO_OT_enhancer_settings(Operator):
    bl_idname = "angelo.enhancer_settings"
    bl_label = "AI Enhance Settings"
    connection: EnumProperty(name="Connection", items=enhancer_accounts, update=enhancer_account_changed)
    model: EnumProperty(name="Model", items=enhancer_models)
    custom_model: StringProperty(name="Gemini text model ID")

    def invoke(self, context, event):
        config = library().enhancer
        valid = {i[0] for i in enhancer_accounts(self, context)}
        self.connection = config["connection"] if config["connection"] in valid else "AUTO_GEMINI"
        valid_models = {i[0] for i in enhancer_models(self, context)}
        self.model = config["model"] if config["model"] in valid_models else "CUSTOM" if "CUSTOM" in valid_models else next(iter(valid_models))
        self.custom_model = config["model"]
        return context.window_manager.invoke_props_dialog(self, width=510)

    def draw(self, context):
        self.layout.prop(self, "connection")
        self.layout.prop(self, "model")
        self.layout.operator("angelo.enhancer_refresh", text="Refresh connected coding models", icon='FILE_REFRESH')
        if self.model == "CUSTOM":
            self.layout.prop(self, "custom_model")
        self.layout.label(text="Default: Gemini Flash 3.8 · same API key as Nano Banana.")
        self.layout.label(text="No automatic account/billing fallback. Prompts are reviewed before applying.")

    def execute(self, context):
        try:
            model = self.custom_model.strip() if self.model == "CUSTOM" else self.model
            if model == "UNAVAILABLE":
                raise ValueError(n_("Refresh the connected coding models first"))
            library().configure_enhancer(self.connection, model)
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_enhancer_refresh(Operator):
    bl_idname = "angelo.enhancer_refresh"
    bl_label = "Refresh Connected Models"

    def execute(self, context):
        from . import ui
        ui.refresh_connected_models(force=True)
        ui.preferences_ui.redraw()
        return {'FINISHED'}


def draw_enhancer_settings(layout):
    box = layout.box()
    row = box.row()
    row.label(text="AI Enhance", icon='SHADERFX')
    row.operator("angelo.enhancer_settings", text="Configure…")
    config = library().enhancer
    from . import ui
    name = iface_("Gemini · shared Nano Banana key") if config["connection"] == "AUTO_GEMINI" else (ui.find_connection(config["connection"]) or {}).get("name", iface_("Reconnect selected account"))
    box.label(text=iface_("{account} · {model}", account=name, model=config["model"]), translate=False)
    box.label(text="Prompt enhancement only. API keys stay in their existing connections.")


def asset_folders(self, context):
    global _folder_items
    _folder_items = [("UNFILED", n_("Unfiled"), "")] + [(r["id"], r["name"], "") for r in library().folders.values()]
    key = ("folders", tuple(_folder_items))
    return _enum_cache.setdefault(key, _folder_items)


class ANGELO_OT_studio_asset(Operator):
    bl_idname = "angelo.studio_asset"
    bl_label = "Asset Details"
    asset_id: StringProperty(options={'SKIP_SAVE'})
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    folder: EnumProperty(name="Folder", items=asset_folders)

    def invoke(self, context, event):
        asset = library().assets.get(self.asset_id)
        if not asset:
            return {'CANCELLED'}
        state_for(self.area_pointer)
        self.folder = asset.get("folder") or "UNFILED"
        return context.window_manager.invoke_props_dialog(self, width=500, confirm_text=iface_("Save folder"))

    def draw(self, context):
        asset = library().assets[self.asset_id]
        layout = self.layout
        layout.label(text=iface_("{model} · {kind}", model=model_label(asset["model"]), kind=iface_(KIND_LABELS.get(asset["kind"], asset["kind"]))), translate=False)
        layout.label(text=status_title(asset) if asset["status"] in {"ready", "failed", "stopped"} else iface_("{status} · {percent}%", status=status_title(asset), percent=asset.get("progress", 0)), translate=False)
        for line in asset["prompt"].splitlines()[:6]:
            layout.label(text=line[:85])
        if asset.get("error"):
            for line in [text[i:i + 80] for text in (iface_(asset["error"]),) for i in range(0, len(text), 80)]:
                layout.label(text=line, icon='ERROR', translate=False)
        if asset.get("task_id"):
            layout.label(text=iface_("Provider task: {task_id}", task_id=asset["task_id"]), translate=False)
        if asset["status"] == "ready":
            # What dragging the item does, as menu items (the keyboard's and the agent's way): media_drop.
            row = layout.row()
            row.operator("angelo.media_add_to_scene", icon='ADD').item_ids = self.asset_id
            if asset["kind"] == "IMAGE":
                row.operator("angelo.media_apply_to_selected", icon='MATERIAL').item_ids = self.asset_id
            row.operator("angelo.media_add_to_chat", icon='PASTEDOWN').item_ids = self.asset_id
            row = layout.row()
            if asset["kind"] == "IMAGE":
                op = row.operator("angelo.studio_asset_action", text="Use as reference", icon='IMAGE')
                op.action, op.asset_id, op.area_pointer = "REFERENCE", self.asset_id, self.area_pointer
            op = row.operator("angelo.studio_export", text="Export…", icon='EXPORT')
            op.asset_id = self.asset_id
            layout.label(text=iface_("Drag items into the 3D view, onto objects, the Outliner, the chat or a node editor"),
                         translate=False)
        if self.asset_id in core._jobs.active:
            op = layout.operator("angelo.studio_asset_action", text="Stop local monitoring")
            op.action, op.asset_id = "STOP", self.asset_id
            layout.label(text="Submitted provider tasks may still finish and be billed.")
        layout.prop(self, "folder")

    def execute(self, context):
        asset = dict(library().assets[self.asset_id], folder="" if self.folder == "UNFILED" else self.folder)
        library().update(asset)
        redraw()
        return {'FINISHED'}


class ANGELO_OT_studio_asset_action(Operator):
    bl_idname = "angelo.studio_asset_action"
    bl_label = "Use Asset"
    action: StringProperty(options={'SKIP_SAVE'})
    asset_id: StringProperty(options={'SKIP_SAVE'})
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            if self.action == "STOP":
                core._jobs.stop(self.asset_id)
            elif self.action == "REFERENCE":
                asset = library().assets[self.asset_id]
                path = library().checked_file(asset, asset["files"][0])
                attach(state_for(self.area_pointer), [str(path)])
            redraw()
            return {'FINISHED'}
        except (ValueError, OSError, KeyError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_import_3d(Operator):
    bl_idname = "angelo.studio_import_3d"
    bl_label = "Import Generated 3D Asset"
    bl_options = {'UNDO'}
    asset_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title=iface_("Import 3D asset into the current scene?"),
            message=iface_("Adds the local GLB with native Blender import. Blender Undo can revert the import."), confirm_text=iface_("Import"))

    def execute(self, context):
        try:
            asset = library().assets[self.asset_id]
            path = library().checked_file(asset, "model.glb")
            from .studio_generation import checked_glb
            checked_glb(path.read_bytes())
            bpy.ops.import_scene.gltf(filepath=str(path))
            self.report({'INFO'}, rpt_("3D asset imported. Use Blender Undo to revert."))
            return {'FINISHED'}
        except (KeyError, ValueError, OSError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_export(Operator):
    bl_idname = "angelo.studio_export"
    bl_label = "Export Asset"
    asset_id: StringProperty(options={'SKIP_SAVE'})
    filepath: StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        asset = library().assets[self.asset_id]
        self.filepath = "Angelo-" + self.asset_id[:8] + Path(asset["files"][0]).suffix
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import shutil
        try:
            asset = library().assets[self.asset_id]
            source = library().checked_file(asset, asset["files"][0])
            target = Path(self.filepath)
            if not target.is_absolute() or target.exists():
                raise ValueError(n_("Choose a new absolute output path; existing files are never overwritten"))
            shutil.copyfile(source, target)
            return {'FINISHED'}
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
