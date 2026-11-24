# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The typed wrapped operations' registry enforces the declaration contract (agent-verbs design, Part E).

Each lane declares its wrapped operations as plain data (``WRAPPED_OPS``). ``wrapped_ops`` is the shared code that
registers them, and it refuses any declaration that breaks Part E: unknown or missing fields, a kind that is already
a typed kind or declared twice, an operator, source, target, mode or ID type that is not one of Blender's names,
writes that are not the families the program transaction journals, parameters that are not a closed JSON object
mapped one to one onto operator properties, and the shared budget's rules (``wrapped_op_budget``). ``admit`` refuses a
call over the turn budget by its registered kind. Every add-on module that declares ``WRAPPED_OPS`` must be listed in
``wrapped_op_modules.MODULES``, so a lane cannot ship a declaration the registry never checks.

Dependency-free: plain declarations, no Blender. Blender's own names are read from its RNA sources.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import copy
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
RNA = ROOT / "source/blender/makesrna/intern"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import refusal_codes, wrapped_op_modules, wrapped_ops  # noqa: E402

PARAMETERS = {"type": "object", "additionalProperties": False, "properties": {
    "count": {"type": "integer", "minimum": 1, "maximum": 400, "default": 1},
    "method": {"enum": ["SLOW", "QUICK"], "default": "QUICK"},
    "style": {"enum": ["PLAIN", "FANCY"], "default": "PLAIN"}}}


def no_checks(parameters, before, after):
    return []


def declaration(**fields):
    base = dict(kind="test_op", operator="angelo.test_op", source="angelo", target="MESH", mode="OBJECT",
                parameters=PARAMETERS, map={name: name for name in PARAMETERS["properties"]}, reads_only=False,
                creates=None, mutates={"modifiers"}, input_bound={"triangles": 1000}, job_kind=None,
                levers=("count",), checks=no_checks, guide="test_op")
    base.update(fields)
    return base


def without(name):
    return {key: value for key, value in declaration().items() if key != name}


class Registration(unittest.TestCase):
    def refused(self, *declarations, words=None):
        with self.assertRaises(ValueError) as caught:
            wrapped_ops.register(declarations)
        if words:
            self.assertIn(words, str(caught.exception))
        return str(caught.exception)

    def test_a_complete_declaration_registers_read_only(self):
        operations = wrapped_ops.register([declaration()])
        self.assertEqual(list(operations), ["test_op"])
        self.assertEqual(operations["test_op"]["operator"], "angelo.test_op")
        with self.assertRaises(TypeError):
            operations["test_op"]["mode"] = "SCULPT"

    def test_refusals_name_the_kind(self):
        self.assertIn("test_op", self.refused(declaration(mode="EDIT")))

    def test_unknown_fields_are_refused(self):
        self.refused(declaration(job_knd="UV_LAYOUT"), words="job_knd")

    def test_every_required_field_is_needed(self):
        for name in sorted(wrapped_ops.REQUIRED):
            with self.subTest(field=name):
                self.refused(without(name))

    def test_optional_fields_may_be_left_out(self):
        bare = {name: value for name, value in declaration().items() if name in wrapped_ops.REQUIRED}
        self.assertIn("test_op", wrapped_ops.register([bare]))


class Identity(Registration):
    def test_kind_is_a_lowercase_name(self):
        for kind in ("Test Op", "", "x" * 49, 3, "test-op", "_test"):
            with self.subTest(kind=kind):
                self.refused(declaration(kind=kind))

    def test_kind_is_not_already_a_typed_kind(self):
        self.refused(declaration(kind="transform"), words="transform")

    def test_kind_is_declared_once(self):
        self.refused(declaration(), declaration(operator="angelo.other"), words="test_op")

    def test_operator_is_an_operator_idname(self):
        for idname in ("angelo_test_op", "bpy.ops.angelo.test_op", "ANGELO.test_op", "angelo.", None):
            with self.subTest(operator=idname):
                self.refused(declaration(operator=idname))

    def test_source_is_angelo_or_one_bundled_extension(self):
        for source in ("angelo", "bundled:bool_tool"):
            wrapped_ops.register([declaration(source=source)])
        for source in ("bundled:", "blender", "bundled:bool-tool", "bundled:a b", None):
            with self.subTest(source=source):
                self.refused(declaration(source=source))

    def test_target_is_an_object_type_or_the_scene(self):
        for target in (None, "MESH", "ARMATURE", "POINTCLOUD", "CURVE"):
            wrapped_ops.register([declaration(target=target)])
        for target in ("mesh", "BONE", "SCENE", ""):
            with self.subTest(target=target):
                self.refused(declaration(target=target))

    def test_mode_is_a_context_mode(self):
        for mode in ("OBJECT", "EDIT_MESH", "POSE", "SCULPT"):
            wrapped_ops.register([declaration(mode=mode)])
        for mode in ("EDIT", "object", None):   # EDIT is object.mode_set's name, not the mode the context reports.
            with self.subTest(mode=mode):
                self.refused(declaration(mode=mode))


