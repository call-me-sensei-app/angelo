# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded final link facts and private exact-unlink conjunctions.

Only bound identifiers establish selector aliases. Unknown cardinality/aliases
retain the affected semantic facts as explicit unknowns; the latest requested
positive edge remains a mandatory fact.
"""

import copy

LINK_GUARD = "__host_node_links"
ENDS = ("from_node", "from_socket", "to_node", "to_socket")


class LinkFacts:
    def __init__(self, roles, unknown, *, strict=False, private=False, input_ids=None, output_ids=None, output_names=None):
        self.input_ids, self.output_ids = copy.deepcopy(input_ids or {}), copy.deepcopy(output_ids or {})
        self.output_names = copy.deepcopy(output_names or {})
        self.roles = {node: {self.input_ids.get(node, {}).get(key, key): value for key, value in sockets.items()}
                      for node, sockets in (roles or {}).items()}
        self.unknown, self.strict, self.private = unknown, strict, private
        self.links, self.unlinked, self.absent = [], {}, []
        self.invalidated = set()

    def _input(self, node, selector):
        return self.input_ids.get(node, {}).get(selector, selector)

    def _output(self, node, selector):
        return self.output_ids.get(node, {}).get(selector, selector)

    def _row(self, edit):
        return {"from_node": edit["from_node"], "from_socket": self._output(edit["from_node"], edit["from_socket"]),
                "to_node": edit["to_node"], "to_socket": self._input(edit["to_node"], edit["to_socket"])}

    def _same_input(self, link, edit):
        if link["to_node"] != edit["to_node"]:
            return False
        wanted = self._input(edit["to_node"], edit["to_socket"])
        if link["to_socket"] == wanted:
            return True
        proven = set(self.input_ids.get(edit["to_node"], {}).values())
        return False if link["to_socket"] in proven and wanted in proven else None

    def _selected(self, link, edit):
        if edit.get("from_node") is not None and link["from_node"] != edit["from_node"]:
            return False
        same = self._same_input(link, edit)
        if same is False:
            return False
        selector = edit.get("from_socket")
        if selector is None or selector == link["from_socket"]:
            return same
        name = self.output_names.get(link["from_node"], {}).get(link["from_socket"])
        if name is not None:
            return same if selector == name else False
        return None

    def related(self, name):
        result = {"links": [row for row in self.links if name in (row["from_node"], row["to_node"])]}
        unlinked = [row for (node, _socket), row in self.unlinked.items() if node == name]
        if unlinked:
            result["unlinked"] = unlinked
        negatives = [row for row in self.absent if name in (row.get("from_node"), row["to_node"])]
        if negatives:
            result[LINK_GUARD] = {"absent": negatives}
        return result

    def forget(self, name):
        self.links[:] = [row for row in self.links if name not in (row["from_node"], row["to_node"])]
        self.absent[:] = [row for row in self.absent if name not in (row.get("from_node"), row["to_node"])]
        for key in [key for key in self.unlinked if key[0] == name]:
            del self.unlinked[key]
        self.invalidate_bindings(name)
        self.invalidated.discard(name)

    def invalidate_bindings(self, name):
        for values in (self.roles, self.input_ids, self.output_ids, self.output_names):
            if name in values:
                self.invalidated.add(name)
            values.pop(name, None)

    def add(self, edit):
        row = self._row(edit)
        if self.invalidated.intersection((row["from_node"], row["to_node"])):
            self.unknown(edit, "socket_selector_alias", [{"links": [row]}])
        role = self.roles.get(row["to_node"], {}).get(row["to_socket"])
        if type(role) is not bool:
            role = None if self.strict else False
        kept = []
        for previous in self.links:
            same = self._same_input(previous, row)
            if same is False or same is True and role is True:
                kept.append(previous)
            elif same is None or role is None:
                reason = "socket_selector_alias" if same is None else "unknown_input_cardinality"
                self.unknown(edit, reason, [{"links": [previous]}])
        self.links[:] = kept
        if row not in self.links:
            self.links.append(row)
        self.unlinked.pop((row["to_node"], row["to_socket"]), None)
        negatives = []
        for absent in self.absent:
            selected = self._selected(row, absent)
            if selected is False:
                negatives.append(absent)
            elif selected is None:
                self.unknown(edit, "socket_selector_alias", [{LINK_GUARD: {"absent": [absent]}}])
        self.absent[:] = negatives

    def remove_selected(self, edit):
        kept = []
        for previous in self.links:
            selected = self._selected(previous, edit)
            if selected is False:
                kept.append(previous)
            elif selected is None:
                self.unknown(edit, "socket_selector_alias", [{"links": [previous]}])
        self.links[:] = kept

    def apply(self, edit):
        op = edit["op"]
        if op == "link":
            self.add(edit)
        elif op == "unlink":
            self.remove_selected(edit)
            node, socket = edit["to_node"], self._input(edit["to_node"], edit["to_socket"])
            if edit.get("from_node") is None and edit.get("from_socket") is None:
                self.unlinked[node, socket] = {"node": node, "socket": socket}
                self.absent[:] = [row for row in self.absent if self._same_input(row, edit) is not True]
            elif self.private:
                row = {key: edit[key] for key in ENDS if edit.get(key) is not None}
                row["to_socket"] = socket
                if row not in self.absent:
                    self.absent.append(row)
        elif op == "insert":
            self.remove_selected(edit)
            if edit["input"] is None or edit["output"] is None:
                self.unknown(edit, "automatic_insert_endpoints",
                             [{key: value for key, value in edit.items() if not key.startswith("_")}])
                return
            first = dict(edit, to_node=edit["node"], to_socket=edit["input"])
            second = dict(edit, from_node=edit["node"], from_socket=edit["output"])
            self.add(first)
            self.add(second)
        elif op == "detach":
            affected = self.related(edit["node"])
            if edit.get("reconnect"):
                if affected["links"]:
                    self.unknown(edit, "automatic_reconnection", [affected])
            elif self.private:
                for row in affected["links"]:
                    if row not in self.absent:
                        self.absent.append(copy.deepcopy(row))
            self.links[:] = [row for row in self.links if edit["node"] not in (row["from_node"], row["to_node"])]
