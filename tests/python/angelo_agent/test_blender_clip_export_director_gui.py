# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export Action Clips in the running, confined app: the person's export writes where they confirm, an assistant's
never elsewhere (2.1.0 exports review, findings R-S1, R-S2 and R-S5; the clip export break-it review of 980206f3f0a,
M2; fails first on it).

Default Director startup, Angelo enabled as it starts (the real confinement). An animated armature whose name is a
path (``../../steer/Hero``, a name an assistant can set) in a saved document; beside where its clips will go, a clip
file of the person's (``<model>@Walk.fbx``) that Angelo did not make. Twice, before any script region (C allows every
path then) and after one (C answers by its records), the person runs Export Action Clips as FBX:

* the save browser opens in the document's folder with one clean file name (nothing from the name's folders), and
  Return with Replace unticked writes nothing: the person's clip file stays;
* opened again with Replace ticked, while another clip file of the person's appears after the browser drew its list
  (the clip export re-read of 30ade86d137, S-D), Return replaces nothing: Replace covers only the files it showed;
* opened again, the browser's own panel lists the person's clip file; with Replace ticked there (the suite sets the
  browser's operator property, as the person's click on the box does), Return replaces it and writes the model and
  every clip, in that one run; nothing appears where the name pointed.

Then the person opens the export once more and cancels it (Esc), and assistant scripts
(``script_confinement.run_confined``) try: a GLB onto the model file the person picked earlier (no pick outlives its
run, so it stays as it was), a path outside Angelo's folders (refused, nothing written), and an FBX export over the
person's clip file (it stays).

Run through ``run_native_suites.py --gui --include test_blender_clip_export_director_gui``; check
``clip-export-director.json`` for ``success`` and ``phase == "complete"``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-clip-export-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import product_env  # noqa: E402
from qa_humanoid import humanoid, walk  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The default Director startup, as people get it.
os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "clip-export-director.json"
DOCUMENT = OUT / "export-document" / "project.blend"
PLAIN = OUT / "plain"   # A folder of the person's that Angelo did not make.
NAME = "../../steer/Hero"
PERSONS = b"the person's own clip"
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head"] + [
    side + part for side in ("Left", "Right") for part in ("UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg",
                                                         "Foot", "Toes")]
NAMES = {role: "b." + role for role in ROLES}
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "pass": 0}
started = time.monotonic()
PASSES = ("before_any_script", "after_a_script")


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
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'FILE_BROWSER' and area.spaces.active.params is not None:
                return win, area, area.spaces.active.params
    return None