class Writes(Registration):
    def test_reads_only_is_a_boolean(self):
        self.refused(declaration(reads_only=0))

    def test_a_read_writes_nothing(self):
        clean = dict(reads_only=True, mutates=(), creates=None)
        wrapped_ops.register([declaration(**clean)])
        for extra in (dict(mutates={"mesh"}), dict(creates={"object"}), dict(new_ids=("ACTION",))):
            with self.subTest(extra=extra):
                self.refused(declaration(**dict(clean, **extra)))

    def test_a_mutation_names_what_it_writes(self):
        self.refused(declaration(mutates=(), creates=None))
        wrapped_ops.register([declaration(mutates=(), creates={"object"})])   # Keeps its source, makes a copy.
        wrapped_ops.register([declaration(mutates=(), new_ids=("NODETREE",))])

    def test_mutates_names_journaled_families_only(self):
        for families in ({"mesh", "modifiers"}, ("scene",), frozenset({"node_trees"})):
            wrapped_ops.register([declaration(mutates=families)])
        for families in ({"uv_maps"}, {"Mesh"}, "mesh", {"mesh": True}):
            with self.subTest(mutates=families):
                self.refused(declaration(mutates=families))

    def test_creates_is_one_of_the_pattern_sets(self):
        for creates in ({"object"}, {"children"}, {"object", "children"}, {"object_with_children"},
                        frozenset({"children"})):
            wrapped_ops.register([declaration(creates=creates)])
        for creates in ({"object_with_children", "children"}, {"objects"}, "object", set()):
            with self.subTest(creates=creates):
                self.refused(declaration(creates=creates))

    def test_new_ids_are_blender_id_types_other_than_objects(self):
        wrapped_ops.register([declaration(new_ids=("COLLECTION", "ACTION"))])
        for new_ids in (("OBJECT",), ("COLLECTIONS",), "COLLECTION", ()):
            with self.subTest(new_ids=new_ids):
                self.refused(declaration(new_ids=new_ids))

    def test_creates_if_ties_a_created_member_to_enum_values(self):
        made = {"object", "children"}
        wrapped_ops.register([declaration(creates=made, creates_if={"object": ("style", {"FANCY"})})])
        for creates_if in ({"object_with_children": ("style", {"FANCY"})},   # Not a member this operation creates.
                           {"object": ("count", {1})}, {"object": ("style", {"GOLD"})},
                           {"object": ("style", set())}, {"object": "style"}):
            with self.subTest(creates_if=creates_if):
                self.refused(declaration(creates=made, creates_if=creates_if))


class Parameters(Registration):
    def test_parameters_are_a_closed_json_object(self):
        open_ = dict(PARAMETERS, additionalProperties=True)
        loose = {key: value for key, value in PARAMETERS.items() if key != "additionalProperties"}
        not_json = dict(PARAMETERS, properties=dict(PARAMETERS["properties"], count={"default": math.nan}))
        unknown_required = dict(PARAMETERS, required=["size"])
        for schema in ({"type": "array"}, open_, loose, not_json, unknown_required, None):
            with self.subTest(parameters=schema):
                self.refused(declaration(parameters=schema))

    def test_no_parameters_at_all(self):
        empty = {"type": "object", "additionalProperties": False, "properties": {}}
        wrapped_ops.register([declaration(parameters=empty, map={}, levers=())])

    def test_map_sends_each_parameter_to_its_own_property(self):
        full = {name: name for name in PARAMETERS["properties"]}
        for mapping in ({"count": "count"}, dict(full, size="size"), dict(full, style=""),
                        dict(full, style="method"), dict(full, style=3)):
            with self.subTest(map=mapping):
                self.refused(declaration(map=mapping))
        wrapped_ops.register([declaration(map=dict(full, style="use_style"))])

    def test_checks_and_receipt_are_functions_and_guide_is_a_name(self):
        wrapped_ops.register([declaration(receipt=lambda after: {})])
        for fields in (dict(checks=None), dict(checks="checks"), dict(receipt={}), dict(guide=""), dict(guide=7)):
            with self.subTest(fields=fields):
                self.refused(declaration(**fields))


