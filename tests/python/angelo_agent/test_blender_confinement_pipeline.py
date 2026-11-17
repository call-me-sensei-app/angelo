# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pipeline libraries under the agent-script confinement, in the real app (doc/angelo/script_confinement_pipeline.rst).

USD, OpenImageIO, OpenEXR, OpenVDB, MaterialX, OpenColorIO, OpenTimelineIO, OSL queries, Audaspace and Mantaflow
are no longer refused on import: a confined script uses them on its own files, and every private path is refused
where the library would open it.

* ``ORDINARY``: each library's ordinary pipeline work on a project folder succeeds, including Blender's own USD
  import and export and a module reached through ``importlib.import_module``.
* ``REFUSED``: a private (or startup) path named directly is refused with the confinement's exception.
* ``SEALED``: a private file reached indirectly (a USD sublayer, reference, payload, relative path, link or
  ``.mtlx``; a MaterialX XInclude; a LUT found through a search path; a usdz dependency; Blender's USD importer) is
  never read: nothing the script gets back holds its content, and USD never opened it.
* USD's primary resolver is the confined one; modules imported before any script (``PRELOADED``) are gated too.
* The inventory: every file-like entry point the bundled libraries expose is classified
  (``native_inventory.PIPELINE``), and the C refusal list holds only the un-gateable modules.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import importlib
import inspect
import json
import re
import shutil
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

import native_inventory as inventory
import pipeline_cases as cases

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-confinement-pipeline-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
REASON = "An agent script can't"


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:900]


def runner(names):
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    finishing = "finish" in inspect.signature(script_confinement.run_confined).parameters

    def run(source, label=""):
        print("pipeline case:", label or source.strip().splitlines()[0][:100], file=sys.stderr, flush=True)
        namespace = {"__name__": "__angelo_script__", "bpy": bpy, "os": os, **names}
        admit("pipeline", source)
        outcome = {"refused": None}
        try:
            if finishing:
                _, result = script_confinement.run_confined(
                    source, namespace, None, "pipeline", finish=lambda ns: script_errors.read_back(ns.get("result")))
            else:
                script_confinement.run_confined(source, namespace, None, "pipeline")
                result = script_errors.read_back(namespace.get("result"))
            outcome["result"] = json.dumps(result, default=str)[:2000]
        except script_confinement.ScriptConfined as error:
            outcome["refused"] = script_confinement.reason_of(error)
        except Exception as error:   # noqa: BLE001 - the check's detail.
            outcome["error"] = repr(error)[:2000]
        return outcome
    return run


def fixtures(plain, priv, key):
    for name, text in cases.text_fixtures(str(plain), str(priv), str(key)).items():
        folder, _, file = name.partition("/")
        (plain if folder == "plain" else priv).joinpath(file).write_text(text)
    link = plain / "link.usda"
    if not link.exists():
        link.symlink_to(priv / "secret.usda")


def startup_folder():
    folder = Path(bpy.utils.user_resource('SCRIPTS', path="addons", create=True))
    return folder


def ordinary(run):
    for name, source in cases.ORDINARY.items():
        outcome = run(source, "ordinary " + name)
        result = json.loads(outcome["result"]) if "result" in outcome else None
        result = result.get("result") if isinstance(result, dict) else result   # The executor's read-back.
        check("ordinary_" + name, outcome["refused"] is None and "error" not in outcome and result == "ok", outcome)


def refused(run, priv):
    before = set(os.listdir(priv))
    for name, source in cases.REFUSED.items():
        outcome = run(source, "refused " + name)
        good = outcome["refused"] is not None or REASON in outcome.get("error", "")
        check("refused_" + name, good, outcome)
        check("no_private_content_" + name, cases.MARK not in json.dumps(outcome) and
              cases.KEY_TEXT not in json.dumps(outcome), outcome)
    made = set(os.listdir(priv)) - before
    check("nothing_written_privately", not made, sorted(made))


