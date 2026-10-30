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
from dataclasses import dataclass
from pathlib import Path

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
    if not within or parts[0].casefold() == WORKING_FOLDER or resolved.is_relative_to((root / WORKING_FOLDER).resolve()):
        raise AssetError("outside", ident, refused["outside"])   # Angelo's own working files are never assets.
    if path.suffix.lower() not in kinds:
        raise AssetError("kind", ident, refused["kind"])
    if not path.is_file():
        raise AssetError("missing", ident, refused["missing"])
    return root, path, parts


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
    """Record the asset file's digest beside the working file, through a new private file (never through a link)."""
    path = stamp_path(asset)
    temporary = "%s.%s.tmp" % (path, secrets.token_hex(4))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"sha256": digest(asset.path), "asset": asset.ident}, handle)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def working_is_current(asset):
    """True when the working .blend exists and the asset file is still what Angelo last read or wrote."""
    return os.path.isfile(working_path(asset)) and read_stamp(asset) == digest(asset.path)


def live_gltf(options):
    """Live preview options from save-time glTF ``options``: no animation and at the current frame, so an export never
    changes the frame (which would put animated values the person hasn't keyed back to their keys)."""
    return dict(options, export_animations=False, export_current_frame=True)


def export(window, path, options):
    """Write the window's scene (``window`` None: the context's) as one .glb at ``path`` with ``options``, atomically;
    returns its size in bytes.

    Reads the scene only. Raises RuntimeError when the exporter writes nothing."""
    from .web_export import _addon, run_exporter
    _addon()
    folder = os.path.dirname(path) or "."
    descriptor, scratch = tempfile.mkstemp(prefix=".angelo-link-", suffix=".glb", dir=folder)
    os.close(descriptor)
    try:
        settings = dict(options, filepath=scratch, check_existing=False, export_format="GLB",
                        gltf_export_id=EXPORT_ID, will_save_settings=False, use_active_scene=True,
                        use_selection=False)
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
        shutil.copytree(engine_adapter.PLUGIN_SOURCE, copy, ignore=shutil.ignore_patterns(*engine_adapter.PLUGIN_IGNORE))
        if os.path.isdir(target):
            os.replace(target, os.path.join(staging, "previous"))
        os.replace(copy, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target