class Budget(Registration):
    def test_the_shared_budget_rules_apply(self):
        self.refused(declaration(budget_s=None), words="TURN_BUDGET_S")
        self.refused(declaration(job_kind="NOT_A_JOB"), words="NOT_A_JOB")
        self.refused(declaration(levers=("size",)), words="size")
        self.refused(declaration(input_bound={"triangles": 0}))

    def test_job_kind_is_one_of_the_hosts_jobs(self):
        wrapped_ops.register([declaration(job_kind="RENDER_STILL", job_if=("method", {"SLOW"}))])

    def test_job_if_parameter_has_a_value_when_left_out(self):
        """Left out with no default, the operator's own default would decide, which may be the slow value."""
        open_method = {"enum": ["SLOW", "QUICK"]}
        schema = dict(PARAMETERS, properties=dict(PARAMETERS["properties"], method=open_method))
        self.refused(declaration(parameters=schema, job_kind="RENDER_STILL", job_if=("method", {"SLOW"})),
                     words="method")
        required = dict(schema, required=["method"])
        wrapped_ops.register([declaration(parameters=required, job_kind="RENDER_STILL", job_if=("method", {"SLOW"}))])


class Admission(unittest.TestCase):
    def setUp(self):
        self.operations = wrapped_ops.register([declaration(job_kind="RENDER_STILL", job_if=("method", {"SLOW"}))])

    def test_admit_reads_the_registered_declaration(self):
        self.assertIsNone(wrapped_ops.admit("test_op", {"triangles": 1000}, {}, operations=self.operations))
        with self.assertRaises(refusal_codes.RefusalError) as caught:
            wrapped_ops.admit("test_op", {"triangles": 1001}, {}, operations=self.operations)
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(caught.exception.details["job_kind"], "RENDER_STILL")
        with self.assertRaises(refusal_codes.RefusalError) as caught:
            wrapped_ops.admit("test_op", {"triangles": 1}, {"method": "SLOW"}, operations=self.operations)
        self.assertEqual(caught.exception.details["job_if"], {"method": "SLOW"})

    def test_a_declaration_edited_after_it_registers_changes_nothing_admit_reads(self):
        """The uv-pack lock-bypass review (S1): a one-level copy left the declaring module's own nested objects in
        the registry, so a script editing them in place changed what ``admit`` read. Every nested value is
        frozen as it registers: the parameters, their enums and defaults, the map and job_if's values."""
        parameters = copy.deepcopy(PARAMETERS)
        values = {"QUICK"}
        original = declaration(parameters=parameters, map={name: name for name in parameters["properties"]},
                               job_kind="RENDER_STILL", job_if=("method", values))
        operations = wrapped_ops.register([original])
        parameters["properties"]["method"]["default"] = "SLOW"   # What a script could do to the module's dict.
        parameters["properties"]["method"]["enum"].append("FAST")
        values.discard("QUICK")
        original["map"]["count"] = "other"
        with self.assertRaises(refusal_codes.RefusalError) as caught:   # Left out, method is still QUICK: a job.
            wrapped_ops.admit("test_op", {"triangles": 1}, {}, operations=operations)
        self.assertEqual(caught.exception.details["job_if"], {"method": "QUICK"})
        registered = operations["test_op"]
        self.assertEqual(list(registered["parameters"]["properties"]["method"]["enum"]), ["SLOW", "QUICK"])
        self.assertEqual(registered["map"]["count"], "count")
        self.assertIs(registered["checks"], no_checks)   # Functions are kept as declared.
        for nested in (registered["parameters"], registered["parameters"]["properties"]["method"], registered["map"]):
            with self.assertRaises(TypeError):
                nested["default"] = "SLOW"
        with self.assertRaises(AttributeError):
            registered["job_if"][1].discard("QUICK")
        with self.assertRaises(AttributeError):
            registered["parameters"]["properties"]["method"]["enum"].append("FAST")

    def test_a_model_is_sent_plain_json_from_the_registry_never_its_frozen_copy(self):
        """What a model is sent about a wrapped operation (its parameters) comes from the registered declaration as
        plain JSON, a fresh copy each time (the uv-pack re-read, SHOULD-1): never the frozen copy, which a JSON
        encoder refuses, and never the declaring module's own dict, which a script could have changed."""
        import json
        parameters = copy.deepcopy(PARAMETERS)
        operations = wrapped_ops.register([declaration(parameters=parameters,
                                                       map={name: name for name in parameters["properties"]})])
        parameters["properties"]["method"]["default"] = "SLOW"   # After registering: not what the model is told.
        schema = wrapped_ops.model_schema("test_op", operations=operations)
        self.assertEqual(json.loads(json.dumps(schema)), schema)   # Plain JSON, as registered.
        self.assertEqual(schema["properties"]["method"], {"enum": ["SLOW", "QUICK"], "default": "QUICK"})
        schema["properties"]["method"]["default"] = "SLOW"   # The caller's own copy:
        self.assertEqual(wrapped_ops.model_schema("test_op", operations=operations)["properties"]["method"]["default"],
                         "QUICK")
        with self.assertRaises(ValueError):
            wrapped_ops.model_schema("unknown_op", operations=operations)

    def test_admit_refuses_a_kind_never_registered(self):
        with self.assertRaises(ValueError):
            wrapped_ops.admit("unknown_op", {"triangles": 1}, {}, operations=self.operations)


