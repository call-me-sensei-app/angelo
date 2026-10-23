# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a cold first launch compiled, against what the app ships compiled (first_launch_frames' ``cold`` phase).

The install step precompiles every module the app ships as a checked hash-based ``.pyc`` next to its source, and
``sitecustomize``'s shipped-bytecode loader reads it (doc/angelo/releasing.rst, "Nothing is written inside the
app"). So a first launch with an empty bytecode folder compiles a shipped module only when that loader never saw
it: one imported before ``sitecustomize`` installed it (``encodings``, ``importlib``, ``sitecustomize`` itself) or
loaded straight from its file (``sitecustomize``'s ``_angelo_tls_distrust``): 10 such on a real 1.0.5 build
(d8e801755eb); everything else it compiles is a source
without a matching shipped file (the test's own modules, a person's add-ons). A bound that comes from what ships
and how it loaded, never a fixed count. Dependency-free, so the host tests check the rule
(test_native_cold_bytecode.py) and the phase applies it inside the app.
"""

import importlib.util
import os
import sys


def shipped_bytecode(source):
    """Whether ``source`` has the checked hash-based ``.pyc`` the install step writes, matching its bytes."""
    tag = sys.implementation.cache_tag
    folder, name = os.path.split(source)
    if not name.endswith(".py") or tag is None:
        return False
    try:
        with open(os.path.join(folder, "__pycache__", "%s.%s.pyc" % (name[:-3], tag)), "rb") as stream:
            header = stream.read(16)
        with open(source, "rb") as stream:
            data = stream.read()
    except OSError:
        return False
    return (header[:4] == importlib.util.MAGIC_NUMBER and header[4:8] == b"\x03\x00\x00\x00"
            and header[8:16] == importlib.util.source_hash(data))


def shipped_loader(module):
    """Whether ``module`` was loaded by sitecustomize's shipped-bytecode loader: Python's own ``SourceFileLoader``
    with an instance ``get_code`` of its own (since e6069efa501), or a subclass overriding it (before)."""
    from importlib.machinery import SourceFileLoader
    loader = getattr(getattr(module, "__spec__", None), "loader", None)
    return "get_code" in getattr(loader, "__dict__", {}) or (
        isinstance(loader, SourceFileLoader) and type(loader).get_code is not SourceFileLoader.get_code)


def audit(prefix, modules=None, app_root=None):
    """What a launch wrote into the bytecode folder ``prefix``, and which loaded modules came from shipped bytecode.

    ``written``: every ``.pyc`` there; ``recompiled_shipped``: the sources among them that ship compiled, split by
    the module they belong to into ``recompiled_by_shipped_loader`` (a miss: that loader should have read the
    shipped file), ``recompiled_before_loader`` (loaded by Python's own loader, which never reads it) and
    ``recompiled_unknown`` (no loaded module has that source: counted as a miss); ``loaded_shipped``: modules in
    ``modules`` (default ``sys.modules``) whose source is inside ``app_root`` and ships compiled, loaded by the
    shipped loader.
    """
    modules = modules if modules is not None else sys.modules
    written, recompiled = [], []
    saved = sys.pycache_prefix
    sys.pycache_prefix = prefix   # source_from_cache maps a file in the folder back only while it is the prefix.
    try:
        for folder, _dirs, files in os.walk(prefix) if prefix and os.path.isdir(prefix) else ():
            for name in files:
                if not name.endswith(".pyc"):
                    continue
                path = os.path.join(folder, name)
                written.append(path)
                try:
                    source = importlib.util.source_from_cache(path)
                except ValueError:
                    continue
                if shipped_bytecode(source):
                    recompiled.append(source)
    finally:
        sys.pycache_prefix = saved
    by_source = {}   # One source can be loaded under two names (sitecustomize's ``_angelo_tls_distrust``).
    for name, module in list(modules.items()):
        if isinstance(getattr(module, "__file__", None), str):
            by_source.setdefault(os.path.realpath(module.__file__), []).append((name, module))
    split = {"recompiled_by_shipped_loader": [], "recompiled_before_loader": [], "recompiled_unknown": []}
    for source in sorted(recompiled):
        loaded = by_source.get(os.path.realpath(source), [])
        early = [name for name, module in loaded if not shipped_loader(module)]
        kind = ("recompiled_unknown" if not loaded else "recompiled_before_loader" if early
                else "recompiled_by_shipped_loader")
        split[kind].append(sorted(early or [name for name, _ in loaded])[0] if loaded else source)
    root = os.path.join(os.path.realpath(app_root), "") if app_root else None
    loaded = sorted(
        name for name, module in list(modules.items())
        if isinstance(getattr(module, "__file__", None), str) and module.__file__.endswith(".py")
        and (root is None or os.path.realpath(module.__file__).startswith(root)) and shipped_loader(module)
        and shipped_bytecode(module.__file__))
    return dict(split, written=len(written), recompiled_shipped=sorted(recompiled), loaded_shipped=loaded)


def probe_loader(modules=None, app_root=None):
    """The modules the shipped-bytecode loader served (``audit``'s ``loaded_shipped``) whose loader, asked again for
    their code with a fresh empty bytecode folder, wrote a file there: each compiled its source instead of reading
    the shipped file. Empty when the loader works.

    The direct proof of the contract. A launch also compiles sources loaded straight from their file outside the
    import system, which the loader never sees (sitecustomize's ``_angelo_tls_distrust``, ``angelo_render_device``'s
    private ``product_env``, Blender's ``bpy.utils.execfile`` of keyconfig presets in a windowed launch, release-105
    fix round 2026-10-23): what was compiled cannot say which loader compiled it, but asking the loader can."""
    import shutil
    import tempfile
    modules = modules if modules is not None else sys.modules
    served = audit(None, modules=modules, app_root=app_root)["loaded_shipped"]
    folder = tempfile.mkdtemp(prefix="angelo-cold-probe-")
    saved = sys.pycache_prefix, sys.dont_write_bytecode
    sys.pycache_prefix, sys.dont_write_bytecode = folder, False
    failed = set()
    try:
        for name in served:
            spec = getattr(modules.get(name), "__spec__", None)
            try:
                spec.loader.get_code(spec.name)
            except Exception:  # noqa: BLE001 - a loader that cannot give the code is a failure too.
                failed.add(name)
        written = {os.path.realpath(importlib.util.source_from_cache(os.path.join(root, file)))
                   for root, _dirs, files in os.walk(folder) for file in files if file.endswith(".pyc")}
    finally:
        sys.pycache_prefix, sys.dont_write_bytecode = saved
        shutil.rmtree(folder, ignore_errors=True)
    for name in served:
        if os.path.realpath(modules[name].__file__) in written:
            failed.add(name)
    return sorted(failed)