def press_return(win, region):
    for value in ('PRESS', 'RELEASE'):
        win.event_simulate(type='RET', value=value, x=region.x + region.width // 2, y=region.y + region.height // 2)


def armature():
    """The walk as the active action, and a wave on a track: two clips, two clip files."""
    rig = humanoid("Hero", NAMES, 1.2, 40, clavicles=False, root=True)
    walking = walk(rig, NAMES)
    ad = rig.animation_data
    wave = bpy.data.actions.new("Wave")
    ad.action = wave
    head = rig.pose.bones[NAMES["Head"]]
    head.rotation_mode = 'XYZ'
    for f in (1, 10):
        head.rotation_euler = (0.2 * f / 10, 0, 0)
        head.keyframe_insert("rotation_euler", frame=f)
    ad.action = walking
    track = ad.nla_tracks.new()
    track.name = "WaveTrack"
    track.strips.new("Wave", 1, wave)
    rig.name = NAME
    return rig


def clip_paths_for(model):
    """Where the export writes each clip: the active action and each unmuted single-strip track (read here, so the
    suite does not lean on the module it checks)."""
    from angelo_agent import clip_paths
    ad = bpy.data.objects[state["rig"]].animation_data
    names = ([ad.action.name] if ad.action else []) + [s.action.name for t in ad.nla_tracks if not t.mute
                                                      for s in t.strips if not s.mute and s.action]
    return clip_paths.clip_files(str(model), names)


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
try:
    result = sorted(bpy.ops.angelo.export_action_clips(armature=NAME, filepath=TARGET, format=FORMAT))
except RuntimeError as error:
    result = "refused: " + str(error)[:200]
'''


def open_export(attempt):
    """Export Action Clips (FBX) as the person runs it, for ``attempt``: 'unticked', 'ticked' or 'cancel'."""
    area, region = view()
    rig = bpy.data.objects[state["rig"]]
    with bpy.context.temp_override(window=window(), area=area, region=region, object=rig, active_object=rig):
        bpy.ops.angelo.export_action_clips('INVOKE_DEFAULT', armature=rig.name, format='FBX')
    state["phase"], state["since"], state["attempt"] = "browser", time.monotonic(), attempt


def begin_pass():
    """A clean folder, the person's clip file where the walk's clip goes, then the export left unticked."""
    state["tag"] = PASSES[state["pass"]]
    for stale in DOCUMENT.parent.glob("*.fbx"):
        stale.unlink()
    state["persons_written"] = False
    open_export("unticked")


def finish(error=None):
    if error is not None:
        state["error"] = "".join(traceback.format_exception(error))
    else:
        state["phase"] = "complete"
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CLIP_EXPORT_DIRECTOR", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def closed(seconds=10):
    return browser() is None or time.monotonic() - state["since"] > seconds


def tick():
    try:
        assert time.monotonic() - started < 400, "Clip export fixture timed out in " + state["phase"]
        from angelo_agent import director
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
            PLAIN.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))
            state["rig"] = armature().name
            begin_pass()
            return .3
        tag, attempt = state.get("tag"), state.get("attempt")
        if state["phase"] == "browser":
            found = browser()
            if found is None:
                assert time.monotonic() - state["since"] < 10, "the save browser did not open"
                return .2
            win, area, params = found
            region = next(r for r in area.regions if r.type == 'WINDOW')
            if attempt == "cancel":
                for value in ('PRESS', 'RELEASE'):   # The person changes their mind: Esc.
                    win.event_simulate(type='ESC', value=value, x=region.x + 10, y=region.y + 10)
                state["phase"], state["since"] = "cancelled", time.monotonic()
                return .5
            directory = params.directory.decode() if isinstance(params.directory, bytes) else params.directory
            folder, name = Path(bpy.path.abspath(directory)), params.filename
            if attempt == "unticked":
                check(tag + "_browser_opens_in_the_documents_folder", folder.resolve() == DOCUMENT.parent.resolve(),
                      {"directory": str(folder), "filename": name})
                check(tag + "_browser_name_has_no_folder", name.endswith(".fbx") and "/" not in name
                      and "\\" not in name and not name.startswith("."), name)
                model = folder / name
                clips = clip_paths_for(model)
                persons = Path(next(path for action, path in clips.items() if action.startswith("Walk")
                                    or action == sorted(clips)[0]))
                persons.write_bytes(PERSONS)
                state.update(model=str(model), persons=str(persons), clips=sorted(clips.values()))
            elif attempt == "late":   # Ticked, then a file appears after the browser drew its list.
                if state.get("late_ticked") is None:
                    area.spaces.active.active_operator.replace_existing = True
                    area.tag_redraw()
                    state["late_ticked"] = time.monotonic()
                    return .6
                late = Path(next(p for p in state["clips"] if Path(p) != Path(state["persons"])))
                late.write_bytes(PERSONS)
                state["late"] = str(late)
            else:   # The person ticks Replace in the browser's own panel, where the files are listed.
                operator = area.spaces.active.active_operator
                check(tag + "_browser_lists_the_persons_clip", operator is not None
                      and Path(state["persons"]) in [Path(p) for p in operator._theirs(bpy.context)])
                operator.replace_existing = True
            press_return(win, region)
            state["phase"] = {"unticked": "refused", "late": "late_refused"}.get(attempt, "written")
            state["since"] = time.monotonic()
            return .5
        if state["phase"] == "refused":
            if not closed():
                return .2
            persons, model = Path(state["persons"]), Path(state["model"])
            check(tag + "_unticked_writes_nothing", persons.read_bytes() == PERSONS and not model.exists(),
                  {"model_exists": model.exists()})
            state["late_ticked"] = None
            open_export("late")
            return .3
        if state["phase"] == "late_refused":
            if not closed():
                return .2
            persons, model, late = Path(state["persons"]), Path(state["model"]), Path(state["late"])
            check(tag + "_replace_covers_only_the_files_it_showed", persons.read_bytes() == PERSONS
                  and late.read_bytes() == PERSONS and not model.exists(), {"model_exists": model.exists()})
            late.unlink()
            open_export("ticked")
            return .3
        if state["phase"] == "written":
            persons, model = Path(state["persons"]), Path(state["model"])
            done = model.exists() and persons.read_bytes() != PERSONS
            if not done and time.monotonic() - state["since"] < 15:
                return .2
            check(tag + "_ticked_replaces_and_writes_every_clip", done and all(
                Path(p).exists() and Path(p).stat().st_size > 200 for p in state["clips"]),
                {"model": model.exists(), "clips": {Path(p).name: Path(p).exists() for p in state["clips"]}})
            check(tag + "_nothing_where_the_name_pointed", not (OUT.parent / "steer").exists()
                  and not (DOCUMENT.parent.parent.parent / "steer").exists())
            state["pass"] += 1
            if state["pass"] < len(PASSES):
                run("result = 1")   # A script region has begun: C answers by its records from here on.
                begin_pass()
                return .3
            state["model_bytes"] = Path(state["model"]).read_bytes()
            open_export("cancel")
            return .3
        if state["phase"] == "cancelled":
            if not closed():
                return .2
            model = Path(state["model"])
            outcome = run(SCRIPT, NAME=state["rig"], TARGET=str(model), FORMAT="GLB")
            check("cancel_then_script_cannot_write_the_picked_file", model.read_bytes() == state["model_bytes"],
                  outcome)
            outside = PLAIN / "assistant.glb"
            outcome = run(SCRIPT, NAME=state["rig"], TARGET=str(outside), FORMAT="GLB")
            check("assistant_path_refused_nothing_written", not outside.exists(), outcome)
            persons = Path(state["persons"])
            persons.write_bytes(PERSONS)
            outcome = run(SCRIPT, NAME=state["rig"], TARGET=state["model"], FORMAT="FBX")
            check("assistant_cannot_replace_a_persons_clip", persons.read_bytes() == PERSONS, outcome)
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
