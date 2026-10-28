# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual staged ID lineage across native copies, operations and suspension."""

import uuid

from .program_jobs import ProgramError

MARKER = "_angelo_obligation_resource"
REGISTRY = "obligation_resources"
CLONE_MARKER = "_angelo_obligation_clone"
CLONES = "obligation_clones"


def _uid(block):
    return int(block.session_uid)


def _charge(budget, count=1):
    budget[0] -= count
    if budget[0] < 0:
        raise ProgramError("Staged material provenance exceeds the bounded fingerprint budget", code="LIMIT_EXCEEDED")


def record_clone(stage, source, private, prefix):
    """Called only at the trusted native copy output, before exposing the stage."""
    bundle = stage.binding.get("obligations")
    if bundle is None or not bundle.requires_binding:
        return
    resource = "%s:%d" % (prefix, _uid(source))
    if not hasattr(stage, "_obligation_resource_tokens"):
        stage._obligation_resource_tokens = frozenset(bundle.facts().get("resources", {}))
    if resource not in stage._obligation_resource_tokens:
        return
    marker = uuid.uuid4().hex
    private[CLONE_MARKER] = marker
    # A saved artist property must never become this request's lineage.
    if MARKER in private.keys():
        del private[MARKER]
    entry = {"resource": resource, "marker": marker, "locator": private.name, "type": private.id_type}
    stage.extras.setdefault(CLONES, []).append(entry)
    if not hasattr(stage, "_obligation_clone_ids"):
        stage._obligation_clone_ids = {}
    stage._obligation_clone_ids[marker] = (private.as_pointer(), _uid(private))


def initialize_clones(stage):
    if not stage.extras.get(CLONES):
        return
    stage.context._obligation_clone_ids = stage._obligation_clone_ids
    stage.context._obligation_clone_resources = {entry["marker"]: entry["resource"] for entry in stage.extras[CLONES]}
    index = _index(stage.context)
    for entry in stage.extras[CLONES]:
        entry["owners"] = _locations(stage.context, entry["marker"], clone=True, index=index)


def _clone_matches(context, block, token):
    """Source text only corroborates a captured copy, never establishes it."""
    from .node_edit_spec import SOURCE
    marker = block.get(CLONE_MARKER)
    identity = (block.as_pointer(), _uid(block))
    if not marker or getattr(context, "_obligation_clone_ids", {}).get(marker) != identity:
        return False
    if identity in getattr(context, "_obligation_id_tokens", {}):
        return False  # A registered edited ID cannot regain clone authority by forging SOURCE.
    if getattr(context, "_obligation_clone_resources", {}).get(marker) != token:
        return False
    return block.get(SOURCE) == int(token.split(":", 1)[1])


def _owned(context, block):
    return block in context.owned_ids


def _registered(context, block):
    marker = block.get(MARKER)
    if marker is None or not _owned(context, block):
        return None
    identity = (block.as_pointer(), _uid(block))
    token = getattr(context, "_obligation_id_tokens", {}).get(identity)
    entry = context.extras.get(REGISTRY, {}).get(token)
    if entry is not None and entry["marker"] == marker and _transfer_matches(block, entry.get("transfer")):
        return token, entry
    return None


def _transfer_matches(block, expected):
    if expected is None:
        return True  # Pending copies acquire their approved transfer semantics after the kernel.
    from .node_edit_spec import SOURCE, EDITED, MODE
    if SOURCE in expected:
        return block.get(SOURCE) == expected[SOURCE]
    return all(block.get(key) == expected.get(key) for key in (SOURCE, EDITED, MODE))


