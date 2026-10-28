# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Operators of Media Studio's Workflows tab (``studio_workflows``): choosing the character image, and two requests
on the person's own AI Enhance account after they approve each: the completeness check behind the review stop
(``parts_check``) and the fit of the parts to the character image that Place Character uses (``parts_ground``)."""

import shutil
import sys
from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import flow_host, flow_templates, live_windows
from .provider_registry import is_coding
from .studio_workflow_run import attach
from .studio_workflows import _s, current
from .ui_text import iface_, n_, rpt_


class ANGELO_OT_studio_workflow_image(Operator):
    """Choose the character image: a front view of the whole figure"""
    bl_idname = "angelo.studio_workflow_image"
    bl_label = "Choose Character Image"
    bl_options = {'INTERNAL'}
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    area_pointer: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        s = _s()
        state = s._views.get(int(self.area_pointer or 0))
        if state is None:
            return {'CANCELLED'}
        try:
            attach(state, [self.filepath])
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        s.redraw()
        return {'FINISHED'}


CHECK_EDGE = 1568   # The long edge the check's images are reduced to: what vision models take in without resizing.


def fitted_check_images(files, directory):
    """The character image and sheet as the check sends them: private PNG copies within ``CHECK_EDGE`` px, which
    also keeps them inside every attachment's byte limit (a 3840 px sheet is 4-6 MB). Main thread."""
    import uuid
    from .studio_host import write_fitted
    folder = Path(directory) / "checks" / str(uuid.uuid4())
    folder.mkdir(parents=True, mode=0o700)
    return [write_fitted(path, folder / f"{name}.png", CHECK_EDGE) for path, name in zip(files, ("character", "sheet"))]


def _check_images(state):
    """(character image file, sheet file, split key) for the check, from the run's flow, reduced for sending."""
    s = _s()
    value, tree, shown, _ = current(state)
    store = s.library()
    character = value["image"] if value["template"] == "character-in-parts" else None
    if character is None:
        character = ((flow_host.cached(tree.name) or {}).get("states", {}).get("Character image") or {}).get("chosen")
    files = []
    for identity in (character, shown["sheet"].get("chosen")):
        asset = store.assets.get(identity) if identity else None
        if not asset or not asset.get("files"):
            raise ValueError(rpt_("The character image or the parts sheet isn't in the library"))
        files.append(store.checked_file(asset, asset["files"][0]))
    return fitted_check_images(files, store.directory), shown["split"].get("key")


class ANGELO_OT_studio_workflow_check(Operator):
    """Compare the parts sheet with your character image on your AI Enhance account; it only reports"""
    bl_idname = "angelo.studio_workflow_check"
    bl_label = "Compare with My Image"
    bl_options = {'INTERNAL'}
    area_pointer: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        from . import parts_check
        return parts_check.READY  # Not offered until its accuracy is measured (parts_check.MEASUREMENT).

    def invoke(self, context, event):
        s = _s()
        state = s._views.get(int(self.area_pointer or 0))
        try:
            record, config = s.enhancement_binding()
            if not is_coding(record):
                raise ValueError(rpt_("Choose Claude or Codex for AI Enhance in AI Providers to compare parts"))
            self._name = iface_("{account} · {model}", account=record["name"], model=config["model"])
        except ValueError as error:
            if state is not None:
                state["notice"] = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=440, confirm_text=iface_("Compare"))

    def draw(self, context):
        self.layout.label(text=self._name, translate=False)
        self.layout.label(text="Uses this account's credits or subscription allowance.")
        self.layout.label(text="Sends your character image and the parts sheet. It can be wrong; it only reports.")

    def execute(self, context):
        import threading
        from . import ui
        from .studio_enhance import check_native
        s = _s()
        state = s._views.get(int(self.area_pointer or 0))
        try:
            if state is None or state.get("enhancing"):
                raise ValueError(rpt_("Wait for the running check to finish"))
            record, config = s.enhancement_binding()
            files, key = _check_images(state)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        settings = ui.connection_settings(record["mode"], record=record)
        models = ui.catalogs().entries.get(record["id"], [])
        secret = ui._memory_keys.get(record["id"], "")
        cancel, pointer, directory = threading.Event(), str(self.area_pointer), s.library().directory
        s._enhancements[pointer] = cancel
        s._enhancement_connections[pointer] = record["id"]
        state["enhancing"] = True

        def run():
            try:
                found = check_native(directory, record, settings, secret, config["model"], models, files,
                                     flow_templates.CHARACTER_SLOTS, cancel)
                result = {"workflow_check": dict(found, key=key)}
            except Exception as error:
                message = str(error).replace(secret, "[REDACTED]") if secret else str(error)
                result = {"notice": message[:350] or n_("The parts check didn't complete. Nothing changed.")}
            finally:
                shutil.rmtree(files[0].parent, ignore_errors=True)   # The reduced copies made for this check only.
            if not cancel.is_set():
                s._enhancement_events.put((pointer, result))
        threading.Thread(target=run, daemon=True, name="AngeloPartsCheck").start()
        s.redraw()
        return {'FINISHED'}


