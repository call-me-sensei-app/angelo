# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure schema sharing/projection contracts; no Blender or provider requests."""

from collections import Counter
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.schema_compaction import MIN_SHARED_BYTES, compact_schema
from angelo_agent.program_spec import PROGRAM_TOOLS

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def fragment():
    return {'type': 'object', 'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 64, 'pattern': '^[A-Za-z]+$'},
        'count': {'type': 'integer', 'minimum': 1, 'maximum': 8},
        'flags': {'type': 'array', 'items': {'type': 'boolean'}, 'minItems': 1, 'maxItems': 3,
                  'uniqueItems': True, 'default': [True]},
        'mode': {'type': 'string', 'enum': ['SET', 'DELTA'], 'default': 'SET'}},
        'required': ['name', 'count', 'flags', 'mode'], 'additionalProperties': False}


def paired_schema(shared=None):
    shared = fragment() if shared is None else shared
    return {'type': 'object', 'properties': {'left': shared, 'right': copy.deepcopy(shared)},
            'required': ['left', 'right'], 'additionalProperties': False}


def expand_generated(schema):
    """Independent projection of generated root-scoped references.

    Literal dictionaries are copied as data, never interpreted as references.
    The active definition stack rejects any accidental generated recursion.
    """
    definitions = schema.get('$defs', {}) if isinstance(schema, dict) else {}
    schema_maps = {'properties', 'patternProperties', 'dependentSchemas', 'dependencies'}
    schema_lists = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    schema_values = {'additionalProperties', 'additionalItems', 'contains', 'unevaluatedProperties',
                     'unevaluatedItems', 'propertyNames', 'contentSchema', 'not', 'if', 'then', 'else', 'items'}
    references = Counter()

    def expand(node, active=()):
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        if set(node) == {'$ref'}:
            reference = node['$ref']
            if not reference.startswith('#/$defs/'):
                raise AssertionError('Generated references must stay in this document')
            name = reference[len('#/$defs/'):]
            if name not in definitions or name in active:
                raise AssertionError('Unresolved or cyclic generated reference: ' + reference)
            references[name] += 1
            return expand(definitions[name], active + (name,))
        result = {}
        for keyword, value in node.items():
            if keyword in schema_maps and isinstance(value, dict):
                result[keyword] = {name: expand(child, active) if isinstance(child, dict) or type(child) is bool
                                   else copy.deepcopy(child) for name, child in value.items()}
            elif (keyword in schema_lists or keyword == 'items') and isinstance(value, list):
                result[keyword] = [expand(child, active) for child in value]
            elif keyword in schema_values and (isinstance(value, dict) or type(value) is bool):
                result[keyword] = expand(value, active)
            else:
                result[keyword] = copy.deepcopy(value)
        return result

    root = {key: value for key, value in schema.items() if key != '$defs'} if isinstance(schema, dict) else schema
    projection = expand(root)
    if set(references) != set(definitions):
        raise AssertionError('Generated definitions must all be reachable')
    return projection


