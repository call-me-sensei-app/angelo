# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's library as a Blender asset library: the plan, with no Blender dependency.

Blender's asset system lists datablocks inside .blend files, so every ready Studio item gets a small .blend of
its own in ``<library>/blender-assets`` (``studio_assets_convert`` writes them in a background Blender process,
``studio_assets_host`` registers the folder and runs the process). This module decides which files are missing,
stale or orphaned, and owns the folder's catalogue and manifest.

* A 3D model becomes an Object asset (one object) or a Collection asset (several), catalogue *Models*.
* An image becomes a Material asset, catalogue *Images*: Blender 5.2's Asset Browser lists Image datablocks
  only under the experimental Extended Asset Browser, and a material dropped on an object textures it. The
  material reads the Studio file by a relative path, so the picture is not stored twice.
* One way, Studio to Blender: the Studio record is the source. Blender cannot rename or delete an asset that
  lives in another file from its Asset Browser, so nothing flows back; a record that changes (a folder move, a new
  file) rewrites its .blend, and a record that disappears removes the .blend this folder made for it.
"""

import contextlib
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from .studio_state import atomic_json, read_json
from .ui_text import n_

LIBRARY_NAME = "Angelo Media Studio"   # Product name, as "Media Studio" is in every catalogue (glossary).
FOLDER = "blender-assets"
CONVERTER_VERSION = 3   # Raise to rewrite every file after a change in what a file holds (3: real model data again).
BATCH = 40              # Items per background process: bounded memory, and new items are not stuck behind a backfill.
# Fixed identities: a catalogue's name follows the interface language, its assets keep their catalogue.
CATALOGS = {"3D": ("5c0e3e1a-7d0b-4c8e-9a51-0f6a2d3b7e11", n_("Models"), "models"),
            "IMAGE": ("9b2f6c47-1e83-4d5a-b7c2-3a8e4f1d6c22", n_("Images"), "images")}
# The parent catalogue's path is fixed and never translated: an Asset Shelf tab is stored by catalogue path, so a
# translated path would lose the tab when the language changes. Its two children carry the translated names.
TAB_PATH = LIBRARY_NAME
PARENT_CATALOG = "3f1d2b6e-8a4c-4e0f-9b7d-5c6a1e2f3d44"
CATS_FILE = "blender_assets.cats.txt"
MANIFEST = "manifest.json"
REGISTRATION = "registration.json"
LOCK = ".sync.lock"


def asset_directory(library_dir):
    return Path(library_dir) / FOLDER


HEADER = ("# This is an Asset Catalog Definition file for Blender.", "#",
          "# Angelo writes its two catalogues; catalogues you add here are kept.", "", "VERSION 1", "")


def _clean(name):
    return " ".join(str(name).replace(":", " ").replace("/", " ").replace("\\", " ").split())


def _catalog_lines(names):
    lines = {PARENT_CATALOG: f"{PARENT_CATALOG}:{TAB_PATH}:{TAB_PATH}"}
    for kind, (identity, english, _folder) in CATALOGS.items():
        name = _clean(names.get(kind) or english)
        lines[identity] = f"{identity}:{TAB_PATH}/{name}:{name}"
    return lines


def catalog_kind(path, names=None):
    """``"3d"`` or ``"image"`` for one of Angelo's two catalogue paths or any catalogue the person made under one
    (``.../Images/Skies``), in the interface language or English, else None. ``names`` maps a kind to its catalogue
    name; by default the interface language's (main thread)."""
    if names is None:
        from .ui_text import iface_
        names = {kind: iface_(english) for kind, (_identity, english, _folder) in CATALOGS.items()}
    for kind, (_identity, english, _folder) in CATALOGS.items():
        for root in {f"{TAB_PATH}/{_clean(names.get(kind) or english)}", f"{TAB_PATH}/{english}"}:
            if path == root or path.startswith(root + "/"):
                return "3d" if kind == "3D" else "image"
    return None


