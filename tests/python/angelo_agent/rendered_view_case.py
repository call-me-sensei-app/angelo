# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The host case for ``angelo_view``'s rendered looks and finished pictures.

``rendered_view_fakes`` stands in for Blender (settings that change with the
image format as Blender's do, one render job the test drives) and
``picture_fakes`` for ``imbuf``; the job manager, the reference store,
``view_render``, ``view_finished`` and ``view_picture`` are the real ones.
"""

import importlib
import sys
import tempfile
import unittest

import numpy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes  # noqa: E402
import rendered_view_fakes as fakes  # noqa: E402
from angelo_agent.job_manager import JobManager  # noqa: E402
from angelo_agent.job_store import JobStore  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

LOOK = {"targets": [], "views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 768}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class LookCase(unittest.TestCase):
    """view_render over the stand-in Blender, with the real manager, store and references."""

    engine, image, render_result = "CYCLES", None, True

    def setUp(self):
        self.blender = fakes.Blender(fakes.Scene(self.engine, self.image), render_result=self.render_result)
        self.temp = tempfile.TemporaryDirectory()
        self.references = ReferenceStore(self.temp.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        self.clock = Clock()
        mathutils = ModuleType("mathutils")
        mathutils.Matrix = mathutils.Vector = tuple
        job_ui = ModuleType("angelo_agent.job_ui")
        self.modules = patch.dict(sys.modules, {"bpy": self.blender.bpy, "_bpy": self.blender.native,
                                                "imbuf": self.blender.imbuf,
                                                "mathutils": mathutils, "angelo_agent.job_ui": job_ui})
        self.modules.start()
        for name in ("view_render", "view_render_restore", "view_capture", "job_runners", "job_settings", "scene_queries"):
            sys.modules.pop("angelo_agent." + name, None)
        self.render = importlib.import_module("angelo_agent.view_render")
        self.manager = JobManager(JobStore(self.temp.name), clock=self.clock,
                                  on_finish=lambda record: self.render.ended(self.manager, record))
        job_ui.manager = lambda create=True: self.manager
        capture = importlib.import_module("angelo_agent.view_capture")
        self.patches = [
            patch.object(self.render, "scene_queries", SimpleNamespace(document_epoch=lambda: self.epoch)),
            patch.object(self.render.time, "monotonic", self.clock),
            patch.object(capture, "_targets", lambda window, handles: ([], [])),
            patch.object(capture, "_scene_camera", lambda window, epoch, resolution: None),
            patch("angelo_agent.native_programs.runtime_is_staging", lambda: self.staging)]
        for item in self.patches:
            item.start()
        self.epoch, self.staging = "e" * 32, False
        self.controller = SimpleNamespace(state={"id": "chat"}, executing_call=SimpleNamespace(id="chat/call-1"),
                                          _epoch=1, _request_generation=1)
        self.render._spent.clear()
        self.render._pending.clear()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.modules.stop()
        self.temp.cleanup()

    def begin(self, **arguments):
        return self.render.begin(self.blender.window, dict(LOOK, **arguments), self.references, self.controller)

    def run_look(self, seconds=20.0, progress=1.0, written=True, **arguments):
        """One look that Blender finishes after ``seconds``."""
        pending = self.begin(**arguments)
        self.blender.progress = progress
        self.manager.poll()
        self.clock.now += seconds
        self.blender.end(written)
        self.manager.poll()
        return pending, pending.poll()

    def reference_files(self):
        return sorted(path.name for path in self.references.directory.glob("*")) if self.references.directory.exists() else []

    def delivered(self, result):
        """The pixels of the result's picture as the model gets them: RGBA rows, top row first."""
        return picture_fakes.read_png(Path(result["views"][0]["path"]).read_bytes())


GREY, RED = (120, 130, 140, 255), (255, 0, 0, 255)


def picture(width, height, colour=GREY):
    pixels = numpy.empty((height, width, 4), dtype=numpy.uint8)
    pixels[...] = colour
    return pixels


class FinishedCase(unittest.TestCase):
    """``view_finished`` over the stand-in Blender, with planted job records and files."""

    def setUp(self):
        self.blender = fakes.Blender()
        self.scene = self.blender.scene
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve() / "workspace"
        self.folder.mkdir()
        self.references = ReferenceStore(Path(self.temp.name) / "state", "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        self.records, self.active = {}, None
        manager = SimpleNamespace(record=lambda identity: self.records.get(identity), active=lambda: self.active,
                                  store=SimpleNamespace(records=self.records))
        job_ui = ModuleType("angelo_agent.job_ui")
        job_ui.manager = lambda create=True: manager
        job_ui.output_path = lambda record: (Path(record["detail"]["output_path"])
                                             if Path(record["detail"].get("output_path") or "/missing").exists() else None)
        queries = SimpleNamespace(document_epoch=lambda: "e" * 32)
        self.modules = patch.dict(sys.modules, {"bpy": self.blender.bpy, "_bpy": self.blender.native,
                                                "imbuf": self.blender.imbuf, "angelo_agent.job_ui": job_ui,
                                                "angelo_agent.scene_queries": queries})
        self.modules.start()
        for name in ("view_finished", "view_render_restore", "job_settings"):
            sys.modules.pop("angelo_agent." + name, None)
        self.finished = importlib.import_module("angelo_agent.view_finished")
        self.patches = [patch("angelo_agent.job_ui", job_ui, create=True),
                        patch("angelo_agent.scene_queries", queries, create=True)]
        for item in self.patches:
            item.start()
        self.controller = SimpleNamespace(state={"id": "chat"}, _epoch=1, _request_generation=1)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.modules.stop()
        self.temp.cleanup()

    def file(self, name, data):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else picture_fakes.png_bytes(data))
        return path

    def job(self, path, kind="RENDER_STILL", identity="j3", **changes):
        record = dict({"id": identity, "session": "chat", "kind": kind, "status": "done",
                       "request": {"output": "renders/" + Path(path).name}, "receipt": {},
                       "detail": {"engine": "CYCLES", "samples": 128, "output_path": str(path)}}, **changes)
        self.records[identity] = record
        return identity

    def show(self, image="j3", **checked):
        return self.finished.finished_image(self.blender.window, dict(LOOK, image=image, **checked), self.references,
                                            self.controller)

    def delivered(self, result):
        return picture_fakes.read_png(Path(result["views"][0]["path"]).read_bytes())

    def reference_files(self):
        return sorted(path.name for path in self.references.directory.glob("*")) if self.references.directory.exists() else []
