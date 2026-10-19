# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only material binding facts and request-local staged identity proofs.

No name grants identity: live facts use session UIDs, and new IDs receive a
private nonce only after the native operation created them. Nonces travel with
the private stage through suspension and are removed before live transfer.
"""

import hashlib
import json

from . import material_rules
from .program_jobs import ProgramError

from .program_material_provenance import (  # noqa: F401 (native/kernel compatibility facade)
    MARKER, REGISTRY, _register, logical_material_name, record_isolation, record_copy, matches_resource,
    begin_operation, finish_operation, snapshot_provenance, restore_provenance, guard_material,
    selected_material_report, strip_provenance, record_clone, initialize_clones,
)


def _charge(budget, count=1):
    budget[0] -= count
    if budget[0] < 0:
        raise ProgramError("Exact material binding exceeds the bounded fingerprint budget", code="LIMIT_EXCEEDED")


def _uid(block):
    return int(block.session_uid)


def _bsdf(tree, budget):
    """What ``material_principled`` edits in place (action_nodes._principled_in_place): the count of Principled BSDF
    nodes, and of the only one its name, the typed inputs a link drives and whether it reaches the surface."""
    from .action_node_types import _principled_inputs
    _charge(budget, len(tree.nodes) if tree is not None else 0)
    nodes = [node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"] if tree is not None else []
    if len(nodes) != 1:
        return {"count": len(nodes)}
    from .material_rules import driven, tree_reaches
    _charge(budget, len(tree.links))
    linked = sorted(name for name in _principled_inputs if nodes[0].inputs.get(name) is not None
                    and driven(nodes[0].inputs[name]))
    return {"count": 1, "name": nodes[0].name, "linked": linked, "shown": tree_reaches(tree, nodes[0])}


def _principled(tree, budget):
    """The proven scalar-reader role, never a guess from a socket's name."""
    if tree is None:
        return None
    nodes, outputs = [], []
    for node in tree.nodes:
        _charge(budget)
        if node.type == "BSDF_PRINCIPLED":
            nodes.append(node)
        if node.type == "OUTPUT_MATERIAL" and getattr(node, "is_active_output", True):
            outputs.append(node)
    if len(nodes) != 1 or len(outputs) != 1:
        return None
    node, output = nodes[0], outputs[0]
    surface = output.inputs.get("Surface")
    if surface is None or len(surface.links) != 1 or surface.links[0].from_node != node:
        return None
    if any(node.inputs.get(name) is None or node.inputs[name].is_linked
           for name in ("Base Color", "Roughness", "Metallic")):
        return None
    return node.name


def _material_holder(block):
    prop = block.bl_rna.properties.get("materials")
    return prop is not None and prop.type == "COLLECTION" and not prop.is_runtime


def _user_maps(blocks, budget):
    """Pre-admit Blender's user-map allocations, then request only selected IDs."""
    import bpy
    # user_map may scan Main internally. Account for that native traversal
    # before asking it to allocate sets; no whole-scene Python snapshot.
    main_count = sum(len(getattr(bpy.data, prop.identifier)) for prop in bpy.data.bl_rna.properties
                     if prop.type == "COLLECTION" and not prop.is_runtime)
    _charge(budget, main_count + sum(block.users for block in blocks))
    users = bpy.data.user_map(subset=set(blocks)) if blocks else {}
    holders = set()
    for entries in users.values():
        for user in entries:
            _charge(budget)
            if not isinstance(user, bpy.types.Object) and _material_holder(user):
                holders.add(user)
    if holders:
        _charge(budget, main_count + sum(holder.users for holder in holders))
    return users, bpy.data.user_map(subset=holders) if holders else {}


def _users(block, backend, budget, maps):
    """Bounded exact user identities, including users outside declared handles."""
    import bpy
    from .material_sharing import affected
    direct, through = maps
    users = affected(direct.get(block, ()))  # Kept users of this file only (material_sharing.affected).
    ids, objects, outside = [], set(), False
    for user in users:
        _charge(budget)
        ids.append(_uid(user))
        if isinstance(user, bpy.types.Object):
            candidates = (user,)
        elif _material_holder(user):
            candidates = affected(through.get(user, ()))
        else:
            outside = True
            continue
        for obj in candidates:
            _charge(budget)
            if not isinstance(obj, bpy.types.Object):
                outside = True
                continue
            objects.add(_uid(obj))
            if backend.scene.objects.get(obj.name) != obj:
                outside = True
    encoded = json.dumps([sorted(ids), sorted(objects), bool(block.use_fake_user)], separators=(",", ":"))
    return users, outside, hashlib.sha256(encoded.encode()).hexdigest()


