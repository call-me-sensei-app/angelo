# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete, bounded verification obligations of a canonical typed program.

The public program contains at most 32 authored checks. Mandatory checks are
host-owned data derived from its operations, never fields a caller can supply.
Splitting a conjunction into several closed checks preserves every assertion;
resource admission refuses the whole program instead of dropping assertions.
"""

import copy
import hashlib
import json
from dataclasses import dataclass

from .action_schema_types import MAX_OPERATIONS
from .validation_spec import MAX_CHECKS, validate_check
from .program_material_obligations import GUARD, NeedsMaterialBinding, needs_material_binding
from .node_link_obligations import LINK_GUARD
from .check_defaults import proves_per_value

# A material operation has two target checks and at most 31 additional targets.
# A node operation has 32 edits of at most 32 members, packed 32 per check.
# Other fan-outs (keyed properties, joins, batches) are smaller. Keep a runtime
# guard: a new operation must justify its complete expansion before admission.
MAX_HOST_CHECKS = MAX_OPERATIONS * 33
MAX_PROGRAM_CHECKS = MAX_CHECKS + MAX_HOST_CHECKS
MAX_CHECK_LEAVES = MAX_OPERATIONS * 2 * 64 * 32
MAX_CHECK_BYTES = 1024 * 1024


def _refuse(detail):
    raise ValueError("PROGRAM_CHECK_BUDGET: " + detail + "; split the operations and their expected checks "
                     "across smaller programs")


def _chunks(values, size):
    return [values[index:index + size] for index in range(0, len(values), size)]


def _split_maps(check, fields, size):
    """Conjunctive maps, keeping the same target/identity on every fragment."""
    parts = {field: _chunks(list(check[field].items()), size) for field in fields if check.get(field)}
    if not parts:
        return [check]
    base = {key: value for key, value in check.items() if key not in parts}
    return [dict(base, **{field: dict(groups[index]) for field, groups in parts.items() if index < len(groups)})
            for index in range(max(len(groups) for groups in parts.values()))]


def _split(check):
    kind = check["kind"]
    if kind in {"property", "particle_values"}:
        return _split_maps(check, ("values",), 32 if kind == "property" else 64)
    if kind == "bone":
        return _split_maps(check, ("settings", "pose_settings"), 64)
    if kind in {"collection_membership", "modifier_stack"}:
        fields = ("collections", "absent") if kind == "collection_membership" else ("positions", "absent")
        groups = {field: _chunks(check.get(field, []), 16) for field in fields}
        base = {key: value for key, value in check.items() if key not in fields}
        return [dict(base, **{field: parts[index] for field, parts in groups.items() if index < len(parts)})
                for index in range(max((len(parts) for parts in groups.values()), default=0))] or [check]
    if kind != "node_graph":
        return [check]
    # Repeated named-node fragments assert distinct settings of the same node.
    # No quantifier, count, or positional meaning depends on their grouping.
    nodes = [part for node in check.get("nodes", ())
             for part in _split_maps(node, ("settings", "inputs", "outputs"), 32)]
    lists = {"nodes": nodes, **{key: check.get(key, []) for key in ("absent", "links", "unlinked")}}
    groups = {key: _chunks(values, 32) for key, values in lists.items()}
    negative = _chunks(check.get(LINK_GUARD, {}).get("absent", []), 32)
    base = {key: value for key, value in check.items() if key not in lists and key != LINK_GUARD}
    result = []
    for index in range(max([len(negative), *(len(parts) for parts in groups.values())], default=0)):
        part = dict(base, **{key: parts[index] for key, parts in groups.items() if index < len(parts)})
        if index < len(negative):
            part[LINK_GUARD] = {"absent": negative[index]}
            if not any(part.get(key) for key in lists):
                part["nodes"] = [{"name": name} for name in dict.fromkeys(row["to_node"] for row in negative[index])]
        result.append(part)
    return result or [check]


def _key(value):
    """Canonical predicate equality: numbers compare numerically, booleans do not."""
    if isinstance(value, dict):
        return ("object", tuple((key, _key(item)) for key, item in sorted(value.items())))
    if isinstance(value, list):
        return ("array", tuple(_key(item) for item in value))
    if type(value) in (int, float):
        return ("number", value)
    return (type(value).__name__, value)


def _keys(check):
    """A check's predicate key, and its tolerance-free twin's when it proves that one (check_defaults)."""
    keys = [_key(check)]
    if isinstance(check, dict) and "kind" in check and proves_per_value(check):
        keys.append(_key({key: value for key, value in check.items() if key != "tolerance"}))
    return keys


def _budget(checks):
    """Bound scalar traversal and encoded storage before schema work/retention."""
    pending, leaves, minimum_bytes = [checks], 0, 0
    try:
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                minimum_bytes += 2 + sum(len(key.encode("utf-8")) for key in value)
                pending.extend(value.values())
            elif isinstance(value, list):
                minimum_bytes += 2
                pending.extend(value)
            else:
                leaves += 1
                minimum_bytes += len(value.encode("utf-8")) if isinstance(value, str) else 1
                if leaves > MAX_CHECK_LEAVES:
                    _refuse("effective checks exceed %d scalar values (observed at least %d)" % (MAX_CHECK_LEAVES, leaves))
            if minimum_bytes > MAX_CHECK_BYTES:
                _refuse("effective checks exceed %d UTF-8 bytes (observed at least %d)" %
                        (MAX_CHECK_BYTES, minimum_bytes))
        # The lower-bound guard caps string input before the C JSON encoder.
        # Escaping expands a code point at most sixfold; punctuation and numeric
        # text are bounded by the scalar/container counts. This avoids millions
        # of Python encoder fragments while keeping transient storage bounded.
        size = len(json.dumps(checks, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        if size > MAX_CHECK_BYTES:
            _refuse("effective checks use %d UTF-8 bytes (limit %d)" % (size, MAX_CHECK_BYTES))
    except (TypeError, UnicodeError, OverflowError) as error:
        _refuse("effective checks cannot be represented as bounded finite JSON: " + str(error)[:160])
    except ValueError as error:
        if str(error).startswith("PROGRAM_CHECK_BUDGET:"):
            raise
        _refuse("effective checks cannot be represented as bounded finite JSON: " + str(error)[:160])


def host_checks(operations, *, raw=None):
    """Every final-state host assertion, validated and split without omissions."""
    from .derived_checks import derive_checks
    raw = derive_checks(operations) if raw is None else raw
    _budget(raw)
    expanded = [part for check in raw for part in _split(check)]
    if len(expanded) > MAX_HOST_CHECKS:
        _refuse("%d mandatory checks exceed the host limit of %d" % (len(expanded), MAX_HOST_CHECKS))
    _budget(expanded)
    result = []
    for index, check in enumerate(expanded):
        try:
            guard = check.get(GUARD)
            canonical = validate_check({key: value for key, value in check.items() if key not in {GUARD, LINK_GUARD}})
            if guard is not None:
                if (not isinstance(guard, dict) or set(guard) - {"resource", "slot", "tree_resource"}
                        or not isinstance(guard.get("resource"), str) or not guard["resource"]
                        or type(guard.get("slot")) is not int or guard["slot"] < 0
                        or "tree_resource" in guard and not isinstance(guard["tree_resource"], str)):
                    raise ValueError("invalid private material identity guard")
                canonical[GUARD] = copy.deepcopy(guard)
            if LINK_GUARD in check:
                negative = check[LINK_GUARD]
                if (not isinstance(negative, dict) or set(negative) != {"absent"}
                        or not isinstance(negative["absent"], list) or not 1 <= len(negative["absent"]) <= 32
                        or any(not isinstance(row, dict) or not {"to_node", "to_socket"} <= set(row)
                               or set(row) - {"to_node", "to_socket", "from_node", "from_socket"}
                               or any(not isinstance(value, str) or not 1 <= len(value) <= 64 for value in row.values())
                               for row in negative["absent"])):
                    raise ValueError("invalid private exact-unlink obligation")
                canonical[LINK_GUARD] = copy.deepcopy(negative)
            result.append(canonical)
        except ValueError as error:
            _refuse("mandatory check %d (%s) cannot be fully verified: %s" %
                    (index + 1, check.get("kind", "unknown"), str(error)[:240]))
    return result


def _combine(program, implied):
    """Authored checks plus all implied checks of an already canonical program.

    This does not normalize public arguments or admit a larger authored list.
    Equal predicates are executed once, as is an implied one an authored twin
    proves at a tolerance no looser than its own per-value one; conflicting or
    looser authored predicates never replace a mandatory assertion. The caller owns the returned list.
    """
    authored = program.get("expected_checks", [])
    if not isinstance(authored, list) or len(authored) > MAX_CHECKS:
        raise ValueError("A program may request at most 32 authored expected checks")
    result = copy.deepcopy(authored)
    seen = {key for check in result for key in _keys(check)}
    for check in implied:
        key = _key(check)
        if key not in seen:
            result.append(check)
            seen.add(key)
    if len(result) > MAX_PROGRAM_CHECKS:
        _refuse("%d effective checks exceed the total limit of %d" % (len(result), MAX_PROGRAM_CHECKS))
    _budget(result)
    return result


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _digest(encoded):
    return hashlib.sha256(encoded).hexdigest()


def program_digest(program):
    """Digest the exact canonical expanded public program, without private facts."""
    return _digest(_encoded(program))


@dataclass(frozen=True, slots=True)
class FrozenObligations:
    """Private value owned by native binding; detached copies carry no authority."""

    encoded: bytes
    facts_encoded: bytes
    effects_encoded: bytes
    program_digest: str
    facts_digest: str
    checks_digest: str
    identity: str
    derived_flags: tuple
    requires_binding: bool
    unknown_encoded: bytes = b"[]"

    def checks(self):
        return json.loads(self.encoded)

    def facts(self):
        return json.loads(self.facts_encoded)

    def effects(self):
        return json.loads(self.effects_encoded)

    def unknowns(self):
        return json.loads(self.unknown_encoded)

    def assert_integrity(self, program):
        checks = self.checks()
        actual = (_digest(_encoded(program)), _digest(self.facts_encoded), _digest(_encoded(checks)))
        expected = (self.program_digest, self.facts_digest, self.checks_digest)
        identity = _digest(_encoded([*actual, _digest(self.effects_encoded), self.derived_flags, self.requires_binding,
                                    _digest(self.unknown_encoded)]))
        if (actual != expected or identity != self.identity or len(self.derived_flags) != len(checks)
                or any(type(flag) is not bool for flag in self.derived_flags)
                or type(self.requires_binding) is not bool or self.requires_binding != needs_material_binding(program)):
            raise ValueError("The program's private verification obligations changed; stage it again")


def _material_program(program):
    return any(operation["kind"] in {"material_principled", "shader_nodes", "material_assign", "node_edit"}
               for operation in program["operations"])


def resolve_obligations(program, facts=None):
    """Resolve complete immutable checks from canonical arguments and private facts.

    No public argument/result is a source of facts. Native jobs independently
    collect them again and compare this value with the approved binding.
    """
    from .derived_checks import derive_checks, identity as check_identity
    from .program_material_obligations import material_checks, replaces
    if facts:
        _budget(facts)
    bound = needs_material_binding(program)
    if bound and facts is None:  # Also a program without material kinds: property_set's material paths.
        raise NeedsMaterialBinding("Program material obligations need authoritative native binding facts")
    effects, unknowns = [], []
    if _material_program(program):
        material, effects, unproved = material_checks(program, facts)
        seen_unknown = set()
        for entry in unproved:
            key = _key(entry)
            if key not in seen_unknown:
                unknowns.append(entry)
                seen_unknown.add(key)
        # Preserve existing review/check order wherever the final predicate has
        # the same public identity. Newly proved owner relations follow them.
        pending = {}
        for check in material:
            pending.setdefault(check_identity(check), []).append(check)
        raw = []
        for check in derive_checks(program["operations"]):
            if not replaces(check):
                raw.append(check)
            elif pending.get(check_identity(check)):
                raw.append(pending[check_identity(check)].pop(0))
        raw.extend(check for values in pending.values() for check in values)
        implied = host_checks(program["operations"], raw=raw)
    else:
        implied = host_checks(program["operations"])
    checks = _combine(program, implied)
    from .derived_geometry import unchecked_geometry
    unknowns.extend(unchecked_geometry(program["operations"], checks))  # Geometry changes no check measures.
    if len(implied) + len(unknowns) > MAX_HOST_CHECKS or len(checks) + len(unknowns) > MAX_PROGRAM_CHECKS:
        _refuse("known checks and explicit unproved obligations exceed the program allowance")
    # Metadata participates in the existing budget; it is never a second
    # unbounded store beside the admitted check payload.
    if effects or facts or unknowns:
        _budget({"checks": checks, "facts": facts or {}, "effects": effects, "unknowns": unknowns})
    plain_implied = {_key({key: item for key, item in check.items() if key not in {GUARD, LINK_GUARD}}) for check in implied}
    flags = tuple(any(key in plain_implied for key in _keys({key: item for key, item in check.items()
                                                              if key not in {GUARD, LINK_GUARD}})) for check in checks)
    # Detached checks preserve established review field order; canonical hashes
    # remain independent of dictionary insertion order.
    encoded = json.dumps(checks, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    facts_encoded, effects_encoded = _encoded(facts or {}), _encoded(effects)
    unknown_encoded = _encoded(unknowns)
    program_digest, facts_digest, check_digest = _digest(_encoded(program)), _digest(facts_encoded), _digest(_encoded(checks))
    identity = _digest(_encoded([program_digest, facts_digest, check_digest, _digest(effects_encoded), flags, bound,
                                _digest(unknown_encoded)]))
    return FrozenObligations(encoded, facts_encoded, effects_encoded, program_digest, facts_digest,
                             check_digest, identity, flags, bound, unknown_encoded)


def admit_obligations(program):
    """Public structural admission; native binding resolves any unknown identity.

    The returned checks are for validating target references only. A caller
    must use resolve_obligations before approval, staging, or certification.
    """
    if not _material_program(program):
        return _combine(program, host_checks(program["operations"]))
    if not needs_material_binding(program):
        return resolve_obligations(program).checks()
    return _combine(program, host_checks(program["operations"]))


def required_checks(program, *, material_facts=None):
    """Detached complete checks; unresolved existing identities fail closed."""
    if not _material_program(program):
        return _combine(program, host_checks(program["operations"]))
    obligations = resolve_obligations(program, material_facts)
    if obligations.unknowns():
        raise ValueError("Program verification is partial; resolve_obligations preserves its explicit unproved obligations")
    return obligations.checks()


def checks_digest(checks):
    """Bind the exact ordered effective predicates to an approval or preview."""
    encoded = json.dumps(checks, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
