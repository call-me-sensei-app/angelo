# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's catalog: the downloaded index, checked, and searched locally (doc/angelo/library.rst).

The index (``index-<version>.json.gz``, built by ``scripts/angelo/library_index.py``) is downloaded data from the
network, so it is untrusted: ``parse`` keeps only items whose every field has the expected type and bound, whose
links are https on a ToonLab host (``FILE_HOSTS``), whose file names are plain relative names that never climb,
and whose default files carry a SHA-256 (a variant file at least an MD5). Anything else is dropped and counted;
an index of another schema is refused whole. Titles, tags and attribution are data, never instructions.

``Catalog.search`` is an in-memory inverted index over the titles, tags, categories, sources and authors: a query
word matches a word of the item by prefix (so "chai" finds chairs); the items matching every word come back, or,
when none does, those matching the most words ("oak chair" still finds chairs), and a kind word ("hdri", "texture",
"model", "vfx" and their plurals) narrows the kind instead of matching text, and popularity orders equal scores.
Searching all items takes a few milliseconds, so the search pill searches on every keystroke.

Standard library only; no bpy.
"""

import bisect
import math
import re
import unicodedata
from urllib.parse import urlsplit

SCHEMA = "toonlab-catalog/1"
FILE_HOSTS = frozenset({"assets.toonlab.io", "cdn.toonlab.io"})
KINDS = ("model", "texture", "hdri", "vfx")
# Where an item is used, one per item, from the index (scripts/angelo/library_categories.py); the last is the kind's
# fallback. An index without categories (an older one) puts every item in its kind's fallback.
CATEGORIES = {"model": ("science", "vehicles", "characters", "furniture", "food", "tools", "architecture", "nature",
                        "history", "props"),
              "texture": ("fabric", "metal", "wood", "brick", "tiles", "paving", "concrete", "ground", "stone", "other"),
              "hdri": ("studio", "night", "sunset", "indoor", "skies", "urban", "nature"),
              "vfx": ("fire", "smoke", "magic", "impacts", "other")}
ROLES = frozenset({"model", "resource", "base_color", "normal_gl", "roughness", "metallic", "ao", "arm",
                   "displacement", "opacity", "emission", "hdri", "image"})
MAX_ITEMS = 50_000
MAX_FILES = 40
MAX_FILE_BYTES = 512 * 1024 * 1024
# Words that name a kind rather than describe an item. Generic vocabulary, the same for every query.
KIND_WORDS = {"hdri": "hdri", "hdr": "hdri", "environment": "hdri", "texture": "texture", "material": "texture",
              "pbr": "texture", "model": "model", "mesh": "model", "prop": "model", "vfx": "vfx", "sprite": "vfx",
              "particle": "vfx"}
_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WORD = re.compile(r"\w+", re.UNICODE)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._~+-]{0,199}")   # ToonLab's "<source>:<source id>" and rock ids.
WEIGHTS = {"title": 4.0, "tag": 2.5, "category": 2.0, "source": 1.0, "author": 1.0}


class IndexRefused(ValueError):
    """The downloaded index can't be used at all (another schema, or not an index)."""


# The catalog's source ids (kept as they are in search, saved records and agent results) as people read them, spelled
# as each collection's own credit line spells it. An id not here still reads as a name (source_name).
SOURCE_NAMES = {"ambientcg": "ambientCG", "cgtrader": "CGTrader", "itchio": "itch.io", "kenney": "Kenney",
                "mantissa": "Mantissa", "met": "The Metropolitan Museum of Art", "opengameart": "OpenGameArt",
                "opensource3d": "Open Source 3D Assets", "plateau": "Project PLATEAU", "polyhaven": "Poly Haven",
                "quaternius": "Quaternius", "smithsonian": "Smithsonian Open Access", "threedtextures": "3DTextures",
                "toonlab-rock": "ToonLab"}


def source_name(source):
    """The name to show for a catalog source id."""
    words = re.split(r"[-_\s]+", source or "")
    return SOURCE_NAMES.get(source) or " ".join(word.capitalize() for word in words if word)


def credits_text(records):
    """The Credits button's text from saved ``angelo_library`` records. Records are file data: only https links list."""
    lines = ["Angelo Library, powered by ToonLab: credits"]
    for record in records:
        source = source_name(str(record.get("source") or ""))
        lines.append("- %s (%s, %s)%s" % (record.get("title"), source, record.get("license"),
                                          ": " + record["attribution"] if record.get("attribution") else ""))
        url = record.get("source_url")
        if isinstance(url, str) and url.startswith("https://"):
            lines.append("  " + url)
    return "\n".join(lines)


