# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared driver for the operator_run family suites (not a suite itself).

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
run cooperatively, committed through the QA Undo operator, then undone and
redone. Import it only inside a disposable Blender instance.
"""

import bpy

from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent.program_dependencies import _fingerprint
from angelo_agent.tool_specs import validate_call
from program_qa_helpers import no_stages


class Driver:
    def __init__(self, report):
        self.report = report

    @property
    def window(self):
        return bpy.context.window_manager.windows[0]

    def objects(self):
        return self.window.scene.objects

    def check(self, name, condition):
        self.report["checks"][name] = bool(condition)
        assert condition, name

    def op(self, target, operator, **parameters):
        handle = target if isinstance(target, str) else queries.object_handle(target)
        return {"kind": "operator_run", "target": handle, "parameters": dict(operator=operator, **parameters)}

    def program(self, operations, writes=(), reads=(), mode="APPLY", checks=()):
        binding = lambda obj: {"handle": queries.object_handle(obj), "revision": queries.object_revision(self.window, obj)}
        arguments = {"mode": mode, "read_set": [binding(obj) for obj in reads],
                     "write_set": [binding(obj) for obj in writes], "operations": operations}
        if checks:
            arguments["expected_checks"] = [dict(check, target=queries.object_handle(writes[0])) for check in checks]
        return validate_call("angelo_scene_program", arguments)

    def refused_by_validation(self, operations, writes, code):
        try:
            self.program(operations, writes=writes)
        except ValueError as error:
            return code in str(error)
        return False

    @staticmethod
    def settle(job):
        for _ in range(400):
            result = job.poll()
            if result.get("final") or result.get("ready_to_commit") or result.get("status") == "preview":
                return result
        raise AssertionError("cooperative native program did not settle")

    def run(self, arguments):
        job = native.begin(self.window, "angelo_scene_program", arguments)
        return job, self.settle(job)

    def commit(self, job):
        result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        self.report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
        return "FINISHED" in result and job.result.get("status") == "committed"

    def failed_with(self, result, code):
        return result.get("status") == "failed" and code in (result.get("error") or "")

    def undo(self):
        bpy.ops.ed.undo()
        queries.invalidate()

    def redo(self):
        bpy.ops.ed.redo()
        queries.invalidate()

    def applied(self, label, operations, writes, state, intended, reads=(), checks=()):
        """Stage, commit, Undo and Redo one program; ``state(name)`` reads the written object by name.

        ``checks`` (without targets) are expected checks on the first written
        object, verified on the stage before the program is ready to commit.

        The live objects must be untouched until commit, the committed state
        must be ``intended``, one Undo must restore the state before and one
        Redo the committed state.
        """
        names = [obj.name for obj in writes]
        before = {name: state(name) for name in names}
        prints = [_fingerprint(obj) for obj in [*writes, *reads]]
        job, staged = self.run(self.program(operations, writes=writes, reads=reads, checks=checks))
        self.report.setdefault("staged", {})[label] = {key: staged.get(key) for key in ("status", "error")}
        self.check(label + "_ready_without_live_edit", staged.get("ready_to_commit") and no_stages()
                   and [_fingerprint(obj) for obj in [*writes, *reads]] == prints
                   and {name: state(name) for name in names} == before)
        self.check(label + "_commits", self.commit(job))
        after = {name: state(name) for name in names}
        self.check(label + "_intended", intended(after))
        self.undo()
        self.check(label + "_one_undo", {name: state(name) for name in names} == before and no_stages())
        self.redo()
        self.check(label + "_one_redo", {name: state(name) for name in names} == after)
        self.undo()
        return after

    def stale(self, label, operations, target, edit, restore, reads=()):
        """A manual edit after the plan was made fails the program and is kept."""
        arguments = self.program(operations, writes=[target], reads=reads)
        edit()
        job, result = self.run(arguments)
        self.check(label + "_manual_edit_makes_plan_stale", result.get("status") == "failed" and no_stages())
        restore()
        queries.invalidate()

    def refused(self, label, operations, writes, code, reads=()):
        prints = [_fingerprint(obj) for obj in [*writes, *reads]]
        job, result = self.run(self.program(operations, writes=writes, reads=reads))
        self.report.setdefault("refusals", {})[label] = result.get("error")
        self.check(label + "_refused_" + code, self.failed_with(result, code) and no_stages()
                   and [_fingerprint(obj) for obj in [*writes, *reads]] == prints)