class SchemaCompactionTests(unittest.TestCase):
    def assert_lossless(self, original):
        before = copy.deepcopy(original)
        compact = compact_schema(original)
        self.assertEqual(original, before)
        self.assertEqual(expand_generated(compact), original)
        self.assertLessEqual(encoded_size(compact), encoded_size(original))
        self.assertEqual(compact_schema(original), compact)
        return compact

    def test_shares_exact_constraints_with_independent_returns(self):
        original = paired_schema()
        compact = self.assert_lossless(original)
        self.assertIn('$defs', compact)
        self.assertEqual(compact['properties']['left'], compact['properties']['right'])
        body = next(iter(compact['$defs'].values()))
        self.assertFalse(body['additionalProperties'])
        body['required'].clear()
        self.assertEqual(len(original['properties']['left']['required']), 4)
        fresh = compact_schema(original)
        self.assertEqual(len(next(iter(fresh['$defs'].values()))['required']), 4)

    def test_deterministic_ids_and_order_for_mapping_order(self):
        original = paired_schema()

        def reverse_maps(value):
            if isinstance(value, dict):
                return {key: reverse_maps(child) for key, child in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse_maps(child) for child in value]
            return value

        first = compact_schema(original)
        second = compact_schema(reverse_maps(original))
        self.assertEqual(json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False))
        self.assertEqual(list(first['$defs']), list(second['$defs']))

    def test_small_or_unique_schema_nodes_are_not_extracted(self):
        small = {'type': 'string', 'maxLength': 64}
        self.assertNotIn('$defs', self.assert_lossless(paired_schema(small)))
        self.assertNotIn('$defs', self.assert_lossless(fragment()))

    def test_minimum_size_is_utf8_bytes_and_inclusive(self):
        # Description is retained verbatim as an annotation, not stripped for size.
        base = {'type': 'string', 'description': ''}
        needed = MIN_SHARED_BYTES - encoded_size(base)
        exact = dict(base, description='x' * needed)
        self.assertEqual(encoded_size(exact), MIN_SHARED_BYTES)
        self.assertIn('$defs', self.assert_lossless(paired_schema(exact)))
        below = dict(base, description='x' * (needed - 1))
        self.assertNotIn('$defs', self.assert_lossless(paired_schema(below)))
        utf8 = dict(base, description='猫' * (needed // 3 + 1))
        self.assertGreaterEqual(encoded_size(utf8), MIN_SHARED_BYTES)
        self.assertIn('$defs', self.assert_lossless(paired_schema(utf8)))

    def test_literals_are_never_traversed_or_replaced(self):
        literal = fragment()
        literal['$ref'] = '#/$defs/not_a_schema_reference'
        literal['$defs'] = {'literal': fragment()}
        original = paired_schema()
        original['default'] = {'left': literal, 'right': copy.deepcopy(literal)}
        original['examples'] = [literal, copy.deepcopy(literal)]
        original['enum'] = [literal, copy.deepcopy(literal)]
        original['const'] = literal
        original['x-vendor-data'] = {'properties': {'first': literal, 'second': literal}}
        compact = self.assert_lossless(original)
        self.assertIn('$defs', compact)  # Literal $defs/$ref must not disable sharing.
        for keyword in ('default', 'examples', 'enum', 'const', 'x-vendor-data'):
            self.assertEqual(compact[keyword], original[keyword])

    def test_property_and_pattern_maps_are_containers_not_schemas(self):
        # Even schema-looking names and values are property-map entries.
        literal_map = {'type': fragment(), 'properties': fragment(), 'default': fragment(), '$ref': fragment()}
        original = {'type': 'object', 'properties': literal_map, 'patternProperties': copy.deepcopy(literal_map),
                    'additionalProperties': False}
        compact = self.assert_lossless(original)
        self.assertEqual(set(compact['properties']), set(literal_map))
        self.assertNotEqual(set(compact['properties']), {'$ref'})

    def test_all_standard_schema_positions_and_legacy_dependency_lists(self):
        shared = fragment()
        cases = [
            {'oneOf': [shared, copy.deepcopy(shared)]},
            {'anyOf': [shared, copy.deepcopy(shared)]},
            {'allOf': [shared, copy.deepcopy(shared)]},
            {'type': 'array', 'prefixItems': [shared, copy.deepcopy(shared)], 'items': False},
            {'type': 'array', 'items': [shared, copy.deepcopy(shared)], 'additionalItems': False},
            {'type': 'array', 'items': shared, 'contains': copy.deepcopy(shared), 'unevaluatedItems': False},
            {'type': 'object', 'additionalProperties': shared, 'unevaluatedProperties': copy.deepcopy(shared)},
            {'if': shared, 'then': copy.deepcopy(shared), 'else': copy.deepcopy(shared), 'not': False},
            {'propertyNames': shared, 'contentSchema': copy.deepcopy(shared)},
            {'type': 'object', 'dependentSchemas': {'one': shared, 'two': copy.deepcopy(shared)}},
            {'type': 'object', 'dependencies': {'one': shared, 'two': copy.deepcopy(shared), 'flags': ['name', 'count']}},
        ]
        for original in cases:
            with self.subTest(keywords=list(original)):
                self.assertIn('$defs', self.assert_lossless(original))

    def test_nested_sharing_has_no_self_refs_or_single_use_definitions(self):
        shared = fragment()
        parent = {'type': 'object', 'properties': {'child': shared}, 'required': ['child'], 'additionalProperties': False}
        compact = self.assert_lossless(paired_schema(parent))
        self.assertEqual(len(compact['$defs']), 1)  # Child appears only once after sharing its parent.
        nested = {'type': 'object', 'properties': {'one': parent, 'two': copy.deepcopy(parent), 'three': shared},
                  'additionalProperties': False}
        compact = self.assert_lossless(nested)
        self.assertEqual(len(compact['$defs']), 2)

    def test_existing_definitions_refs_and_scopes_are_safe_deep_copy_no_ops(self):
        for keyword, value in [('$defs', {'existing': fragment()}), ('definitions', {'existing': fragment()}),
                               ('$ref', '#/$defs/existing'), ('$id', 'https://example.test/schema'),
                               ('id', 'https://example.test/legacy'), ('$anchor', 'node'),
                               ('$dynamicRef', '#node'), ('$dynamicAnchor', 'node'),
                               ('$recursiveRef', '#'), ('$recursiveAnchor', True),
                               ('$vocabulary', {'https://example.test/vocabulary': True})]:
            original = paired_schema()
            original['properties']['left'][keyword] = value
            before = copy.deepcopy(original)
            with self.subTest(keyword=keyword):
                result = compact_schema(original)
                self.assertEqual(result, original)
                self.assertIsNot(result, original)
                result['properties']['left']['required'].clear()
                self.assertEqual(original, before)

    def test_recursive_references_are_preserved_without_expansion(self):
        original = {'$defs': {'node': {'type': 'object', 'properties': {
            'value': {'type': 'integer'}, 'next': {'anyOf': [{'$ref': '#/$defs/node'}, {'type': 'null'}]}},
            'required': ['value', 'next'], 'additionalProperties': False}}, '$ref': '#/$defs/node'}
        compact = compact_schema(original)
        self.assertEqual(compact, original)
        self.assertEqual(compact_schema(compact), original)

    def test_booleans_invalid_roots_and_python_cycles(self):
        self.assertIs(compact_schema(True), True)
        self.assertIs(compact_schema(False), False)
        for invalid in (None, [], 'object', 1):
            with self.assertRaises(TypeError):
                compact_schema(invalid)
        cyclic = {'type': 'array'}
        cyclic['items'] = cyclic
        with self.assertRaises(ValueError):
            compact_schema(cyclic)

    def test_static_compaction_is_content_cached_and_returns_are_not_shared(self):
        from angelo_agent.schema_compaction import _compact_serialized
        original = paired_schema()
        original['description'] = 'Independent cache fixture'
        before = _compact_serialized.cache_info()
        first = compact_schema(original)
        middle = _compact_serialized.cache_info()
        second = compact_schema(copy.deepcopy(original))
        after = _compact_serialized.cache_info()
        self.assertEqual(middle.misses, before.misses + 1)
        self.assertEqual(after.misses, middle.misses)
        self.assertEqual(after.hits, middle.hits + 1)
        first['properties'].clear()
        self.assertEqual(expand_generated(second), original)

    def test_a_smaller_sharing_threshold_stays_lossless_and_is_bounded(self):
        program = PROGRAM_TOOLS[0]['inputSchema']
        default, finer = compact_schema(program), compact_schema(program, min_shared_bytes=40)
        self.assertEqual(expand_generated(finer), program)
        self.assertLess(encoded_size(finer), encoded_size(default))
        self.assertGreater(len(finer['$defs']), len(default['$defs']))
        self.assertEqual(compact_schema(program), default, 'The default threshold is unchanged')
        for invalid in (31, 0, 100.0, True, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                compact_schema(program, min_shared_bytes=invalid)

    def test_program_and_complete_wrapped_plan_projection_and_size(self):
        schemas = [tool['inputSchema'] for tool in PROGRAM_TOOLS]
        program = schemas[0]
        compact = self.assert_lossless(program)
        self.assertLess(encoded_size(compact), encoded_size(program))
        # Each dynamic function gets a separately scoped schema. For a plan,
        # compact AFTER wrapping raw schemas, so all refs share the plan root.
        for schema in schemas:
            self.assert_lossless(schema)
        plan = {'type': 'object', 'properties': {
            'reply': {'type': 'string'}, 'operations': {'type': 'array', 'maxItems': 64, 'items': {'oneOf': [
                {'type': 'object', 'properties': {'tool': {'const': tool['name']}, 'arguments': tool['inputSchema']},
                 'required': ['tool', 'arguments'], 'additionalProperties': False} for tool in PROGRAM_TOOLS]}},
            'verify': {'type': 'boolean'}}, 'required': ['reply', 'operations', 'verify'], 'additionalProperties': False}
        compact_plan = self.assert_lossless(plan)
        self.assertLess(encoded_size(compact_plan), encoded_size(plan))

    @unittest.skipIf(Draft202012Validator is None, 'jsonschema is not installed; exact projection tests still run')
    def test_jsonschema_valid_and_invalid_vectors_are_unchanged(self):
        original = paired_schema()
        compact = compact_schema(original)
        Draft202012Validator.check_schema(compact)
        valid_item = {'name': 'Cube', 'count': 2, 'flags': [True, False], 'mode': 'SET'}
        valid = {'left': valid_item, 'right': copy.deepcopy(valid_item)}
        vectors = [(valid, True), ({}, False), (dict(valid, unexpected=True), False)]
        for key, bad in [('name', ''), ('name', 'with space'), ('count', True), ('count', 0), ('count', 9),
                         ('flags', []), ('flags', [True, True]), ('flags', [True, False, True, False]),
                         ('flags', ['yes']), ('mode', 'PYTHON')]:
            value = copy.deepcopy(valid)
            value['left'][key] = bad
            vectors.append((value, False))
        missing = copy.deepcopy(valid)
        del missing['left']['mode']
        vectors.append((missing, False))
        vectors.append((dict(valid, left=dict(valid_item, extra='not allowed')), False))
        for value, expected in vectors:
            with self.subTest(value=value):
                self.assertEqual(Draft202012Validator(original).is_valid(value), expected)
                self.assertEqual(Draft202012Validator(compact).is_valid(value), expected)

    @unittest.skipIf(Draft202012Validator is None, 'jsonschema is not installed')
    def test_jsonschema_recursive_no_op_validity(self):
        recursive = {'$defs': {'node': {'anyOf': [{'type': 'null'}, {'type': 'object', 'properties': {
            'value': {'type': 'integer'}, 'next': {'$ref': '#/$defs/node'}},
            'required': ['value', 'next'], 'additionalProperties': False}]}}, '$ref': '#/$defs/node'}
        compact = compact_schema(recursive)
        for value, expected in [(None, True), ({'value': 1, 'next': {'value': 2, 'next': None}}, True),
                                ({'value': True, 'next': None}, False), ({'value': 1, 'next': {}}, False)]:
            self.assertEqual(Draft202012Validator(recursive).is_valid(value), expected)
            self.assertEqual(Draft202012Validator(compact).is_valid(value), expected)

    @unittest.skipIf(Draft202012Validator is None, 'jsonschema is not installed')
    def test_jsonschema_program_valid_and_invalid_vectors(self):
        original = PROGRAM_TOOLS[0]['inputSchema']
        compact = compact_schema(original)
        Draft202012Validator.check_schema(compact)
        valid = {'mode': 'PREVIEW', 'read_set': [], 'write_set': [], 'operations': [
            {'kind': 'create_primitive', 'target': None, 'parameters': {'primitive': 'CUBE'}}]}
        vectors = [(valid, True)]
        for field, bad in [('mode', 'PYTHON'), ('read_set', [{}]), ('write_set', [{'handle': 'a', 'revision': ''}]),
                           ('operations', []), ('operations', valid['operations'] * 33)]:
            value = copy.deepcopy(valid)
            value[field] = bad
            vectors.append((value, False))
        for key, bad in [('primitive', 'UNKNOWN'), ('size', 0), ('size', 1001), ('size', True), ('python', 'exec')]:
            value = copy.deepcopy(valid)
            value['operations'][0]['parameters'][key] = bad
            vectors.append((value, False))
        value = copy.deepcopy(valid)
        value['operations'][0]['target'] = 'doc:object:existing'
        vectors.append((value, False))
        for value, expected in vectors:
            with self.subTest(value=value):
                self.assertEqual(Draft202012Validator(original).is_valid(value), expected)
                self.assertEqual(Draft202012Validator(compact).is_valid(value), expected)


if __name__ == '__main__':
    unittest.main()
