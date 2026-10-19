# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh, bounded numeric Attribute.data hashes, not a revision cache.

Only homogeneous numeric RNA layouts are eligible. Every exposed scalar/vector
field is included, even alternate color views. Strings, pointers and collections
retain the caller's exact recursive path. Tags bind RNA type, attribute name,
domain, data type, count, field names and shapes; no pointer is used as content.
"""

from array import array
import hashlib
import json
import math
import sys

from .program_jobs import ProgramError
from .scene_read_values import all_finite


def charge_packed(budget, size, message):
    """Charge bytes hashed from one C copy to the packed slot of a two-slot budget.

    A legacy one-slot budget keeps charging its single item count.
    """
    slot = 1 if len(budget) > 1 else 0
    if size > budget[slot]:
        raise ProgramError(message, code='LIMIT_EXCEEDED')
    budget[slot] -= size


def packed_bytes_value(value, *, budget):
    """Hash every fresh RNA byte, charged to the packed byte budget."""
    charge_packed(budget, len(value), 'Exact RNA byte preconditions exceed the bounded fingerprint budget')
    digest = hashlib.blake2b(digest_size=32)
    digest.update(json.dumps(['rna_bytes_v1', len(value)], separators=(',', ':')).encode())
    digest.update(value)
    return {'packed_rna_bytes_v1': {'length': len(value), 'digest': digest.hexdigest()}}


def _field_layout(prop):
    if prop.type not in {'BOOLEAN', 'INT', 'FLOAT'}:
        return None
    if prop.is_array:
        shape = [int(size) for size in prop.array_dimensions if size]
        width = math.prod(shape)
        if not shape or width != prop.array_length or not 1 <= width <= 64:
            return None
        # The generic walker counts each nested array and each numeric leaf.
        cost, partial = 1, 1
        for size in shape:
            partial *= size
            cost += partial
    else:
        shape, width, cost = [], 1, 1
    return {'name': prop.identifier, 'type': prop.type, 'shape': shape,
            'width': width, 'cost': cost, 'array_code': {'BOOLEAN': 'b', 'INT': 'i', 'FLOAT': 'f'}[prop.type]}


def packed_attribute_values(attribute, collection, *, budget, skip_names, child_depth=1):
    """Return complete packed metadata or None for the exact generic fallback.

    A two-slot budget is charged the bytes hashed, in its packed slot; a legacy
    one-slot budget keeps the recursive layout's work count. Charges happen
    before any array allocation. Unsupported bulk access falls back with
    no budget consumption; nonfinite values and exhausted budgets fail closed.
    All reads are fresh foreach_get calls on the original RNA data, not a graph.
    """
    count = len(collection)
    if count == 0:
        return None  # Parent metadata already binds the empty attribute's type/domain.
    first = collection[0]
    child_type = first.bl_rna.identifier
    fields = []
    for prop in first.bl_rna.properties:
        if prop.identifier in skip_names or (prop.is_readonly and prop.identifier.startswith('is_')):
            continue
        field = _field_layout(prop)
        if field is None:
            return None
        if child_depth + 1 + len(field['shape']) > 16:
            return None  # Retain the generic recursion's depth failure, not a bypass.
        fields.append(field)
    if not fields:
        return None
    work = count * (1 + sum(field['cost'] for field in fields))
    if len(budget) > 1:
        work = count * sum(field['width'] * {'b': 1, 'i': 4, 'f': 4}[field['array_code']] for field in fields)
    if work > budget[1 if len(budget) > 1 else 0]:
        raise ProgramError('Exact numeric attribute preconditions exceed the bounded fingerprint budget', code='LIMIT_EXCEEDED')
    attribute_tag = [child_type, attribute.name, attribute.data_type, attribute.domain, count]
    del first  # Bulk access may materialize storage; do not retain a value RNA pointer.
    fields_digest = []
    for field in fields:
        # All products/counts were checked against the remaining logical work
        # budget first. Keep one field buffer live at a time, never every field.
        packed = array(field['array_code'], [0]) * (count * field['width'])
        try:
            collection.foreach_get(field['name'], packed)
        except (TypeError, RuntimeError):
            return None  # An uncommon RNA storage/accessor retains exact recursion.
        if field['type'] == 'FLOAT' and not all_finite(packed):
            raise ProgramError('Non-finite source attribute values cannot be staged', code='VALIDATION')
        if packed.itemsize != {'b': 1, 'i': 4, 'f': 4}[field['array_code']]:
            return None  # Unexpected platform ABI: use the portable recursive path.
        if sys.byteorder != 'little':
            packed.byteswap()
        tag = ['numeric_attribute_field_v1', *attribute_tag, field['name'], field['type'], field['shape']]
        digest = hashlib.blake2b(digest_size=32)
        digest.update(json.dumps(tag, separators=(',', ':'), allow_nan=False).encode())
        digest.update(memoryview(packed).cast('B'))
        fields_digest.append({'name': field['name'], 'type': field['type'], 'shape': field['shape'],
                              'count': count, 'digest': digest.hexdigest()})
    budget[1 if len(budget) > 1 else 0] -= work
    return {'packed_numeric_attribute_v1': fields_digest}
