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
from .display_text import describe_error
from .studio_state import error_text
from .ui_kit import paragraph   # Dialog sentences wrap, so a longer translation is never clipped.
from .ui_text import iface_, n_, plural_
from .studio_input import state_for
from .studio import (  # noqa: E402 (studio imports this module after defining these)
    _enhancement_connections, _enhancement_events, _enhancements, attach, is_studio, library, redraw, selected)

ENHANCE_FAILED = n_("Enhancement failed; original prompt unchanged")


class ANGELO_OT_studio_files(Operator):
    bl_idname = "angelo.studio_files"
    bl_label = "Add Images"
    purpose: EnumProperty(items=[("REFERENCE", "References", ""), ("IMPORT", "Import to library", ""),
                                 ("FRAME_FIRST", "First frame", ""), ("FRAME_LAST", "Last frame", "")])
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
            elif self.purpose.startswith("FRAME_"):
                from . import studio_video_ui
                studio_video_ui.set_frame(state, "video_" + self.purpose.removeprefix("FRAME_").lower(), paths)
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
        paragraph(self.layout, iface_("Public HTTPS image link. Downloaded only when you generate."), 390)

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
                     field_focus="expanded_prompt", caret=len(self.prompt), selection=None)
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
            if state["kind"] == "VIDEO":
                return self._invoke_video(context, state, model)
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

    def _invoke_video(self, context, state, model):
        """A video's confirmation: everything checked first (fal_video.validate), with its price."""
        from . import fal_video, flow_prices, studio_video_ui
        from .ui import generation_binding
        generation_binding(model["connection"])
        options, inputs = studio_video_ui.options(state, model), studio_video_ui.inputs(state)
        fal_video.validate_any(model["id"], state["prompt"], options, inputs)
        price = flow_prices.estimate(model["mode"], model["id"], studio_video_ui.price_options(state, model),
                                     len(inputs["references"]))
        self._snapshot = {"kind": "VIDEO", "prompt": state["prompt"], "folder": state["folder"], "options": options,
                          "inputs": inputs, "price": price, "price_line": studio_video_ui.price_line(state, model),
                          "references": [r for r in (inputs["first"], inputs["last"], *inputs["references"],
                                                     *inputs["videos"]) if r]}
        self._model = dict(model)
        return context.window_manager.invoke_props_dialog(self, width=460, confirm_text=iface_("Generate video"))

    def _draw_video(self, model, state):
        layout = self.layout
        layout.label(text=iface_("{model} · {account}", model=model["name"], account=model["account"]), translate=False)
        layout.label(text=state["price_line"], translate=False)
        paragraph(layout, iface_("Uses your team's fal key through Angelo.") if model.get("team") else iface_("Billed to this fal account by fal."), 400)
        sent = len(state["references"])
        if sent:
            paragraph(layout, plural_(sent, "Uploads {count} image to fal (an unlisted link).", "Uploads {count} images to fal (unlisted links)."), 400)
        paragraph(layout, iface_("Stop asks fal to cancel; a video already started may still be billed."), 400, 'INFO')
        if not model["verified"] and not model.get("team"):
            paragraph(layout, iface_("Key/model access has not been verified yet."), 400, 'ERROR')

    def draw(self, context):
        model, state = self._model, self._snapshot
        if state["kind"] == "VIDEO":
            return self._draw_video(model, state)
        self.layout.label(text=iface_("{model} · {account}", model=model["name"], account=model["account"]), translate=False)
        paragraph(self.layout, iface_("Uses your team's provider key through Angelo.") if model.get("team") else iface_("Billed to this API account, not your coding subscription."), 380)
        paragraph(self.layout, plural_(len(state["references"]), "Upload {count} image reference. One output requested.", "Upload {count} image references. One output requested."), 380)
        if model["mode"] == "meshy_api" and state["texture"] and not state["references"]:
            paragraph(self.layout, iface_("Two billable stages: geometry preview + textured refine."), 380, 'INFO')
        if not model["verified"] and not model.get("team"):
            paragraph(self.layout, iface_("Key/model access has not been verified yet."), 380, 'ERROR')
        paragraph(self.layout, iface_("Nothing is inserted into the scene automatically."), 380)

    def execute(self, context):
        try:
            if not hasattr(self, "_snapshot"):
                raise ValueError(n_("Generate must be confirmed in Media Studio"))
            state, model = self._snapshot, self._model
            folder = state["folder"] if state["folder"] not in {"ALL", "UNFILED"} else ""
            if state["kind"] == "VIDEO":
                start_job("VIDEO", state["prompt"].strip(), model, folder, state["options"], state["references"],
                          inputs=state["inputs"], price=state["price"])
                state_for(self.area_pointer)["notice"] = ""   # "Frame added…" is done; the card shows the job.
                redraw()
                return {'FINISHED'}
            start_job(state["kind"], state["prompt"].strip(), model, folder,
                      {k: state[k] for k in ("aspect", "resolution", "quality", "background", "size", "polycount", "texture")},
                      state["references"])
            return {'FINISHED'}
        except (OSError, ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


def start_job(kind, prompt, model, folder, options, references, inputs=None, price=None):
    """A confirmed generation: a library record and its job, billed to ``model``'s exact account (a ``model_options``
    entry). Generate and the viewport's photo drop (``photo_drop``) both start here. Returns the record. A video
    passes its ``inputs`` (frames, references, reference video: ``studio_video_ui.inputs``) and its estimated
    ``price``."""
    from . import ui
    # Recheck the exact account/model after confirmation. Don't use the
    # stale key cached in a dialog if the user removed/replaced it.
    binding = ui.generation_binding(model["connection"])
    library()
    if len(core._jobs.active) >= 4:
        raise ValueError(n_("Four generations are already running"))
    asset = library().add(kind, prompt, model["name"], model["connection"], folder, mode=model["mode"],
                          model_id=model["id"], options=dict(options), reference_names=[r["name"] for r in references],
                          **({"price": price} if kind == "VIDEO" else {}))
    core._jobs.start(asset, binding, asset["options"], references if inputs is None else inputs)
    redraw()
    return asset


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
    bl_label = "Enhance Media Studio Prompt"
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            record, config = enhancement_binding()
            self._name = iface_("{account} · {model}", account=record["name"], model=config["model"])
            state_for(self.area_pointer)
            return context.window_manager.invoke_props_dialog(self, width=420, confirm_text=iface_("Enhance prompt"))
        except ValueError as error:
            state_for(self.area_pointer)["notice"] = describe_error(error)
            redraw()
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}

    def draw(self, context):
        self.layout.label(text=self._name, translate=False)
        paragraph(self.layout, iface_("Uses this account's credits or subscription allowance."), 360)
        paragraph(self.layout, iface_("Text only. Original prompt preserved; review before applying."), 360)

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
                    def redact(text):
                        return access.redact(text) if access is not None else text.replace(key, "[REDACTED]") if key else text
                    result = {"notice": describe_error(error, ENHANCE_FAILED, redact)}
                if not cancel.is_set():
                    _enhancement_events.put((pointer, result))
            try:
                threading.Thread(target=run, daemon=True, name="AngeloPromptEnhance").start()
            except RuntimeError:   # No thread to enhance on (``thread_start``): it ends now, as one that failed.
                _enhancement_events.put((pointer, {"notice": ENHANCE_FAILED}))
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}
