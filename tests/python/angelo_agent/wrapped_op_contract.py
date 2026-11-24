# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host checks for a plain-data typed-operation declaration (the agent-verbs design, Part E, revisions 1-4), shared
by every module that declares ``WRAPPED_OPS`` before ``wrapped_ops.WrappedOp`` lands. Not a test file itself.

``check_declaration`` proves, without Blender: the declaring module imports nothing; the parameters are a valid JSON
Schema whose defaults validate; every parameter maps to a property of the operator it runs (read from the operator's
source), with the same default and a range inside the property's; and the declared families and creations are the
pattern's names, with levers and conditional creations naming real parameters and values.
``check_guide_examples`` proves the feature guide's example scripts call the operator as declared.
"""

import ast
import json
from pathlib import Path

try:
    import jsonschema
except ImportError:  # CI installs it (run_host_tests --max-skips 0): a missing one is a skip there, never a load error.
    jsonschema = None

from host_test_policy import required_skip  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"

# Revision 2: the families action_schemas._mutations journals, and only these.
FAMILIES = frozenset({
    "object", "transforms", "mesh", "modifiers", "vertex_groups", "materials", "node_groups", "node_trees",
    "world_nodes", "camera", "light", "curve", "armature", "pose", "animation", "nla_stack", "nla", "particles",
    "properties", "data", "visibility", "collections", "scene", "world", "markers"})
CREATES = ({"object"}, {"children"}, {"object", "children"}, {"object_with_children"})
FIELDS = frozenset({"kind", "operator", "source", "target", "mode", "parameters", "map", "reads_only", "creates",
                    "new_ids", "mutates", "input_bound", "job_kind", "job_if", "levers", "creates_if",
                    "checks", "receipt", "guide"})


def _literal(node):
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None   # A name or call (e.g. spec_mod.MAX_COPIES): the range check reads what it can.


def operator_properties(module, idname):
    """{property: {"kind": "IntProperty", "default", "min", "max", ...}} of the operator class with this idname."""
    tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        names = {t.id: _literal(s.value) for s in cls.body if isinstance(s, ast.Assign)
                 for t in s.targets if isinstance(t, ast.Name)}
        if names.get("bl_idname") != idname:
            continue
        props = {}
        for item in cls.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.annotation, ast.Call):
                call = item.annotation
                kind = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
                props[item.target.id] = dict({k.arg: _literal(k.value) for k in call.keywords}, kind=kind)
        return props
    raise AssertionError("no operator %s in %s" % (idname, module))


def _imports(module):
    tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def check_declaration(case, module, declaration, operator_module):
    """Assert one plain-data declaration from ``module`` against its operator in ``operator_module``."""
    case.assertEqual(_imports(module), [], "%s must stay plain data until wrapped_ops lands" % module)
    case.assertLessEqual(set(declaration), FIELDS)
    case.assertIn(declaration["creates"], CREATES + (None,))
    case.assertLessEqual(set(declaration["mutates"]), FAMILIES)
    case.assertTrue(declaration["mutates"], "a mutating operation names what it writes")
    case.assertIs(declaration["reads_only"], False)
    schema = declaration["parameters"]
    if jsonschema is None:
        case.skipTest(required_skip("jsonschema is not installed (CI installs it)"))
    jsonschema.Draft202012Validator.check_schema(schema)
    defaults = {name: spec["default"] for name, spec in schema["properties"].items()}
    jsonschema.validate(defaults, schema)
    json.dumps(schema)   # Canonical JSON: nothing but data.
    case.assertEqual(set(declaration["map"]), set(schema["properties"]))
    if "job_if" in declaration:   # Calls with these values always run as the job, at any size (revision 5).
        parameter, values = declaration["job_if"]
        case.assertTrue(declaration["job_kind"], "job_if sends calls to the operation's job_kind")
        case.assertIn(parameter, schema["properties"])
        case.assertTrue(values and set(values) <= set(schema["properties"][parameter]["enum"]))
    # Revision 4: levers are parameters that shrink the work; creates_if ties a created member to one enum's values.
    case.assertLessEqual(set(declaration.get("levers", ())), set(schema["properties"]))
    for member, (parameter, values) in declaration.get("creates_if", {}).items():
        case.assertIn(member, declaration["creates"] or ())
        case.assertTrue(values)
        case.assertLessEqual(set(values), set(schema["properties"][parameter]["enum"]))
    props = operator_properties(operator_module, declaration["operator"])
    for name, prop_name in declaration["map"].items():
        case.assertIn(prop_name, props, "%s maps to no property of %s" % (name, declaration["operator"]))
        prop, spec = props[prop_name], schema["properties"][name]
        default = prop.get("default")
        if isinstance(default, tuple):
            default = list(default)
        case.assertEqual(spec["default"], default, "%s: default differs from the operator's" % name)
        bounds = spec.get("items", spec) if spec.get("type") == "array" else spec
        for key, op_key, inside in (("minimum", "min", lambda a, b: a >= b), ("maximum", "max", lambda a, b: a <= b)):
            if isinstance(prop.get(op_key), (int, float)):
                case.assertIn(key, bounds, "%s: the operator bounds it, the schema must too" % name)
                case.assertTrue(inside(bounds[key], prop[op_key]), "%s: %s outside the operator's" % (name, key))
        if "enum" in spec and isinstance(prop.get("items"), tuple):
            allowed = {item[0] for item in prop["items"]}
            case.assertLessEqual(set(spec["enum"]), allowed, "%s: values the operator lacks" % name)
    guide = "".join(path.read_text(encoding="utf-8") for path in sorted(ADDON.glob("feature_guide*.py")))
    case.assertIn('Guide("%s"' % declaration["guide"], guide)


def _json(value):
    """A literal as JSON would carry it: tuples as arrays."""
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


def check_registered(case, declaration):
    """The shared registry holds ``declaration`` as registered: the same fields and values, frozen
    (``wrapped_ops.register`` keeps a deep read-only copy, never the module's dict), and refusing writes."""
    from angelo_agent import read_only, wrapped_ops
    registered = wrapped_ops.registry()[declaration["kind"]]
    case.assertEqual(registered, read_only.frozen(declaration))
    with case.assertRaises(TypeError):
        registered["parameters"]["type"] = "changed"


def check_guide_examples(case, declaration):
    """The feature guide's example scripts for this operation call its operator by keyword with arguments its
    parameters accept, and an example of its job (``job_kind``) passes the host's job validator, so the guide cannot
    teach a call the operator or the job refuses or a property it lacks."""
    from angelo_agent import feature_guide
    (guide,) = [g for g in feature_guide.GUIDES if g.id == declaration["guide"]]
    case.assertTrue(guide.examples, "the guide needs a validated example")
    if jsonschema is None:
        case.skipTest(required_skip("jsonschema is not installed (CI installs it)"))
    module, name = declaration["operator"].split(".")
    by_property = {prop: parameter for parameter, prop in declaration["map"].items()}
    calls = 0
    for example in guide.examples:
        if example["tool"] == "angelo_job_start" and example["arguments"].get("kind") == declaration.get("job_kind"):
            # The operation's job (revision 3's job_kind): the host's own validator must take the example.
            from angelo_agent import job_spec
            job_spec.validate_job("angelo_job_start", example["arguments"])
            calls += 1
            continue
        code = example.get("arguments", {}).get("code")
        for node in ast.walk(ast.parse(code)) if code else ():
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name
                    and ast.unparse(node.func.value) == "bpy.ops." + module):
                case.assertFalse(node.args, "operator arguments by keyword")
                arguments = {k.arg: _json(ast.literal_eval(k.value)) for k in node.keywords}
                case.assertLessEqual(set(arguments), set(by_property), "properties the operator lacks")
                jsonschema.validate({by_property[k]: v for k, v in arguments.items()}, declaration["parameters"])
                calls += 1
    case.assertTrue(calls, "an example calls %s or its job" % declaration["operator"])
