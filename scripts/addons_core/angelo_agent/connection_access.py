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
A mode's own legacy key (a chat with no connection record, ``mode_key_usable``)
is a personal API key too, refused by the same rule, in the window and for
worker chats alike (enterprise_policy.rst: no own API keys while personal
connections are off, no AI connection before a required team sign-in). A
policy known to forbid them also ends what already runs on them
(``end_refused``), as a removal does; an account that is only offline,
restoring or signing in refuses launches and consent but ends nothing, as a key
Angelo does not hold this moment reads as unknown, never as a mismatch.
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
    policy = managed_policy.current()   # The team is asked only when the policy requires its sign-in.
    return managed_policy.personal_refusal(policy, policy.require_team_sign_in and _team().signed_in()) is None


def personal_forbidden():
    """Whether the policy is known to forbid personal connections now: turned off, or a required team sign-in while
    the person is signed out (no account to sign in with counts so). Offline, restoring or signing in is not known
    to be signed out: ``personal_allowed`` refuses launches and consent then, but nothing running ends for it."""
    policy = managed_policy.current()
    if not policy.allow_personal_connections:
        return True
    if not policy.require_team_sign_in:
        return False
    from .team_view import SIGNED_OUT
    account = _team().account()
    return account is None or account.view().status == SIGNED_OUT


def mode_key_usable(mode, keys=None):
    """Whether a chat with no connection record may run on ``mode``'s own legacy key (``keys``: the window's
    ``_memory_keys``): a personal API key, so only while the policy allows personal connections."""
    from .native_suggestions import LEGACY_KEY_MODES
    keys = _ui()._memory_keys if keys is None else keys
    return mode in LEGACY_KEY_MODES and bool(keys.get(mode)) and personal_allowed()


def end_refused(ui):
    """While the policy is known to forbid personal connections (``personal_forbidden``), what runs on them ends, as
    a removed connection's does: every worker's personal children end now, a closed chat's and a mode's own key's
    included (``runtime_bindings.end_personal``, which needs no record), and the window's shared app-servers of each
    stored one and of the legacy key modes retire (``codex_runtime``). Idempotent; how many children ended. Called
    wherever the effective policy can change (``team_account_ui._pump``: an organization's policy arriving or
    changing, a sign-in, a sign-out); a launch or a consent is already refused (``find_connection``,
    ``mode_key_usable``)."""
    if not personal_forbidden():
        return 0
    from . import codex_runtime, runtime_bindings
    from .native_suggestions import LEGACY_KEY_MODES
    ended = runtime_bindings.end_personal()
    for identity in list(ui.connections().records):
        codex_runtime.retire_connection(identity)
    for mode in sorted(LEGACY_KEY_MODES):
        codex_runtime.retire_mode(mode)
    return ended


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