def catalog_text(names, existing=""):
    """Blender's catalogue definition file: Angelo's parent catalogue (``TAB_PATH``) and its two children in
    ``names``' language, every other line kept.

    ``names`` maps a kind to its (translated) catalogue name. Catalogues the person adds in the Asset Browser
    (Blender saves them into this file) stay as they are.
    """
    ours = _catalog_lines(names)
    kept = []
    for line in existing.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("VERSION"):
            continue
        if stripped.split(":", 1)[0] not in ours:
            kept.append(line)
    return "\n".join((*HEADER, *ours.values(), *kept)) + "\n"


def write_catalogs(directory, names):
    """Write the catalogue file when Angelo's lines differ (first run, another language); True when written."""
    path = Path(directory) / CATS_FILE
    try:
        existing = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        existing = ""
    lines = {line.strip() for line in existing.splitlines()}
    if "VERSION 1" in lines and set(_catalog_lines(names).values()) <= lines:
        return False   # Blender may save the file with its own header; Angelo's lines are what matter.
    text = catalog_text(names, existing)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def asset_name(prompt, limit=60):
    """The asset's name: the prompt's first line, shortened. Names are the person's text, never translated."""
    line = " ".join(str(prompt or "").split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def tags(record, folders):
    """Tags Blender's asset search matches: the model and the Studio folder. The prompt is the name and description."""
    found = [str(record.get("model") or "")[:60]]
    folder = folders.get(record.get("folder") or "")
    if folder:
        found.append(str(folder.get("name") or "")[:60])
    return [t for i, t in enumerate(found) if t and t not in found[:i]]


def _file_state(path):
    stat = path.stat()
    return [path.name, stat.st_size, stat.st_mtime_ns]


def item(library_dir, record, folders):
    """What a ready record's asset file holds, or None when there is nothing to list (not ready, files missing)."""
    kind = record.get("kind")
    if record.get("status") != "ready" or kind not in CATALOGS:
        return None
    try:
        identity = str(uuid.UUID(record["id"]))
    except (KeyError, TypeError, ValueError):
        return None
    root = Path(library_dir) / "outputs" / identity

    def regular(name):
        path = root / str(name or "")
        if not name or Path(name).name != name or path.is_symlink() or not path.is_file():
            return None
        return path
    names = ["model.glb"] if kind == "3D" else [n for n in record.get("files") or [] if isinstance(n, str)]
    files = [p for p in map(regular, names) if p]
    if not files or (kind == "3D" and "model.glb" not in (record.get("files") or [])):
        return None
    thumbnail = regular(record.get("thumbnail")) if kind == "3D" else None
    catalog, _name, folder = CATALOGS[kind]
    blend = asset_directory(library_dir) / folder / (identity + ".blend")
    state = {"version": CONVERTER_VERSION, "kind": kind, "prompt": record.get("prompt", ""),
             "model": record.get("model", ""), "tags": tags(record, folders),
             "files": [_file_state(p) for p in files], "thumbnail": _file_state(thumbnail) if thumbnail else None}
    # ASCII escapes: any text a record holds (a lone surrogate too) has a fingerprint.
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True).encode("ascii")).hexdigest()
    return {"id": identity, "kind": kind, "name": asset_name(record.get("prompt")) or identity[:8],
            "description": str(record.get("prompt") or "")[:4000], "tags": state["tags"], "catalog_id": catalog,
            "files": [str(p) for p in files], "thumbnail": str(thumbnail) if thumbnail else "",
            "blend": str(blend), "fingerprint": fingerprint}


