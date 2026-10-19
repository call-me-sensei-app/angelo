# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""RNA stand-ins for the host tests of the read tools. Not a test module; no Blender.

They model only what ``data_read`` consults: a struct's ``bl_rna`` (identifier,
base, properties, functions, name property), ``id_data``, and collections that
take an index, a name or a slice. Reading a property is ``getattr``, and every
read is counted, so a test can prove a withheld value was never fetched.
"""

import hashlib
from types import SimpleNamespace


class Properties:
    """``bl_rna.properties``: iterates definitions in order and looks one up by identifier."""

    def __init__(self, items):
        self._items = {item.identifier: item for item in items}

    def __iter__(self):
        return iter(self._items.values())

    def get(self, name, default=None):
        return self._items.get(name, default)


def prop(identifier, kind="FLOAT", **fields):
    defaults = dict(identifier=identifier, type=kind, subtype="NONE", name=identifier.replace("_", " ").title(),
                    is_runtime=False, is_readonly=False, array_length=0, array_dimensions=(0, 0, 0), fixed_type=None,
                    enum_items=(), enum_items_static=(), hard_min=float("-inf"), hard_max=float("inf"))
    defaults.update(fields)
    defaults.setdefault("default", {"STRING": "", "BOOLEAN": False, "INT": 0, "FLOAT": 0.0, "ENUM": ""}.get(kind))
    defaults.setdefault("default_array", (0.0,) * defaults["array_length"])
    return SimpleNamespace(**defaults)


def rna(identifier, properties=(), *, base=None, functions=(), name_property="name"):
    inherited = list(base.properties) if base is not None else []
    definition = SimpleNamespace(identifier=identifier, base=base, functions=Properties(
        [SimpleNamespace(identifier=name) for name in functions]))
    definition.properties = Properties(inherited + list(properties))
    definition.name_property = definition.properties.get(name_property) if name_property else None
    return definition


class Struct:
    """An RNA struct value: attribute reads are counted in ``reads``."""

    reads = []

    def __init__(self, definition, owner=None, **values):
        object.__setattr__(self, "bl_rna", definition)
        object.__setattr__(self, "_values", dict(values))
        object.__setattr__(self, "id_data", self if owner is None else owner)

    def __getattr__(self, name):
        values = object.__getattribute__(self, "_values")
        if name not in values:
            raise AttributeError(name)
        Struct.reads.append((object.__getattribute__(self, "bl_rna").identifier, name))
        value = values[name]
        if isinstance(value, Exception):
            raise value
        return value

    def set(self, name, value):
        object.__getattribute__(self, "_values")[name] = value


class Collection:
    """A ``bpy_prop_collection``: index, name, slice, ``keys`` and ``len``; ``named`` False refuses names."""

    def __init__(self, items, *, named=True, definition=None):
        self.items, self.named = list(items), named
        if definition is not None:
            self.bl_rna = definition

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def keys(self):
        return [item.name for item in self.items] if self.named else []

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self.items[key]
        if not self.named:
            raise TypeError("bpy_prop_collection[key]: this collection does not support string lookups")
        for item in self.items:
            if item.name == key:
                return item
        raise KeyError('bpy_prop_collection[key]: key "%s" not found' % key)


class StorageCopies:
    """Native bounded-copy contract; access stand-in storage without calling its instance getters."""

    def __init__(self):
        self.calls = []

    def property_info(self, prop):
        return {"runtime": prop.is_runtime, "uses_idprop_storage": getattr(prop, "uses_idprop_storage", False),
                "has_python_getter": getattr(prop, "has_python_getter", False),
                "has_python_enum_items": getattr(prop, "has_python_enum_items", False),
                "enum_default": getattr(prop, "enum_default", 0)}

    def _stored_record(self, owner, path):
        """The fake address ABI's raw-storage record for ``_member`` (the native stored_member)."""
        self.calls.append(("stored", owner, path))
        value, definition = owner, None
        for part in path:
            if type(part) is int:
                if not isinstance(value, Collection) or not 0 <= part < len(value.items):
                    return {"state": "INVALID"}
                value = value.items[part]
                continue
            if not isinstance(value, Struct):
                return {"state": "INVALID"}
            definition = value.bl_rna.properties.get(part)
            stored = object.__getattribute__(value, "_values")
            if part not in stored:
                return {"state": "ABSENT", "type": "NONE"}
            value = stored[part]
        if isinstance(value, Struct):
            if value.id_data is value:
                return {"state": "VALUE", "type": "ID", "value": value}
            return {"state": "GROUP", "type": "GROUP"}
        if isinstance(value, Collection):
            return {"state": "COLLECTION", "type": "IDPARRAY", "length": len(value.items)}
        if isinstance(value, Exception):
            return {"state": "INVALID"}
        if definition is not None and definition.type == "ENUM" and isinstance(value, str):
            value = next((item.value for item in definition.enum_items_static if item.identifier == value), value)
        if isinstance(value, (list, tuple)):
            subtype = "DOUBLE" if any(isinstance(item, float) for item in value) else "INT"
            return {"state": "VALUE", "type": "ARRAY", "subtype": subtype,
                    "value": list(value)[:64], "length": len(value)}
        if value is None and definition is not None and definition.type == "POINTER":
            return {"state": "VALUE", "type": "ID", "value": None}
        kind = {str: "STRING", bool: "BOOLEAN", int: "INT", float: "DOUBLE"}.get(type(value))
        return {"state": "VALUE", "type": kind, "value": value} if kind else {"state": "INVALID"}

    def vertex_groups(self, mesh, vertex, offset, limit):
        self.calls.append(("weights", mesh, vertex, offset, limit))
        weights = object.__getattribute__(mesh, "_values").get("stored_weights", {}).get(vertex, [])
        return {"length": len(weights), "items": weights[offset:offset + limit]}

    def image_tile_metadata(self, image, tile, frame=0, view=0):
        self.calls.append(("tile", image, tile, frame, view))
        return object.__getattribute__(image, "_values").get("cached_tiles", {}).get((tile, frame, view))

    def image_metadata(self, image):
        self.calls.append(("image", image))
        return object.__getattribute__(image, "_values").get("cached_default")


