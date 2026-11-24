# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Code a script loads through a loader it built is the script's, whatever the loader claims
(test_blender_script_confinement's loader checks; BREAKIT break-it-confine-fixes-b31e34840ca MUST-1 and
review-confine-fixes-3cb71ae002d S-1).

An exemption decided by the importing objects (a spec, a loader whose ``path`` is an installed file, the import
system's frames) is steerable: a script builds ``SourceFileLoader(N, T)`` with ``T`` an installed file and steers one
of its methods, so the import system loads the script's own bytes while the loader claims ``T``. In those bytes the
module code is a closure body (``exec`` raises before its audit event) and a class body nested in it defines ``g``,
which reads the private key; the script recovers the code from the traceback, runs the class body, and leaves ``g`` in
a tuple and in a handler list. Only the bytes decide (the confinement's bytes provenance): they are not an installed
file's, so the code is the script's from birth, and ``g`` must be refused PRIVATE when Angelo's code calls it after
the region.

* ``get_filename`` steered to an installed callable (``os.fspath`` or ``str``, so it names ``N``), the nested code
  relabelled to another installed name: the tree has mixed names, which the compiler never makes, so
  ``_imp._fix_co_filename`` refuses it (GUARD) before anything is left.
* ``get_filename`` steered as above, every code object relabelled to one installed name: no mixed names, so nothing
  but the code's birth can confine ``g``.
* ``get_data`` steered to return the script's bytes for whatever the loader reads (``T``'s own cache file): the
  loader names only ``T``, so its bytecode path is ``T``'s installed ``.pyc`` (beside it, or the mirror of its
  folder under ``PYTHONPYCACHEPREFIX``), and the bytes it unmarshals are not that file's: provenance refuses the
  unmarshal (GUARD, at exactly that ``.pyc``, as importlib names it) before any code exists. The case used to expect
  the route to reach ``g`` (checked only in plain, unconfined Python): it never did under confinement, since the
  bytes-against-file check predates it (linux-c and iso-mac on 1a17d20 onward; head-14 triage).
* The same with bytes that fail importlib's validation (a wrong magic number): the loader falls back to ``T``'s
  source, which the steered ``get_data`` answers with the script's own text, and provenance refuses compiling it
  under ``T``'s name (GUARD, at ``T``). Both run for two installed files from different installed folders (the
  standard library's and Angelo's add-on), and the suite runs twice, with the app's bytecode cache prefix and with
  none (``native_suite_manifest``: ``pycache_prefix`` and ``no_pycache_prefix``), so the ``.pyc`` named is the
  prefix's mirror in one run and the one beside the source in the other (break-it-confine-reparse SHOULD-4).

Which part of the bytecode cache is installed code (SHOULD-5): only the prefix's mirrors of installed folders. An
installed module whose bytecode is already in the cache imports while confined, from there. A module from a
folder that is not installed, whose bytecode an unconfined compile left in the prefix, imports too, but its code is
the script's: a function it defines is refused PRIVATE when Angelo's code calls it after the region. And its cached
bytes alone (a sourceless load, a function made from a nested code object, never run as module code, so no name
taints it) are refused at the unmarshal: they are not installed bytes. The whole prefix used to be trusted, so those
bytes passed as installed and the function ran free. Both run with and without a prefix.

Run only inside Blender.
"""

import functools
import importlib
import importlib.util
import marshal
import os
import py_compile
import sys
import tempfile
from pathlib import Path

INSTALLED_T2 = os.__file__   # The installed name the script's code is relabelled to.

MODULE = """def make():
    x = 1
    def body():
        x
        class C:
            def g(*a):
                return open(KEY).read()
    return body
"""

LOAD = """import importlib._bootstrap as b, importlib.machinery as m, os, types
L = m.SourceFileLoader(N, T)
%s
code = None
try:
    b._load(m.ModuleSpec(N, L))
except TypeError as error:
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_locals.get("code", code)
        tb = tb.tb_next
body = [c for c in code.co_consts if isinstance(c, types.CodeType)][0]
ns = {"KEY": KEY, "open": open}
exec(body, ns)
held["names"] = (code.co_filename, body.co_filename, ns["g"].__code__.co_filename)
held["tuple"] = (ns["g"],)
bpy.app.handlers.depsgraph_update_post.append(ns["g"])
held["handler"] = len(bpy.app.handlers.depsgraph_update_post) - 1
"""

STEER_FILENAME = "L.get_filename = STEER"
STEER_DATA = "L.get_data = lambda path, data=DATA: data"
# A .pyc read gets the script's bytes; any other read (the source, on the fallback) gets the script's own text.
STEER_DATA_SOURCE = "L.get_data = lambda path, data=DATA, text=TEXT: data if path.endswith('.pyc') else text"

INSTALLED_FROM_CACHE = "colorsys"   # A pure-Python standard module Angelo does not import itself.

# The cached bytes alone, through the import system's own sourceless loader, and a function made from a code
# object nested in them, never run as module code (so nothing but the bytes' provenance decides whose it is).
SOURCELESS = """import importlib.machinery as m, types
code = m.SourcelessFileLoader("angelo_cached_elsewhere", PYC).get_code("angelo_cached_elsewhere")
held["tuple"] = (types.FunctionType([c for c in code.co_consts if isinstance(c, types.CodeType)][0], {"open": open}),)
held["key"] = KEY
"""

IMPORT_CACHED = """import importlib, importlib.util as u
spec = u.spec_from_file_location("angelo_cached_elsewhere", S) if S else None
module = u.module_from_spec(spec) if spec else importlib.import_module(MODULE)
if spec:
    spec.loader.exec_module(module)
