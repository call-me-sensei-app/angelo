# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed refusal policy. Human prose never determines retry authority."""

CODES = frozenset({
    "STALE_SETTING", "STALE_REVISION", "STALE_DEPENDENCY", "STALE_CONTEXT", "FRAME_CHANGED",
    "EXPIRED", "OWNERSHIP", "CANCELLED", "READ_ONLY", "DECLINED", "VALIDATION", "CHECK_FAILED",
    "UNSUPPORTED", "LIMIT_EXCEEDED", "INTERNAL_ERROR", "CLEANUP_FAILED", "WRITER_BUSY", "UNKNOWN",
    "RECOVERY_REQUIRED",
})
STALE_CODES = frozenset({"STALE_SETTING", "STALE_REVISION", "STALE_DEPENDENCY", "STALE_CONTEXT"})
CORRECTABLE_CODES = frozenset({"VALIDATION", "CHECK_FAILED", "UNSUPPORTED", "LIMIT_EXCEEDED"})


class RefusalError(ValueError):
    """A host refusal with an explicit closed code and optional factual details."""

    def __init__(self, message, *, code="UNKNOWN", details=None):
        if code not in CODES:
            raise ValueError("Unknown refusal code")
        super().__init__(message)
        self.code = code
        self.details = dict(details) if isinstance(details, dict) else {}


def refusal_code(error):
    """Unknown exceptions fail closed; never infer safety from their wording."""
    value = error.get("error_code") if isinstance(error, dict) else getattr(error, "refusal_code", getattr(error, "code", None))
    return value if isinstance(value, str) and value in CODES else "UNKNOWN"


def refusal_details(error):
    value = error.get("refusal_details") if isinstance(error, dict) else getattr(error, "details", None)
    return dict(value) if isinstance(value, dict) else {}


def error_result(error, **fields):
    """Preserve structured authority when a native/controller owner wraps an error."""
    result = {"error": str(error)[:2000], "error_code": refusal_code(error), **fields}
    if (getattr(error, "partial_changes_possible", True) is False and "scene_changed" not in result
            and result["error_code"] not in {"UNKNOWN", "INTERNAL_ERROR"}):
        # A classified preflight refusal (sculpt_preflight, job and library preflights): nothing ran, so it says
        # so, and the request's other edits are not held (request_recovery.unchanged).
        result["scene_changed"] = False
    if result["error_code"] in {"UNKNOWN", "INTERNAL_ERROR"} and result.get("scene_changed") is False:
        result.pop("scene_changed")
        result["partial_changes_possible"] = True
    details = refusal_details(error)
    if details:
        result["refusal_details"] = details
    return result
