# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web's engine-link adapter: a developer's own Three.js page, through the Angelo Link plugin of its dev server
(``@call-me-sensei/angelo``; ``engine_link_assets`` has the adapter contract, doc/angelo/web_link.rst is the web's).

* A project is a folder holding ``package.json`` (the dev server's root). An asset is a ``.glb`` the page loads,
  named on the wire by its path in the project, ``/``-separated (``public/models/chair.glb``). The plugin maps the
  page's URL to that path; Angelo never sees a URL.
* Angelo edits a working .blend in the project's ``.angelo`` folder and writes the .glb back on every save, with the
  mesh compression and image format the file already had, so the page decodes the new file as it did the old one.
* **Live scenes** are written like the save but uncompressed, with images embedded and no animation
  (``live_options``), under ``LIVE_EXPORT_ID``: the add-on's glTF hooks then give each node its identity for this
  Angelo process (``extras.angelo_uid``, ``web_export.WebExportExtension``), which lets the page keep its own objects
  through a rename. Saved files never carry it.
* Nothing is copied into the project (``PLUGIN_SOURCE`` None): the developer installs the npm package.
"""

import json
import os
import re
import stat
import struct
from pathlib import Path

from .engine_link_assets import Asset, AssetError, inside, live_gltf
from .engine_link_facts import listing
from .web_link_live import live_frames, live_time_counts  # noqa: F401 - the adapter's frames (engine_link_live).

ENGINE, DISPLAY = "web", "Three.js"   # DISPLAY: a name, never translated ("Live in Three.js: chair.glb").
CAPABILITIES = ("durable", "live")
KINDS = (".glb",)
LIVE_EXPORT_ID = "angelo_link_web"
PACKAGE = "@call-me-sensei/angelo"
PLUGIN_SOURCE = None   # An npm package the developer adds (``install_hint``), never a copy from Angelo's bundle.
NAME_ROOT, NAME_PREFIX = "", ""
NAME_IGNORED = ("", "")
# Folders a by-name lookup and an asset never come from: installed packages, build output and caches the dev server
# or a bundler writes (a project's node_modules alone outgrows ``engine_link_assets.NAME_SCAN``). Dot-folders are
# skipped for every engine.
NAME_SKIPPED = frozenset({"node_modules", "dist", "build", "out", "coverage", "bower_components"})
ROOT_SCAN = 5000   # Entries of the project's own folder ``_resolves_into_skipped`` reads; past it, refused.
MAX_GLB_JSON = 16 * 1024 * 1024   # The JSON chunk ``import_params`` reads at most (once, when the asset opens, on the
# main thread); a larger one is left unread and the save writes the plain form.

REFUSED = {   # code -> English, shown by the plugin on the page and in the dev server; ``asset`` is the project path.
    "not_project": "That folder isn't a web project (it has no package.json)",
    "outside": "{asset} is outside the project's own files (Angelo doesn't edit node_modules or build output)",
    "kind": "Angelo can edit .glb models on the web; {asset} is another kind",
    "missing": "{asset} doesn't exist yet. Save it in the project first",
}

# How a save writes the .glb: what Angelo's web export writes (Y up, lights in the unitless mode three.js scenes use,
# cameras, GPU instancing), with modifiers applied and the person's custom properties left out.
WEB_DURABLE = {
    "export_apply": True,
    "export_yup": True,
    "export_cameras": True,
    "export_lights": True,
    "export_extras": False,
    "export_import_convert_lighting_mode": "COMPAT",
}
MESHOPT, DRACO, WEBP = "EXT_meshopt_compression", "KHR_draco_mesh_compression", "EXT_texture_webp"
INSTANCING = "EXT_mesh_gpu_instancing"


def is_project(folder):
    return isinstance(folder, str) and os.path.isfile(os.path.join(folder, "package.json"))


def resolve(request):
    """The ``Asset`` for the request's ``asset`` (a path in the project, ``/``-separated) in the web project
    ``project``; raises AssetError."""
    project, path = request.get("project"), request.get("asset")
    if not isinstance(path, str) or not path or len(path) > 1024 or path.startswith(("/", "\\")):
        raise AssetError("outside", str(path)[:200], REFUSED["outside"])
    if not isinstance(project, str) or not os.path.isabs(project) or not is_project(project):
        raise AssetError("not_project", path, REFUSED["not_project"])
    if _skipped(path.replace("\\", "/").split("/")[:-1]):
        raise AssetError("outside", path, REFUSED["outside"])
    root, file, parts = inside(project, path, REFUSED, path, KINDS)
    if _resolves_into_skipped(root, file):
        raise AssetError("outside", path, REFUSED["outside"])
    relative = "/".join(parts)
    return Asset(ENGINE, str(root), relative, str(file), file.suffix.lower(), relative)


def _skipped(folders):
    """Whether any of ``folders`` (names) is one of ``NAME_SKIPPED``, as a file system that ignores case and trailing
    dots and spaces (Windows) reads it."""
    return any(part.rstrip(". ").casefold() in NAME_SKIPPED for part in folders)


def _resolves_into_skipped(root, file):
    """Whether ``file`` lies in a skipped folder once resolved: through a link (a pnpm or workspace link from
    ``public`` into ``node_modules``), or by a name the file system folds into one (8.3 names, trailing dots), so the
    write-back never edits a file a package manager rewrites (break-it F3). The resolved path's folders are checked
    by name, and each skipped folder of the project by identity."""
    try:
        resolved = Path(file).resolve()
        if _skipped(resolved.relative_to(root).parts[:-1]):
            return True
        for count, entry in enumerate(listing(str(root))):   # Entry by entry, bounded, as every project walk.
            if count >= ROOT_SCAN:
                return True   # Unchecked: refused, never assumed.
            name = entry.name
            if name.rstrip(". ").casefold() in NAME_SKIPPED and resolved.is_relative_to((root / name).resolve()):
                return True
    except (OSError, ValueError):
        return True   # Unreadable or outside the project once resolved: refused, never assumed.
    return False


MAX_TOKENS = 1000000   # Strings and brackets ``_top_level_used`` reads (Blender writes extensionsUsed first).
TAIL = 64 * 1024      # Characters ``_last_used`` reads from the end (three.js's exporter writes the list last).
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]]')


def _top_level_used(text):
    """The document's own ``extensionsUsed`` list: the key at the top level only, never one inside extras (break-it
    round 2), found by a scan of strings and brackets, with no parse of the whole document."""
    depth = 0
    for count, token in enumerate(TOKEN.finditer(text)):
        if count > MAX_TOKENS:
            return _last_used(text)   # Too long to scan on the main thread: the list written last (three.js).
        value = token.group()
        if value in "{[":
            depth += 1
        elif value in "}]":
            depth -= 1
        elif depth == 1 and (value == '"extensionsUsed"' or "\\" in value and json.loads(value) == "extensionsUsed"):
            rest = text[token.end():].lstrip()
            if rest.startswith(":"):
                found, _end = json.JSONDecoder().raw_decode(rest[1:].lstrip())
                return found
    return []


def _last_used(text):
    """The last ``"extensionsUsed": [...]`` in the chunk's last ``TAIL`` characters: what an exporter that writes
    the list last (three.js) holds, for a document too long to scan whole. Its depth isn't checked: only a page
    whose own extras repeat the key there could mislead it, and then only into keeping a compression."""
    tail = text[-TAIL:]
    index = tail.rfind('"extensionsUsed"')
    rest = tail[index + len('"extensionsUsed"'):].lstrip() if index >= 0 else ""
    if not rest.startswith(":"):
        return []
    try:
        found, _end = json.JSONDecoder().raw_decode(rest[1:].lstrip())
    except ValueError:
        return []
    return found


def glb_extensions(path):
    """The glTF extensions a .glb at ``path`` uses (``extensionsUsed``), found in its JSON chunk without parsing the
    whole document (it runs on the main thread when the asset opens: break-it F4); an empty set when the file isn't
    a regular .glb Angelo can read (a save then writes the plain form). Never raises."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return frozenset()
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):   # Never a FIFO or device swapped in after resolve.
            return frozenset()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            head = handle.read(20)
            if len(head) < 20 or head[:4] != b"glTF":
                return frozenset()
            length, kind = struct.unpack("<II", head[12:20])
            if kind != 0x4E4F534A or not 0 < length <= MAX_GLB_JSON:   # "JSON"
                return frozenset()
            used = _top_level_used(handle.read(length).decode("utf-8"))
    except (OSError, ValueError, UnicodeError, struct.error, RecursionError, MemoryError):
        return frozenset()
    finally:
        os.close(descriptor)
    return frozenset(item for item in used if isinstance(item, str)) if isinstance(used, list) else frozenset()