def _scene_names(block, backend, budget, maps):
    """``({uid: name}, holders)``: the bound scene's objects that show ``block``, and the data that holds it while no
    object uses it (an orphan mesh with a fake user). An edit for all users names both."""
    import bpy
    from .material_sharing import MAX_NAMED_USERS, affected
    direct, through = maps
    named, holders = {}, []
    for user in affected(direct.get(block, ())):
        candidates = (user,) if isinstance(user, bpy.types.Object) else affected(through.get(user, ())) if _material_holder(user) else ()
        if _material_holder(user) and not any(isinstance(obj, bpy.types.Object) for obj in candidates):
            holders.append(user.name[:64])
        for obj in candidates:
            _charge(budget)
            if isinstance(obj, bpy.types.Object) and backend.scene.objects.get(obj.name) == obj:
                named[_uid(obj)] = obj.name[:64]
    return named, sorted(holders)[:MAX_NAMED_USERS]


def _socket_fact(tree, node_name, socket_name, budget, *, output=False):
    """Read one native SDNA socket flag; never an add-on getter or evaluated graph."""
    if tree is None:
        return None
    _charge(budget, len(tree.nodes))
    node = tree.nodes.get(node_name)
    if node is None:
        return None
    found = None
    sockets = node.outputs if output else node.inputs
    for socket in sockets:
        _charge(budget)
        if socket.identifier == socket_name:
            found = socket
            break
    if found is None:
        for socket in sockets:
            _charge(budget)
            if socket.name == socket_name and socket.enabled:
                found = socket
                break
    if found is None:
        return None
    if output:
        return found.identifier, found.name
    prop = found.bl_rna.properties.get("is_multi_input")
    if prop is None or prop.type != "BOOLEAN" or prop.is_runtime:
        raise ProgramError("Node binding requires the built-in stored socket multiplicity flag", code="UNSUPPORTED")
    # rna_node_socket.cc defines this as SOCK_MULTI_INPUT, not a getter;
    # bpy_rna.cc resolves this native RNA property before Python attributes.
    return found.identifier, bool(found.is_multi_input)


def _input_role(tree, node_name, socket_name, budget):
    observed = _socket_fact(tree, node_name, socket_name, budget)
    return observed[1] if observed is not None else None


def _node_inputs(backend, binding, facts, blocks, budget):
    roots = {}
    owners = {row["target"]: row for row in facts["owners"]}
    for operation in binding["program"]["operations"]:
        if operation["kind"] != "node_edit":
            continue
        params, target = operation["parameters"], operation["target"]
        destinations, sources = set(), set()
        for edit in params["edits"]:
            if edit["op"] in {"link", "unlink", "insert"}:
                destinations.add((edit["to_node"], edit["to_socket"]))
            if edit["op"] in {"link", "unlink", "insert"} and edit.get("from_node") and edit.get("from_socket"):
                sources.add((edit["from_node"], edit["from_socket"]))
            if edit["op"] == "insert":
                if edit.get("input"):
                    destinations.add((edit["node"], edit["input"]))
                if edit.get("output"):
                    sources.add((edit["node"], edit["output"]))
        if not destinations:
            continue
        tree, token = None, None
        obj = binding["originals"].get(target)
        if params["material"] is not None:
            for slot in owners.get(target, {}).get("slots", ()):
                _charge(budget)
                if slot["name"] == params["material"]:
                    token = slot["resource"]
                    break
            tree = blocks[token].node_tree if token is not None else None
        elif params["world"] and (binding.get("scene") or {}).get("handle") == target:
            tree = backend.scene.world.node_tree if backend.scene.world else None
        elif obj is not None and params["modifier"] is not None:
            _charge(budget, len(obj.modifiers))
            modifier = obj.modifiers.get(params["modifier"])
            tree = modifier.node_group if modifier and modifier.type == "NODES" else None
        if params["group"] is not None and tree is not None:
            _charge(budget, len(tree.nodes))
            node = tree.nodes.get(params["group"])
            tree = node.node_tree if node and node.bl_idname == "ShaderNodeGroup" else None
            token = facts["resources"].get(token, {}).get("groups", {}).get(params["group"])
        key = (target, params["material"], params["modifier"], params["world"], params["group"])
        row = roots.setdefault(key, {"target": target, **{name: params[name]
                               for name in ("material", "modifier", "world", "group")}, "inputs": {}})
        for output, selectors in ((False, destinations), (True, sources)):
            field = "output_identifiers" if output else "input_identifiers"
            for node, socket in sorted(selectors):
                observed = _socket_fact(tree, node, socket, budget, output=output)
                if observed is None:
                    continue  # Absent/new/changed sockets are unresolved, never guessed.
                identifier, multi = observed
                row.setdefault(field, {}).setdefault(node, {})[socket] = identifier
                if output:
                    row.setdefault("output_names", {}).setdefault(node, {})[identifier] = multi
                else:
                    row["inputs"].setdefault(node, {})[socket] = multi
                if token is not None:
                    resource = facts["resources"][token]
                    resource.setdefault(field, {}).setdefault(node, {})[socket] = identifier
                    if output:
                        resource.setdefault("output_names", {}).setdefault(node, {})[identifier] = multi
                    else:
                        resource.setdefault("inputs", {}).setdefault(node, {})[socket] = multi
    if roots:
        facts["node_roots"] = list(roots.values())


