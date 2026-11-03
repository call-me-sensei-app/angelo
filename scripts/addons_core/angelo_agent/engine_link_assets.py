# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a linked asset is to Angelo for any game engine, and the one adapter per engine that knows its rules.

* **An adapter** (``adapter(engine)``; Godot's is ``godot_link_assets``) is a module that says, for one engine, what
  a project is, which assets it admits and what they are called on the wire (``resolve``), what the person's import
  settings are (``import_params``), how the live preview and the save are exported (``live_options``, ``durable``),
  what its editors can show by default (``CAPABILITIES``) and where its plugin is installed (``PLUGIN_*``).
  doc/angelo/engine_link.rst ("Adapters") is the contract.
* **Shared by every engine**: the ``Asset`` an adapter returns, the path check that keeps an asset inside its project,
  the working .blend that Angelo edits for an asset it can't open itself (``<project>/.angelo/<relative path>.blend``)
  with its stamp, the live .glb export, and copying a plugin into a project in one step.

No Blender data changes here; ``export`` only reads the scene.
"""

import hashlib
import importlib
import json
import os
import secrets
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import engine_link_reads as reads, user_copies
from .engine_link_facts import listing as _listing

WORKING_FOLDER = ".angelo"     # Inside the project; no engine imports it (hidden, and Godot also reads its .gdignore).
EXPORT_ID = "angelo_link"
ADAPTERS = {"godot": "godot_link_assets",   # engine -> adapter module; an engine not listed is refused ("engine").
            "unity": "unity_link_assets",
            "unreal": "unreal_link_assets"}

# The live preview and save of an engine that records no exporter table of its own: the glTF exporter's defaults,
# with modifiers applied (what the person sees). Live: no animation, at the current frame (``live_options``).
GLTF_DURABLE = {"export_apply": True}


class AssetError(ValueError):
    """A request for an asset Angelo can't edit; ``code`` is the refusal's code, ``english()`` the engine's text."""

    def __init__(self, code, asset, template):
        super().__init__(code)
        self.code, self.asset, self.template = code, asset, template

    def english(self):
        return self.template.format(asset=self.asset)


@dataclass(frozen=True)
class Asset:
    engine: str         # "godot": the adapter that resolved it.
    project: str        # The project folder, absolute.
    ident: str          # What the engine calls the asset ("res://props/chair.glb"); replies echo it.
    path: str           # The file Angelo opens or writes back, absolute.
    kind: str           # ".blend", ".glb", ...
    relative: str       # ``path`` relative to ``project``, "/"-separated (names the working file).

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def editable(self):
        """The .blend Angelo opens: the asset itself, or its working file."""
        return self.path if self.kind == ".blend" else working_path(self)


def adapter(engine):
    """The adapter module for ``engine``, or None when this Angelo can't link with it."""
    name = ADAPTERS.get(engine) if isinstance(engine, str) else None
    return importlib.import_module("." + name, __package__) if name else None


def opens_by_name(engine_adapter):
    """Whether an asset of this engine resolves from its name and project alone (``resolve`` needs nothing only the
    engine's plugin knows), so Angelo can ask to open it without the engine asking first."""
    return getattr(engine_adapter, "OPENS_BY_NAME", True)


NAME_SCAN = 20000   # Folder entries one by-name lookup reads at most in a project; past it the engine's name is needed.
NAME_LISTED = 400   # Characters of matching names a lookup names back; the rest are counted.
_HIDDEN = 0x2       # FILE_ATTRIBUTE_HIDDEN; Windows' listing carries it, so reading it costs no call there.


def _skipped(entry, engine_adapter):
    """Whether the lookup leaves this folder entry out: a link or junction, anything hidden, or a folder the engine
    ignores (``NAME_IGNORED``: Godot's .gdignore, Unity's trailing ~); None when it can't be read."""
    try:
        if entry.name.startswith(".") or entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            return True
        if os.name == "nt" and getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _HIDDEN:
            return True
        if not entry.is_dir(follow_symlinks=False):
            return False
        suffix, mark = getattr(engine_adapter, "NAME_IGNORED", ("", ""))
        return bool(suffix and entry.name.endswith(suffix) or mark and os.path.exists(os.path.join(entry.path, mark)))
    except OSError:
        return None


def _spelled(text):
    return unicodedata.normalize("NFC", str(text)).strip().casefold()


def _showable(name):
    """Whether a reply can carry this name: no control character or line break (any other character, a space of
    any width or an emoji joiner included, is part of a person's names)."""
    return not any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in name)