def import_params(asset):
    """What the asset file already uses, so its save keeps it: mesh compression and WebP images."""
    used = glb_extensions(asset.path)
    return {"meshopt": MESHOPT in used, "draco": DRACO in used, "webp": WEBP in used, "instancing": INSTANCING in used}


def live_options(params):
    """The live scene: the save's layout, uncompressed (Angelo's live exports pay no encoder time), images embedded
    as they are, no animation and at the current frame."""
    return live_gltf(dict(WEB_DURABLE, export_image_format="AUTO", export_draco_mesh_compression_enable=False,
                          export_meshopt_compression_enable=False, export_gpu_instances=False))


def durable(asset, params):
    """How saving writes the .glb: the web layout with the compression and image format the file had. A KTX2 file
    is written with its images' own PNG and JPEG (Angelo's write-back runs no gltfpack)."""
    params = params or {}
    meshopt = bool(params.get("meshopt"))
    return "gltf", dict(WEB_DURABLE, export_image_format="WEBP" if params.get("webp") else "AUTO",
                        export_gpu_instances=bool(params.get("instancing")),
                        export_meshopt_compression_enable=meshopt,
                        export_draco_mesh_compression_enable=bool(params.get("draco")) and not meshopt)


def install_hint():
    """The two steps a developer takes in a web project, in English (code and package names are never translated)."""
    return ("npm i -D %s" % PACKAGE,
            "import { angeloLive } from \"%s/vite\";  // plugins: [angeloLive()]" % PACKAGE)
