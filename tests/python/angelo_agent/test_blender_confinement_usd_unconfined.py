# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""USD when its resolver is not the confined one, in the real app (doc/angelo/script_confinement_pipeline.rst).

The confinement judges every asset USD reads or writes in Angelo's own resolver (io/usd/usd_confined_resolver.hh).
When USD runs any other resolver (the plugin missing from an install, ``PXR_AR_DISABLE_PLUGIN_RESOLVER`` in the
environment), no USD is a confined script's: nothing USD does is judged, so every way in is refused, not only the
entry points that name a file (2.0.0 Windows T0: a script read the provider key through ``Ar.OpenAsset`` and a
private sublayer through Blender's USD importer).

Run as ``@no_plugin_resolver`` (``PXR_AR_DISABLE_PLUGIN_RESOLVER=1``, which leaves USD its default resolver):

* ``closed_*``: a confined script reaches USD through modules Angelo imported first (``Ar``, an in-memory stage
  composing a private sublayer, a stage opened by path), Blender's USD import and export operators, a USD cache file
  and Hydra: each is refused, nothing private comes back, nothing is written.
* ``angelo_*``: Angelo's own (unconfined) USD keeps working, before and after.
* ``preferred_resolver``: a child Angelo whose environment declares another primary resolver plugin (one that sorts
  first) still uses the confined resolver.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

import pipeline_cases as cases

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-confinement-usd-unconfined-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
REASON = "An agent script can't"

CLOSED = {
    "ar_open_asset": """
from pxr import Ar
asset = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(KEY))
result = None if asset is None else bytes(asset.GetBuffer()).decode("latin-1")
""",
    "ar_resolve": """
from pxr import Ar
result = str(Ar.GetResolver().Resolve(KEY))
""",
    "in_memory_sublayer": """
from pxr import Usd
stage = Usd.Stage.CreateInMemory()
stage.GetRootLayer().subLayerPaths.append(os.path.join(PRIV, "secret.usda"))
result = [str(prim.GetPath()) for prim in stage.Traverse()]
""",
    "stage_open": """
from pxr import Usd
stage = Usd.Stage.Open(os.path.join(PLAIN, "outer_sub.usda"))
result = [str(prim.GetPath()) for prim in stage.Traverse()]
""",
    "import_operator": """
bpy.ops.wm.usd_import(filepath=os.path.join(PLAIN, "outer_sub.usda"))
result = sorted(bpy.data.objects.keys())
""",
    "export_operator": """
bpy.ops.wm.usd_export(filepath=os.path.join(PLAIN, "made_by_script.usda"))
result = "exported"
""",
    "cache_file": """
cache = bpy.data.cache_files.new("outer_sub") if hasattr(bpy.data.cache_files, "new") else None
if cache is None:
    bpy.ops.cachefile.open(filepath=os.path.join(PLAIN, "outer_sub.usda"))
    cache = bpy.data.cache_files[-1]
cache.filepath = os.path.join(PLAIN, "outer_sub.usda")
holder = bpy.data.objects.new("cache_holder", bpy.data.meshes.new("cache_holder"))
bpy.context.scene.collection.objects.link(holder)
holder.modifiers.new("cache", "MESH_SEQUENCE_CACHE").cache_file = cache
bpy.context.view_layer.update()   # A cache file opens when evaluated, by an object that uses it: inside the script.
result = [path.path for cache in bpy.data.cache_files for path in cache.object_paths]
bpy.data.objects.remove(holder)
""",
    "hydra": """
import _bpy_hydra
_bpy_hydra.engine_create(None, None, None)
result = "created"
""",
}
WRITTEN = {"export_operator": "made_by_script.usda"}
UNOPENED = {"cache_file"}   # Evaluation has no report: refused, the cache file opens nothing.


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:900]


def runner(names):
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit

    def run(source, label):
        print("usd case:", label, file=sys.stderr, flush=True)
        namespace = {"__name__": "__angelo_script__", "bpy": bpy, "os": os, **names}
        admit("usd-unconfined", source)
        outcome = {"refused": None}
        try:
            _, result = script_confinement.run_confined(
                source, namespace, None, "usd-unconfined", finish=lambda ns: script_errors.read_back(ns.get("result")))
            outcome["result"] = json.dumps(result, default=str)[:2000]
        except script_confinement.ScriptConfined as error:
            outcome["refused"] = script_confinement.reason_of(error)
        except Exception as error:   # noqa: BLE001 - the check's detail.
            outcome["error"] = repr(error)[:2000]
        return outcome
    return run


