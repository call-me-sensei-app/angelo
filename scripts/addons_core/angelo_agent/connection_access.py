# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Connection lookups across the stored connections and the signed-in team, and Media Studio bindings.

Split from ``ui`` (which re-exports every name here, so ``ui.find_connection``
and the others keep working). Stored connections come from ``ui.connections()``
and their keys from ``ui._memory_keys``; team connections are virtual and come
from ``team_account_ui`` (never stored). ``_ui`` and ``_team`` resolve those
modules at call time, which keeps this module importable without Blender.

The managed policy is applied here, where chats, collaborators, Media Studio
and Flows look connections up: while it refuses personal connections
(``managed_policy.personal_refusal``) they are not found at all, so nothing
can route to them; they stay stored and come back when the policy allows.
"""

from . import managed_policy
from .provider_registry import is_team


def _ui():
    from . import ui
    return ui


def _team():
    from . import team_account_ui
    return team_account_ui


def personal_allowed():
    """Whether the managed policy lets stored (personal) connections be used now."""
    return managed_policy.personal_refusal(managed_policy.current(), _team().signed_in()) is None


def collaborators_allowed():
    return managed_policy.current().allow_collaborators


def connection_records():
    """Stored connections the policy allows, plus the signed-in team's virtual ones."""
    return {**(_ui().connections().records if personal_allowed() else {}), **_team().records()}


def find_connection(identity):
    record = _ui().connections().records.get(identity) if personal_allowed() else None
    return record or _team().find(identity)


def generation_health():
    """Connection health for Media Studio, by id: API-key checks and usable team connections."""
    return {**_ui()._api_connections.results, **_team().health_results()}


def generation_binding(connection_id):
    from .generative import bind_generation
    record = find_connection(connection_id)
    if not record:
        raise ValueError("The generation connection is unavailable; open AI Providers")
    if is_team(record["mode"]):
        return _team().binding(record)
    ui = _ui()
    return bind_generation(record, ui._memory_keys.get(connection_id, ""), ui._api_connections.results.get(connection_id))


def gemini_text_ready(record):
    """Whether AI Enhance can use this Gemini connection (a personal key, or a usable team one)."""
    if record["mode"] == "gemini_images_api":
        return bool(_ui()._memory_keys.get(record["id"]))
    return record["mode"] == "gemini_images_team" and _team().ready(record)


def gemini_text_binding(record):
    from .generative import gemini_text_binding as personal
    if is_team(record["mode"]):
        return _team().binding(record)
    return personal(record, _ui()._memory_keys.get(record["id"], ""))


def remember_preferred_mode(mode):
    """The legacy default mode for unbound chats; only a mode the preference enum lists."""
    ui = _ui()
    prefs = ui.preferences()
    if prefs and any(mode == item[0] for item in ui.PROVIDERS):
        prefs.provider = mode