class TheAddOn(unittest.TestCase):
    def test_every_declaring_module_is_listed(self):
        """A module that declares WRAPPED_OPS but is not listed would never be checked."""
        declaring = sorted(path.stem for path in ADDON.glob("*.py")
                           if re.search(r"^WRAPPED_OPS\s*=", path.read_text(encoding="utf-8"), re.MULTILINE))
        self.assertEqual(sorted(wrapped_op_modules.MODULES), declaring)
        self.assertEqual(len(set(wrapped_op_modules.MODULES)), len(wrapped_op_modules.MODULES))

    def test_the_add_on_builds_the_registry_as_it_registers(self):
        """Before any agent script runs, so no declaration is read after a script could edit it (the uv-pack
        lock-bypass review, S1): ``registry()`` builds once, at the add-on's ``register``."""
        tree = ast.parse((ADDON / "__init__.py").read_text(encoding="utf-8"))
        register = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "register")
        calls = [ast.unparse(n.func) for n in ast.walk(register) if isinstance(n, ast.Call)]
        self.assertIn("wrapped_ops.registry", calls)

    def test_the_add_ons_declarations_register(self):
        operations = wrapped_ops.registry()
        self.assertEqual(sorted(operations),
                         sorted(d["kind"] for name in wrapped_op_modules.MODULES
                                for d in __import__("angelo_agent." + name, fromlist=["WRAPPED_OPS"]).WRAPPED_OPS))


class RegisteredReads(unittest.TestCase):
    def test_no_test_reads_a_registered_declaration_as_a_one_level_dict(self):
        """A registered declaration is a deep read-only copy: dict() of it keeps frozen values inside, so a comparison
        with the module's plain declaration fails (shape_cut, then uv_layout, at merge). Lanes compare through
        wrapped_op_contract.check_registered."""
        import re
        found = [path.name for path in sorted(Path(__file__).resolve().parent.glob("test_*.py"))
                 if re.search(r"dict\(\s*wrapped_ops\.registry\(\)", path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "use wrapped_op_contract.check_registered")


class OptionalModules(unittest.TestCase):
    def test_test_modules_import_jsonschema_only_guarded(self):
        """A host test module or helper that imports jsonschema bare fails to load where it is missing (Blender's own
        Python): an import ERROR that hides every test the module holds, where a guarded import reads as a skip, which
        CI's --max-skips 0 still fails (v210-rigging on release/v2.1.0 c91073340b5: wrapped_op_contract)."""
        import ast
        bare = []
        for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:   # Module level, outside any try: what runs as the module loads.
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) else [])
                if any((name or "").split(".")[0] == "jsonschema" for name in names):
                    bare.append("%s:%d" % (path.name, node.lineno))
        self.assertEqual(bare, [], "import jsonschema inside try/except ImportError and skip where it is needed")


def rna_identifiers(filename, array):
    """The identifiers of one ``EnumPropertyItem`` array in Blender's RNA source, its item macros resolved."""
    source = (RNA / filename).read_text(encoding="utf-8")
    macros = dict(re.findall(r'#define (\w+) \{\w+, "([A-Z_0-9]+)"', source))
    body = re.search(r"const EnumPropertyItem %s\[\] = \{(.*?)\n\};" % array, source, re.DOTALL).group(1)
    names = []
    for line in body.splitlines():
        line = line.strip()
        item = re.match(r'\{\w+, "([A-Z_0-9]+)"', line)
        if item:
            names.append(item.group(1))
        elif line.rstrip(",") in macros:
            names.append(macros[line.rstrip(",")])
    return frozenset(names)


class BlenderNames(unittest.TestCase):
    """The names a declaration may use are Blender's own, read from the RNA source of the Blender Angelo builds."""

    def test_modes_are_the_context_modes(self):
        self.assertEqual(wrapped_ops.MODES, rna_identifiers("rna_context.cc", "rna_enum_context_mode_items"))

    def test_targets_are_the_object_types(self):
        self.assertEqual(wrapped_ops.OBJECT_TYPES, rna_identifiers("rna_object.cc", "rna_enum_object_type_items"))

    def test_new_ids_are_the_id_types(self):
        self.assertEqual(wrapped_ops.ID_TYPES, rna_identifiers("rna_ID.cc", "rna_enum_id_type_items") - {"OBJECT"})


if __name__ == "__main__":
    unittest.main()
