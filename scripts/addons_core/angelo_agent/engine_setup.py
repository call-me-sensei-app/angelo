# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render engines installed as add-ons but not on in Angelo yet, and setting one up from the engine switcher.

A third-party engine arrives by its vendor's installer, which knows Blender, not Angelo: it puts its add-on (often a
small loader that points at the vendor's own install folder) in Blender's add-ons folder for this version, whether or
not Blender itself is installed. A person may also have put an add-on in Angelo's own add-ons folder without turning
it on. ``found`` lists both, by the add-on's own ``bl_info`` (category ``Render``), read as text and never imported:
nothing third-party runs until the person chooses it (doc/angelo/render_engines.rst, "Setting up an installed
engine").

Choosing one (``set_up``) is the person's consent to run it: an add-on from Blender's folder is linked into Angelo's
add-ons folder (a symbolic link, a directory junction on Windows, else a copy of a small add-on), so the vendor's
updates and uninstaller keep applying; then it is enabled and kept on in the preferences, and the render engines it
registered are returned for the switcher to choose. An add-on that registers no engine is turned off again and its
link removed. Angelo's shipped add-ons are never listed. Standard library only outside ``found`` and ``set_up``.
"""

import ast
import os
import sys
from pathlib import Path

from . import blender_folders, user_copies
from .ui_text import n_

CATEGORY = "Render"
MAX_BYTES = 1 << 20          # A bl_info is at the top of an add-on's __init__; nothing bigger is read.
MAX_COPY_BYTES = 64 << 20    # Where a link is impossible, only a small add-on (a vendor's loader) is copied.
SETUP_WHY = n_("Installed, not on in Angelo yet: choose it to set it up")
NOT_ON = n_("{name} could not be turned on: {error}")
NO_ENGINE = n_("{name} adds no render engine, so it was turned off again")
NO_LINK = n_("{name} could not be added to Angelo's add-ons folder: {error}")


class SetupError(Exception):
    """A set-up that did not finish: ``template`` (an ``n_`` constant) and its fields, translated where shown."""

    def __init__(self, template, **fields):
        super().__init__(template.format(**fields))
        self.template, self.fields = template, fields


def bl_info(path):
    """An add-on file's ``bl_info`` dictionary, read without importing it, or None."""
    try:
        if Path(path).stat().st_size > MAX_BYTES:
            return None
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "bl_info" for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def _module_file(entry):
    """``(module name, file holding bl_info)`` of one add-ons-folder entry, or None."""
    if entry.is_dir() and (entry / "__init__.py").is_file():
        return entry.name, entry / "__init__.py"
    if entry.is_file() and entry.suffix == ".py":
        return entry.stem, entry
    return None


_scanned = {}   # folder -> (its modification time, its rows): the switcher asks on every open.


def scan(folder, skip=()):
    """``[{module, name, source}]``: the Render add-ons in ``folder`` whose module is not in ``skip``, by name."""
    folder = Path(folder)
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return []
    cached = _scanned.get(str(folder))
    if cached is None or cached[0] != stamp:
        rows = []
        for entry in sorted(folder.iterdir(), key=lambda path: path.name.lower()):
            module = _module_file(entry) if not entry.name.startswith((".", "_")) else None
            info = bl_info(module[1]) if module else None
            if info and info.get("category") == CATEGORY:
                rows.append({"module": module[0], "name": str(info.get("name") or module[0])[:80],
                             "source": str(entry)})
        cached = _scanned[str(folder)] = (stamp, rows)
    return [dict(row) for row in cached[1] if row["module"] not in skip]


def blender_addons(platform=None, home=None, environ=None, version=None):
    """Blender's add-ons folder for this version, where vendors' installers put their add-ons."""
    import bpy
    platform, environ = platform or sys.platform, os.environ if environ is None else environ
    home = home or (environ.get("CFFIXED_USER_HOME") if platform == "darwin" else None) or os.path.expanduser("~")
    root = blender_folders.version_folder(platform, home, environ, version or bpy.app.version[:2])
    return root / "scripts" / "addons"


def found():
    """The installed Render add-ons that are not on: Angelo's own add-ons folders first, then Blender's."""
    import addon_utils
    import bpy
    from . import render_extensions
    roots, rows = render_extensions._shipped_roots(), []
    # On means loaded: a preference left on for an add-on whose files are gone (a removed copy) hides nothing.
    enabled = {name for name in bpy.context.preferences.addons.keys() if addon_utils.check(name)[1]}
    known = set(enabled)
    for module in addon_utils.modules(refresh=False):
        known.add(module.__name__)
        info = addon_utils.module_bl_info(module)
        if (info.get("category") == CATEGORY and module.__name__ not in enabled
                and not render_extensions._shipped(getattr(module, "__file__", None), roots)):
            rows.append({"module": module.__name__, "name": str(info.get("name") or module.__name__)[:80],
                         "source": ""})
    rows += scan(blender_addons(), skip=known)
    return [row for row in rows if row["module"] not in _no_engine or _no_engine[row["module"]] != _stamp(row)]


_no_engine = {}   # module -> the files' stamp when it registered no engine: not offered again until they change.


def _stamp(row):
    """When an add-on's files last changed: its own folder (or file) and its __init__, Angelo's or Blender's."""
    import addon_utils
    path = Path(row["source"]) if row["source"] else None
    if path is None:
        module = next((m for m in addon_utils.modules(refresh=False) if m.__name__ == row["module"]), None)
        path = Path(module.__file__) if module is not None and getattr(module, "__file__", None) else None
    try:
        return tuple(entry.stat().st_mtime_ns for entry in ((path, path / "__init__.py") if path.is_dir() else (path,)))
    except (AttributeError, OSError):
        return None


def _size(path):
    return sum(entry.stat().st_size for entry in Path(path).rglob("*") if entry.is_file()) if path.is_dir() \
        else path.stat().st_size


def link(source, target):
    """Put ``source`` at ``target``: a symbolic link, a directory junction on Windows, else a copy of a small one."""
    source, target = Path(source), Path(target)
    try:
        os.symlink(source, target, target_is_directory=source.is_dir())
        return "link"
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32" and source.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(source), str(target))
            return "junction"
        except OSError:
            pass
    if source.is_file():
        try:
            os.link(source, target)   # Windows without Developer Mode: the vendor's updates still apply.
            return "hard"   # i18n: a link kind, never shown
        except OSError:
            pass
    if _size(source) > MAX_COPY_BYTES:
        raise OSError("too large to copy")
    try:
        # The person's permissions, never the vendor's install's: a read-only copy could not be updated or removed.
        (user_copies.copy_tree if source.is_dir() else user_copies.copy_file)(source, target)
    except OSError:
        _remove(target, "copy")   # Only what this copy created: the target did not exist before.
        raise
    return "copy"


def _remove(target, how):
    if how == "copy" and target.is_dir() and not target.is_symlink():
        user_copies.remove_tree(target)
    elif how == "copy" and target.is_file():
        target.unlink()
    elif how is not None:
        try:
            os.rmdir(target) if how == "junction" else target.unlink()
        except OSError:
            pass


def set_up(module, name, source=""):
    """Link ``module`` in from ``source`` (another app's add-ons folder; "" for Angelo's own), enable it and keep it
    on; returns the engine identifiers it registered. Raises SetupError, leaving nothing behind."""
    import addon_utils
    import bpy
    from . import render_extensions
    before, target, how = set(render_extensions.addon_engines()), None, None
    if source:
        target = Path(bpy.utils.user_resource('SCRIPTS', path="addons", create=True)) / Path(source).name
        if not target.exists():
            try:
                how = link(source, target)
            except OSError as error:
                raise SetupError(NO_LINK, name=name, error=str(error)) from None
        # A first add-on: its folder did not exist at start-up, so Python cannot import from it until Blender
        # puts its script folders on the path again.
        bpy.utils.refresh_script_paths()
        addon_utils.modules_refresh()
    errors = []
    loaded = addon_utils.enable(module, default_set=True, persistent=True, handle_error=errors.append)
    if loaded is None:
        _remove(target, how)
        raise SetupError(NOT_ON, name=name, error=str(errors[0]) if errors else "?")
    added = sorted(set(render_extensions.addon_engines()) - before)
    if not added:
        addon_utils.disable(module, default_set=True)
        _remove(target, how)
        _no_engine[module] = _stamp({"module": module, "source": source})   # A Render tool, not an engine.
        raise SetupError(NO_ENGINE, name=name)
    prefs = bpy.context.preferences
    if getattr(prefs, "use_preferences_save", False):
        bpy.ops.wm.save_userpref()   # Kept on after a crash too, as Preferences' own Save does.
    return added