def named(engine_adapter, project, name):
    """``(idents, complete)``: what the engine calls each asset of its kinds in ``project`` whose file is ``name``,
    with or without its extension, in any case or Unicode form: the way a person names one ("the barrel"). Reads the
    adapter's ``NAME_ROOT`` folder of the project, never what ``_skipped`` leaves out. ``complete`` is False when the
    lookup couldn't read the project whole: past ``NAME_SCAN`` entries or its time (``engine_link_reads``), an entry it
    can't read, or a match whose name a reply can't carry (``_showable``), so a single match it found may not be the
    only one. The caller resolves an ident as any other; off the main thread (``engine_link_reads.Lookup``)."""
    wanted = _spelled(name)
    root = os.path.join(project, *engine_adapter.NAME_ROOT.split("/")) if engine_adapter.NAME_ROOT else project
    found, folders, read, complete = [], [(root, "")], 0, True
    while folders:
        folder, prefix = folders.pop()
        try:
            for entry in _listing(folder):
                read += 1
                if reads.expired():
                    raise reads.Late()   # Unfinished for time, never "too many entries" (break-it 9-L1).
                if read > NAME_SCAN:
                    return sorted(found), False
                complete = _named_entry(engine_adapter, entry, prefix, wanted, found, folders) and complete
        except OSError:
            complete = False
    return sorted(found), complete


def _named_entry(engine_adapter, entry, prefix, wanted, found, folders):
    """One entry of ``named``'s walk: a folder to read later, or a match added to ``found``. False when the entry
    leaves the lookup unsure (unreadable, or a match whose name a reply can't carry)."""
    skipped = _skipped(entry, engine_adapter)
    if skipped is not False:
        return skipped is not None
    try:
        folder_entry = entry.is_dir(follow_symlinks=False)
    except OSError:
        return False
    if folder_entry:
        folders.append((entry.path, prefix + entry.name + "/"))
        return True
    stem, kind = os.path.splitext(entry.name)
    if kind.lower() in engine_adapter.KINDS and wanted in {_spelled(stem), _spelled(entry.name)}:
        if not _showable(prefix + entry.name):
            return False
        found.append(engine_adapter.NAME_PREFIX + prefix + entry.name)
    return True


def display_name(engine):
    found = adapter(engine)
    return found.DISPLAY if found is not None else str(engine)[:40]


def capabilities(info):
    """What a connected editor can show (``engine_link_server.client_info``): what it said, else its engine adapter's
    default."""
    said = info.get("capabilities")
    if isinstance(said, list):
        return frozenset(said)
    found = adapter(info.get("engine", "godot"))
    return frozenset(found.CAPABILITIES) if found is not None else frozenset()


def serves(asset, info, live=False):
    """Whether a connected editor is one of ``asset``'s engine and project (and, with ``live``, shows live scenes)."""
    return (info.get("engine", "godot") == asset.engine
            and os.path.normcase(os.path.realpath(info.get("project", ""))) == os.path.normcase(asset.project)
            and (not live or "live" in capabilities(info)))


def inside(project, relative, refused, ident, kinds):
    """(root, path, parts) for the "/"-separated ``relative`` path in the folder ``project``, checked: never through
    "..", a link out of the project or Angelo's working folder (any case), of one of ``kinds``, and existing. Raises
    AssetError with the engine's ``refused`` texts (keys "outside", "kind", "missing")."""
    root = Path(project).resolve()
    parts = relative.replace("\\", "/").split("/")
    if not relative or any(part in {"", ".", ".."} for part in parts):
        raise AssetError("outside", ident, refused["outside"])
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve()
        within = resolved.is_relative_to(root)
    except OSError:
        resolved, within = path, False
    if not within or parts[0].casefold() == WORKING_FOLDER or _in_working_folder(root, resolved):
        raise AssetError("outside", ident, refused["outside"])   # Angelo's own working files are never assets.
    if path.suffix.lower() not in kinds:
        raise AssetError("kind", ident, refused["kind"])
    if not path.is_file():
        raise AssetError("missing", ident, refused["missing"])
    return root, path, parts


