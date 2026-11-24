# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A custom provider's model catalog for Codex: each model gets the metadata of the model it is.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints" (model profiles).
Codex shapes every request from its catalog entry for the model's name: the
request form (Responses Lite), tool-search support, how much of a tool's output
it keeps and the context window. A server's own name for a model
(``team-gpt-sol``) matches no entry, so Codex would use its generic fallback
(10 KB of tool output, a 272,000-token window, no tool search). When the
organization maps the name to an Angelo model (``custom_endpoints.Model.profile``),
this module copies that model's entry from the runtime's own catalog under the
server's name, writes the catalog into the connection's isolated Codex profile
and passes it as ``model_catalog_json``. Unmapped models stay on what Codex
gives them without a catalog; so does every model when the runtime can't list
its catalog (an older or the person's own Codex): the chat still runs, with
Codex's defaults.

Each catalog is the one of a wire (``wire``): Codex's bundled catalog for its
own Responses wire (``codex debug models --bundled``), and for a wire only
Angelo's runtime has (Gemini) that wire's built-in models, listed offline as
the release check lists them (``scripts/angelo/runtime_wires.py``). A mapped
model's entry comes from the catalog of its profile's own wire
(``provider_registry.registry_wire``), whatever the endpoint's format. A
connection of the registry on such a wire (``gemini_api``) gets a catalog too:
a model the runtime lists keeps its own entry, and one it lacks that runs like
one it lists (``provider_registry.RUNTIME_ENTRY_LIKE``: Gemini 4 Neon like
Carbon, the same API) gets that entry with its own name, efforts and default.
An explicit catalog replaces the provider wire's built-in one, so the written
catalog also carries that wire's own entries under their names (after Angelo's,
which win): a model the server names as the runtime does keeps what it had.

A runtime C refuses to start is no catalog either: the refusal propagates
(``PermissionError``; ``RpcError`` for a command line the start check refuses,
``agent_runtime.check_start``), is never cached, the catalog already written
stays, and the endpoint check reports it. The listing's command line is
``runtime_listing.catalog_listing``, the one the start check admits. The
catalog holds model metadata only, never a key or address. A listing runs
from an empty CODEX_HOME and working folder made for it,
a private scratch folder of its own (``private_roots``, ``LISTING_PREFIX``,
removed after; ``debug models`` has no ``--ignore-user-config``), so no
person's or project's configuration changes it; an administrator's system layer
still applies, as it does to the app-server.

A successful listing is kept per executable (path, size and modification time)
and wire. A failed one is told once and tried again at a launch after
``RETRY_SECONDS``; concurrent first launches share one listing, and writes of
the catalog file never share a temporary file (``write``).

