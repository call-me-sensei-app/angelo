# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bringing a person's earlier settings into Angelo: which folder, what is copied, the backup and its undo.

Standard library only (host-tested on all three folder layouts); ``settings_import_apply`` does the Blender side.
Contract: ``doc/angelo/app_identity.rst``, "Migrating existing settings".

Which folder (``find_source``):

* An earlier **Angelo** version's folder (an Angelo upgrade) wins: Blender's own import copies it
  (``preferences.copy_prev``), so the caller passes what that operator found.
* Otherwise stock **Blender**'s folder (``blender_folders``): the newest version not newer than Angelo's own,
  within the window Blender's import allows, that holds something to bring.
* ``BLENDER_USER_RESOURCES`` or ``BLENDER_USER_CONFIG`` set: Angelo already uses that folder, so nothing is
  brought. A ``BLENDER_USER_SCRIPTS``/``DATAFILES``/``EXTENSIONS`` variable shares that one part, which is skipped.

What is copied (``plan``): everything in Blender's per-version folder, as Blender's own import copies an earlier
version's (preferences with keymaps, Quick Favorites, asset libraries and add-on choices; add-ons, extensions and
their own folders; presets, app templates, startup scripts and modules; studio lights, MatCaps, HDRIs, fonts and
colour management; bookmarks and recent files), except ``LEFT_OUT``: the agent state an early Angelo build kept in
Blender's ``config`` (``migrate_user_folder.py`` moves it on request) and Blender's ``startup.blend``, whose
workspaces would replace Angelo's own start-up layout.

Copy only (``copy``): Blender's folder is read, never written, moved or deleted from. A file Angelo already has is
first copied into the backup folder; the manifest (written before anything is copied) lists every file the
import adds or replaces, so ``undo`` puts Angelo's folder back exactly and touches nothing outside it. A link is
recreated as a link; one that points into the copied folder points at Angelo's copy instead, so nothing Angelo
writes through it reaches Blender's folder.
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import blender_folders

ANGELO, BLENDER = "Angelo", "Blender"   # Product names, shown as they are.
FOUND, NONE, SHARED = "found", "none", "shared"
LEFT_OUT = frozenset({("config", "angelo-agent"), ("config", "toonlab-agent"), ("config", "startup.blend")})
# Lists Angelo keeps its own entries in: when Angelo already has one, Blender's entries are added after its own.
MERGED = frozenset({("config", "recent-files.txt"), ("config", "bookmarks.txt")})
MANIFEST = "manifest.json"
MANIFEST_VERSION = 1
ROOT = ""   # The destinations key of the per-version folder itself (entries outside the four parts).


@dataclass(frozen=True)
class Source:
    app: str                 # ANGELO or BLENDER
    version: tuple           # (major, minor)
    root: Path = None        # Blender's per-version folder (None for an earlier Angelo: copy_prev finds it)
    parts: tuple = blender_folders.PARTS   # The parts this import copies.

    @property
    def version_label(self):
        return blender_folders.version_name(self.version)


@dataclass(frozen=True)
class Item:
    source: Path
    target: Path
    link: str = None         # A symbolic link's (possibly re-pointed) target; None for a file.
    merge: bool = False      # A list (recent files, bookmarks) merged into Angelo's own when it has one.


@dataclass
class Result:
    copied: int = 0
    errors: list = field(default_factory=list)   # [(path, message)]
    backup: Path = None


def find_source(platform, home, environ, version, angelo_previous=None):
    """``(Source or None, FOUND | NONE | SHARED)``; ``angelo_previous`` is the earlier Angelo version Blender's own
    import found, or None."""
    if blender_folders.shared_variable(environ):
        return None, SHARED
    if angelo_previous:
        return Source(ANGELO, tuple(angelo_previous[:2])), FOUND
    shared = blender_folders.shared_parts(environ)
    parts = tuple(part for part in blender_folders.PARTS if part not in shared)
    for candidate in blender_folders.versions(version):
        root = blender_folders.version_folder(platform, home, environ, candidate)
        source = Source(BLENDER, candidate, root, parts)
        if root.is_dir() and _has_content(source):
            return source, FOUND
    return None, NONE


def _has_content(source):
    return next(iter(_walk(source)), None) is not None


def _walk(source):
    """``(relative path, absolute path, is_link)`` of every file and link to bring, in a stable order."""
    for entry in sorted(source.root.iterdir()):
        if entry.name in blender_folders.PARTS and entry.name not in source.parts:
            continue   # Shared through a BLENDER_USER_* variable: already the same folder.
        part = entry.name in blender_folders.PARTS
        if entry.is_file() or (entry.is_symlink() and not (part and entry.is_dir())):
            yield (entry.name,), entry, entry.is_symlink()
            continue   # A part folder that is a link (a synced scripts folder) is walked like a folder.
        if not entry.is_dir():
            continue
        for folder, names, files in os.walk(entry):
            names.sort()
            relative = Path(folder).relative_to(source.root).parts
            if len(relative) == 1:
                names[:] = [name for name in names if (relative[0], name) not in LEFT_OUT]
            for name in sorted(files) + [name for name in names if (Path(folder) / name).is_symlink()]:
                path = Path(folder) / name
                if len(relative) == 1 and (relative[0], name) in LEFT_OUT:
                    continue
                if path.is_symlink() or path.is_file():   # Sockets, pipes and devices are not settings.
                    yield relative + (name,), path, path.is_symlink()
            names[:] = [name for name in names if not (Path(folder) / name).is_symlink()]