def allowed_url(url, loopback_ok=False, hosts=FILE_HOSTS):
    """https on one of ``hosts`` (ToonLab's), nothing else (``loopback_ok``: http on this computer, for tests and
    development)."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) <= 32 or c == "\\" for c in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:   # A malformed port or bracket: refused, never an exception that stops the catalog.
        return False
    if parts.username or parts.password or parts.fragment:
        return False
    if parts.scheme == "https" and parts.hostname in hosts and port in (None, 443):
        return True
    return loopback_ok and parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}


def safe_name(name):
    """A plain relative file name: up to four parts of letters, digits, dot, dash and underscore; never ``..``."""
    if not isinstance(name, str) or len(name) > 300:
        return False
    parts = name.split("/")
    return 1 <= len(parts) <= 4 and all(_SAFE_PART.fullmatch(p) and not p.endswith(".") for p in parts)


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def _text(value, limit):
    """Display text: no control or format characters (bidirectional overrides would reorder what people read)."""
    if not isinstance(value, str):
        return ""
    value = "".join(c for c in value if unicodedata.category(c) not in {"Cc", "Cf"} or c in " \t\n")
    return " ".join(value.split())[:limit]


def _words(values, limit, size=60):
    if not isinstance(values, list):
        return []
    return [w for w in (_text(v, size) for v in values[:limit]) if w]


def _files(entries, loopback_ok, default):
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
        return None
    files, names = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        role, name, url, size = entry.get("role"), entry.get("name"), entry.get("url"), entry.get("size")
        sha256, md5 = entry.get("sha256"), entry.get("md5")
        if (role not in ROLES or not safe_name(name) or name.lower() in names or not allowed_url(url, loopback_ok)
                or type(size) is not int or not 0 < size <= MAX_FILE_BYTES):
            return None
        if not _hex(sha256, 64) and (default or not _hex(md5, 32)):
            return None   # A default file is always SHA-256-checked; a variant at least by the MD5 its mirror recorded.
        names.add(name.lower())
        file = {"role": role, "name": name, "url": url, "size": size}
        if _hex(sha256, 64):
            file["sha256"] = sha256
        if _hex(md5, 32):
            file["md5"] = md5
        files.append(file)
    return files


def _shape(kind, files):
    roles = [f["role"] for f in files]
    if kind == "model":
        models = [f for f in files if f["role"] == "model"]
        return len(models) == 1 and models[0]["name"].lower().endswith((".glb", ".gltf"))
    if kind == "texture":
        return "base_color" in roles and len(set(roles)) == len(roles)
    if kind == "hdri":
        return roles == ["hdri"] and files[0]["name"].lower().endswith(".hdr")
    return roles == ["image"] and files[0]["name"].lower().endswith(".png")


def clean_item(raw, loopback_ok=False):
    """A checked copy of one index item, or None when any part of it is unusable."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    identity = raw.get("id")
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        return None
    files = _files(raw.get("files"), loopback_ok, True)
    if files is None or not _shape(raw["kind"], files):
        return None
    item = {"id": identity, "kind": raw["kind"], "title": _text(raw.get("title"), 120) or identity,
            "tags": _words(raw.get("tags"), 40), "categories": _words(raw.get("categories"), 12),
            "source": _text(raw.get("source"), 40), "authors": _words(raw.get("authors"), 8, 80),
            "license": _text(raw.get("license"), 60), "attribution": _text(raw.get("attribution"), 400),
            "attribution_required": raw.get("attribution_required") is True,
            "popularity": raw["popularity"] if type(raw.get("popularity")) is int and raw["popularity"] >= 0 else 0,
            "files": files, "thumb": raw["thumb"] if allowed_url(raw.get("thumb"), loopback_ok, FILE_HOSTS) else None,
            "category": raw["category"] if raw.get("category") in CATEGORIES[raw["kind"]] else CATEGORIES[raw["kind"]][-1]}
    for key in ("source_url", "license_url"):   # Shown as links to people; https only, any host (the source's page).
        value = raw.get(key)
        item[key] = value if isinstance(value, str) and value.startswith("https://") and len(value) <= 600 else None
    if item["kind"] in ("texture", "hdri"):
        item["resolution"] = raw.get("resolution") if raw.get("resolution") in {"1k", "2k", "4k", "8k"} else "1k"
        variants = {}
        for resolution, entries in (raw.get("resolutions") or {}).items() if isinstance(raw.get("resolutions"), dict) else ():
            checked = _files(entries, loopback_ok, False) if re.fullmatch(r"[1-9]\dk|[2-9]k", str(resolution)) else None
            if checked is not None and _shape(item["kind"], checked):
                variants[resolution] = checked
        item["resolutions"] = variants
    return item