def sealed(run, plain, priv):
    for name, source in cases.SEALED.items():
        outcome = run(source, "sealed " + name)
        text = json.dumps(outcome)
        check("sealed_" + name, cases.MARK not in text and cases.KEY_TEXT not in text, outcome)
        REPORT.setdefault("sealed", {})[name] = outcome
    package = plain / "tex_stage.usdz"
    check("sealed_package_holds_no_key", not package.exists() or cases.KEY_TEXT.encode() not in package.read_bytes())
    check("sealed_no_secret_object", not any(cases.MARK in name for name in bpy.data.objects.keys()))
    from pxr import Sdf
    opened = [layer.identifier for layer in Sdf.Layer.GetLoadedLayers() if cases.MARK in layer.ExportToString()]
    check("usd_never_opened_secret", not opened and Sdf.Layer.Find(str(priv / "secret.usda")) is None, opened)


def resolver(run, key):
    """USD's resolver is the confined one: it resolves the key file for Angelo, never for a confined script (Python
    shows the resolver as the base type, so this is judged by what it does)."""
    from pxr import Ar
    check("usd_resolves_unconfined", bool(str(Ar.GetResolver().Resolve(str(key)))))
    outcome = run("from pxr import Ar\nresult = str(Ar.GetResolver().Resolve(KEY))", "usd resolver")
    check("usd_resolver_confined", outcome["refused"] is not None or REASON in outcome.get("error", "") or
          json.loads(outcome.get("result", "null")) in ("", {"result": ""}), outcome)


def dll_names(plain):
    """The app's own shared library folder (where sitecustomize points ``PXR_USD_WINDOWS_DLL_PATH``), a file in it,
    and a folder a script can write."""
    if sys.platform == "win32":
        shared = os.environ.get("PXR_USD_WINDOWS_DLL_PATH", "")
    else:
        shared = os.path.join(os.path.dirname(os.path.normpath(bpy.utils.resource_path('LOCAL'))), "lib")
    return {"SHARED": shared, "PLAIN": str(plain), "PLANTED": os.path.join(shared, "angelo-confinement-planted.dll")}


def dll_directories(run, names):
    """On Windows the pipeline packages add DLL folders as they import (pxr's ``Tf.WindowsImportWrapper`` adds
    ``PXR_USD_WINDOWS_DLL_PATH``, the app's own shared library folder): a confined import may add that folder again,
    never another (a folder the script can write would let it plant a DLL), and the folder takes no write. Judged by
    the audit event itself, so the rule is proven on every platform; OSL's package adds nothing (it would add every
    PATH entry)."""
    if sys.platform == "win32":
        check("osl_adds_no_dll_folders", os.environ.get("OSL_LOAD_DLLS_FROM_PATH") == "0",
              os.environ.get("OSL_LOAD_DLLS_FROM_PATH"))
    ours = run("import sys\nsys.audit('os.add_dll_directory', SHARED)\nresult = 'ok'", "dll folder: the app's own")
    check("dll_directory_the_apps_own", ours["refused"] is None and "error" not in ours, ours)
    other = run("import sys\nsys.audit('os.add_dll_directory', PLAIN)\nresult = 'added'", "dll folder: another")
    check("dll_directory_another_refused", other["refused"] is not None or REASON in other.get("error", ""), other)
    write = run("with open(PLANTED, 'wb') as file:\n    file.write(b'MZ')\nresult = 'planted'", "dll folder: write")
    check("dll_directory_takes_no_write", write["refused"] is not None or REASON in write.get("error", ""), write)
    if os.path.exists(names["PLANTED"]):
        os.remove(names["PLANTED"])


# ---------------------------------------------------------------------------------------------------------------
# The inventory: every file-like entry point of the bundled pipeline libraries is classified.

FILE_WORDS = re.compile(r"(open|read|write|save|load|export|import|file|folder|plugin|searchpath|report|archive|"
                        r"bake|touch|record|package|serialize|font|lut|ocio|colorconfig|directory|processor)", re.I)
OWNER_WORDS = re.compile(r"(file|image|input|output|buf|config|query|sound|reader|writer|loader|cache|texture)", re.I)
SKIP_MODULES = re.compile(r"(__main__|\.tests?\b|\._?test|usdviewq|usdGenSchema|usdInitSchema|\.console|adapters\.cmx|"
                          r"PyMaterialXGenGlsl|PyMaterialXRenderGlsl|PyMaterialXGenMsl|PyMaterialXRenderMsl)")


