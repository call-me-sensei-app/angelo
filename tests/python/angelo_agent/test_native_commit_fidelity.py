# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Commit fidelity: a commit reproduces the staged result its checks measured, or rolls back (``commit_fidelity``).

Dependency-free, with stand-ins for Blender data. Regression: a linked
duplicate's extrude, measured on the stage, was lost at commit and the program
was certified "Host-verified". test_blender_partial_checks.py commits the same
programs in the compiled app; the certificate names objects outside the program
whose shape follows geometry it changed.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import commit_fidelity  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402


class _Data(SimpleNamespace):
    def __init__(self, kind="Mesh", vertices=8, edges=12, faces=6, loops=24, pointer=1):
        super().__init__(bl_rna=SimpleNamespace(identifier=kind), vertices=[0] * vertices, edges=[0] * edges,
                         polygons=[0] * faces, loops=[0] * loops, pointer=pointer)

    def as_pointer(self):
        return self.pointer


class _Object(SimpleNamespace):
    def __init__(self, data, bounds=(0, 0, 0, 1, 1, 1), modifiers=(), matrix=None):
        low, high = bounds[:3], bounds[3:]
        corners = [[(low, high)[(index >> axis) & 1][axis] for axis in range(3)] for index in range(8)]
        super().__init__(data=data, bound_box=corners, modifiers=list(modifiers), animation_data=None,
                         matrix_basis=matrix or [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


def written(live, staged):
    """One written-object row as capture() makes it."""
    return ("Cube", live, staged.data, staged, commit_fidelity._matrix(staged))


class FidelityGuardTests(unittest.TestCase):
    def test_the_same_data_block_passes_whatever_it_holds(self):
        data = _Data()
        self.assertEqual(commit_fidelity.verify(([written(_Object(data), _Object(data))], {})), 1)

    def test_another_block_must_have_the_staged_counts(self):
        live, staged = _Object(_Data(pointer=1)), _Object(_Data(pointer=2))
        self.assertEqual(commit_fidelity.verify(([written(live, staged)], {})), 1, "An unchanged copy passes")
        cases = {"counts": (_Object(_Data(vertices=12, pointer=1)), staged),
                 "type": (_Object(_Data("Camera", pointer=1)), staged)}
        for name, (live_case, staged_case) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(ProgramError, "COMMIT_FIDELITY"):
                commit_fidelity.verify(([written(live_case, staged_case)], {}))

    def test_bounds_are_never_compared(self):
        """Regression: stale or fallback bounds of curves, lattices, hidden objects and data after an
        Undo rolled valid commits back."""
        stale = _Object(_Data(pointer=1), bounds=(0, 0, 0, 0, 0, 0))
        self.assertEqual(commit_fidelity.verify(([written(stale, _Object(_Data(pointer=2), bounds=(0, 0, 0, 5, 5, 5)))],
                                                 {})), 1)

    def test_a_written_objects_local_matrix_is_the_staged_ones(self):
        data = _Data()
        moved = _Object(data, matrix=[[1, 0, 0, 2], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        with self.assertRaisesRegex(ProgramError, "transform"):
            commit_fidelity.verify(([written(_Object(data), moved)], {}))
        animated = _Object(data, matrix=[[1, 0, 0, 2], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        animated.animation_data = SimpleNamespace(action=object(), drivers=[], nla_tracks=[])
        self.assertIsNone(commit_fidelity._matrix(animated), "The stage keeps animated channels as given")

    def test_a_re_pointed_new_object_is_compared_with_its_captured_stage(self):
        staged_data, live_data = _Data(pointer=2), _Data(vertices=12, edges=20, faces=10, loops=40, pointer=1)
        duplicate = _Object(live_data)  # After commit: pointed at its source's live data.
        captured = {2: commit_fidelity._counts(staged_data)}
        with self.assertRaisesRegex(ProgramError, "COMMIT_FIDELITY: the committed geometry of 'Cube.001'"):
            commit_fidelity.verify(([("Cube.001", duplicate, staged_data, None, None)], captured))
        unknown = ([("Cube.002", _Object(live_data), staged_data, None, None)], {})
        with self.assertRaisesRegex(ProgramError, "COMMIT_FIDELITY"):
            commit_fidelity.verify(unknown)  # A new object no capture expected to change: fail closed.

    def test_the_refusal_is_classified_and_names_the_object(self):
        try:
            commit_fidelity.verify(([written(_Object(_Data(vertices=9, pointer=1)), _Object(_Data(pointer=2)))], {}))
        except ProgramError as error:
            self.assertEqual(error.code, "UNSUPPORTED")
            self.assertIn("nothing was changed", str(error))


class DependentsLineTests(unittest.TestCase):
    def test_named_bounded_and_unknown(self):
        line = commit_fidelity.dependents_line({"unchecked_dependents": [{"name": "Other", "uses": "Lat"},
                                                                         {"more": 3}]})
        self.assertEqual(line, "- Not checked: the evaluated shape or placement of 'Other' (uses 'Lat') and 3 more, "
                               "outside this program, which uses an object it changed.")
        self.assertIn("not listed", commit_fidelity.dependents_line({"unchecked_dependents": [{"unknown": True}]}))
        for result in ({}, {"unchecked_dependents": []}, {"unchecked_dependents": "Other"}, None):
            self.assertIsNone(commit_fidelity.dependents_line(result))

    def test_the_certificate_names_them(self):
        from test_native_check_presentation import typed_result
        from angelo_agent.claude_outcomes import certified_completion
        operation, result = typed_result(cubes=1)
        plain = certified_completion([operation], [result], read_only=lambda name: False)
        self.assertNotIn("Not checked", plain)
        result["unchecked_dependents"] = [{"name": "Deformed", "uses": "Cube 0"}]
        named = certified_completion([operation], [result], read_only=lambda name: False)
        self.assertIn("- Not checked: the evaluated shape or placement of 'Deformed' (uses 'Cube 0')", named)


if __name__ == "__main__":
    unittest.main()
