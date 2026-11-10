# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative native irreversible-recovery capability boundary.

Lifecycle/status scaffolding is not recovery authority. The actual capture/preimage,
checked restoration and fresh terminal effect producer must all exist before a caller
may enter an irreversible native operation. No production consumer is enabled here.
"""

from typing import NamedTuple

from .program_jobs import ProgramError

SCHEMA_VERSION = 1
OBSERVER_LIMIT = 16
READY_FIELDS = ("identity_binding_ready", "exact_preimage_ready", "checked_restore_ready",
                "terminal_effect_seal_ready")
REQUIRED_METHODS = ("prepare_recovery", "recovery_status", "seal_recovery_effects")


class Capabilities(NamedTuple):
    identity_binding_ready: bool
    exact_preimage_ready: bool
    checked_restore_ready: bool
    terminal_effect_seal_ready: bool


def native_bridge():
    try:
        from _bpy import _angelo_undo
    except ImportError:
        return None
    return _angelo_undo


def capabilities(bridge):
    """Strict native declarations, or None; never coerce malformed readiness values."""
    read = getattr(bridge, "recovery_capabilities", None)
    if not callable(read):
        return None
    try:
        values = read()
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    if (type(values) is not dict or type(values.get("schema_version")) is not int
            or values["schema_version"] != SCHEMA_VERSION
            or type(values.get("observer_limit")) is not int or values["observer_limit"] != OBSERVER_LIMIT
            or any(type(values.get(field)) is not bool for field in READY_FIELDS)):
        return None
    return Capabilities(*(values[field] for field in READY_FIELDS))


def require_ready(bridge=None):
    """Refuse missing/older/incomplete native support before any effects or snapshot authority."""
    bridge = native_bridge() if bridge is None else bridge
    ready = capabilities(bridge)
    if (ready is None or not all(ready)
            or any(not callable(getattr(bridge, method, None)) for method in REQUIRED_METHODS)):
        raise ProgramError("This binary cannot certify irreversible native recovery. Nothing was applied; "
                           "use a build with complete native recovery support before retiring self-linked data.",
                           code="UNSUPPORTED")
    return bridge
