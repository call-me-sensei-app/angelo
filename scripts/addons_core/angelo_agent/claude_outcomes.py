# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Truthful terminal classification of Claude's host-confirmed native results."""

import json


from .program_completion import (incomplete_verification, completions_current, commit_facts, live_rows,
                                 pure_program_checks as program_checks, pure_derived_flags as derived_flags)


def native_result_problem(result, *, verification=False):
    """Return actionable feedback; a missing ``error`` is not proof of success.

    Only outcome envelopes and their explicit operations/checks are inspected,
    never arbitrary scene data, memory records or prose. Standalone verification
    needs positive pass evidence; inconclusive/malformed evidence fails closed.
    """
    if not isinstance(result, dict):
        return "The host returned no usable native result. Inspect before continuing; do not automatically retry edits."
    if result.get("error"):
        return str(result["error"])
    status = result.get("status")
    if (result.get("success") is False or result.get("passed") is False
            or status in ("fail", "failed", "error", "inconclusive")):
        outcome = "inconclusive" if status == "inconclusive" else "failed"
        return (f"Native execution or verification {outcome}. The host read-backs "
                "do not prove completion. Inspect the failed checks and current "
                "scene; do not automatically retry edits.")
    operations, checks = result.get("operations", ()), result.get("checks", ())
    if not isinstance(operations, (list, tuple)) or not isinstance(checks, (list, tuple)):
        return "The host returned malformed execution/check evidence. Inspect before continuing; do not retry edits automatically."
    for child in operations:
        problem = native_result_problem(child)
        if problem:
            return problem
    for check in checks:
        problem = native_result_problem(check, verification=True)
        if problem:
            return problem
    if verification and (result.get("passed") is not True or status != "pass"):
        return ("Native verification is inconclusive: positive pass evidence is "
                "missing. Inspect the checks; do not automatically retry edits.")
    return None


def runtime_result_error(result):
    """Surface the runtime's actual error instead of falsely accepting a plan."""
    if not isinstance(result, dict):
        return "Claude Code did not complete the turn"
    subtype = result.get("subtype")
    if result.get("is_error") or (subtype and subtype != "success"):
        errors = result.get("errors")
        if isinstance(errors, list):
            text = "; ".join(error for error in errors if isinstance(error, str) and error.strip())
            if text:
                from .claude_stream import likely_cause
                cause = likely_cause(text)  # A plan that failed its schema: the one mismatch to read first.
                return cause + ". " + text if cause else text
        return result.get("result") or subtype or "Claude Code did not complete the turn"
    return None


# Staged programs certify a goal: each expected check runs on the staged copy,
# again on the prospective live scene immediately before commit, and on Accept.
_PROGRAM_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"})
# Edits whose refusal can say that nothing changed (correctable_failure); the last two are job_spec.JOB_NAMES.
_UNCHANGED_WHEN_REFUSED = (_PROGRAM_TOOLS - {"angelo_accept_preview"}
                           | {"angelo_apply_batch", "angelo_job_start", "angelo_job_cancel"})
_MAX_CHECK_LINES = 32
_MAX_SUMMARY_CHARS = 4000


def _display(value, limit=64):
    """A bounded one-line label for untrusted names and values."""
    text = "".join(" " if ord(character) < 32 or character == "\x7f" else character for character in str(value))
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return format(value, ".6g")


def _value(value):
    if isinstance(value, str):
        return "'" + _display(value, 48) + "'"
    number = _number(value)
    if number is not None:
        return number
    if isinstance(value, bool) or value is None:
        return str(value).lower()
    if isinstance(value, (list, tuple)) and len(value) <= 4 and all(_number(item) is not None for item in value):
        return "[" + ", ".join(_number(item) for item in value) + "]"
    return None  # Nested or long evidence stays in the full read-back above.


def _passed_rows(rows):
    from .program_completion import positive_rows
    return positive_rows(rows)


def _positive(check):
    """Positive pass evidence only: an outcome and every per-check row it holds."""
    return (isinstance(check, dict) and check.get("passed") is True and check.get("status") == "pass"
            and _passed_rows(check.get("checks")))


def _labels(result):
    """Display names for check targets, from the result's own read-back."""
    labels = {}
    for row in (result.get("live_revisions") or {}).get("objects", ()):
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            for key in ("handle", "alias"):
                if isinstance(row.get(key), str):
                    labels[row[key]] = row["name"]
    for row in (result.get("delta") or {}).get("modified", ()):
        if isinstance(row, dict) and isinstance(row.get("handle"), str) and isinstance(row.get("name"), str):
            labels.setdefault(row["handle"], row["name"])
    return labels


