# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Admitting a test's agent script as Angelo's approval would (shared by the confinement suites)."""


def admit(call_id, text):
    """Admit one run as Angelo's own approval would (the authority's C ledger, v201-local-models): a single-use
    approved digest for this call. Without the authority (this branch alone) every run is admitted already."""
    try:
        import _bpy
        guard = getattr(_bpy, "_angelo_guard", None)
    except ImportError:
        return
    if guard is None or not hasattr(guard, "automatic"):
        return
    import hashlib
    guard.automatic(guard.set, "approved_scripts", call_id, hashlib.sha256(text.encode("utf-8")).hexdigest())
