# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread glue for custom model endpoints (an organization's own model server) in AI Providers.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints". ``ui`` stays the
owner of connections, in-memory keys, catalogs and controllers, and
``team_account_ui`` of the account. This module owns:

* the endpoint connections (``endpoint_connections.records``), virtual like team
  ones, recomputed when the account or the policy changes (``refresh``);
* each person's key for each endpoint, one per endpoint and shared by its formats:
  typed into AI Providers (``set_key``), kept in the OS key store through the same
  ``saved_keys`` worker as personal API keys under the endpoint's own key slot
  (``endpoint_connections.key_identity``), and mirrored into ``ui._memory_keys``
  for each of the endpoint's connections, so chats, catalogs and worker launches
  pass it to the runtime exactly as an API key (``replace_connection_key``);
* readiness and status (``ready``, ``reason``, ``status``) and the explicit
  Test connection (``endpoint_check``), run off the main thread.

Nothing here does network or key-store I/O on the main thread; draw code only reads.
"""

import threading
import time

from . import endpoint_connections, endpoint_keys, endpoint_routes
from .display_text import Text
from .endpoint_connections import account_admits, key_identity, needs_key
from .ui_text import n_

SIGN_IN = n_("Sign in to your Angelo team to use your organization's model server")
from .endpoint_connections import NO_SEAT  # noqa: E402 (re-exported: the one sentence for the seat rule)
NEEDS_KEY = n_("Add your key for {endpoint}")
LOADING_KEY = n_("Loading your saved key…")
UNCONFIRMED = n_("Couldn't reach your Angelo team to confirm your seat. Your organization's model server works again "
                 "once Angelo reaches it.")
# A key program still running: the page's own line for that credential (providers_page_endpoints).
COMMAND_PENDING = n_("Gets a short-lived key from your organization's sign-in program")
from .endpoint_routes import MOVED  # noqa: E402 (re-exported: a kept key's route changed, endpoint_routes)
READY = n_("Available in chat")
LEFT_OUT = n_("Some of your organization's model servers are described in a way Angelo can't use ({count} left out). "
              "Contact your admin.")
KEY_LABEL = "Angelo organization endpoint key"   # i18n: label of the OS key-store item; product and kind only.

_records = {}        # Current endpoint connections by id.
_keys = {}           # Endpoint id -> the person's key, in memory.
_loading = set()     # Endpoint ids whose saved key is being read.
_attempted = set()   # Endpoint ids whose saved key was asked for in this scope (once, without a prompt).
_scope = None        # (console url, org id, user id) the keys belong to.
_source = ("", 0)    # (where the endpoints came from: "policy" or "console", how many entries were left out).
_view = None
_checks = {}         # Endpoint id -> endpoint_check.Report (the last Test connection).
_running = set()     # Endpoint ids whose Test connection runs.
_finished = []       # (endpoint id, report) from the check threads, applied by ``pump``.
_key_problems = {}   # Endpoint id -> why its key program gave none (endpoint_keys).
_admitted = False    # Whether the account admitted endpoints at the last ``refresh``.
_withdrawn = set()   # Connection ids ended while the account doesn't admit endpoints (once each, until admitted).
_stale = set()       # Connection ids withdrawn while a chat on them was busy: its runtime ends when the turn ends.
_last = None         # (view, console url, policy) of the last ``refresh``, for the grace's end in ``pump``.
_routes = {}         # Endpoint id -> the route its key in memory was given for (endpoint_routes.route).
_moved = {}          # Endpoint id -> why a member's kept key was dropped (a route change), until they enter it again.
_lock = threading.Lock()


def _ui():
    from . import ui
    return ui


def records():
    return dict(_records)


def find(identity):
    return _records.get(identity)


def endpoint_of(record):
    return endpoint_connections.find(record["endpoint"]) if record else None


def source():
    return _source


def key_slot(endpoint_id):
    if _scope is None:
        return None
    return key_identity(*_scope, endpoint_id)


def has_key(endpoint_id):
    return bool(_keys.get(endpoint_id))


def refresh(ui, view, console_url, policy, now=None):
    """Recompute the endpoints and their connections for ``view`` and ``policy``; True when anything changed.

    The seat rule applies to what runs, not only to what starts: when the account no longer admits endpoints (a seat
    removed, model use withdrawn, or the console unreachable past ``OFFLINE_GRACE``), every endpoint connection's
    key leaves the runtimes and what runs on it ends (``_withdraw``), as a team connection's grant key does. The
    person's keys stay in memory for this account, so a seat given back (or the console reached again) restores
    them. Signing out forgets them."""
    global _records, _scope, _source, _view, _admitted, _last
    now = time.monotonic() if now is None else now
    _last = (view, console_url, policy)
    endpoints, where, left_out = endpoint_connections.effective(view, policy)
    _admitted = account_admits(view, now)   # Offline: for OFFLINE_GRACE after the last token that confirmed the seat.
    endpoint_connections.set_current(endpoints, _admitted)
    known = endpoint_connections.account_known(view)
    scope = (console_url, view.org_id, view.user_id) if known else None
    if scope != _scope:   # Another person or organization: the keys in memory were someone else's.
        for endpoint_id in list(_keys):
            _forget_memory(ui, endpoint_id)
        _loading.clear()
        _attempted.clear()
        _checks.clear()
        _key_problems.clear()
        _moved.clear()
        endpoint_keys.forget()
    _scope, _source, _view = scope, (where, left_out), view
    previous = _records
    # Listed whenever signed in, like team connections: the seat decides whether they can be used (``reason``).
    _records = endpoint_connections.records(view, console_url, endpoints)
    changed = previous.keys() != _records.keys()
    for identity in previous.keys() - _records.keys():
        _withdraw(ui, identity)
    present = {e.id for e in endpoints}
    for endpoint_id in [e for e in _keys if e not in present]:
        _forget_memory(ui, endpoint_id)
    for endpoint in endpoints:
        moved = endpoint_routes.change(_routes.get(endpoint.id), endpoint) if endpoint.id in _keys else None
        if moved is not None:
            _route_changed(ui, endpoint, moved)
    if _admitted:
        _withdrawn.clear()
        for endpoint_id in present & set(_keys):
            _mirror(ui, endpoint_id)
    else:   # No seat (or no confirmation of one): nothing more runs on any of these connections, keyed or not.
        for identity in [i for i in _records if i not in _withdrawn]:
            _withdraw(ui, identity)
            _withdrawn.add(identity)
    _load_missing()
    _source_keys()
    return changed


def grace_ended(now=None):
    """Whether an offline account's grace has run out since the last ``refresh`` (``pump`` refreshes then)."""
    now = time.monotonic() if now is None else now
    return _admitted and _last is not None and not account_admits(_last[0], now)