def _check_line(row, labels):
    observed = row.get("observed") if isinstance(row.get("observed"), dict) else {}
    values = [key + " " + text for key, value in observed.items()
              if key != "scope" and (text := _value(value)) is not None]
    target = labels.get(row.get("target"))
    return (_display(row.get("kind", "check"), 32) + (" on '" + _display(target) + "'" if target else "")
            + (": " + "; ".join(values) if values else ""))


def _changes(result):
    changes = result.get("changes") if result.get("status") == "committed" else result.get("delta")
    changes = changes if isinstance(changes, dict) else {}
    parts = []
    added = [row for row in changes.get("added", ()) if isinstance(row, dict)]
    if added:
        parts.append("added " + ", ".join("'" + _display(row.get("name", "")) + "' (" + _display(row.get("type", ""), 16) + ")"
                                          for row in added[:8]) + (" and %d more" % (len(added) - 8) if len(added) > 8 else ""))
    modified = [row for row in changes.get("modified", ()) if isinstance(row, dict)]
    if modified:
        parts.append("modified " + ", ".join("'" + _display(row.get("name", "")) + "'" for row in modified[:8])
                     + (" and %d more" % (len(modified) - 8) if len(modified) > 8 else ""))
    for row in _shared_edits(result)[:4]:
        # A material edited for every object that uses it: the users are part of what changed.
        users, count = row["users"], row["count"]
        parts.append("material '%s' edited for all %d users (%s%s)" % (
            _display(row["material"]), count, ", ".join("'" + _display(name) + "'" for name in users[:8]),
            " and %d more" % (count - min(len(users), 8)) if count > min(len(users), 8) else "")
            + (" and in data no object uses (%s)" % ", ".join("'" + _display(name) + "'" for name in row["holders"][:8])
               if row.get("holders") else ""))
    return "; ".join(parts) or "no object changes reported"


def _shared_edits(result):
    rows = result.get("shared_edits") if isinstance(result, dict) else None
    return [{"material": row["material"], "users": [name for name in row["users"] if isinstance(name, str)],
             "count": row["count"], **_holders(row)} for row in (rows if isinstance(rows, list) else ())
            if isinstance(row, dict) and isinstance(row.get("material"), str) and isinstance(row.get("users"), list)
            and type(row.get("count")) is int]


def _holders(row):  # Data no object uses that an edit for all users changed too (an orphan mesh), when named.
    holders = [name for name in row["holders"] if isinstance(name, str)] if isinstance(row.get("holders"), list) else []
    return {"holders": holders} if holders else {}


def _certified_program(operation, result, completion=None):
    """Summary lines for one program whose host checks prove it, else None."""
    tool, arguments = operation["tool"], operation["arguments"]
    if tool not in _PROGRAM_TOOLS or not isinstance(result, dict) or incomplete_verification(result, completion):
        return None
    expected_status = "committed" if tool == "angelo_accept_preview" or arguments.get("mode") != "PREVIEW" else "preview"
    if (result.get("success") is not True or result.get("final") is not True or result.get("status") != expected_status
            or result.get("error") or result.get("replayed") or result.get("partial_changes_possible")
            or result.get("cleanup_warning") or result.get("cleanup_pending") or result.get("journal_durable") is False):
        return None
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks or not all(_positive(check) for check in checks):
        return None  # No checks, or any non-positive one: the host proved nothing about the goal.
    if completion is not None:
        from .program_completion import ProgramCompletion
        if type(completion) is not ProgramCompletion or not completion.verified(operation, result):
            return None
        flags = completion.obligations.derived_flags
    elif tool == "angelo_scene_program":
        # The host ran the canonical checks: declared plus derived ones.
        canonical = program_checks(arguments)
        if canonical is None or len(canonical) != len(checks):
            return None
        flags = derived_flags(canonical, arguments)
    else:
        return None  # Recipes and Accept need their actual retained/expanded bundle.
    derived = sum(len(check["checks"]) for check, flag in zip(checks, flags) if flag)
    rows = [row for check in checks for row in check["checks"]]
    labels = _labels(result)
    implied = "; %d implied by the operations" % derived if derived else ""
    if expected_status == "preview":
        head = ("Staged a PREVIEW (preview " + _display(result.get("preview_id", ""), 40) + "); the live scene is unchanged "
                "until you Accept it. Staged result: " + _changes(result) + ".")
        verdict = "Host checks passed on the staged copy (%d of %d%s):" % (len(rows), len(rows), implied)
    else:
        head = (("Accepted the preview and committed it" if tool == "angelo_accept_preview" else "Committed")
                + " with one Blender Undo step: " + _changes(result) + ".")
        verdict = "Host checks passed on the live result (%d of %d%s):" % (len(rows), len(rows), implied)
    lines = [head, verdict] + ["- " + _check_line(row, labels) for row in rows[:_MAX_CHECK_LINES]]
    if len(rows) > _MAX_CHECK_LINES:
        lines.append("- and %d more passing checks (full read-back above)" % (len(rows) - _MAX_CHECK_LINES))
    from .commit_fidelity import dependents_line  # Objects outside the program whose shape follows its edits.
    return lines + [line for line in [dependents_line(result)] if line]