def _transfer(token, source, mode, context):
    from .node_edit_spec import SOURCE, EDITED, MODE
    entry = context.extras.get(REGISTRY, {}).get(token)
    if mode == "reuse" and entry is not None:
        return entry.get("transfer", {})
    if token.startswith(("live:", "group:")):
        uid = int(token.split(":", 1)[1])
        return {SOURCE: uid} if mode == "reuse" else {EDITED: uid, MODE: "REMAP"}
    if mode in {"create", "copy"}:
        return {}
    previous = context.extras.get(REGISTRY, {}).get(source, {}).get("transfer", {})
    if source and source.startswith(("live:", "group:")):
        return {EDITED: int(source.split(":", 1)[1]), MODE: "ASSIGN"}
    return previous if EDITED in previous else {}


def logical_material_name(context, target, material):
    """A created isolated ID's logical lookup name, proven by private provenance."""
    registered = _registered(context, material)
    return registered[1]["name"] if registered is not None else None


def record_isolation(context, target, previous, private):
    """Keep lookup provenance during the kernel; native after-readback assigns the final token."""
    from .node_edit import logical_name
    record_copy(context, previous, private, logical_name(context, target, previous))


def record_copy(context, previous, private, name):
    """An exact native copy output and its source, before later slot mutations."""
    if not _owned(context, previous) or not _owned(context, private) or previous == private:
        raise ProgramError("Material isolation did not produce a private native ID", code="UNKNOWN")
    token = "pending:" + uuid.uuid4().hex
    if CLONE_MARKER in private.keys():
        del private[CLONE_MARKER]
    _register(context, token, private, name)
    if not hasattr(context, "_obligation_copy_sources"):
        context._obligation_copy_sources = {}
    context._obligation_copy_sources[token] = (previous.as_pointer(), _uid(previous))


def _from_source(context, block, source):
    if (block.as_pointer(), _uid(block)) == source:
        return True
    registered = _registered(context, block)
    return (registered is not None and registered[0].startswith("pending:") and
            getattr(context, "_obligation_copy_sources", {}).get(registered[0]) == source)


def _register(context, token, block, name, transfer=None):
    if not _transfer_matches(block, transfer):
        raise ProgramError("Material transfer markers disagree with its proven source and approved effect", code="UNKNOWN")
    registry = context.extras.setdefault(REGISTRY, {})
    previous = _registered(context, block)
    if previous is not None and previous[0].startswith("pending:"):
        del registry[previous[0]]
        old = context._obligation_ids.pop(previous[0], None)
        context._obligation_id_tokens.pop(old, None)
        getattr(context, "_obligation_copy_sources", {}).pop(previous[0], None)
    marker = uuid.uuid4().hex
    block[MARKER] = marker
    registry[token] = {"marker": marker, "name": name, "locator": block.name, "type": block.id_type,
                       "transfer": transfer}
    if not hasattr(context, "_obligation_ids"):
        context._obligation_ids = {}
        context._obligation_id_tokens = {}
    old = context._obligation_ids.get(token)
    context._obligation_id_tokens.pop(old, None)
    context._obligation_ids[token] = (block.as_pointer(), _uid(block))
    context._obligation_id_tokens[context._obligation_ids[token]] = token


def matches_resource(context, block, token):
    """An actual staged ID or unedited clone's eventual live identity."""
    if block is None or not _owned(context, block):
        return False
    entry = context.extras.get(REGISTRY, {}).get(token)
    if entry is not None:
        registered = _registered(context, block)
        if registered is None or registered[0] != token:
            # An untouched private clone restores its exact live source at
            # commit. That live ID may then be remapped by an edited copy.
            return token.startswith(("live:", "group:")) and _clone_matches(context, block, token)
        return True  # Fresh lifecycle indexes reject duplicate markers before any retained/committed result.
    if token.startswith(("live:", "group:")):
        return _clone_matches(context, block, token)
    return False


def _slot(context, target, index):
    obj = context.resolve(target)
    return obj.material_slots[index].material if 0 <= index < len(obj.material_slots) else None


