# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's operators: references and imports (files, a URL, a drop), folders, the prompt editor, Generate and Enhance.

Split from ``studio.py`` (its 500-line limit); ``studio`` owns the state and re-exports these names."""

import threading
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, EnumProperty, StringProperty
from bpy.types import FileHandler, Operator, OperatorFileListElement

from . import studio as core   # Rebound state (_jobs) is read through the module.
from .provider_registry import is_coding
from .studio_generation import GenerationError, enhance_gemini, public_url
from .studio_state import error_text
from .ui_text import iface_, n_, plural_
from .studio_input import state_for
from .studio import (  # noqa: E402 (studio imports this module after defining these)
    _enhancement_connections, _enhancement_events, _enhancements, attach, is_studio, library, redraw, selected)


class ANGELO_OT_studio_files(Operator):
    bl_idname = "angelo.studio_files"
    bl_label = "Add Images"
    purpose: EnumProperty(items=[("REFERENCE", "References", ""), ("IMPORT", "Import to library", "")])
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH')
    filepath: StringProperty(subtype='FILE_PATH')
    files: CollectionProperty(type=OperatorFileListElement)
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.webp", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.area_pointer:
            self.area_pointer = str(context.area.as_pointer())
        state_for(self.area_pointer)
        if self.filepath or self.files:
            return self.execute(context)  # Native OS/FileHandler drop.
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            state = state_for(self.area_pointer)
            paths = [str(Path(self.directory) / f.name) for f in self.files] or [self.filepath]
            if self.purpose == "REFERENCE":
                attach(state, paths)
            else:
                folder = "" if state["folder"] in {"ALL", "UNFILED"} else state["folder"]
                for path in paths:
                    library().import_image(path, folder)
                state["notice"] = n_("Images imported to the local library. No provider was called.")
            redraw()
            return {'FINISHED'}
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_FH_studio(FileHandler):
    bl_idname = "ANGELO_FH_studio"
    bl_label = "Media Studio References"
    bl_import_operator = "angelo.studio_files"
    bl_file_extensions = ".png;.jpg;.jpeg;.webp"  # i18n: file extension filter, not text

    @classmethod
    def poll_drop(cls, context):
        return is_studio(context.area)


class ANGELO_OT_studio_url(Operator):
    bl_idname = "angelo.studio_url"
    bl_label = "Add Image URL"
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    url: StringProperty(name="Image URL")

    def invoke(self, context, event):
        state_for(self.area_pointer)
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        self.layout.prop(self, "url", text="")
        self.layout.label(text="Public HTTPS image link. Downloaded only when you generate.")

    def execute(self, context):
        try:
            parsed = public_url(self.url.strip())
            state = state_for(self.area_pointer)
            if len(state["references"]) >= 4:
                raise ValueError(n_("Use up to four references"))
            import uuid
            state["references"].append({"id": str(uuid.uuid4()), "url": self.url.strip(), "name": parsed.hostname + parsed.path[:50]})
            redraw()
            return {'FINISHED'}
        except (ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_folder(Operator):
    bl_idname = "angelo.studio_folder"
    bl_label = "New Folder"
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    name: StringProperty(name="Folder name")

    def invoke(self, context, event):
        state_for(self.area_pointer)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        try:
            folder = library().folder(self.name)
            state_for(self.area_pointer).update(folder=folder["id"], grid_scroll=0)
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_prompt(Operator):
    bl_idname = "angelo.studio_prompt"
    bl_label = "Prompt Editor"
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    prompt: StringProperty(name="Prompt", translation_context="Angelo")
    enhanced: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        state = state_for(self.area_pointer)
        state.update(prompt_dialog={"enhanced": self.enhanced}, expanded_prompt=self.prompt,
                     focus="expanded_prompt", caret=len(self.prompt), selection=None)
        redraw()
        return {'FINISHED'}

    def draw(self, context):
        self.layout.label(text="Improved prompt · original kept for Undo" if self.enhanced else "Generation prompt")
        self.layout.prop(self, "prompt", text="")
        paragraph_lines = self.prompt.splitlines() or [""]
        for line in paragraph_lines[:14]:
            self.layout.label(text=line[:85])

    def execute(self, context):
        state = state_for(self.area_pointer)
        if self.enhanced:
            state["original_prompt"] = state["prompt"]
            state["enhanced"] = None
        state["prompt"] = self.prompt[:16000]
        redraw()
        return {'FINISHED'}


class ANGELO_OT_studio_generate(Operator):
    bl_idname = "angelo.studio_generate"
    bl_label = "Generate with API Credits"
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        from . import ui
        try:
            state = state_for(self.area_pointer)
            model = selected(state)
            if not model or not model["ready"]:
                raise ValueError(n_("Connect this model's API account in AI Providers"))
            if not state["prompt"].strip() and (state["kind"] == "IMAGE" or not state["references"]):
                raise ValueError(n_("Describe what you want to generate"))
            ui.generation_binding(model["connection"])
            from .studio_generation import validate_generation
            validate_generation(model["mode"], model["id"], state["prompt"], state, state["references"])
            self._snapshot = {k: state[k] for k in ("kind", "prompt", "aspect", "resolution", "quality", "background", "size", "polycount", "texture", "folder")}
            self._snapshot["references"] = [dict(r) for r in state["references"]]
            self._model = dict(model)
            return context.window_manager.invoke_props_dialog(self, width=440, confirm_text=iface_("Generate"))
        except (ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}

    def draw(self, context):
        model, state = self._model, self._snapshot
        self.layout.label(text=iface_("{model} · {account}", model=model["name"], account=model["account"]), translate=False)
        self.layout.label(text="Uses your team's provider key through Angelo." if model.get("team") else "Billed to this API account, not your coding subscription.")
        self.layout.label(text=plural_(len(state["references"]), "Upload {count} image reference. One output requested.", "Upload {count} image references. One output requested."), translate=False)
        if model["mode"] == "meshy_api" and state["texture"] and not state["references"]:
            self.layout.label(text="Two billable stages: geometry preview + textured refine.", icon='INFO')
        if not model["verified"] and not model.get("team"):
            self.layout.label(text=iface_("Key/model access has not been verified yet."), icon='ERROR', translate=False)
        self.layout.label(text="Nothing is inserted into the scene automatically.")

    def execute(self, context):
        from . import ui
        try:
            if not hasattr(self, "_snapshot"):
                raise ValueError(n_("Generate must be confirmed in Media Studio"))
            state, model = self._snapshot, self._model
            # Recheck the exact account/model after confirmation. Don't use the
            # stale key cached in a dialog if the user removed/replaced it.
            binding = ui.generation_binding(model["connection"])
            if len(core._jobs.active) >= 4:
                raise ValueError(n_("Four generations are already running"))
            folder = state["folder"] if state["folder"] not in {"ALL", "UNFILED"} else ""
            asset = library().add(state["kind"], state["prompt"].strip(), model["name"], model["connection"], folder,
                mode=model["mode"], model_id=model["id"], options={k: state[k] for k in ("aspect", "resolution", "quality", "background", "size", "polycount", "texture")},
                reference_names=[r["name"] for r in state["references"]])
            core._jobs.start(asset, binding, asset["options"], state["references"])
            redraw()
            return {'FINISHED'}
        except (OSError, ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


def enhancement_binding():
    from . import ui
    config = library().enhancer
    identity = config["connection"]
    if identity == "AUTO_GEMINI":
        records = [r for r in ui.generation_connections() if ui.gemini_text_ready(r)]
        if len(records) != 1:
            raise ValueError(n_("Add a Gemini API key in AI Providers") if not records else n_("Choose which Gemini account AI Enhance should use in AI Providers"))
        record = records[0]
    else:
        record = ui.find_connection(identity)
        if not record:
            raise ValueError(n_("The selected enhancer connection is missing. Open AI Providers."))
    if not is_coding(record):
        if not ui.gemini_text_ready(record):
            raise ValueError(n_("Choose Gemini or a connected coding model for AI Enhance"))
    elif not ui.connection_ready(record):
        raise ValueError(n_("Reconnect the selected enhancer in AI Providers"))
    return record, dict(config)


class ANGELO_OT_studio_enhance(Operator):
    bl_idname = "angelo.studio_enhance"
    bl_label = "Enhance Prompt"
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            record, config = enhancement_binding()
            self._name = iface_("{account} · {model}", account=record["name"], model=config["model"])
            state_for(self.area_pointer)
            return context.window_manager.invoke_props_dialog(self, width=420, confirm_text=iface_("Enhance prompt"))
        except ValueError as error:
            state_for(self.area_pointer)["notice"] = str(error)
            redraw()
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}

    def draw(self, context):
        self.layout.label(text=self._name, translate=False)
        self.layout.label(text="Uses this account's credits or subscription allowance.")
        self.layout.label(text="Text only. Original prompt preserved; review before applying.")

    def execute(self, context):
        from . import ui
        from .studio_enhance import enhance_native
        try:
            state = state_for(self.area_pointer)
            if state.get("enhancing") or not state["prompt"].strip():
                raise ValueError(n_("Enter a prompt and wait for any running enhancement"))
            record, config = enhancement_binding()
            model = selected(state)
            prompt, kind = state["prompt"], state["kind"]
            generation_model = model["name"] if model else kind
            key = ui._memory_keys.get(record["id"], "")
            access = None if is_coding(record) else ui.gemini_text_binding(record)
            settings, models = (ui.connection_settings(record["mode"], record=record), ui.catalogs().entries.get(record["id"], [])) if is_coding(record) else ({}, [])
            cancel = threading.Event()
            _enhancements[self.area_pointer] = cancel
            _enhancement_connections[self.area_pointer] = record["id"]
            state["enhancing"] = True
            pointer, directory, transport = str(self.area_pointer), library().directory, core._jobs.transport

            def run():
                try:
                    reply = (enhance_native(directory, record, settings, key, config["model"], models, prompt, kind, generation_model, cancel)
                        if is_coding(record) else enhance_gemini(access, config["model"], prompt, kind, generation_model, transport))
                    result = {"enhanced": reply, "notice": n_("Enhanced prompt ready. Review & apply to replace your draft.")}
                except Exception as error:
                    message = access.redact(str(error)) if access is not None else str(error).replace(key, "[REDACTED]") if key else str(error)
                    result = {"notice": message[:350] or n_("Enhancement failed; original prompt unchanged")}
                if not cancel.is_set():
                    _enhancement_events.put((pointer, result))
            threading.Thread(target=run, daemon=True, name="AngeloPromptEnhance").start()
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