def certified_completion(operations, results, *, read_only, completions=None):
    """A host-composed final summary when host checks prove every edit, else None.

    ``operations`` are a plan's {tool, arguments} and ``results`` their host
    results in order; callers have already stopped on any failed result.
    ``read_only(tool)`` names tools that cannot edit. A summary is returned only
    when the plan made at least one edit and every edit is a staged program
    (APPLY committed, PREVIEW staged, or an accepted preview) whose explicit
    expected checks all passed with positive evidence; standalone verifications
    must also have passed. Anything else (an edit without checks, a missing,
    failed or inconclusive check, any other kind of edit, a replayed, partial or
    cleanup-pending result) returns None, so the caller never claims success
    that the host did not prove.
    """
    if not operations or len(operations) != len(results):
        return None
    if completions is not None and len(completions) != len(results):
        return None
    sections, edits = [], 0
    for operation, result, completion in zip(operations, results, completions or [None] * len(results)):
        tool = operation["tool"]
        if tool == "angelo_scene_verify":
            if native_result_problem(result, verification=True):
                return None
            sections.append(["Standalone verification passed (%d checks)." % len(result.get("checks", ()))])
            continue
        if read_only(tool) and tool not in _PROGRAM_TOOLS:
            continue
        if completions is not None and completion is None:
            return None  # Runtime delivery requires private proof, even for pure programs.
        lines = _certified_program(operation, result, completion)
        if lines is None:
            return None
        sections.append(lines)
        edits += 1
    if not edits:
        return None
    text = "Host-verified result (no further model round was needed):\n" + "\n".join(
        line for section in sections for line in section)
    if not completions_current(completions):
        return None
    return text if len(text) <= _MAX_SUMMARY_CHARS else text[:_MAX_SUMMARY_CHARS - 40] + "\n… (full read-back above)"


_RECEIPT_REVISION_FIELDS = ("handle", "name", "type", "role", "alias", "object_revision", "revision_omitted")
_MAX_RECEIPT_ROWS = 32


def _receipt_rows(rows, fields):
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, (list, tuple)) else []
    output = [{key: row[key] for key in fields if key in row} for row in rows[:_MAX_RECEIPT_ROWS]]
    return output, len(rows) > _MAX_RECEIPT_ROWS


def _clean_program_checks(tool, result, *, allow_partial=False):
    """The existing receipt gate, shared with model-facing check presentation."""
    if (tool not in _PROGRAM_TOOLS or not isinstance(result, dict)
            or not allow_partial and incomplete_verification(result)):
        return None
    if (result.get("success") is not True or result.get("final") is not True
            or result.get("status") not in ("committed", "preview") or result.get("error") or result.get("replayed")
            or result.get("partial_changes_possible") or result.get("cleanup_warning") or result.get("cleanup_pending")
            or result.get("journal_durable") is False):
        return None
    checks = result.get("checks")
    return checks if isinstance(checks, list) and (checks or allow_partial) and all(_positive(check) for check in checks) else None


def _uncertain_evidence(value, *, allow_revision_budget=False):
    """Unknown warning fields retain full evidence; this is presentation policy only."""
    pending = [(value, ())]
    while pending:
        item, path = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                # This retained metadata describes an unavailable future binding,
                # not an omitted verification. Only this leaf has an exception.
                if (allow_revision_budget and key == "reason" and child == "BUDGET" and len(path) == 4
                        and path[:2] == ("live_revisions", "objects") and type(path[2]) is int
                        and isinstance(value["live_revisions"].get("objects"), list)
                        and path[3] == "revision_omitted"):
                    continue
                label = str(key).lower()
                if (child and any(part in label for part in ("warning", "error", "uncertain", "partial", "cleanup", "truncated",
                                                            "reason", "failure", "inconclusive", "unverified", "replayed"))
                        or key in ("success", "passed", "verified", "journal_durable") and child is False
                        or key == "checks" and not _passed_rows(child)
                        or key == "status" and child in ("fail", "failed", "error", "inconclusive", "uncertain", "unverified", "cancelled", "partial")):
                    return True
                pending.append((child, path + (key,)))
        elif isinstance(item, (list, tuple)):
            pending.extend((child, path + (index,)) for index, child in enumerate(item))
    return False


