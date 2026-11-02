# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render engine add-ons load only in Angelo's window, never in the background Angelo processes it starts.

Contract: doc/angelo/render_engines.rst, "Background processes". Angelo's own children (a parallel-edit worker, the
external MCP's headless session; both run ``--background`` with ``ANGELO_HEADLESS=1``) may load the person's
add-ons. A render engine add-on has no use there (a background Angelo never renders with one) and can end the whole
process: V-Ray for Blender's Community Edition exits as it registers without a window, and a paid V-Ray that cannot
get its licence exits about two minutes later. So in those children every engine add-on is **lead-only**: Blender's
``addon_utils.enable`` is wrapped before any add-on loads (``scripts/startup/angelo_lead_only.py``, a startup module,
which Blender registers before it enables add-ons) and returns None for one, without importing it. No code of it runs,
so no route there (an Angelo job, an approved script, a direct operator call) can start its engine.

An engine add-on is told without importing it: a legacy add-on whose ``bl_info`` (read as text) has the category
``Render``, or an extension whose ``blender_manifest.toml`` tags include ``Render``, in either case outside the
scripts and extensions that ship with Angelo. A Render add-on that is a tool rather than an engine is held back too:
the child renders nothing with it. A person's own ``Angelo --background`` render is not Angelo's child and is
unchanged.
"""

import ast
import os
import sys
from pathlib import Path

CATEGORY = "Render"
MAX_BYTES = 1 << 20
EXTENSION_PREFIX = "bl_ext."
_held = []   # The add-ons held back in this process, for its log and its tests.


def applies(environ=None, background=None):
    """Whether this process is a background Angelo that Angelo itself started."""
    import bpy
    environ = os.environ if environ is None else environ
    return (bpy.app.background if background is None else background) and environ.get("ANGELO_HEADLESS") == "1"


def _bl_info(path):
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


def _manifest_tags(path):
    try:
        import tomllib
        with open(path, "rb") as handle:
            return tuple(tomllib.load(handle).get("tags") or ())
    except (OSError, ValueError, ImportError):
        return ()


def _inside(path, roots):
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def is_engine_addon(module, paths, repos, shipped):
    """Whether add-on ``module`` is a render engine add-on outside ``shipped`` roots.

    ``paths``: the add-on folders (``addon_utils.paths()``); ``repos``: ``{repo module: (directory, shipped)}`` of
    the extension repositories."""
    if module.startswith(EXTENSION_PREFIX):
        _prefix, repo, name = (module.split(".", 2) + ["", ""])[:3]
        directory, ships = repos.get(repo, (None, True))
        if directory is None or ships:
            return False
        return CATEGORY in _manifest_tags(Path(directory) / name / "blender_manifest.toml")
    for folder in paths:
        for candidate in (Path(folder) / module / "__init__.py", Path(folder) / (module + ".py")):
            if candidate.is_file():
                if _inside(candidate, shipped):
                    return False
                info = _bl_info(candidate)
                return bool(info) and info.get("category") == CATEGORY
    return False


def _context():
    import addon_utils
    import bpy
    shipped = [Path(p).resolve() for p in (bpy.utils.system_resource('SCRIPTS'),
                                           bpy.utils.system_resource('EXTENSIONS')) if p]
    repos = {}
    for repo in getattr(bpy.context.preferences.extensions, "repos", ()):
        try:
            repos[repo.module] = (repo.directory, getattr(repo, "source", "") == 'SYSTEM')
        except AttributeError:
            continue
    return list(addon_utils.paths()), repos, shipped


def install():
    """Wrap ``addon_utils.enable`` in this process so engine add-ons are held back; idempotent."""
    import addon_utils
    original = addon_utils.enable
    if getattr(original, "_angelo_lead_only", False):
        return

    def enable(module_name, *args, **kwargs):
        try:
            held = is_engine_addon(module_name, *_context())
        except Exception as error:   # Never stop an add-on for a check that could not run.
            print("Angelo: lead-only add-ons:", module_name, error, file=sys.stderr)
            held = False
        if held:
            _held.append(module_name)
            print("Angelo: %s is a render engine add-on; it loads only in Angelo's window" % module_name)
            return None
        return original(module_name, *args, **kwargs)

    enable._angelo_lead_only = True
    enable.original = original
    addon_utils.enable = enable


def held():
    return list(_held)