def plan(library_dir, records, folders, manifest, session=""):
    """``(convert, remove)``: items whose file is missing or stale, and manifest ids whose Media Studio item is gone.

    A fingerprint that failed in this session is not retried until the record, its files or the converter change;
    the next session tries it once more (a failure can be an interrupted process, not a bad file), even while an
    older file is still there. A model written without its preview (the render failed, or ended its writer where a
    background Blender has no usable GPU context) is written again once in each later session, marked
    ``preview_only``, so a machine that can render later gets its preview. ``remove`` holds only items whose record
    is positively deleted (``removable``).
    """
    wanted = {}
    for record in records:
        try:
            found = item(library_dir, record, folders)
        except (OSError, TypeError, ValueError, AttributeError):
            continue   # One damaged record is left out; the rest of the library still lists.
        if found:
            wanted[found["id"]] = found
    convert = []
    for identity, found in wanted.items():
        entry = manifest.get(identity) or {}
        if entry.get("fingerprint") == found["fingerprint"]:
            if entry.get("failed"):
                if entry.get("session") == session:
                    continue   # Failed in this session: not again until something changes or a new session.
            elif Path(found["blend"]).is_file():
                if not entry.get("preview_missing") or entry.get("session") == session:
                    continue
                found = dict(found, preview_only=True)   # Written; its preview is tried once in this session.
        convert.append(found)
    remove = removable(library_dir, sorted(set(manifest) - set(wanted)))
    return convert, remove


def removable(library_dir, identities):
    """Of ``identities`` (manifest ids no listed item wants), those whose copy may be deleted: the item's record file
    is positively gone and every record in the folder reads cleanly. An item left out for any other reason (its
    record unreadable or over the reader's size limit, not ready, its model file missing, past the reader's count
    limit, the records folder unreachable) keeps its copy: since 1.0.5 a copy holds the only Blender data of its
    model, and a scene that links it would lose it. A key that is no item id is only forgotten (no file).

    "Gone" is proven, never assumed: ``os.path.lexists`` and ``Path.glob`` answer "nothing there" for a folder they
    can't read (a permission or I/O error), which deleted every copy when the records folder was unreadable."""
    records = Path(library_dir) / "assets"
    ids, keys = [], []
    for identity in identities:
        try:
            ids.append(str(uuid.UUID(identity)))
        except (TypeError, ValueError, AttributeError):
            keys.append(identity)
    gone = [i for i in ids if _absent(records / (i + ".json"))]
    if gone:
        blocking = unclean_record(records)
        if blocking is not None:
            if blocking not in _blocking_logged:   # Once per record a session: the copies wait for it.
                _blocking_logged.add(blocking)
                print("Angelo: Media Studio record %s does not read cleanly; the asset library deletes no copy until "
                      "it does" % blocking)
            gone = []
    return keys + gone


_blocking_logged = set()


def _absent(path):
    """True only when nothing is at ``path`` (``FileNotFoundError``); False when something is or it can't be told."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return False


def _json_names(folder):
    """The names ``StudioLibrary.reload`` reads (``Path.glob("*.json")``'s matches, with its casing rules), listed
    so that a folder that can't be read raises ``OSError`` instead of matching nothing."""
    with os.scandir(folder) as entries:
        return sorted(entry.name for entry in entries if os.path.normcase(entry.name).endswith(".json"))


def unclean_record(records):
    """The first ``*.json`` in the records folder the reader would not accept (not a dict with its own id, over 1 MiB,
    as ``studio_state.read_json`` reads it), the folder itself when it cannot be listed, or None when all are clean."""
    try:
        paths = [Path(records) / name for name in _json_names(records)]
    except (OSError, ValueError):
        return str(records)
    for path in paths:
        try:
            if path.stat().st_size > 1024 * 1024:
                return path.name
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or str(uuid.UUID(value["id"])) != path.stem:
                return path.name
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, AttributeError):
            return path.name
    return None


def records_read_cleanly(records):
    return unclean_record(records) is None


def owned_blend(library_dir, identity):
    """The one path this folder may delete for ``identity``: never anything the folder did not name itself."""
    identity = str(uuid.UUID(identity))
    return [asset_directory(library_dir) / folder / (identity + ".blend") for _c, _n, folder in CATALOGS.values()]


