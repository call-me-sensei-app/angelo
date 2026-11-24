# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export Splats in the running, confined app: the person's export writes where they confirm, an assistant's never
elsewhere (2.1.0 exports review, findings S-S1, S-S2 and S-S3; fails first).

Default Director startup, Angelo enabled as it starts (the real confinement). A script region begins first, so C
answers by its records from then on, as in a session where the assistant has worked. On a splat cloud whose name is a
path (``../../steer/Scan``, a name an assistant can set):

* the person runs Export Splats: the save browser opens in the saved document's folder with one clean file name
  (nothing from the name's folders), Return confirms it, and that file is written; nothing appears where the name
  pointed;
* an assistant script (``script_confinement.run_confined``) exporting to a path it names outside Angelo's folders is
  refused with nothing written, and one naming an existing file of the person's leaves it unchanged;
* the operator asks before replacing a file (``check_existing``) and keeps the ``.ply`` extension (``check``).

Run through ``run_native_suites.py --gui --include test_blender_splat_export_director_gui``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-splat-export-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_operator  # noqa: E402  (a stand-in self for an operator's method, never a bare namespace)
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The default Director startup, as people get it.
os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "splat-export-director.json"
DOCUMENT = OUT / "export-document" / "project.blend"
PLAIN = OUT / "plain"   # A folder of the person's that Angelo did not make.
NAME = "../../steer/Scan"
state = {"phase": "boot", "success": False, "checks": {}, "details": {}}
started = time.monotonic()
SH_C0 = 0.28209479177387814


def check(name, ok, detail=None):
    state["checks"][name] = bool(ok)
    if detail is not None:
        state["details"][name] = detail


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def browser():
    """(window, area, params) of an open file browser, or None."""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'FILE_BROWSER' and area.spaces.active.params is not None:
                return win, area, area.spaces.active.params
    return None


def cloud():
    """A small splat cloud imported through Blender's PLY importer, named like a path."""
    path = OUT / "cloud.ply"
    rng = np.random.default_rng(7)
    n = 400
    fields = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
              "rot_0", "rot_1", "rot_2", "rot_3"]
    data = np.zeros((n, len(fields)), dtype=np.float32)
    data[:, 0:3] = rng.normal(size=(n, 3))
    data[:, 3:6] = (rng.random((n, 3)) - 0.5) / SH_C0
    data[:, 6] = 2.0
    data[:, 7:10] = np.log(0.02)
    data[:, 10] = 1.0
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n
    header += "".join("property float %s\n" % f for f in fields) + "end_header\n"
    path.write_bytes(header.encode("ascii") + data.astype("<f4").tobytes())
    before = set(bpy.data.objects)
    bpy.ops.wm.ply_import(filepath=str(path))
    (obj,) = set(bpy.data.objects) - before
    obj.name = NAME
    return obj


def run(source, **names):
    """``source`` as an agent script, through the executor's confinement."""
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
    admit("escape", source)
    outcome = {"refused": None}
    try:
        _, result = script_confinement.run_confined(
            source, namespace, None, "escape", finish=lambda ns: script_errors.read_back(ns.get("result")))
        outcome["result"] = json.dumps(result, default=str)[:300]
    except script_confinement.ScriptConfined as error:
        outcome["refused"] = script_confinement.reason_of(error)
    except Exception as error:   # noqa: BLE001 - reported as the check's detail.
        outcome["error"] = repr(error)[:300]
    gc.collect()
    return outcome


SCRIPT = '''
obj = bpy.data.objects[NAME]
try:
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        result = sorted(bpy.ops.angelo.export_splat_ply(filepath=TARGET))
except RuntimeError as error:
    result = "refused: " + str(error)[:200]
'''


def finish(error=None):
    if error is not None:
        state["error"] = "".join(traceback.format_exception(error))
    else:
        state["phase"] = "complete"
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_SPLAT_EXPORT_DIRECTOR", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 240, "Splat export fixture timed out in " + state["phase"]
        from angelo_agent import director
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not hasattr(bpy.ops.angelo, "export_splat_ply"):
                check("operator_exists", False)
                finish()
                return None
            DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
            PLAIN.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))
            state["cloud"] = cloud().name
            run("result = 1")   # A script region has begun: C answers by its records from here on.
            props = bpy.ops.angelo.export_splat_ply.get_rna_type().properties   # The operator's own settings.
            check("asks_before_replacing", "check_existing" in props and props["check_existing"].default is True)
            area, region = view()
            obj = bpy.data.objects[state["cloud"]]
            with bpy.context.temp_override(window=window(), area=area, region=region, object=obj, active_object=obj,
                                           selected_objects=[obj]):
                bpy.ops.angelo.export_splat_ply('INVOKE_DEFAULT')
            state["phase"], state["since"] = "browser", time.monotonic()
            return .3
        if state["phase"] == "browser":
            found = browser()
            if found is None:
                assert time.monotonic() - state["since"] < 10, "the save browser did not open"
                return .2
            win, area, params = found
            folder, name = Path(bpy.path.abspath(params.directory.decode() if isinstance(params.directory, bytes)
                                                 else params.directory)), params.filename
            state["browser"] = {"directory": str(folder), "filename": name}
            check("browser_opens_in_the_documents_folder", folder.resolve() == DOCUMENT.parent.resolve(),
                  state["browser"])
            check("browser_name_has_no_folder", name.endswith(".ply") and "/" not in name and "\\" not in name
                  and not name.startswith("."), name)
            region = next(r for r in area.regions if r.type == 'WINDOW')
            for value in ('PRESS', 'RELEASE'):   # The person confirms with Return.
                win.event_simulate(type='RET', value=value, x=region.x + 10, y=region.y + 10)
            state["phase"], state["since"], state["picked"] = "written", time.monotonic(), str(folder / name)
            return .3
        if state["phase"] == "written":
            picked = Path(state["picked"])
            if not picked.exists() and time.monotonic() - state["since"] < 10:
                return .2
            check("persons_export_writes_the_confirmed_file", picked.exists() and picked.stat().st_size > 200,
                  str(picked))
            steered = (DOCUMENT.parent / NAME).with_suffix(".ply")
            check("nothing_where_the_name_pointed", not steered.exists() and not (OUT.parent / "steer").exists(),
                  str(steered))
            outside = PLAIN / "assistant.ply"
            outcome = run(SCRIPT, NAME=state["cloud"], TARGET=str(outside))
            check("assistant_path_refused_nothing_written", not outside.exists()
                  and (outcome["refused"] or "refused" in str(outcome.get("result", "")) or outcome.get("error")),
                  outcome)
            existing = PLAIN / "persons.ply"
            existing.write_bytes(b"the person's own file")
            outcome = run(SCRIPT, NAME=state["cloud"], TARGET=str(existing))
            check("assistant_cannot_replace_a_persons_file", existing.read_bytes() == b"the person's own file", outcome)
            from angelo_agent import splat_mesh
            op = qa_operator.stand_in(splat_mesh.ANGELO_OT_export_splat_ply, filepath=str(DOCUMENT.parent / "typed.xyz"))
            changed = op.check(bpy.context)
            check("a_typed_name_keeps_ply", changed and op.filepath.endswith(".ply"), op.filepath)
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
