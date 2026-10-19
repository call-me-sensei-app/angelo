# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A kernel's deliberate refusal is a correctable refusal, never an unexplained failure (no Blender).

A castle one-shot stopped half built: a staged
``material_assign`` refused with a plain ``ValueError``, so the result said
``error_code: UNKNOWN`` and ``partial_changes_possible``, the request's recovery
barrier blocked every later edit (``recovery_required``) and the model asked the
person to clear it, although nothing live had been written. A 33rd material slot
did the same (the long-session replay). A deliberate refusal raised while a
program stages carries its closed code (``refusal_codes``) at the raise site;
only genuine internal faults stay unclassified, which still fail closed.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.program_jobs import ProgramJob, PreviewStore, WriterLease  # noqa: E402
from angelo_agent.refusal_codes import RefusalError, refusal_code  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402
from test_native_program_jobs import Backend, finish, program  # noqa: E402

# Families whose functions run while a program binds, stages or checks (``action_kernels.apply_operation`` and the
# material obligations replayed at binding).
KERNEL_FAMILIES = ("action_", "anim_", "node_", "bone_", "particle_", "operator_", "material_", "batch_", "world_",
                   "program_material_", "transform_bake")
# Validators of a call's arguments. They run at admission, before anything is staged, where
# ``Controller.request_tool`` answers an unclassified ValueError as VALIDATION; a kernel only repeats them on the
# same canonical arguments, which already passed.
ADMISSION_ONLY = {"action_bones_spec.py", "action_camera_light_spec.py", "action_catalog.py", "action_operator_spec.py",
                  "action_schema_types.py", "batch_spec.py", "node_edit_spec.py", "node_graph_rules.py"}
# Internal invariants: an unexpected state the host cannot explain. Each raise site carries INTERNAL_ERROR, which
# raises the recovery barrier as any other internal fault does (as plain ValueErrors they were read
# as UNKNOWN). The one left reads a canonical result row its own kernel built.
INVARIANTS = {("material_rules.py", "canonical")}
PLAIN = {"ValueError", "TypeError", "RuntimeError", "KeyError", "AssertionError"}


def plain_raises(path):
    """``(function, line)`` of every ``raise <plain exception>(...)`` / ``return ValueError(...)`` in a module."""
    tree = ast.parse(path.read_text())
    found = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            call = node.exc if isinstance(node, ast.Raise) else node.value if isinstance(node, ast.Return) else None
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) in PLAIN:
                found.append((function.name, node.lineno))
    return found


class KernelRefusalsAreClassified(unittest.TestCase):
    def test_no_kernel_raises_an_unclassified_refusal(self):
        offenders = []
        for path in sorted(ADDON.glob("*.py")):
            if not path.name.startswith(KERNEL_FAMILIES) or path.name in ADMISSION_ONLY:
                continue
            offenders += ["%s:%d %s()" % (path.name, line, function) for function, line in plain_raises(path)
                          if (path.name, function) not in INVARIANTS]
        self.assertEqual(offenders, [], "a deliberate refusal raised while a program stages needs its closed code: "
                                        "raise RefusalError(message, code=...)")

    def test_a_33rd_material_slot_is_a_validation_refusal(self):
        # The slot rule alone: stand-ins for Blender and the mesh helpers, removed again with the module.
        helpers = SimpleNamespace(_mesh=None, _face_selection=None, _readback=None)
        with patch.dict(sys.modules, {"bpy": SimpleNamespace(types=SimpleNamespace()),
                                      "angelo_agent.action_context": helpers}):
            sys.modules.pop("angelo_agent.action_materials", None)
            from angelo_agent import action_materials
            sys.modules.pop("angelo_agent.action_materials", None)
        mesh = SimpleNamespace(materials=[object()] * 32)
        with self.assertRaises(RefusalError) as caught:
            action_materials.place(SimpleNamespace(material_slots=[]), mesh, object(), 32, [])
        self.assertIs(type(caught.exception), RefusalError)  # Native preparation trusts only the exact type.
        self.assertEqual(refusal_code(caught.exception), "VALIDATION")
        self.assertIn("at most 32 slots", str(caught.exception))

    def test_an_effective_slot_that_does_not_take_the_material_is_an_internal_error(self):
        helpers = SimpleNamespace(_mesh=None, _face_selection=None, _readback=None)
        with patch.dict(sys.modules, {"bpy": SimpleNamespace(types=SimpleNamespace()),
                                      "angelo_agent.action_context": helpers}):
            sys.modules.pop("angelo_agent.action_materials", None)
            from angelo_agent import action_materials
            sys.modules.pop("angelo_agent.action_materials", None)

        class Stuck:  # An effective slot that keeps what it had.
            material = property(lambda self: None, lambda self, value: None)
        with self.assertRaises(RefusalError) as caught:
            action_materials.place(SimpleNamespace(material_slots=[Stuck()]), SimpleNamespace(materials=[]), object(), 0, [])
        self.assertIs(type(caught.exception), RefusalError)
        self.assertEqual(refusal_code(caught.exception), "INTERNAL_ERROR")

    def test_obligation_state_faults_are_internal_errors(self):
        from angelo_agent import program_material_obligations as obligations
        with self.assertRaises(RefusalError) as caught:
            obligations._error("unknown created material owner $wall")
        self.assertIs(type(caught.exception), RefusalError)
        self.assertEqual(refusal_code(caught.exception), "INTERNAL_ERROR")
        missing = obligations.NeedsMaterialBinding("Existing material slots need authoritative binding: scn:x")
        self.assertIsInstance(missing, ValueError)  # initial_obligations still catches it as control flow.
        self.assertEqual(refusal_code(missing), "INTERNAL_ERROR")  # Anywhere else: a classified internal fault.

    def test_editing_a_linked_duplicates_data_is_a_validation_refusal(self):
        from angelo_agent import program_rules
        operations = [{"kind": "object_duplicate", "target": None,
                       "parameters": {"source": "scn:a:1", "alias": "copy", "linked": True}},
                      {"kind": "object_join", "target": "scn:b:2", "parameters": {"sources": ["$copy"]}}]
        with self.assertRaises(RefusalError) as caught:
            program_rules.check(operations)
        self.assertIs(type(caught.exception), RefusalError)
        self.assertEqual(refusal_code(caught.exception), "VALIDATION")
        self.assertIn("LINKED_EDIT", str(caught.exception))

    def test_material_and_batch_refusals_carry_their_codes(self):
        from angelo_agent import material_rules
        error = material_rules.not_in_program("Stone")
        self.assertIs(type(error), RefusalError)
        self.assertEqual((refusal_code(error), str(error)[:36]), ("VALIDATION", "material_assign MATERIAL_NOT_FOUND: "))
        from angelo_agent.node_edit_spec import NodeEditError
        for code, policy in (("SHARED_CONFLICT", "VALIDATION"), ("ALL_USERS_MATERIAL_ONLY", "VALIDATION"),
                             ("SHARED_OUTSIDE_SCENE", "UNSUPPORTED")):
            self.assertEqual(refusal_code(NodeEditError(code, "detail")), policy, code)


