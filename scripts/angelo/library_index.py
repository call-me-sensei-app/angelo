# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the Angelo Library index (powered by ToonLab) from ToonLab's catalog: one versioned static file.

The client never pages ToonLab's live API (120 requests per IP); it downloads one ``index-<version>.json.gz`` from
the library's prefix on ToonLab's public R2 host and searches it locally (doc/angelo/library.rst). This module turns
two catalog exports into that index; ``library_index_files.py`` fetches what the catalog lacks (digests of default
files, mirrored thumbnails) and stages the upload.

Inputs (both read-only exports, see library.rst "Building the index"):

* **Pro** (``cc0_assets`` rows, the live open-asset catalog with the R2 mirrors' resolutions): which items are
  visible, their titles, tags, attribution and mirrored files.
* **OSS** (``catalog_assets`` + ``catalog_asset_files`` from ToonLab's seed releases): the first-party rocks, and
  SHA-256-checked copies on R2 of many open assets, preferred when present.

Rules: an item is listed only when every file it needs is on a ToonLab host (``FILE_HOSTS``) with a known size;
rows the catalog marks ``source_unavailable``, tree recipes (JavaScript-only) and formats Angelo can't import are
left out and counted. Every default file must carry a SHA-256 (from the catalog, or computed by the builder);
higher-resolution variants carry the MD5 the mirror recorded at upload (the object's ETag) and a SHA-256 when known.

Standard library only.
"""

import json
import re
from urllib.parse import quote, urlsplit

import library_categories

SCHEMA = "toonlab-catalog/1"
FILE_HOSTS = frozenset({"assets.toonlab.io", "cdn.toonlab.io"})
TOS_BASE = "https://cdn.toonlab.io"     # ToonLab Pro's public buckets (file_path, thumb_path) behind its CDN.
KINDS = ("model", "texture", "hdri", "vfx")
# Catalog map names -> the index's texture roles (Poly Haven and ambientCG mirrors share the first set).
ROLES = {"diffuse": "base_color", "color": "base_color", "basecolor": "base_color", "normal": "normal_gl",
         "normalgl": "normal_gl", "roughness": "roughness", "metalness": "metallic", "metallic": "metallic",
         "ao": "ao", "arm": "arm", "displacement": "displacement", "height": "displacement", "opacity": "opacity",
         "emission": "emission"}
MAX_FILE_BYTES = 512 * 1024 * 1024   # library_catalog.MAX_FILE_BYTES: the client drops larger files.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
SAFE_RELATIVE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,120}/){0,3}[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class Skip(ValueError):
    """An item left out of the index, with the reason counted in the build report."""


def on_file_host(url):
    parts = urlsplit(url or "")
    return parts.scheme == "https" and parts.hostname in FILE_HOSTS and not parts.username and not parts.port


def tos_url(bucket, path):
    return "%s/%s/%s" % (TOS_BASE, bucket, quote(path, safe="/"))


def _hex(value, length):
    value = str(value or "").lower()
    return value if re.fullmatch("[0-9a-f]{%d}" % length, value) else None


def _file(role, name, url, size, sha256=None, md5=None):
    if not on_file_host(url):
        raise Skip("file not on a ToonLab host")
    if not isinstance(size, int) or size <= 0:
        raise Skip("file size unknown")
    if size > MAX_FILE_BYTES:
        raise Skip("file larger than Angelo imports")
    if not SAFE_RELATIVE.match(name) or ".." in name.split("/"):
        raise Skip("unsafe file name")
    entry = {"role": role, "name": name, "url": url, "size": size}
    if _hex(sha256, 64):
        entry["sha256"] = _hex(sha256, 64)
    if _hex(md5, 32):
        entry["md5"] = _hex(md5, 32)
    return entry


def _ext(url, fallback):
    match = re.search(r"\.([A-Za-z0-9]{2,5})$", urlsplit(url).path)
    return (match.group(1) if match else fallback).lower()


def _authors(metadata):
    authors = metadata.get("authors")
    if isinstance(authors, list):
        return [a for a in authors if isinstance(a, str)][:8]
    if isinstance(authors, dict):
        return [a for a in authors if isinstance(a, str)][:8]
    return []


def _license(row, metadata):
    text = str(row.get("license") or "")
    if text.upper().startswith("CC0"):
        return "CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/", False
    url = metadata.get("licenseUrl")
    return text, url if isinstance(url, str) and url.startswith("https://") else None, True


def _words(values, limit=40):
    seen, out = set(), []
    for value in values or ():
        if not isinstance(value, str):
            continue
        word = " ".join(value.split())[:60]
        if word and word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return out[:limit]


def _maps(texture_files, base_url=TOS_BASE):
    """``{role: (url, size, md5)}`` from a catalog ``textureFiles`` record (an R2 ``url`` or a TOS ``path``)."""
    maps = {}
    for name, record in (texture_files or {}).items():
        role = ROLES.get(str(name).lower().replace("_", "").replace("-", ""))
        if not role or not isinstance(record, dict) or role in maps:
            continue
        url = record.get("url") if on_file_host(record.get("url")) else (
            tos_url("assets", record["path"]) if isinstance(record.get("path"), str) else None)
        if url:
            maps[role] = (url, record.get("sizeBytes"), record.get("md5"))
    return maps


def _map_files(maps, oss_by_url):
    files = []
    for role, (url, size, md5) in sorted(maps.items()):
        known = oss_by_url.get(url) or {}
        files.append(_file(role, "%s.%s" % (role, _ext(url, "png")), url, size or known.get("size"),
                           known.get("sha256"), md5))
    if not any(f["role"] == "base_color" for f in files):
        raise Skip("texture set without a base colour map")
    return files


def _variants(metadata, kind, oss_by_url):
    """Higher resolutions the R2 mirrors added (``metadata.resolutions``), keyed ``"2k"``, ``"4k"``."""
    found = {}
    for resolution, entry in (metadata.get("resolutions") or {}).items():
        if not re.fullmatch(r"\d+k", str(resolution)) or resolution == "1k" or not isinstance(entry, dict):
            continue
        try:
            if kind == "hdri":
                url = entry.get("primaryUrl")
                found[resolution] = [_file("hdri", "environment_%s.%s" % (resolution, _ext(url or "", "hdr")), url,
                                           entry.get("fileSize"), md5=entry.get("md5"))]
            elif kind == "texture":
                found[resolution] = _map_files(_maps(entry.get("textureFiles")), oss_by_url)
        except Skip:
            continue
    return dict(sorted(found.items(), key=lambda pair: int(pair[0][:-1])))


def _model_files(row, metadata, oss):
    fmt = str(metadata.get("format") or ("glb" if row["kind"] == "model" else "")).lower()
    primary = next((f for f in (oss or {}).get("files") or () if f["kind"] == "primary"), None)
    if fmt == "glb":
        if primary and on_file_host(primary["url"]) and primary["url"].lower().endswith(".glb"):
            files = [_file("model", "model.glb", primary["url"], primary["size"], primary["sha256"])]
        elif on_file_host(row.get("download_url")) and str(row["download_url"]).lower().endswith(".glb"):
            mirror = metadata.get("r2Mirror") or {}
            files = [_file("model", "model.glb", row["download_url"], metadata.get("fileSize"), mirror.get("sha256"),
                           mirror.get("md5"))]
        elif row.get("file_path"):
            files = [_file("model", "model.glb", tos_url("assets", row["file_path"]), metadata.get("fileSize"))]
        else:
            raise Skip("model not on a ToonLab host")
        if oss and oss.get("source") == "toonlab-rock":   # First-party rocks: KTX2 textures in the GLB, PNG maps beside.
            files += _rock_maps(oss)
        return files
    if fmt == "gltf":
        url = row.get("download_url")
        if not on_file_host(url):
            raise Skip("model not on a ToonLab host")
        resources = metadata.get("modelResources") or {}
        files = []
        for relative, record in sorted(resources.items()):
            if not isinstance(record, dict) or not on_file_host(record.get("url")):
                raise Skip("glTF resource not on a ToonLab host")
            files.append(_file("resource", relative, record["url"], record.get("sizeBytes"), md5=record.get("md5")))
        name = urlsplit(url).path.rsplit("/", 1)[-1]
        size = (metadata.get("fileSize") or 0) - sum(f["size"] for f in files)   # The catalog's size is the total.
        return [_file("model", name if SAFE_NAME.match(name) else "model.gltf", url, size if size > 0 else None)] + files
    raise Skip("model format not imported (%s)" % (fmt or "unknown"))


ROCK_MAPS = {"baseColor.png": "base_color", "normalGL.png": "normal_gl", "roughness.png": "roughness", "ao.png": "ao"}


def _rock_maps(oss):
    return [_file(ROCK_MAPS[f["path"]], f["path"], f["url"], f["size"], f["sha256"])
            for f in oss.get("files") or () if f["path"] in ROCK_MAPS]


def _thumbnail(row, metadata, oss):
    """The item's thumbnail on a ToonLab host (its own render, else ToonLab's mirror of the source's preview), else
    None: a preview on a source's CDN is never listed (ToonLab's sync-thumbnails-r2 mirrors those)."""
    for f in (oss or {}).get("files") or ():
        if f["kind"] == "thumbnail" and on_file_host(f["url"]):
            return f["url"]
    if oss and on_file_host(oss.get("thumbnail_url")):
        return oss["thumbnail_url"]
    path = row.get("thumb_path") or row.get("styled_thumb_path")   # The plain render first: imports are plain PBR.
    if path:
        return tos_url("thumbnails", path)
    url = metadata.get("thumbnailUrl")   # ToonLab's mirror of the source's preview, as its site shows it.
    return url if isinstance(url, str) and on_file_host(url) else None


def build_item(row, oss=None, oss_by_url=None):
    """One index item from a Pro row (and its OSS release row, when there is one); raises ``Skip``."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    oss_by_url = oss_by_url or {}
    if metadata.get("availability") == "source_unavailable":
        raise Skip("source unavailable")
    kind = row.get("kind")
    if kind not in KINDS:
        raise Skip("kind not listed (%s)" % kind)
    if kind == "model":
        files = _model_files(row, metadata, oss)
    elif kind == "texture":
        files = _map_files(_maps(metadata.get("textureFiles")), oss_by_url)
    elif kind == "hdri":
        url = row.get("download_url")
        files = [_file("hdri", "environment_1k.%s" % _ext(url or "", "hdr"), url, metadata.get("fileSize"))]
    else:
        primary = next((f for f in (oss or {}).get("files") or () if f["kind"] == "primary"), None)
        if primary:
            files = [_file("image", "sprite.png", primary["url"], primary["size"], primary["sha256"])]
        elif row.get("file_path"):
            files = [_file("image", "sprite.png", tos_url("assets", row["file_path"]), metadata.get("fileSize"))]
        else:
            raise Skip("sprite not on a ToonLab host")
    license_id, license_url, attribution_required = _license(row, metadata)
    item = {"id": row["id"], "kind": kind, "title": " ".join(str(row.get("name") or row["id"]).split())[:120],
            "tags": _words(row.get("tags")), "categories": _words(row.get("categories"), 12),
            "source": row.get("source"), "source_url": row.get("source_url"), "authors": _authors(metadata),
            "license": license_id, "license_url": license_url, "attribution": str(row.get("attribution") or "")[:400],
            "attribution_required": attribution_required, "popularity": int(row.get("popularity") or 0),
            "thumb": _thumbnail(row, metadata, oss), "files": files}
    if kind in ("texture", "hdri"):
        item["resolution"] = "1k"
        variants = _variants(metadata, kind, oss_by_url)
        if variants:
            item["resolutions"] = variants
    if kind == "vfx" and metadata.get("blendMode"):
        item["blend"] = str(metadata["blendMode"])[:20]
    item["category"] = library_categories.categorize(item)
    return item


def rock_row(oss):
    """A first-party rock (only in the OSS releases) shaped as a Pro row."""
    return {"id": oss["id"], "kind": "model", "name": oss["name"], "tags": oss.get("tags") or [], "categories": ["rocks"],
            "source": oss["source"], "source_url": oss.get("source_url"), "license": oss.get("license"),
            "attribution": oss.get("attribution"), "popularity": 0, "download_url": oss.get("download_url"),
            "metadata": {"format": "glb", "authors": ["ToonLab"]}}


def build(pro_rows, oss_rows):
    """``(items, report)``: the index's items in a stable order, and the counts of what was left out and why."""
    oss_by_id = {row["id"]: row for row in oss_rows}
    oss_by_url = {f["url"]: f for row in oss_rows for f in row.get("files") or ()}
    rows = [(row, oss_by_id.get(row["id"])) for row in pro_rows]
    pro_ids = {row["id"] for row in pro_rows}
    rows += [(rock_row(row), row) for row in oss_rows if row.get("source") == "toonlab-rock" and row["id"] not in pro_ids]
    items, skipped = [], {}
    for row, oss in rows:
        try:
            items.append(build_item(row, oss, oss_by_url))
        except Skip as reason:
            skipped[str(reason)] = skipped.get(str(reason), 0) + 1
    items.sort(key=lambda item: (KINDS.index(item["kind"]), -item["popularity"], item["title"].lower(), item["id"]))
    report = {"listed": len(items), "by_kind": {k: sum(i["kind"] == k for i in items) for k in KINDS},
              "by_category": {k: {c: sum(i["kind"] == k and i["category"] == c for i in items)
                                  for c in library_categories.ids(k)} for k in KINDS},
              "skipped": dict(sorted(skipped.items(), key=lambda pair: -pair[1]))}
    return items, report


def document(items, version, generated_at, base_url):
    return {"schema": SCHEMA, "version": version, "generated_at": generated_at, "base_url": base_url,
            "name": "Angelo Library, powered by ToonLab", "items": items}


def needing_digest(items):
    """Default files with no SHA-256: the builder hashes these before the index is written."""
    return [f for item in items for f in item["files"] if "sha256" not in f]


def encode(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