def _in_working_folder(root, resolved):
    """Whether ``resolved`` (inside ``root``) is in the project's working folder by identity, not spelling: a link
    named otherwise, or spelled in another case on a case-insensitive volume, still counts (break-it 2-6)."""
    relative = resolved.relative_to(root).parts
    if relative and relative[0].casefold() == WORKING_FOLDER:
        return True
    working = root / WORKING_FOLDER
    if not working.exists():
        return False
    for parent in [resolved, *resolved.parents]:
        if not parent.is_relative_to(root) or parent == root:
            break
        try:
            if os.path.samefile(parent, working):
                return True
        except OSError:
            continue
    try:   # A hard link elsewhere in the project is the working file itself (break-it 3-5).
        info = resolved.stat()
    except OSError:
        return False
    if info.st_nlink < 2:
        return False
    if working.is_symlink() or working.is_junction():   # A linked working folder is never walked (break-it 6-F5).
        return True
    return _holds_inode(working, info)


WORKING_SCAN_LIMIT = 50000   # Entries of a working folder checked for a hard link; past it, it counts as held.


def _holds_inode(folder, info):
    """Whether ``folder`` holds the file ``info`` describes (break-it 4-4: by the inode numbers its listing already
    returns, a stat only for an entry whose number matches, since a listing's device is 0 on Windows (break-it 6-2);
    a folder too large, too slow (``engine_link_reads``) or unreadable to check counts as holding it: refused, never
    assumed). Reads each folder entry by entry (``_listing``), never whole."""
    pending, seen = [str(folder)], 0
    while pending:
        try:
            for entry in _listing(pending.pop()):
                seen += 1
                if reads.expired():
                    raise reads.Late()   # Unchecked for time: refused as unreadable, never as "outside".
                if seen > WORKING_SCAN_LIMIT:
                    return True
                try:   # A listing whose numbers aren't the files' own (overlayfs, FUSE: break-it 7-4) can't clear it.
                    if seen == 1 and entry.inode() != os.stat(entry.path, follow_symlinks=False).st_ino:
                        return True
                    if entry.inode() == info.st_ino and os.path.samestat(os.stat(entry.path, follow_symlinks=False),
                                                                         info):
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                except OSError:   # Gone or unreadable while checked: the same rule.
                    return True
        except FileNotFoundError:   # A folder gone since it was listed holds nothing.
            continue
        except OSError:   # A folder it can't read might hold it (break-it 5-H1): refused, never assumed.
            return True
    return False


def working_path(asset):
    """Where the working .blend of an asset Angelo can't open itself lives: ``<project>/.angelo/<relative>.blend``."""
    parts = asset.relative.split("/")
    return str(Path(asset.project, WORKING_FOLDER, *parts[:-1], parts[-1] + ".blend"))


def ensure_working_folder(asset):
    """Create the project's .angelo folder with a .gdignore and a README. Raises OSError when the folder, or the working
    file, is a link or leads out of the project (a cloned project can't make Angelo write elsewhere)."""
    folder = Path(asset.project, WORKING_FOLDER)
    working = Path(working_path(asset))
    current = folder
    for part in (None,) + working.parent.relative_to(folder).parts:   # Checked before anything is made in it.
        current = current if part is None else current / part
        if current.is_symlink():
            raise OSError("Angelo's working folder in the project is a link: %s" % current.name)
        current.mkdir(exist_ok=True)
    for name in (working, Path(stamp_path(asset)), Path(previous_path(asset)), folder / ".gdignore",
                 folder / "README.txt"):
        if name.is_symlink():
            raise OSError("Angelo's working file in the project is a link: %s" % name.name)
    marker = folder / ".gdignore"
    if not marker.exists():
        marker.write_text("", encoding="utf-8")
    readme = folder / "README.txt"
    if not readme.exists():
        readme.write_text("Angelo's working files for the assets it edits through Angelo Link.\n"
                          "Each .blend is the source of the asset at the same path; keep them with the project.\n",
                          encoding="utf-8")