def _route_changed(ui, endpoint, why):
    """An endpoint's route changed (``endpoint_routes.change``): a key kept for the old one is never sent along the
    new one. A member enters theirs again (``why`` names the change); a program's key is made again for it."""
    _forget_memory(ui, endpoint.id)
    _key_problems.pop(endpoint.id, None)
    endpoint_keys.forget(endpoint.id)
    if endpoint.credential == "member_key":
        _moved[endpoint.id] = why


def _source_keys():
    """Run each key program once per scope and again when due (``endpoint_keys``: before its key lapses)."""
    if not _admitted:
        return   # No seat: no key is read, fetched or made.
    for endpoint_id in {r["endpoint"] for r in _records.values()}:
        endpoint = endpoint_connections.find(endpoint_id)
        if endpoint is None or endpoint_keys.running(endpoint_id):
            continue
        never = endpoint_id not in _keys and endpoint_id not in _key_problems   # Not asked yet in this scope.
        if endpoint.credential == "command" and (never or endpoint_keys.due(endpoint_id)):
            endpoint_keys.run_command(endpoint)


def _apply_sourced(ui):
    """Keys the key programs gave, applied as typed ones; True when anything changed."""
    finished = endpoint_keys.take_finished()
    for endpoint_id, key, problem, _lifetime in finished:
        if endpoint_connections.find(endpoint_id) is None:
            continue
        if key:
            _key_problems.pop(endpoint_id, None)
            if _keys.get(endpoint_id) != key:
                try:
                    _apply(ui, endpoint_id, key)
                except (ValueError, RuntimeError) as error:
                    _key_problems[endpoint_id] = error.args[0] if error.args else ""
        else:
            _key_problems[endpoint_id] = problem
    return bool(finished)


