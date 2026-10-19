# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Detached native read records, exact transport validation and copied-value policy.

These declarations own only native primitives. They never retain RNA, open a
native context or fetch a value. Access owns all request lifecycle operations.
"""
from dataclasses import dataclass, field
import re

from . import data_read_values as values

NATIVE_REQUIRED = "NATIVE_READER_REQUIRED"
NATIVE_MESSAGE = "This read needs the native read helper in a newer Angelo build; no value was fetched."


class ReadUnavailable(Exception):
    def __init__(self, code, message, *, address=None, edge=None):
        super().__init__(message)
        self.code, self.message, self.address, self.edge = code, message, address, edge


class Missing(ReadUnavailable):
    def __init__(self, record, address):
        super().__init__(record["kind"], "The current path no longer resolves.", address=address, edge=record["edge"])
        self.length, self.member = record["length"], record["member"]


def _exact(value, kind):
    if type(value) is not kind:
        raise ValueError("Invalid detached read record")
    return value


_FIELDS = {}


def _record(value, keys):
    fields = _FIELDS.get(keys)
    if fields is None:
        fields = _FIELDS[keys] = frozenset(keys.split())
    if type(value) is not dict or value.keys() != fields:
        raise ValueError("Invalid detached read record fields")
    return value


_SCALARS = frozenset({type(None), bool, int, float, str})


def _copy(value):
    """Validate exact native primitives before any conversion or user operation."""
    kind = type(value)
    if kind in _SCALARS:
        return value
    if kind is tuple:
        return tuple([_copy(item) for item in value])
    if kind is dict:
        copied = {}
        for key, item in value.items():
            if type(key) is not str:
                break
            copied[key] = _copy(item)
        else:
            return copied
    raise ValueError("Native read returned a live or unsupported object")


def _annotations(value):
    d = _record(value, "value_source computed_getter")
    if d["value_source"] is not None and _exact(d["value_source"], str) not in {"stored", "default"}:
        raise ValueError("Invalid read value source")
    if d["computed_getter"] is not None and _exact(d["computed_getter"], str) != "not_run":
        raise ValueError("Invalid read getter annotation")
    return d


def _choice_items(value, limit=None):
    items = _exact(value, tuple)
    if limit is not None and len(items) > limit:
        raise ValueError("Invalid native choice page size")
    for item in items:
        _exact(item, tuple)
        if len(item) != 3:
            raise ValueError("Invalid native choice record")
        _exact(item[0], str)
        _exact(item[1], str)
        _exact(item[2], int)
    return items


def _page(value, offset):
    d = _record(value, "items length offset more")
    length = _exact(d["length"], int)
    current = _exact(d["offset"], int)
    _exact(d["more"], bool)
    if length < 0 or current < 0 or current != offset:
        raise ValueError("Invalid native page position")
    _exact(d["items"], tuple)
    return d


def _raw(value):
    d = _record(value, "state type subtype length value default default_array enum_default enum_items_static")
    if _exact(d["state"], str) not in {"ABSENT", "INVALID", "VALUE", "GROUP", "COLLECTION"}:
        raise ValueError("Invalid raw storage state")
    _exact(d["type"], str)
    if d["subtype"] is not None:
        _exact(d["subtype"], str)
    if d["length"] is not None and _exact(d["length"], int) < 0:
        raise ValueError("Invalid raw storage length")
    item = d["value"]
    if is_node(item):
        _node(item)
    elif type(item) is tuple:
        if any(type(number) not in (bool, int, float) for number in item) or len(item) > values.MAX_ELEMENTS:
            raise ValueError("Invalid raw numeric prefix")
    elif item is not None and type(item) not in (bool, int, float, str):
        raise ValueError("Invalid raw scalar")
    if d["default"] is not None and type(d["default"]) not in (bool, int, float, str):
        raise ValueError("Invalid raw default")
    if d["default_array"] is not None:
        array = _exact(d["default_array"], tuple)
        if len(array) > values.MAX_ELEMENTS or any(type(number) not in (bool, int, float) for number in array):
            raise ValueError("Invalid raw default array")
    if d["enum_default"] is not None:
        _exact(d["enum_default"], int)
    if d["enum_items_static"] is not None:
        _choice_items(d["enum_items_static"])
    return d


def _freeze(mapping):
    return tuple((key, mapping[key]) for key in sorted(mapping))


_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PUBLIC_IDENTIFIERS = set()  # Strings that passed the public identifier check (str equals only str).


_ADDRESSES = {}  # Addresses that passed validation; a hit is rechecked for exact index types.


def _address(value):
    if type(value) is tuple:
        try:
            known = _ADDRESSES.get(value)
        except TypeError:  # An unhashable part: validation below refuses it.
            known = None
        # Equal tuples may still differ in kind (1 == True == 1.0): selectors must be exact.
        if known is not None and all(type(step[1]) in (int, str) for steps in value[1:] for step in steps):
            return value
    checked = _validate_address(value)
    if len(_ADDRESSES) >= 16384:
        _ADDRESSES.clear()
    _ADDRESSES[checked] = True
    return checked


def _validate_address(value):
    _exact(value, tuple)
    if len(value) != 3 or value[0] not in ("scene", "data") or type(value[0]) is not str:
        raise ValueError("Invalid native read address")
    for steps, bound, derived in ((value[1], 16, False), (value[2], 2, True)):
        _exact(steps, tuple)
        if len(steps) > bound:
            raise ValueError("Invalid native read address length")
        for step in steps:
            _exact(step, tuple)
            if len(step) != 2 or type(step[0]) is not str:
                raise ValueError("Invalid native read selector")
            kind, item = step
            if kind == "index":
                _exact(item, int)
                if not (-9999999 <= item <= 9999999 if not derived else 0 <= item <= 10000063):
                    raise ValueError("Invalid native read index")
            elif kind in (("attribute",) if derived else ("attribute", "key")):
                _exact(item, str)
                if not item:
                    raise ValueError("Invalid native read identifier")
                if not derived and kind == "attribute" and item not in _PUBLIC_IDENTIFIERS:
                    if len(item) > 64 or _IDENTIFIER.fullmatch(item) is None:
                        raise ValueError("Invalid public property identifier")
                    if len(_PUBLIC_IDENTIFIERS) < 8192:
                        _PUBLIC_IDENTIFIERS.add(item)
                if kind == "key" and (len(item) > 255 or not all(character.isprintable() for character in item)):
                    raise ValueError("Invalid public name selector")
            else:
                raise ValueError("Invalid native read selector")
    return value


@dataclass(frozen=True, slots=True)
class TypeDecl:
    identifier: str
    bases: tuple
    name_property: str | None
    # ``(identifier, *bases)``, derived once (data_read_values.struct_names); not compared.
    names: tuple = field(default=(), compare=False, repr=False)


_TYPES = {}


def _type(value):
    if value is None:
        return None
    d = _record(value, "identifier bases name_property")
    key = (d["identifier"], d["bases"], d["name_property"])
    if type(key[0]) is str and type(key[1]) is tuple:
        known = _TYPES.get(key)  # Strings equal only strings: a hit was validated with these exact types.
        if known is not None:
            return known
    _exact(d["identifier"], str)
    _exact(d["bases"], tuple)
    if len(d["bases"]) > 15 or any(type(item) is not str for item in d["bases"]):
        raise ValueError("Invalid native type bases")
    if d["name_property"] is not None:
        _exact(d["name_property"], str)
    declared = TypeDecl(d["identifier"], d["bases"], d["name_property"], (d["identifier"], *d["bases"]))
    if len(_TYPES) < 4096:
        _TYPES[key] = declared
    return declared


@dataclass(frozen=True, slots=True)
class OwnerFacts:
    exists: bool
    id_type: str | None
    is_original: bool
    is_bound_scene: bool
    is_self: bool


@dataclass(frozen=True, slots=True)
class Node:
    address: tuple
    type: TypeDecl
    owner: OwnerFacts
    is_data_root: bool
    is_id: bool
    virtual_kind: str
    # Native key of this node's property descriptions (data_read_access caches them by it);
    # None on a reference's node, which is labelled and never listed.
    declarations: int | None


def _fingerprint(value):
    if type(value) is not int or not 0 <= value < 1 << 62:
        raise ValueError("Invalid native declaration fingerprint")
    return value


_OWNERS = {}  # Validated owner facts by their exact values: a handful of combinations.
_VIRTUAL_KINDS = frozenset({"NONE", "STORED_GROUP", "STORED_COLLECTION_ITEM", "WEIGHT"})


def _owner(value):
    """``(exists, id_type, is_original, is_bound_scene, is_self)`` as OwnerFacts."""
    if type(value) is not tuple or len(value) != 5:
        raise ValueError("Invalid detached read record")
    exists, id_type, original, bound, own = value
    if type(exists) is not bool or type(original) is not bool or type(bound) is not bool \
            or type(own) is not bool or (id_type is not None and type(id_type) is not str):
        raise ValueError("Invalid detached read record")
    facts = _OWNERS.get(value)
    if facts is None:
        facts = OwnerFacts(*value)
        if len(_OWNERS) < 1024:
            _OWNERS[value] = facts
    return facts


def _node_type(value):
    """``(identifier, bases, name_property)`` as TypeDecl."""
    if type(value) is not tuple or len(value) != 3:
        raise ValueError("A node needs a detached type declaration")
    if type(value[0]) is str and type(value[1]) is tuple:
        known = _TYPES.get(value)  # Same key as _type: a hit was validated with these exact types.
        if known is not None:
            return known
    return _type(dict(identifier=value[0], bases=value[1], name_property=value[2]))


def is_node(value):
    """Whether a copied value is a node record (``("NODE", ...)``)."""
    return type(value) is tuple and len(value) == 8 and value[0] == "NODE"


def _node(value):
    """A node record: ("NODE", address, type, owner, is_data_root, is_id, virtual_kind, declarations)."""
    if not is_node(value):
        raise ValueError("Invalid detached read node")
    _tag, address, kind, owner, is_data_root, is_id, virtual_kind, declarations = value
    if type(is_data_root) is not bool or type(is_id) is not bool:
        raise ValueError("Invalid detached read record")
    if type(virtual_kind) is not str or virtual_kind not in _VIRTUAL_KINDS:
        raise ValueError("Invalid virtual read node")
    if kind is None:
        raise ValueError("A node needs a detached type declaration")
    declarations = None if declarations is None else _fingerprint(declarations)
    return Node(_address(address), _node_type(kind), _owner(owner), is_data_root, is_id, virtual_kind, declarations)


def _native_node(value):
    """``_node`` for a record the native module built: its shape is closed by construction, so
    only the parts shared with other nodes (type, owner facts) are looked up, not checked again."""
    _tag, address, kind, owner, is_data_root, is_id, virtual_kind, declarations = value
    facts = _OWNERS.get(owner)
    return Node(address, _node_type(kind), facts if facts is not None else _owner(owner),
                is_data_root, is_id, virtual_kind, declarations)


_OWNER = "type is_data_root is_id owner_exists owner_id_type owner_is_original owner_is_bound_scene virtual_kind"
_DECL = "identifier type subtype name is_runtime is_deprecated is_readonly is_enum_flag array_length array_dimensions has_dynamic_length fixed_type collection_type uses_idprop_storage has_python_getter has_python_enum_items"
_ROUTE = "value length refine iterate lookup_index lookup_name enum_value_items enum_decl_items storage_locator"
# Native source-bound family vocabulary. These labels are compared by native;
# neither a copied label nor the fake backend grants callback authority.
_ROUTES = frozenset({"NONE", "UNSUPPORTED", "ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "WEIGHTS",
                     "IMAGE_CACHE", "IMAGE_TILE_CACHE", "SOURCE_SCALAR", "SOURCE_ARRAY", "SOURCE_POINTER",
                     "SOURCE_COLLECTION", "SOURCE_LENGTH", "SOURCE_REFINE", "SOURCE_ENUM_VALUE",
                     "SOURCE_ENUM_DECL", "SOURCE_STORAGE_LOCATOR"})
_CODES = frozenset({NATIVE_REQUIRED, "INVALID_STORAGE", "COMPUTED_VALUE", "UNSUPPORTED_VALUE", "DENIED",
                    "OUT_OF_DOCUMENT", "OTHER_SCENE", "NOT_DATA", "STALE_ADMISSION", "BUDGET",
                    values.PATH, values.SECRET, values.SCRIPT, values.SOCKET_TEXT, values.BYTES, values.LOADS_FILE,
                    values.NOT_EVALUATED, values.VIEW_LAYER_OUT_OF_SYNC, values.EXTENSION_CODE,
                    values.RESOURCE_LIMIT})
# What the model reads when a single requested value is refused (rows carry only the code).
# Model-visible text: never translated.
UNAVAILABLE_MESSAGES = {
    values.NOT_EVALUATED: ("This value comes from an evaluation Blender has not made yet, and a read never "
                           "evaluates. Read again after the scene updates, or use an approved script."),
    values.VIEW_LAYER_OUT_OF_SYNC: ("The view layer is out of date and reading it would update it. Read again "
                                    "after Blender refreshes the scene, or use an approved script."),
    values.EXTENSION_CODE: "Reading this would run add-on code; use an approved script.",
    values.LOADS_FILE: "Reading this would load a file, so nothing was loaded; use an approved script.",
    values.RESOURCE_LIMIT: "This value is larger than a read may copy; use an approved script.",
}
UNAVAILABLE_MESSAGE = "This value cannot be read safely; use an approved script."


@dataclass(frozen=True, slots=True)
class PropertyDecl:
    projection: tuple
    identifier: str
    type: str
    subtype: str
    name: str
    is_runtime: bool
    is_deprecated: bool
    is_readonly: bool
    is_enum_flag: bool
    array_length: int
    array_dimensions: tuple
    has_dynamic_length: bool
    uses_idprop_storage: bool
    has_python_getter: bool
    has_python_enum_items: bool
    fixed_type: TypeDecl | None
    collection_type: TypeDecl | None
    fixed_name: tuple | None
    # Native fingerprint of this whole description; handed back for admission.
    fingerprint: int

    def transport(self):
        return self.fingerprint


_PARSED = {}


def _property(value):
    # A fingerprint stands for the whole description (data_read_access): one parse per fingerprint.
    if type(value) is dict and type(value.get("fingerprint")) is int:
        known = _PARSED.get(value["fingerprint"])
        if known is not None:
            _record(value, "projection fixed_type collection_type fixed_name fingerprint")
            return known
    parsed = _parse_property(value)
    if len(_PARSED) >= 16384:
        _PARSED.clear()
    _PARSED[parsed.fingerprint] = parsed
    return parsed


def _native_property(value):
    """``_property`` for a description the native module built (closed shape by construction):
    the same declaration, without checking each field again."""
    fingerprint = value["fingerprint"]
    known = _PARSED.get(fingerprint)
    if known is not None:
        return known
    owner, decl, route = value["projection"]
    fixed_name = value["fixed_name"]
    parsed = PropertyDecl(
        (_freeze(owner), _freeze(decl), _freeze(route)), decl["identifier"], decl["type"], decl["subtype"],
        decl["name"], decl["is_runtime"], decl["is_deprecated"], decl["is_readonly"], decl["is_enum_flag"],
        decl["array_length"], decl["array_dimensions"], decl["has_dynamic_length"], decl["uses_idprop_storage"],
        decl["has_python_getter"], decl["has_python_enum_items"], _type(value["fixed_type"]),
        _type(value["collection_type"]), _freeze(fixed_name) if fixed_name is not None else None, fingerprint)
    if len(_PARSED) >= 16384:
        _PARSED.clear()
    _PARSED[fingerprint] = parsed
    return parsed


def _parse_property(value):
    d = _record(value, "projection fixed_type collection_type fixed_name fingerprint")
    projection = _exact(d["projection"], tuple)
    if len(projection) != 3:
        raise ValueError("Invalid native read projection")
    owner, decl, route = (_record(item, fields) for item, fields in zip(projection, (_OWNER, _DECL, _ROUTE)))
    for part in (owner, decl):
        for key, item in part.items():
            if key.startswith("is_") or key.startswith("has_") or key.startswith("owner_is_") or key in {"owner_exists", "uses_idprop_storage"}:
                _exact(item, bool)
    for key in ("identifier", "type", "subtype", "name"):
        _exact(decl[key], str)
    _exact(decl["array_length"], int)
    _exact(decl["array_dimensions"], tuple)
    if len(decl["array_dimensions"]) != 3 or any(type(item) is not int or item < 0 for item in decl["array_dimensions"]):
        raise ValueError("Invalid array dimensions")
    for item in route.values():
        if _exact(item, str) not in _ROUTES:
            raise ValueError("Unknown native callback route family")
    if decl["type"] not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM", "POINTER", "COLLECTION"} or decl["array_length"] < 0:
        raise ValueError("Invalid native property kind or length")
    if owner["owner_id_type"] is not None:
        _exact(owner["owner_id_type"], str)
    if owner["virtual_kind"] not in {"NONE", "STORED_GROUP", "STORED_COLLECTION_ITEM", "WEIGHT"} or owner["type"] is None:
        raise ValueError("Invalid projection owner")
    for key in (owner["type"], decl["fixed_type"], decl["collection_type"]):
        if key is not None:
            _exact(key, tuple)
            if len(key) != 2 or type(key[0]) is not str or type(key[1]) is not tuple or any(type(base) is not str for base in key[1]):
                raise ValueError("Invalid projection type key")
    fixed_name = d["fixed_name"]
    if fixed_name is not None:
        fixed_name = _freeze(_record(fixed_name, "identifier type subtype"))
        if any(type(item) is not str for _, item in fixed_name):
            raise ValueError("Invalid fixed name declaration")
    fields = {key: decl[key] for key in _DECL.split() if key not in {"fixed_type", "collection_type"}}
    fixed, collection = _type(d["fixed_type"]), _type(d["collection_type"])
    for key, info in (("fixed_type", fixed), ("collection_type", collection)):
        expected = (info.identifier, info.bases) if info is not None else None
        if expected != decl[key]:
            raise ValueError("Inconsistent native type declaration")
    return PropertyDecl(tuple(_freeze(part) for part in projection), **fields,
                        fixed_type=fixed, collection_type=collection, fixed_name=fixed_name,
                        fingerprint=_fingerprint(d["fingerprint"]))


@dataclass(frozen=True, slots=True)
class Collection:
    address: tuple
    property: PropertyDecl
    length: int | None  # None for a walked step whose length was not fetched (``collection_at``).

    @property
    def type(self):
        return self.property.collection_type

    @property
    def is_id(self):
        return False

    @property
    def is_data_root(self):
        return False


def _value_policy(prop):
    """A changed declaration must pass the existing policy before retry."""
    from . import data_read_policy as policy
    owner = dict(prop.projection[0])
    names = (owner["type"][0], *owner["type"][1])
    for name in names:
        if name in policy.DENIED_STRUCTS:
            raise ReadUnavailable("DENIED", policy.DENIED_STRUCTS[name])
    if not owner["is_data_root"]:
        if not owner["owner_exists"]:
            raise ReadUnavailable("OUT_OF_DOCUMENT", "This belongs to no datablock of the open document.")
        if owner["owner_id_type"] in policy.DENIED_ID_TYPES:
            raise ReadUnavailable("DENIED", policy.DENIED_ID_TYPES[owner["owner_id_type"]])
        if not owner["owner_is_original"]:
            raise ReadUnavailable("DENIED", "Evaluated copies are not read; read the original datablock.")
        if owner["owner_id_type"] == "SCENE" and not owner["owner_is_bound_scene"]:
            raise ReadUnavailable("OTHER_SCENE", "Other scenes are not read; the bound scene is the root scene.")
    reason = values.text_reason(names, prop) if prop.type == "STRING" else None
    if reason:
        raise ReadUnavailable(reason, "This text is withheld by the read policy.")
    if owner["is_data_root"] and prop.identifier in policy.DATA_DENIED:
        raise ReadUnavailable("DENIED", policy.DATA_DENIED[prop.identifier])
    if owner["owner_id_type"] in policy.FILE_BACKED and prop.identifier not in policy.IDENTITY_PROPERTIES:
        raise ReadUnavailable(values.LOADS_FILE, policy.FILE_BACKED_REASON)
    if prop.type == "POINTER":
        for name in values.struct_names(prop.fixed_type):
            if name in policy.DENIED_STRUCTS:
                raise ReadUnavailable("DENIED", policy.DENIED_STRUCTS[name])
    if "Image" in names and prop.identifier in values.IMAGE_NEVER:
        raise ReadUnavailable(values.LOADS_FILE, "Image pixels are not read.")