class ReadAPI(StorageCopies):
    """Explicit host-only implementation of the detached address contract.

    Its mutable stand-in document is confined here. Production never selects
    this backend and never receives any of these stand-in values.
    """

    ADDRESS_READ_VERSION = 2

    def __init__(self, bpy=None):
        super().__init__()
        self.bpy = bpy
        self.contexts = []
        self.before_call = None

    def begin(self, window, seconds):
        context = SimpleNamespace(window=window, scene=window.scene, closed=False, seconds=seconds)
        self.contexts.append(context)
        return context

    def close(self, context):
        context.closed = True

    def check(self, context):
        if context.closed:
            return dict(status="EXPIRED", reason="CLOSED")
        if context.window.scene is not context.scene:
            return dict(status="EXPIRED", reason="SCENE")
        return dict(status="OK", budget_exhausted=False)

    @staticmethod
    def type_info(definition):
        if definition is None:
            return None
        bases, base = [], definition.base
        while base is not None and len(bases) < 15:
            bases.append(base.identifier)
            base = base.base
        return dict(identifier=definition.identifier, bases=tuple(bases),
                    name_property=definition.name_property.identifier if definition.name_property else None)

    @staticmethod
    def fingerprint(*parts):
        """Stand-in for native fingerprints: a stable hash of everything a description holds."""
        digest = hashlib.blake2b(repr(parts).encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big") & ((1 << 62) - 1)

    def facts(self, context, value, address, virtual="NONE"):
        owner = value.id_data if isinstance(value, Struct) else None
        stored = owner._values if owner is not None else {}
        return dict(address=address, type=self.type_info(value.bl_rna),
                    owner=dict(exists=owner is not None, id_type=stored.get("id_type"),
                               is_original=not stored.get("is_evaluated", False),
                               is_bound_scene=owner is context.scene, is_self=owner is value),
                    is_data_root=value is self.bpy.data, is_id=owner is value, virtual_kind=virtual)

    def declarations(self, context, value):
        return self.fingerprint(tuple(self.property(context, value, definition)["fingerprint"]
                                      for definition in value.bl_rna.properties), value is self.bpy.data)

    def node(self, context, value, address, virtual="NONE"):
        """The native node record: ("NODE", address, type, owner, is_data_root, is_id, virtual_kind, declarations)."""
        facts = self.facts(context, value, address, virtual)
        kind, owner = facts["type"], facts["owner"]
        return ("NODE", address,
                (kind["identifier"], kind["bases"], kind["name_property"]) if kind is not None else None,
                (owner["exists"], owner["id_type"], owner["is_original"], owner["is_bound_scene"], owner["is_self"]),
                facts["is_data_root"], facts["is_id"], virtual, self.declarations(context, value))

    def property(self, context, owner, definition):
        node = self.facts(context, owner, ("scene", (), ())) if isinstance(owner, Struct) else None
        type_info = self.type_info(owner.bl_rna)
        key = lambda info: (info["identifier"], info["bases"]) if info else None
        fixed = self.type_info(definition.fixed_type)
        collection = owner._values.get(definition.identifier) if isinstance(owner, Struct) else None
        collection_type = self.type_info(collection.bl_rna) if isinstance(collection, Collection) and hasattr(collection, "bl_rna") else self.type_info(getattr(definition, "collection_type", None))
        own = dict(type=key(type_info), is_data_root=owner is self.bpy.data,
                   is_id=node["is_id"] if node else False,
                   owner_exists=node["owner"]["exists"] if node else True,
                   owner_id_type=node["owner"]["id_type"] if node else None,
                   owner_is_original=node["owner"]["is_original"] if node else True,
                   owner_is_bound_scene=node["owner"]["is_bound_scene"] if node else False,
                   virtual_kind="NONE")
        decl = {name: getattr(definition, name, False) for name in
                ("is_runtime", "is_deprecated", "is_readonly", "is_enum_flag", "has_dynamic_length",
                 "uses_idprop_storage", "has_python_getter", "has_python_enum_items")}
        decl.update({name: getattr(definition, name) for name in
                     ("identifier", "type", "subtype", "name", "array_length", "array_dimensions")})
        decl.update(fixed_type=key(fixed), collection_type=key(collection_type))
        route = {name: "NONE" for name in ("value", "length", "refine", "iterate", "lookup_index", "lookup_name",
                                          "enum_value_items", "enum_decl_items", "storage_locator")}
        route["value"] = "ID_SYSTEM_STORAGE" if self.is_storage(definition) else "SOURCE_SCALAR"
        if definition.type == "COLLECTION":
            route.update(length="SOURCE_LENGTH", iterate="SOURCE_COLLECTION", lookup_index="SOURCE_COLLECTION", lookup_name="SOURCE_COLLECTION")
        name = definition.fixed_type.name_property if definition.fixed_type else None
        description = dict(projection=(own, decl, route), fixed_type=fixed, collection_type=collection_type,
                           fixed_name={field: getattr(name, field) for field in ("identifier", "type", "subtype")} if name else None)
        return dict(description, fingerprint=self.fingerprint(description))

    @staticmethod
    def is_storage(definition):
        return any(getattr(definition, key, False) for key in
                   ("uses_idprop_storage", "has_python_getter", "has_python_enum_items"))

    def _value(self, context, owner, definition):
        name = definition.identifier
        if isinstance(owner, Collection):
            return getattr(owner, name)
        if owner.bl_rna.identifier == "MeshVertex" and name == "groups":
            mesh = owner.id_data
            index = owner._values["index"]
            items = [Struct(definition.fixed_type, mesh, group=group, weight=weight)
                     for group, weight in self.vertex_groups(mesh, index, 0, 64)["items"]]
            return Collection(items, named=False)
        if self.is_storage(definition):
            stored = owner._values
            if name not in stored:
                if definition.type == "POINTER":
                    info = self.type_info(definition.fixed_type)
                    if info and "ID" in (info["identifier"], *info["bases"]):
                        return None
                    return Struct(definition.fixed_type, owner.id_data)
                if definition.type == "COLLECTION":
                    return Collection([])
                return definition.default
            value = stored[name]
            if definition.type == "POINTER" and value is not None and not isinstance(value, Struct):
                raise ValueError("INVALID_STORAGE")
            return value
        return getattr(owner, name)

    def _resolve(self, context, address, allowance):
        checked = self.check(context)
        if checked["status"] != "OK":
            return checked
        if self.before_call:
            self.before_call(self, address)
        value = context.scene if address[0] == "scene" else self.bpy.data
        edges, names = dict(allowance[0]), allowance[1]
        definition = None
        for edge, (kind, selector) in enumerate(address[1] + address[2]):
            if value is None:
                return self.missing("NULL", edge)
            if kind == "attribute":
                if not hasattr(value, "bl_rna"):
                    return self.missing("NOT_A_STRUCT", edge)
                definition = value.bl_rna.properties.get(selector)
                if definition is None:
                    return self.missing("NOT_A_STRUCT", edge, member=selector)
                actual = self.property(context, value, definition)["fingerprint"]
                if edges.get(edge + 1) != actual:
                    return dict(status="STALE_ADMISSION", address=address, edge=edge)
                try:
                    value = self._value(context, value, definition)
                except ValueError as error:
                    return dict(status="UNAVAILABLE", code=str(error))
            else:
                if not isinstance(value, Collection):
                    return self.missing("NOT_A_COLLECTION", edge)
                if kind == "index":
                    if not -len(value.items) <= selector < len(value.items):
                        return self.missing("INDEX_RANGE", edge, length=len(value.items))
                    value = value.items[selector]
                else:
                    if not value.named:
                        return self.missing("NO_NAMES", edge, length=len(value.items))
                    found = None
                    for item in value.items:
                        if item is None:
                            continue
                        name_prop = item.bl_rna.name_property
                        if name_prop is None:
                            return self.missing("NO_NAMES", edge, length=len(value.items))
                        description = self.property(context, item, name_prop)
                        if name_prop.subtype in {"FILE_PATH", "DIR_PATH", "FILE_NAME"}:
                            if item._values.get(name_prop.identifier) == selector:
                                found = item
                                break
                            continue
                        if description["fingerprint"] not in names:
                            return dict(status="NEEDS_ADMISSION", address=address, edge=edge,
                                        descriptions=(description,))
                        if self._value(context, item, name_prop) == selector:
                            found = item
                            break
                    if found is None:
                        return self.missing("UNKNOWN_NAME", edge, length=len(value.items), member=selector)
                    value = found
        return value, definition

    @staticmethod
    def missing(kind, edge, length=None, member=None):
        return dict(status="MISSING", kind=kind, edge=edge, length=length, member=member)

    @staticmethod
    def ok(kind, payload):
        return dict(status="OK", kind=kind, payload=payload)

    def describe(self, context, address, allowance, member, detail=False, copy=True):
        resolved = self._resolve(context, address, allowance)
        if type(resolved) is dict:
            return resolved
        value, originating = resolved
        if value is None:
            return self.missing("NULL", len(address[1])+len(address[2]))
        if isinstance(value, Collection) and not hasattr(value, "bl_rna"):
            value.bl_rna = rna("Collection", name_property=None)
        if member is not None:
            definition = value.bl_rna.properties.get(member)
            limits = None
            if detail and definition and definition.type in {"INT", "FLOAT"}:
                import math
                limits = {key: getattr(definition, key) if math.isfinite(getattr(definition, key)) else None
                          for key in ("hard_min", "hard_max")}
            return self.ok("MEMBER", dict(property=self.property(context, value, definition) if definition else None,
                                          function_exists=value.bl_rna.functions.get(member) is not None,
                                          members=tuple(prop.identifier for prop in value.bl_rna.properties),
                                          members_total=len(tuple(value.bl_rna.properties)), numeric_limits=limits))
        properties = tuple(self.property(context, value, definition) for definition in value.bl_rna.properties)
        key = self.declarations(context, value)
        held = not copy
        if isinstance(value, Collection):
            owner_address = self.parent_address(address)
            parent = self._resolve(context, owner_address, allowance)
            owner = parent[0] if type(parent) is tuple else context.scene
            description = self.property(context, owner, originating)
            return self.ok("COLLECTION", dict(address=address, property=description,
                                               collection_type=description["collection_type"], declarations=key,
                                               properties=None if held else properties))
        collections = tuple((prop.fixed_type.identifier, prop.identifier) for prop in self.bpy.data.bl_rna.properties
                            if prop.type == "COLLECTION" and prop.fixed_type is not None) if value is self.bpy.data else ()
        bookkeeping = ("users", "session_uid", "id_type", "library") if value.id_data is value else ()
        if held:
            properties = bookkeeping = collections = None
        return self.ok("STRUCT", dict(node=self.node(context, value, address), properties=properties,
                                       bookkeeping=bookkeeping, data_collections=collections))

    @staticmethod
    def parent_address(address):
        root, parsed, expansion = address
        return (root, parsed, expansion[:-1]) if expansion else (root, parsed[:-1], ())

    def fetch(self, context, address, allowance, expected):
        self.calls.append(("fetch", address))
        parent = self._resolve(context, self.parent_address(address), allowance)
        if type(parent) is dict:
            return parent
        return self._member(context, parent[0], address, expected)

    @staticmethod
    def extend(address, allowance, steps):
        """The address and allowance of a group or entry: ``address`` extended by relative steps."""
        edges, expansion = tuple(allowance[0]), address[2]
        for step in steps:
            if step[0] == "attribute":
                expansion += (("attribute", step[1]),)
                edges += ((len(address[1]) + len(expansion), step[2]),)
            else:
                expansion += (tuple(step),)
        return (address[0], address[1], expansion), (edges, allowance[1])

    def fetch_many(self, context, address, allowance, groups):
        """One resolution, then each group's members exactly as ``fetch`` of the extended address (tests patch ``fetch``)."""
        self.calls.append(("fetch_many", address, tuple(identifier for _steps, members in groups for identifier, _ in members)))
        resolved = self._resolve(context, address, allowance)
        if type(resolved) is dict:
            return resolved
        pages = []
        for steps, members in groups:
            node_address, node_allowance = self.extend(address, allowance, steps)
            node = self._resolve(context, node_address, node_allowance) if steps else resolved
            if type(node) is dict or node[0] is None or not isinstance(node[0], Struct) or len(node_address[2]) >= 2:
                pages.append(("RETRY",))
                continue
            pages.append(("OK", tuple(self.compact(self.fetch(
                context, (node_address[0], node_address[1], node_address[2] + (("attribute", identifier),)),
                node_allowance, expected)) for identifier, expected in members)))
        return self.ok("VALUES", tuple(pages))

    @staticmethod
    def compact(result):
        """A member's entry of a value page, as native returns it."""
        if result.get("status") == "OK":
            payload = result["payload"]
            notes = payload["annotations"]
            return ("OK", payload["tag"], payload["value"], notes["value_source"], notes["computed_getter"])
        if result.get("status") == "UNAVAILABLE":
            return ("UNAVAILABLE", result["code"])
        return ("RETRY",)

    def _member(self, context, owner, address, expected):
        steps = address[1] + address[2]
        definition = owner.bl_rna.properties.get(steps[-1][1])
        if definition is None:
            return self.missing("NOT_A_STRUCT", len(steps)-1)
        if self.property(context, owner, definition)["fingerprint"] != expected:
            return dict(status="STALE_ADMISSION", address=address, edge=len(steps)-1)
        notes = dict(value_source=None, computed_getter=None)
        if self.is_storage(definition):
            record = self._stored_record(owner, (definition.identifier,))
            defaults = dict(subtype=None, length=None, value=None, default=None, default_array=None,
                            enum_default=None, enum_items_static=None)
            defaults.update(record)
            if record["state"] == "ABSENT":
                defaults.update(default=definition.default, default_array=tuple(definition.default_array[:64]),
                                enum_default=getattr(definition, "enum_default", 0))
            if definition.type == "ENUM":
                defaults["enum_items_static"] = tuple((item.identifier, item.name, item.value)
                                                       for item in definition.enum_items_static)
            if isinstance(defaults["value"], Struct):
                defaults["value"] = self.node(context, defaults["value"], address)
            if definition.type == "POINTER" and record["state"] in {"GROUP", "ABSENT"}:
                defaults["value"] = self.node(context, self._value(context, owner, definition), address) if self._value(context, owner, definition) is not None else None
            if type(defaults["value"]) is list:
                defaults["value"] = tuple(defaults["value"])
            defaults.setdefault("type", "NONE")
            if getattr(definition, "has_python_getter", False) or getattr(definition, "has_python_enum_items", False):
                notes.update(value_source="default" if record["state"] == "ABSENT" else "stored", computed_getter="not_run")
            return self.ok("VALUE", dict(tag="RAW", value=defaults, annotations=notes))
        names = (owner.bl_rna.identifier, *self.type_info(owner.bl_rna)["bases"])
        name = definition.identifier
        if "UDIMTile" in names and name in {"size", "channels"}:
            record = self.image_tile_metadata(owner.id_data, owner._values["number"], 0, 0)
            if record is None:
                return dict(status="UNAVAILABLE", code="LOADS_FILE")
            value = record[name]
        elif "Image" in names and name in {"size", "channels", "depth", "is_float", "resolution", "file_format", "frame_duration"}:
            record = self.image_metadata(owner)
            if record is None or name not in record:
                return dict(status="UNAVAILABLE", code="LOADS_FILE")
            value = record[name]
        else:
            value = self._value(context, owner, definition)
        if isinstance(value, Struct):
            tag, value = "STRUCT", self.node(context, value, address)
        elif isinstance(value, Collection):
            tag, value = "COLLECTION", dict(address=address, length=len(value.items))
        elif value is None:
            tag = "NULL"
        elif type(value) in (tuple, list):
            flat = []
            def flatten(items):
                for item in items:
                    if type(item) in (tuple, list):
                        flatten(item)
                    else:
                        flat.append(item)
            flatten(value)
            tag, value = "ARRAY", dict(flat=tuple(flat[:64]), declared_length=definition.array_length,
                                       actual_length=len(flat), dimensions=definition.array_dimensions)
        elif type(value) in (set, frozenset):
            tag, value = "ENUM_FLAGS", tuple(sorted(value))
        else:
            tag = definition.type
        return self.ok("VALUE", dict(tag=tag, value=value, annotations=notes))

    def items(self, context, address, allowance, offset, limit):
        resolved = self._resolve(context, address, allowance)
        if type(resolved) is dict:
            return resolved
        collection, _ = resolved
        items = tuple(dict(index=index, node=self.node(context, item,
                            (address[0], address[1], address[2]+(("index", index),))) if item is not None else None)
                      for index, item in enumerate(collection.items[offset:offset+limit], start=offset))
        return self.ok("ITEMS", dict(length=len(collection.items), offset=offset, items=items,
                                     more=offset+len(items)<len(collection.items)))

    def choices(self, context, address, allowance, expected, offset=0, limit=64):
        parent = self._resolve(context, self.parent_address(address), allowance)
        if type(parent) is dict:
            return parent
        owner, _ = parent
        definition = owner.bl_rna.properties.get((address[1]+address[2])[-1][1])
        source = definition.enum_items_static if definition.is_runtime else definition.enum_items
        items = tuple((item.identifier, item.name, getattr(item, "value", index)) for index, item in enumerate(source))
        return self.ok("CHOICES", dict(items=items[offset:offset+limit], length=len(items), offset=offset,
                                       more=offset+limit<len(items)))

    def reference(self, context, address, allowance, relation, fields):
        resolved = self._resolve(context, address, allowance)
        if type(resolved) is dict:
            return resolved
        value, _ = resolved
        if relation == "owner":
            value = value.id_data
        requested = dict(fields)
        descriptions, result = [], []
        for definition in value.bl_rna.properties:
            if definition.identifier not in {value.bl_rna.name_property.identifier if value.bl_rna.name_property else None,
                                            "identifier", "type", "enabled"}:
                continue
            from angelo_agent import data_read_values as values
            if definition.type not in {"STRING", "ENUM", "BOOLEAN"} or values.is_array(definition):
                continue
            if definition.type == "STRING" and values.text_reason((value.bl_rna.identifier,), definition):
                continue
            description = self.property(context, value, definition)
            if requested.get(definition.identifier) != description["fingerprint"]:
                descriptions.append(description)
                continue
            try:
                item = self._value(context, value, definition)
            except AttributeError:
                item = "" if definition.type == "STRING" else None
            notes = dict(value_source=None, computed_getter=None)
            if getattr(definition, "has_python_getter", False):
                notes.update(value_source="stored" if definition.identifier in value._values else "default", computed_getter="not_run")
            result.append(dict(identifier=definition.identifier, tag=definition.type, value=item,
                               withheld=None, annotations=notes))
        if descriptions:
            return dict(status="NEEDS_ADMISSION", address=address, edge=len(address[1])+len(address[2]),
                        descriptions=tuple(descriptions))
        identity = None
        if value.id_data is value:
            identity = dict(name=value._values.get("name", ""), id_type=value._values.get("id_type", ""),
                            embedded=value._values.get("is_embedded_data", False), linked=value._values.get("library") is not None)
        return self.ok("REFERENCE", dict(node=self.node(context, value, address), id_facts=identity, fields=tuple(result)))

    def reference_many(self, context, address, allowance, entries):
        """One resolution, then each entry exactly as ``reference`` (and ``object_identity``) of the extended address."""
        self.calls.append(("reference_many", address, tuple(steps for steps, _relation, _fields in entries)))
        resolved = self._resolve(context, address, allowance)
        if type(resolved) is dict:
            return resolved
        answers = []
        for steps, relation, fields in entries:
            entry_address, entry_allowance = self.extend(address, allowance, steps)
            reference = self.reference(context, entry_address, entry_allowance, relation, fields)
            identity = None
            if reference.get("status") == "OK" and (reference["payload"]["id_facts"] or {}).get("id_type") == "OBJECT":
                identity = self.object_identity(context, entry_address, entry_allowance, relation)
            answers.append((reference, identity))
        return self.ok("REFERENCES", tuple(answers))

    def object_identity(self, context, address, allowance, relation):
        resolved = self._resolve(context, address, allowance)
        if type(resolved) is dict:
            return resolved
        value, _ = resolved
        if relation == "owner":
            value = value.id_data
        payload = None
        if value._values.get("id_type") == "OBJECT" and value in context.scene._values["objects"].items[:20000]:
            payload = dict(type_name="Struct", session_uid=value._values["session_uid"], pointer=id(value),
                           scene_uid=context.scene._values["session_uid"])
        return self.ok("OBJECT_IDENTITY", payload)
