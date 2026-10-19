# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The console's team library and IP routes for ``FakeConsole``, host tests only.

``legacy = False`` (default) is the current console:
upload SHA-256 enforced at the storage PUT (``x-amz-checksum-sha256``, 403 when
the header is dropped, 400 BadDigest on other bytes), content sniffed on
complete (``UPLOAD_INVALID`` deletes the reservation), quota counting ready
items plus reservations younger than 15 minutes, ``fileName``/``sha256``/
``createdBy {userId, name, label}`` on items, ``PersonRef`` on IPs, and paged IP
list and detail (``limit``/``cursor``/``kind``/``media=0``). ``legacy = True`` is
the earlier console contract: a strict IP list that refuses ``limit``,
an IP detail that ignores its parameters, items without file name or digest,
labels that can be email addresses, IP creators as strings.

Mirrors the console's library and IP contract (doc/angelo/team_library.rst) and
its local storage provider: the ``{"data", "meta"}``
envelope, keyset cursors, presigned single-operation storage links on the
console's own origin (``/api/dev-storage/<token>``, as with
``STORAGE_PROVIDER=local``), the upload check (size and content type), the
idempotent complete, ``CHARACTER_NOT_READY`` with per-entity ``fields``, and
the error codes. Nothing here talks to a real service.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid

READINESS_MESSAGES = {"character_sheet": "Add a character sheet, or turnarounds viewed from the front, side and back.",
                      "close_up": "Add at least one close-up of the face."}
LIBRARY_TYPES = {"image": (("image/png", "image/jpeg", "image/webp", "image/gif"), 64 << 20),
                 "model": (("model/gltf-binary", "model/gltf+json", "model/vnd.usdz+zip", "application/x-blender",
                            "application/octet-stream"), 1 << 30)}


UPLOAD_URL_SECONDS = 15 * 60
SWEEP_SECONDS = 60 * 60
_EXTENSION_FOR = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
                  "model/gltf-binary": "glb", "model/vnd.usdz+zip": "usdz", "application/x-blender": "blend"}


def content_matches(data, mime, file_name):
    """The console's complete-time content sniff (images by magic here)."""
    head = data[:4096]
    images = {"image/png": head.startswith(b"\x89PNG"), "image/jpeg": head.startswith(b"\xff\xd8\xff"),
              "image/webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP", "image/gif": head.startswith(b"GIF8")}
    if mime in images:
        return images[mime], mime.split("/")[1].upper()
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    rules = {"glb": (b"glTF",), "usdz": (b"PK\x03\x04",), "usdc": (b"PXR-USDC",), "usda": (b"#usda",),
             "usd": (b"PXR-USDC", b"#usda"), "fbx": (b"Kaydara FBX Binary  \x00", b"; FBX"),
             "blend": (b"BLENDER", b"\x28\xb5\x2f\xfd", b"\x1f\x8b")}
    rule = {"model/gltf-binary": "glb", "model/vnd.usdz+zip": "usdz", "application/x-blender": "blend"}.get(
        mime, extension if mime == "application/octet-stream" else "")
    if rule == "obj":
        return b"\x00" not in head, "OBJ"
    if rule not in rules:
        return True, ""
    return head.startswith(rules[rule]), rule.upper()


def kind_for(mime):
    for kind, (types, _) in LIBRARY_TYPES.items():
        if mime in types:
            return kind
    return None


