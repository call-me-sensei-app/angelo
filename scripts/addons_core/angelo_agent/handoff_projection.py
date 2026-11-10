# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete primitive handoff copies with the identifier owner's ordering.

Refusal never returns a partial value. Identifier numbering may already have
advanced, as it does for entries the eager handoff eventually leaves out.
This is presentation only; it neither certifies results nor binds a request.
"""

from dataclasses import dataclass
import math

from .handoff_json import CYCLE, INVALID, UNSUPPORTED, WORK_LIMIT
from .identifiers import FULL_REVISION, LONG_HANDLE, IdentifierTable, _HANDLE_KEYS, _REVISION_KEYS


@dataclass(frozen=True)
class Projection:
    value: object = None
    reason: str | None = None
    shape: object = None


class _Refused(Exception):
    pass


def compact(value, *, budget, identifiers=None, observed_numbers=False, measure_shape=False, compact_json=False):
    """Iterative counterpart of ``IdentifierTable.compact`` on exact JSON types.

    Character credit is checked before regexes or hashing strings. A container
    iterator advances only after positive node credit; no unvisited tail is
    copied. The caller owns a stable source snapshot and the actual table.
    """
    if (type(observed_numbers) is not bool or type(measure_shape) is not bool or type(compact_json) is not bool
            or identifiers is not None and type(identifiers) is not IdentifierTable
            or measure_shape and (identifiers is not None or observed_numbers)):
        return Projection(reason=UNSUPPORTED)
    from .handoff_bound import ShapeBuilder
    shape = ShapeBuilder(compact_json) if measure_shape else None
    root, active = [], set()

    def text(value):
        if value and not budget.scan(len(value)):
            raise _Refused(WORK_LIMIT)
        return value

    def handle(value):
        text(value)
        return identifiers.short_handle(value) if identifiers is not None and LONG_HANDLE.fullmatch(value) else value

    stack = [("value", value, root, None)]
    try:
        while stack:
            action, source, destination, key = stack.pop()
            if action == "object":
                original, iterator, length = source
                if not budget.visit():
                    raise _Refused(WORK_LIMIT)
                try:
                    child_key, child = next(iterator)
                except StopIteration:
                    if len(original) != length:
                        raise _Refused(UNSUPPORTED)
                    if identifiers is not None:
                        target = None
                        for field in _HANDLE_KEYS:
                            if not budget.visit():
                                raise _Refused(WORK_LIMIT)
                            candidate = original.get(field)
                            if type(candidate) is str and LONG_HANDLE.fullmatch(text(candidate)):
                                target = candidate
                                break
                        if target is not None:
                            for field in _REVISION_KEYS:
                                if not budget.visit():
                                    raise _Refused(WORK_LIMIT)
                                revision = original.get(field)
                                if type(revision) is str and FULL_REVISION.fullmatch(text(revision)):
                                    destination[field] = identifiers.short_revision(target, revision)
                    active.remove(id(original))
                    continue
                if type(child_key) is not str:
                    raise _Refused(UNSUPPORTED)
                converted = handle(child_key)
                if shape is not None:
                    shape.key(child_key)
                stack.extend((("object", source, destination, None), ("value", child, destination, converted)))
                continue
            if action == "array":
                original, index, length = source
                if index == length:
                    if len(original) != length:
                        raise _Refused(UNSUPPORTED)
                    active.remove(id(original))
                    continue
                if budget.exhausted:
                    raise _Refused(WORK_LIMIT)
                stack.extend((("array", (original, index + 1, length), destination, None),
                              ("value", original[index], destination, None)))
                continue
            if not budget.visit():
                raise _Refused(WORK_LIMIT)
            kind = type(source)
            if kind is dict or kind is list:
                if id(source) in active:
                    raise _Refused(CYCLE)
                active.add(id(source))
                output = {} if kind is dict else []
                next_action = (("object", (source, iter(source.items()), len(source)), output, None) if kind is dict
                               else ("array", (source, 0, len(source)), output, None))
                stack.append(next_action)
            elif kind is str:
                output = handle(source)
            elif kind is float and not math.isfinite(source):
                raise _Refused(INVALID)
            elif kind is float and observed_numbers:
                from .program_model_result import OBSERVED_DIGITS
                encoded = format(source, ".%dg" % OBSERVED_DIGITS)
                if not budget.scan(len(encoded)):
                    raise _Refused(WORK_LIMIT)
                output = float(encoded)
            elif source is None or kind is bool or kind is int or kind is float:
                output = source
            else:
                raise _Refused(UNSUPPORTED)
            if shape is not None and (reason := shape.value(source, budget)) is not None:
                raise _Refused(reason)
            if type(destination) is list:
                destination.append(output)
            else:
                destination[key] = output
    except _Refused as error:
        return Projection(reason=error.args[0])
    except (RuntimeError, IndexError):
        return Projection(reason=UNSUPPORTED)
    return Projection(value=root[0], shape=shape.finish() if shape is not None else None)