class RejectingBackend(Backend):
    """A backend whose kernel refuses the operation, as a staged kernel does."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    def apply(self, stage, operation):
        self._record("apply")
        raise self.error


class StagedRefusalStaysCorrectable(unittest.TestCase):
    def run_refused(self, error):
        backend = RejectingBackend(error)
        job = ProgramJob(backend, program(), owner="chat-a", writer=WriterLease(), previews=PreviewStore())
        arguments = program()
        result = finish(job)
        barrier = RecoveryBarrier()
        delivered = barrier.refused("angelo_scene_program", arguments, result)
        return backend, result, delivered, barrier

    def test_a_classified_kernel_refusal_leaves_the_request_free_to_correct_it(self):
        refusal = RefusalError("material_assign MATERIAL_NOT_FOUND: the source has no material named 'Stone'; its "
                               "materials: 'Brick'", code="VALIDATION")
        backend, result, delivered, barrier = self.run_refused(refusal)
        self.assertEqual(result["error_code"], "VALIDATION")
        self.assertFalse(result["partial_changes_possible"])
        self.assertIs(result["scene_changed"], False)
        self.assertNotIn("recovery", result)
        self.assertNotIn("recovery_required", delivered)
        self.assertIsNone(barrier.pending)
        self.assertFalse(barrier.check("angelo_scene_program", program()))  # The next program is admitted.
        self.assertEqual(backend.commits, 0)

    def test_an_unclassified_kernel_failure_still_fails_closed(self):
        _backend, result, delivered, barrier = self.run_refused(ValueError("an internal fault"))
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("scene_changed", result)
        self.assertIs(delivered["recovery_required"], True)
        with self.assertRaises(RefusalError):
            barrier.check("angelo_scene_program", program())

    def test_a_refusal_when_the_binding_is_resolved_again_is_a_changed_state(self):
        # The same program resolved when it was bound; refusing at commit's verification means the live state changed
        # (a source's material renamed meanwhile): a stale dependency, never a correctable refusal.
        from angelo_agent import native_programs as native
        backend = Backend()
        job = ProgramJob(backend, program(), owner="chat-a", writer=WriterLease(), previews=PreviewStore())
        finish(job)
        backend.verify = lambda binding: native.BlenderProgramBackend.verify(
            backend, {"handles": (), "scene": None, "obligations": None})
        missing = RefusalError("material_assign MATERIAL_NOT_FOUND: the source has no material named 'Stone'; its "
                               "materials: 'Rock'", code="VALIDATION")
        with patch.object(native, "resolve_binding", side_effect=missing):
            result = job.commit()
        self.assertEqual((result["status"], result["error_code"]), ("failed", "STALE_DEPENDENCY"))
        self.assertIn("MATERIAL_NOT_FOUND", result["error"])
        self.assertEqual(backend.commits, 0)

    def test_an_internal_fault_when_the_binding_is_resolved_again_still_blocks(self):
        from angelo_agent import native_programs as native
        backend = Backend()
        job = ProgramJob(backend, program(), owner="chat-a", writer=WriterLease(), previews=PreviewStore())
        finish(job)
        backend.verify = lambda binding: native.BlenderProgramBackend.verify(
            backend, {"handles": (), "scene": None, "obligations": None})
        fault = RefusalError("PROGRAM_MATERIAL_STATE: unknown created material owner $wall", code="INTERNAL_ERROR")
        with patch.object(native, "resolve_binding", side_effect=fault):
            result = job.commit()
        self.assertEqual((result["status"], result["error_code"]), ("failed", "INTERNAL_ERROR"))
        self.assertTrue(result["partial_changes_possible"])
        self.assertEqual(backend.commits, 0)

    def test_a_classified_internal_fault_still_raises_the_barrier(self):
        fault = RefusalError("material_assign NOT_STAGED: the material is not a private staged copy",
                             code="INTERNAL_ERROR")
        _backend, result, delivered, barrier = self.run_refused(fault)
        self.assertEqual(result["error_code"], "INTERNAL_ERROR")
        self.assertTrue(result["partial_changes_possible"])
        self.assertIs(delivered["recovery_required"], True)
        with self.assertRaises(RefusalError):
            barrier.check("angelo_scene_program", program())


if __name__ == "__main__":
    unittest.main()
