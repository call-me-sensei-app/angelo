# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Content-free receipt classes from closed refusal codes, never error prose."""

from .refusal_codes import STALE_CODES, refusal_code

# Few and short: every allowlisted count can appear in each of 256 retained
# receipts, inside the receipt file's fixed byte budget (telemetry).
CODES = ("frame", "binding", "check", "modifier", "expired", "script", "other")


def failure_code(result):
    if not isinstance(result, dict):
        return "other"
    code = refusal_code(result)
    if code == "FRAME_CHANGED" or result.get("context_changed") == "frame":
        return "frame"
    if code == "EXPIRED" or result.get("expired") is True:
        return "expired"
    if result.get("script_error") is True:
        return "script"  # The executor's own account, never a guess from error prose.
    if code in STALE_CODES or code == "RECOVERY_REQUIRED":
        return "binding"
    if code == "CHECK_FAILED":
        return "check"
    return "other"