def _load_missing():
    """Ask the key store once for each usable endpoint's saved key the person hasn't typed in this session."""
    if not _admitted:
        return   # No seat: no key is read, fetched or made.
    for endpoint_id in {r["endpoint"] for r in _records.values()} - set(_keys) - _attempted:
        endpoint = endpoint_connections.find(endpoint_id)
        if endpoint is None or endpoint.credential != "member_key":
            continue   # Only a member's own key is kept in the key store.
        if _load_saved(endpoint_id):
            _attempted.add(endpoint_id)


def _endpoint_records(endpoint_id):
    return [r for r in _records.values() if r["endpoint"] == endpoint_id]


def _mirror(ui, endpoint_id):
    """Every connection of ``endpoint_id`` holds its key in ``ui._memory_keys`` (as an API key's)."""
    key = _keys[endpoint_id]
    for record in _endpoint_records(endpoint_id):
        if ui._memory_keys.get(record["id"]) != key:
            try:
                ui.replace_connection_key(record, key)
            except (ValueError, RuntimeError):
                pass   # A turn runs on it: it keeps the key it started with; the next refresh applies this one.


def _withdraw(ui, identity):
    """A connection that is gone: its key leaves memory and what runs on it ends (``runtime_bindings``), a
    consultation any chat runs on it included (its call ends cancelled). A busy chat's turn keeps a redaction-only
    copy of the key until its runtime ends (``activity_rows.redaction_keys``)."""
    from .runtime_bindings import end_connection
    ui._memory_keys.pop(identity, None)
    end_connection(identity)
    withdrawn = "The model server connection was withdrawn; no edits were allowed"   # i18n: for the model.
    for controller in ui._controllers.values():
        for job in [j for j in getattr(controller, "delegate_jobs", {}).values() if j.connection_id == identity]:
            job.cancel(withdrawn)
        if controller.state.get("connection_id") != identity:
            continue
        controller.api_key = ""   # Busy or not: no later request of this chat starts with it.
        if controller.busy:   # The running turn finishes on the process it started; that process ends with it.
            _stale.add(identity)
        elif controller.provider is not None:
            threading.Thread(target=controller.provider.close, daemon=True).start()
            controller.provider = None


def _end_stale(ui):
    """Close the runtime of each chat whose connection was withdrawn during its turn, once the turn has ended."""
    for identity in list(_stale):
        waiting = False
        for controller in ui._controllers.values():
            if controller.state.get("connection_id") != identity:
                continue
            if controller.busy:
                waiting = True
            elif controller.provider is not None:
                threading.Thread(target=controller.provider.close, daemon=True).start()
                controller.provider = None
        if not waiting:
            _stale.discard(identity)


def send_refusal(mode, connection_id):
    """Why a request may not start on endpoint connection ``connection_id`` now (a ``Text``), or None; None for any
    other mode. Angelo's own process only: a worker chat's runtime is launched and ended by Angelo (runtime_relay)."""
    if not endpoint_connections.is_endpoint(mode):
        return None
    from . import runtime_relay
    if runtime_relay.relayed():
        return None
    record = _records.get(connection_id)
    return (reason(record) or None) if record is not None else Text(endpoint_connections.REMOVED)


def _forget_memory(ui, endpoint_id):
    _keys.pop(endpoint_id, None)
    _routes.pop(endpoint_id, None)
    for record in _endpoint_records(endpoint_id):
        _withdraw(ui, record["id"])


def _vault():
    from . import saved_keys_ui
    return saved_keys_ui._vault


