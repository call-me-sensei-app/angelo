# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A custom endpoint's model catalog for Codex: each mapped model gets the metadata of the model it is.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints" (model profiles).
Codex shapes every request from its catalog entry for the model's name: the
request form (Responses Lite), tool-search support, how much of a tool's output
it keeps and the context window. A server's own name for a model
(``team-gpt-sol``) matches no entry, so Codex would use its generic fallback
(10 KB of tool output, a 272,000-token window, no tool search). When the
organization maps the name to an Angelo model (``custom_endpoints.Model.profile``),
this module copies that model's entry from the runtime's own bundled catalog
(``codex debug models --bundled``) under the server's name, writes the catalog
into the connection's isolated Codex profile and passes it as
``model_catalog_json``. Unmapped models stay on the generic fallback; so does
every model when the runtime can't list its catalog (an older or the person's
own Codex): the chat still runs, with Codex's defaults. A runtime C refuses to start is no catalog either: the
refusal propagates (``PermissionError``), the catalog already written stays, and the endpoint check reports it.

The catalog holds model metadata only, never a key or address. The runtime's
catalog is read once per executable (path, size and modification time).

Standard library only; no bpy.
"""

import json
import os
import subprocess
import threading
from pathlib import Path

from . import essential_environment, owned_paths, shared_replace

FILE_NAME = "angelo-endpoint-models.json"
LIST_SECONDS = 20.0
_cache = {}            # (path, size, mtime) -> {slug: entry}
_lock = threading.Lock()


def _signature(executable):
    try:
        status = os.stat(executable)
    except OSError:
        return None
    return str(executable), status.st_size, status.st_mtime_ns


def bundled(executable, home, run=None):
    """``{slug: entry}`` of ``executable``'s bundled catalog; empty when it can't list one. ``home``: the isolated
    Codex profile it runs in, so no configuration of the person's is read. ``PermissionError`` when C refuses to start
    it (``owned_paths.require_runnable``, its message saying why): never cached, never read as "no catalog"."""
    signature = _signature(executable)
    if signature is None:
        return {}
    with _lock:
        if signature in _cache:
            return _cache[signature]
    run = run or subprocess.run
    try:
        program = str(owned_paths.require_runnable(executable))   # Never a program from state (C).
        completed = run([program, "debug", "models", "--bundled"], capture_output=True, timeout=LIST_SECONDS,
                        stdin=subprocess.DEVNULL, env=_quiet_environment(home))
        value = json.loads(completed.stdout) if completed.returncode == 0 else None
    except PermissionError:   # C refused to start it: the caller reports why (the re-review of 4be92cfb84f, N-2).
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        value = None
    models = value.get("models") if isinstance(value, dict) else None
    found = {m["slug"]: m for m in models or () if isinstance(m, dict) and isinstance(m.get("slug"), str)}
    with _lock:
        _cache[signature] = found
    return found


def _quiet_environment(home):
    """No credential, endpoint or profile of the person's: listing the bundled catalog needs none."""
    return essential_environment.environment(("LC_ALL",), CODEX_HOME=str(home))   # The OS's own variables only.


def entries(spec, catalog):
    """The catalog entries for ``spec``'s mapped models, each under the server's own name."""
    output = []
    for model in spec["models"]:
        source = catalog.get(model.get("profile") or "")
        if source is None:
            continue
        output.append(dict(source, slug=model["id"], display_name=model["id"]))
    return output


def write(executable, spec, home, run=None):
    """The catalog file for ``spec`` in Codex profile ``home``, or None when no model has an entry. A runtime C refuses
    to start raises ``PermissionError`` before anything is removed: the launch it is for is refused with that
    message."""
    owned_paths.require_writable(home)
    models = entries(spec, bundled(executable, home, run))
    path = Path(home) / FILE_NAME
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
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    shared_replace.replace(temporary, path)   # A running Codex may hold it open (Windows).
    return str(path)