def plan(source, destinations):
    """The ``Item`` list copying ``source`` into ``destinations`` (``{part: folder, ROOT: per-version folder}``)."""
    items, same = [], _same_folders(source, destinations)
    for relative, path, is_link in _walk(source):
        head = relative[0]
        if head in same:
            continue
        if head in blender_folders.PARTS:
            target = Path(destinations[head]).joinpath(*relative[1:])
        elif destinations.get(ROOT):
            target = Path(destinations[ROOT]).joinpath(*relative)
        else:
            continue
        link = _link_target(path, source.root, target, destinations) if is_link else None
        items.append(Item(path, target, link, merge=not is_link and relative[:2] in MERGED and len(relative) == 2))
    return items


def _same_folders(source, destinations):
    """Parts whose destination already is the source folder (a link between the two apps' folders): skipped."""
    same = set()
    for part in blender_folders.PARTS:
        try:
            if os.path.samefile(source.root / part, destinations[part]):
                same.add(part)
        except (OSError, KeyError, TypeError):
            continue
    return same


def _link_target(path, root, target, destinations):
    """Where the copied link points: the same place, unless that is inside the folder being copied."""
    text = os.readlink(path)
    absolute = Path(os.path.normpath(path.parent / text))
    try:
        inside = absolute.relative_to(root).parts
    except ValueError:
        return str(absolute) if not os.path.isabs(text) else text
    if inside and inside[0] in blender_folders.PARTS:
        mapped = Path(destinations[inside[0]]).joinpath(*inside[1:])
    else:
        mapped = Path(destinations.get(ROOT) or target.parent).joinpath(*inside)
    return str(mapped)


def summary(items, destinations):
    """What the items bring, by kind (counts), and whether some only take effect when Angelo opens next."""
    found = {"preferences": False, "addons": set(), "extensions": set(), "presets": 0, "templates": set(),
             "startup_scripts": 0, "lights": 0, "fonts": 0, "colour": False, "bookmarks": False, "recent": False}
    for item in items:
        part, relative = _part_of(item.target, destinations)
        if part == "config":
            found["preferences"] |= relative == ("userpref.blend",)
            found["bookmarks"] |= relative == ("bookmarks.txt",)
            found["recent"] |= relative == ("recent-files.txt",)
        elif part == "scripts" and len(relative) > 1:
            if relative[0] == "addons" and not relative[1].startswith("__"):
                found["addons"].add(relative[1].split(".")[0])
            elif relative[0] == "presets":
                found["presets"] += 1
            elif relative[0] == "startup" and relative[1] == "bl_app_templates_user" and len(relative) > 2:
                found["templates"].add(relative[2])
            elif relative[0] == "startup" and len(relative) == 2 and relative[1].endswith(".py"):
                found["startup_scripts"] += 1
        elif part == "datafiles" and len(relative) > 1:
            found["lights"] += relative[0] == "studiolights"
            found["fonts"] += relative[0] == "fonts"
            found["colour"] |= relative[0] == "colormanagement"
        elif part == "extensions" and len(relative) > 2 and not relative[0].startswith("."):
            found["extensions"].add(relative[0] + "." + relative[1])
    counts = {key: len(value) if isinstance(value, set) else value for key, value in found.items()}
    counts["restart"] = bool(counts["startup_scripts"] or counts["fonts"] or counts["colour"])
    return counts


def _part_of(target, destinations):
    for part in blender_folders.PARTS:
        try:
            return part, Path(target).relative_to(destinations[part]).parts
        except (KeyError, ValueError):
            continue
    return ROOT, ()


