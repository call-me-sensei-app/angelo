# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The RETOPOLOGY job: Object > Retopology on Blender's own window-manager job (``job_runners.Runner``'s contract).

It writes a new object beside the target and never changes the target. The receipt is the facts the operator measured
on its result (``job_retopology_spec.receipt``). doc/angelo/retopology.rst.
"""

import bpy

from . import job_retopology_spec as spec
from .job_manager import JobStartError
from .job_runners import CANCELLED_NOTHING, Runner, native
from .job_spec import JobRequestError
from .ui_text import n_

# Refinement stops at 24 million working triangles (retopo_remesh.cc): a larger source would be remeshed coarser than
# asked without saying so first.
MAX_SOURCE_TRIANGLES = 20_000_000
NO_RESULT = n_("Retopology ended without a result; see the Info log for its reason")


class RetopologyRunner(Runner):
    job_type = "RETOPOLOGY"

    def __init__(self, window, request, workspace, target):
        super().__init__(window, request, workspace)
        self.target = target
        self.admit()

    def admit(self):
        target = self.target
        try:
            name, kind = target.name, target.type
        except ReferenceError:
            raise JobRequestError("The object to retopologize was removed", code="STALE_REVISION") from None
        if kind != 'MESH':
            raise JobRequestError("%s is a %s; retopology works on a mesh" % (name, kind.lower()), code="VALIDATION")
        if target.library is not None or target.override_library is not None or target.data.library is not None:
            raise JobRequestError("%s is linked from another file; retopology needs a mesh of this file" % name,
                                  code="UNSUPPORTED")
        if target.mode != 'OBJECT':
            raise JobRequestError("%s is in %s mode; retopology runs in Object Mode" % (name, target.mode.lower()),
                                  code="VALIDATION")
        mesh = target.data
        self.triangles = len(mesh.loops) - 2 * len(mesh.polygons)   # Exact for any polygons.
        if not mesh.polygons:
            raise JobRequestError("%s has no faces to retopologize" % name, code="VALIDATION")
        if self.triangles > MAX_SOURCE_TRIANGLES:
            raise JobRequestError("%s has %s triangles; retopology takes at most %s. Ask the person whether to "
                                  "decimate it first" % (name, f"{self.triangles:,}", f"{MAX_SOURCE_TRIANGLES:,}"),
                                  code="LIMIT_EXCEEDED")
        self.name = name

    @property
    def locks_interface(self):
        return True   # The operator locks Blender's interface while its job runs, as QuadriFlow does.

    def describe(self):
        return {"objects": [self.name], "faces": self.request["faces"], "symmetry": self.request["symmetry"],
                "source_triangles": self.triangles}

    def _selection(self):
        return {"selected_objects": [self.target], "active_object": self.target, "object": self.target}

    def _start(self):
        arguments = spec.operator_arguments(self.request, self.identity)
        try:
            with bpy.context.temp_override(window=self.window, **self._selection()):
                result = bpy.ops.object.retopology('EXEC_DEFAULT', **arguments)
        except RuntimeError as error:   # Blender's own refusal: its report says what to change.
            reason = " ".join(str(error).split()).removeprefix("Error: ")
            raise JobStartError("Angelo refused to start the job: %s" % reason[:300]) from None
        if 'FINISHED' not in result or native().progress(self.scene, self.job_type) is None:
            raise JobStartError("Angelo did not start retopology; see the Info log for its reason")

    def _finish(self, stopped):
        found = spec.find_result(((obj.name, _facts(obj)) for obj in self.scene.objects), self.identity)
        if found is None:
            if stopped:
                return "cancelled", "", {"note": CANCELLED_NOTHING}
            return "failed", NO_RESULT, {"note": NO_RESULT}
        return "done", "", spec.receipt(*found)


def _facts(obj):
    """The facts the operator stored on an object (a plain dict), or None."""
    group = obj.get(spec.PROPERTY)
    return group.to_dict() if group is not None and hasattr(group, "to_dict") else None
