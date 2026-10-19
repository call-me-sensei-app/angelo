# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An in-process stand-in for the Angelo console's app-auth API, for host tests only.

It listens on 127.0.0.1 inside the test process and follows the console as
built (its app-auth contract, doc/angelo/ai_providers.rst): the allow screen's checks and its redirect with ``code``
or ``error``, ``state`` and ``iss`` (RFC 9207); the token endpoint
(authorization code with PKCE S256, single-use 60-second codes whose reuse
revokes the grant, rotating refresh tokens whose reuse revokes the grant,
membership re-checked on every refresh); revoke; grant keys (scope
``generate``, the seat read now, at most 8 unexpired per grant); and
providers/available (404 to non-members and to another organization's
token). Error bodies and ``invalid_grant`` descriptions are the console's own
text. Organization and user ids are UUIDs, as on the console.

Differences from the real console that a test can rely on are deliberate and
listed in doc/angelo/ai_providers.rst ("Fake console"). ``console.library``
(``fake_team_library``) adds the team library, IP and storage-link routes
(doc/angelo/team_library.rst); ``console.reports`` (``fake_usage_reports``) the
app's batched agent-task reports. Nothing here talks to a real service. Tokens
are random fixture strings.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from fake_team_library import FakeLibrary
from fake_usage_reports import FakeUsageReports

ORG_ID = "6b0f3c1e-2d4a-4c8e-9f10-1a2b3c4d5e6f"
OTHER_ORG_ID = "0e9d8c7b-6a5f-4e3d-8c2b-1a0f9e8d7c6b"
USER_ID = "f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b"
EMAIL = "mika@northwind.example"
CLIENT_ID = "angelo-desktop"
LOOPBACK = re.compile(r"^http://(127\.0\.0\.1|\[::1\]):(\d{1,5})/callback$")
SCOPES = {"api", "generate", "library:read", "ips:read", "offline_access"}
ENDED = "This sign-in has ended. Sign in again."
USED_CODE = "This code was already used."
POLICY_SWITCHES = ("allowPersonalConnections", "allowLocalCliAccess", "allowVoice", "allowCollaborators")
UNSAFE_TEXT = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
# ``revokeGrant`` reasons and the ``error_reason`` a later refresh gets.
REVOKE_REASONS = {"signed out": "signed_out", "admin": "signed_out", "replaced by newer sign-ins": "signed_out",
                  "member removed": "member_removed", "left the organization": "member_removed",
                  "removed by the directory": "member_removed", "refresh token reuse": "reuse_detected",
                  "code reuse": "code_reused"}
NOT_MEMBER = "You're no longer a member of this organization."
CODE_LIFETIME = 60
KID = "fake-2026-10-09"
GRANT_KEYS_PER_GRANT = 8