Standard library only; no bpy.
"""

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import child_process, essential_environment, owned_paths, runtime_environment, runtime_listing, shared_replace
from .protocol import RpcError

FILE_NAME = "angelo-endpoint-models.json"
LIST_SECONDS = 20.0
RETRY_SECONDS = 30.0   # A failed listing: the next launch after this lists again (a slow cold start is not forever).
CATALOG_PROVIDER = runtime_listing.CATALOG_PROVIDER   # The throwaway provider a wire's listing names.
LISTING_PREFIX = runtime_listing.LISTING_PREFIX   # private_families: one listing's empty CODEX_HOME and working folder.
# ((path, size, mtime), wire) -> {slug: entry} for a listing that answered, or the monotonic time a listing failed.
_cache = {}
_flights = {}          # key -> _Flight: the listing running now; a concurrent caller waits for its answer.
_told = set()          # (wire, reason) of failures already told.
_lock = threading.Lock()
_writes = threading.Lock()


class _Flight:
    def __init__(self):
        self.done = threading.Event()
        self.found = None
        self.error = None


def _signature(executable):
    try:
        status = os.stat(executable)
    except OSError:
        return None
    return str(executable), status.st_size, status.st_mtime_ns


def _listing(program, wire):
    """The argv that lists the catalog of ``wire`` (None: Codex's own Responses wire, its bundled catalog)."""
    return runtime_listing.catalog_listing(program, wire)


def bundled(executable, home, run=None, wire=None):
    """``{slug: entry}`` of ``executable``'s catalog for ``wire`` (``_listing``); empty when it can't list one.
    ``home``: the caller's isolated Codex profile (the listing makes a folder of its own, ``_list``), kept for the
    callers' contract. ``PermissionError`` when C refuses to start it (``owned_paths.require_runnable``, its message
    saying why), ``RpcError`` when the start check refuses the command line: never cached, never read as "no
    catalog", so a later launch says it again rather than running without one."""
    signature = _signature(executable)
    if signature is None:
        return {}
    key = signature, wire if wire and wire != "responses" else None
    with _lock:
        kept = _cache.get(key)
        if isinstance(kept, dict):
            return kept
        if kept is not None and time.monotonic() - kept < RETRY_SECONDS:
            return {}
        flight = _flights.get(key)
        leading = flight is None
        if leading:
            flight = _flights[key] = _Flight()
    if not leading:   # One listing per key at a time: its answer is this caller's too.
        flight.done.wait(LIST_SECONDS + 10.0)
        if flight.error is not None:
            raise flight.error
        return flight.found or {}
    found, reason = None, "the listing stopped"
    try:
        found, reason = _list(executable, home, run, key[1])
    except (PermissionError, RpcError) as error:   # Refused: the caller reports why (the re-review of 4be92cfb84f, N-2).
        flight.error = error
        raise
    finally:
        with _lock:
            _flights.pop(key, None)
            if found is not None:
                _cache[key] = found
            elif flight.error is None:
                _cache[key] = time.monotonic()
        flight.found = found
        flight.done.set()
    if found is None:
        _tell(key[1], reason)
        return {}
    return found


def _list(executable, home, run, wire):
    """``({slug: entry}, None)``, or ``(None, reason)`` when the runtime gave no catalog."""
    from . import private_roots
    run = run or child_process.run   # Angelo's own runtime: started under its launch rules (child_process).
    try:
        scratch = str(private_roots.allocate(LISTING_PREFIX))   # Empty: nobody's configuration; private, as all scratch.
    except OSError as error:
        return None, "no listing folder (%s)" % type(error).__name__
    try:
        program = str(owned_paths.require_runnable(executable))   # Never a program from state (C).
        completed = run(_listing(program, wire), capture_output=True, timeout=LIST_SECONDS, cwd=scratch,
                        stdin=subprocess.DEVNULL,
                        env=runtime_environment.for_agent_runtime(_quiet_environment(scratch), program))
        if completed.returncode != 0:
            return None, "exit status %s" % completed.returncode
        value = json.loads(completed.stdout)
    except PermissionError:
        raise
    except subprocess.TimeoutExpired:
        return None, "no answer in %d s" % LIST_SECONDS
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return None, type(error).__name__
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    models = value.get("models") if isinstance(value, dict) else None
    if not isinstance(models, list):
        return None, "no model list"
    return {m["slug"]: m for m in models if isinstance(m, dict) and isinstance(m.get("slug"), str)}, None


def _tell(wire, reason):
    """A failed listing, once per wire and reason: its models run on Codex's own defaults until one answers."""
    with _lock:
        if (wire, reason) in _told:
            return
        _told.add((wire, reason))
    print("Angelo: the agent runtime's %s model catalog could not be listed (%s); its models run on Codex's own "
          "defaults until a later launch lists it" % (wire or "Responses", reason))


def _quiet_environment(home):
    """No credential, endpoint or profile of the person's: listing the catalog needs none."""
    return essential_environment.environment(("LC_ALL",), CODEX_HOME=str(home))   # The OS's own variables only.


def _levels(levels, efforts, default):
    """The source's reasoning levels this model offers, each described without another model's claims."""
    return [dict(level, description="Thinking level %s%s" % (level["effort"],
                                                             ", the model's default" if level["effort"] == default else ""))
            for level in levels or () if isinstance(level, dict) and level.get("effort") in efforts]


def entry(identity, catalog):
    """The runtime's entry for Angelo model ``identity``: its own, else (``provider_registry.RUNTIME_ENTRY_LIKE``) the
    entry of the listed model it runs like, under its name, with its efforts and its default (``chat_settings``)."""
    if identity in catalog:
        return catalog[identity]
    from .provider_registry import RUNTIME_ENTRY_LIKE, registry_model
    source, model = catalog.get(RUNTIME_ENTRY_LIKE.get(identity, "")), registry_model(identity)
    if source is None or model is None:
        return None
    from .chat_settings import default_effort
    default = default_effort(model["efforts"], identity)
    return dict(source, slug=identity, display_name=model["name"], description=model["name"],
                default_reasoning_level=default,
                supported_reasoning_levels=_levels(source.get("supported_reasoning_levels"), model["efforts"], default))


def entries(spec, catalogs, own=False):
    """The catalog entries for ``spec``'s models: an endpoint's mapped models (``profile``), each under the server's
    own name; ``own``, a registry connection's models, each under its own ID (``entry``). ``catalogs(wire)`` is the
    runtime's catalog for a wire, so a profile's entry comes from its own wire's; a dict is one catalog for all."""
    from .provider_registry import registry_wire
    if not callable(catalogs):
        catalogs = (lambda one: lambda _wire: one)(catalogs)
    output = []
    for model in spec["models"]:
        identity = model["id"] if own else model.get("profile") or ""
        source = entry(identity, catalogs(registry_wire(identity))) if identity else None
        if source is None:
            continue
        output.append(source if own else dict(source, slug=model["id"], display_name=model["id"]))
    return output


def write(executable, spec, home, run=None, wire=None, own=False):
    """The catalog file for ``spec`` in Codex profile ``home``, or None when no model has an entry. A runtime C refuses
    to start raises ``PermissionError`` before anything is removed: the launch it is for is refused with that
    message."""
    owned_paths.require_writable(home)
    listed = {}

    def catalogs(of):
        of = of if of and of != "responses" else None
        if of not in listed:
            listed[of] = bundled(executable, home, run, of)
        return listed[of]
    models = entries(spec, catalogs, own)
    if models:   # The provider wire's own entries, after Angelo's: an explicit catalog replaces its built-in one.
        named = {model["slug"] for model in models}
        models += [value for slug, value in catalogs(wire).items() if slug not in named]
    path = Path(home) / FILE_NAME
    with _writes:   # One writer at a time here; another process's write never shares a temporary name.
        if not models:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return None
        text = json.dumps({"models": models}, sort_keys=True)
        try:
            if shared_replace.read_text(path) == text:
                return str(path)   # Unchanged: a prewarmed app-server's launch still matches.
        except OSError:
            pass
        temporary = path.with_name("%s.%s.tmp" % (path.name, uuid.uuid4().hex))
        try:
            _new_file(temporary, text)
            shared_replace.replace(temporary, path)   # Atomic; a running Codex may hold the old one open (Windows).
        except BaseException:
            shared_replace.discard(temporary)
            raise
    return str(path)


def _new_file(temporary, text):
    """``text`` in a file made at ``temporary``, a name no other writer has (exclusive: never another's file)."""
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
