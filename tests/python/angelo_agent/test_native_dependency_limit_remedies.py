# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual binding facade distinguishes an object bound from stale observations."""
import suite_isolation  # noqa: F401
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import native_programs as native, program_refusals, program_spec, tool_specs
from angelo_agent.program_jobs import ProgramError
from angelo_agent.request_fences import DeliveryOwner, RequestFence


def runtime():
    host = NS(_epoch=3, _request_generation=7, _closing=False, cancelled=False)
    fence = RequestFence(3, 7)
    current = NS(host=host, request_fence=fence, delivery_owner=DeliveryOwner(fence),
                 allowed_tools=frozenset({"angelo_execute_python"}), voice=None)
    host._runtime_context = current
    return host, current


class DependencyLimitRemedies(unittest.TestCase):
    def binding(self, count, dependency_count=1, *, scene_setting=False, changed=None):
        inputs, dependencies = [object() for _ in range(count)], [object() for _ in range(dependency_count)]
        handles = {obj: "input-%d" % index for index, obj in enumerate(inputs)}
        handles.update({obj: "dependency-%d" % index for index, obj in enumerate(dependencies)})
        backend = native.BlenderProgramBackend.__new__(native.BlenderProgramBackend)
        backend.scene = NS(objects=inputs + dependencies)
        backend.controller = NS()
        backend.check_context = lambda: None
        backend._scene_handle = lambda: "scene"
        backend._scene_revision = lambda: "observed"
        by_handle = {handle: obj for obj, handle in handles.items()}
        backend._resolve = by_handle.get
        backend._revision = lambda obj, handle: "changed" if handle == changed else "observed"
        entries = [{"handle": handles[obj], "revision": "observed"} for obj in inputs]
        if scene_setting:
            entries.append({"handle": "scene", "revision": "observed"})
        arguments = program_spec.validate_program("angelo_scene_program", {"mode": "APPLY", "read_set": entries,
            "write_set": [], "operations": [{"kind": "create_primitive", "target": None,
                "parameters": {"primitive": "CUBE", "name": "New cube", "alias": "new", "location": [0, 0, 0]}}]})
        observed = Mock(side_effect=lambda controller, obj, scene: (handles[obj], "observed"))
        stack = ExitStack()
        stack.enter_context(patch.object(native, "require_cleanup_complete"))
        stack.enter_context(patch.object(native, "admit_input", return_value=0))
        stack.enter_context(patch.object(native, "_fingerprint", return_value={}))
        stack.enter_context(patch.object(native, "observed_binding", observed))
        stack.enter_context(patch.object(native, "undeclared_dependencies",
            side_effect=lambda originals: [obj for obj in dependencies if obj not in originals]))
        return backend, arguments, observed, stack

    def test_overflow_is_a_limit_before_another_dependency_observation(self):
        backend, arguments, observed, stack = self.binding(64)
        with stack, self.assertRaises(ProgramError) as raised:
            backend.bind(arguments)
        self.assertEqual(raised.exception.code, "LIMIT_EXCEEDED")
        self.assertIn("input count is 64", str(raised.exception))
        self.assertIn("dependencies add at least 1 more", str(raised.exception))
        observed.assert_not_called()

    def test_changed_dependency_under_the_cap_remains_stale_with_its_handle(self):
        backend, arguments, observed, stack = self.binding(1, changed="dependency-0")
        with stack, self.assertRaises(ProgramError) as raised:
            backend.bind(arguments)
        self.assertEqual(raised.exception.code, "STALE_DEPENDENCY")
        self.assertEqual(raised.exception.details["handles"], ["dependency-0"])
        self.assertNotIn("more than 64", str(raised.exception))
        self.assertEqual(observed.call_count, 1)

    def test_declared_revision_still_fails_before_dependency_admission(self):
        backend, arguments, observed, stack = self.binding(64, changed="input-0")
        with stack, self.assertRaises(ProgramError) as raised:
            backend.bind(arguments)
        self.assertEqual(raised.exception.code, "STALE_REVISION")
        self.assertEqual(raised.exception.details["handles"], ["input-0"])
        observed.assert_not_called()

    def test_one_input_with_many_dependencies_never_suggests_fewer_inputs(self):
        backend, arguments, observed, stack = self.binding(1, 64)
        with stack, self.assertRaises(ProgramError) as raised:
            backend.bind(arguments)
        self.assertEqual(raised.exception.code, "LIMIT_EXCEEDED")
        self.assertIn("input count is 1", str(raised.exception))
        self.assertNotIn("fewer input", str(raised.exception))
        self.assertNotIn("angelo_execute_python", str(raised.exception))
        self.assertEqual(observed.call_count, 63)

    def test_scene_binding_is_not_miscounted_as_an_object_dependency_root(self):
        backend, arguments, observed, stack = self.binding(63, 2, scene_setting=True)
        with stack, self.assertRaises(ProgramError) as raised:
            backend.bind(arguments)
        self.assertEqual(raised.exception.code, "LIMIT_EXCEEDED")
        self.assertIn("input count is 63", str(raised.exception))
        self.assertIn("dependencies add at least 2 more", str(raised.exception))
        self.assertEqual(observed.call_count, 1)

    def test_helper_uses_the_actual_shared_bound(self):
        with patch.object(program_spec, "MAX_INPUT_OBJECTS", 3), self.assertRaises(ProgramError) as raised:
            program_refusals.check_dependency_limit(3, 2)
        self.assertIn("at most 3 objects", str(raised.exception))
        self.assertIn("fewer input", str(raised.exception))

    def test_script_hint_requires_current_exact_request_ownership(self):
        host, current = runtime()
        self.assertTrue(program_refusals._script_available(host))
        self.assertTrue(program_refusals._script_available(current))
        host._request_generation += 1
        self.assertFalse(program_refusals._script_available(host))
        host._request_generation -= 1
        current.delivery_owner.close()
        self.assertFalse(program_refusals._script_available(host))
        host, current = runtime()
        host._runtime_context = NS()
        self.assertFalse(program_refusals._script_available(current))

    def test_unoffered_or_unknown_script_tools_never_get_a_hint(self):
        host, current = runtime()
        current.allowed_tools = frozenset({"angelo_scene_program"})
        self.assertFalse(program_refusals._script_available(host))
        del current.allowed_tools
        self.assertFalse(program_refusals._script_available(host))
        self.assertFalse(program_refusals._script_available(NS()))

    def test_voice_hint_reads_the_same_filter_as_actual_dynamic_declarations(self):
        host, current = runtime()
        current.voice = object()
        for allowed in (None, frozenset(), frozenset({"angelo_execute_python"}),
                        frozenset({"angelo_scene_program"})):
            current.allowed_tools = allowed
            rows = tool_specs.dynamic_tools(allowed)
            self.assertEqual([tool["name"] for tool in tool_specs.declared_dynamic(allowed)],
                             [tool["name"] for tool in rows])
            self.assertEqual(program_refusals._script_available(host),
                             any(tool["name"] == "angelo_execute_python" for tool in rows))


if __name__ == "__main__":
    unittest.main()