_fit_errors = set()


def fit_offered():
    """Whether Fit to My Image is offered: AI Enhance's connection and model (``parts_ground.offered``). The card draws
    with it and the Fit operator's poll is it, so it never raises: no usable AI Enhance account is "not offered", and
    an unexpected failure reads the same and is reported once to the console instead of breaking the card."""
    from . import parts_ground
    if not parts_ground.MEASURED:
        return False
    try:
        record, config = _s().enhancement_binding()
    except (ValueError, KeyError):
        return False   # No usable AI Enhance account: nothing to offer.
    except Exception as error:  # noqa: BLE001 - drawn every frame; the card must draw whatever the binding does
        if repr(error) not in _fit_errors:
            _fit_errors.add(repr(error))
            print(f"Angelo: Fit parts to my image is unavailable: {error!r}", file=sys.stderr, flush=True)
        return False
    return parts_ground.offered(record, config)


def _rgba(path):
    """A cut-out's pixels as uint8 RGBA, top row first. Main thread."""
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * 4, np.float32)
        image.pixels.foreach_get(pixels)
        return (np.flipud(pixels.reshape(height, width, 4)) * 255.0 + 0.5).astype(np.uint8)
    finally:
        bpy.data.images.remove(image)


def _fit_images(state):
    """([character file, numbered parts file], part ids in their numbers' order, character size as sent, split key,
    tree name) for the fit, from the run's flow: private copies made for this request only, removed on any failure.
    Parts turned off on the card are neither sent nor counted. Main thread."""
    import uuid
    import numpy as np
    from . import parts_ground
    from .flow_split_host import _png
    from .studio_host import write_fitted
    s = _s()
    value, tree, shown, _ = current(state)
    store = s.library()
    character = value["image"] if value["template"] == "character-in-parts" else None
    if character is None:
        character = ((flow_host.cached(tree.name) or {}).get("states", {}).get("Character image") or {}).get("chosen")
    asset = store.assets.get(character) if character else None
    if not asset or not asset.get("files"):
        raise ValueError(rpt_("The character image or the parts sheet isn't in the library"))
    parts = parts_ground.ordered([p for p in shown["split"].get("parts") or [] if p["id"] not in value["skip"]])
    if not parts or parts[0]["kind"] != "BODY":
        raise ValueError(rpt_("The sheet has no body to fit the parts to"))
    folder = Path(store.directory) / "checks" / str(uuid.uuid4())
    folder.mkdir(parents=True, mode=0o700)
    try:
        cutouts = []
        for part in parts:
            crop = store.assets.get(part.get("asset")) if part.get("asset") else None
            if not crop or not crop.get("files"):
                raise ValueError(rpt_("A part's cut-out isn't in the library; split the sheet again"))
            cutouts.append(_rgba(store.checked_file(crop, crop["files"][0])))
        character_file = write_fitted(store.checked_file(asset, asset["files"][0]), folder / "character.png",
                                      parts_ground.CHARACTER_EDGE)
        sent = bpy.data.images.load(str(character_file), check_existing=False)
        try:
            size = tuple(sent.size)
        finally:
            bpy.data.images.remove(sent)
        sheet = parts_ground.contact_sheet(cutouts)
        _png(folder / "parts.png", np.concatenate([sheet, np.full(sheet.shape[:2] + (1,), 255, np.uint8)], axis=2))
    except (ValueError, OSError, RuntimeError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(str(error) if isinstance(error, ValueError)
                         else rpt_("The parts couldn't be prepared for fitting; split the sheet again")) from None
    return ([character_file, folder / "parts.png"], [part["id"] for part in parts], size, shown["split"].get("key"),
            tree.name)


def keep_fit(state, fit):
    """Store a finished fit with the split and run it was made for, where Place Character reads it, whatever the
    Studio area shows now, as an undo step of its own. Main thread (Studio's pump). False, with a notice on the view
    when there is one, if that split is gone (split again or the run deleted), or for a result that carries no fit."""
    if fit is None:
        return False
    from .flow_split_host import store_grounding
    tree = bpy.data.node_groups.get(fit.get("tree") or "")
    stored = tree is not None and store_grounding(tree, flow_templates.SPLIT_STEP, fit.get("key"),
                                                  {name: fit[name] for name in ("size", "body", "boxes")})
    if not stored:
        if state is not None:
            state["notice"] = rpt_("The fit belongs to a sheet that was split again; fit the parts again")
        return False
    flow_host.refresh(tree)
    window = live_windows.first(bpy.context.window_manager)
    if window is not None:
        try:   # Undo after Place must not also take the fit (paid for on the account) with it.
            with bpy.context.temp_override(window=window):
                bpy.ops.ed.undo_push(message="Fit Parts to My Image")  # i18n: undo history name
        except RuntimeError:
            pass
    return True


class ANGELO_OT_studio_workflow_fit(Operator):
    """Find each part on your character image with your AI Enhance account, so Place puts it where your image shows it"""
    bl_idname = "angelo.studio_workflow_fit"
    bl_label = "Fit Parts to My Image"
    bl_options = {'INTERNAL'}
    area_pointer: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return fit_offered()  # Offered only on a connection and model whose fit was measured (parts_ground.MEASURED).

    def invoke(self, context, event):
        s = _s()
        state = s._views.get(int(self.area_pointer or 0))
        try:
            record, config = s.enhancement_binding()
            self._name = iface_("{account} · {model}", account=record["name"], model=config["model"])
        except ValueError as error:
            if state is not None:
                state["notice"] = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=440, confirm_text=iface_("Fit"))

    def draw(self, context):
        self.layout.label(text=self._name, translate=False)
        self.layout.label(text="Uses this account's credits or subscription allowance.")
        self.layout.label(text="Sends your character image and the parts. Place then puts each part where it shows.")

    def execute(self, context):
        import threading
        from . import ui
        from .studio_enhance import ground_native
        s = _s()
        state = s._views.get(int(self.area_pointer or 0))
        try:
            if state is None or state.get("enhancing"):
                raise ValueError(rpt_("Wait for the running check to finish"))
            record, config = s.enhancement_binding()
            files, part_ids, size, key, tree_name = _fit_images(state)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        settings = ui.connection_settings(record["mode"], record=record)
        models = ui.catalogs().entries.get(record["id"], [])
        secret = ui._memory_keys.get(record["id"], "")
        cancel, pointer, directory = threading.Event(), str(self.area_pointer), s.library().directory
        s._enhancements[pointer] = cancel
        s._enhancement_connections[pointer] = record["id"]
        state["enhancing"] = "fit"

        def run():
            from .parts_ground import FitTimeout
            timed_out = False
            try:
                found = ground_native(directory, record, settings, secret, config["model"], models, files, part_ids,
                                      size, cancel)
                result = {"workflow_fit": dict(found, key=key, tree=tree_name)}
            except Exception as error:
                timed_out = isinstance(error, FitTimeout)
                message = str(error).replace(secret, "[REDACTED]") if secret else str(error)
                result = {"notice": message[:350] or n_(
                    "Fitting the parts to your image didn't complete. Place uses the sheet's layout.")}
            finally:
                shutil.rmtree(files[0].parent, ignore_errors=True)   # The copies made for this request only.
            if timed_out or not cancel.is_set():   # A timeout reports; only a closed view cancels silently.
                s._enhancement_events.put((pointer, result))
        threading.Thread(target=run, daemon=True, name="AngeloPartsFit").start()
        s.redraw()
        return {'FINISHED'}


CLASSES = (ANGELO_OT_studio_workflow_image, ANGELO_OT_studio_workflow_check, ANGELO_OT_studio_workflow_fit)