def _load_saved(endpoint_id):
    """Queue a prompt-free read of the saved key (the ``saved_keys`` worker); ``key_loaded`` applies it."""
    vault, slot = _vault(), key_slot(endpoint_id)
    if vault is None or slot is None or vault.busy(slot):
        return False   # No store yet (it opens after the account), or busy: asked again at the next pump.
    if vault.stored.get(slot) is not False:
        _loading.add(endpoint_id)
        vault.load(slot)
    return True


def key_loaded(ui, identity, kind, outcome, secret):
    """A finished key-store operation for ``identity``; True when it was an endpoint's key slot (handled here)."""
    endpoint_id = next((e.id for e in endpoint_connections.current() if key_slot(e.id) == identity), None)
    if endpoint_id is None:
        return False
    if kind == "load":
        _loading.discard(endpoint_id)
        endpoint = endpoint_connections.find(endpoint_id)
        if outcome == "loaded" and secret and not _keys.get(endpoint_id):
            kept, key = endpoint_routes.read(secret)
            why = endpoint_routes.change(kept, endpoint)
            if key is None or why is not None:   # Saved for another route (or unreadable): never sent along this one.
                _moved[endpoint_id] = why or Text(NEEDS_KEY, endpoint=endpoint.name)
                return True
            try:
                _apply(ui, endpoint_id, key)
            except (ValueError, RuntimeError):
                pass   # A chat on it is busy, or the saved value is no key: the person types it again.
    return True


def stored_value(secret):
    """``(route, key)`` of a saved member key (``set_key`` keeps both), or ``(None, None)`` (``endpoint_routes``)."""
    return endpoint_routes.read(secret)


def _apply(ui, endpoint_id, secret):
    from .api_connections import clean_key
    secret = clean_key(secret)
    for record in _endpoint_records(endpoint_id):
        if ui.connection_in_use(record["id"]):
            raise RuntimeError(Text(n_("Stop this connection's active turns before replacing its key")))
    _keys[endpoint_id] = secret
    _routes[endpoint_id] = endpoint_routes.route(endpoint_connections.find(endpoint_id))   # The only route it takes.
    _mirror(ui, endpoint_id)
    _checks.pop(endpoint_id, None)   # A check of the previous key says nothing about this one.


def set_key(endpoint_id, secret, remember=True):
    """The person typed their key for ``endpoint_id``: used now, and kept in the OS key store when ``remember``."""
    ui = _ui()
    if endpoint_connections.find(endpoint_id) is None or _scope is None:
        raise ValueError(Text(endpoint_connections.REMOVED))
    _apply(ui, endpoint_id, secret)
    _moved.pop(endpoint_id, None)
    vault, slot = _vault(), key_slot(endpoint_id)
    if vault is not None:
        if remember:   # Kept with the route it was entered for (``endpoint_routes``, ``_route_changed``).
            vault.save(slot, endpoint_routes.stored(_routes[endpoint_id], _keys[endpoint_id]), KEY_LABEL)
        elif vault.stored.get(slot) is not False:
            vault.forget(slot)


def forget_key(endpoint_id):
    """Remove the person's key for ``endpoint_id`` from memory and the OS key store."""
    ui = _ui()
    _forget_memory(ui, endpoint_id)
    _checks.pop(endpoint_id, None)
    vault, slot = _vault(), key_slot(endpoint_id)
    if vault is not None and slot is not None:
        vault.forget(slot)


def stored_state(endpoint_id):
    """``(kind, outcome)`` of the key slot's last key-store operation, or (None, None)."""
    vault, slot = _vault(), key_slot(endpoint_id)
    if vault is None or slot is None:
        return None, None
    return vault.state.get(slot, (None, None))