def begin_operation(stage, operation):
    """Capture identity before the existing native dispatch, never from its result."""
    context = stage.context
    index = stage.extras.get("obligation_operation_index", 0)
    effects = stage.binding["obligations"].effects()
    effect = next((item for item in effects if item["operation"] == index), None)
    if effect and effect.get("append"):
        from .program_material_slots import appended_where_planned
        appended_where_planned(stage, operation, effect, index)
    before = {block.as_pointer() for block in context.owned_ids} if effect else set()
    source_identity = None
    tree_identity = None
    if effect and effect.get("source"):
        if operation["kind"] == "material_assign":
            from .action_materials import _find
            source = _find(context, context.resolve(operation["target"]), operation["parameters"], operation["target"])
        else:
            source = _slot(context, effect["target"], effect["slot"])
        if not matches_resource(context, source, effect["source"]):
            raise ProgramError("Material operation no longer has its bound source identity", code="UNKNOWN")
        source_identity = (source.as_pointer(), _uid(source))
        if effect.get("tree_source"):
            node = source.node_tree.nodes.get(operation["parameters"]["group"]) if source.node_tree else None
            tree = node.node_tree if node is not None and node.bl_idname == "ShaderNodeGroup" else None
            if not matches_resource(context, tree, effect["tree_source"]):
                raise ProgramError("Material operation no longer has its selected group identity", code="UNKNOWN")
            tree_identity = (tree.as_pointer(), _uid(tree))
    return index, effect, before, source_identity, tree_identity


def finish_operation(stage, operation, proof):
    """Read actual native slots and fresh IDs after the existing dispatch."""
    context = stage.context
    index, effect, before, source_identity, tree_identity = proof
    if effect:
        material = _slot(context, effect["target"], effect["slot"])
        if material is None:
            raise ProgramError("Material operation did not produce its required slot", code="UNKNOWN")
        mode, token = effect["mode"], effect["resource"]
        if source_identity is not None and not _from_source(context, material, source_identity):
            raise ProgramError("Material operation did not retain its actual native source or copied lineage", code="UNKNOWN")
        if mode in {"create", "copy"} or mode == "isolate" and not str(effect.get("source")).startswith("live:"):
            if material.as_pointer() in before or not _owned(context, material):
                raise ProgramError("Material operation did not produce its required distinct native ID", code="UNKNOWN")
        elif mode == "isolate":
            from .node_edit_spec import EDITED, MODE
            if (material.get(EDITED) != int(effect["source"].split(":", 1)[1]) or material.get(MODE) != "ASSIGN"):
                raise ProgramError("Material isolation did not preserve its exact live source and assignment policy", code="UNKNOWN")
        elif source_identity is None and not matches_resource(context, material, token):
            raise ProgramError("Material operation changed its bound native identity", code="UNKNOWN")
        from .node_edit import logical_name
        name = logical_name(context, effect["target"], material)
        if material.as_pointer() not in before and CLONE_MARKER in material.keys():
            del material[CLONE_MARKER]
        if mode in {"create", "copy", "isolate", "edit"} or token not in context.extras.get(REGISTRY, {}):
            _register(context, token, material, name, _transfer(token, effect.get("source"), mode, context))
        tree_token = effect.get("tree_resource")
        if tree_token:
            group = material.node_tree.nodes.get(operation["parameters"]["group"])
            tree = group.node_tree if group is not None and group.bl_idname == "ShaderNodeGroup" else None
            if tree is None:
                raise ProgramError("Material operation lost its selected group", code="UNKNOWN")
            if not _from_source(context, tree, tree_identity):
                raise ProgramError("Material group did not retain its exact native source or copied lineage", code="UNKNOWN")
            if tree.as_pointer() not in before and CLONE_MARKER in tree.keys():
                del tree[CLONE_MARKER]
            if tree_token != effect.get("tree_source"):
                from .node_edit_spec import EDITED, MODE
                from .node_edit import source_uid
                live_source = effect["tree_source"].startswith("group:")
                uid = int(effect["tree_source"].split(":", 1)[1]) if live_source else None
                if (not _owned(context, tree) or live_source and source_uid(tree) != uid or
                        tree.as_pointer() in before and (tree.get(EDITED) != uid or tree.get(MODE) != "ASSIGN")):
                    raise ProgramError("Material group isolation did not create its required native ID", code="UNKNOWN")
            tree_mode = "isolate" if tree_token != effect.get("tree_source") else "edit"
            _register(context, tree_token, tree, tree.name,
                      _transfer(tree_token, effect.get("tree_source"), tree_mode, context))
        for target in operation["parameters"].get("also", ()):
            if not any(slot.material == material for slot in context.resolve(target).material_slots):
                raise ProgramError("Material assignment did not retain one actual ID for all targets", code="UNKNOWN")
    stage.extras["obligation_operation_index"] = index + 1
    inventory = _index(context) if context.extras.get(REGISTRY) or context.extras.get(CLONES) else None
    for token, entry in context.extras.get(REGISTRY, {}).items():
        entry["owners"] = _locations(context, token, index=inventory)
    for entry in context.extras.get(CLONES, ()):
        entry["owners"] = _locations(context, entry["marker"], clone=True, index=inventory)