def copy(items, backup, source, destinations):
    """Copy ``items``, first backing up every file Angelo already had into ``backup`` (replaced if present).

    Returns a ``Result``; a file that cannot be copied is listed in ``errors`` and the rest go on.
    """
    backup = Path(backup)
    if backup.exists():
        shutil.rmtree(backup)
    (backup / "files").mkdir(parents=True)
    manifest = {"version": MANIFEST_VERSION, "created": time.time(), "app": source.app,
                "source_version": source.version_label, "destinations": _keys(destinations),
                "added": [], "replaced": {}, "folders": []}
    result = Result(backup=backup)
    for index, item in enumerate(items):
        if os.path.lexists(item.target):
            stored = "files/%d" % index
            try:
                _clone(item.target, backup / stored)
            except OSError as error:
                result.errors.append((str(item.target), str(error)))
                continue
            manifest["replaced"][str(item.target)] = stored
        else:
            manifest["added"].append(str(item.target))
    manifest["folders"] = sorted({str(folder) for item in items for folder in _new_folders(item.target, destinations)})
    _write_json(backup / MANIFEST, manifest)   # Before the first copy: an interrupted import can still be undone.
    skipped = {path for path, _ in result.errors}
    for item in items:
        if str(item.target) in skipped:
            continue
        try:
            _place(item)
            result.copied += 1
        except OSError as error:
            result.errors.append((str(item.target), str(error)))
    return result


def _keys(destinations):
    return {key: str(Path(value)) for key, value in destinations.items() if value}


def _new_folders(target, destinations):
    """The folders up to ``target``'s part folder (included) that do not exist yet; never the per-version folder."""
    top = Path(destinations[ROOT]) if destinations.get(ROOT) else None
    folder = Path(target).parent
    while folder != top and not folder.exists() and folder != folder.parent:
        yield folder
        folder = folder.parent


def _clone(path, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.path.islink(path):
        os.symlink(os.readlink(path), destination)
    else:
        shutil.copy2(path, destination)


def _place(item):
    """Write one item next to its target, then swap it in, so a failure never leaves half a file."""
    item.target.parent.mkdir(parents=True, exist_ok=True)
    if item.target.is_dir() and not item.target.is_symlink():
        raise IsADirectoryError("A folder is in the way of " + item.target.name)
    temporary = item.target.with_name(".angelo-import-" + item.target.name)
    if os.path.lexists(temporary):
        os.unlink(temporary)
    try:
        if item.link is not None:
            os.symlink(item.link, temporary)
        elif item.merge and item.target.is_file():
            temporary.write_text(merge_lists(item.target.read_text(encoding="utf-8", errors="replace"),
                                             item.source.read_text(encoding="utf-8", errors="replace")),
                                 encoding="utf-8")
        else:
            shutil.copy2(item.source, temporary)
        os.replace(temporary, item.target)
    except OSError:
        if os.path.lexists(temporary):
            os.unlink(temporary)
        raise


def merge_lists(own, brought):
    """Blender's ``recent-files.txt`` or ``bookmarks.txt`` after Angelo's own entries, each once, per ``[section]``."""
    sections, order = {}, []
    for text in (own, brought):
        section = None
        for line in text.splitlines():
            if line.startswith("[") and line.rstrip().endswith("]"):
                section = line.strip()
            elif not line.strip():
                continue
            entries = sections.setdefault(section, [])
            if section not in order:
                order.append(section)
            if line.strip() != section and line not in entries:
                entries.append(line)
    lines = []
    for section in order:
        lines += ([section] if section else []) + sections[section]
    return "\n".join(lines) + "\n"


def backup_matches(backup, destinations):
    """The manifest in ``backup`` when it belongs to these destinations (this Angelo version's folders), else None."""
    try:
        manifest = json.loads((Path(backup) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        return None
    return manifest if manifest.get("destinations") == _keys(destinations) else None


def undo(backup, destinations):
    """Put Angelo's folder back as it was before the import recorded in ``backup``; returns a ``Result``.

    Only paths inside the recorded destinations are touched; the backup folder is removed when everything went back.
    """
    backup, result = Path(backup), Result(backup=Path(backup))
    manifest = backup_matches(backup, destinations)
    if manifest is None:
        result.errors.append((str(backup), "No import to undo for this version of Angelo"))
        return result
    roots = [Path(value) for value in manifest["destinations"].values()]

    def inside(path):
        return any(_within(Path(path), root) for root in roots)

    for path in reversed(manifest["added"]):
        if inside(path) and os.path.lexists(path) and (os.path.islink(path) or not os.path.isdir(path)):
            try:
                os.unlink(path)
                result.copied += 1
            except OSError as error:
                result.errors.append((path, str(error)))
    for path, stored in manifest["replaced"].items():
        if not inside(path):
            continue
        try:
            _place(Item(backup / stored, Path(path), os.readlink(backup / stored) if os.path.islink(backup / stored)
                        else None))
            result.copied += 1
        except OSError as error:
            result.errors.append((path, str(error)))
    for folder in sorted(manifest["folders"], key=len, reverse=True):
        if inside(folder):
            try:
                os.rmdir(folder)   # Only when empty: anything saved there since the import stays.
            except OSError:
                pass
    if not result.errors:
        shutil.rmtree(backup, ignore_errors=True)
    return result


def _within(path, root):
    try:
        Path(os.path.normpath(path)).relative_to(os.path.normpath(root))
        return True
    except ValueError:
        return False


def _write_json(path, value):
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1), encoding="utf-8")
    os.replace(temporary, path)
