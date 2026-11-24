# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout's run-time tables and its ending paths, without Blender (doc/angelo/uv_layout.rst; the review of
86b9af0f149, S1, S2, S4 and N1).

* Every module-level table a later layout reads is read-only, so an agent script cannot change in place what the
  person's own later layouts do (``rotate``'s default, which packer Fast means, the operator's parameter names). Only
  the schemas a model is sent stay plain data, as 2.0.0's read-only rule allows.
* The operation's declaration holds its own sets as frozensets.
* A layout never ends half way: a refused pack, a fault in the modal and a failed cleanup all end it with its cleanup
  done, and the hand path never starts while a job locks the interface.
``test_blender_uv_layout`` proves the tables and a mesh changed between packs in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import uv_layout_ops  # noqa: E402

MODULES = ("job_uv_layout.py", "job_uv_layout_spec.py", "uv_layout.py", "uv_layout_job.py", "uv_layout_math.py",
           "uv_layout_ops.py", "uv_layout_ui.py")
# Sent to a model as JSON schema, plain data (read_only.py): the checks read frozen copies of them.
MODEL_SCHEMAS = {("uv_layout_ops.py", "PARAMETERS"), ("job_uv_layout_spec.py", "SCHEMA")}
PLAIN = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)


def _tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def _function(tree, name, owner=None):
    scope = tree.body if owner is None else next(n for n in tree.body if isinstance(n, ast.ClassDef)
                                                and n.name == owner).body
    return next(n for n in scope if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls_inside_try(function, text):
    """Whether every call whose source contains ``text`` sits in the body of a ``try`` with a handler."""
    source = ast.unparse(function)
    found, guarded = 0, 0
    for node in ast.walk(function):
        if isinstance(node, ast.Try) and node.handlers:
            guarded += sum(text in ast.unparse(statement) for statement in node.body)
    found = source.count(text)
    return found > 0 and guarded >= found


class Tables(unittest.TestCase):
    def test_no_table_a_later_layout_reads_is_plain(self):
        plain = set()
        for name in MODULES:
            for node in _tree(name).body:
                targets = node.targets if isinstance(node, ast.Assign) else []
                for target in targets:
                    if (isinstance(target, ast.Name) and target.id.lstrip("_").isupper()
                            and isinstance(node.value, PLAIN)):
                        plain.add((name, target.id))
        self.assertEqual(plain - MODEL_SCHEMAS, set())
        self.assertEqual(MODEL_SCHEMAS - plain, set(), "stale entries")

    def test_the_operator_reads_its_names_from_a_tuple(self):
        ui = (ADDON / "uv_layout_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("PARAMETERS", ui)
        self.assertEqual(uv_layout_ops.NAMES, tuple(uv_layout_ops.PARAMETERS["properties"]))

    def test_the_declarations_sets_are_frozen(self):
        declaration = uv_layout_ops.WRAPPED_OPS[0]
        self.assertIsInstance(declaration["mutates"], frozenset)
        self.assertIsInstance(declaration["job_if"], tuple)
        self.assertIsInstance(declaration["job_if"][1], frozenset)


class Endings(unittest.TestCase):
    def test_a_refused_pack_ends_the_layout_failed(self):
        advance = _function(_tree("uv_layout_job.py"), "advance", "Layout")
        self.assertTrue(_calls_inside_try(advance, "uv_layout.pack("))

    def test_a_failed_cleanup_still_ends_it(self):
        close = _function(_tree("uv_layout_job.py"), "_close", "Layout")
        self.assertTrue(_calls_inside_try(close, "uv_layout.finish("))
        restore = _function(_tree("uv_layout.py"), "_restore")
        self.assertIn("missed.append(name)", ast.unparse(restore))
        self.assertNotIn("bpy.data.objects[name]", ast.unparse(restore))   # .get: a renamed mesh is named, not raised

    def test_a_fault_in_the_modal_still_ends_it(self):
        modal = _function(_tree("uv_layout_ui.py"), "modal", "ANGELO_OT_uv_layout_run")
        self.assertTrue(_calls_inside_try(modal, "self._layout.advance("))

    def test_a_fault_ends_it_the_deferred_way_first(self):
        """As the job runner does (review 2, NIT-3): the first fault asks the layout to fail, and the next
        ``advance`` puts the meshes back once no pack runs; only a second fault puts them back at once, and never
        while a job locks the interface."""
        handler = next(n for n in ast.walk(_function(_tree("uv_layout_ui.py"), "modal", "ANGELO_OT_uv_layout_run"))
                       if isinstance(n, ast.ExceptHandler))
        source = ast.unparse(handler)
        self.assertIn("self._layout.fail(", source)
        self.assertLess(source.index("self._layout.fail("), source.index("self._layout.abandon("))
        self.assertLess(source.index("is_interface_locked"), source.index("self._layout.abandon("))

    def test_the_job_consults_the_lock_before_entering_edit_mode(self):
        poll = ast.unparse(_function(_tree("job_uv_layout.py"), "poll", "UvLayoutRunner"))
        self.assertLess(poll.index("is_interface_locked"), poll.index("mode_set(mode='EDIT')"))

    def test_the_topology_check_runs_at_c_speed(self):
        """Every re-read compares each mesh's topology (review 2, NIT-4). At 1M corners on the pool VM, under one
        load: a Python walk over the corners 468 ms; reading them per loop (``MeshLoop.vertex_index``) 113 ms; the
        ``.corner_vert`` attribute array 0.4 ms (the copy into the scratch mesh 16, the faces' starts 9)."""
        topology = ast.unparse(_function(_tree("uv_layout.py"), "_topology"))
        self.assertIn("foreach_get(", topology)
        self.assertIn("attributes.get('.corner_vert')", topology)
        self.assertLess(topology.index(".corner_vert"), topology.index("'vertex_index'"))   # Per loop: only a fallback.
        self.assertNotIn("for loop in", topology)
        self.assertNotIn("for face in", topology)

    def test_the_hand_path_never_starts_under_a_lock(self):
        invoke = _function(_tree("uv_layout_ui.py"), "invoke", "ANGELO_OT_uv_layout_run")
        first = invoke.body[0]
        self.assertIsInstance(first, ast.If)
        self.assertIn("is_interface_locked", ast.unparse(first.test))
        self.assertIn("CANCELLED", ast.unparse(first.body))

    def test_every_re_read_checks_the_meshes_are_as_fixed(self):
        steps = ast.unparse(_function(_tree("uv_layout.py"), "steps"))
        self.assertGreater(steps.count("_rebind(objects, fixed, shapes)"), 0)
        self.assertEqual(steps.count("_rebind(objects, fixed)"), 0)


class SearchDefault(unittest.TestCase):
    """Search is off by default and stays an option. On the held-out benchmark (2026-11-18,
    doc/angelo/uv_layout.rst, "Benchmark against Blender's packer") it found no tighter layout on any of five held-out
    meshes and cost 15-44% more time, so no text may claim it packs tighter."""

    def test_search_is_off_by_default_everywhere(self):
        self.assertEqual(uv_layout_ops.PARAMETERS["properties"]["search"]["default"], 0)   # The agent's.
        layout = (ADDON / "uv_layout.py").read_text(encoding="utf-8")
        self.assertIn('"search": 0,', layout[layout.index("DEFAULTS = "):][:400])            # A layout's.
        ui = (ADDON / "uv_layout_ui.py").read_text(encoding="utf-8")
        self.assertIn('search: bpy.props.IntProperty(name="Search", default=0,', ui)      # The dialog's.
        doc = (ROOT / "doc/angelo/uv_layout.rst").read_text(encoding="utf-8")
        self.assertRegex(doc, r"``search``\s+Pack Islands' Search for each tile, 0-256 \(0\)")

    def test_no_text_claims_search_packs_tighter(self):
        # A claim, not its denial: "Search found no tighter layout" (the benchmark's result) is not one.
        claim = re.compile(r"Search[^.]{0,40}tighter|tighter Pack Islands|Search[^.]{0,40}(better|denser) (layout|pack)",
                           re.IGNORECASE)
        denial = re.compile(r"\b(no|not|never)\b", re.IGNORECASE)
        for path in (ADDON / "feature_guide.py", ADDON / "feature_guide_scene.py", ROOT / "doc/angelo/uv_layout.rst",
                     ROOT / "doc/angelo/features.rst",
                     ADDON / "uv_layout_ops.py", ADDON / "uv_layout_ui.py"):
            claims = [m.group(0) for m in claim.finditer(path.read_text(encoding="utf-8"))
                      if not denial.search(m.group(0))]
            self.assertEqual(claims, [], path.name)


if __name__ == "__main__":
    unittest.main()