def _index(context):
    """One charged owned-ID and edge inventory per native lifecycle boundary."""
    from .program_dependencies import fingerprint_budget
    budget, index = fingerprint_budget(), {"resource": {}, "clone": {}, "owners": {}}
    for block in context.owned_ids:
        _charge(budget)
        for marker, field in ((MARKER, "resource"), (CLONE_MARKER, "clone")):
            value = block.get(marker)
            if value is not None:
                index[field].setdefault(value, []).append(block)
    groups = any(entry["type"] == "NODETREE" for entry in context.extras.get(REGISTRY, {}).values()) or any(
        entry["type"] == "NODETREE" for entry in context.extras.get(CLONES, ()))
    group_edges = {}
    for target, obj in (*context.objects.items(), *context.aliases.items()):
        for slot_index, slot in enumerate(obj.material_slots):
            _charge(budget)
            material = slot.material
            if material is None:
                continue
            identity = (material.as_pointer(), _uid(material))
            index["owners"].setdefault(identity, []).append([target, slot_index])
            if groups and material.node_tree is not None:
                if identity not in group_edges:
                    edges = group_edges[identity] = []
                    for node in material.node_tree.nodes:
                        _charge(budget)
                        tree = node.node_tree if node.bl_idname == "ShaderNodeGroup" else None
                        if tree is not None:
                            edges.append(((tree.as_pointer(), _uid(tree)), node.name))
                for group, name in group_edges[identity]:
                    _charge(budget)
                    index["owners"].setdefault(group, []).append([target, slot_index, name])
    return index


def _locations(context, token, *, clone=False, index):
    identities = getattr(context, "_obligation_clone_ids" if clone else "_obligation_ids", {})
    return index["owners"].get(identities.get(token), [])


def _manifest(stage):
    stage.binding["obligations"].assert_integrity(stage.binding["program"])
    return {token for effect in stage.binding["obligations"].effects()
            for token in (effect["resource"], effect.get("tree_resource")) if token}


def _needed(stage):
    bundle = stage.binding["obligations"]
    needed = {row["__host_material"][key] for row in bundle.checks() if "__host_material" in row
              for key in ("resource", "tree_resource") if key in row["__host_material"]}
    needed.update(effect[key] for effect in bundle.effects()
                  if effect["operation"] >= stage.extras.get("obligation_operation_index", 0)
                  for key in ("source", "resource", "tree_source", "tree_resource") if effect.get(key))
    return needed


