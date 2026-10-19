# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request-local native address access and copied admission facts.

Detached records and their closed validators live in data_read_records.
Access retains the native context until copied results and epoch checks finish.
Parsed property descriptions are shared between reads by their native declarations
key (``_DECLARATIONS``); values, nodes and admissions are never shared.
"""
from collections import OrderedDict
from dataclasses import dataclass
import time
import re
import math

from . import data_read_values as values
from .data_read_records import (
    NATIVE_REQUIRED, NATIVE_MESSAGE, ReadUnavailable, Missing, TypeDecl, OwnerFacts,
    Node, PropertyDecl, Collection, _exact, _record, _copy, _annotations, _choice_items,
    _page, _raw, _freeze, _address, _type, _node, _native_node, _property, _native_property, _value_policy, _fingerprint, is_node,
    _OWNER, _DECL, _ROUTE, _ROUTES, _CODES, UNAVAILABLE_MESSAGES, UNAVAILABLE_MESSAGE,
)

from .data_read_fetch import FetchAccess
from .data_read_batches import (  # Re-exported: callers and tests use them from here.
    BatchAccess, _REFERENCE_FIELDS, _OWNER_FIELDS, _object_handle_for_read,
)

ADDRESS_READ_VERSION = 2
_NO_NOTES = {}
# Operations whose OK payload is a page of entries, each validated (``_copy``) only when used.
_PAGED = frozenset({"fetch_many", "reference_many"})
# Parsed property descriptions by native declarations key, shared by reads. A key is a hash of
# every description it stands for (type, owner facts, element traits, declarations, routes), so a
# changed list has another key; native still checks each declaration's fingerprint on every use.
_DECLARATIONS = OrderedDict()
MAX_DECLARATIONS = 512
_LIMITS = {}


def _remember(key, entry):
    _DECLARATIONS[key] = entry
    _DECLARATIONS.move_to_end(key)
    while len(_DECLARATIONS) > MAX_DECLARATIONS:
        _DECLARATIONS.popitem(last=False)


def _known(key):
    entry = _DECLARATIONS.get(key)
    if entry is not None:
        _DECLARATIONS.move_to_end(key)
    return entry


def native_api():
    try:
        from _bpy import _angelo_read
        return _angelo_read
    except ImportError:
        return None


# The native module as this add-on found it. Its results are built from a closed set of
# primitives (native ``Value``: None, bool, int, float, str, tuple, dict with str keys), fresh
# for every call, so they need no detaching copy. Any other backend is copied and checked.
_NATIVE = native_api()
_METHODS = ("begin", "describe", "fetch", "fetch_many", "choices", "items", "reference", "reference_many",
            "object_identity", "check", "close")


def _usable(api):
    return (api is not None and type(getattr(api, "ADDRESS_READ_VERSION", None)) is int
            and api.ADDRESS_READ_VERSION == ADDRESS_READ_VERSION
            and all(callable(getattr(api, name, None)) for name in _METHODS))


_NATIVE_USABLE = _usable(_NATIVE)
# Its functions as imported. A trusted read calls these, never the module's (writable) attributes,
# so a later reassignment of one cannot feed the trusted path.
_NATIVE_CALLS = {name: getattr(_NATIVE, name) for name in _METHODS} if _NATIVE_USABLE else {}


class Access(BatchAccess, FetchAccess):
    """One native context, copied admission facts and epoch for a read call."""

    _METHODS = _METHODS

    def __init__(self, window, deadline, epoch_owner, *, backend=None):
        self._api = native_api() if backend is None else backend
        # Results of the native module this add-on imported are used as built, through the
        # functions captured at import; any other backend's are copied and checked against the
        # closed record shapes, its functions looked up on each call.
        self._trusted = self._api is not None and self._api is _NATIVE
        self._calls = _NATIVE_CALLS if self._trusted else None
        self._parse_node = _native_node if self._trusted else _node
        self._parse_property = _native_property if self._trusted else _property
        if not (_NATIVE_USABLE if self._trusted else _usable(self._api)):
            raise ReadUnavailable(NATIVE_REQUIRED, NATIVE_MESSAGE)
        self.deadline, self._epoch_owner = deadline, epoch_owner
        self.epoch = epoch_owner()
        from . import scene_queries
        if epoch_owner is scene_queries.document_epoch:
            # The same epoch without the per-call thread check: native refuses other threads.
            self._epoch_owner = lambda: scene_queries._epoch
        self._context = self._native("begin")(window, float(max(.000001, min(2.0, deadline - time.monotonic()))))
        self._edges, self._names, self._notes, self._descriptions = {}, [], {}, {}
        self._array_lengths = {}
        self._prefetched, self._prefetched_nodes = {}, {}
        self._references, self._identities, self._roots = {}, {}, {}
        self._admitted_fields = None
        self.budget_exhausted = False
        try:
            self.assert_epoch()
        except Exception:
            self.close()
            raise

    def assert_epoch(self):
        if self._epoch_owner() != self.epoch:
            raise ReadUnavailable("EXPIRED", "The document changed during this read; read again.")

    def _native(self, operation):
        return self._calls[operation] if self._calls is not None else getattr(self._api, operation)

    def close(self):
        if self._context is not None:
            self._native("close")(self._context)
            self._context = None

    def _allowance(self, address):
        steps = address[1] + address[2]
        edges = []
        for count in range(1, len(steps) + 1):
            prefix = (address[0], steps[:count])
            prop = self._edges.get(prefix)
            if prop is not None:
                edges.append((count, prop.fingerprint))
        return tuple(edges), tuple(prop.fingerprint for prop in self._names)

    def _admit(self, prop):
        # Name admission is label-only. Unknown, path, secret and script names
        # are not permission to run a getter, even to find a nonmatching item.
        names = dict(prop.projection[0])["type"]
        if prop.type != "STRING" or values.text_reason((names[0], *names[1]), prop):
            raise ReadUnavailable("DENIED", "This collection's names are not readable labels.")
        if prop.fingerprint not in [item.fingerprint for item in self._names]:
            self._names.append(prop)
            return True
        return False

    def _call(self, operation, address=None, *args, **kwargs):
        changed = False
        while True:
            self.assert_epoch()
            if address is None:
                result = self._native(operation)(self._context, *args, **kwargs)
            else:
                result = self._native(operation)(self._context, address, self._allowance(address), *args, **kwargs)
            self.assert_epoch()
            if self._trusted:
                # The native module's records have their closed shapes by construction.
                status = result["status"]
                if status == "OK" and operation != "check":
                    return result
            else:
                if operation in _PAGED and type(result) is dict and result.get("status") == "OK":
                    # A page's entries are validated when used: a batch copies more than a page shows.
                    result = {key: (_copy(item) if key != "payload" else item) for key, item in result.items()}
                else:
                    result = _copy(result)
                _exact(result, dict)
                status = _exact(result.get("status"), str)
            if status == "OK":
                if operation == "check":
                    _record(result, "status budget_exhausted")
                    self.budget_exhausted = _exact(result["budget_exhausted"], bool)
                    return result
                _record(result, "status kind payload")
                return result
            if status == "NEEDS_ADMISSION":
                _record(result, "status address edge descriptions")
                self._position(result, address)
                progress = False
                for description in _exact(result["descriptions"], tuple):
                    prop = _property(description)
                    if operation != "reference":
                        progress = self._admit(prop) or progress
                    elif prop.type == "STRING" and values.text_reason((dict(prop.projection[0])["type"][0], *dict(prop.projection[0])["type"][1]), prop):
                        raise ReadUnavailable("DENIED", "This reference field is not a readable label.")
                    if operation == "reference":
                        relation, fields = args
                        entry = (prop.identifier, prop.fingerprint)
                        if entry not in fields:
                            args = (relation, tuple(item for item in fields if item[0] != prop.identifier) + (entry,))
                            progress = True
                            self._admitted_fields = args[1]
                if not progress:
                    raise ValueError("Native admission made no progress")
                if operation != "reference" and time.monotonic() > self.deadline:
                    raise ReadUnavailable("BUDGET", "The read budget ended during collection name admission.", address=address)
                continue
            if status == "STALE_ADMISSION":
                _record(result, "status address edge")
                self._position(result, address)
                if changed:
                    raise ReadUnavailable("STALE_ADMISSION", "A declaration changed during this read; read again.", address=result["address"], edge=result["edge"])
                changed = True
                edge = _exact(result["edge"], int)
                original = _address(result["address"])
                steps = original[1] + original[2]
                if not 0 <= edge < len(steps) or steps[edge][0] != "attribute":
                    raise ReadUnavailable("STALE_ADMISSION", "A declaration changed during this read; read again.", address=original, edge=edge)
                prefix = (original[0], original[1][:edge], original[2][:max(0, edge-len(original[1]))])
                prop = self.member(self.describe(prefix), steps[edge][1])["property"]
                if prop is None:
                    raise Missing(dict(kind="NOT_A_STRUCT", edge=edge, length=None, member=steps[edge][1]), original)
                old = self._edges.get((original[0], steps[:edge+1]))
                if old is None or old.type != prop.type or old.fixed_type != prop.fixed_type:
                    raise ReadUnavailable("STALE_ADMISSION", "The property type changed during this read; read again.", address=original, edge=edge)
                self._value_policy(prop)
                if operation in {"fetch", "choices"} and edge == len(steps)-1:
                    # Only route metadata may change under the old converter/row.
                    # A later fresh read uses the changed declaration normally.
                    before, after = dict(old.projection[1]), dict(prop.projection[1])
                    for field in ("uses_idprop_storage", "has_python_getter", "has_python_enum_items"):
                        before.pop(field)
                        after.pop(field)
                    if (before != after or old.projection[0] != prop.projection[0]
                            or old.collection_type != prop.collection_type or old.fixed_name != prop.fixed_name):
                        raise ReadUnavailable("STALE_ADMISSION", "The property declaration changed during this read; read again.",
                                              address=original, edge=edge)
                self.admit_edge(prefix, prop, derived=edge >= len(original[1]))
                if operation in {"fetch", "choices"} and edge == len(steps)-1:
                    args = (prop.transport(), *args[1:])
                continue
            if status == "MISSING":
                _record(result, "status kind edge length member")
                self._position(result, address)
                if result["kind"] not in {"NULL", "INDEX_RANGE", "NO_NAMES", "UNKNOWN_NAME", "NOT_A_COLLECTION", "NOT_A_STRUCT"}:
                    raise ValueError("Invalid missing read kind")
                if result["length"] is not None:
                    _exact(result["length"], int)
                if result["member"] is not None:
                    _exact(result["member"], str)
                raise Missing(result, address)
            if status == "BUDGET":
                _record(result, "status")
                self.budget_exhausted = True
                raise ReadUnavailable("BUDGET", "The read budget ended before this value could be copied.", address=address)
            if status == "EXPIRED":
                _record(result, "status reason")
                if result["reason"] not in {"CLOSED", "DOCUMENT", "WINDOW", "SCENE"}:
                    raise ValueError("Invalid read expiry reason")
                raise ReadUnavailable("EXPIRED", "The document or window changed during this read; read again.", address=address)
            if status == "UNAVAILABLE":
                _record(result, "status code")
                code = _exact(result["code"], str)
                if code not in _CODES:
                    raise ValueError("Unknown native read refusal code")
                raise ReadUnavailable(code, UNAVAILABLE_MESSAGES.get(code, UNAVAILABLE_MESSAGE), address=address)
            raise ValueError("Unknown native read status")

    @staticmethod
    def _position(result, address):
        original = _address(result.get("address", address))
        edge = _exact(result["edge"], int)
        if address is not None and original != address:
            raise ValueError("Native replay changed the requested address")
        if not 0 <= edge <= len(original[1]) + len(original[2]):
            raise ValueError("Invalid native replay position")

    _value_policy = staticmethod(_value_policy)

    def check(self):
        self._call("check")
        self.assert_epoch()

    def describe(self, address):
        # Descriptions are copied only for a declarations key this process does not hold yet.
        result = self._call("describe", address, None, False, False)
        payload = result["payload"] if result["kind"] in {"STRUCT", "COLLECTION"} else None
        if type(payload) is dict:
            node = payload.get("node")
            key = (node[7] if is_node(node) else None) if result["kind"] == "STRUCT" else payload.get("declarations")
            if key not in _DECLARATIONS:
                result = self._call("describe", address, None, False, True)
        if result["kind"] == "STRUCT":
            d = result["payload"] if self._trusted else _record(
                result["payload"], "node properties bookkeeping data_collections")
            node = self._parse_node(d["node"])
            if d["properties"] is None:
                entry = _known(node.declarations)
                if entry is None or d["bookkeeping"] is not None or d["data_collections"] is not None:
                    raise ValueError("Native omitted declarations this reader does not hold")
            else:
                for identifier in _exact(d["bookkeeping"], tuple):
                    _exact(identifier, str)
                for pair in _exact(d["data_collections"], tuple):
                    _exact(pair, tuple)
                    if len(pair) != 2:
                        raise ValueError("Invalid data collection declaration")
                    _exact(pair[0], str)
                    _exact(pair[1], str)
                parse = self._parse_property
                entry = (tuple(parse(item) for item in _exact(d["properties"], tuple)),
                         _exact(d["bookkeeping"], tuple), _exact(d["data_collections"], tuple))
                _remember(node.declarations, entry)
            self._descriptions[node.address] = entry
            return node
        if result["kind"] == "COLLECTION":
            d = _record(result["payload"], "address property collection_type declarations properties")
            prop = _property(d["property"])
            collection_type = _type(d["collection_type"])
            if collection_type != prop.collection_type:
                raise ValueError("Inconsistent collection declaration")
            key = _fingerprint(d["declarations"])
            if d["properties"] is None:
                entry = _known(key)
                if entry is None:
                    raise ValueError("Native omitted declarations this reader does not hold")
            else:
                parse = self._parse_property
                entry = (tuple(parse(item) for item in _exact(d["properties"], tuple)), (), ())
                _remember(key, entry)
            collection = Collection(_address(d["address"]), prop, 0)
            self._descriptions[collection.address] = entry
            return collection
        raise ValueError("Invalid native description kind")

    def root(self, root):
        node = self._roots.get(root)
        if node is None:
            node = self._roots[root] = self.describe((root, (), ()))
        return node

    def numeric_limits(self, owner, prop):
        """A number's hard limits; part of its declaration, so kept by fingerprint."""
        limits = _LIMITS.get(prop.fingerprint)
        if limits is None:
            limits = self.member(owner, prop.identifier, detail=True)["numeric_limits"]
            if len(_LIMITS) >= 4096:
                _LIMITS.clear()
            _LIMITS[prop.fingerprint] = limits
        return limits

    def member(self, node, name, *, detail=False):
        if not detail:
            # A held declaration list answers a found member; the fetch that follows is admitted by
            # its fingerprint. A missing member is described natively (members, functions).
            entry = self._descriptions.get(node.address)
            if entry is None and isinstance(node, Node) and node.declarations is not None:
                entry = _known(node.declarations)
            if entry is not None:
                for prop in entry[0]:
                    if prop.identifier == name:
                        return dict(property=prop, function_exists=False, members=(), members_total=0,
                                    numeric_limits=None)
        result = self._call("describe", node.address, name, detail)
        if result["kind"] != "MEMBER":
            raise ValueError("Invalid native member kind")
        d = _record(result["payload"], "property function_exists members members_total numeric_limits")
        for identifier in _exact(d["members"], tuple):
            _exact(identifier, str)
        if _exact(d["members_total"], int) < 0:
            raise ValueError("Invalid member count")
        if d["numeric_limits"] is not None:
            limits = _record(d["numeric_limits"], "hard_min hard_max")
            for number in limits.values():
                if number is not None and (type(number) not in (int, float) or not math.isfinite(number)):
                    raise ValueError("Invalid numeric declaration limit")
        return dict(property=self._parse_property(d["property"]) if d["property"] is not None else None,
                    function_exists=_exact(d["function_exists"], bool), members=_exact(d["members"], tuple),
                    members_total=_exact(d["members_total"], int), numeric_limits=d["numeric_limits"])

    def holds(self, node):
        """Whether this request holds ``node``'s declarations (described here or held by key)."""
        if self._descriptions.get(node.address) is not None:
            return True
        entry = _known(node.declarations) if isinstance(node, Node) and node.declarations is not None else None
        if entry is not None:
            self._descriptions[node.address] = entry
        return entry is not None

    def properties(self, node):
        entry = self._descriptions.get(node.address)
        if entry is None and isinstance(node, Node) and node.declarations is not None:
            # A node copied this request names its declarations; a held list needs no description.
            entry = _known(node.declarations)
            if entry is not None:
                self._descriptions[node.address] = entry
        if entry is None:
            self.describe(node.address)
            entry = self._descriptions[node.address]
        return entry[0]

    def bookkeeping(self, node):
        self.properties(node)
        return self._descriptions[node.address][1]

    def data_collections(self):
        node = self.root("data")
        return self._descriptions[node.address][2]

    def admit_edge(self, address, prop, *, derived=False):
        root, parsed, expansion = address
        step = ("attribute", prop.identifier)
        if derived:
            # A validated address plus a copied declaration's identifier: only the bound can fail.
            if len(expansion) >= 2:
                raise ValueError("Invalid native read address length")
            target = (root, parsed, expansion + (step,))
        else:
            target = _address((root, parsed + (step,), expansion))
        self._edges[(root, target[1] + target[2])] = prop
        return target

    def annotations(self, owner, prop):
        notes = self._notes.get((owner.address, prop.identifier)) if self._notes else None
        return dict(notes) if notes else _NO_NOTES

    def collection_at(self, owner, prop):
        """The collection a public step names, admitted but not fetched (its length is unknown)."""
        return Collection(self.admit_edge(owner.address, prop, derived=False), prop, None)

    def item(self, collection, kind, key, *, derived=False):
        root, parsed, expansion = collection.address
        step = (kind, key)
        address = (root, parsed, expansion + (step,)) if derived else (root, parsed + (step,), expansion)
        try:
            return self.describe(_address(address))
        except Missing as missing:
            if missing.code == "NULL" and missing.edge == len(address[1]) + len(address[2]):
                return None
            raise

    def items(self, collection, offset, limit):
        result = self._call("items", collection.address, offset, limit)
        if result["kind"] != "ITEMS":
            raise ValueError("Invalid native item page kind")
        if self._trusted:
            d = result["payload"]
            parse = self._parse_node
            items = tuple((item["index"], parse(item["node"]) if item["node"] is not None else None)
                          for item in d["items"])
            return items, d["length"], d["more"]
        d = _page(result["payload"], offset)
        if len(d["items"]) > limit:
            raise ValueError("Invalid native item page size")
        items = []
        for item in _exact(d["items"], tuple):
            i = _record(item, "index node")
            index = _exact(i["index"], int)
            if index < 0:
                raise ValueError("Invalid native item index")
            items.append((index, self._parse_node(i["node"]) if i["node"] is not None else None))
        return tuple(items), _exact(d["length"], int), _exact(d["more"], bool)

    def choices(self, owner, prop):
        address = self.admit_edge(owner.address, prop)
        accepted, offset, unfinished = [], 0, False
        while len(accepted) <= values.MAX_CHOICES:
            try:
                result = self._call("choices", address, prop.transport(), offset, 64)
            except ReadUnavailable as error:
                if error.code != "BUDGET":
                    raise
                return tuple(accepted), True
            if result["kind"] != "CHOICES":
                raise ValueError("Invalid native choice page kind")
            d = _page(result["payload"], offset)
            source = _choice_items(d["items"], values.MAX_CHOICES)
            unfinished = d["more"]
            for identifier, label, number in source:
                if identifier and values.choice_text(identifier) and values.choice_text(label) is not None:
                    accepted.append((identifier, label, number))
                    if len(accepted) > values.MAX_CHOICES:
                        break
            if not d["more"]:
                break
            if not d["items"] or self.budget_exhausted or time.monotonic() > self.deadline:
                break
            offset += len(d["items"])
        return tuple(accepted), unfinished or len(accepted) > values.MAX_CHOICES