def present_program_checks(tool, result, *, completion=None):
    """A model-facing copy of a large, fully passed typed result; never mutate raw evidence."""
    checks = _clean_program_checks(tool, result)
    if (checks is None or len(checks) <= _MAX_CHECK_LINES or "check_presentation" in result
            or incomplete_verification(result, completion)):
        return result
    summary = result.get("check_summary")
    if (not isinstance(summary, dict)
            or any(type(summary.get(key)) is not int or summary[key] != len(checks) for key in ("required", "checked", "passed"))):
        return result
    digest = summary.get("checks_digest")
    if (not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
            or _uncertain_evidence(result, allow_revision_budget=True)):
        return result
    return dict(result, checks=checks[:_MAX_CHECK_LINES], check_presentation={
        "checked": len(checks), "passed": len(checks), "shown": _MAX_CHECK_LINES,
        "omitted_passing": len(checks) - _MAX_CHECK_LINES, "presentation_only": True})


def present_plan_checks(operations, result, *, completions=None):
    """Present continuation results using the dispatched plan's authoritative tool names."""
    if (not isinstance(result, dict) or result.get("success") is not True
            or not isinstance(operations, (list, tuple))):
        return result
    rows = result.get("operations")
    if (not isinstance(rows, list) or len(rows) != len(operations)
            or any(not isinstance(operation, dict) or not isinstance(operation.get("tool"), str) for operation in operations)):
        return result
    if (_uncertain_evidence({key: value for key, value in result.items() if key != "operations"})
            or any(_uncertain_evidence(row, allow_revision_budget=operation["tool"] in _PROGRAM_TOOLS)
                   for operation, row in zip(operations, rows))):
        return result
    if completions is not None and len(completions) != len(rows):
        return result
    proofs = completions if completions is not None else [None] * len(rows)
    if any(incomplete_verification(row, proof) for row, proof in zip(rows, proofs)):
        return result
    shown = [present_program_checks(operation["tool"], row, completion=proof)
             for operation, row, proof in zip(operations, rows, proofs)]
    return dict(result, operations=shown) if any(after is not before for before, after in zip(rows, shown)) else result


def compact_receipt(tool, result):
    """What a later turn needs from a host-certified program result, or None.

    Used for the shared-history handoff of a plan whose completion the host
    composed (``certified_completion``): the host's summary row already states
    the outcome and each check's observed values, so the handoff keeps only
    the identities a next plan binds with (post-commit handles and revisions,
    preview and view IDs), the changed object names and the check count. The
    gates match ``_certified_program``; anything else (a failure, a partial or
    uncertain outcome, missing or non-positive checks) returns None and the
    full result stays in the handoff for the model to reason about.
    """
    if _clean_program_checks(tool, result) is None:
        return None
    status = result["status"]
    # The host summary that follows states the Undo step and every check.
    receipt = {"status": status}
    if result.get("preview_id"):
        receipt["preview_id"] = result["preview_id"]
    live = result.get("live_revisions")
    if isinstance(live, dict):
        objects, truncated = _receipt_rows(live.get("objects"), _RECEIPT_REVISION_FIELDS)
        receipt["live_revisions"] = {"status": live.get("status"), "objects": objects}
        if truncated or live.get("truncated"):
            receipt["live_revisions"]["truncated"] = True
    changes = result.get("changes") if status == "committed" else result.get("delta")
    if isinstance(changes, dict):
        summary = {}
        for key, fields in (("added", ("name", "type")), ("modified", ("name", "fields")), ("removed", ("name", "type"))):
            rows, truncated = _receipt_rows(changes.get(key), fields)
            if rows:
                summary[key] = rows
            if truncated:
                summary["truncated"] = True
        if changes.get("truncated"):
            summary["truncated"] = True
        receipt["changes"] = summary
    views, _ = _receipt_rows(result.get("views"), ("id", "view_id", "name"))
    if views:
        receipt["views"] = views
    if _shared_edits(result):
        receipt["shared_edits"] = [dict(row, users=row["users"][:8]) for row in _shared_edits(result)[:4]]
    return dict(receipt, **commit_facts(result))


