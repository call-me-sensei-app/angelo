# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private immutable obligations across approval, jobs, previews and completion.

The job independently resolves a native binding before allocating a stage.
JSON check lists are presentation copies, never an authority to replace the
frozen bundle. Dependency-free backends use the proven pure resolver only.
"""

from .program_jobs import ProgramError


def assert_bundle(bundle, program):
    from .program_checks import FrozenObligations
    if not isinstance(bundle, FrozenObligations):
        raise ProgramError("The program has no immutable verification obligations", code="INTERNAL_ERROR")
    try:
        bundle.assert_integrity(program)
    except ValueError as error:
        raise ProgramError("The program's verification obligations changed; stage a fresh program", code="OWNERSHIP") from error
    return bundle


def initial_obligations(program, preview=None, approved=None):
    from .program_checks import NeedsMaterialBinding, resolve_obligations
    if preview is not None:
        bundle = assert_bundle(preview.get("obligations"), program)
        assert_preview(preview)
        return bundle
    if approved is not None:
        return assert_bundle(approved, program)
    try:
        return resolve_obligations(program)
    except NeedsMaterialBinding:
        return None  # The authoritative native bind completes admission before stage allocation.


def bound_obligations(program, binding, approved=None):
    from .program_checks import resolve_obligations
    fresh = binding.get("obligations") if isinstance(binding, dict) else None
    fresh = assert_bundle(fresh if fresh is not None else resolve_obligations(program), program)
    if approved is not None and fresh.identity != assert_bundle(approved, program).identity:
        raise ProgramError("The program's bound verification changed after approval; review and stage it again",
                           code="STALE_DEPENDENCY", details={"opaque": True})
    return fresh


def assert_checks(bundle, program, checks, digest):
    from .program_checks import checks_digest
    assert_bundle(bundle, program)
    if bundle.checks_digest != digest or checks_digest(checks) != digest:
        raise ProgramError("The program's verification obligations changed; stage a fresh program", code="OWNERSHIP")


def assert_preview(record):
    bundle = assert_bundle(record.get("obligations"), record["program"])
    assert_checks(bundle, record["program"], record.get("checks", []), record.get("checks_digest"))
    bound = record["binding"].get("obligations")
    if bound is not None and bound.identity != bundle.identity:
        raise ProgramError("The preview's bound verification changed; stage a fresh preview", code="OWNERSHIP")
    return bundle


def completion_obligations(job):
    """Evidence is available only from the actual finished, fully checked job."""
    result = job.result
    if (not job.closed or job.phase not in {"COMMITTED", "PREVIEW"} or not isinstance(result, dict)
            or result.get("success") is not True or result.get("final") is not True
            or any(result.get(key) for key in ("partial_changes_possible", "cleanup_error", "cleanup_pending", "cancelled"))):
        return None
    job._check_obligations()
    if (len(job.check_results) != len(job.checks) or result.get("checks") != job.check_results
            or not all(row.get("passed") is True for row in job.check_results)):
        return None
    return job.obligations


def summarize_checks(job):
    """Known checks remain mandatory; opaque effects never become a complete certificate."""
    unobservable = sorted(set(getattr(job, "unobservable", ())) | set(job.result.get("unobservable_checks", ())))
    unknowns = job.obligations.unknowns() + [
        {"operation": None, "root": {"target": job.checks[index].get("target")}, "reason": "unobservable_check",
         "affected": [{"kind": job.checks[index].get("kind"), "check": index}]}
        for index in unobservable if index < len(job.checks)] + list(job.result.get("unverified_effects", ()))
    if unknowns:
        job.result.pop("check_summary", None)
        job.result["verification"] = {"status": "partial", "unknowns": unknowns,
                                      "known_required": len(job.checks), "known_checked": len(job.check_results)}
        return
    if len(job.checks) > 32:
        job.result["check_summary"] = {
            "required": len(job.checks), "checked": len(job.check_results),
            "passed": sum(row.get("passed") is True for row in job.check_results),
            "checks_digest": job.checks_digest}
