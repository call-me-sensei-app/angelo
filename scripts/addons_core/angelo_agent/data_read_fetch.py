# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Copying one member's value for ``data_read_access.Access``: fetched alone, or taken from
a value page (``data_read_batches``), and shaped as Python shows it."""

from . import data_read_values as values
from .data_read_records import (
    ReadUnavailable, Collection, _exact, _record, _copy, _annotations, _raw, _node, _address,
    _CODES, UNAVAILABLE_MESSAGES, UNAVAILABLE_MESSAGE,
)

_SCALAR_TAGS = {"BOOLEAN": bool, "INT": int, "FLOAT": float, "STRING": str, "ENUM": str}
_TRUSTED_SHAPES = frozenset({"ARRAY", "COLLECTION", "ENUM_FLAGS"})
_MATRIX_SIZES = {9: 3, 16: 4}


def _shape(items, sizes):
    """``items`` as nested lists of ``sizes`` (outermost first)."""
    if len(sizes) < 2:
        return list(items)
    stride = 1
    for size in sizes[1:]:
        stride *= size
    return [_shape(items[index:index + stride], sizes[1:]) for index in range(0, len(items), stride)]


class FetchAccess:
    """``value`` and its shapes; mixed into ``data_read_access.Access``, whose state it uses."""

    def value(self, owner, prop, *, derived=True):
        key = (owner.address, prop.identifier)
        entry = self._prefetched.pop(key, None) if derived else None
        if entry is not None:
            node = self._prefetched_nodes.pop(key, None)
            if node is not None:
                return node
            if entry[0] == "OK" and entry[3] is None and entry[4] is None and type(entry[1]) is str:
                kind = _SCALAR_TAGS.get(entry[1])
                if kind is not None:  # The common case: an exact scalar, nothing to note.
                    if type(entry[2]) is not kind:
                        raise ValueError("Invalid detached read record")
                    if self._notes:
                        self._notes.pop(key, None)
                    return entry[2]
            if not self._trusted:
                entry = _copy(entry)
            # Copied without admission (``prefetch_groups``); admitted now that it is used.
            address = self.admit_edge(owner.address, prop, derived=True)
            if entry[0] == "UNAVAILABLE":
                code = _exact(entry[1], str)
                if code not in _CODES:
                    raise ValueError("Unknown native read refusal code")
                raise ReadUnavailable(code, UNAVAILABLE_MESSAGES.get(code, UNAVAILABLE_MESSAGE), address=address)
            tag, value, source, getter = entry[1], entry[2], entry[3], entry[4]
            if source is None and getter is None:
                notes = None
            elif self._trusted:
                notes = {"value_source": source, "computed_getter": getter}
            else:
                notes = _annotations({"value_source": source, "computed_getter": getter})
        else:
            address = self.admit_edge(owner.address, prop, derived=derived)
            result = self._call("fetch", address, prop.transport())
            if result["kind"] != "VALUE":
                raise ValueError("Invalid native value kind")
            if self._trusted:
                d = result["payload"]
                notes = d["annotations"]
            else:
                d = _record(result["payload"], "tag value annotations")
                notes = _annotations(d["annotations"])
            tag, value = d["tag"], d["value"]
        kept = tuple((name, item) for name, item in notes.items() if item is not None) if notes else ()
        if kept:
            self._notes[key] = kept
        elif self._notes:
            self._notes.pop(key, None)
        kind = _SCALAR_TAGS.get(tag)
        if kind is not None:
            if type(value) is not kind:
                raise ValueError("Invalid detached read record")
            return value
        if tag == "STRUCT":
            return self._parse_node(value)
        if self._trusted and tag in _TRUSTED_SHAPES:
            # The native module's records have these shapes by construction.
            if tag == "ARRAY":
                self._array_lengths[(owner.address, prop.identifier)] = value["actual_length"]
                return self.array_shape(value["flat"], value["dimensions"], prop)
            if tag == "COLLECTION":
                return Collection(value["address"], prop, value["length"])
            return frozenset(value)
        if tag == "COLLECTION":
            c = _record(value, "address length")
            length = _exact(c["length"], int)
            if length < 0:
                raise ValueError("Invalid collection length")
            return Collection(_address(c["address"]), prop, length)
        if tag == "ENUM_FLAGS":
            return frozenset(_exact(item, str) for item in _exact(value, tuple))
        if tag == "ARRAY":
            a = _record(value, "flat declared_length actual_length dimensions")
            _exact(a["flat"], tuple)
            if len(a["flat"]) > values.MAX_ELEMENTS or any(type(item) not in (bool, int, float) for item in a["flat"]):
                raise ValueError("Invalid native array prefix")
            for key in ("declared_length", "actual_length"):
                if _exact(a[key], int) < 0:
                    raise ValueError("Invalid array length")
            _exact(a["dimensions"], tuple)
            if len(a["dimensions"]) != 3 or any(type(item) is not int or item < 0 for item in a["dimensions"]):
                raise ValueError("Invalid native array shape")
            self._array_lengths[(owner.address, prop.identifier)] = a["actual_length"]
            return self.array_shape(a["flat"], a["dimensions"], prop)
        if tag == "RAW":
            return self._stored_value(owner, prop, value, address)
        if tag == "NULL" and value is None:
            return None
        raise ValueError("Invalid native value tag")

    def _stored_value(self, owner, prop, record, address):
        d = _raw(record)
        state, kind = d["state"], prop.type
        if state == "INVALID":
            raise ReadUnavailable("INVALID_STORAGE", "The stored property has an incompatible type; it was not repaired.")
        value = d["value"]
        if kind in {"POINTER", "COLLECTION"}:
            if kind == "POINTER" and "ID" in values.struct_names(prop.fixed_type):
                if state in {"ABSENT", "GROUP"} or value is None:
                    return None
                if state == "VALUE" and d["type"] == "ID":
                    node = _node(value)
                    if prop.fixed_type.identifier in values.struct_names(node.type):
                        return node
            elif kind == "POINTER" and state in {"ABSENT", "GROUP"} and value is not None:
                return _node(value)
            elif kind == "COLLECTION" and state in {"ABSENT", "COLLECTION"}:
                return Collection(address, prop, d["length"] or 0)
            raise ReadUnavailable("INVALID_STORAGE", "The stored property has an incompatible type; it was not repaired.")
        if state == "ABSENT":
            if values.is_array(prop):
                return self.array_shape(d["default_array"], prop.array_dimensions, prop)
            value = d["enum_default"] if kind == "ENUM" else d["default"]
        elif state != "VALUE":
            raise ReadUnavailable("INVALID_STORAGE", "The stored property has an incompatible type; it was not repaired.")
        if kind == "ENUM":
            _exact(value, int)
            choices = d["enum_items_static"] or ()
            if prop.is_enum_flag:
                selected = frozenset(identifier for identifier, _label, number in choices if number and value & number == number)
                mask = 0
                for _identifier, _label, number in choices:
                    mask |= number
                if not value & ~mask:
                    return selected
            else:
                for identifier, _label, number in choices:
                    if number == value:
                        return identifier
            raise ReadUnavailable("COMPUTED_VALUE", "The stored enum needs computed items; use an approved script.")
        compatible = {"BOOLEAN": {"BOOLEAN", "INT"}, "INT": {"INT"}, "FLOAT": {"FLOAT", "DOUBLE"}, "STRING": {"STRING"}}.get(kind, set())
        if state == "VALUE":
            if values.is_array(prop):
                if d["type"] != "ARRAY" or d["subtype"] not in compatible or d["length"] != prop.array_length:
                    raise ReadUnavailable("INVALID_STORAGE", "The stored array has an incompatible type or length; it was not repaired.")
                return self.array_shape(value, prop.array_dimensions, prop)
            if d["type"] not in compatible:
                raise ReadUnavailable("INVALID_STORAGE", "The stored property has an incompatible type; it was not repaired.")
        return bool(value) if kind == "BOOLEAN" else value

    def array_length(self, owner, prop):
        return self._array_lengths.get((owner.address, prop.identifier))

    @staticmethod
    def array_shape(flat, dimensions, prop=None):
        """Nested lists as Python shows the property: a 3x3 or 4x4 float matrix as mathutils rows."""
        size = _MATRIX_SIZES.get(len(flat))
        if (size and prop is not None and prop.type == "FLOAT" and prop.subtype == "MATRIX"
                and not prop.has_dynamic_length and prop.array_length == len(flat)):
            # RNA stores matrices column by column; mathutils indexes rows.
            return [[flat[column * size + row] for column in range(size)] for row in range(size)]
        sizes = [size for size in dimensions if size]
        return list(flat) if len(sizes) < 2 else _shape(flat, sizes)