def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class FakeConsole:
    def __init__(self, *, seat=True, providers=("anthropic", "openai", "google", "tripo"), access_lifetime=600,
                 org_name="Northwind Studio", email=EMAIL):
        self.seat, self.providers, self.access_lifetime = seat, list(providers), access_lifetime
        self.org_name, self.email, self.member = org_name, email, True
        self.role = "member"  # The access token's ``role`` claim ("owner", "admin" or "member").
        # The newer console contract: the
        # organization's app policy in token answers (all five fields, ``readAppPolicy``) and a machine-readable
        # ``error_reason`` on every ``invalid_grant``. None/False: production today. ``app_policy`` sets the first
        # from a stored value the way the console reads it.
        self.policy, self.error_reasons = None, False
        self.refresh_lifetime = None  # Seconds a refresh token lasts unused (the console: 60 days); None: forever.
        self.authorize_hints = []  # (org_hint, login_hint) of each allow screen, "" when not sent.
        self.lock = threading.Lock()
        self.codes = {}            # code -> {"challenge", "redirect_uri", "org", "device", "expires", "grant"}
        self.refresh = {}          # refresh token -> {"grant": id, "used": bool, "expires": monotonic or None}
        self.access = {}           # access token -> grant id
        self.grants = {}           # grant id -> {"org", "device", "scopes"}
        self.revoked = {}          # grant id -> reason
        self.requests = []         # (method, path, form or None, headers)
        self.refresh_delay = 0.0
        self.fail_next_refresh = None  # None, "drop" (close without answer), or an HTTP status
        self.grant_keys = []       # (key, grant, revoked) in issue order
        self.usage = {             # The console's PersonUsageResponse, abridged.
            "person": {"userId": USER_ID, "name": "Mika", "email": email, "role": "member", "seat": seat,
                       "joinedAt": "2026-10-01T00:00:00.000Z", "lastActiveAt": "2026-10-09"},
            "stats": {"lifetimeTokens": 4200000, "lifetimeEstCost": 0, "peakDayTokens": 900000, "peakDay": "2026-10-08",
                      "longestTaskMs": 42000, "longestStreakDays": 5, "currentStreakDays": 3, "activeDaysTotal": 7},
            "period": {"start": "2026-10-01", "end": "2026-10-31", "source": "calendar"},
            "thisPeriod": {"tokens": 1250000, "inputTokens": 1000000, "outputTokens": 250000, "cacheTokens": 0,
                           "requests": 310, "agentTasks": 42, "generations": 6, "estCost": 0, "activeDays": 7},
            "daily": [], "topModels": []}
        console = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, value, headers=None):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for name, item in (headers or {}).items():
                    self.send_header(name, item)
                self.end_headers()
                self.wfile.write(data)

            def _api_error(self, status, code, message):
                return self._send(status, {"error": {"code": code, "message": message}})

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _grant(self):
                """The bearer token's grant; the API checks the grant on every request, so a revoked one stops at once."""
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with console.lock:
                    grant = console.access.get(token)
                    return grant if grant and grant not in console.revoked else None

            def _library(self, method, body=b""):
                """Library, IP and storage-link routes (fake_team_library); False for any other path."""
                parts = urlsplit(self.path)
                segments = parts.path.split("/")
                storage = parts.path.startswith("/api/dev-storage/")
                if not storage and not (parts.path.startswith("/api/orgs/") and len(segments) > 4
                                        and segments[4] in {"library", "ips"}):
                    return False
                with console.lock:
                    console.requests.append((method, parts.path, None, dict(self.headers)))
                if not storage:
                    grant = self._grant()
                    if grant is None or not console.member:
                        self._api_error(401, "UNAUTHENTICATED", "Sign in to continue.")
                        return True
                    with console.lock:
                        token_org = console.grants[grant]["org"]
                    if segments[3] != token_org:  # The token's org must match the route's.
                        self._api_error(404, "NOT_FOUND", "Organization not found.")
                        return True
                payload = body
                if body and self.headers.get("Content-Type", "").startswith("application/json"):
                    payload = json.loads(body)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                answer = console.library.handle(method, parts.path, query, payload, dict(self.headers))
                if len(answer) == 3 and isinstance(answer[1], bytes):
                    status, data, content_type = answer
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(answer[0], answer[1], answer[2] if len(answer) > 2 else None)
                return True

            def do_PUT(self):  # noqa: N802
                try:
                    if not self._library("PUT", self._body()):
                        self._api_error(404, "NOT_FOUND", "Not found.")
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A test's upload stopped early on purpose; there is nobody to answer.

            def do_DELETE(self):  # noqa: N802
                if urlsplit(self.path).path.startswith("/api/me/devices/"):
                    return self._delete_device()
                if not self._library("DELETE"):
                    self._api_error(404, "NOT_FOUND", "Not found.")

            def do_POST(self):  # noqa: N802
                path = urlsplit(self.path).path
                body = self._body()
                if self._library("POST", body):
                    return None
                report = re.fullmatch(r"/api/orgs/([^/]+)/usage/app-reports", path)
                if report:  # Read raw: the body is checked exactly as the console's schema reads it.
                    return console._app_report(self, report.group(1), body)
                form = None
                if body and self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                    form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                elif body and self.headers.get("Content-Type", "").startswith("application/json"):
                    form = json.loads(body)
                with console.lock:
                    console.requests.append(("POST", path, form, dict(self.headers)))
                if path == "/api/oauth/token":
                    return console._token(self, form or {})
                if path == "/api/oauth/revoke":  # RFC 7009: always 200, whatever the token.
                    with console.lock:
                        entry = console.refresh.get((form or {}).get("token", ""))
                        if entry:
                            console.revoked.setdefault(entry["grant"], "signed out")
                    return self._send(200, {})
                if path == "/api/oauth/grant-key":
                    return console._grant_key(self)
                return self._api_error(404, "NOT_FOUND", "Not found.")

            def do_GET(self):  # noqa: N802
                if self._library("GET"):
                    return None
                path = urlsplit(self.path).path
                with console.lock:
                    console.requests.append(("GET", path, None, dict(self.headers)))
                if path == "/.well-known/jwks.json":
                    return self._send(200, {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": KID, "alg": "EdDSA",
                                                      "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}]})
                grant = self._grant()
                if path == "/api/me/devices":  # Your devices: this person's signed-in apps, newest first.
                    if grant is None:
                        return self._api_error(401, "UNAUTHENTICATED", "Sign in to continue.")
                    with console.lock:
                        rows = [{"id": g, "orgId": entry["org"], "orgName": console.org_name, "userId": USER_ID,
                                 "clientId": CLIENT_ID, "clientName": "Angelo", "verified": True,
                                 "scopes": sorted(entry["scopes"]), "deviceName": entry["device"],
                                 "createdAt": "2026-10-09T00:00:00.000Z", "lastUsedAt": None}
                                for g, entry in reversed(list(console.grants.items())) if g not in console.revoked]
                    return self._send(200, {"data": rows})
                usage = re.fullmatch(r"/api/orgs/([^/]+)/usage/people/([^/]+)", path)
                if usage:  # The person's own usage (admins may read anyone's; this fake has no admins).
                    if grant is None:
                        return self._api_error(401, "UNAUTHENTICATED", "Sign in to continue.")
                    if usage.group(1) != console.grants[grant]["org"] or usage.group(2) != USER_ID or not console.member:
                        return self._api_error(404, "NOT_FOUND", "Not found.")
                    return self._send(200, {"data": console.usage})
                policy = re.fullmatch(r"/api/orgs/([^/]+)/app-policy", path)
                if policy and console.policy is not None:  # A console without the policy has no such route.
                    if grant is None:  # Any member (a token with ``api`` for its own org); everyone else: 404.
                        return self._api_error(401, "UNAUTHENTICATED", "Sign in to continue.")
                    with console.lock:
                        entry = console.grants[grant]
                    if policy.group(1) != entry["org"] or "api" not in entry["scopes"] or not console.member:
                        return self._api_error(404, "NOT_FOUND", "Organization not found.")
                    return self._send(200, {"data": dict(console.policy)})
                match = re.fullmatch(r"/api/orgs/([^/]+)/providers/available", path)
                if match:
                    if grant is None:
                        return self._api_error(401, "UNAUTHENTICATED", "Sign in to continue.")
                    with console.lock:
                        token_org = console.grants[grant]["org"]
                    if match.group(1) != token_org or not console.member:  # Non-members and other orgs: 404.
                        return self._api_error(404, "NOT_FOUND", "Organization not found.")
                    return self._send(200, {"data": {"providers": console.providers + ["unknown-provider"]}})
                return self._api_error(404, "NOT_FOUND", "Not found.")

            def _delete_device(self):
                """Your devices: sign one of this person's devices out (``DELETE /api/me/devices/{grant}``)."""
                path = urlsplit(self.path).path
                with console.lock:
                    console.requests.append(("DELETE", path, None, dict(self.headers)))
                match = re.fullmatch(r"/api/me/devices/([^/]+)", path)
                if not match:
                    return self._api_error(404, "NOT_FOUND", "Not found.")
                if self._grant() is None:
                    return self._api_error(401, "UNAUTHENTICATED", "Sign in to continue.")
                with console.lock:
                    if match.group(1) not in console.grants or match.group(1) in console.revoked:
                        return self._api_error(404, "NOT_FOUND", "Device not found.")
                    console.revoked[match.group(1)] = "signed out"
                return self._send(200, {"data": {"revoked": True}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.library = FakeLibrary(self.url, user_id=USER_ID, org_id=ORG_ID)
        self.reports = FakeUsageReports()
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # Browser side ----------------------------------------------------------

    def callback(self, authorize_url, decision="allow", org=ORG_ID):
        """What the allow screen does: the loopback URL the browser is sent to (``code`` or ``error``, ``state``, ``iss``).

        An invalid request raises ``ValueError``: the console shows the reason and never redirects.
        """
        parsed = urlsplit(authorize_url)
        if parsed.path != "/oauth/authorize":
            raise ValueError("not the authorize page")
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        scopes = set(query.get("scope", "").split())
        if (query.get("client_id") != CLIENT_ID or query.get("response_type") != "code"
                or query.get("code_challenge_method") != "S256"
                or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", query.get("code_challenge", ""))
                or not 8 <= len(query.get("state", "")) <= 512 or not scopes or not scopes <= SCOPES
                or not LOOPBACK.match(query.get("redirect_uri", "")) or len(query.get("device_name", "")) > 80):
            raise ValueError("invalid authorize request")
        self.authorize_hints.append((query.get("org_hint", ""), query.get("login_hint", "")))
        values = {"state": query["state"], "iss": self.url}
        if decision == "allow":
            code = "code_" + secrets.token_urlsafe(32)
            with self.lock:
                self.codes[code] = {"challenge": query["code_challenge"], "redirect_uri": query["redirect_uri"],
                                    "org": org, "device": query.get("device_name", ""), "scopes": scopes,
                                    "expires": time.monotonic() + CODE_LIFETIME, "grant": None}
            values["code"] = code
        else:
            values["error"] = "access_denied"
        return query["redirect_uri"] + "?" + urlencode(values)

    def approve(self, authorize_url):
        """``(redirect_uri, code, state)`` of an allowed request (``callback`` builds the whole URL)."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(self.callback(authorize_url)).query).items()}
        return parse_qs(urlsplit(authorize_url).query)["redirect_uri"][0], query["code"], query["state"]

    # Console-side changes a test makes ---------------------------------------

    def remove_member(self):
        """An admin removed the person: refresh fails (and revokes), the API answers 404."""
        self.member = False

    def app_policy(self, stored):
        """An owner's stored policy as token answers carry it (``readAppPolicy``: every field, defaults filled)."""
        stored = stored if isinstance(stored, dict) else {}
        policy = {key: stored[key] if isinstance(stored.get(key), bool) else True for key in POLICY_SWITCHES}
        contact = stored.get("adminContact")
        contact = contact.strip() if isinstance(contact, str) else ""
        policy["adminContact"] = contact if contact and len(contact) <= 200 and not UNSAFE_TEXT.search(contact) else None
        self.policy = policy

    def revoke_all(self, reason="signed out"):
        """Your devices / an admin's sign-out: every grant ends (``revokeGrant``'s reasons, oauth-error.ts)."""
        with self.lock:
            for grant in self.grants:
                self.revoked.setdefault(grant, reason)

    # Token endpoint ----------------------------------------------------------

    def _jwt(self, grant):
        """An access token shaped like the console's (EdDSA JWT); the signature is random bytes, never checked here."""
        def part(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
        now = int(time.time())
        claims = {"iss": self.url, "aud": ["angelo-api", "angelo-gateway"], "sub": USER_ID, "org": self.grants[grant]["org"],
                  "role": self.role, "seat": self.seat, "scope": " ".join(sorted(self.grants[grant]["scopes"])),
                  "gid": grant, "client_id": CLIENT_ID, "iat": now, "exp": now + self.access_lifetime,
                  "jti": secrets.token_hex(8)}
        return ".".join((part({"alg": "EdDSA", "typ": "JWT", "kid": KID}), part(claims),
                         base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()))

    def _issue(self, grant):
        access, refresh = self._jwt(grant), secrets.token_urlsafe(32)
        self.access[access] = grant
        lifetime = self.refresh_lifetime
        self.refresh[refresh] = {"grant": grant, "used": False,
                                 "expires": None if lifetime is None else time.monotonic() + lifetime}
        return {"access_token": access, "token_type": "Bearer", "expires_in": self.access_lifetime,
                "refresh_token": refresh, "scope": " ".join(sorted(self.grants[grant]["scopes"])), "seat": self.seat,
                "org": {"id": self.grants[grant]["org"], "name": self.org_name},
                "user": {"id": USER_ID, "email": self.email, "name": "Mika"},
                **({"policy": dict(self.policy)} if self.policy is not None else {})}

    def _token(self, handler, form):
        """``exchangeCode`` and ``refresh``: every refusal is a 400 ``invalid_grant``."""
        def refuse(reason, description):
            body = {"error": "invalid_grant", "error_description": description}
            if self.error_reasons:
                body["error_reason"] = reason
            return handler._send(400, body)
        if form.get("grant_type") == "authorization_code":
            with self.lock:
                entry = self.codes.get(form.get("code", ""))
                if entry is None:
                    return refuse("expired", "Unknown or expired code.")
                if entry["grant"] is not None:  # A replayed code leaked: end the sign-in it made.
                    self.revoked.setdefault(entry["grant"], "code reuse")
                    return refuse("code_reused", USED_CODE)
                if entry["expires"] <= time.monotonic():
                    return refuse("expired", "Unknown or expired code.")
                if form.get("client_id") != CLIENT_ID or entry["redirect_uri"] != form.get("redirect_uri"):
                    return refuse("expired", "The code was issued to a different app or redirect URI.")
                if entry["challenge"] != _challenge(form.get("code_verifier", "")):
                    return refuse("expired", "PKCE verification failed.")
                if not self.member:
                    return refuse("member_removed", NOT_MEMBER)
                grant = "grant-" + secrets.token_hex(8)
                entry["grant"] = grant
                self.grants[grant] = {"org": entry["org"], "device": entry["device"],
                                      "scopes": entry["scopes"] - {"library:read", "ips:read"}}
                value = self._issue(grant)
            return handler._send(200, value)
        if form.get("grant_type") == "refresh_token":
            if self.refresh_delay:
                time.sleep(self.refresh_delay)
            with self.lock:
                failure, self.fail_next_refresh = self.fail_next_refresh, None
                entry = self.refresh.get(form.get("refresh_token", ""))
                if entry is None or form.get("client_id") != CLIENT_ID:
                    return refuse("signed_out", ENDED)  # Unknown, or another app's token.
                if entry["grant"] in self.revoked:
                    return refuse(REVOKE_REASONS.get(self.revoked[entry["grant"]], "signed_out"), ENDED)
                if entry["expires"] is not None and entry["expires"] <= time.monotonic():
                    return refuse("expired", ENDED)
                if entry["used"]:
                    self.revoked.setdefault(entry["grant"], "refresh token reuse")  # Ends the whole grant.
                    return refuse("reuse_detected", ENDED)
                if failure in {503, 429}:
                    return handler._send(failure, {"error": {"code": "RATE_LIMITED", "message": "Slow down."}},
                                         {"Retry-After": "30"} if failure == 429 else None)
                if not self.member:
                    self.revoked.setdefault(entry["grant"], "member removed")
                    return refuse("member_removed", NOT_MEMBER)
                entry["used"] = True
                if failure == "drop":
                    handler.close_connection = True
                    handler.wfile.flush()
                    handler.connection.shutdown(2)
                    return None
                value = self._issue(entry["grant"])
            return handler._send(200, value)
        return handler._send(400, {"error": "unsupported_grant_type", "error_description": "Invalid token request."})

    def _grant_key(self, handler):
        """Bearer access token only; scope ``generate``; the seat is read now. The body is not read."""
        grant = handler._grant()
        if grant is None or not self.member:
            return handler._api_error(401, "UNAUTHENTICATED", "Your sign-in has ended. Sign in again from Angelo.")
        with self.lock:
            scopes = self.grants[grant]["scopes"]
        if "generate" not in scopes:
            return handler._api_error(403, "FORBIDDEN", "This sign-in can't generate. Sign in again from Angelo.")
        if not self.seat:
            return handler._api_error(403, "FORBIDDEN", "You need a seat in this organization to generate. Ask an admin.")
        key = "agk_" + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        with self.lock:
            live = [i for i, (_k, g, revoked) in enumerate(self.grant_keys) if g == grant and not revoked]
            if len(live) >= GRANT_KEYS_PER_GRANT:  # Issuing a ninth revokes the oldest.
                oldest = self.grant_keys[live[0]]
                self.grant_keys[live[0]] = (oldest[0], oldest[1], True)
            self.grant_keys.append((key, grant, False))
        return handler._send(200, {"data": {"key": key, "expires_at": "2026-10-10T05:00:00.000Z",
                                            "expires_in": 43200}})

    def _app_report(self, handler, org, body):
        """``requireUser`` (a live grant with ``api``), ``requireOrg`` (the token's own organization), then
        ``fake_usage_reports``; ``"drop"`` closes the connection after applying, as a lost answer."""
        with self.lock:
            self.requests.append(("POST", "/api/orgs/%s/usage/app-reports" % org, None, dict(handler.headers)))
        grant = handler._grant()
        with self.lock:
            entry = self.grants.get(grant) if grant else None
        if entry is None or "api" not in entry["scopes"]:
            return handler._api_error(401, "UNAUTHENTICATED", "Your app sign-in has expired or ended. Sign in again.")
        if org != entry["org"] or not self.member:
            return handler._api_error(404, "NOT_FOUND", "Organization not found.")
        status, value, headers = self.reports.handle(org, USER_ID, body)
        if status == "drop":
            handler.close_connection = True
            handler.wfile.flush()
            handler.connection.shutdown(2)
            return None
        return handler._send(status, value, headers)

    # Inspection ----------------------------------------------------------------

    def refresh_requests(self):
        with self.lock:
            return [r for r in self.requests if r[1] == "/api/oauth/token" and (r[2] or {}).get("grant_type") == "refresh_token"]

    def issued_keys(self):
        with self.lock:
            return [key for key, _grant, _revoked in self.grant_keys]


class FakeVault:
    """The ``TokenVault`` interface in memory; records every call."""

    def __init__(self, token=None):
        self.token, self.reason, self.calls = token, "", []

    def read(self):
        self.calls.append("read")
        return self.token

    def write(self, token):
        self.calls.append("write")
        self.token = token
        return True

    def delete(self):
        self.calls.append("delete")
        existed, self.token = self.token is not None, None
        return existed