def same_file(left, right):
    """Whether two paths name one file (by identity when both exist, else by normalized path)."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def stamp_path(asset):
    return working_path(asset) + ".json"


def previous_path(asset):
    """Where a working .blend the asset no longer matches is kept when Angelo imports the asset again."""
    return working_path(asset)[:-len(".blend")] + ".previous.blend"


def read_stamp(asset):
    """The digest of the asset file that the working file was last imported from or written to, or None."""
    try:
        with open(stamp_path(asset), encoding="utf-8") as handle:
            value = json.load(handle).get("sha256")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def write_stamp(asset):
    """Record the asset file's digest beside the working file, through a new private file (never through a link);
    returns the digest."""
    path, sha256 = stamp_path(asset), digest(asset.path)
    temporary = "%s.%s.tmp" % (path, secrets.token_hex(4))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"sha256": sha256, "asset": asset.ident}, handle)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise
    return sha256


def working_is_current(asset):
    """True when the working .blend exists and the asset file is still what Angelo last read or wrote."""
    return os.path.isfile(working_path(asset)) and read_stamp(asset) == digest(asset.path)


def live_gltf(options):
    """Live preview options from save-time glTF ``options``: no animation and at the current frame, so an export never
    changes the frame (which would put animated values the person hasn't keyed back to their keys)."""
    return dict(options, export_animations=False, export_current_frame=True)


def baked_plan(window):
    """``export_baked.plan`` for the window's scene (``window`` None: the context's)."""
    import bpy
    from . import export_baked
    with bpy.context.temp_override(**({} if window is None else {"window": window})):
        return export_baked.plan(bpy.context.scene.objects, bpy.context.evaluated_depsgraph_get())


def baked_for_save(say):
    """What a glTF write-back of the saved scene assigns: ``baked_plan``'s ``used``. ``say(code, message)`` tells the
    engine's editor, in English as the link's other status messages, which objects keep their own materials."""
    found = baked_plan(None)
    nearby = [name for name in found["stale"] if name in found.get("nearby", ())]
    changed = [name for name in found["stale"] if name not in nearby]
    if changed:
        say("baked_stale", "%s changed after its maps were baked, so it keeps its own materials; bake its maps "
                           "again to send them" % ", ".join(changed[:3]))
    if nearby:
        say("baked_stale_nearby", "Something near %s changed after its maps were baked, and its baked ambient "
                                  "occlusion saw it, so it keeps its own materials; bake its maps again to send them"
            % ", ".join(nearby[:3]))
    if found["packing"]:
        say("baked_packing", "%s keeps its own materials: its maps were baked with a packing glTF can't carry; "
                             "bake them with the glTF packing to send them"
            % ", ".join(name for name, _packing in found["packing"][:3]))
    return found["used"]


def export(window, path, options):
    """Write the window's scene (``window`` None: the context's) as one .glb at ``path`` with ``options``, atomically;
    returns its size in bytes.

    Objects with a current baked material are written with it (``export_baked``); the save's write-back says which
    were stale. Reads the scene only. Raises RuntimeError when the exporter writes nothing."""
    from . import export_baked
    from .web_export import _addon, run_exporter
    _addon()
    baked = baked_plan(window)
    folder = os.path.dirname(path) or "."
    descriptor, scratch = tempfile.mkstemp(prefix=".angelo-link-", suffix=".glb", dir=folder)
    os.close(descriptor)
    try:
        settings = dict(options, filepath=scratch, check_existing=False, export_format="GLB",
                        gltf_export_id=EXPORT_ID, will_save_settings=False, use_active_scene=True,
                        use_selection=False)
        with export_baked.active(baked):
            result = run_exporter(window, settings)   # Never moves the person out of Edit Mode.
        if "FINISHED" not in result or not os.path.getsize(scratch):
            raise RuntimeError("Blender's glTF exporter didn't write the file")
        os.replace(scratch, path)
    except BaseException:
        if os.path.exists(scratch):
            os.remove(scratch)
        raise
    return os.path.getsize(path)


def install_plugin(engine_adapter, project):
    """Copy the adapter's bundled plugin into ``project`` (``PLUGIN_FOLDER``), replacing an older copy in one step.
    Raises AssetError ("not_project") or OSError; returns the folder."""
    if not engine_adapter.is_project(project):
        raise AssetError("not_project", "", engine_adapter.REFUSED["not_project"])
    target = os.path.join(project, *engine_adapter.PLUGIN_FOLDER)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    name = os.path.basename(target)
    staging = os.path.join(os.path.dirname(target), ".%s-%s" % (name, secrets.token_hex(6)))   # Hidden: skipped.
    os.mkdir(staging, 0o700)
    try:
        copy = os.path.join(staging, name)
        # The person's permissions, never the bundle's: a read-only install's copy could not be moved into place.
        user_copies.copy_tree(engine_adapter.PLUGIN_SOURCE, copy,
                              ignore=shutil.ignore_patterns(*engine_adapter.PLUGIN_IGNORE))
        if os.path.isdir(target):
            user_copies.own(target)   # An older read-only copy moves aside too.
            os.replace(target, os.path.join(staging, "previous"))
        os.replace(copy, target)
    finally:
        user_copies.remove_tree(staging)
    return target