def _clone_provenance(stage, index, *, restore=False):
    entries = stage.extras.get(CLONES, ())
    if not entries:
        return
    context = stage.context
    manifest = stage.binding["obligations"].facts()["resources"]
    identities = {} if restore else getattr(context, "_obligation_clone_ids", {})
    context._obligation_clone_resources = {entry["marker"]: entry["resource"] for entry in entries}
    needed, retained = _needed(stage), []
    registered = {entry["marker"]: entry for entry in context.extras.get(REGISTRY, {}).values()}
    carried = {entry["resource"] for entry in entries if entry.get("owners")}
    carried.update(token for token, entry in context.extras.get(REGISTRY, {}).items() if entry.get("owners"))
    for entry in entries:
        blocks = index["clone"].get(entry["marker"], ())
        if (entry["resource"] not in manifest or len(blocks) != 1 or blocks[0].name != entry["locator"]
                or blocks[0].id_type != entry["type"]):
            raise ProgramError("The stage lost a captured native material clone", code="UNKNOWN")
        identity = (blocks[0].as_pointer(), _uid(blocks[0]))
        from .node_edit_spec import SOURCE
        transfer = registered.get(blocks[0].get(MARKER))
        if ((transfer is None and blocks[0].get(SOURCE) != int(entry["resource"].split(":", 1)[1])) or
                transfer is not None and not _transfer_matches(blocks[0], transfer.get("transfer"))):
            raise ProgramError("A captured native clone's source or transfer policy changed", code="UNKNOWN")
        if not restore and identities.get(entry["marker"]) != identity:
            raise ProgramError("A marker cannot replace a captured native clone identity", code="UNKNOWN")
        identities[entry["marker"]] = identity
        context._obligation_clone_ids = identities
        if _locations(context, entry["marker"], clone=True, index=index) != entry["owners"]:
            raise ProgramError("The captured native clone's owner slots changed", code="UNKNOWN")
        if not restore and not entry["owners"] and (entry["resource"] not in needed or entry["resource"] in carried):
            del blocks[0][CLONE_MARKER]
            identities.pop(entry["marker"], None)
        else:
            retained.append(entry)
    stage.extras[CLONES] = retained


def snapshot_provenance(stage):
    """Prove exact resident IDs before persisting their private locators."""
    if stage.context is None:
        return
    if not stage.extras.get(REGISTRY) and not stage.extras.get(CLONES):
        return
    index = _index(stage.context)
    _clone_provenance(stage, index)
    if not stage.extras.get(REGISTRY):
        return
    manifest = _manifest(stage)
    needed = _needed(stage)
    for token, entry in list(stage.extras[REGISTRY].items()):
        blocks = index["resource"].get(entry["marker"], ())
        if token not in manifest or len(blocks) != 1 or not matches_resource(stage.context, blocks[0], token):
            raise ProgramError("Staged material provenance changed before suspension", code="UNKNOWN")
        if _locations(stage.context, token, index=index) != entry.get("owners", []):
            raise ProgramError("Staged material ownership changed before suspension", code="UNKNOWN")
        if not entry.get("owners") and token not in needed:
            del blocks[0][MARKER]
            del stage.extras[REGISTRY][token]
            identity = stage.context._obligation_ids.pop(token, None)
            stage.context._obligation_id_tokens.pop(identity, None)
            continue  # An overwritten, unused ID need not enter the scene bundle.
        entry["locator"] = blocks[0].name


def restore_provenance(stage):
    """Rebind trusted private bundle locators, checking manifest and uniqueness."""
    if not stage.extras.get(REGISTRY) and not stage.extras.get(CLONES):
        return
    index = _index(stage.context)
    _clone_provenance(stage, index, restore=True)
    registry = stage.extras.get(REGISTRY, {})
    if not registry:
        return
    manifest = _manifest(stage)
    identities = {}
    for token, entry in registry.items():
        blocks = index["resource"].get(entry["marker"], ())
        if (token not in manifest or len(blocks) != 1 or blocks[0].name != entry["locator"]
                or blocks[0].id_type != entry["type"] or not _transfer_matches(blocks[0], entry.get("transfer"))):
            raise ProgramError("Hydrated material provenance does not match its retained native identity", code="UNKNOWN")
        identities[token] = (blocks[0].as_pointer(), _uid(blocks[0]))
    stage.context._obligation_ids = identities
    stage.context._obligation_id_tokens = {identity: token for token, identity in identities.items()}
    for token, entry in registry.items():
        if _locations(stage.context, token, index=index) != entry.get("owners", []):
            raise ProgramError("Hydrated material does not have its retained owner slots", code="UNKNOWN")


