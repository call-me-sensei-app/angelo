# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The UV_LAYOUT job: the ``uv_layout`` operation's layout on its target, one pack at a time on Pack Islands' own
window-manager job (``job_runners.Runner``'s contract; doc/angelo/uv_layout.rst, doc/angelo/long_jobs.rst).

``uv_layout_job.Layout`` drives the layout as UV Layout by hand does: each poll advances it, so between packs the
next tile's islands are placed on the main thread and during one Pack Islands' job reports progress and stops at
its next island. The target enters Edit Mode alone for the run (the person's selection comes back after), and
leaves it at the end. A cancelled or failed layout puts the UVs back as they were; one Undo takes a finished one
back (its own undo step, pushed once the layout is done and the target is back in Object Mode).
"""

import bpy

from . import job_uv_layout_spec as spec
from . import uv_layout_job, uv_layout_ops
from .job_manager import JobStartError
from .job_runners import Runner
from .job_spec import JobRequestError
from .ui_text import n_

PUT_BACK = n_("The UVs were put back as they were.")
REMOVED = n_("The object was removed during the layout.")
PARTIAL = n_("The UVs could not all be put back; Undo takes the layout back.")
NOT_FINISHED = n_("The layout ended without finishing; see the Info log for its reason")
UNDO_STEP = "UV Layout"   # i18n: undo history entry, named in English as Blender's own are.


class UvLayoutRunner(Runner):
    job_type = "UV_PACK"   # Pack Islands' window-manager job (_angelo_jobs' table), one per tile.

    def __init__(self, window, request, workspace, target):
        super().__init__(window, request, workspace)
        self.target = target
        self.layout = self.selection = None
        self.outcome = None   # The layout's last state once it ended: DONE, STOPPED, FAILED or REMOVED.
        self.faulted = self.unrestored = False
        self.admit()

    def admit(self):
        target = self.target
        try:
            name, kind = target.name, target.type
        except ReferenceError:
            raise JobRequestError("The object to lay out was removed", code="STALE_REVISION") from None
        if kind != 'MESH':
            raise JobRequestError("%s is a %s; UV Layout works on a mesh" % (name, kind.lower()), code="VALIDATION")
        if target.library is not None or target.override_library is not None or target.data.library is not None:
            raise JobRequestError("%s is linked from another file; UV Layout needs a mesh of this file" % name,
                                  code="UNSUPPORTED")
        if self.window is not None and not target.visible_get(view_layer=self.window.view_layer):
            raise JobRequestError("%s is hidden; UV Layout edits a visible mesh" % name, code="VALIDATION")
        if target.mode != 'OBJECT':
            raise JobRequestError("%s is in %s mode; UV Layout starts in Object Mode" % (name, target.mode.lower()),
                                  code="VALIDATION")
        mesh = target.data
        if not mesh.polygons:
            raise JobRequestError("%s has no faces to lay out" % name, code="VALIDATION")
        if self.request["layout"]["unwrap"] == "KEEP" and not mesh.uv_layers:
            raise JobRequestError("%s has no UV map to keep; ask for unwrap SMART or SEAMS" % name,
                                  code="VALIDATION")
        self.name, self.faces = name, len(mesh.polygons)

    @property
    def locks_interface(self):
        return True   # The target is in Edit Mode while the layout runs: nothing else may read or change it.

    def describe(self):
        layout = self.request["layout"]
        return {"objects": [self.name], "faces": self.faces, "packer": layout["packer"],
                "texture_size": layout["texture_size"], "unwrap": layout["unwrap"]}

    def _context(self):
        return bpy.context.temp_override(window=self.window, active_object=self.target, object=self.target,
                                         selected_objects=[self.target], selected_editable_objects=[self.target])

    def _start(self):
        layer = self.window.view_layer
        # Entering Edit Mode takes every selected mesh with it: the target goes alone, the selection comes back.
        self.selection = (layer.objects.active, [o for o in layer.objects if o.select_get(view_layer=layer)])
        for obj in self.selection[1]:
            obj.select_set(False, view_layer=layer)
        self.target.select_set(True, view_layer=layer)
        layer.objects.active = self.target
        with self._context():
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except RuntimeError as error:   # Blender's own refusal: its report says what to change.
                reason = " ".join(str(error).split()).removeprefix("Error: ")
                raise JobStartError("Angelo could not edit %s: %s" % (self.name, reason[:300])) from None
            try:
                self.layout = uv_layout_job.Layout([self.target], uv_layout_ops.to_layout(self.request["layout"]))
            except ValueError as error:
                raise JobStartError("Angelo refused the layout: %s" % str(error)[:300]) from None
            state, _ = self.layout.advance(self.scene, False)
        if state == "FAILED":
            raise JobStartError("Angelo refused the layout: %s" % (self.layout.error or "")[:300])

    def _undo_start(self):
        try:
            self._leave()
        finally:
            super()._undo_start()

    def poll(self):
        if self.outcome is not None:
            return None
        try:
            alive = self.target.mode is not None
        except ReferenceError:
            alive = False
        if not alive:   # Its mesh is gone: nothing to put back; the sync setting still is.
            self.outcome = "REMOVED"
            self.layout.abandon("the object was removed")
            self._leave()
            return None
        try:
            with self._context():
                # While a job locks the interface (its own pack's included) nothing touches the mesh, its mode
                # included: advance then only reads the running pack's progress.
                locked = bpy.context.window_manager.is_interface_locked
                if self.target.mode != 'EDIT' and not locked:   # Left Edit Mode between packs: the layout reads its
                    bpy.ops.object.mode_set(mode='EDIT')        # mesh there.
                state, fraction = self.layout.advance(self.scene, locked)
        except Exception as error:   # Never left half laid out: the layout puts the UVs back once no pack runs.
            reason = str(error) or type(error).__name__
            if not self.faulted:
                self.faulted = True
                self.layout.fail(self.scene, reason)
                return {"progress": self.layout.fraction()}
            self.outcome = "FAILED"   # Again: end here, putting back what it can.
            try:
                with self._context():
                    self.layout.abandon(reason, restore=True)
            except Exception:
                self.unrestored = True
            self.unrestored = self.unrestored or bool(self.layout.unrestored)
            self._leave()
            return None
        if state == "PACKING":
            return {"progress": fraction}
        self.outcome = state
        self.unrestored = self.unrestored or bool(self.layout.unrestored)   # Changed meanwhile: named in the error.
        self._leave()
        if state == "DONE":   # A timer's change pushes no undo step: one, in Object Mode, takes the layout back.
            with bpy.context.temp_override(window=self.window):
                bpy.ops.ed.undo_push(message=UNDO_STEP)
        return None

    def stop(self):
        if self.layout is not None and self.outcome is None:
            self.layout.stop(self.scene)

    def _leave(self):
        """Back to Object Mode with the person's selection, once."""
        if self.selection is None:
            return
        active, selected = self.selection
        self.selection = None
        try:
            if self.target.mode == 'EDIT':
                with self._context():
                    bpy.ops.object.mode_set(mode='OBJECT')
        except ReferenceError:
            pass
        layer = self.window.view_layer
        for obj in layer.objects:
            obj.select_set(obj in selected, view_layer=layer)
        try:
            if active is not None and active.name in layer.objects:
                layer.objects.active = active
        except ReferenceError:
            pass   # The person's active object was removed meanwhile.

    def _finish(self, stopped):
        if self.outcome == "DONE":
            return "done", "", spec.receipt(self.layout.receipt)
        if self.outcome == "REMOVED":
            return "failed", REMOVED, {"note": REMOVED}
        if self.outcome == "STOPPED":
            return "cancelled", "", {"note": PUT_BACK}
        reason = self.layout.error if self.layout is not None and self.layout.error else NOT_FINISHED
        return "failed", reason, {"note": PARTIAL if self.unrestored else PUT_BACK}
