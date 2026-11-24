# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout by hand without freezing the interface (doc/angelo/uv_layout.rst): ``uv_layout.steps`` driven one pack
at a time, each pack running as Pack Islands' own window-manager job, so Blender keeps drawing, the status bar shows
the job's progress and a stop ends it at the packer's next island.

``Layout.advance`` is called on a modal operator's timer, with whether any job locks the interface: nothing
touches mesh data while one does (a render started mid-layout reads it on its thread). It returns
``(state, fraction)``: ``"PACKING"`` while a pack
runs or starts, then ``"DONE"`` with the receipt in ``Layout.receipt``, ``"STOPPED"`` after ``stop``, or ``"FAILED"``
with the reason in ``Layout.error``. A stopped or failed layout puts the meshes back as they were; one it could not
(changed or removed meanwhile) is named in ``Layout.unrestored`` and the error, and the layout ends ``"FAILED"``.
Nothing it calls ends it unfinished: a refused pack or a failed restore ends it ``"FAILED"`` with its cleanup done.
"""

from . import uv_layout

JOB = "UV_PACK"   # Pack Islands' window-manager job, as _bpy._angelo_jobs names it.


def _jobs():
    from _bpy import _angelo_jobs
    return _angelo_jobs


class Layout:
    def __init__(self, objects, spec):
        self.spec, self.objects, self._state = uv_layout.begin(objects, spec)
        self._steps = uv_layout.steps(self.objects, self.spec)
        self._packing = False
        self._stopping = False
        self._closed = False
        self.done, self.tiles = 0, 1
        self.receipt = None
        self.error = None
        self.unrestored = []

    def fraction(self, pack_progress=0.0):
        """How far the layout is: tiles packed plus the running pack's share, over the tiles planned so far."""
        return min(1.0, (self.done + pack_progress) / max(1, self.tiles))

    def advance(self, scene, locked):
        if self._closed:
            return ("DONE" if self.receipt is not None else "FAILED" if self.error else "STOPPED", 1.0)
        if self._packing:
            progress = _jobs().progress(scene, JOB)
            if progress is not None:
                return ("PACKING", self.fraction(progress))
            self._packing = False   # The job has ended: its end callback ran before Blender stopped counting it.
        if locked:   # Another job holds the interface lock: the next step, a finish or a restore waits for it.
            return ("PACKING", self.fraction())
        if self._stopping:
            self._close(restore=True)
            return ("FAILED" if self.error else "STOPPED", self.fraction())
        try:
            step = next(self._steps)
            self.done, self.tiles = step["done"], step["tiles"]
            uv_layout.pack(self.spec, step, job=True)   # Refused when its poll fails (mode or object changed).
        except StopIteration as done:
            self.receipt = done.value
            self._close(restore=False)
            return ("DONE", 1.0)
        except Exception as error:   # A refusal (ValueError) or a fault: either way, the meshes go back.
            self.error = str(error) or type(error).__name__
            self._close(restore=True)
            return ("FAILED", self.fraction())
        self._packing = True
        return ("PACKING", self.fraction())

    def stop(self, scene):
        """Ask the running pack to stop; the next ``advance`` puts the meshes back once the job has ended."""
        self._stopping = True
        if self._packing:
            _jobs().stop(scene, JOB)

    def fail(self, scene, reason):
        """End the layout as failed: the next ``advance`` puts the meshes back once no pack runs."""
        self.error = self.error or reason
        self._stopping = True
        if self._packing:
            try:
                _jobs().stop(scene, JOB)
            except Exception:
                pass

    def abandon(self, reason, restore=False):
        """End now, failed: with ``restore``, putting the meshes back (never while a pack runs); without, when they
        are gone. The person's settings come back either way."""
        self.error = self.error or reason
        self._close(restore=restore)

    def _close(self, restore):
        if self._closed:
            return
        self._closed = True
        try:
            self._steps.close()
            self.unrestored = uv_layout.finish(self._state, restore)
        except Exception as error:   # The cleanup itself failed: say so, never leave the caller half way.
            self.unrestored = [obj.name for obj in self.objects if _alive(obj)] or ["the meshes"]
            self.error = self.error or (str(error) or type(error).__name__)
        if self.unrestored:
            self.error = "%s; not put back (changed or removed while it was laid out): %s" % (
                self.error or "UV Layout stopped", ", ".join(self.unrestored))


def _alive(obj):
    try:
        return obj.name is not None
    except ReferenceError:
        return False