def guard_material(stage, check, guard):
    """Conjunctive exact-slot identity proof for one host-owned predicate."""
    material = _slot(stage.context, check["target"], guard["slot"])
    passed = matches_resource(stage.context, material, guard["resource"])
    proof = {"kind": "material_identity", "passed": passed, "status": "pass" if passed else "fail", "slot": guard["slot"]}
    if guard.get("tree_resource"):
        node = material.node_tree.nodes.get(check["group"]) if material and material.node_tree else None
        tree = node.node_tree if node is not None and node.bl_idname == "ShaderNodeGroup" else None
        tree_passed = matches_resource(stage.context, tree, guard["tree_resource"])
        proof["checks"] = [{"kind": "material_group_identity", "passed": tree_passed,
                            "status": "pass" if tree_passed else "fail"}]
        proof["passed"] = passed and tree_passed
        proof["status"] = "pass" if proof["passed"] else "fail"
    return material, proof


def selected_material_report(material, check, holder=None, slot=None):
    """Reuse scalar predicates on the identity-proved slot, without scanning slots. An assignment is rendered: on a
    mesh with faces, at least one face uses ``slot`` (a slot no face uses leaves the object looking unchanged)."""
    from .validation_checks import _material_values
    from .validation_reader import Inconclusive, values_near
    from .validation_spec import validate_check
    check = validate_check(check)
    item = {"index": 0, "kind": check["kind"], "target": check["target"]}
    try:
        if check["kind"] == "material_values":
            values = _material_values(material)
            # The values the check states, as validation_checks reads them (one stated alone, SETTINGS_NOTE).
            passed = all(values_near(values[key], check[key], check.get("tolerance")) for key in values if key in check)
        else:
            if check["all_faces"]:
                raise Inconclusive("Exact face assignment requires the normal bounded mesh predicate")
            from .material_faces import rendered_faces, slots_holding
            bound = holder.material_slots[slot].material if holder is not None and slot is not None \
                and 0 <= slot < len(holder.material_slots) else None
            # Every slot that holds the bound slot's live material: the same material may sit in two slots.
            from .validation_spec import MAX_MESH_ELEMENTS
            within = bound is not None and len(getattr(getattr(holder, "data", None), "polygons", ())) <= MAX_MESH_ELEMENTS
            faces = rendered_faces(holder, slots_holding(holder, bound)) if within else None
            values, passed = {"material_present": True, "scope": "exact_bound_material_slot"}, True
            if faces is not None:
                values, passed = dict(values, rendered_faces=faces, scope="exact_bound_material_slot_faces"), faces > 0
            elif bound is not None and not within:
                values["faces_unread"] = "Base mesh exceeds deterministic verification limits"
        item.update(status="pass" if passed else "fail", passed=passed, observed=values)
    except (Inconclusive, AttributeError, TypeError, ValueError, ReferenceError, RuntimeError) as error:
        item.update(status="inconclusive", passed=False, reason=str(error)[:240])
    return {"success": item["passed"], "status": item["status"], "checks": [item],
            "truncated": item["status"] == "inconclusive", "read_only": True, "scope": "closed_base_data_predicates"}


def strip_provenance(stage):
    """Private identity markers never become artist data."""
    for block in stage.context.owned_ids:
        for marker in (MARKER, CLONE_MARKER):
            if marker in block.keys():
                del block[marker]
