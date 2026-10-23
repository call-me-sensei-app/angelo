# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's first launch never shows a Blender form: real frames from the first one, on every way in.

Run outside Blender (``run_native_suites.py --gui --include test_blender_first_launch_frames`` does this)::

  python3 tests/python/angelo_agent/test_blender_first_launch_frames.py --binary /abs/Angelo --out /fresh/dir

Blender builds its startup splash before its main loop, while Angelo's add-on loads later; the startup module's
stand-ins (``angelo_default_layout``) keep the splash to its art until the add-on takes over. Each phase is one real
window process with its own sandboxed profile (HOME, CFFIXED_USER_HOME, TMPDIR and BLENDER_USER_* inside ``--out``),
no keychain, no agent runtime, no provider CLI on PATH and ``--offline-mode``. The default showcase is left on, as a
person sees it. From the first main-loop turn each phase captures the window as fast as it can for a few seconds
(``first_launch_frames_probe``) and checks every frame twice: which classes drew the splash's menus (Blender's own
and the stand-ins, counted from ``--python`` time, before the splash exists), and no frame has a form's selected
radio button in the splash's band (``splash_frames``). On every Angelo start (not the opted-out or failed ones) each
frame is also Angelo's own: until its layout has settled, the window below the top bar and outside the splash is
Angelo's one-colour cover, never the stock Layout's editors (``own_frame``, pixels; ``angelo_default_layout``).

Phases (``first_launch_frames_phases``):

1. ``warm``: a first launch. The first frame is the splash's art; the setup card follows, drawn over the showcase
   with the showcase's input handler above the card's, and takes its clicks; the Look control in dark and light,
   in English and Arabic (captured, with its rectangle in the receipt); Continue saves and the welcome follows.
2. ``second``: the same profile again. The add-on owns the splash menus before the splash opens, the start panel
   follows; Help > Splash Screen gives the start panel again; Preferences > Themes names "Angelo Dark" and
   "Angelo Light" (captured), with the chat's connect cards drawing their text in each, and in the very first
   frame after each look's preset is applied (pixels inside every welcome row: a preset reloads Blender's
   interface fonts, which keeps the fonts a script loaded), and the bundled presets are exactly those two.
3. ``cold``: a first launch with an empty bytecode folder (``--python-use-system-env`` with a
   ``PYTHONPYCACHEPREFIX`` the host checks is empty before launch): the splash keeps its art throughout; the launch
   compiles only what the app does not ship compiled and loads Angelo's add-on from its shipped bytecode
   (``cold_bytecode``).
4. ``finder``: a .blend opened as the Finder's open-document event does, after Angelo scheduled its setup card and
   before it showed: the setup card still follows, then the welcome.
5. ``finder-late``: the same file opened while the setup card shows: the card comes back once the file is open.
6. ``cli``: the .blend on the command line at first launch: the splash's art, then the setup card.
7. ``opted-out``: automation's way in (``ANGELO_DISABLE_DEFAULT_CHAT``, ``--factory-startup``, an empty profile):
   Angelo's startup never runs and the splash still shows its art alone (captured). Then the add-on is turned on and
   off and the splash opened again with no preferences saved: Blender's own Quick Setup class draws the art alone.
8. ``pixel-control``: control. A fixture menu draws a form row in the splash and the pixel check finds it, so a pass
   elsewhere is not a blind check.
9. ``failed-start``: enabling the add-on raises (the fixture's one expected error, the only one the log may hold):
   the splash keeps its art alone after the start failed.

Receipts: ``first-launch-frames.json`` (phases, checks and milestone timings in seconds after the host started each
process) and ``<phase>.json`` with the kept frames and captures beside them.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHASES = ("warm", "second", "cold", "finder", "finder-late", "cli", "opted-out", "pixel-control", "failed-start")
EXPECTED_FAILURE = "first-launch fixture: Angelo's add-on failed to start"


def environment(box, extra=None):
    """A sandboxed environment: every writable location inside ``box``; no inherited Angelo or Blender setting."""
    home = box / "home"
    for folder in (home / ".config", home / ".cache", home / ".local/share", home / ".local/state",
                   home / "AppData/Roaming", home / "AppData/Local", box / "tmp", box / "user/config"):
        folder.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_", "PYTHON"))}
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), USERPROFILE=str(home), TMPDIR=str(box / "tmp"),
               TMP=str(box / "tmp"), TEMP=str(box / "tmp"), APPDATA=str(home / "AppData/Roaming"),
               LOCALAPPDATA=str(home / "AppData/Local"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_CACHE_HOME=str(home / ".cache"), XDG_DATA_HOME=str(home / ".local/share"),
               XDG_STATE_HOME=str(home / ".local/state"), BLENDER_USER_RESOURCES=str(box / "user"),
               BLENDER_USER_CONFIG=str(box / "user/config"), BLENDER_USER_SCRIPTS=str(box / "user/scripts"),
               BLENDER_USER_DATAFILES=str(box / "user/datafiles"), BLENDER_USER_EXTENSIONS=str(box / "user/extensions"),
               ANGELO_DISABLE_KEYCHAIN="1", ANGELO_AGENT_RUNTIME="off", ANGELO_STATE_DIR=str(box / "state"),
               ANGELO_CONNECTIONS_DIR=str(box / "accounts"), ANGELO_LIBRARY_DIR=str(box / "library"))
    if os.name == "posix":
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"   # No provider CLI: the chat stays unconnected.
    if os.environ.get("PYTHONPYCACHEPREFIX"):
        # The runner's shared bytecode folder (run_native_suites.PYCACHE_FOLDER): compiled once, as for a returning
        # person; the ``cold`` phase sets an empty one of its own.
        env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
    env.update(extra or {})
    return env


