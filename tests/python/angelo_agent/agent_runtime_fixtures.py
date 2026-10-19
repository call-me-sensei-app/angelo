# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared fake runtime fixtures; no TestCase base or discoverable test methods."""

import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import controller as controller_module
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.program_jobs import ProgramError, ProgramJob, PreviewStore, WriterLease
from angelo_agent.program_spec import PROGRAM_TOOLS, validate_program

HANDLE = "scn:" + "a" * 32 + ":1"


def program(mode="APPLY", location=None):
    return validate_program({"mode": mode, "read_set": [], "write_set": [{"handle": HANDLE, "revision": "a" * 64}],
        "operations": [{"kind": "transform", "target": HANDLE, "parameters": {"location": location or [1, 2, 3]}}],
        "expected_checks": []})


class Backend:
    def __init__(self, clock=None):
        self.source = [0, 0, 0]
        self.revision = 1
        self.commits = 0
        self.discards = 0
        self.stage = None
        self.calls = []
        self.clock = clock
        self.on_apply = None

    def tick(self, label):
        self.calls.append(label)
        if self.clock is not None:
            self.clock[0] += .001

    def ensure_main_thread(self):
        if threading.current_thread() is not threading.main_thread():
            raise AssertionError("Staged RNA cleanup cannot run on a worker")

    def check_context(self, binding):
        self.tick("context")

    def bind(self, _):
        self.tick("bind")
        return {"handles": (HANDLE,), "revision": self.revision}

    def create_stage(self, _):
        self.tick("stage")
        self.stage = {}
        return self.stage

    def copy_object(self, stage, _):
        self.tick("copy")
        stage["location"] = list(self.source)

    def finish_stage(self, _):
        self.tick("remap")

    def apply(self, stage, operation):
        self.tick("apply")
        stage["location"] = list(operation["parameters"]["location"])
        if self.on_apply:
            self.on_apply()
        return {"success": True}

    def validate(self, _stage, _check):
        self.tick("validate")
        return {"passed": True}

    def validate_stage(self, _):
        self.tick("integrity")

    def delta(self, stage):
        self.tick("delta")
        return {"summary": "Moved one staged object", "location": list(stage["location"])}

    def verify(self, binding):
        self.tick("verify")
        if binding["revision"] != self.revision:
            raise ProgramError("Exact native revision changed", code="STALE_REVISION")

    def commit(self, stage):
        self.tick("commit")
        self.source = list(stage["location"])
        self.commits += 1
        self.discard(stage)
        return {"verified": {"object_count": 1}, "changes": {"added": [], "removed": [],
                "modified": [{"name": "Sphere", "fields": ["location"]}]}}

    def discard(self, stage):
        self.tick("discard")
        self.discards += 1
        stage.clear()
        self.stage = None


class Executor:
    native_job_tools = {"angelo_scene_program", "angelo_accept_preview"}

    def __init__(self, writer, previews, clock=None):
        self.writer, self.previews, self.clock = writer, previews, clock
        self.backends = {}
        self.jobs = []
        self.direct = []
        self.fingerprint = "exact"
        self.studio_context = None
        self.on_precondition = None

    def backend(self):
        key = self.studio_context.state["id"]
        if key not in self.backends:
            self.backends[key] = Backend(self.clock)
        return self.backends[key]

    def precondition(self, name, arguments):
        if self.on_precondition:
            self.on_precondition(name, arguments)
        if name == "angelo_accept_preview":
            self.previews.get(arguments["preview_id"], ("chat", self.studio_context.state["id"]))
        return self.fingerprint

    def start_native_job(self, name, arguments, *, invocation_id):
        owner = ("chat", self.studio_context.state["id"])
        if name == "angelo_accept_preview":
            job = ProgramJob.accept(arguments["preview_id"], owner, previews=self.previews, writer=self.writer)
        else:
            job = ProgramJob(self.backend(), arguments, owner=owner, previews=self.previews, writer=self.writer)
        self.jobs.append(job)
        return job

    def execute(self, name, arguments):
        self.direct.append((self.studio_context.state["id"], name))
        return {"success": True, "objects": [], "object_count": 0}


class AgentRuntimeFixture:
    """Lifecycle/helpers only; a concrete unittest.TestCase owns all assertions."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.writer, self.previews = WriterLease(), PreviewStore()
        self.executor = Executor(self.writer, self.previews)
        self.controllers = []
        self.c = self.new_controller()
        self.validators = controller_module.validate_call
        self.tool_patch = patch.dict(controller_module.TOOL_MAP, {value["name"]: value for value in PROGRAM_TOOLS})
        self.tool_patch.start()
        self.bounded_patch = patch("angelo_agent.controller.BOUNDED_MUTATIONS",
            controller_module.BOUNDED_MUTATIONS | {"angelo_scene_program"})
        self.bounded_patch.start()
        self.validator_patch = patch("angelo_agent.controller.validate_call", side_effect=lambda name, args:
            validate_program(name, args) if name in {value["name"] for value in PROGRAM_TOOLS} else self.validators(name, args))
        self.validator_patch.start()
        self.writer_patch = patch("angelo_agent.program_jobs.WRITER", self.writer)
        self.writer_patch.start()

    def new_controller(self):
        c = Controller(self.directory.name, session_id=str(uuid.uuid4()))
        c.set_document(("document", 1))
        c.busy = True
        c.state["options"]["approval"] = "AUTO_BOUNDED"
        self.controllers.append(c)
        return c

    def tearDown(self):
        for c in self.controllers:
            c.close()
        self.previews.clear()
        self.writer_patch.stop()
        self.validator_patch.stop()
        self.bounded_patch.stop()
        self.tool_patch.stop()
        self.directory.cleanup()

    def enqueue(self, c=None, identity="program", mode="APPLY", callback=None, location=None):
        c = c or self.c
        answers = []
        c.request_tool(identity, "angelo_scene_program", program(mode, location), callback or answers.append)
        return answers, c.pending.get(c.state["id"] + "/" + identity)

    def pump(self, c=None, commit=True):
        c = c or self.c
        c.pump_tools(self.executor, c.document, budget=ToolDispatchBudget(seconds=60),
                     apply_mutation=lambda call: c.execute_tool(call, self.executor, c.document),
                     commit_mutation=(lambda call: c.commit_native_job(call, self.executor, c.document)) if commit else None)

    def ready(self, c=None):
        c = c or self.c
        for _ in range(40):
            self.pump(c, commit=False)
            if c.native_jobs and next(iter(c.native_jobs.values())).ready_to_commit:
                return next(iter(c.native_jobs.values()))
        self.fail("Program failed to stage")
