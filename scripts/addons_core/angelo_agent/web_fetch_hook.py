# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude Code's PreToolUse hook for WebFetch on a team connection: the page comes through the gateway.

Contract: doc/angelo/ai_providers.rst, "Team connections reach only the gateway".
Claude Code runs this script (``team_network.HOOK_COMMAND``) before each WebFetch
call, with the hook input on stdin. It asks the gateway for a ticket for the
page (``POST /v1/web/tickets`` with the session's grant key); the gateway runs
Anthropic's domain check and answers with a short-lived ticket path on its own
origin, which fetches the page. The hook hands WebFetch that URL
(``updatedInput``), so the fetch, its redirects and the domain check all go
through the gateway, and Claude Code converts and summarizes the page as usual.
A blocked domain or any failure denies the call with a reason the model reads;
nothing falls back to a direct fetch (the child's proxy refuses one anyway).

Run with Angelo's interpreter (``-I -S -B``) outside Blender, once per WebFetch call, so it loads only what
the request needs (1.0.9's interpreter on a Windows T4: 271 ms with Angelo's HTTP client, 148 ms this way): the standard library and Angelo's TLS distrust list (``tls_distrust``, loaded by
file). Trust is the interpreter's roots (Blender's bundled certifi through ``SSL_CERT_FILE``, else
certifi) plus ``NODE_EXTRA_CA_CERTS``, which Angelo sets from the managed policy's
``ExtraCACertificates`` for Claude Code itself (``team_runtime.apply_policy``), so the hook trusts
exactly what Claude Code does for the same gateway. Plain http only to a loopback gateway.
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

MAX_INPUT = 1024 * 1024
TICKET_PATH = re.compile(r"/v1/web/pages/[A-Za-z0-9_-]{16,4096}\.[A-Za-z0-9_-]{16,128}")   # The gateway's ticket path.
# Each socket step (connect, TLS, each read) is bounded by this, so the whole request stays well inside the hook's
# 60 s limit, past which Claude Code would kill the hook and run the original call. The gateway's own domain check
# takes at most 10 s; the page itself is fetched later, by WebFetch.
TIMEOUT = 12
MAX_ANSWER = 64 * 1024   # A ticket answer is a few hundred bytes.


class Unreachable(OSError):
    """The ticket request got no complete answer."""


def _gateway_origin(base_url):
    """``scheme://host[:port]`` of the session's gateway route (``ANTHROPIC_BASE_URL``), or None.

    Angelo validated the route (``team_endpoints``); only https, or http to a loopback development gateway, is used.
    """
    try:
        parts = urlsplit(base_url or "")
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    # As team_endpoints admits a gateway origin: https on its default port, or http to a loopback development one.
    if not host or parts.username or parts.password or not (parts.scheme == "https" and port in {None, 443} or (
            parts.scheme == "http" and _loopback(host))):
        return None
    return "%s://%s" % (parts.scheme, parts.netloc)


def _loopback(host):
    import ipaddress
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _tls_context(environ):
    import ssl
    context = ssl.create_default_context()
    if not context.cert_store_stats().get("x509_ca"):
        try:   # Run with -S (no site step): the bundled certifi is in site-packages, added only when needed.
            import sysconfig
            sys.path.append(sysconfig.get_paths()["purelib"])
            import certifi
            context.load_verify_locations(cafile=certifi.where())
        except (ImportError, OSError, ssl.SSLError):
            pass   # The handshake then fails, and the call is denied.
    extra = environ.get("NODE_EXTRA_CA_CERTS")
    if extra:
        try:
            context.load_verify_locations(cafile=extra)
        except (OSError, ssl.SSLError):
            pass
    import importlib.util
    spec = importlib.util.spec_from_file_location("angelo_tls_distrust", Path(__file__).with_name("tls_distrust.py"))
    distrust = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(distrust)
    return distrust.checked_context(context)


def _post(url, headers, body, timeout, environ):
    """``(status, body)`` of one POST with no proxy and no redirects; ``Unreachable`` without a complete answer."""
    import http.client   # Here, not at load: a failed import must end in a denial, like every other failure.
    parts = urlsplit(url)
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout,
                                                 context=_tls_context(environ))
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        connection.request("POST", parts.path, body=body, headers=dict(headers, **{"User-Agent": "Angelo-Desktop/0.1"}))
        response = connection.getresponse()
        return response.status, response.read(MAX_ANSWER + 1)   # An oversized answer fails as JSON: denied.
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise Unreachable(str(error)) from None
    finally:
        connection.close()


def decision(hook_input, environ, transport=None):
    """The hook's answer for one ``hook_input`` (a dict), or None to leave a call that is not WebFetch alone."""
    if hook_input.get("tool_name") != "WebFetch":
        return None
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    url = tool_input.get("url")
    if not isinstance(url, str) or not url:
        return _deny("WebFetch needs a URL.")
    origin = _gateway_origin(environ.get("ANTHROPIC_BASE_URL"))
    key = environ.get("ANTHROPIC_API_KEY")
    if origin is None or not key:
        return _deny("Web pages are fetched through your organization's gateway, and this session has no gateway route.")
    post = transport or (lambda *args: _post(*args, environ))
    try:
        status, body = post(origin + "/v1/web/tickets", {"content-type": "application/json", "x-api-key": key},
                            json.dumps({"url": url}).encode(), TIMEOUT)
    except OSError:
        return _deny("Your organization's gateway could not be reached to fetch this page. Try again shortly.")
    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    answer = answer if isinstance(answer, dict) else {}
    path = answer.get("path")
    if status == 200 and isinstance(path, str) and TICKET_PATH.fullmatch(path):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": dict(tool_input, url=origin + path)}}
    error = answer.get("error") if isinstance(answer.get("error"), dict) else {}
    message = error.get("message") if isinstance(error.get("message"), str) else ""
    if error.get("code") == "WEB_FETCH_BLOCKED":
        return _deny(message or "This site can't be fetched.")
    return _deny("Your organization's gateway did not fetch this page (%s). %s"
                 % (error.get("code") or "HTTP %d" % status, message[:300]))


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def main():
    """Always answers and exits 0: Claude Code runs the original call after a hook that fails, so any failure here
    denies the call instead (the fence would refuse it too, but nothing relies on that alone)."""
    try:
        # Claude Code writes UTF-8; -I ignores PYTHONUTF8, and Windows would decode a pipe in its ANSI code page.
        hook_input = json.loads(sys.stdin.buffer.read(MAX_INPUT).decode("utf-8"))
        answer = decision(hook_input, os.environ) if isinstance(hook_input, dict) else _deny("Unreadable hook input.")
    except Exception:   # noqa: BLE001 - every failure must end in an answer, never a non-zero exit.
        answer = _deny("Angelo couldn't prepare this fetch through your organization's gateway. Try again.")
    if answer is not None:
        sys.stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
