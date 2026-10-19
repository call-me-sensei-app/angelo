# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compact, model-facing shapes of the console's library and IP answers.

* **No links.** Download, thumbnail and reference URLs are short-lived
  capabilities to private files; a model never needs them (the host fetches by
  item id) and they are never sent to one.
* **Bounded.** Descriptions, prompts, field values and lists are cut to fixed
  sizes, so one item or entity can't flood a model's context.
* **People by name only**: "you" for the caller, "a teammate" when the console
  has no name (it would otherwise show an email address), as the console's MCP
  tools do for third-party apps.
* **Known items.** Every item a result shows is remembered per organization
  (bounded), so an import's approval card can say what it will download without
  a network request on Blender's main thread.

Standard library only; no bpy.
"""

import json
import threading
from collections import OrderedDict

# The console's own readiness sentences, as the console words them (its READINESS_MESSAGES).
READINESS_MESSAGES = {
    "character_sheet": "Add a character sheet, or turnarounds viewed from the front, side and back.",
    "close_up": "Add at least one close-up of the face.",
    "three_quarter": "A three-quarter turnaround helps generation with angled shots.",
    "expressions": "3 or more expressions keep faces consistent.",
    "outfit": "An outfit detail keeps costumes consistent.",
    "palette": "A palette reference keeps colours on brand.",
}
NOT_READY_FIX = ("Add the missing approved references to the character in the Angelo console (IPs, the character's "
                 "reference sheet), or ask whoever manages the IP. Angelo won't generate this character without them; "
                 "don't work around it with other images or a plain prompt.")
IMPORTABLE_MODEL_TYPES = frozenset({"model/gltf-binary", "model/vnd.usdz+zip", "application/octet-stream"})
MAX_KNOWN = 512
_known = OrderedDict()
_known_lock = threading.Lock()


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _drop_empty(value):
    return {k: v for k, v in value.items() if v not in (None, "", [], {})}


MODEL_EXTENSIONS = frozenset({"glb", "fbx", "obj", "usd", "usda", "usdc", "usdz"})


def person(created_by, user_id):
    """"you", the person's name, or "a teammate" (an earlier console's label can be an email address)."""
    if not isinstance(created_by, dict):
        return None
    if created_by.get("userId") and created_by.get("userId") == user_id:
        return "you"
    for value in (created_by.get("name"), created_by.get("label")):
        if isinstance(value, str) and value.strip() and "@" not in value:
            return _text(value, 80)
    return "a teammate"


def file_extension(name):
    return name.rsplit(".", 1)[-1].lower() if isinstance(name, str) and "." in name else ""


def importable(item):
    """Where ``angelo_team_library_import`` can put this item, or None with ``import_note``."""
    kind, mime = item.get("kind"), item.get("mimeType")  # No MIME: an IP reference known by kind only.
    extension = file_extension(item.get("fileName"))  # The current console names the file; earlier ones don't.
    if kind == "image" and (mime is None or mime in {"image/png", "image/jpeg", "image/webp"}):
        return ["SCENE", "STUDIO"], None
    if kind == "model" and mime == "application/octet-stream" and extension and extension not in MODEL_EXTENSIONS:
        return None, "Only GLB, binary FBX, OBJ and USD models are imported (this is a .%s file)." % extension[:12]
    if kind == "model" and (mime is None or mime in IMPORTABLE_MODEL_TYPES):
        return ["SCENE"], None
    if mime == "application/x-blender":
        return None, ".blend files are never imported from the team library."
    if kind == "model":
        return None, "Only GLB, binary FBX, OBJ and USD models are imported."
    return None, "Only images and 3D models are imported."


def item(value, user_id, *, full=False):
    places, note = importable(value)
    result = {"id": value.get("id"), "title": _text(value.get("title"), 200), "kind": value.get("kind"),
              "space": value.get("space"), "description": _text(value.get("description"), 1500 if full else 300),
              "tags": [t for t in value.get("tags") or [] if isinstance(t, str)][:20],
              "file": _text(value.get("fileName"), 120), "format": value.get("mimeType"),
              "bytes": value.get("sizeBytes"), "width": value.get("width"),
              "height": value.get("height"), "duration_ms": value.get("durationMs"),
              "by": person(value.get("createdBy"), user_id), "created": _text(value.get("createdAt"), 10),
              "thumbnail": bool(value.get("thumbnailUrl")), "import_into": places, "import_note": note}
    if full:
        provenance = value.get("provenance") if isinstance(value.get("provenance"), dict) else {}
        result["provenance"] = _drop_empty({
            "source": provenance.get("source"), "prompt": _text(provenance.get("prompt"), 1200),
            "model": _text(provenance.get("model"), 200), "provider": _text(provenance.get("provider"), 60),
            "reference_item_ids": provenance.get("referenceItemIds"), "ip_entities": provenance.get("ipEntities"),
            "parent_item_ids": provenance.get("parentItemIds"), "scene": _text(provenance.get("scene"), 120),
            "app": provenance.get("app")})
        result["used_by"] = [_drop_empty({"ip": _text(u.get("ipName"), 120), "ip_id": u.get("ipId"),
                                          "ip_space": u.get("ipSpace"), "entity": _text(u.get("entityName"), 120),
                                          "entity_id": u.get("entityId")})
                             for u in (value.get("usedBy") or [])[:25] if isinstance(u, dict)]
        result["hidden_uses"] = value.get("hiddenUseCount") or None
        result["can_edit"] = value.get("canEdit") is True
    return _drop_empty(result)


def ip(value):
    return _drop_empty({"id": value.get("id"), "name": _text(value.get("name"), 120),
                        "logline": _text(value.get("logline"), 300), "space": value.get("space"),
                        "locked": value.get("locked") or None, "archived": value.get("archived") or None,
                        "entities": value.get("entityCounts") if isinstance(value.get("entityCounts"), dict) else None,
                        "updated": _text(value.get("updatedAt"), 10), "can_edit": value.get("canEdit") is True})


def entity_summary(value):
    variant = (_drop_empty({"kind": value.get("variantKind"), "label": _text(value.get("variantLabel"), 80)})
               if value.get("variantOf") else None)
    return _drop_empty({"id": value.get("id"), "kind": value.get("kind"), "name": _text(value.get("name"), 120),
                        "version": value.get("version"), "variant_of": value.get("variantOf"), "variant": variant,
                        "references": value.get("referenceCount"), "archived": value.get("archived") or None})


def readiness(value):
    """A character's readiness in the person's terms; None for other kinds."""
    if not isinstance(value, dict):
        return None
    missing = [READINESS_MESSAGES.get(m, str(m)) for m in value.get("missing") or []]
    sheet = value.get("sheet") if isinstance(value.get("sheet"), dict) else {}
    return _drop_empty({"ready": value.get("ready") is True, "missing": missing,
                        "recommended": [READINESS_MESSAGES.get(m, str(m)) for m in value.get("recommended") or []],
                        "sheet_via": sheet.get("via"), "missing_views": sheet.get("missingViews"),
                        "generation": ("allowed: resolve puts the sheet and close-up first" if value.get("ready") is True
                                       else "blocked until the missing references are approved. " + NOT_READY_FIX)})


def _bounded_fields(data, budget=6000):
    """An entity's field values, each cut so the whole stays within ``budget`` characters of JSON."""
    if not isinstance(data, dict):
        return {}
    fields = dict(list(data.items())[:60])
    while len(json.dumps(fields, ensure_ascii=False)) > budget:
        longest = max(fields, key=lambda k: len(json.dumps(fields[k], ensure_ascii=False)))
        size = len(json.dumps(fields[longest], ensure_ascii=False))
        if size <= 40:
            break
        fields[longest] = _text(json.dumps(fields[longest], ensure_ascii=False), max(40, size // 2))
    return fields


def entity(value):
    result = entity_summary(value)
    prompt = value.get("prompt") if isinstance(value.get("prompt"), dict) else {}
    result.update(_drop_empty({
        "ip_id": value.get("ipId"), "fields": _bounded_fields(value.get("resolved")),
        "prompt": _drop_empty({"positive": _text(prompt.get("positive"), 3000),
                               "negative": [_text(n, 300) for n in (prompt.get("negative") or [])[:20]]}),
        "references": [_drop_empty({
            "id": r.get("id"), "item_id": r.get("libraryItemId"), "kind": r.get("kind"), "view": r.get("view"),
            "purposes": r.get("purposes"), "status": r.get("status"), "label": _text(r.get("label"), 120),
            "guidance": _text(r.get("guidance"), 500), "neutral_pose": r.get("neutralPose") or None,
            "version": r.get("entityVersion"), "title": _text(r.get("title"), 200),
            "format": (r.get("media") or {}).get("mimeType") if isinstance(r.get("media"), dict) else None})
            for r in (value.get("references") or [])[:60] if isinstance(r, dict)],
        "variants": [entity_summary(v) for v in (value.get("variants") or [])[:30] if isinstance(v, dict)],
        "versions": [_drop_empty({"version": v.get("version"), "note": _text(v.get("changeNote"), 200),
                                  "date": _text(v.get("createdAt"), 10)})
                     for v in (value.get("versions") or [])[:10] if isinstance(v, dict)],
        "readiness": readiness(value.get("readiness"))}))
    return result


def resolved(value):
    prompt = value.get("prompt") if isinstance(value.get("prompt"), dict) else {}
    return {"fingerprint": value.get("fingerprint"),
            "prompt": {"positive": _text(prompt.get("positive"), 6000) or "",
                       "negative": _text(prompt.get("negative"), 3000) or ""},
            "entities": [_drop_empty({"entity_id": e.get("entityId"), "version": e.get("version"),
                                      "kind": e.get("kind"), "name": _text(e.get("name"), 120)})
                         for e in value.get("entities") or [] if isinstance(e, dict)],
            "references": [_drop_empty({"slot": r.get("slot"), "item_id": r.get("libraryItemId"),
                                        "reference_id": r.get("referenceId"), "entity_id": r.get("entityId"),
                                        "kind": r.get("kind"), "purposes": r.get("purposes"),
                                        "guidance": _text(r.get("guidance"), 500)})
                           for r in value.get("references") or [] if isinstance(r, dict)],
            "note": "References are library items in generation order; import an image into STUDIO to pass it to "
                    "angelo_studio_generate. Record the entity versions when you save the result."}


def not_ready(error, requested):
    """A ``CHARACTER_NOT_READY`` refusal as a result the model can act on (never retried)."""
    entities = []
    for key, messages in (error.fields or {}).items():
        index = key.rsplit(".", 1)[-1]
        if index.isdigit() and int(index) < len(requested):
            entities.append({"entity_id": requested[int(index)]["entity_id"], "missing": messages})
    return {"error": error.message, "code": "CHARACTER_NOT_READY", "not_ready": entities, "fix": NOT_READY_FIX,
            "retry": False}


# Known items ---------------------------------------------------------------------

def remember(org_id, value, **partial):
    """Remember what a result showed about a library item (a full DTO, or partial fields by id)."""
    identity = value.get("id") if isinstance(value, dict) else value
    if not isinstance(identity, str):
        return
    facts = ({"title": value.get("title"), "kind": value.get("kind"), "mimeType": value.get("mimeType"),
              "fileName": value.get("fileName"), "sizeBytes": value.get("sizeBytes"), "space": value.get("space"),
              "by": person(value.get("createdBy"), None), "updatedAt": value.get("updatedAt")}
             if isinstance(value, dict) else {})
    with _known_lock:
        current = _known.pop((org_id, identity), {})
        if isinstance(value, dict) or not current:
            current.update({k: v for k, v in dict(facts, **partial).items() if v is not None})
        _known[(org_id, identity)] = current
        while len(_known) > MAX_KNOWN:
            _known.popitem(last=False)


def known(org_id, item_id):
    with _known_lock:
        return dict(_known.get((org_id, item_id), {}))


def forget_all():
    with _known_lock:
        _known.clear()
