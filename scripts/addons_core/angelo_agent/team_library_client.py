# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The console's team library and IP API, called as the signed-in person.

Contract: the console's library and IP API, described in doc/angelo/team_library.rst. Every request
carries the person's short-lived access token from ``TeamAccount.access_token``
(the account owns refresh, single-flight and sign-out) through the team
account's own transport (``team_http.http_transport``: verified TLS, loopback
http only for a development console, no redirects, bounded answers). The token
is sent to the console origin only, never to a storage link and never logged;
a 401 is answered by one refresh, as ``TeamAccount._authorized`` does.

Library and IPs need no seat. Refusals keep the console's own reason (bounded
and redacted by ``provider_errors.sanitize``) and add who can fix it.

Two console generations are served. The current console pages the IP list
(``limit``/``cursor``) and an IP's entities (``kind``/``limit``/``cursor``,
``media=0``); the earlier one answers ``400`` to an IP-list ``limit`` and
ignores the detail's parameters. A first-page refusal or a detail without
``meta.nextCursor`` falls back to the whole answer, filtered and cut here, with
a ``local:<offset>`` cursor (``LOCAL_CURSOR``).

Standard library only; no bpy. Called only on worker threads.
"""

import json
from urllib.parse import quote, urlencode

from .provider_errors import sanitize
from .team_errors import NotSent, SIGN_IN_AGAIN, SignInRequired, TeamError, Unanswered, retry_after_seconds
from .team_http import http_transport, json_object

ADMIN = "Ask a team admin if you need access."
LOCAL_CURSOR = "local:"  # A page of an answer the console sent whole (an earlier console); the offset follows.


class TeamLibraryError(TeamError):
    """A console refusal: ``code`` (``NOT_FOUND``, ``FORBIDDEN``, ``CHARACTER_NOT_READY``…) and ``fields``."""

    def __init__(self, message, *, code="", status=None, retry_after=None, fields=None):
        super().__init__(message, code=code, status=status, retry_after=retry_after)
        self.fields = fields or {}


def refusal(status, body, retry_after=None, secrets=()):
    """The console's error envelope ``{"error": {"code", "message", "fields"}}`` as a ``TeamLibraryError``.

    ``secrets``: values redacted from the console's text (the request's own token).
    """
    value = json_object(body[:65536] if isinstance(body, bytes) else b"") or {}
    error = value.get("error") if isinstance(value.get("error"), dict) else {}
    code = error.get("code") if isinstance(error.get("code"), str) else ""
    reason = sanitize(error.get("message"), secrets) if isinstance(error.get("message"), str) else ""
    fields = {}
    if isinstance(error.get("fields"), dict):
        for key, messages in list(error["fields"].items())[:40]:
            if isinstance(key, str) and isinstance(messages, list):
                fields[sanitize(key, secrets, limit=80)] = [sanitize(m, secrets) for m in messages[:8]
                                                             if isinstance(m, str)]
    seconds = retry_after_seconds(retry_after)
    if status == 401 or code == "UNAUTHENTICATED":
        return SignInRequired("Your Angelo sign-in has expired. " + SIGN_IN_AGAIN, code=code, status=status)
    if status == 403:
        text = reason or "The Angelo console refused this for your account."
        return TeamLibraryError("%s %s" % (text, ADMIN), code=code or "FORBIDDEN", status=status)
    if status == 404:
        return TeamLibraryError((reason or "Not found.") + " It may not exist, or it isn't visible to you (personal "
                                "items and IPs are visible only to their owner).", code=code or "NOT_FOUND",
                                status=status)
    if code == "QUOTA_EXCEEDED":
        return TeamLibraryError("Your team's library storage is full. Ask a team admin to free space or add seats.",
                                code=code, status=status)
    if code == "UPLOAD_INVALID":
        return TeamLibraryError("The team library refused the file: %s Nothing was added to the library."
                                % (reason or "it doesn't match what was declared."), code=code, status=status,
                                fields=fields)
    if code == "CHARACTER_NOT_READY":
        return TeamLibraryError(reason or "A character isn't ready for generation.", code=code, status=status,
                                fields=fields)
    if status == 429 or code == "RATE_LIMITED":
        wait = "in %d s" % seconds if seconds is not None else "in a minute"
        return TeamLibraryError("The Angelo console is busy; try again %s." % wait, code="RATE_LIMITED",
                                status=status, retry_after=seconds)
    if 400 <= status < 500 and reason:
        detail = "; ".join("%s: %s" % (k, " ".join(v)) for k, v in fields.items())
        return TeamLibraryError(reason + (" (" + detail + ")" if detail else ""), code=code, status=status,
                                fields=fields)
    return TeamLibraryError("The Angelo console couldn't answer (HTTP %s); try again later." % status,
                            code=code, status=status)


class TeamLibraryClient:
    """One signed-in person's view of their organization's library and IPs."""

    def __init__(self, account, transport=http_transport):
        self.account, self._transport = account, transport
        view = account.view()
        self.org_id, self.org_name, self.user_id = view.org_id, view.org_name, view.user_id

    def _org(self, *parts):
        return "/api/orgs/%s/%s" % (quote(self.org_id, safe=""), "/".join(quote(p, safe="") for p in parts))

    def call(self, method, path, payload=None, query=None):
        """``(data, meta)`` of one console API request; raises ``TeamError`` (never with a token in it)."""
        if query:
            path += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        token = self.account.access_token()
        for attempt in (0, 1):
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"  # Bearer POSTs send JSON (contract 2026-10-08).
            try:
                status, answer, data = self._transport(method, self.account.console_url + path, headers, body)
            except NotSent:
                raise TeamError("Couldn't reach %s. Check your network and try again." % self.account.console_url) from None
            except Unanswered:
                raise TeamError("The Angelo console didn't answer completely (the network dropped, or the answer "
                                "was larger than Angelo reads at once). Try again, or ask for less.") from None
            if status == 401 and attempt == 0:
                token = self.account.access_token(stale=token)
                continue
            if 200 <= status < 300:
                value = json_object(data)
                if value is None or "data" not in value:
                    raise TeamError("The Angelo console returned an invalid answer; try again later.")
                return value["data"], value.get("meta") if isinstance(value.get("meta"), dict) else {}
            raise refusal(status, data, answer.get("retry-after"), secrets=(token,))
        raise SignInRequired("Your Angelo sign-in has expired. " + SIGN_IN_AGAIN)

    # Library ----------------------------------------------------------------

    def search(self, *, space="team", query=None, kind=None, tag=None, ip_entity_id=None, cursor=None, limit=10):
        data, meta = self.call("GET", self._org("library"), query={
            "space": space, "q": query or None, "kind": kind, "tag": tag, "ipEntityId": ip_entity_id,
            "cursor": cursor, "limit": limit})
        return _list(data), meta.get("nextCursor") if isinstance(meta.get("nextCursor"), str) else None

    def item(self, item_id):
        return _object(self.call("GET", self._org("library", item_id))[0])

    def download_link(self, item_id):
        data = _object(self.call("GET", self._org("library", item_id, "download"))[0])
        if not isinstance(data.get("url"), str):
            raise TeamError("The Angelo console returned an invalid download link.")
        return data["url"]

    def stats(self):
        return _object(self.call("GET", self._org("library", "stats"))[0])

    def create_upload(self, *, space, file_name, mime_type, size, sha256):
        body = {"space": space, "fileName": file_name, "mimeType": mime_type, "sizeBytes": size}
        if sha256:
            body["sha256"] = sha256  # Optional in the contract (never null); the console verifies it.
        data = _object(self.call("POST", self._org("library", "uploads"), body)[0])
        if not isinstance(data.get("itemId"), str) or not isinstance(data.get("uploadUrl"), str):
            raise TeamError("The Angelo console returned an invalid upload.")
        return data

    def complete_upload(self, item_id, metadata):
        return _object(self.call("POST", self._org("library", item_id, "complete"), metadata)[0])

    def delete_item(self, item_id):
        """Delete an item this person may delete (used only for their own unfinished upload)."""
        self.call("DELETE", self._org("library", item_id))

    # IPs ----------------------------------------------------------------------

    def ips(self, *, space="team", archived=False):
        """Every IP of the space (the console's own list, up to 500)."""
        return _list(self.call("GET", self._org("ips"), query={"space": space, "archived": "1" if archived else "0"})[0])

    def ips_page(self, *, space="team", archived=False, limit=20, cursor=None):
        """``(ips, next_cursor)``: one page, most recently changed first."""
        offset = _local_offset(cursor)
        if offset is None:
            query = {"space": space, "archived": "1" if archived else "0", "limit": limit, "cursor": cursor}
            try:
                data, meta = self.call("GET", self._org("ips"), query=query)
                return _list(data), meta.get("nextCursor") if isinstance(meta.get("nextCursor"), str) else None
            except TeamLibraryError as error:
                # An earlier console refuses an unknown ``limit``; a first page can't be invalid otherwise.
                if cursor is not None or error.status != 400:
                    raise
            offset = 0
        everything = sorted(self.ips(space=space, archived=archived), key=lambda ip: str(ip.get("updatedAt", "")),
                            reverse=True)
        return _slice(everything, offset, limit)

    def ip(self, ip_id):
        return _object(self.call("GET", self._org("ips", ip_id))[0])

    def ip_page(self, ip_id, *, kind=None, limit=50, cursor=None):
        """``(detail without media, next_cursor)``: the IP and one page of its entities (by kind, then name)."""
        offset = _local_offset(cursor)
        query = {"kind": kind, "media": "0"} if offset is not None else {
            "kind": kind, "limit": limit, "cursor": cursor, "media": "0"}
        data, meta = self.call("GET", self._org("ips", ip_id), query=query)
        detail = _object(data)
        if offset is None and "nextCursor" in meta:
            detail.pop("media", None)
            return detail, meta["nextCursor"] if isinstance(meta["nextCursor"], str) else None
        # An earlier console sends every entity and the whole gallery whatever was asked.
        entities = [e for e in detail.get("entities") or [] if isinstance(e, dict) and (not kind or e.get("kind") == kind)]
        entities.sort(key=lambda e: (str(e.get("kind", "")), str(e.get("name", "")).lower()))
        page, next_cursor = _slice(entities, offset or 0, limit)
        return {"ip": detail.get("ip") or {}, "entities": page}, next_cursor

    def entity(self, ip_id, entity_id):
        return _object(self.call("GET", self._org("ips", ip_id, "entities", entity_id))[0])

    def resolve(self, entities, purposes=None, limit=8):
        payload = {"entities": [{"entityId": e["entity_id"], **({"version": e["version"]} if "version" in e else {})}
                                for e in entities], "limit": limit}
        if purposes:
            payload["purposes"] = list(purposes)
        return _object(self.call("POST", self._org("ips", "resolve"), payload)[0])


def _local_offset(cursor):
    if not isinstance(cursor, str) or not cursor.startswith(LOCAL_CURSOR):
        return None
    digits = cursor[len(LOCAL_CURSOR):]
    if not digits.isdigit() or len(digits) > 6:
        raise TeamError("That page cursor isn't valid; start again without one.")
    return int(digits)


def _slice(values, offset, limit):
    page = values[offset:offset + limit]
    return page, (LOCAL_CURSOR + str(offset + limit)) if offset + limit < len(values) else None


def _object(value):
    if not isinstance(value, dict):
        raise TeamError("The Angelo console returned an invalid answer; try again later.")
    return value


def _list(value):
    if not isinstance(value, list):
        raise TeamError("The Angelo console returned an invalid answer; try again later.")
    return [item for item in value if isinstance(item, dict)]