def angelo_reads(plain, key, when):
    """Angelo's own USD, unconfined: the resolver resolves the key, a stage composes its sublayers."""
    from pxr import Ar, Usd
    check("angelo_resolves_" + when, bool(str(Ar.GetResolver().Resolve(str(key)))))
    stage = Usd.Stage.Open(str(plain / "outer_sub.usda"))
    check("angelo_composes_" + when, stage is not None and stage.GetPrimAtPath("/Outer").IsValid())


def closed(run, plain):
    objects = set(bpy.data.objects.keys())
    for name, source in CLOSED.items():
        outcome = run(source, name)
        text = json.dumps(outcome)
        unopened = name in UNOPENED and json.loads(outcome.get("result", "null")) in ([], {"result": []})
        check("closed_" + name, outcome["refused"] is not None or REASON in outcome.get("error", "") or unopened,
              outcome)
        check("closed_nothing_private_" + name, cases.MARK not in text and cases.KEY_TEXT not in text, outcome)
        if name in WRITTEN:
            check("closed_nothing_written_" + name, not (plain / WRITTEN[name]).exists())
    check("closed_no_object_made", set(bpy.data.objects.keys()) == objects, sorted(bpy.data.objects.keys()))
    check("closed_no_secret_cache_file", not any(cases.MARK in path.path for cache in bpy.data.cache_files
                                                 for path in cache.object_paths))


STRAY = {"Plugins": [{"Info": {"Types": {"AaaStrayResolver": {"bases": ["ArResolver"]}}},
                      "Name": "aaaStrayResolver", "ResourcePath": ".", "Root": ".", "Type": "resource"}]}


def preferred_resolver():
    """Another primary resolver declared in the environment sorts before Angelo's: USD must still pick Angelo's
    (``ArSetPreferredResolver``), so a stray or third-party plugin never turns the confinement's resolver off."""
    folder = OUT / "stray_resolver"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "plugInfo.json").write_text(json.dumps(STRAY))
    env = dict(os.environ, PXR_PLUGINPATH_NAME=str(folder), TF_DEBUG="AR_RESOLVER_INIT")
    env.pop("PXR_AR_DISABLE_PLUGIN_RESOLVER", None)
    probe = "import addon_utils; addon_utils.enable('angelo_agent', default_set=False, persistent=False)"
    child = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                            "--python-expr", probe], env=env, capture_output=True, text=True, timeout=240)
    output = child.stdout + child.stderr
    lines = [line for line in output.splitlines() if "ArGetResolver" in line or "asset resolver" in line]
    check("preferred_resolver_stray_declared", "AaaStrayResolver" in output, lines)
    check("preferred_resolver", "Using asset resolver BlenderConfinedResolver" in output, lines)


def main():
    check("resolver_plugins_disabled", os.environ.get("PXR_AR_DISABLE_PLUGIN_RESOLVER") == "1",
          "run as @no_plugin_resolver")
    from angelo_agent import ui
    state = Path(ui.state_directory())
    key = state / "keys" / "provider.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(cases.KEY_TEXT)
    priv = state / "pipeline"
    plain = OUT / "plain"
    for folder in (priv, plain):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
    for name, text in cases.text_fixtures(str(plain), str(priv), str(key)).items():
        folder, _, file = name.partition("/")
        (plain if folder == "plain" else priv).joinpath(file).write_text(text)
    import pxr.Usd, pxr.Ar, pxr.Sdf  # noqa: E401,F401  Angelo's own use first, as the person's earlier work would.
    angelo_reads(plain, key, "before")
    closed(runner({"PLAIN": str(plain), "PRIV": str(priv), "KEY": str(key)}), plain)
    angelo_reads(plain, key, "after")
    preferred_resolver()


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "confinement-usd-unconfined.json").write_text(json.dumps(REPORT, indent=2, default=str))
failed = sorted(name for name, ok in REPORT["checks"].items() if not ok)
print(json.dumps({"success": REPORT["success"], "failed": failed, "error": REPORT.get("error"),
                  "details": {name: REPORT.get("details", {}).get(name) for name in failed},
                  "checks": len(REPORT["checks"])}, default=str)[:8000])
