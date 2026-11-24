# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rigging operators' typed wrapped operations (``rigging_wrapped_ops.WRAPPED_OPS``): they register under Part E,
and each matches the operator it wraps, read from that operator's own source (no Blender).

* every parameter is one of the operator's properties, and every enum parameter offers exactly the operator's items,
  with the operator's default;
* the clip export is not wrapped (it writes files; the pattern has no output parameter yet);
* the checks report a fact the snapshot lacks as ``unknown``, never a pass, and fail a source the operation changed.
Expected to fail before rigging_wrapped_ops exists.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import rigging_wrapped_ops, wrapped_op_modules, wrapped_ops  # noqa: E402


def operator_properties():
    """{bl_idname: {property: {"items": [...] or None, "default": value or None}}} from rigging_ops.py and
    ik_controls.py, by reading their class bodies."""
    out = {}
    for name in ("rigging_ops.py", "ik_controls.py"):
        tree = ast.parse((ADDON / name).read_text(encoding="utf-8"))
        helpers = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            idname, props = None, {}
            for item in node.body:
                if isinstance(item, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in item.targets):
                    idname = ast.literal_eval(item.value)
                if isinstance(item, ast.AnnAssign) and isinstance(item.annotation, ast.Call):
                    call = item.annotation
                    func = getattr(call.func, "id", "")
                    keywords = {k.arg: k.value for k in call.keywords}
                    items = None
                    if "items" in keywords:
                        try:
                            items = [entry[0] for entry in ast.literal_eval(keywords["items"])]
                        except ValueError:
                            items = "dynamic"
                    default = None
                    if "default" in keywords:
                        try:
                            default = ast.literal_eval(keywords["default"])
                        except ValueError:
                            default = "dynamic"
                    props[item.target.id] = {"kind": func if func not in helpers else "FloatVectorProperty",
                                             "items": items, "default": default}
            if idname:
                out[idname] = props
    return out


class Declarations(unittest.TestCase):
    def test_they_register_and_the_module_is_listed(self):
        operations = wrapped_ops.register(rigging_wrapped_ops.WRAPPED_OPS)
        self.assertEqual(sorted(operations), sorted(d["kind"] for d in rigging_wrapped_ops.WRAPPED_OPS))
        self.assertIn("rigging_wrapped_ops", wrapped_op_modules.MODULES)

    def test_each_matches_the_operator_it_wraps(self):
        props = operator_properties()
        for declaration in rigging_wrapped_ops.WRAPPED_OPS:
            with self.subTest(declaration["kind"]):
                operator = props[declaration["operator"]]
                for parameter, spec in declaration["parameters"]["properties"].items():
                    prop = operator[declaration["map"][parameter]]
                    if "enum" in spec and spec.get("type") != "array":
                        items = prop["items"]
                        if items == "dynamic":   # Built from data: the engines' profiles.
                            from angelo_agent import skeleton_profiles
                            items = list(skeleton_profiles.PROFILES)
                        self.assertEqual(spec["enum"], items, parameter)
                        self.assertEqual(spec.get("default"), prop["default"], parameter)
                    elif spec.get("type") == "array" and "enum" in spec.get("items", {}):   # An ENUM_FLAG set.
                        self.assertEqual(spec["items"]["enum"], prop["items"], parameter)
                    elif spec.get("type") == "boolean":
                        self.assertEqual(prop["kind"], "BoolProperty", parameter)
                        self.assertEqual(spec.get("default"), prop["default"], parameter)

    def test_the_clip_export_is_not_wrapped(self):
        self.assertNotIn("angelo.export_action_clips", [d["operator"] for d in rigging_wrapped_ops.WRAPPED_OPS])

    def test_checks_never_pass_what_they_cannot_see_and_fail_a_changed_source(self):
        for declaration in rigging_wrapped_ops.WRAPPED_OPS:
            with self.subTest(declaration["kind"]):
                parameters = {"source": "Src", "target": "Tgt", "armature": "Tgt"}
                for check in declaration["checks"](parameters, {}, {}):
                    self.assertEqual(check["status"], "unknown", check)
        before = {"objects": {"Src": {"type": "ARMATURE", "digest": "a"}, "Tgt": {"type": "ARMATURE", "digest": "b"}},
                  "actions": {}, "collections": []}
        after = {"objects": {"Src": {"type": "ARMATURE", "digest": "CHANGED"}, "Tgt": {"type": "ARMATURE",
                                                                                     "digest": "c"}},
                 "actions": {"Walk.retarget": {"digest": "d"}}, "collections": []}
        results = {c["check"]: c["status"] for c in rigging_wrapped_ops.retarget_checks(
            {"source": "Src", "target": "Tgt"}, before, after)}
        self.assertEqual(results, {"source_unchanged": "fail", "one_new_action": "pass", "no_objects_added": "pass"})


if __name__ == "__main__":
    unittest.main()
