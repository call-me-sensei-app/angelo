# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Non-billable BYOK checks. No inference, generation, redirects or raw logs.

The main thread only queues work and reads sanitized results. Keys are supplied
by the connection manager, not discovered in environment variables or files.
"""

import json
import math
import queue
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .provider_registry import HTTP_MODES, spec_for
from .team_http import tls_context


class ConnectionCheckError(RuntimeError):
    pass


def clean_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("Enter a valid API key without spaces or control characters")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionCheckError("Provider redirected the check; credentials were not forwarded")


def probe_request(mode, api_key):
    spec = spec_for(mode)
    if mode not in HTTP_MODES:
        raise ValueError("This connection uses native runtime authentication")
    key = clean_key(api_key)
    if spec["probe"] is None:
        return None
    header = spec.get("auth_header", "Authorization")
    return urllib.request.Request(spec["base_url"] + spec["probe"],
        headers={header: key if header != "Authorization" else "Bearer " + key,
                 "Accept": "application/json", "User-Agent": "Angelo-ConnectionCheck/0.1"}, method="GET")


def read_probe(request):
    # Default TLS verification stays on. Never send credentials to a user-supplied
    # host or follow an authentication redirect. A check reads at most 1 MiB.
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=tls_context()))
    try:
        with opener.open(request, timeout=15) as response:
            payload = response.read(1048577)
            if len(payload) > 1048576:
                raise ConnectionCheckError("Provider returned an oversized connection-check response")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except urllib.error.HTTPError as error:
        error.close()
        messages = {401: "API key was rejected", 403: "API key has insufficient permissions for this check",
                    402: "Provider requires billing or credits", 404: "Provider check endpoint is unavailable",
                    429: "Provider rate limit reached; try the check again later"}
        raise ConnectionCheckError(messages.get(error.code, "Provider connection check failed (HTTP %d)" % error.code)) from None
    except (ValueError, UnicodeError):
        raise ConnectionCheckError("Provider returned an invalid connection-check response") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConnectionCheckError("Couldn't reach the provider securely; check your network and retry") from None


def _models(mode, data):
    spec = spec_for(mode)
    values = data.get("models") if mode == "gemini_images_api" else data.get("data")
    if not isinstance(values, list):
        raise ConnectionCheckError("Provider didn't return a model catalog")
    identities = set()
    for value in values[:1000]:
        if isinstance(value, dict):
            identity = value.get("id") or value.get("name", "").removeprefix("models/")
            if isinstance(identity, str):
                identities.add(identity)
    # Capabilities come from primary docs, not untrusted response metadata. We
    # don't accidentally offer embeddings, image models or an incompatible wire
    # model as a coding agent. Unknown/new models require registry admission.
    models = [dict(m, access="listed") for m in spec["models"] if m["id"] in identities]
    if models and not any(m["default"] for m in models):
        models[0]["default"] = True
    return models


@dataclass
class CheckResult:
    state: str
    message: str
    verified: bool = False
    models: list = field(default_factory=list)
    balance: float | None = None


def check_connection(mode, api_key, transport=read_probe):
    from . import runtime_relay
    if not api_key and runtime_relay.relayed():   # A worker chat: Angelo holds the key and runs the check (C5).
        found = runtime_relay.probe("check_connection")
        return CheckResult(str(found.get("state") or "error"), str(found.get("message") or ""),
                           verified=found.get("verified") is True, models=list(found.get("models") or []))
    request = probe_request(mode, api_key)
    spec = spec_for(mode)
    if request is None:
        # There is no documented non-billable image-scoped Seedream auth probe.
        # A saved key isn't a verified key; don't fabricate a success or send an
        # empty generation request to try to elicit a validation response.
        return CheckResult("configured", "Key configured · verification deferred until first generation",
                           models=[dict(m, access="not_checked") for m in spec["models"]])
    data = transport(request)
    if data.get("error") or data.get("code", 0) != 0:
        raise ConnectionCheckError("Provider rejected the connection check")
    if mode in {"tripo_api", "meshy_api"}:
        body = data.get("data", {}) if mode == "tripo_api" else data
        balance = body.get("balance") if isinstance(body, dict) else None
        if isinstance(balance, bool) or not isinstance(balance, (float, int)) or not math.isfinite(balance) or balance < 0:
            raise ConnectionCheckError("Provider didn't return a valid credit balance")
        return CheckResult("verified", "API key verified · model access checked when generating", True,
                           [dict(m, access="not_checked") for m in spec["models"]], float(balance))
    models = _models(mode, data)
    return CheckResult("verified" if models else "no_models",
                       "API key verified · requested models listed" if models else "Authenticated · requested models not listed for this key",
                       True, models)


class ApiConnections:
    def __init__(self, transport=read_probe):
        self.results, self.running, self.generations = {}, set(), {}
        self.events = queue.Queue()
        self.transport, self.closed = transport, False

    def invalidate(self, identity):
        self.generations[identity] = self.generations.get(identity, 0) + 1
        self.results.pop(identity, None)
        self.running.discard(identity)

    def start(self, record, api_key):
        identity, mode = record["id"], record["mode"]
        if self.closed or identity in self.running:
            raise RuntimeError("A connection check is already running")
        api_key = clean_key(api_key)
        self.invalidate(identity)
        generation = self.generations[identity]
        self.running.add(identity)
        self.results[identity] = CheckResult("checking", "Checking API connection…")

        def run():
            try:
                result = check_connection(mode, api_key, self.transport)
            except (ConnectionCheckError, ValueError) as error:
                result = CheckResult("failed", str(error).replace(api_key, "[REDACTED]")[:250])
            except Exception:
                # Never expose a library exception that might include headers.
                result = CheckResult("failed", "Connection check failed; retry or check the provider's API settings")
            self.events.put((identity, generation, result))
        threading.Thread(target=run, daemon=True, name="AngeloApiConnectionCheck").start()

    def pump(self):
        changes = []
        while True:
            try:
                identity, generation, result = self.events.get_nowait()
            except queue.Empty:
                return changes
            if self.closed or generation != self.generations.get(identity):
                continue
            self.running.discard(identity)
            self.results[identity] = result
            changes.append(identity)

    def close(self):
        self.closed = True
        self.running.clear()
        self.results.clear()