def make_scene(binary, out):
    """A small .blend to open, saved by the app in the background (where Angelo's startup never runs)."""
    path = out / "scene" / "first-launch-scene.blend"
    path.parent.mkdir(parents=True)
    code = ("import bpy; bpy.ops.mesh.primitive_monkey_add(); "
            "bpy.ops.wm.save_as_mainfile(filepath=%r)" % str(path))
    subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", code],
                   env=environment(out / "scene-box"), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                   timeout=180, check=True)
    if not path.is_file():
        raise AssertionError("The fixture scene was not saved")
    return path


def run_host():
    sys.path.insert(0, str(HERE))
    import gui_launch
    import native_suite_checks
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--phases", default=",".join(PHASES), help="comma list (the receipt fails unless all ran)")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh disposable directory")
    out.mkdir(parents=True, exist_ok=True)
    binary = Path(args.binary).resolve(strict=True)
    scene = make_scene(binary, out)
    report = {"success": False, "binary": str(binary), "phases": [], "timings": {}}

    def phase(name, box, extra_env=None, before=(), python=(), expected_failure=None, factory_startup=False,
              empty_before=None):
        env = environment(out / box, extra_env)
        # A cold launch's premise, checked directly: its bytecode folder is empty or missing before it starts.
        cold = None if empty_before is None else not (empty_before.exists() and any(empty_before.iterdir()))
        command = [str(binary), "--disable-autoexec", *gui_launch.window_flags(), "--window-geometry", "60", "60",
                   "1440", "900", "--offline-mode", *(("--factory-startup",) if factory_startup else ()), *python,
                   *before, "--python",
                   str(Path(__file__).resolve()), "--", "--native-phase", name, "--fixture-root", str(out),
                   "--blend", str(scene), "--launched-at", repr(time.time()),
                   *(("--expected-failure", expected_failure) if expected_failure else ())]
        output = out / (name + ".out")
        with output.open("w") as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        data = json.loads((out / (name + ".json")).read_text())
        findings = native_suite_checks.scan_log(output.read_text(errors="replace"))
        host = {"exit_code_zero": result.returncode == 0}
        if cold is not None:
            host["bytecode_folder_empty_before_launch"] = cold
        if expected_failure:   # The fixture's own error must appear, and nothing else.
            host["expected_failure_seen"] = any(expected_failure in f["line"] for f in findings)
            findings = [f for f in findings if expected_failure not in f["line"]]
        host["no_failures_in_output"] = not findings
        data.update(exit_code=result.returncode, findings=findings, host=host)
        data["success"] = bool(data.get("success")) and all(host.values())
        report["phases"].append({key: data[key] for key in ("phase", "success", "checks", "timings", "host")})
        report["timings"][name] = data["timings"]
        if not data["success"]:
            raise AssertionError("Phase failed: %s %s %s %s" % (name, data.get("failed_checks"), json.dumps(host),
                                                                data.get("error")))

    wanted = args.phases.split(",")
    cold_cache = out / "cold" / "pycache"
    plans = {
        "warm": dict(box="returning"),
        "second": dict(box="returning"),
        "cold": dict(box="cold", python=("--python-use-system-env",), empty_before=cold_cache,
                     extra_env={"PYTHONPYCACHEPREFIX": str(cold_cache), "PYTHONNOUSERSITE": "1"}),
        "finder": dict(box="finder"),
        "finder-late": dict(box="finder-late"),
        "cli": dict(box="cli", before=(str(scene),)),
        "opted-out": dict(box="opted-out", extra_env={"ANGELO_DISABLE_DEFAULT_CHAT": "1"}, factory_startup=True),
        "pixel-control": dict(box="pixel-control", extra_env={"ANGELO_DISABLE_DEFAULT_CHAT": "1"}),
        "failed-start": dict(box="failed-start", expected_failure=EXPECTED_FAILURE),
    }
    try:
        for name in PHASES:
            if name in wanted:
                phase(name, **plans[name])
        report["success"] = [p["phase"] for p in report["phases"]] == list(PHASES) and all(
            p["success"] for p in report["phases"])
    finally:
        (out / "first-launch-frames.json").write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps({"success": report["success"], "phases": [(p["phase"], p["success"])
                                                                   for p in report["phases"]]}))
    if report["success"]:
        print("ANGELO_FIRST_LAUNCH_FRAMES_OK", flush=True)
    else:
        raise SystemExit(1)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        run_host()
    raise SystemExit

sys.path.insert(0, str(HERE))
import first_launch_frames_phases  # noqa: E402  (the in-app half: Blender only)
import first_launch_frames_probe  # noqa: E402

first_launch_frames_probe.start(first_launch_frames_phases.PHASES, first_launch_frames_phases.AT_PYTHON)