def _diagnostic_sources(operations, handles):
    """Initial owners whose material lookup may need available-name diagnostics."""
    from . import batch_spec
    from .material_rules import KINDS
    origins, requested, batches = {target: target for target in handles}, {}, 0
    changed = {}
    for operation in operations:
        kind, params = operation["kind"], operation["parameters"]
        if params.get("alias"):
            source = params.get("source") if kind in {"object_duplicate", "object_convert"} else None
            alias = params["alias"]
            alias = alias if alias.startswith("$") else "$" + alias
            origins[alias], changed[alias] = origins.get(source), changed.get(source, False)
        if kind == "batch":
            batches += 1
            for part, source in enumerate(params["sources"]):
                for alias in batch_spec.copy_aliases(source, batches, part, batch_spec.instance_count(params)):
                    origins[alias], changed[alias] = origins.get(source), changed.get(source, False)
        reference = (params.get("source") if kind == "material_assign" else
                     operation["target"] if kind == "node_edit" and params["material"] is not None else None)
        if reference is not None:
            source = origins.get(reference)
            if source is not None:
                entry = requested.setdefault(source, {"names": set(), "changed": False, "full": False})
                entry["names"].add(params["material"])
                entry["changed"] |= changed.get(reference, False)
                entry["full"] |= kind == "node_edit"
        if kind in KINDS:
            for target in (operation["target"], *params.get("also", ())):
                changed[target] = True
    return requested