def _terse_values(kind, observed):
    """A passed check's observed values, without what its passing already implies.

    ``true`` flags and the echoed ``space``/``scope`` are left out, and a value
    named like its check (``dimensions`` of a dimensions check, the ``name``
    of an object_name check) is shown without repeating the name.
    """
    values = []
    for key, value in (observed.items() if isinstance(observed, dict) else ()):
        if key in ("scope", "space") or value is True:
            continue
        text = _value(value)
        if text is not None:
            values.append(text if key == kind or str(kind).endswith("_" + key) else key + " " + text)
    return values


def terse_receipt(tool, result, *, accepted=False, observed=None):
    """One handoff entry for a host-certified result: a verified line plus what a next plan needs.

    Replaces, for a Claude context, the pair of a compact receipt and the
    host's multi-line summary row (``compact_receipt``, ``certified_completion``):
    the same gates apply, so None means the full result stays in the handoff.
    ``verified`` states the outcome, the changed objects and every check with
    the values it observed. The entry keeps a preview's ID and view IDs, and
    the post-commit ``live`` rows (handle, name, revision) except those the
    same message's observation already shows with the same revision:
    ``observed(handle)`` returns that revision.
    """
    receipt = compact_receipt(tool, result)
    if receipt is None:
        return None
    rows = [row for check in result["checks"] for row in check["checks"]]
    labels = _labels(result)
    targets = {labels.get(row.get("target")) for row in rows}
    lines = []
    for row in rows[:_MAX_CHECK_LINES]:
        target = labels.get(row.get("target"))
        values = _terse_values(row.get("kind"), row.get("observed"))
        lines.append(_display(row.get("kind", "check"), 32) + (" '" + _display(target) + "'" if target and len(targets) > 1 else "")
                     + (" " + ", ".join(values) if values else ""))
    if len(rows) > _MAX_CHECK_LINES:
        lines.append("and %d more" % (len(rows) - _MAX_CHECK_LINES))
    if receipt["status"] == "preview":
        head = "PREVIEW staged; live scene unchanged until Accept; staged " + _changes(result)
        where = "on the staged copy"
    else:
        head = (("Accepted preview, committed" if accepted or tool == "angelo_accept_preview" else "Committed")
                + " (one Undo step): " + _changes(result))
        where = "live"
    entry = {"verified": head + ". Checks passed %s (%d/%d): " % (where, len(rows), len(rows)) + "; ".join(lines)}
    for key in ("preview_id", "views", "isolated_shared_data"):
        if key in receipt:
            entry[key] = receipt[key]
    live = receipt.get("live_revisions") or {}
    kept = live_rows(live.get("objects", ()), observed)  # A revision left out keeps its handle and reason.
    if kept:
        entry["live"] = kept
    if live.get("truncated"):
        entry["live_truncated"] = True
    return entry


def correctable_failure(operations, results):
    """The error to return to the model for one corrected plan, or None.

    A program that failed on its isolated stage, before any live write, is like
    a plan the host rejected: nothing changed, so the model may submit a
    corrected plan (it is approved again) instead of the request ending as
    ``interrupted`` (otherwise, when a rebased context guessed a modifier
    name, the stage rejected it and the whole request failed). Never for a result
    that could have changed the scene, needs cleanup, was declined, expired or
    stopped, and never after an earlier operation of the same plan changed the
    scene. A stale refusal that changed nothing qualifies with its recovery
    (``request_recovery``): the targets to read again before binding them.
    """
    if not operations or len(results) != len(operations) or not results:
        return None
    for result in results[:-1]:
        if (not isinstance(result, dict) or result.get("scene_changed") is True
                or result.get("partial_changes_possible") or result.get("mutation_started")):
            return None
    failed, tool = results[-1], operations[-1]["tool"]
    # A cube/location batch qualifies only when its refusal says nothing ran
    # (otherwise a batch refused at dispatch ended the request). A
    # background-job call does too: refused at admission, by Blender at its
    # start or as a cancel, it started nothing (job_host).
    if tool not in _UNCHANGED_WHEN_REFUSED or not isinstance(failed, dict):
        return None
    error = failed.get("error")
    from .request_recovery import unchanged
    if (not isinstance(error, str) or not unchanged(failed) or failed.get("interrupted") or failed.get("expired")
            or failed.get("context_changed")):
        return None
    from .refusal_codes import CORRECTABLE_CODES, STALE_CODES, refusal_code
    code = refusal_code(failed)
    if code in STALE_CODES:  # Nothing changed; its recovery names the targets to read again (request_recovery).
        recovery = failed.get("recovery_requirements", failed.get("recovery"))
        return error + ("\nRecovery: " + json.dumps(recovery, separators=(",", ":")) if recovery else "")
    return error if code in CORRECTABLE_CODES else None
