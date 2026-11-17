# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Deep read-only copies of JSON-shaped data: mappings become ``MappingProxyType`` over copies, lists tuples, sets
frozensets. The validators read the tools' schemas and their own tables this way, taken as they register, so a check
reads what registered, never a dict, list or set an agent script could change in place (the reviews of c76f2f1 and
a6edbf3: C and S1). A JSON schema a model is sent stays the registered plain data; only the validators' copies are
frozen (``seal`` rebinds a module's own names once its tools are built)."""

from collections.abc import Mapping
from types import MappingProxyType


def frozen(value, memo=None):
    """``value`` with nothing left to change in place: reads, iteration and lookups behave as on the original. With a
    shared ``memo``, one object frozen twice (a fragment several schemas embed) is the same frozen object each time, so
    an identity check (``schema is REFERENCE``) holds on the copies as on the originals."""
    memo = {} if memo is None else memo
    if id(value) in memo and memo[id(value)][0] is value:   # The original is kept, so its id is never reused.
        return memo[id(value)][1]
    if isinstance(value, Mapping):
        result = MappingProxyType({key: frozen(item, memo) for key, item in value.items()})
    elif isinstance(value, (list, tuple)):
        result = tuple(frozen(item, memo) for item in value)
    elif isinstance(value, (set, frozenset)):
        result = frozenset(frozen(item, memo) for item in value)
    else:
        return value
    if isinstance(value, (dict, list, set)):
        memo[id(value)] = (value, result)
    return result


def seal(namespace, *names, memo=None):
    """Rebind each of ``names`` in a module's ``namespace`` to its ``frozen`` copy, at the end of the module: the
    tool schemas built from them keep the plain originals a model is sent; the module's validators read the copies."""
    for name in names:
        namespace[name] = frozen(namespace[name], memo)


def thawed(value):
    """A fresh plain copy of a ``frozen`` value (dicts and lists, as registered): for a validator that builds its
    result from the schema (defaults copied in, values compared with ``==``), each call its own copy."""
    if isinstance(value, Mapping):
        return {key: thawed(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thawed(item) for item in value]
    return value
