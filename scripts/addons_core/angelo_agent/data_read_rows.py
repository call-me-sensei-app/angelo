# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rows of an ``angelo_read_data`` answer: properties of a struct and items of a collection.

``data_read`` resolves a path to one struct or collection; ``Rows`` lists what
is there, within the page, the character budget and the deadline its caller
gives it. A property row is its name, type and current value or why the value
is withheld (``data_read_values``); a pointer or an item is a reference (what
it is and how to read on), never its contents, unless ``depth`` 2 asks for the
values one level down. Every struct a row would show is admitted by
``data_read_policy`` first; a refused one is a row that says so.

Nothing is cached beyond one call and nothing is written. Native address
operations supply detached records; host tests explicitly inject a backend.
"""

import time

from . import data_read_policy as policy
from . import data_read_values as values
from .data_read_access import ReadUnavailable, Missing, _object_handle_for_read
from .data_read_values import encoded, _number_size, _text_size  # Row sizes in the answer's JSON.
from .data_path import DATA, quote

MAX_MEMBER_OBJECTS = 20000
# ``Rows.listing`` results by (declarations key, socket-input state); see there.
_LISTINGS = {}
# Row kinds ``_scalar_row`` may build, and informative labels by declaration fingerprint.
_SCALAR_ROWS = frozenset({"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING"})
_LABELS = {}
MAX_COLLECTION_PROPERTIES = 16
# Row fields copied from a collection item when its struct has them.
ROW_FIELDS = ("identifier", "type", "enabled")
SOCKET_INPUT_STRUCTS = frozenset({"GeometryNodesInterfaceInputs", "GeometryNodesInterfaceOutputs"})
# At depth 2 a row's own values take at most this share of the page's characters, and never less than MIN_NESTED.
NESTED_SHARE = 4
MIN_NESTED = 600


class Rows:
    def __init__(self, bpy, scene, deadline, handle=None, access=None):
        """The caller supplies the same request-local Access used by the walker."""
        self.deadline = deadline
        self.access = access
        if access is None:
            raise ValueError("Rows requires a request-local read Access")
        # Set while the path is inside a Geometry Nodes modifier's inputs: their text values are socket text.
        self.socket_inputs = False
        self._collections = None
        self._bookkeeping = None

    def note_struct(self, struct):
        """Remember that the path entered a modifier's socket inputs (``admit`` calls this for every struct)."""
        if not SOCKET_INPUT_STRUCTS.isdisjoint(values.struct_names(struct.type)):
            self.socket_inputs = True

    # References ----------------------------------------------------------------------------------------------

    def data_collections(self):
        if self._collections is None:
            self._collections = {kind: name for kind, name in self.access.data_collections()
                                 if name not in policy.DATA_DENIED}
        return self._collections

    def reference(self, value, *, locate=True, relation="self"):
        """Build the existing reference JSON from one freshly copied native record."""
        current, identity, fields = self.access.reference(value, relation)
        names = values.struct_names(current.type)
        row = {"struct": current.type.identifier}
        for field in fields:
            identifier, tag, item = field["identifier"], field["tag"], field["value"]
            label = "name" if identifier == current.type.name_property else identifier
            if field["withheld"]:
                row.setdefault("read_notes", {})[label] = {"withheld": field["withheld"]}
                continue
            notes = {key: note for key, note in field["annotations"].items() if note is not None}
            if notes:
                row.setdefault("read_notes", {})[label] = notes
            if tag == "STRING":
                item = values.screened_text(item, is_name=identifier in values.NAME_PROPERTIES)[0]
            elif tag == "ENUM":
                item = values.choice_text(item)
            elif tag != "BOOLEAN":
                item = None
            if item is not None:
                row[label] = item
        if identity is not None:
            if locate and not identity["embedded"]:
                for name in names:
                    collection = self.data_collections().get(name)
                    if collection:
                        row["read"] = "%s.%s[%s]" % (DATA, collection, quote(identity["name"]))
                        break
            if identity["linked"]:
                row["linked"] = True
            if identity["id_type"] == "OBJECT":
                handle = _object_handle_for_read(self.access, value, relation)
                if handle is not None:
                    row["handle"] = handle
        return row

    # Properties ----------------------------------------------------------------------------------------------

    def property_row(self, owner, prop, *, names, depth=1, detail=False, nested=0, public=False):
        """One property of ``owner``: name, type and current value, or why the value is withheld.

        ``names``: ``struct_names`` of ``owner``. ``detail``: the answer is this
        one property, so it adds the label, an enum's choices and a number's
        range. ``nested``: characters a pointer's own values may take at depth 2.
        """
        row = {"name": prop.identifier, "type": prop.type}
        # Add-on and generated properties (Cycles, modifier inputs) have no other description of what they are.
        label = values.informative_label(prop) if detail or getattr(prop, "is_runtime", False) else None
        if label:
            row["label"] = label
        if getattr(prop, "is_deprecated", False):
            row["deprecated"] = True  # Blender plans to remove it: a script should not rely on it.
        if policy.identity_only(owner) and prop.identifier not in policy.IDENTITY_PROPERTIES:
            return dict(row, withheld=values.LOADS_FILE)
        if "Image" in names:
            reason = values.image_reason(owner, prop)
            if reason:
                return dict(row, withheld=reason)
        if prop.type == "STRING":
            reason = values.text_reason(names, prop, socket_inputs=self.socket_inputs)
            if reason:
                return dict(row, withheld=reason)
        try:
            self._fill(row, owner, prop, names, depth, detail, nested, public)
            row.update(self.access.annotations(owner, prop))
        except Missing as error:
            if public:
                raise
            row["withheld"] = error.code
        except ReadUnavailable as error:
            if error.code in {"EXPIRED", "STALE_ADMISSION", "BUDGET"}:
                raise
            row.update(self.access.annotations(owner, prop))
            row["withheld"] = error.code
        except Exception as error:  # A getter that fails (an add-on's, or data in an unusual state) is one row's loss.
            row["unreadable"] = type(error).__name__
        return row

    def _scalar_row(self, owner, prop):
        """``(row, encoded(row))`` for a page row whose copied value is a plain number, choice or
        text, else None.

        Only a fetched row (``fetches``) with a prefetched value and nothing to note takes this
        path; it builds exactly the row ``property_row`` would, without its general steps, and
        counts its JSON characters as it goes ('{"name":' ... ',"type":' ... '}').
        """
        taken, value = self.access.take_scalar(owner, prop)
        if not taken:
            return None
        kind = prop.type
        row = {"name": prop.identifier, "type": kind}
        size = 17 + _text_size(prop.identifier) + len(kind) + 2
        if prop.is_runtime:
            label = _LABELS.get(prop.fingerprint, False)
            if label is False:
                label = _LABELS[prop.fingerprint] = values.informative_label(prop)
            if label:
                row["label"] = label
                size += 9 + _text_size(label)
        if prop.is_deprecated:
            row["deprecated"] = True
            size += 18
        if kind == "STRING":
            text, reason = values.screened_text(value, is_name=prop.identifier in values.NAME_PROPERTIES)
            if reason:
                row["withheld"] = reason
                size += 12 + _text_size(reason)
            else:
                row["value"] = text
                size += 9 + _text_size(text)
        elif kind == "ENUM":
            if values.choice_text(value) is None:
                row["withheld"] = values.PATH  # An add-on used file paths as the identifiers of its choices.
                size += 12 + _text_size(values.PATH)
            else:
                row["value"] = value
                size += 9 + _text_size(value)
        else:
            number = row["value"] = values.number(value)
            size += 9 + _number_size(number)
        return row, size

    def _fill(self, row, owner, prop, names, depth, detail, nested, public):
        kind = prop.type
        if kind == "COLLECTION":
            fixed = prop.fixed_type
            row.update(struct=fixed.identifier if fixed else None, length=self.access.value(owner, prop, derived=not public).length)
        elif kind == "POINTER":
            self._pointer(row, owner, prop, depth, nested, public)
        elif kind == "STRING":
            reason = values.text_reason(names, prop, socket_inputs=self.socket_inputs)
            if reason:
                row["withheld"] = reason  # Decided from the definition: the value is not fetched.
                return
            text, reason = values.screened_text(self.access.value(owner, prop, derived=not public),
                                                is_name=prop.identifier in values.NAME_PROPERTIES)
            row.update({"withheld": reason} if reason else {"value": text})
        elif kind == "ENUM":
            value = self.access.value(owner, prop, derived=not public)
            chosen = sorted(value) if isinstance(value, (set, frozenset)) else [value]
            if any(values.choice_text(item) is None for item in chosen):
                row["withheld"] = values.PATH  # An add-on used file paths as the identifiers of its choices.
                return
            row["value"] = chosen if isinstance(value, (set, frozenset)) else value
            if detail:
                self._choices(row, owner, prop)
        elif values.is_array(prop):
            row.update(values.array(self.access.value(owner, prop, derived=not public), prop,
                                    actual_length=self.access.array_length(owner, prop)))
        else:
            row["value"] = values.number(self.access.value(owner, prop, derived=not public))
        if detail:
            row["readonly"] = bool(prop.is_readonly)
            if kind in ("INT", "FLOAT"):
                row.update(values.limits(self.access.numeric_limits(owner, prop)))

    def _pointer(self, row, owner, prop, depth, nested, public):
        fixed = prop.fixed_type
        row["struct"] = fixed.identifier if fixed else None
        if fixed is not None and not policy.DENIED_STRUCTS.keys().isdisjoint(values.struct_names(fixed)):
            row["withheld"] = "DENIED"  # Not even fetched: a dependency graph, a preview, packed bytes.
            return
        target = self.access.value(owner, prop, derived=not public)
        if target is None:
            row["value"] = None
            return
        refused = policy.refusal(target)
        if refused:
            row["withheld"] = refused[0]
            return
        row["value"] = self.reference(target)
        if depth > 1 and not policy.is_id(target):
            saved = self.socket_inputs
            self.note_struct(target)
            try:
                rows, _total, more = self.struct_rows(target, 0, 10 ** 6, depth=1, budget=nested)
            finally:
                self.socket_inputs = saved
            row["properties"] = rows
            if more:
                row["more"] = True

    def _choices(self, row, owner, prop):
        source, truncated = self.access.choices(owner, prop)
        row["choices"], _ = values.choices(source)
        if truncated:
            row["choices_truncated"] = True
            return
        current = row["value"] if isinstance(row["value"], list) else [row["value"]]
        if not set(current) <= {choice["id"] for choice in row["choices"]}:
            # Blender computes some lists for the interface (render engines): RNA gives their fixed part.
            row["choices_partial"] = True

    def listed_properties(self, struct):
        """A struct's RNA properties in listing order.

        RNA lists a datablock's bookkeeping first (full name, session UID, flags,
        library and override state): twenty rows before its first setting. A
        datablock therefore lists its name, then its own properties, then that
        bookkeeping; every other struct keeps RNA's order.
        """
        properties = [prop for prop in self.access.properties(struct) if prop.identifier not in policy.DENIED_PROPERTIES]
        if not policy.is_id(struct):
            return properties
        if self._bookkeeping is None:
            self._bookkeeping = set(self.access.bookkeeping(struct)) - {"name"}
        own = [prop for prop in properties if prop.identifier not in self._bookkeeping]
        return own + [prop for prop in properties if prop.identifier in self._bookkeeping]

    def fetches(self, owner, prop, names):
        """Whether ``property_row`` fetches this property's value (as a page row, not a public read)."""
        if owner.is_data_root and prop.identifier in policy.DATA_DENIED:
            return False
        if policy.identity_only(owner) and prop.identifier not in policy.IDENTITY_PROPERTIES:
            return False
        if "Image" in names and values.image_reason(owner, prop):
            return False
        if prop.type == "STRING" and values.text_reason(names, prop, socket_inputs=self.socket_inputs):
            return False
        if prop.type == "POINTER" and prop.fixed_type is not None and not policy.DENIED_STRUCTS.keys().isdisjoint(
                values.struct_names(prop.fixed_type)):
            return False
        return True

    def listing(self, struct):
        """``(listed, fetched)``: ``listed_properties`` and the identifiers whose values its rows fetch.

        Both are functions of the struct's declarations (its type and owner facts are part of
        their key) and the socket-input state, so they are computed once for each.
        """
        key = (struct.declarations, self.socket_inputs)
        known = _LISTINGS.get(key)
        if known is not None and self.access.holds(struct):
            return known
        names = values.struct_names(struct.type)
        listed = self.listed_properties(struct)
        fetched = frozenset(prop.identifier for prop in listed if self.fetches(struct, prop, names))
        if len(_LISTINGS) >= 4096:
            _LISTINGS.clear()
        _LISTINGS[key] = (listed, fetched)
        return listed, fetched

    def _first_fetched(self, struct, budget):
        """The properties of ``struct`` its nested rows fetch in their first batch."""
        saved = self.socket_inputs
        self.note_struct(struct)
        try:
            listed, fetched = self.listing(struct)
        finally:
            self.socket_inputs = saved
        return [prop for prop in listed if prop.identifier in fetched][:max(8, min(64, budget // 60))]

    def _prefetch_nested(self, base, targets, nested):
        """Copy, in one native operation each, the first values of ``targets``' nested rows and
        the references their pointers show. ``targets``: ``(steps, node)`` reached from ``base``.
        """
        groups = []
        for steps, node in targets:
            props = self._first_fetched(node, nested)
            if props:
                groups.append((node, steps, props))
        if not groups:
            return
        self.access.prefetch_groups(base, groups)
        references = []
        for node, steps, props in groups:
            for prop in props:
                inner = self.access.prefetched_node(node, prop) if prop.type == "POINTER" else None
                if inner is not None and not policy.refusal(inner):
                    references.append((steps + (("attribute", prop.identifier, prop.fingerprint),), inner))
        self.access.prefetch_references(base, references)

    def _prefetch(self, struct, batch):
        """Copy the values of ``batch`` and the references of its pointers, one native operation each."""
        self.access.prefetch(struct, batch)
        targets = []
        for prop in batch:
            node = self.access.prefetched_node(struct, prop) if prop.type == "POINTER" else None
            if node is not None and not policy.refusal(node):
                targets.append(((("attribute", prop.identifier, prop.fingerprint),), node))
        self.access.prefetch_references(struct, targets)

    def struct_rows(self, struct, offset, limit, *, depth, budget):
        """``(rows, total, more)`` for a page of a struct's properties within ``budget`` characters.

        Values are copied in batches: the first as many rows as the budget usually holds, then
        doubling, so a page cut short by its budget copied at most about twice the rows it shows.
        At depth 2 the nested rows of pointer rows are copied two pointer rows at a time.
        """
        names = values.struct_names(struct.type)
        listed, fetched_ids = self.listing(struct)
        nested = max(MIN_NESTED, budget // NESTED_SHARE)
        rows, used = [], 0
        page = listed[offset:offset + limit]
        fetched = [prop for prop in page if prop.identifier in fetched_ids]
        # Next unbatched fetched property, and the next batch's size (rows average about 60 characters).
        cursor, chunk = 0, max(8, min(64, budget // 60))
        expand = depth > 1 and len(struct.address[2]) == 0  # A nested row's value needs two derived steps.
        pointers = [prop for prop in fetched if prop.type == "POINTER"] if expand else []
        planned = 0  # Pointer rows whose nested rows are planned (two at a time: most pages stop early).
        access, deadline, monotonic = self.access, self.deadline, time.monotonic
        fetched_count, pointer_count, data_root = len(fetched), len(pointers), struct.is_data_root
        for prop in page:
            if rows and (access.budget_exhausted or monotonic() > deadline):
                break
            if cursor < fetched_count and fetched[cursor] is prop:
                self._prefetch(struct, fetched[cursor:cursor + chunk])
                cursor, chunk = cursor + chunk, min(chunk * 2, 64)
            if planned < pointer_count and pointers[planned] is prop:
                targets = []
                while planned < pointer_count and len(targets) < 2:
                    candidate = pointers[planned]
                    planned += 1
                    node = access.prefetched_node(struct, candidate)
                    if node is not None and not policy.refusal(node) and not policy.is_id(node):
                        targets.append(((("attribute", candidate.identifier, candidate.fingerprint),), node))
                if targets:
                    self._prefetch_nested(struct, targets, nested)
            if data_root and prop.identifier in policy.DATA_DENIED:
                row = {"name": prop.identifier, "type": prop.type, "withheld": "DENIED"}
                size = encoded(row)
            elif prop.type in _SCALAR_ROWS and (scalar := self._scalar_row(struct, prop)) is not None:
                row, size = scalar
            else:
                try:
                    row = self.property_row(struct, prop, names=names, depth=depth, nested=nested)
                except ReadUnavailable as error:
                    if error.code != "BUDGET" or not rows:
                        raise
                    break
                size = encoded(row)
            if rows and (used + size > budget or monotonic() > deadline):
                break
            if "properties" in row and used + size > budget:  # A first row too large by itself keeps its reference.
                row = {key: value for key, value in row.items() if key != "properties"}
                row["more"] = True
                size = encoded(row)
            rows.append(row)
            used += size
        return rows, len(listed), offset + len(rows) < len(listed)

    # Collections ---------------------------------------------------------------------------------------------

    def collection_properties(self, collection):
        return [prop.identifier for prop in self.access.properties(collection)
                if prop.identifier not in policy.DENIED_PROPERTIES][:MAX_COLLECTION_PROPERTIES]

    def item_rows(self, collection, offset, limit, *, depth, budget):
        """``(rows, length, more)`` for a page of a collection's items within ``budget`` characters.

        At depth 2 an item's rows may take a quarter of the budget, so items are copied in
        pages of 4, 8, 16 ... as rows fit instead of all ``limit`` at once.
        """
        length = collection.length
        rows, used = [], 0
        position, page, stopped = offset, (limit if depth <= 1 else min(limit, NESTED_SHARE)), False
        while not stopped and position < offset + limit:
            count = min(page, offset + limit - position)
            items, length, _native_more = self.access.items(collection, position, count)
            targets = [((("index", index),), item) for index, item in items
                       if item is not None and not policy.refusal(item)]
            self.access.prefetch_references(collection, targets)
            if depth > 1 and len(collection.address[2]) == 0:  # A nested row's value needs two derived steps.
                self._prefetch_nested(collection, targets, max(MIN_NESTED, budget // NESTED_SHARE))
            for index, item in items:
                if rows and (self.access.budget_exhausted or time.monotonic() > self.deadline):
                    stopped = True
                    break
                row = {"index": index}
                refused = policy.refusal(item) if item is not None else None
                if item is None:
                    row["value"] = None  # An empty slot (a mesh's unset material).
                elif refused:
                    row["withheld"] = refused[0]
                else:
                    try:
                        row.update(self.reference(item, locate=False))
                    except Missing as error:
                        row["withheld"] = error.code
                    except ReadUnavailable as error:
                        if error.code != "BUDGET" or not rows:
                            raise
                        stopped = True
                        break
                    if depth > 1 and "withheld" not in row:
                        saved = self.socket_inputs
                        self.note_struct(item)
                        try:
                            nested, _total, more = self.struct_rows(item, 0, 10 ** 6, depth=1,
                                                                   budget=max(MIN_NESTED, budget // NESTED_SHARE))
                        except Missing as error:
                            row["withheld"] = error.code
                        else:
                            row["properties"] = nested
                            if more:
                                row["more"] = True
                        finally:
                            self.socket_inputs = saved
                size = encoded(row)
                if rows and (used + size > budget or time.monotonic() > self.deadline):
                    stopped = True
                    break
                rows.append(row)
                used += size
            if len(items) < count:
                break  # The collection ended (or the native deadline cut the page).
            position, page = position + len(items), min(page * 2, 64)
        return rows, length, offset + len(rows) < length