held["cached"] = module.__spec__.cached
held["key"] = KEY
if S:
    held["tuple"] = (module.g,)
"""


def _pyc(folder, every):
    """``folder/evil.py``, its hash-based unchecked ``.pyc`` and the ``.pyc``'s bytes: a closure body as the module
    code, relabelled to INSTALLED_T2 (``every``: the whole tree; else only the nested code, leaving the module code's
    own name). Made outside any region, as a script's earlier file write would make it."""
    source = Path(folder) / "evil.py"
    source.write_text("# placeholder: the .pyc is unchecked\n")
    namespace = {}
    exec(compile(MODULE, str(source), "exec"), namespace)
    body = namespace["make"]().__code__   # A closure body: co_freevars == ('x',).
    raw = marshal.dumps(body, 2)
    old = str(source).encode()
    tag = b"u" + len(old).to_bytes(4, "little") + old
    new = INSTALLED_T2.encode()
    relabelled = b"u" + len(new).to_bytes(4, "little") + new
    if every:
        raw = raw.replace(tag, relabelled)
    else:
        # Nested code is written before its parent's name, so the module code's own name is the last occurrence.
        last = raw.rindex(tag)
        raw = raw[:last].replace(tag, relabelled) + raw[last:]
    flags = (1).to_bytes(4, "little")   # Hash-based, unchecked: no source validation.
    data = importlib.util.MAGIC_NUMBER + flags + b"\0" * 8 + raw
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return str(source), data


def _called(check, name, call, PRIVATE, ScriptConfined):
    try:
        read = call()
        check(name, False, "read the private key: %d characters" % len(read))
    except ScriptConfined as error:
        check(name, error.reason == PRIVATE, error.reason)
    except Exception as error:   # noqa: BLE001 - reported as the check's detail.
        check(name, False, repr(error))


def _invalid(data):
    """The same .pyc under a wrong magic number: importlib's validation refuses it, so get_code compiles the source."""
    return b"\0\0\0\0" + data[4:]


def _route_cases(run, check, plain, plain_data, GUARD):
    """``get_data`` steered on two installed files, with bytes that pass validation and bytes that fail it: refused
    GUARD at exactly the file importlib names (the .pyc, or the source on the fallback), with no code left."""
    from angelo_agent import script_confinement
    for t_label, t in (("stdlib", functools.__file__), ("addon", script_confinement.__file__)):
        pyc = importlib.util.cache_from_source(t)
        for label, steer, names, expected in (
                ("get_data", STEER_DATA, {"DATA": plain_data}, pyc),
                ("get_data_invalid", STEER_DATA_SOURCE, {"DATA": _invalid(plain_data), "TEXT": MODULE.encode()}, t)):
            held = {}
            outcome = run(LOAD % steer, N=plain, T=t, held=held, STEER=None, **names)
            check("loader_route_refused_%s_%s" % (label, t_label), "tuple" not in held and
                  outcome.get("refused") == GUARD and outcome.get("what") == expected,
                  (expected, outcome, held.get("names")))
            held.clear()


