# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Final material and node facts, replayed from private binding evidence.

Names select slots using the kernel's lookup order; only resource tokens establish
identity. Native binding supplies live tokens. Creations, copies and isolation
fork distinct tokens. This module never reads Blender or accepts public authority.
"""

import copy

from . import batch_spec, material_rules, node_edit_spec
from .refusal_codes import RefusalError
from .program_material_sharing_replay import SharingReplay
from .derived_checks import DEFAULT_MATERIAL_NAME, DEFAULT_SHADER_NAME

GUARD = "__host_material"
_MATERIAL_KINDS = material_rules.KINDS
_DEFAULT_NAMES = {"material_principled": DEFAULT_MATERIAL_NAME, "shader_nodes": DEFAULT_SHADER_NAME}
_MATERIAL_CHECKS = frozenset({"material_assignment", "material_values", "node_graph"})


class NeedsMaterialBinding(RefusalError):
    """The public program cannot determine an existing material's identity.

    Control flow at admission (``initial_obligations`` binds natively first); escaping anywhere else it is an
    internal fault, classified so (it still raises the recovery barrier)."""

    def __init__(self, message):
        super().__init__(message, code="INTERNAL_ERROR")


def needs_material_binding(program):
    """A cheap gate: programs without relevant existing owners do no native scan."""
    dependent, batches = {}, 0
    for operation in program["operations"]:
        kind, params = operation["kind"], operation["parameters"]
        if params.get("alias"):
            source = params.get("source") if kind in {"object_duplicate", "object_convert"} else None
            dependent[_name_token(params["alias"])] = bool(source and (not source.startswith("$") or dependent.get(source)))
        if kind == "batch":
            batches += 1
            for part, source in enumerate(params["sources"]):
                for alias in batch_spec.copy_aliases(source, batches, part, batch_spec.instance_count(params)):
                    dependent[alias] = not source.startswith("$") or dependent.get(source, False)
        if kind in _MATERIAL_KINDS or kind == "node_edit" and params["material"] is not None:
            references = [operation["target"], params.get("source"), *params.get("also", [])]
            if any(reference and (not reference.startswith("$") or dependent.get(reference, False)) for reference in references):
                return True
            if kind == "material_assign" and params["source"] is None and (program.get("read_set") or program.get("write_set")):
                return True  # Its fallback searches every staged existing owner.
        if kind == "node_edit" and any(edit["op"] in {"link", "unlink", "insert"} for edit in params["edits"]):
            return True  # Socket cardinality is bound for material, modifier and World roots.
        if kind == "property_set" and any(_writes_material(path) for path in params["values"]):
            reference = operation["target"]  # A live material written in place: its users are bound (material_sharing).
            if reference and (not reference.startswith("$") or dependent.get(reference, False)):
                return True
    return False


def _writes_material(path):
    from .rna_rules import family
    return family(path) == "material"


def _error(detail):
    raise RefusalError("PROGRAM_MATERIAL_STATE: " + detail, code="INTERNAL_ERROR")


def _name_token(alias):
    return alias if alias.startswith("$") else "$" + alias


# One comparator for a restated material with program_rules and the kernel, so the three never disagree.
_definition = material_rules.definition
class _Replay(SharingReplay):
    def __init__(self, program, facts):
        self.program = program
        self.node_inputs = {(row["target"], node_edit_spec.tree_key(row)): row
                            for row in facts.get("node_roots", [])}
        self.has_material = any(operation["kind"] in _MATERIAL_KINDS or operation["kind"] == "node_edit"
                                and operation["parameters"]["material"] is not None for operation in program["operations"])
        self.resources = copy.deepcopy(facts.get("resources", {}))
        for resource in self.resources.values():
            resource.update(history=[], values=False, edited=False)
        self.owners = {}
        self.bound = {owner["target"]: {entry["resource"] for entry in owner["slots"]} for owner in facts.get("owners", [])}
        self.assigned = set()  # Tokens a material_assign or an also gave to more objects (material_sharing.assigned).
        for owner in facts.get("owners", []):
            self.owners[owner["target"]] = {
                "count": owner["slot_count"],
                "slots": {entry["slot"]: entry["resource"] for entry in owner["slots"]},
            }
            if "diagnostic_names" in owner:
                self.owners[owner["target"]].update(
                    diagnostic_names=tuple(tuple(pair) for pair in owner["diagnostic_names"]),
                    diagnostic_replacements={})
        self.defined, self.assignments, self.graphs = {}, {}, {}
        # (target, token) of the operations that put faces on a material, in order (a repeat is the most recent):
        # an assignment, a statement that creates or reuses one, a name-only keep, an also object, and an in-place
        # edit given a face field. An in-place edit without one moves no face (action_nodes), like a node edit.
        self.placed = {}
        self.values, self.effects, self.other_graphs = {}, [], {}
        self.graph_owners = {}
        self.unknowns = []
        self.batches = 0
        self.linked = {}  # A linked copy's reference -> the object whose data it shares (program_rules' groups).

    def owner(self, target):
        if target not in self.owners:
            if target.startswith("$"):
                _error("unknown created material owner " + target)
            raise NeedsMaterialBinding("Existing material slots need authoritative binding: " + target)
        return self.owners[target]

    def named(self, target, name, *, edited_first=False):
        slots = self.owner(target)["slots"]
        matches = [(slot, token) for slot, token in sorted(slots.items())
                   if self.resources[token]["name"] == name]
        if edited_first:
            matches.sort(key=lambda item: not self.resources[item[1]].get("edited", False))
        return matches[0] if matches else (None, None)

    def held(self, target, token):
        return [slot for slot, value in sorted(self.owner(target)["slots"].items()) if value == token]

    def place(self, target, token, slot=-1):
        owner = self.owner(target)
        held = self.held(target, token)
        if slot == -1 and not held and getattr(self, "_appends", None) is not None:
            self._appends[target] = owner["count"]   # The native op appends at the count it finds (provenance).
        slot = (held[0] if held else owner["count"]) if slot == -1 else slot
        if slot > owner["count"] or slot >= 32:
            raise RefusalError("Material slot must be existing or the next append slot, with at most 32 slots",
                               code="VALIDATION")
        owner["slots"][slot] = token
        if "diagnostic_names" in owner:
            owner["diagnostic_replacements"] = dict(owner["diagnostic_replacements"])
            owner["diagnostic_replacements"][slot] = self.resources[token]["name"]
        owner["count"] = max(owner["count"], slot + 1)
        return slot

    def fork(self, target, token, index, prefix="isolate"):
        fresh = "%s:%d" % (prefix, index)
        self.resources[fresh] = copy.deepcopy(self.resources[token])
        self.resources[fresh].update(uid=None, sharing={}, edited=True)
        owner = self.owner(target)
        owner["slots"] = {slot: fresh if value == token else value for slot, value in owner["slots"].items()}
        if token in self.values:
            self.values[fresh] = target
        return fresh

    def edit_resource(self, target, token, params, index):
        resource = self.resources[token]
        sharing = resource.get("sharing", {}).get(target, {})
        elsewhere = any(owner != target and token in state["slots"].values() for owner, state in self.owners.items())
        policy = params["shared"]
        # This describes the intended state, not kernel eligibility. Actual
        # staged ownership, assignment, animation and shared-data guards keep
        # their native ordering; only a successful checked job can certify it.
        if policy == "ISOLATE" and (sharing.get("others", 0) or elsewhere):
            return self.fork(target, token, index)
        return token

    def edit_principled(self, resource, operation):
        """Stated inputs set on a held material's one Principled BSDF, the rest kept, refused as the kernel does."""
        stated = material_rules.principled_inputs(operation["parameters"])
        bsdf, history = resource.get("bsdf"), resource.get("history", [])
        if bsdf is None:  # Binding states it for every material a program may edit so (program_material_facts._bsdf).
            _error("no bound Principled BSDF facts for %r" % resource["name"][:64])
        since = history[resource.get("bsdf_at", 0):]
        if since and (bsdf["count"] != 1 or not all(material_rules.keeps_bsdf(edit, bsdf["name"]) for edit in since)):
            raise RefusalError("material_principled PRINCIPLED_UNKNOWN: an earlier node_edit of %r in this program may "
                               "change its Principled BSDF; set those inputs in that node_edit (op set) or a "
                               "reviewed script instead"
                               % operation["parameters"]["name"][:64], code="VALIDATION")
        wired = all(material_rules.keeps_wiring(edit) for edit in since)  # Else the kernel reads the staged wiring (same refusals).
        refusal = material_rules.in_place_refusal(operation, bsdf["count"], sorted(set(bsdf.get("linked", ())) & set(stated))
                                                  if wired else [], bsdf.get("shown") if wired else None)
        if refusal is not None:
            raise refusal
        resource.update(values=True, edited=True, principled=bsdf["name"],
                        history=history + [{"op": "set", "node": bsdf["name"], "settings": {}, "inputs": stated,
                                            "outputs": {}, "image": None}])

    def find_assignment(self, target, params):
        if params["source"] is not None:
            return self.named(params["source"], params["material"])[1]
        token = self.named(target, params["material"])[1]
        if token is None:
            token = self.defined.get(params["material"])
        if token is None:
            token = next((found for owner in self.owners
                          if (found := self.named(owner, params["material"])[1]) is not None), None)
        return token

    def material_names(self, target):
        owner = self.owner(target)
        if "diagnostic_names" in owner:
            replaced = owner["diagnostic_replacements"]
            return ({name for slot, name in owner["diagnostic_names"] if slot not in replaced}
                    | set(replaced.values()))
        return {self.resources[token]["name"] for token in owner["slots"].values()}

    def material(self, index, operation):
        params, target, kind = operation["parameters"], operation["target"], operation["kind"]
        source, mode, places = None, "reuse", True
        self._appends = appends = {}   # target -> slot count it planned an appended slot at (place)
        chosen = kind == "material_assign" or params["name"] != _DEFAULT_NAMES[kind]
        if kind == "material_assign":
            source = token = self.find_assignment(target, params)
            if token is None:
                name = params["material"]
                if params["source"] is not None:
                    names = sorted({name[:64] for name in self.material_names(params["source"])})
                    listed = ", ".join(repr(item) for item in names[:8]) or "none"
                    raise RefusalError("material_assign MATERIAL_NOT_FOUND: the source has no material named %r; its "
                                       "materials: %s" % (name[:64], listed), code="VALIDATION")
                raise material_rules.not_in_program(name)
            if params["copy_as"] is not None:
                token, mode = "copy:%d" % index, "copy"
                self.resources[token] = copy.deepcopy(self.resources[source])
                self.resources[token].update(name=params["copy_as"], uid=None, sharing={}, edited=False)
                if source in self.values:
                    self.values[token] = target
            else:
                self.assigned.add(token)
            slot = self.place(target, token, params["slot"])
        else:
            slot, token = self.named(target, params["name"]) if chosen else (None, None)
            existing = token is not None
            if existing and material_rules.names_only(operation):  # Kept as the object holds it (action_nodes._keep).
                source, held = token, self.held(target, token)
                if params["slot"] != -1 and params["slot"] not in held:
                    raise RefusalError("%s: %r is already in slot %s of this object; use slot -1 or %s"
                                       % (kind, params["name"][:64], held[0], held[0]), code="VALIDATION")
                slot = held[0] if params["slot"] == -1 else params["slot"]
            elif existing:
                source = token
                self.shared_in_program(target, token, params, lambda code, detail: RefusalError(
                    "%s %s: %s" % (kind, code, detail), code=node_edit_spec.NodeEditError(code, detail).refusal_code))
                token = self.edit_resource(target, token, params, index)
                self.note_edit(token, params)
                mode = "isolate" if token != source else "edit"
                places = bool(params.get("face_indices")) or params.get("face_selector") is not None
                if params["also"]:
                    raise RefusalError("%s ALSO_NEW_MATERIAL: also assigns a material this operation creates; "
                                       "material_assign gives an existing material to more objects" % kind,
                                       code="VALIDATION")
                held = self.held(target, token)
                if params["slot"] != -1 and params["slot"] not in held:
                    raise RefusalError("%s: %r is already in slot %s of this object and is edited in place; use "
                                       "slot -1 or %s" % (kind, params["name"][:64], held[0], held[0]),
                                       code="VALIDATION")
                slot = held[0] if params["slot"] == -1 else params["slot"]
            elif chosen and params["name"] in self.defined:
                token = self.defined[params["name"]]
                if not material_rules.agrees(self.resources[token]["definition"], operation):
                    raise RefusalError("%s MATERIAL_REDEFINED: %r was stated earlier in this program with other "
                                       "values; state a material once and reuse it with material_assign, or choose "
                                       "another name" % (kind, params["name"][:64]), code="VALIDATION")
                slot = self.place(target, token, params["slot"])
            else:
                token, mode = "created:%d" % index, "create"
                self.resources[token] = {"name": params["name"], "uid": None, "sharing": {}, "groups": {},
                                         "history": [], "values": False, "edited": False}
                slot = self.place(target, token, params["slot"])
            resource = self.resources[token]
            if mode != "reuse" and existing and kind == "material_principled":
                self.edit_principled(resource, operation)
            elif mode != "reuse":
                resource.update(history=[], values=kind == "material_principled", groups={}, edited=existing,
                                principled="Principled BSDF" if kind == "material_principled" else None,
                                bsdf={"count": 1, "name": "Principled BSDF", "linked": [], "shown": True}
                                if kind == "material_principled" else material_rules.graph_bsdf(params), bsdf_at=0)
                if kind == "material_principled":
                    resource["history"] = material_rules.principled_history(params)
                    resource["bsdf_at"] = len(resource["history"])  # Its facts hold after these steps.
                    resource["inputs"] = {"Principled BSDF": {name: False for name in material_rules.principled_inputs(params)},
                                          "Material Output": {"Surface": False}}
                    resource["input_identifiers"] = {name: {key: key for key in sockets}
                                                     for name, sockets in resource["inputs"].items()}
                    resource["output_identifiers"] = {"Principled BSDF": {"BSDF": "BSDF"}}
                    resource["output_names"] = {"Principled BSDF": {"BSDF": "BSDF"}}
            if mode != "reuse":  # Merged into what the program stated of a held material (material_rules.definition).
                resource["definition"] = _definition(operation, resource.get("definition") if existing else None, existing)
                if chosen:
                    self.defined[params["name"]] = token
        effect = {"operation": index, "target": target, "slot": slot, "resource": token, "source": source, "mode": mode}
        self.effects.append(effect)
        if chosen:
            self.assignments[target, token] = None
            if places:
                self.placed.pop((target, token), None)
                self.placed[target, token] = None
            if kind == "material_principled" and mode != "reuse":
                self.values[token] = target
        if params["also"]:
            self.assigned.add(token)
        for other in params["also"]:
            self.place(other, token)
            if chosen:
                self.assignments[other, token] = None
                self.placed.pop((other, token), None)
                self.placed[other, token] = None
        self._appends = None
        if appends:   # Checked before the operation runs (program_material_provenance._appended_where_planned).
            effect["append"] = appends

    def node(self, index, operation):
        target, params = operation["target"], operation["parameters"]
        edits = [dict(copy.deepcopy(edit), _operation=index, _step=step, _target=target)
                 for step, edit in enumerate(params["edits"])]
        if params["material"] is None:
            key = (target, node_edit_spec.tree_key(params))
            previous = self.other_graphs.setdefault(key, {**copy.deepcopy(params), "edits": []})
            previous["edits"].extend(edits)
            return
        slot, source = self.named(target, params["material"], edited_first=True)
        if source is None:
            if target.startswith("$"):
                raise node_edit_spec.NodeEditError("NEW_OBJECT", "build a new object's graph with shader_nodes or "
                    "geometry_nodes, or edit it in a later program")
            names = sorted(self.material_names(target))
            raise node_edit_spec.NodeEditError("MATERIAL_NOT_FOUND", "no material named %r on this object; its materials: %s"
                % (params["material"][:64], ", ".join(repr(name[:64]) for name in names[:8]) or "none"))
        self.shared_in_program(target, source, params, node_edit_spec.NodeEditError)
        token = self.edit_resource(target, source, params, index)
        self.note_edit(token, params)
        root = self.resources[token]
        tree_source = tree = None
        if params["group"] is not None:
            tree_source = tree = root.get("groups", {}).get(params["group"])
            if tree is None or tree not in self.resources:
                raise node_edit_spec.NodeEditError("GROUP_NOT_FOUND", "no group node with a group is named %r"
                                                   % params["group"][:64])
            shared = self.resources[tree].get("sharing", {}).get(target, {}).get("others", 0)
            if params["shared"] == "ISOLATE" and (shared or token != source):
                tree = "group-isolate:%d" % index
                self.resources[tree] = copy.deepcopy(self.resources[tree_source])
                self.resources[tree].update(uid=None, sharing={})
                root["groups"][params["group"]] = tree
        state = self.resources[tree or token]
        state.setdefault("history", []).extend(edits)
        root["edited"] = True
        self.graphs[tree or token] = (target, token, params["group"])
        self.graph_owners.setdefault(token, {})[target] = None
        effect = {"operation": index, "target": target, "slot": slot, "resource": token, "source": source,
                  "mode": "isolate" if token != source else "edit"}
        if tree is not None:
            effect.update(tree_resource=tree, tree_source=tree_source)
        self.effects.append(effect)

    def copy_owner(self, source):
        owner = self.owners.get(source, {"count": 0, "slots": {}})
        result = {"count": owner["count"], "slots": dict(owner["slots"])}
        if "diagnostic_names" in owner:
            # Share initial facts without traversal and the replacement overlay
            # without copying; place() gives each changed owner its own overlay.
            result.update(diagnostic_names=owner["diagnostic_names"],
                          diagnostic_replacements=owner["diagnostic_replacements"])
        return result

    def objects(self, operation):
        kind, params = operation["kind"], operation["parameters"]
        if not self.has_material:
            if kind == "object_delete":
                self.other_graphs = {key: value for key, value in self.other_graphs.items()
                                     if key[0] != operation["target"]}
            return
        alias = params.get("alias")
        if alias:
            reference = _name_token(alias)
            source = params.get("source") if kind in {"object_duplicate", "object_convert"} else None
            self.owners[reference] = self.copy_owner(source)
            if kind == "object_duplicate" and params.get("linked"):
                self.linked[reference] = source
        if kind == "batch":
            self.batches += 1
            for part, source in enumerate(params["sources"]):
                for reference in batch_spec.copy_aliases(source, self.batches, part, batch_spec.instance_count(params)):
                    self.owners[reference] = self.copy_owner(source)
                    if params.get("linked"):
                        self.linked[reference] = source
        if kind == "object_join" and operation["target"] in self.owners:
            self.join(operation["target"], params["sources"])
        removed = [operation["target"]] if kind == "object_delete" else (
            params["sources"] if kind == "object_join" else
            [params["source"]] if kind == "object_convert" and not params["keep_original"] else [])
        for reference in removed:
            self.owners.pop(reference, None)

    def surviving(self, preferred, token):
        if preferred in self.owners and self.held(preferred, token):
            return preferred
        return next((target for target, owner in self.owners.items() if token in owner["slots"].values()), None)

    def guard(self, check, token, tree=None):
        target = check["target"]
        slot = self.held(target, token)[0]
        return dict(check, **{GUARD: {"resource": token, "slot": slot, **({"tree_resource": tree} if tree else {})}})

    def graph(self, target, token, group=None, tree=None):
        resource = self.resources[tree or token]
        params = {"material": self.resources[token]["name"], "modifier": None, "world": False, "group": group,
                  "edits": resource.get("history", [])}
        check = node_edit_spec.derived_check(target, params, input_roles=resource.get("inputs"), strict_roles=True,
                                            unknowns=self.unknowns, **{key: resource.get(key) for key in
                                                ("input_identifiers", "output_identifiers", "output_names")})
        return self.guard(check, token, tree) if check else None

    def result(self):
        checks = []
        # The material an object's most recent placement put faces on must be rendered (material_faces); one a later
        # placement superseded, an in-place edit and every other guarded check (a node edit) read the slots.
        last = {target: token for target, token in self.placed}
        for target, token in self.assignments:
            if target in self.owners and self.held(target, token):
                check = self.guard({"kind": "material_assignment", "target": target,
                                    "material": self.resources[token]["name"], "all_faces": False}, token)
                if last.get(target) == token:
                    check[GUARD]["rendered"] = True
                checks.append(check)
        emitted = set()
        for token, preferred in self.values.items():
            target = self.surviving(preferred, token)
            if target is None:
                continue
            resource = self.resources[token]
            graph = self.graph(target, token)
            if not graph:
                continue
            node = next((entry for entry in graph["nodes"] if entry["name"] == resource.get("principled")), None)
            inputs = node.get("inputs", {}) if node else {}
            scalar = {"base_color": "Base Color", "roughness": "Roughness", "metallic": "Metallic"}
            linked_scalar = any(link.get("to_node") == resource.get("principled") and
                                link.get("to_socket") in scalar.values() for link in graph["links"])
            simple = (sum(entry.get("type") == "ShaderNodeBsdfPrincipled" for entry in graph["nodes"]) == 1
                      and sum(entry.get("type") == "ShaderNodeOutputMaterial" for entry in graph["nodes"]) == 1
                      and not any(edit["op"] == "ungroup" for edit in resource.get("history", [])))
            output = next((entry["name"] for entry in graph["nodes"]
                           if entry.get("type") == "ShaderNodeOutputMaterial"), None)
            surface = [link for link in graph["links"] if link["to_node"] == output and link["to_socket"] == "Surface"]
            simple = simple and len(surface) == 1 and surface[0]["from_node"] == resource.get("principled")
            if resource.get("values") and simple and not linked_scalar and any(name in inputs for name in scalar.values()):
                checks.append(self.guard({"kind": "material_values", "target": target, "material": resource["name"],
                                          **{key: inputs[name] for key, name in scalar.items() if name in inputs},
                                          **({"tolerance": graph["tolerance"]} if "tolerance" in graph else {})},
                                         token))
            else:
                checks.append(graph)
                emitted.add(token)
        for tree, (preferred, token, group) in self.graphs.items():
            if tree in emitted:
                continue
            target = self.surviving(preferred, token)
            if target is None:
                continue
            if group is not None and self.resources[token].get("groups", {}).get(group) != tree:
                continue
            graph = self.graph(target, token, group, tree if group else None)
            if graph:
                checks.append(graph)
        for (target, _key), params in self.other_graphs.items():
            metadata = self.node_inputs.get((target, _key), {})
            check = node_edit_spec.derived_check(target, params, input_roles=metadata.get("inputs"),
                                                strict_roles=True, unknowns=self.unknowns,
                                                **{key: metadata.get(key) for key in
                                                   ("input_identifiers", "output_identifiers", "output_names")})
            if check:
                checks.append(check)
        covered = {(check["target"], check[GUARD]["resource"]) for check in checks if GUARD in check}
        for token, owners in self.graph_owners.items():
            for target in owners:
                if (target, token) not in covered and target in self.owners and self.held(target, token):
                    checks.append(self.guard({"kind": "material_assignment", "target": target,
                                              "material": self.resources[token]["name"], "all_faces": False}, token))
        return checks


def material_checks(program, facts=None):
    """Complete unsplit material/node conjunctions and native provenance effects."""
    if needs_material_binding(program) and facts is None:
        raise NeedsMaterialBinding("Program material obligations need authoritative native binding facts")
    replay = _Replay(program, facts or {})
    for index, operation in enumerate(program["operations"]):
        replay.objects(operation)
        if operation["kind"] in _MATERIAL_KINDS:
            replay.material(index, operation)
        elif operation["kind"] == "node_edit":
            replay.node(index, operation)
    checks = replay.result()
    return checks, replay.effects, replay.unknowns


def replaces(check):
    """Checks owned by this resource replay, rather than target-only derivation."""
    return check["kind"] in _MATERIAL_CHECKS
