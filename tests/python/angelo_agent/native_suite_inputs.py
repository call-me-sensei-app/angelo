# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Machine inputs a native suite needs from outside the app under test: a Godot editor, pinned held-out models.

A suite names its inputs in the manifest (``Suite.inputs``). Before planning, ``run_native_suites`` resolves each one
once per run: from its variable when the caller set it, else from what the machine or the checkout provides (Godot's
default install; the models ``godot_physics_e2e/heldout_assets.json`` pins, fetched and sha256-checked into a cache
beside the run). A resolved input reaches the suite as that variable. An input this machine cannot provide makes the
suite a skip whose reason says what is missing and how to provide it: never a failure that reads as a product defect
(1.0.10's runs reported the physics suite FAILED on every Mac with Godot installed, for want of the models), and
never a pass. Suites started by hand resolve their inputs the same way. Standard library only.
"""

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GODOT_ENV = "ANGELO_QA_GODOT"
PHYSICS_ASSETS_ENV = "ANGELO_QA_PHYSICS_ASSETS"
# Where Godot's own installer puts the editor; the Godot suites are macOS-only (the editor-settings path they write).
DEFAULT_GODOT = {"darwin": "/Applications/Godot.app/Contents/MacOS/Godot"}
HELDOUT_MANIFEST = HERE / "godot_physics_e2e" / "heldout_assets.json"
FETCHER = HERE / "godot_physics_e2e" / "fetch_heldout_assets.py"
CACHE_FOLDER = "angelo-native-inputs"   # Beside the runs in the runner's output root, like its bytecode folder.


class Unavailable(Exception):
    """This machine cannot provide the input; the message says what is missing and how to provide it."""


def default_godot(platform=None):
    """The editor a Godot suite uses without ``ANGELO_QA_GODOT`` (it may not be installed)."""
    return DEFAULT_GODOT.get(platform or sys.platform, "")


def godot_editors(env, platform=None, isfile=os.path.isfile):
    """The Godot editors to run: ``ANGELO_QA_GODOT`` (several joined by the path separator), else the default."""
    value = env.get(GODOT_ENV, "")
    if value:
        editors = [item for item in value.split(os.pathsep) if item]
        missing = [item for item in editors if not isfile(item)]
        if missing:
            raise Unavailable("%s names no Godot editor at %s" % (GODOT_ENV, ", ".join(missing)))
        return editors
    default = default_godot(platform)
    if default and isfile(default):
        return [default]
    raise Unavailable("needs a Godot 4.4+ editor: set %s (several joined by %r)%s"
                      % (GODOT_ENV, os.pathsep, " or install one at " + default if default else ""))


def load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_heldout_assets", FETCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def physics_assets(env, cache_root, fetch=None):
    """A folder of held-out .glb models: ``ANGELO_QA_PHYSICS_ASSETS``, else the pinned set fetched into the cache."""
    value = env.get(PHYSICS_ASSETS_ENV, "")
    if value:
        if not list(Path(value).glob("*.glb")):
            raise Unavailable("%s=%s holds no .glb models" % (PHYSICS_ASSETS_ENV, value))
        return value
    pinned = HELDOUT_MANIFEST.read_bytes()
    # Named by the manifest's digest: a changed pin never reuses another pin's files.
    folder = Path(cache_root) / CACHE_FOLDER / ("heldout-physics-" + hashlib.sha256(pinned).hexdigest()[:12])
    fetch = fetch or load_fetcher().fetch
    try:
        fetch(json.loads(pinned), folder)   # Skips files already there with the pinned sha256.
    except (OSError, ValueError, SystemExit) as error:   # Offline, an HTTP error, or the fetcher's refusal.
        raise Unavailable("the held-out models of %s could not be fetched into %s (%s); set %s to a folder of them "
                          "(godot_physics_e2e/fetch_heldout_assets.py DEST)"
                          % (HELDOUT_MANIFEST.name, folder, error, PHYSICS_ASSETS_ENV)) from None
    return str(folder)


# name: (the variable the suite reads, resolver(env, cache_root) -> value). Godot's value is the editors joined.
RESOLVERS = {
    "godot": (GODOT_ENV, lambda env, cache_root: os.pathsep.join(godot_editors(env))),
    "physics_heldout": (PHYSICS_ASSETS_ENV, physics_assets),
}


def resolve(suites, env, cache_root, resolvers=None):
    """``(values, unavailable)``: each suite's resolved variables by name, and why each other suite cannot run.

    Each input is resolved at most once; a suite that needs an unavailable input is not in ``values``.
    """
    resolvers = RESOLVERS if resolvers is None else resolvers
    resolved, values, unavailable = {}, {}, {}
    for suite in suites:
        found = {}
        for name in suite.inputs:
            if name not in resolved:
                variable, resolver = resolvers[name]
                try:
                    resolved[name] = (True, variable, resolver(env, cache_root))
                except Unavailable as error:
                    resolved[name] = (False, variable, "input %s unavailable: %s" % (name, error))
            ok, variable, value = resolved[name]
            if not ok:
                unavailable[suite.name] = value
                break
            found[variable] = value
        else:
            values[suite.name] = found
    return values, unavailable