def _cache_cases(run, check, folder, PRIVATE, GUARD, ScriptConfined):
    """SHOULD-5 on the import system's own routes: what the prefix holds for an installed module is installed code,
    what it holds for any other module is not."""
    prefix = sys.pycache_prefix
    launched = os.environ.get("PYTHONPYCACHEPREFIX")
    # Which run this is: the variant's own launch environment decides, so a run that silently lost (or kept) the
    # prefix fails here instead of passing the cases for the other layout.
    if launched is not None and not os.path.isabs(launched):
        check("loader_cache_none_as_launched", prefix is None, (launched, prefix))
    elif launched:
        check("loader_cache_prefix_as_launched", prefix == launched, (launched, prefix))
    if prefix:
        # An installed module whose bytecode is already in the cache, compiled there unconfined (as another Angelo
        # process leaves it: this one need not write bytecode itself), and not imported yet here.
        sys.modules.pop(INSTALLED_FROM_CACHE, None)
        installed = importlib.util.find_spec(INSTALLED_FROM_CACHE).origin
        installed_pyc = importlib.util.cache_from_source(installed)
        py_compile.compile(installed, cfile=installed_pyc, doraise=True)
        check("loader_cache_installed_module_bytecode_is_in_the_prefix",
              installed_pyc.startswith(prefix) and os.path.isfile(installed_pyc), installed_pyc)
        held = {}
        outcome = run(IMPORT_CACHED, MODULE=INSTALLED_FROM_CACHE, S="", held=held)
        check("loader_cache_installed_module_imports_from_the_prefix", outcome.get("refused") is None and
              "error" not in outcome and held.get("cached") == installed_pyc, (outcome, held.get("cached")))
        sys.modules.pop(INSTALLED_FROM_CACHE, None)
    # A module from a folder that is not installed, compiled by an unconfined process into the cache (the prefix's
    # mirror of its folder, or beside it without a prefix).
    source = Path(folder) / "cached_elsewhere.py"
    source.write_text("def g(path):\n    return open(path).read()\n")
    pyc = importlib.util.cache_from_source(str(source))
    py_compile.compile(str(source), cfile=pyc, doraise=True)
    # Imported normally, it loads, and its code is the script's.
    held = {}
    outcome = run(IMPORT_CACHED, MODULE="", S=str(source), held=held)
    check("loader_cache_other_module_imports", outcome.get("refused") is None and "error" not in outcome and
          held.get("cached") == pyc, (outcome, held.get("cached")))
    if "tuple" in held:
        _called(check, "loader_cache_other_module_code_is_the_scripts",
                lambda: held["tuple"][0](held["key"]), PRIVATE, ScriptConfined)
    held.clear()
    # Its cached bytes alone are not installed bytes: refused at the unmarshal, at that .pyc, before any code exists.
    held = {}
    outcome = run(SOURCELESS, PYC=pyc, held=held)
    check("loader_cache_other_module_bytes_refused", "tuple" not in held and outcome.get("refused") == GUARD and
          outcome.get("what") == pyc, (pyc, outcome))
    if "tuple" in held:
        _called(check, "loader_cache_other_module_bytes_code_is_the_scripts",
                lambda: held["tuple"][0](held["key"]), PRIVATE, ScriptConfined)
    held.clear()


def run_loader_cases(run, check, PRIVATE, GUARD, ScriptConfined, gc):
    import bpy
    handlers = bpy.app.handlers.depsgraph_update_post
    with tempfile.TemporaryDirectory(prefix="angelo-loader-") as folder:
        for part in ("mixed", "plain"):
            os.makedirs(os.path.join(folder, part))
        mixed, mixed_data = _pyc(os.path.join(folder, "mixed"), False)
        plain, plain_data = _pyc(os.path.join(folder, "plain"), True)
        variants = (
            # (label, file N, the steering line, its names, whether the tree has mixed names, the names expected)
            ("fspath", mixed, STEER_FILENAME, {"STEER": os.fspath, "DATA": mixed_data}, True, None),
            ("str", mixed, STEER_FILENAME, {"STEER": str, "DATA": mixed_data}, True, None),
            ("unmixed_fspath", plain, STEER_FILENAME, {"STEER": os.fspath, "DATA": plain_data}, False, plain),
        )
        for label, n, steer, names, is_mixed, expected in variants:
            held = {}
            outcome = run(LOAD % steer, N=n, T=functools.__file__, held=held, **names)
            gc.collect()
            if is_mixed:
                # Refused at the rename, by the rule that the compiler never mixes names (and only there).
                check("loader_route_confined_%s" % label, "tuple" not in held and outcome.get("refused") == GUARD and
                      "_fix_co_filename" in outcome.get("what", ""), (outcome, held.get("names")))
            else:
                # Nothing refuses the route: the code's birth alone must confine `g` (the old loader-based exemption
                # left it free).
                check("loader_route_reached_%s" % label, "tuple" in held and outcome.get("refused") is None and
                      set(held["names"]) == {expected}, (outcome, held.get("names")))
            if "tuple" in held:
                _called(check, "loader_route_confined_%s_tuple" % label, lambda: held["tuple"][0](), PRIVATE,
                        ScriptConfined)
                _called(check, "loader_route_confined_%s_handler" % label,
                        lambda: handlers[held["handler"]](None, None), PRIVATE, ScriptConfined)
                handlers.pop(held["handler"])
            held.clear()
            gc.collect()
        # Refused at the unmarshal or the fallback compile: the bytes are not the installed file's own (provenance).
        _route_cases(run, check, plain, plain_data, GUARD)
        gc.collect()
        _cache_cases(run, check, folder, PRIVATE, GUARD, ScriptConfined)
        gc.collect()