class FakeLibrary:
    """State and handlers; ``FakeConsole`` routes ``/api/orgs/<org>/library*``, ``/ips*`` and dev storage here."""

    def __init__(self, origin, *, user_id, org_id):
        self.origin, self.user_id, self.org_id = origin, user_id, org_id
        self.lock = threading.Lock()
        self.items = {}      # id -> item dict (internal: + "file", "status", "owner", "thumb", "file_name")
        self.objects = {}    # storage key -> (content type, bytes)
        self.ips, self.entities, self.references = {}, {}, {}
        self.quota = 10 << 30
        self.complete_busy = 0     # answer 429 to this many completes first
        self.role = "member"
        self.fail = {}             # path suffix -> (status, code, message)
        self.legacy = False        # True: the earlier console contract (module docstring)

    # Fixtures ---------------------------------------------------------------

    def add_item(self, title, data, mime, *, kind=None, space="team", owner=None, tags=(), description="",
                 thumbnail=None, provenance=None, created_at=None, file_name=None, sha256=None):
        identity = str(uuid.uuid4())
        key = "orgs/%s/library/%s/original" % (self.org_id, identity)
        self.objects[key] = (mime, data)
        if thumbnail is not None:
            self.objects[key.replace("/original", "/thumb.webp")] = ("image/webp", thumbnail)
        index = len(self.items)
        self.items[identity] = {
            "id": identity, "space": space, "kind": kind or kind_for(mime), "title": title, "description": description,
            "tags": list(tags), "mimeType": mime, "sizeBytes": len(data), "width": None, "height": None,
            "durationMs": None, "fileName": file_name or (title.replace(" ", "_") + (
                "." + _EXTENSION_FOR[mime] if mime in _EXTENSION_FOR else "")),
            "sha256": sha256, "createdBy": {"userId": owner or self.user_id, "name": "Mika", "label": "Mika"},
            "createdAt": created_at or "2026-10-08T10:%02d:00.000Z" % (59 - index % 60),
            "updatedAt": "2026-10-08T12:00:00.000Z", "provenance": provenance or {"source": "upload"},
            "usedBy": [], "hiddenUseCount": 0, "canEdit": True,
            "_key": key, "_status": "ready", "_owner": owner or self.user_id, "_thumb": thumbnail is not None}
        return identity

    def add_ip(self, name, space="team"):
        identity = str(uuid.uuid4())
        self.ips[identity] = {"id": identity, "space": space, "name": name, "logline": "A world of " + name,
                              "locked": False, "archived": False, "coverUrl": None, "coverItemId": None,
                              "entityCounts": {}, "createdBy": "Mika", "updatedAt": "2026-10-08T12:00:00.000Z",
                              "canEdit": True, "canManage": True}
        return identity

    def add_entity(self, ip_id, kind, name, *, version=1, variant_of=None, data=None):
        identity = str(uuid.uuid4())
        self.entities[identity] = {"id": identity, "ipId": ip_id, "kind": kind, "name": name, "version": version,
                                   "variantOf": variant_of, "variantKind": "costume" if variant_of else None,
                                   "variantLabel": "Winter" if variant_of else None,
                                   "data": data or {"appearance": "%s appearance" % name}}
        counts = self.ips[ip_id]["entityCounts"]
        counts[kind] = counts.get(kind, 0) + 1
        return identity

    def add_reference(self, entity_id, item_id, kind, *, view=None, purposes=("identity",), status="approved",
                      version=1):
        identity = str(uuid.uuid4())
        self.references[identity] = {"id": identity, "entityId": entity_id, "libraryItemId": item_id,
                                     "entityVersion": version, "kind": kind, "view": view,
                                     "purposes": list(purposes), "label": "", "guidance": "Match the look",
                                     "neutralPose": False, "status": status, "sortOrder": len(self.references)}
        return identity

    # Helpers ----------------------------------------------------------------

    def sign(self, grant):
        body = base64.urlsafe_b64encode(json.dumps(grant).encode()).decode().rstrip("=")
        return "%s/api/dev-storage/%s" % (self.origin, body)

    def unsign(self, token):
        try:
            return json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        except ValueError:
            return None

    def dto(self, item):
        value = {k: v for k, v in item.items() if not k.startswith("_")}
        if self.legacy:  # No file name or digest; the label falls back to the email address.
            value.pop("fileName", None)
            value.pop("sha256", None)
            value["createdBy"] = {"userId": item["createdBy"]["userId"], "label": "mika@northwind.example"}
        value["thumbnailUrl"] = (self.sign({"op": "get", "key": item["_key"].replace("/original", "/thumb.webp")})
                                 if item["_thumb"] else None)
        return value

    def visible(self, item):
        return item["_status"] == "ready" and (item["space"] == "team" or item["_owner"] == self.user_id)

    def readiness(self, entity_id):
        refs = [r for r in self.references.values() if r["entityId"] == entity_id and r["status"] == "approved"
                and self.items.get(r["libraryItemId"], {}).get("kind") == "image"]
        base = self.entities[entity_id]["variantOf"]
        if base:
            refs += [r for r in self.references.values() if r["entityId"] == base and r["status"] == "approved"]
        sheet = [r["id"] for r in refs if r["kind"] == "character_sheet"]
        views = {v: [r["id"] for r in refs if r["kind"] == "turnaround" and r["view"] == v]
                 for v in ("front", "three_quarter", "side", "back")}
        close = [r["id"] for r in refs if r["kind"] == "close_up"]
        missing_views = [v for v in ("front", "side", "back") if not views[v]]
        via = "character_sheet" if sheet else "turnaround_views" if not missing_views else None
        missing = ([] if via else ["character_sheet"]) + ([] if close else ["close_up"])
        identity = (sheet[:1] if via == "character_sheet" else [views[v][0] for v in ("front", "side", "back")]
                    if via else []) + close[:1]
        return {"ready": not missing, "missing": missing, "recommended": ["three_quarter", "expressions"],
                "sheet": {"via": via, "missingViews": [] if via else missing_views},
                "slots": {"characterSheet": sheet, "front": views["front"], "threeQuarter": views["three_quarter"],
                          "side": views["side"], "back": views["back"], "closeUp": close, "expression": [],
                          "outfit": [], "palette": []},
                "identity": identity if not missing else []}

    # Routes: each returns (status, value) or (status, bytes, content type) for storage ------------------

    def handle(self, method, path, query, body, headers):
        if path.startswith("/api/dev-storage/"):
            return self._storage(method, path.rsplit("/", 1)[1], body, headers)
        parts = path.split("/")  # ["", "api", "orgs", org, area, ...]
        if len(parts) < 5 or parts[3] != self.org_id:
            return 404, error("NOT_FOUND", "Organization not found.")
        for suffix, (status, code, message) in list(self.fail.items()):
            if path.endswith(suffix):
                return status, error(code, message)
        area, rest = parts[4], parts[5:]
        with self.lock:
            if area == "library":
                return self._library(method, rest, query, body)
            if area == "ips":
                return self._ips(method, rest, query, body)
        return 404, error("NOT_FOUND", "Not found.")

    def _library(self, method, rest, query, body):
        if method == "GET" and not rest:
            space = query.get("space", "team")
            limit = int(query.get("limit", "48"))
            if not 1 <= limit <= 100 or space not in {"team", "personal"}:
                return 400, error("VALIDATION_FAILED", "Invalid query.")
            items = [i for i in self.items.values() if self.visible(i) and i["space"] == space
                     and (space == "team" or i["_owner"] == self.user_id)]
            words = query.get("q", "").lower().split()
            items = [i for i in items if all(w in (i["title"] + " " + i["description"] + " " + " ".join(i["tags"])).lower()
                                             for w in words)]
            for field, key in (("kind", "kind"), ("tag", "tags")):
                if query.get(field):
                    items = [i for i in items if (query[field] in i[key] if key == "tags" else i[key] == query[field])]
            if query.get("ipEntityId"):
                used = {r["libraryItemId"] for r in self.references.values()
                        if r["entityId"] == query["ipEntityId"] and r["status"] != "superseded"}
                items = [i for i in items if i["id"] in used]
            items.sort(key=lambda i: (i["createdAt"], i["id"]), reverse=True)
            start = 0
            if query.get("cursor"):
                try:
                    at, identity = base64.urlsafe_b64decode(query["cursor"] + "==").decode().split("|")
                except (ValueError, UnicodeError):
                    return 400, error("VALIDATION_FAILED", "Invalid cursor.")
                start = next((n + 1 for n, i in enumerate(items) if (i["createdAt"], i["id"]) == (at, identity)), len(items))
            page = items[start:start + limit]
            more = len(items) > start + limit
            cursor = (base64.urlsafe_b64encode(("%s|%s" % (page[-1]["createdAt"], page[-1]["id"])).encode()).decode()
                      .rstrip("=") if more else None)
            return 200, {"data": [self.dto(i) for i in page], "meta": {"nextCursor": cursor}}
        if method == "GET" and rest == ["stats"]:
            used = self.used_bytes()
            return 200, {"data": {"usedBytes": used, "quotaBytes": self.quota, "itemCount": len(self.items)}}
        if method == "POST" and rest == ["uploads"]:
            return self._create_upload(body)
        if len(rest) >= 1:
            item = self.items.get(rest[0])
            if item is None or not (item["space"] == "team" or item["_owner"] == self.user_id):
                return 404, error("NOT_FOUND", "Item not found.")
            if method == "POST" and rest[1:] == ["complete"]:
                return self._complete(item, body)
            if method == "DELETE" and len(rest) == 1:
                if item["_owner"] != self.user_id:
                    return 403, error("FORBIDDEN", "Only the person who added this, or an admin, can delete it.")
                del self.items[item["id"]]
                self.objects.pop(item["_key"], None)
                return 200, {"data": None}
            if item["_status"] != "ready":
                return 404, error("NOT_FOUND", "Item not found.")
            if method == "GET" and len(rest) == 1:
                return 200, {"data": self.dto(item)}
            if method == "GET" and rest[1:] == ["download"]:
                return 200, {"data": {"url": self.sign({"op": "get", "key": item["_key"]}),
                                      "expiresAt": "2026-10-09T12:10:00.000Z"}}
        return 404, error("NOT_FOUND", "Not found.")

    def used_bytes(self):
        """Ready items plus reservations younger than the upload link (earlier console: every row)."""
        now = time.monotonic()
        return sum(i["sizeBytes"] for i in self.items.values() if self.legacy or i["_status"] == "ready"
                   or now - i.get("_reserved", now) < UPLOAD_URL_SECONDS)

    def age_reservations(self, seconds):
        """Make every pending upload ``seconds`` older (tests of the quota window and the sweep)."""
        for item in self.items.values():
            if "_reserved" in item:
                item["_reserved"] -= seconds

    def _create_upload(self, body):
        if not isinstance(body, dict) or set(body) - {"space", "fileName", "mimeType", "sizeBytes", "sha256"}:
            return 400, error("VALIDATION_FAILED", "Invalid upload.")
        kind = kind_for(body.get("mimeType"))
        if kind is None:
            return 400, error("UPLOAD_INVALID", "That file type isn't supported.", {"mimeType": ["Unsupported type."]})
        if body.get("sizeBytes", 0) > LIBRARY_TYPES[kind][1]:
            return 400, error("UPLOAD_INVALID", "Too large.", {"sizeBytes": ["Too large."]})
        if not self.legacy:  # Reservations older than an hour are swept before the quota is read.
            now = time.monotonic()
            for stale in [i for i in self.items.values() if i["_status"] == "pending"
                          and now - i.get("_reserved", now) > SWEEP_SECONDS]:
                del self.items[stale["id"]]
                self.objects.pop(stale["_key"], None)
        if self.used_bytes() + body["sizeBytes"] > self.quota:
            return 409, error("QUOTA_EXCEEDED", "Library storage is full. Delete files or add seats for more space.")
        identity = self.add_item(body["fileName"].rsplit(".", 1)[0], b"", body["mimeType"], space=body["space"],
                                 file_name=body["fileName"])
        item = self.items[identity]
        item.update(_status="pending", sizeBytes=body["sizeBytes"], _sha=body.get("sha256"), _reserved=time.monotonic())
        del self.objects[item["_key"]]
        put = {"op": "put", "key": item["_key"], "contentType": body["mimeType"], "contentLength": body["sizeBytes"]}
        headers = {"content-type": body["mimeType"]}
        if body.get("sha256") and not self.legacy:
            put["sha256"] = body["sha256"]
            headers["x-amz-checksum-sha256"] = base64.b64encode(bytes.fromhex(body["sha256"])).decode()
        thumb = ({"op": "put", "key": item["_key"].replace("/original", "/thumb.webp"), "contentType": "image/webp"}
                 if kind == "model" else None)
        return 201, {"data": {"itemId": identity, "uploadUrl": self.sign(put), "headers": headers,
                              "expiresAt": "2026-10-09T12:15:00.000Z",
                              "thumbnailUploadUrl": self.sign(thumb) if thumb else None}}

    def _complete(self, item, body):
        if item["_owner"] != self.user_id:
            return 404, error("NOT_FOUND", "Item not found.")
        if item["_status"] == "ready":
            return 200, {"data": self.dto(item)}
        if self.complete_busy:
            self.complete_busy -= 1
            return 429, error("RATE_LIMITED", "Busy; retry."), {"Retry-After": "1"}
        stored = self.objects.get(item["_key"])
        if stored is None:
            return 400, error("UPLOAD_INVALID", "The file hasn't arrived. Upload it again.")
        if len(stored[1]) != item["sizeBytes"] or stored[0] != item["mimeType"]:
            del self.objects[item["_key"]]
            return 400, error("UPLOAD_INVALID", "The uploaded file doesn't match what was declared. Upload it again.")
        matches, label = content_matches(stored[1], item["mimeType"], item["fileName"])
        if not self.legacy and not matches:  # Refused: the reservation and its object go.
            del self.items[item["id"]]
            self.objects.pop(item["_key"], None)
            return 400, error("UPLOAD_INVALID", "This isn't a %s file, as its type or name says. Upload it again." % label)
        allowed = {"title", "description", "tags", "provenance", "width", "height", "durationMs"}
        if not isinstance(body, dict) or set(body) - allowed:
            return 400, error("VALIDATION_FAILED", "Invalid completion.")
        provenance = body.get("provenance", {"source": "upload"})
        if set(provenance) - {"source", "prompt", "model", "provider", "params", "referenceItemIds", "ipEntities",
                              "parentItemIds", "app", "scene"}:
            return 400, error("VALIDATION_FAILED", "Invalid provenance.")
        thumb_key = item["_key"].replace("/original", "/thumb.webp")
        if item.get("_sha") and not self.legacy:
            item["sha256"] = item["_sha"]  # Storage checked it at the PUT.
        item.update(_status="ready", title=body.get("title", item["title"]), description=body.get("description", ""),
                    tags=body.get("tags", []), provenance=provenance, width=body.get("width"),
                    height=body.get("height"), _thumb=thumb_key in self.objects or item["kind"] == "image")
        if item["kind"] == "image":
            self.objects[thumb_key] = ("image/webp", stored[1])
        return 200, {"data": self.dto(item)}

    def _storage(self, method, token, body, headers):
        grant = self.unsign(token)
        if grant is None or grant.get("op") != {"GET": "get", "PUT": "put"}.get(method):
            return 403, error("FORBIDDEN", "Invalid storage link.")
        if "authorization" in {k.lower() for k in headers}:
            return 400, error("VALIDATION_FAILED", "Storage links never take credentials.")
        if method == "GET":
            stored = self.objects.get(grant["key"])
            return (404, error("NOT_FOUND", "No object.")) if stored is None else (200, stored[1], stored[0])
        lowered = {k.lower(): v for k, v in headers.items()}
        content_type = lowered.get("content-type")
        if content_type != grant["contentType"] or ("contentLength" in grant and len(body) != grant["contentLength"]):
            return 403, error("FORBIDDEN", "Signature mismatch.")
        if "sha256" in grant:  # R2: the signed checksum header must be sent, and the bytes must hash to it.
            expected = base64.b64encode(bytes.fromhex(grant["sha256"])).decode()
            if lowered.get("x-amz-checksum-sha256") != expected:
                return 403, error("FORBIDDEN", "SignatureDoesNotMatch")
            if hashlib.sha256(body).digest() != bytes.fromhex(grant["sha256"]):
                return 400, error("VALIDATION_FAILED", "BadDigest")
        self.objects[grant["key"]] = (content_type, body)
        return 200, {}

    def _ip_dto(self, ip):
        return dict(ip, createdBy="Mika" if self.legacy else {"userId": self.user_id, "name": "Mika"})

    @staticmethod
    def _page(values, query):
        """Offset pages with an opaque cursor, as ``ips_get`` and the IP detail answer."""
        try:
            limit = int(query["limit"])
            offset = int(base64.urlsafe_b64decode(query["cursor"] + "==").decode()) if query.get("cursor") else 0
        except (ValueError, UnicodeError):
            return 400, error("VALIDATION_FAILED", "Invalid cursor.")
        if not 1 <= limit <= 100:
            return 400, error("VALIDATION_FAILED", "Invalid limit.")
        page = values[offset:offset + limit]
        cursor = base64.urlsafe_b64encode(str(offset + limit).encode()).decode().rstrip("=") if offset + limit < len(values) else None
        return 200, {"data": page, "meta": {"nextCursor": cursor}}

    def _entity_summary(self, entity):
        refs = [r for r in self.references.values() if r["entityId"] == entity["id"] and r["status"] != "superseded"]
        return {k: entity[k] for k in ("id", "kind", "name", "version", "variantOf", "variantKind", "variantLabel")} | {
            "coverUrl": None, "coverItemId": None, "referenceCount": len(refs), "archived": False,
            "updatedAt": "2026-10-08T12:00:00.000Z"}

    def _ips(self, method, rest, query, body):
        if method == "GET" and not rest:
            space, archived = query.get("space", "team"), query.get("archived", "0") == "1"
            ips = [self._ip_dto(ip) for ip in self.ips.values() if ip["space"] == space and ip["archived"] == archived]
            if set(query) - {"space", "archived"} and self.legacy:  # The earlier route's strict query.
                return 400, error("VALIDATION_FAILED", "Unrecognized key(s) in object.", {"": ["Unrecognized key"]})
            if "cursor" in query and "limit" not in query:
                return 400, error("VALIDATION_FAILED", "Send limit with cursor.", {"cursor": ["Send limit with cursor."]})
            if "limit" not in query:
                return 200, {"data": ips}
            ips.sort(key=lambda ip: (ip["updatedAt"], ip["id"]), reverse=True)
            return self._page(ips, query)
        if method == "POST" and rest == ["resolve"]:
            return self._resolve(body)
        ip = self.ips.get(rest[0]) if rest else None
        if ip is None:
            return 404, error("NOT_FOUND", "IP not found.")
        if method == "GET" and len(rest) == 1:
            entities = [self._entity_summary(e) for e in self.entities.values() if e["ipId"] == ip["id"]]
            media = [{"libraryItemId": r["libraryItemId"], "kind": "image", "title": "Reference",
                      "mimeType": "image/png", "sizeBytes": 69, "addedBy": "Mika" if self.legacy else
                      {"userId": self.user_id, "name": "Mika"}, "uses": []} for r in self.references.values()
                     if self.entities[r["entityId"]]["ipId"] == ip["id"]]
            if self.legacy:  # The earlier route ignores every parameter.
                return 200, {"data": {"ip": self._ip_dto(ip), "entities": entities, "media": media}}
            entities.sort(key=lambda e: (e["kind"], e["name"].lower()))
            if query.get("kind"):
                entities = [e for e in entities if e["kind"] == query["kind"]]
            detail = {"ip": self._ip_dto(ip), "entities": entities}
            if query.get("media", "1") != "0":
                detail["media"] = media
            if "limit" not in query:
                return 200, {"data": detail}
            status, page = self._page(entities, query)
            return status, ({"data": dict(detail, entities=page["data"]), "meta": page["meta"]} if status == 200 else page)
        if method == "GET" and len(rest) == 3 and rest[1] == "entities":
            entity = self.entities.get(rest[2])
            if entity is None or entity["ipId"] != ip["id"]:
                return 404, error("NOT_FOUND", "Entity not found.")
            refs = [dict(r, thumbnailUrl=None, title=self.items[r["libraryItemId"]]["title"], media={})
                    for r in self.references.values() if r["entityId"] == entity["id"]]
            for ref in refs:
                ref.pop("entityId")
            variants = [self._entity_summary(e) for e in self.entities.values() if e["variantOf"] == entity["id"]]
            value = self._entity_summary(entity) | {
                "ipId": ip["id"], "data": entity["data"], "resolved": entity["data"],
                "prompt": {"positive": "%s, %s" % (entity["name"], entity["data"].get("appearance", "")),
                           "negative": ["blurry"]},
                "references": refs, "variants": variants,
                "versions": [{"version": v, "name": entity["name"], "changeNote": "v%d" % v,
                              "createdBy": "Mika" if self.legacy else {"userId": self.user_id, "name": "Mika"},
                              "createdAt": "2026-10-08T12:00:00.000Z"} for v in range(entity["version"], 0, -1)],
                "readiness": self.readiness(entity["id"]) if entity["kind"] == "character" else None,
                "canEdit": True}
            return 200, {"data": value}
        return 404, error("NOT_FOUND", "Not found.")

    def _resolve(self, body):
        if not isinstance(body, dict) or set(body) - {"entities", "purposes", "limit"}:
            return 400, error("VALIDATION_FAILED", "Invalid resolve.")
        requested, limit = body.get("entities") or [], body.get("limit", 8)
        if not 1 <= len(requested) <= 20 or not 0 <= limit <= 30:
            return 400, error("VALIDATION_FAILED", "Invalid resolve.")
        entities, not_ready, chosen = [], {}, []
        for index, item in enumerate(requested):
            entity = self.entities.get(item.get("entityId"))
            if entity is None:
                return 404, error("NOT_FOUND", "Entity not found.")
            version = item.get("version", entity["version"])
            if version > entity["version"]:
                return 404, error("NOT_FOUND", "Version %d not found." % version)
            entities.append({"entityId": entity["id"], "version": version, "kind": entity["kind"],
                             "name": entity["name"], "positive": entity["name"], "negative": ["blurry"]})
            if entity["kind"] == "character":
                readiness = self.readiness(entity["id"])
                if not readiness["ready"]:
                    not_ready["entities.%d" % index] = [READINESS_MESSAGES[m] for m in readiness["missing"]]
                    continue
                chosen += [self.references[r] for r in readiness["identity"]]
            chosen += [r for r in self.references.values() if r["entityId"] == entity["id"]
                       and r["status"] == "approved" and r not in chosen]
        if not_ready:
            names = ", ".join(entities[int(k.split(".")[1])]["name"] for k in not_ready)
            return 409, error("CHARACTER_NOT_READY", "%s isn't ready for generation. Add an approved character sheet "
                              "and close-up first." % names, not_ready)
        references = [{"slot": n + 1, "referenceId": r["id"], "libraryItemId": r["libraryItemId"],
                       "entityId": r["entityId"], "kind": r["kind"], "purposes": r["purposes"],
                       "guidance": r["guidance"], "url": self.sign({"op": "get", "key": self.items[r["libraryItemId"]]["_key"]}),
                       "expiresAt": "2026-10-09T13:00:00.000Z"} for n, r in enumerate(chosen[:limit])]
        fingerprint = hashlib.sha256(json.dumps([entities, [r["referenceId"] for r in references]]).encode()).hexdigest()
        return 200, {"data": {"fingerprint": fingerprint, "prompt": {"positive": ", ".join(e["positive"] for e in entities),
                                                                     "negative": "blurry"},
                              "entities": entities, "references": references}}


def error(code, message, fields=None):
    value = {"error": {"code": code, "message": message}}
    if fields:
        value["error"]["fields"] = fields
    return value