def reason(record):
    """Why this endpoint connection can't be used now, or ""."""
    if not endpoint_connections.account_known(_view):
        return Text(SIGN_IN)
    if not _admitted:
        return Text(UNCONFIRMED) if not _view.signed_in else Text(NO_SEAT)
    endpoint = endpoint_of(record)
    if endpoint is None:
        return Text(endpoint_connections.REMOVED)
    if not needs_key(endpoint.credential) or _keys.get(endpoint.id):
        return ""
    if endpoint.credential == "member_key":
        if endpoint.id in _moved:
            return _moved[endpoint.id]
        return Text(LOADING_KEY) if endpoint.id in _loading else Text(NEEDS_KEY, endpoint=endpoint.name)
    return _key_problems.get(endpoint.id) or Text(COMMAND_PENDING)


def ready(record):
    """Usable now: admitted, offered, and holding its key (a server that checks none needs none)."""
    if reason(record):
        return False
    endpoint = endpoint_of(record)
    return not needs_key(endpoint.credential) or bool(_ui()._memory_keys.get(record["id"]))


def status(record):
    return reason(record) or Text(READY)


def start_check(endpoint_id):
    """Run Test connection for ``endpoint_id`` with the person's key, off the main thread."""
    from . import endpoint_check
    endpoint, key = endpoint_connections.find(endpoint_id), _keys.get(endpoint_id)
    if endpoint is None:
        raise ValueError(Text(endpoint_connections.REMOVED))
    if not key and needs_key(endpoint.credential):
        raise ValueError(Text(NEEDS_KEY, endpoint=endpoint.name) if endpoint.credential == "member_key"
                         else _key_problems.get(endpoint_id) or Text(COMMAND_PENDING))
    if endpoint_id in _running:
        return False
    _running.add(endpoint_id)

    def run():
        try:
            report = endpoint_check.run(endpoint, key or "", shapes=_tool_shapes(endpoint))
        except Exception as error:  # noqa: BLE001 - reported as the check's own failure, never a crash.
            report = endpoint_check.failed(str(error).replace(key, "[REDACTED]") if key else str(error))
        with _lock:
            _finished.append((endpoint_id, report))

    threading.Thread(target=run, daemon=True, name="AngeloEndpointCheck").start()
    return True


def _tool_shapes(endpoint):
    """Each Responses model's tool shape, from the agent runtime's bundled catalog (off the main thread); none known
    (no runtime, no catalog) means function tools, as the chat itself would then send. A runtime C refuses to start
    raises: the check fails with the refusal, never a guess at shapes."""
    import shutil
    from . import agent_runtime, endpoint_catalog, endpoint_check_tools, private_roots
    try:
        executable = agent_runtime.executable()
    except Exception:  # noqa: BLE001 - a misconfigured runtime says so where a chat starts; the check goes on.
        executable = None
    if not executable or not endpoint.models_in("openai-responses"):
        return {}
    home = private_roots.allocate("angelo-endpoint-check-")   # An isolated Codex profile to list the catalog.
    try:
        return endpoint_check_tools.shapes(endpoint, endpoint_catalog.bundled(executable, home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def checking(endpoint_id):
    return endpoint_id in _running


def check_report(endpoint_id):
    return _checks.get(endpoint_id)


def pump():
    """Apply finished checks on the main thread; True when anything changed."""
    if grace_ended() and _last is not None:   # The console stayed unreachable: the seat no longer counts.
        refresh(_ui(), *_last)
    if _stale:
        _end_stale(_ui())
    _load_missing()
    sourced = _apply_sourced(_ui()) if _scope is not None else False
    _source_keys()
    with _lock:
        finished, _finished[:] = list(_finished), []
    for endpoint_id, report in finished:
        _running.discard(endpoint_id)
        if endpoint_connections.find(endpoint_id) is not None:
            _checks[endpoint_id] = report
    return bool(finished) or sourced


def close():
    global _records, _scope, _view, _source, _admitted, _last
    _records, _scope, _view, _source = {}, None, None, ("", 0)
    _admitted, _last = False, None
    _withdrawn.clear()
    _stale.clear()
    _keys.clear()
    _loading.clear()
    _attempted.clear()
    _checks.clear()
    _key_problems.clear()
    endpoint_keys.forget()
    endpoint_connections.set_current(())