class _DiagnosticNames:
    """Bound provisional slot names before retention, without a second RNA walk."""

    def __init__(self, requested, total, *, changed=False, full=False):
        from .program_checks import MAX_CHECK_BYTES, MAX_CHECK_LEAVES
        self.missing, self.names, self.total = set(requested), [], total
        self.changed = changed
        self.full = full  # node_edit sorts full names before truncating its displayed first eight.
        self.bytes = self.leaves = 0
        self.max_bytes, self.max_leaves, self.overflow = MAX_CHECK_BYTES, MAX_CHECK_LEAVES, False

    def observe(self, slot, name):
        if not self.missing and not self.changed or name is None:
            return
        self.missing.discard(name)
        if not self.missing and not self.changed:
            self.total[0] -= self.bytes
            self.total[1] -= self.leaves
            self.names.clear()
            return
        if self.overflow:
            return
        pair = [slot, name if self.full else name[:64]]
        size = len(json.dumps(pair, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if self.total[0] + size > self.max_bytes or self.total[1] + 2 > self.max_leaves:
            self.overflow = True  # A later matching slot can still make these facts unnecessary.
            return
        self.total[0] += size
        self.total[1] += 2
        self.bytes += size
        self.leaves += 2
        self.names.append(pair)

    def retain(self, owner):
        if self.missing or self.changed:
            if self.overflow:
                from .program_checks import _refuse
                _refuse("material lookup diagnostics exceed the shared byte or scalar allowance")
            owner["diagnostic_names"] = self.names


def collect_material_facts(backend, binding, budget=None):
    """Collect only queried names, selected group identities and scalar roles.

    Slot traversal is charged, never truncated or capped at the placement
    limit. The caller may share the existing 2M fingerprint item allowance.
    Initially missing names and owners with preceding material placements
    conservatively retain diagnostic slot facts under the semantic budget,
    even when later creation/reuse ultimately makes the assignment valid.
    """
    from .program_dependencies import fingerprint_budget
    budget = fingerprint_budget() if budget is None else budget
    operations = binding["program"]["operations"]
    diagnostics, diagnostic_size = _diagnostic_sources(operations, binding["handles"]), [0, 0]
    names = {op["parameters"].get("material", op["parameters"].get("name")) for op in operations
             if op["kind"] in {"material_principled", "shader_nodes", "material_assign", "node_edit"}}
    names.discard(None)
    groups = {}
    copies = {op["parameters"]["copy_as"]: op["parameters"]["material"] for op in operations
              if op["kind"] == "material_assign" and op["parameters"]["copy_as"] is not None}
    for operation in operations:
        params = operation["parameters"]
        if operation["kind"] == "node_edit" and params.get("material") and params.get("group"):
            name, seen = params["material"], set()
            while name not in seen:
                groups.setdefault(name, set()).add(params["group"])
                seen.add(name)
                if name not in copies:
                    break
                name = copies[name]
    facts, blocks = {"owners": [], "resources": {}}, {}
    # A material_principled that states inputs may edit a held material, or a copy of one, in place: bind each
    # material's Principled BSDF facts (program_material_obligations models the edit from them).
    principled_edits = any(op["kind"] == "material_principled" and op["parameters"]["inputs"] for op in operations)
    for target in binding["handles"]:
        obj = binding["originals"][target]
        slots = []
        requested = diagnostics.get(target)
        diagnostic = (_DiagnosticNames(requested["names"], diagnostic_size, changed=requested["changed"],
                                       full=requested["full"])
                      if requested is not None else None)
        for index, slot in enumerate(obj.material_slots if names else ()):
            _charge(budget)
            material = slot.material
            name = material.name if material is not None else None
            if diagnostic is not None:
                diagnostic.observe(index, name)
            if name not in names:
                continue
            token = "live:%d" % _uid(material)
            slots.append({"slot": index, "resource": token, "name": name, "link": slot.link})
            if token not in blocks:
                blocks[token] = material
                selected_groups = {}
                tree = material.node_tree
                selected = groups.get(name, ())
                if tree is not None and selected:
                    for node in tree.nodes:
                        _charge(budget)
                        if node.name not in selected or node.bl_idname != "ShaderNodeGroup":
                            continue
                        group = node.node_tree
                        if group is not None:
                            group_token = "group:%d" % _uid(group)
                            selected_groups[node.name] = group_token
                            blocks[group_token] = group
                facts["resources"][token] = {"uid": _uid(material), "name": name,
                    "principled": _principled(tree, budget), "groups": selected_groups, "sharing": {},
                    "animated": material_rules.animated(material, tree)}
                if principled_edits:
                    facts["resources"][token]["bsdf"] = _bsdf(tree, budget)
        owner = {"target": target, "slot_count": len(obj.material_slots), "slots": slots}
        if diagnostic is not None:
            diagnostic.retain(owner)
        facts["owners"].append(owner)
    owner_resources = {}
    for owner in facts["owners"]:
        held, roots, data_linked = set(), {}, set()
        for slot in owner["slots"]:
            _charge(budget)
            token = slot["resource"]
            held.add(token)
            if slot["link"] == "DATA":
                data_linked.add(token)
            for group in facts["resources"][token].get("groups", {}).values():
                _charge(budget)
                roots[group] = blocks[token]  # Match prepare's last candidate for this owner/group.
        owner_resources[owner["target"]] = held, roots, data_linked
    maps = _user_maps(blocks.values(), budget) if blocks else ({}, {})
    group_counts = {}
    # (target, material name) of every edit for all users: its approval names the other objects it changes.
    from .material_sharing import ALL_USERS, MAX_NAMED_USERS, shared_data as shared
    everyone = {(operation["target"], operation["parameters"].get("material", operation["parameters"].get("name")))
                for operation in operations if operation["parameters"].get("shared") == ALL_USERS}
    for token, block in blocks.items():
        entry = facts["resources"].setdefault(token, {"uid": _uid(block), "name": block.name, "sharing": {}})
        users, outside, digest = _users(block, backend, budget, maps)
        named = None
        for owner in facts["owners"]:
            _charge(budget)
            target = owner["target"]
            obj = binding["originals"][target]
            held, roots, data_linked = owner_resources[target]
            root = roots.get(token)
            if token not in held and root is None:
                continue
            shared_data = shared(obj.data)
            allowed = [root] if root is not None else [obj] + ([obj.data] if obj.data is not None and not shared_data else [])
            # Blender's user_map values are sets, already traversed/charged in
            # _users; exclude only this owner's one or two permitted users.
            _charge(budget, len(allowed))
            count = len(users) - sum(item in users for item in allowed)
            count += int(bool(block.use_fake_user))
            if root is not None:
                pointer = root.as_pointer()
                if pointer not in group_counts:
                    counts = group_counts[pointer] = {}
                    for node in root.node_tree.nodes:
                        _charge(budget)
                        group = node.node_tree if node.bl_idname == "ShaderNodeGroup" else None
                        if group is not None:
                            counts[group.as_pointer()] = counts.get(group.as_pointer(), 0) + 1
                count += int(group_counts[pointer].get(block.as_pointer(), 0) > 1)
            entry["sharing"][target] = {"others": count, "outside": outside, "users_digest": digest,
                "data_shared": bool(shared_data and token in data_linked)}
            if count and (target, entry["name"]) in everyone:
                named = _scene_names(block, backend, budget, maps) if named is None else named
                others = sorted(name for uid, name in named[0].items() if uid != _uid(obj))
                entry["sharing"][target].update(other_users=others[:MAX_NAMED_USERS], other_count=len(others))
                if named[1]:
                    entry["sharing"][target]["holders"] = named[1]
    _node_inputs(backend, binding, facts, blocks, budget)
    return facts


def resolve_binding(backend, binding, budget=None):
    """Resolve independently from this exact native binding, with a lazy pure path."""
    from .program_checks import needs_material_binding, resolve_obligations
    program = binding["program"]
    scene = binding.get("scene")
    if scene is not None:
        from .node_edit_spec import NodeEditError
        for operation in program["operations"]:
            if (operation["kind"] == "node_edit" and not operation["parameters"]["world"]
                    and operation["target"] == scene["handle"]):
                raise NodeEditError("OWNER_TYPE_MISMATCH", "material and modifier trees need an object handle as target")
    facts = collect_material_facts(backend, binding, budget) if needs_material_binding(program) else None
    from .material_sharing import PROPERTY_FACTS, property_facts, property_operations, refuse_linked, refuse_overrides
    refuse_overrides(binding)
    # property_set's in-place material writes and their users; a library material is refused before approval.
    shared = property_facts(binding, backend.scene) if property_operations(program) else []
    refuse_linked(program, shared)
    if shared:
        facts = dict(facts or {}, **{PROPERTY_FACTS: shared})
    return resolve_obligations(program, facts)


def binding_change_message(previous, current):
    """Describe only what immutable user digests prove; never invent a changed user's name."""
    from .material_sharing import PROPERTY_FACTS, property_changes
    changed = property_changes(previous.facts().get(PROPERTY_FACTS, []), current.facts().get(PROPERTY_FACTS, []))
    if changed is not None:
        return ("The bound material verification changed: users of material %r changed; inspect its current "
                "users, review the program, and stage it again" % changed[:64])
    before, after = previous.facts().get("resources", {}), current.facts().get("resources", {})
    for token, old in before.items():
        if not token.startswith("live:") or token not in after:
            continue
        sharing = after[token].get("sharing", {})
        for owner, facts in old.get("sharing", {}).items():
            prior, fresh = facts.get("users_digest"), sharing.get(owner, {}).get("users_digest")
            if prior is not None and fresh is not None and prior != fresh:
                return ("The bound material verification changed: users of material %r changed; inspect its current "
                        "users, review the program, and stage it again" % old["name"][:64])
    return "The bound material verification changed; stage a fresh program"