def remove_orphans(library_dir, manifest, identities):
    """Delete the files of ``identities`` and forget them; True when a file went. A key that is no item id is
    forgotten; an id whose file cannot be deleted now (locked on Windows) stays for the next scan."""
    removed = False
    for identity in identities:
        try:
            paths = owned_blend(library_dir, identity)
        except (TypeError, ValueError):
            manifest.pop(identity, None)
            continue
        try:
            for path in paths:
                if path.is_file() and not path.is_symlink():
                    path.unlink()
                    removed = True
        except OSError:
            continue
        manifest.pop(identity, None)
    return removed


def remove_stale_temporaries(library_dir, older_than=3600, now=None):
    """Temporary files a stopped writer left (``<id>.blend.<hex>.tmp``, and Blender's own ``...tmp@`` when it was stopped
    mid-save), once no writer can still own them."""
    import time
    now = time.time() if now is None else now
    for _catalog, _name, folder in CATALOGS.values():
        root = asset_directory(library_dir) / folder
        found = (list(root.glob("*.blend.*.tmp")) + list(root.glob("*.blend.*.tmp@"))) if root.is_dir() else ()
        for path in found:
            try:
                uuid.UUID(path.name.split(".", 1)[0])
                if not path.is_symlink() and now - path.stat().st_mtime > older_than:
                    path.unlink()
            except (OSError, ValueError):
                continue


@contextlib.contextmanager
def sync_lock(directory):
    """Yield True while this process holds the folder's writer lock, False when another Angelo does.

    An operating-system lock on an open file, never waited for; it ends with the process, so a crash leaves none.
    """
    Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(Path(directory) / LOCK, "a+b")
    try:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()   # Closing releases the lock on every platform.


def read_manifest(directory):
    """The manifest, whatever its size (``read_json`` caps records at 1 MiB; a capped manifest would read as empty
    and every item would be written again on every scan)."""
    try:
        value = json.loads((Path(directory) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        value = {}
    return {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}


def write_manifest(directory, manifest):
    atomic_json(Path(directory) / MANIFEST, manifest)


def apply_results(manifest, batch, results, session=""):
    """Record each converted item; a failure keeps its fingerprint so the same input is not retried every scan, and
    a model written without its preview is marked for one more try in a later session (``plan``)."""
    for found in batch:
        result = results.get(found["id"]) or {"ok": False, "error": "no result"}
        entry = {"fingerprint": found["fingerprint"], "blend": Path(found["blend"]).name}
        if not result.get("ok"):
            entry.update(failed=True, session=session, error=str(result.get("error") or "")[:300])
        elif result.get("preview"):
            entry.update(preview_missing=True, session=session, preview=str(result["preview"])[:300])
        manifest[found["id"]] = entry


# Registration: the folder's entry in Preferences > File Paths > Asset Libraries.

def _path_text(path):
    text = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(str(path)))))
    return text.rstrip("\\/") or text


def same_directory(first, second):
    """Whether two library paths name one folder, by their text alone.

    Never asks the file system: it runs on the main thread for every library in Preferences, and a library on an
    unreachable network share would stall the interface for as long as the share takes to answer.
    """
    try:
        return _path_text(first) == _path_text(second)
    except (TypeError, ValueError):
        return False


def find_library(libraries, directory):
    """The registered entry for ``directory`` (whatever the person renamed it to), else None."""
    return next((library for library in libraries if same_directory(library.path, directory)), None)


def registration_decision(libraries, directory, record):
    """What startup does: ``"present"``, ``"add"`` or ``"removed"`` (the person took it out; it stays out)."""
    if find_library(libraries, directory):
        return "present"
    if record.get("removed") and same_directory(record.get("directory", ""), directory):
        return "removed"
    return "add"


def read_registration(directory):
    value = read_json(Path(directory) / REGISTRATION, {})
    return value if isinstance(value, dict) else {}


def write_registration(directory, *, removed):
    atomic_json(Path(directory) / REGISTRATION, {"directory": str(directory), "removed": bool(removed)})
