# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact rich-query revisions delivered to one native chat request (Codex and Claude).

Packing only records short forms. Inferred bindings become available after the
response write succeeds and its original owner is still current. These are the
host query's hashes, never live revisions or values supplied by a model call.

A Claude plan reads its lookups' results in the next round's prompt
(``providers.ClaudeProvider``), which records the same rows with
``observe_lookups`` just before that prompt is sent, under the later commits and
the fresh observation that prompt carries: the turn observation lists
at most eight objects (``turn_context``), and a plan that then edited an object a
lookup had just shown was refused as having "no revision in your latest
observation".
"""

from . import context_ledger, scene_binding
from .identifiers import FULL_REVISION, LONG_HANDLE, IdentifierTable
from .request_fences import DeliveryOwner


def prepare_observation(context, tool, result, table, owner):
    """Snapshot bounded host rows after packing; return a delivery finalizer.

    The caller captures ``table`` before packing and calls the finalizer only
    inside its successful, current-owner response finalization. No transport
    I/O takes place here. Keeping only immutable handle/hash pairs prevents a
    later change to the result dict from becoming different delivered evidence.
    """
    host = getattr(context, "host", None)
    request_owner = getattr(context, "delivery_owner", None)

    def current():
        return (host is not None and isinstance(table, IdentifierTable)
                and getattr(host, "_identifiers", None) is table
                and getattr(context, "short_identifiers", False) and not context.cancelled
                and isinstance(owner, DeliveryOwner) and isinstance(request_owner, DeliveryOwner)
                and owner.parent is request_owner and context.delivery_owner is request_owner
                and owner.fence is not None and owner.fence == context.request_fence
                and owner.current(host) and request_owner.current(host))

    if not current():
        return None
    observed = query_rows(tool, result)
    if observed is None:
        return None

    def delivered():
        if current():
            table.observe({"objects": [{"handle": handle, "object_revision": revision}
                                       for handle, revision in observed]})

    return delivered


def query_rows(tool, result):
    """``(handle, exact revision or None)`` of each object a successful search or detail shows, else None."""
    if not isinstance(result, dict) or result.get("error") or result.get("success") is False:
        return None
    if tool == "angelo_scene_search":
        rows = result.get("objects")
        if not isinstance(rows, list):
            return None
    elif tool == "angelo_scene_object":
        rows = [result]
    else:
        return None  # Never infer from arbitrary script output or nested metadata.
    observed = []
    for row in rows:
        handle = row.get("object") if isinstance(row, dict) else None
        if not isinstance(handle, str) or not LONG_HANDLE.fullmatch(handle):
            continue
        revision = row.get("object_revision")
        exact = (row.get("revision_exact") is True and row.get("program_revision_supported") is True
                 and row.get("revision_limitations") == [] and not row.get("uncertain_reason")
                 and isinstance(revision, str) and FULL_REVISION.fullmatch(revision))
        # A present inexact row revokes older inferred authority; missing page
        # rows are untouched. Detail-section/page truncation does not by itself
        # make an individual object's separately proven revision inexact.
        observed.append((handle, revision if exact else None))
    return tuple(observed)


def compacted(context, value, operations, results, observation=None):
    """A Claude plan's results in the context's short identifiers, its lookups recorded for the next round.

    Call while the request still owns the turn, just before the prompt that
    carries ``value`` is sent. ``operations`` are the plan's ``{tool, arguments}``
    and ``results`` theirs, in order; ``observation`` is the continuation's
    fresh observation (long forms), already compacted (``observe_lookups``).
    """
    compact = getattr(context, "compact_identifiers", None)
    if not callable(compact):
        return value
    value = compact(value)  # Numbers the results' handles first.
    observe_lookups(context, operations, results, observation)
    return value


def observe_lookups(context, operations, results, observation=None):
    """Record what a Claude plan's searches and details showed, for its next round's inferred bindings.

    Call after the context compacted the results (which numbers their handles).
    A context without short identifiers (voice) has nothing to bind. Each
    object's latest view wins: the plan's results in order (a lookup's rows, a
    commit's post-commit revisions), then ``observation``, the fresh one the
    prompt carries. Rows recorded after it would rebind an object an edit later in
    the plan had changed to its pre-edit revision, and let an inexact
    (budget-truncated) row revoke the observation's exact one.
    """
    table = getattr(getattr(context, "host", None), "_identifiers", None)
    if not getattr(context, "short_identifiers", False) or not isinstance(table, IdentifierTable):
        return
    for operation, result in zip(operations, results):
        rows = query_rows(operation.get("tool") if isinstance(operation, dict) else None, result)
        if rows:
            table.observe({"objects": [{"handle": handle, "object_revision": revision} for handle, revision in rows]})
        context_ledger.observe_committed(table, result)  # Already recorded once; again, as the later view.
    if isinstance(observation, dict) and not observation.get("error"):
        table.observe(observation)
        scene_binding.observe(table, observation)