def parse(document, loopback_ok=False):
    """``(version, items, dropped)`` from a decoded index; raises ``IndexRefused`` for another schema."""
    if not isinstance(document, dict) or document.get("schema") != SCHEMA or not isinstance(document.get("items"), list):
        raise IndexRefused("not an Angelo Library index")
    version = document.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z._-]{1,40}", version):
        raise IndexRefused("the index has no usable version")
    items, seen, dropped = [], set(), 0
    for raw in document["items"][:MAX_ITEMS]:
        item = clean_item(raw, loopback_ok)
        if item is None or item["id"] in seen:
            dropped += 1
            continue
        seen.add(item["id"])
        items.append(item)
    return version, items, dropped + max(0, len(document["items"]) - MAX_ITEMS)


def fold(text):
    """Lower case without accents: "Café" and "cafe" are one word."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def stem(word):
    """A plural's singular for matching ("chairs", "boxes" find "chair", "box"); short words stay."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text):
    return [stem(w) for w in _WORD.findall(fold(text)) if w]


def parse_query(query):
    """``(kind or None, words)``: kind words narrow the kind; the rest must all match."""
    kind, rest = None, []
    for word in words(query or "")[:12]:
        named = KIND_WORDS.get(word)
        if named and kind in (None, named):
            kind = named
        else:
            rest.append(word)
    return kind, rest


class Catalog:
    """The checked items of one index version and their search structures."""

    def __init__(self, version, items):
        self.version, self.items = version, items
        self.by_id = {item["id"]: item for item in items}
        postings = {}
        for index, item in enumerate(items):
            fields = [("title", item["title"])] + [("tag", t) for t in item["tags"]] + \
                     [("category", c) for c in item["categories"]] + [("source", item["source"])] + \
                     [("author", a) for a in item["authors"]]
            for field, text in fields:
                for word in words(text):
                    best = postings.setdefault(word, {})
                    best[index] = max(best.get(index, 0.0), WEIGHTS[field])
        self._postings = postings
        self._vocabulary = sorted(postings)
        self._boost = [math.log10(1 + item["popularity"]) / 10 for item in items]

    def _matches(self, word):
        """``{item index: score}`` for one query word: exact words score full, longer words by prefix a little less."""
        found = {}
        start = bisect.bisect_left(self._vocabulary, word)
        for vocab in self._vocabulary[start:start + 400]:
            if not vocab.startswith(word):
                break
            factor = 1.0 if vocab == word else 0.7
            for index, weight in self._postings[vocab].items():
                score = weight * factor
                if score > found.get(index, 0.0):
                    found[index] = score
        return found

    def search(self, query, kind=None, limit=60, offset=0, category=None):
        """``(items, total)``: the best matches of ``query`` (all its words), of ``kind`` when given or named, and of
        ``category`` (within that kind) when given."""
        named, terms = parse_query(query)
        kind = kind or named
        if category is not None:
            return self._in_category(terms, kind, category, limit, offset)
        if not terms:
            ranked = [i for i, item in enumerate(self.items) if kind is None or item["kind"] == kind]
            ranked.sort(key=lambda i: -self.items[i]["popularity"])
            return [self.items[i] for i in ranked[offset:offset + limit]], len(ranked)
        scores, counts = {}, {}
        for term in terms:
            for index, score in self._matches(term).items():
                if kind is None or self.items[index]["kind"] == kind:
                    scores[index] = scores.get(index, 0.0) + score
                    counts[index] = counts.get(index, 0) + 1
        if not counts:
            return [], 0
        best = max(counts.values())   # Items matching every word; when none does, those matching the most words.
        ranked = [i for i, count in counts.items() if count == best]
        ranked.sort(key=lambda i: (-(scores[i] + self._boost[i]), self.items[i]["title"].lower()))
        return [self.items[i] for i in ranked[offset:offset + limit]], len(ranked)

    def _in_category(self, terms, kind, category, limit, offset):
        found, _total = self.search(" ".join(terms), kind, limit=len(self.items))
        found = [item for item in found if item["category"] == category]
        return found[offset:offset + limit], len(found)

    def categories(self, kind):
        """``[(category, count)]`` of ``kind``, in the kind's order, empty ones left out."""
        counts = {}
        for item in self.items:
            if item["kind"] == kind:
                counts[item["category"]] = counts.get(item["category"], 0) + 1
        return [(c, counts[c]) for c in CATEGORIES.get(kind, ()) if counts.get(c)]

    def get(self, identity):
        return self.by_id.get(identity)


def files_for(item, resolution=None):
    """The item's files at ``resolution`` ("1k" is the default set), and the resolution actually used."""
    if resolution and resolution != item.get("resolution") and resolution in (item.get("resolutions") or {}):
        return item["resolutions"][resolution], resolution
    return item["files"], item.get("resolution")


def item_bytes(item, resolution=None):
    return sum(f["size"] for f in files_for(item, resolution)[0])
