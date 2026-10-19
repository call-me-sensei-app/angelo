# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Value and reference pages for ``data_read_access.Access``: the members of several structs,
or the references of several nodes, copied in one native operation each and used once."""

from . import data_read_values as values
from .data_read_fetch import _SCALAR_TAGS
from .data_read_records import ReadUnavailable, _exact, _record, _copy, _annotations, _CODES

# Shapes of the entries of a native value page (fetch_many).
_COMPACT = frozenset({("OK", 5), ("UNAVAILABLE", 2), ("RETRY", 1)})
_NAVIGABLE = frozenset({"POINTER", "COLLECTION"})
# Reference label fields by declarations key, and the owner label fields last admitted per ID type.
_REFERENCE_FIELDS = {}
MAX_REFERENCE_FIELDS = 512
_OWNER_FIELDS = {}


class BatchAccess:
    """Pages and references; mixed into ``data_read_access.Access``, whose state it uses."""

    def prefetch(self, owner, props):
        """Copy several members of one struct in one native operation for ``value`` to use."""
        self.prefetch_groups(owner, [(owner, (), props)])

    def prefetch_groups(self, base, groups):
        """Copy the members of several structs reached from ``base`` in one native operation.

        ``groups``: ``(node, steps, props)``; ``steps`` lead from ``base`` to ``node``
        (``("attribute", identifier, fingerprint)`` or ``("index", index)``, at most two) and
        ``node`` is the struct this request copied there. Members are admitted exactly as
        ``value`` admits them. Anything but a value or a refusal (a changed declaration, the
        deadline, a node that no longer resolves) is left to ``value``, which fetches that
        member alone; so is everything when the batch itself fails.
        """
        native, admitted = [], []
        for node, steps, props in groups:
            if len(node.address[2]) >= 2:
                continue
            props = [prop for prop in props if (node.address, prop.identifier) not in self._prefetched]
            if props:
                for prop in props:
                    if prop.type in _NAVIGABLE:  # A later address may extend these members.
                        self.admit_edge(node.address, prop, derived=True)
                admitted.append((node, props))
                native.append((tuple(steps), tuple((prop.identifier, prop.fingerprint) for prop in props)))
        if not native:
            return
        try:
            result = self._call("fetch_many", base.address, tuple(native))
        except ReadUnavailable as error:
            if error.code in {"EXPIRED", "BUDGET"}:
                raise
            return
        except Exception:  # Each row then fetches alone and reports its own failure.
            return
        if result["kind"] != "VALUES":
            raise ValueError("Invalid native value page kind")
        pages = _exact(result["payload"], tuple)
        if len(pages) != len(native):
            raise ValueError("Invalid native value page size")
        for (node, members), page in zip(admitted, pages):
            if page == ("RETRY",):
                continue
            if type(page) is not tuple or len(page) != 2 or page[0] != "OK" or type(page[1]) is not tuple \
                    or len(page[1]) != len(members):
                raise ValueError("Invalid native value page group")
            prefetched = self._prefetched
            if self._trusted:
                for prop, entry in zip(members, page[1]):
                    if entry[0] != "RETRY":
                        prefetched[(node.address, prop.identifier)] = entry
                continue
            for prop, entry in zip(members, page[1]):
                # ("OK", tag, value, value_source, computed_getter), ("UNAVAILABLE", code) or ("RETRY",).
                if type(entry) is not tuple or not entry or type(entry[0]) is not str \
                        or (entry[0], len(entry)) not in _COMPACT:
                    raise ValueError("Invalid native value page entry")
                if entry[0] != "RETRY":
                    prefetched[(node.address, prop.identifier)] = entry

    def take_scalar(self, owner, prop):
        """``(True, value)`` for a prefetched exact scalar value with nothing to note (removed from
        the page), else ``(False, None)`` and the member stays for ``value``."""
        key = (owner.address, prop.identifier)
        entry = self._prefetched.get(key)
        if entry is None or entry[0] != "OK" or entry[3] is not None or entry[4] is not None \
                or type(entry[1]) is not str or prop.array_length or prop.has_dynamic_length:
            return False, None
        kind = _SCALAR_TAGS.get(entry[1])
        if kind is None or entry[1] != prop.type or type(entry[2]) is not kind:
            return False, None
        del self._prefetched[key]
        if self._notes:
            self._notes.pop(key, None)
        return True, entry[2]

    def reference_fields(self, node):
        """The label fields a reference to ``node`` shows: name, identifier, type and enabled.

        A function of the node's declarations (its type is part of their key), so it is kept
        with them for a node that names its declarations key.
        """
        key = getattr(node, "declarations", None)
        if key is not None and key in _REFERENCE_FIELDS:
            return _REFERENCE_FIELDS[key]
        fields = []
        for prop in self.properties(node):
            if prop.identifier in {node.type.name_property, "identifier", "type", "enabled"}:
                if prop.type in {"STRING", "ENUM", "BOOLEAN"} and not values.is_array(prop):
                    if prop.type != "STRING" or not values.text_reason(values.struct_names(node.type), prop):
                        fields.append(prop)
        fields = tuple((prop.identifier, prop.fingerprint) for prop in fields)
        if key is not None and len(_REFERENCE_FIELDS) < MAX_REFERENCE_FIELDS:
            _REFERENCE_FIELDS[key] = fields
        return fields

    def prefetched_node(self, owner, prop):
        """The struct a prefetched pointer member refers to, or None (parsed once, shared with ``value``)."""
        key = (owner.address, prop.identifier)
        node = self._prefetched_nodes.get(key)
        if node is not None:
            return node
        entry = self._prefetched.get(key)
        if entry is None or entry[0] != "OK" or entry[1] != "STRUCT":
            return None
        node = self._prefetched_nodes[key] = self._parse_node(entry[2] if self._trusted else _copy(entry[2]))
        return node

    def prefetch_references(self, base, entries):
        """Copy the references (and Object identities) of several nodes in one native operation.

        ``entries``: ``(steps, node)`` for nodes this request copied, ``steps`` leading from
        ``base`` to the node as in ``prefetch_groups``. ``reference`` and ``object_identity``
        use a stored answer once; anything else (a refusal, a changed declaration) is left to
        them to fetch alone.
        """
        entries = [(tuple(steps), node) for steps, node in entries
                   if node.address not in self._references and len(node.address[2]) <= 2]
        if not entries:
            return
        native = tuple((steps, "self", self.reference_fields(node)) for steps, node in entries)
        try:
            result = self._call("reference_many", base.address, native)
        except ReadUnavailable as error:
            if error.code in {"EXPIRED", "BUDGET"}:
                raise
            return
        except Exception:  # Each reference is then fetched alone and reports its own failure.
            return
        if result["kind"] != "REFERENCES":
            raise ValueError("Invalid native reference page kind")
        answers = _exact(result["payload"], tuple)
        if len(answers) != len(entries):
            raise ValueError("Invalid native reference page size")
        for (_steps, node), answer in zip(entries, answers):
            if type(answer) is not tuple or len(answer) != 2:
                raise ValueError("Invalid native reference page entry")
            reference, identity = answer
            if type(reference) is dict and reference.get("status") == "OK":
                self._references[node.address] = (reference, identity)

    def reference(self, node, relation="self"):
        stored = self._references.pop(node.address, None) if relation == "self" else None
        if stored is not None:
            self.assert_epoch()
            result = stored[0] if self._trusted else _record(_copy(stored[0]), "status kind payload")
            identity = stored[1]
            if identity is not None:
                self._identities[node.address] = identity
        else:
            # An owner's label fields are admitted natively; the last admitted ones for its ID
            # type usually still match, which saves the admission round trip.
            fields = self.reference_fields(node) if relation == "self" else _OWNER_FIELDS.get(node.owner.id_type, ())
            self._admitted_fields = None
            result = self._call("reference", node.address, relation, fields)
            if relation == "owner" and self._admitted_fields is not None:
                _OWNER_FIELDS[node.owner.id_type] = self._admitted_fields
        if result["kind"] != "REFERENCE":
            raise ValueError("Invalid native reference kind")
        d = result["payload"]
        if self._trusted:
            return self._parse_node(d["node"]), d["id_facts"], d["fields"]
        _record(d, "node id_facts fields")
        if d["id_facts"] is not None:
            facts = _record(d["id_facts"], "name id_type embedded linked")
            _exact(facts["name"], str)
            _exact(facts["id_type"], str)
            _exact(facts["embedded"], bool)
            _exact(facts["linked"], bool)
        for field in _exact(d["fields"], tuple):
            _record(field, "identifier tag value withheld annotations")
            _exact(field["identifier"], str)
            if field["tag"] is not None:
                _exact(field["tag"], str)
            if field["withheld"] is not None and _exact(field["withheld"], str) not in _CODES:
                raise ValueError("Unknown reference refusal")
            item = field["value"]
            if item is not None and type(item) not in (bool, int, float, str, tuple):
                raise ValueError("Invalid reference value")
            if type(item) is tuple and any(type(part) not in (bool, int, float, str) for part in item):
                raise ValueError("Invalid reference tuple")
            if item is not None and field["tag"] in {"BOOLEAN", "STRING", "ENUM"}:
                _exact(item, {"BOOLEAN": bool, "STRING": str, "ENUM": str}[field["tag"]])
            _annotations(field["annotations"])
        return self._parse_node(d["node"]), d["id_facts"], d["fields"]

    def object_identity(self, node, relation="self"):
        stored = self._identities.pop(node.address, None) if relation == "self" else None
        if type(stored) is dict and stored.get("status") == "OK":
            self.assert_epoch()
            result = stored if self._trusted else _record(_copy(stored), "status kind payload")
        else:
            result = self._call("object_identity", node.address, relation)
        if result["kind"] != "OBJECT_IDENTITY":
            raise ValueError("Invalid native object identity kind")
        d = result["payload"]
        if d is not None and not self._trusted:
            _record(d, "type_name session_uid pointer scene_uid")
            _exact(d["type_name"], str)
            for key in ("session_uid", "pointer", "scene_uid"):
                _exact(d[key], int)
        return d


def _object_handle_for_read(access, node, relation="self"):
    """Fresh native membership/identity facts; never caller-supplied ID integers."""
    from . import scene_queries
    scene_queries._main_thread()
    facts = access.object_identity(node, relation)
    if facts is None:
        return None
    scene_queries._observe_identity(facts["type_name"], facts["session_uid"], facts["pointer"])
    access.assert_epoch()
    return f"scn:{scene_queries._epoch}:{facts['session_uid']}"
