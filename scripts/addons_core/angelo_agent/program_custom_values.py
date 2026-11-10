# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact custom-property values of a program input's datablock, for its bind and commit fingerprint
(program_dependencies._rna_values)."""

import math

from .program_jobs import ProgramError


def _uid(value):
    return int(value.session_uid) if hasattr(value, "session_uid") else int(value.as_pointer())


def _max_items():
    from .program_dependencies import MAX_FINGERPRINT_ITEMS
    return MAX_FINGERPRINT_ITEMS


def _custom_properties(block, *, budget=None, depth=0):
    """Exact custom values, bounded before ID-property group/array expansion."""
    budget = [_max_items()] if budget is None else budget

    def charge(amount, level):
        if level > 16 or amount > budget[0]:
            raise ProgramError("Exact custom-property preconditions exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
        budget[0] -= amount

    def clean(value, level):
        charge(1, level)
        if isinstance(value, dict) or hasattr(value, "to_dict"):
            # IDPropertyGroup supports bounded mapping access. Do not call
            # to_dict(), which materializes all descendants before admission.
            count = len(value)
            if count > budget[0]:
                raise ProgramError("Exact custom-property group exceeds the bounded fingerprint budget", code='LIMIT_EXCEEDED')
            result = {}
            for key in value.keys():
                charge(1, level + 1)
                result[str(key)] = clean(value[key], level + 1)
            return result
        if isinstance(value, (list, tuple)) or hasattr(value, "to_list"):
            count = len(value)
            if count > budget[0]:
                raise ProgramError("Exact custom-property array exceeds the bounded fingerprint budget", code='LIMIT_EXCEEDED')
            return [clean(item, level + 1) for item in value]
        if isinstance(value, str):
            charge(len(value), level)
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ProgramError("Non-finite custom-property source values cannot be staged", code='VALIDATION')
            return value
        if isinstance(value, (bool, int)) or value is None:
            return value
        if hasattr(value, "as_pointer"):
            return [value.bl_rna.identifier, _uid(value)]
        raise ProgramError("Unsupported custom property in an exact program input", code='UNSUPPORTED')

    keys = block.keys()
    if len(keys) > budget[0]:
        raise ProgramError("Exact custom-property keys exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
    result = {}
    for key in keys:
        charge(1, depth)
        result[str(key)] = clean(block[key], depth)
    return result