def library_modules(root):
    found = []
    try:
        top = importlib.import_module(root)
    except Exception:   # noqa: BLE001 - not bundled on this platform.
        return found
    found.append(top)
    import pkgutil

    def walk(package):
        # Skipped by name before importing: pkgutil.walk_packages imports every package to look inside it.
        for info in pkgutil.iter_modules(getattr(package, "__path__", []), package.__name__ + "."):
            if SKIP_MODULES.search(info.name):
                continue
            try:
                module = importlib.import_module(info.name)
            except (Exception, SystemExit):   # noqa: BLE001 - a part that needs what the app lacks (GL, jinja2),
                continue                      # or a command-line tool that parses arguments on import.
            found.append(module)
            if info.ispkg:
                walk(module)
    walk(top)
    return found


def entry_points():
    """Qualified name -> True for every module function and own class attribute whose name says files."""
    found, seen = {}, {}
    for root in inventory.PIPELINE_ROOTS:
        for module in library_modules(root):
            for name, value in list(vars(module).items()):
                if name.startswith("_") and not name.startswith("_Write") and not name.startswith("_Test") \
                        and not name.startswith("_serialize"):
                    continue
                owner = getattr(value, "__module__", "") if isinstance(value, type) else ""
                if isinstance(value, type) and (owner.split(".")[0] == root.split(".")[0] or owner == "builtins"):
                    for attr, raw in list(vars(value).items()):
                        words = FILE_WORDS.search(attr) if attr not in ("__init__", "__new__") else \
                            OWNER_WORDS.search(value.__name__)
                        if words and callable(getattr(value, attr, None)):
                            qualified = "%s.%s.%s" % (owner if owner != "builtins" else module.__name__,
                                                      value.__qualname__, attr)
                            seen.setdefault(id(raw), []).append(qualified)
                elif callable(value) and not isinstance(value, type) and FILE_WORDS.search(name):
                    seen.setdefault(id(value), []).append("%s.%s" % (module.__name__, name))
    for names in seen.values():
        found[min(names)] = True
    return found


def classified(run):
    import _bpy
    refused_c = set(_bpy._angelo_guard.native_file_modules())
    check("refused_list_is_ungateable_only", refused_c == inventory.REFUSED, sorted(refused_c ^ inventory.REFUSED))
    found = entry_points()
    known = set(inventory.PIPELINE_GATED) | set(inventory.PIPELINE_OTHER)
    # A function a package re-exports from its private extension module may be listed under either name.
    public = lambda name: re.sub(r"\._[A-Za-z]\w*(?=\.)", "", name)
    missing = sorted(name for name in found if name not in known and public(name) not in known)
    check("every_pipeline_entry_point_classified", not missing, missing)
    REPORT["unclassified"] = missing
    stale = sorted(known - set(found))
    REPORT["classified_not_found"] = stale
    for name, probe in inventory.PIPELINE_GATED.items():
        outcome = run(probe, "gated " + name)
        check("gated_" + name, outcome["refused"] is not None or REASON in outcome.get("error", ""), outcome)


def main():
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
    fixtures(plain, priv, key)
    for module in cases.PRELOADED:
        importlib.import_module(module)
    run = runner({"PLAIN": str(plain), "PRIV": str(priv), "KEY": str(key), "STARTUP": str(startup_folder())})
    dll_directories(runner(dll_names(plain)), dll_names(plain))
    resolver(run, key)
    ordinary(run)
    for made, secret in cases.COPIED.items():
        if (plain / made).exists():
            shutil.copyfile(plain / made, priv / secret)
    refused(run, priv)
    sealed(run, plain, priv)
    classified(run)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "confinement-pipeline.json").write_text(json.dumps(REPORT, indent=2, default=str))
failed = sorted(name for name, ok in REPORT["checks"].items() if not ok)
print(json.dumps({"success": REPORT["success"], "failed": failed, "error": REPORT.get("error"),
                  "checks": len(REPORT["checks"])}, default=str)[:6000])
