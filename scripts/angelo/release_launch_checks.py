#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launch checks on a release candidate: it starts, and starting it changes nothing inside it.

``sign_notarize_macos.py --launch-check`` runs both on its signed app; from the command line, the seal
check alone (doc/angelo/releasing.rst, "Nothing is written inside the app")::

    python3 scripts/angelo/release_launch_checks.py seal --app <Angelo.app> [--gui] [--report seal.json]
    python scripts\\angelo\\release_launch_checks.py seal --install <install folder> [--gui] [--report ...]

* **Launch check** (``launch_check``): a clone of the signed app starts headless under the hardened
  runtime, calls through ctypes, imports NumPy and renders an OSL material with Cycles; the clone's signed
  agent runtime reports the pinned version and its app-server schema.
* **Seal check** (``seal_check``): a launch must write nothing inside the installed app. On macOS a clone
  starts through Launch Services (``open``), as Finder starts it: only then may the app write into its
  own bundle. Started from a shell without App Management permission, its writes are refused and nothing
  can be learned. One headless launch (``release_seal_probe.py``) enables the add-on and starts the child
  Pythons that could write (``sys.executable`` with ``bpy.app.python_args``, ``pip`` building a source
  distribution, ``-W``, a ``multiprocessing`` worker, ``-O``, ``-OO`` and ``PYTHONOPTIMIZE=1`` children that
  only inherit the exported bytecode folder, a ``-O`` child with a replaced environment), and requires every
  bundled module precompiled; ``--gui`` adds a
  window launch that runs the add-on's own startup and quits after it settles. Each uses a throwaway
  profile. The clone's file inventory must be unchanged and ``codesign --verify --deep --strict`` must
  still pass. On Windows the install folder itself is launched and its inventory compared.

Standard library only (Python 3.9 or newer, as Xcode's ``python3``).
"""

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The one source of window flags (tests/python/angelo_agent/gui_launch.py, test_native_gui_launch_focus).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/python/angelo_agent"))
import gui_launch  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import release_checks  # noqa: E402

CheckError = release_checks.CheckError
IDENTITY = json.loads((release_checks.ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
SEAL_MARK, GUI_MARK, LAUNCH_MARK = "ANGELO_SEAL_PROBE ", "ANGELO_SEAL_GUI_PROBE ", "ANGELO_LAUNCH_CHECK "


def run(argv, check=False, timeout=None, env=None):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode != 0:
        raise CheckError("%s failed (%d): %s" % (" ".join(argv[:3]), result.returncode,
                                                 (result.stderr or result.stdout).strip()[-1500:]))
    return result


def bundle_executable(bundle):
    info = plistlib.loads((Path(bundle) / "Contents/Info.plist").read_bytes())
    return Path(bundle) / "Contents/MacOS" / info["CFBundleExecutable"]


def probe_line(text, mark):
    """The JSON a probe printed after ``mark``, or None."""
    line = next((line for line in text.splitlines() if line.startswith(mark)), "")
    return json.loads(line[len(mark):]) if line else None


# -- launch check ----------------------------------------------------------------------------------------------

LAUNCH_CHECK = r"""
import ctypes, json, os, sys, tempfile
import bpy, numpy
result = {"version": bpy.app.version_string}
callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda value: value + 1)
result["ctypes_callback"] = callback(41) == 42
result["numpy"] = float(numpy.arange(4.0).sum()) == 6.0
result["cycles_osl"] = bool(bpy.app.build_options.cycles_osl)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.shading_system = True
scene.cycles.samples = 1
scene.render.resolution_x = scene.render.resolution_y = 16
scene.render.threads_mode, scene.render.threads = "FIXED", 2
text = bpy.data.texts.new("angelo_probe.osl")
text.write("shader angelo_probe(output color Out = color(0.2, 0.5, 0.8)) { }\n")
material = bpy.data.materials.new("probe")
if material.node_tree is None:
    material.use_nodes = True
nodes = material.node_tree.nodes
script = nodes.new("ShaderNodeScript")
script.mode, script.script = "INTERNAL", text
material.node_tree.links.new(script.outputs["Out"], nodes["Principled BSDF"].inputs["Base Color"])
bpy.data.objects["Cube"].data.materials.append(material)
scene.render.filepath = os.path.join(tempfile.mkdtemp(), "probe.png")
bpy.ops.render.render(write_still=True)
result["osl_render"] = os.path.getsize(scene.render.filepath) > 0
print("ANGELO_LAUNCH_CHECK " + json.dumps(result))
sys.stdout.flush()
if not all(value for key, value in result.items() if key != "version"):
    sys.exit(1)
"""


def launch_check(app, runner, timeout=900):
    """Start a clone of the signed app headless; its results, or CheckError. Started from this process (not
    Launch Services), so it could not write into its bundle even if it tried; ``seal_check`` proves it does not."""
    with tempfile.TemporaryDirectory(prefix="angelo-launch-") as folder:
        clone, home = Path(folder, app.name), Path(folder, "home")
        runner(["ditto", str(app), str(clone)], check=True)
        for name in ("user", "state", "connections", "tmp"):
            (home / name).mkdir(parents=True)
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "CFFIXED_USER_HOME": str(home),
               "TMPDIR": str(home / "tmp"), "BLENDER_USER_RESOURCES": str(home / "user"),
               "ANGELO_STATE_DIR": str(home / "state"), "ANGELO_CONNECTIONS_DIR": str(home / "connections"),
               "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_INTRO": "1", "ANGELO_DISABLE_HERO": "1"}
        argv = [str(bundle_executable(clone)), "--background", "--factory-startup", "--disable-autoexec", "-t", "2",
                "--python-exit-code", "1", "--python-expr", LAUNCH_CHECK]
        result = runner(argv, check=False, timeout=timeout, env=env)
        report = probe_line(result.stdout, LAUNCH_MARK)
        if result.returncode != 0 or report is None:
            raise CheckError("the signed app failed its launch check (exit %d): %s" % (
                result.returncode, (result.stderr or result.stdout).strip()[-1500:]))
        report["exit"] = result.returncode
        runtime = release_checks.agent_runtime_folder(clone, "macos")
        if runtime.is_dir():  # The signed runtime runs under its own hardened runtime and entitlements.
            problems = release_checks.agent_runtime_problems(runtime, "macos", runner, exclusive=True)
            if problems:
                raise CheckError("the signed agent runtime failed its launch check: " + "; ".join(problems))
            report["agent_runtime"] = release_checks.agent_runtime_pin()["version"]
        return report


# -- seal check ------------------------------------------------------------------------------------------------

# Headless: the add-on, the child Pythons that could write inside the app, the build's precompiled bytecode.
SEAL_PROBE = (HERE / "release_seal_probe.py").read_text(encoding="utf-8")

# A window launch: Angelo's own startup enables the add-on; quit once it has settled.
GUI_PROBE = r"""
import json, sys, time
import addon_utils, bpy
started, state = time.monotonic(), {}
def tick():
    now = time.monotonic() - started
    if addon_utils.check("angelo_agent")[1] and "addon_at" not in state:
        state["addon_at"] = round(now, 2)
    if ("addon_at" in state and now - state["addon_at"] > SETTLE) or now > LIMIT:
        state.update(pycache_prefix=sys.pycache_prefix, quit_at=round(now, 2))
        print("ANGELO_SEAL_GUI_PROBE " + json.dumps(state), flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return 0.25
bpy.app.timers.register(tick, first_interval=0.25, persistent=True)
"""


def snapshot(root):
    """``{relative path: (kind, size, mtime_ns)}`` of everything below ``root``; folders by path only."""
    entries = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(folder, name)
            stat = os.lstat(path)
            kind = "link" if os.path.islink(path) else "dir" if os.path.isdir(path) else "file"
            entries[os.path.relpath(path, root)] = (kind, stat.st_size, stat.st_mtime_ns) if kind == "file" else (kind,)
    return entries


def changes(before, after):
    """What a launch did inside the installation: ``{"added": [...], "removed": [...], "changed": [...]}``."""
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(path for path in set(before) & set(after) if before[path] != after[path])}


def write_probe(folder, name, text):
    path = Path(folder, name)
    path.write_text(text, encoding="utf-8")
    return path


def profile_environment(profile):
    """A throwaway profile: home, temporary and user folders inside ``profile`` on every platform."""
    home = Path(profile, "home")
    for path in (home / "AppData/Roaming", home / "AppData/Local", Path(profile, "tmp")):
        path.mkdir(parents=True, exist_ok=True)
    return {"HOME": str(home), "CFFIXED_USER_HOME": str(home), "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
            "TMPDIR": str(Path(profile, "tmp")), "TEMP": str(Path(profile, "tmp")), "TMP": str(Path(profile, "tmp")),
            "ANGELO_DISABLE_KEYCHAIN": "1"}


def caller_environment():
    """This process's environment without Python, Blender or Angelo settings. ``open`` hands its own environment to
    the app, and so does a direct start: a caller's ``PYTHONDONTWRITEBYTECODE`` would hide every write."""
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith(("PYTHON", "BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_"))}


def launch_services(app, env, args, output, runner, timeout):
    """Start ``app`` the way Finder does and wait; its standard output. A launch that outlives ``timeout`` is
    killed (every process run from the clone) and refused."""
    argv = ["open", "-n", "-W", "-g", "-j"]
    for key, value in sorted(env.items()):
        argv += ["--env", "%s=%s" % (key, value)]
    argv += ["--stdout", str(output), "--stderr", str(output) + ".err", "-a", str(app), "--args"] + list(args)
    try:
        runner(argv, check=False, timeout=timeout, env=caller_environment())
    except subprocess.TimeoutExpired:
        runner(["pkill", "-f", str(app) + "/Contents/"], check=False)
        raise CheckError("%s did not quit within %d s" % (app.name, timeout)) from None
    return Path(output).read_text(encoding="utf-8", errors="replace") if Path(output).exists() else ""


def launch_direct(executable, env, args, output, runner, timeout):
    """Start ``executable`` with ``env`` added to this process's environment (Windows); its standard output."""
    result = runner([str(executable)] + list(args), check=False, timeout=timeout, env=dict(caller_environment(), **env))
    Path(output).write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
    return result.stdout or ""


def seal_launches(start, folder, gui, settle, timeout):
    """The headless launch, then (``gui``) the window launch, each with its own throwaway profile."""
    probe = write_probe(folder, "seal_probe.py", SEAL_PROBE)
    launches = []
    output = start(profile_environment(Path(folder, "profile-headless")),
                   ["--background", "--python-exit-code", "1", "--python", str(probe)], Path(folder, "headless.log"))
    report = probe_line(output, SEAL_MARK)
    if report is None:
        raise CheckError("the headless launch printed no probe: %s" % output.strip()[-1500:])
    launches.append(dict(report, kind="headless"))
    if gui:
        text = GUI_PROBE.replace("SETTLE", repr(float(settle))).replace("LIMIT", repr(float(timeout) * 0.8))
        probe = write_probe(folder, "seal_gui_probe.py", text)
        # Behind other applications, with real input: the app as shipped, never focused (no event simulation).
        output = start(profile_environment(Path(folder, "profile-gui")),
                       [*gui_launch.window_flags(input_isolation=False), "--python", str(probe)], Path(folder, "gui.log"))
        report = probe_line(output, GUI_MARK)
        if report is None or "addon_at" not in report:
            raise CheckError("the window launch did not load Angelo's add-on: %s" % output.strip()[-1500:])
        launches.append(dict(report, kind="gui"))
    return launches


def probe_problems(launches):
    """What the probes themselves report wrong: an interpreter writing into Python's default location, a child
    that failed, stale precompiled bytecode."""
    problems = []
    headless = launches[0]
    if not headless.get("writes_bytecode"):
        problems.append("the launch wrote no bytecode at all (PYTHONDONTWRITEBYTECODE?), so it proves nothing")
    elif not headless.get("pycache_prefix"):
        problems.append("the app writes bytecode with no cache folder (inside the installation)")
    if not headless.get("addon"):
        problems.append("the add-on did not enable")
    prefix = headless.get("pycache_prefix") or ""
    child = headless["python_args_child"]
    if child["exit"] != 0:
        problems.append("a child started with bpy.app.python_args failed: %s" % child["error"])
    elif prefix and child["pycache_prefix"] != prefix:
        problems.append("a child started with bpy.app.python_args keeps bytecode in %s, not %s"
                        % (child["pycache_prefix"], prefix))
    worker = headless["multiprocessing_child"]["bytecode_for"]
    if prefix and not worker.startswith(prefix):
        problems.append("a multiprocessing worker would write bytecode to %s, not below %s" % (worker, prefix))
    for name in ("pip_sdist_child", "warnings_child"):
        if headless[name]["exit"] != 0:
            problems.append("the %s failed: %s" % (name.replace("_", " "), headless[name]["error"]))
    for name, child in sorted(headless["optimize_children"].items()):
        if child["exit"] != 0:
            problems.append("the optimizing child (%s) failed: %s" % (name, child["error"]))
        elif prefix and "pycache_prefix" in child and child["pycache_prefix"] != prefix:
            problems.append("the optimizing child (%s) keeps bytecode in %s, not %s"
                            % (name, child["pycache_prefix"], prefix))
    precompiled = headless["bytecode"]
    if not precompiled["files"]:
        problems.append("no precompiled bytecode (build_files/utils/angelo_python_bytecode.py did not run)")
    if precompiled["stale"]:
        problems.append("precompiled bytecode differs from its source: %s" % precompiled["stale"])
    if precompiled["missing"]:
        problems.append("modules have no precompiled bytecode: %s" % precompiled["missing"])
    return problems


def seal_check(app, runner, gui=False, settle=10.0, timeout=600, keep=None):
    """Launch a clone of the macOS ``app`` and require it unchanged and still validly signed (CheckError
    otherwise); the report. ``keep``: write the report (JSON) to this file, pass or fail."""
    app = Path(app).resolve()
    with tempfile.TemporaryDirectory(prefix="angelo-seal-") as folder:
        clone = Path(folder, app.name)
        runner(["ditto", str(app), str(clone)], check=True)
        # A development build is not validly signed as a whole; then only the inventory can be compared.
        signed = runner(["codesign", "--verify", "--deep", "--strict", str(clone)], check=False).returncode == 0
        before = snapshot(clone)
        started = time.monotonic()
        launches = seal_launches(lambda env, args, output: launch_services(clone, env, args, output, runner, timeout),
                                 folder, gui, settle, timeout)
        report = {"app": str(app), "launches": launches, "seconds": round(time.monotonic() - started, 1),
                  "changed": changes(before, snapshot(clone))}
        if signed:
            verify = runner(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(clone)], check=False)
            report["codesign_verify"] = {"exit": verify.returncode,
                                         "output": (verify.stderr or verify.stdout).strip().splitlines()[-2:]}
        else:
            report["codesign_verify"] = {"skipped": "the app was not validly signed before the launch"}
        return finish(report, keep)


def seal_check_install(install, runner, executable=None, gui=False, settle=10.0, timeout=600, keep=None):
    """Windows: launch the install folder's app in place and require the folder unchanged; the report."""
    install = Path(install).resolve()
    executable = Path(executable) if executable else install / (
        IDENTITY["windows"]["exe_stem"] + ".exe")
    with tempfile.TemporaryDirectory(prefix="angelo-seal-") as folder:
        before = snapshot(install)
        started = time.monotonic()

        def start(env, args, output):
            return launch_direct(executable, env, args, output, runner, timeout)

        launches = seal_launches(start, folder, gui, settle, timeout)
        report = {"install": str(install), "launches": launches, "seconds": round(time.monotonic() - started, 1),
                  "changed": changes(before, snapshot(install))}
        return finish(report, keep)


def finish(report, keep):
    problems = probe_problems(report["launches"])
    count = sum(len(paths) for paths in report["changed"].values())
    if count:
        sample = (report["changed"]["added"] + report["changed"]["changed"] + report["changed"]["removed"])[:5]
        problems.append("a launch changed %d entries inside the installation, e.g. %s" % (count, sample))
    if report.get("codesign_verify", {}).get("exit"):
        problems.append("codesign --verify --deep --strict fails after the launch: %s"
                        % " ".join(report["codesign_verify"]["output"]))
    report["problems"] = problems
    if keep:
        Path(keep).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if problems:
        raise CheckError("seal check: " + "; ".join(problems))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal", help="a launch must write nothing inside the installed app")
    where = seal.add_mutually_exclusive_group(required=True)
    where.add_argument("--app", help="an Angelo.app (macOS); a clone of it is launched")
    where.add_argument("--install", help="an install folder (Windows); launched in place")
    seal.add_argument("--exe", help="the executable in --install (default: the identity's Angelo.exe)")
    seal.add_argument("--gui", action="store_true", help="also a window launch (needs a desktop session)")
    seal.add_argument("--settle", type=float, default=10.0, help="seconds to stay open after the add-on loads")
    seal.add_argument("--timeout", type=int, default=600, help="seconds per launch")
    seal.add_argument("--report", help="write the report (JSON) here, pass or fail")
    arguments = parser.parse_args(argv)
    try:
        if arguments.app:
            report = seal_check(arguments.app, run, arguments.gui, arguments.settle, arguments.timeout,
                                arguments.report)
        else:
            report = seal_check_install(arguments.install, run, arguments.exe, arguments.gui, arguments.settle,
                                        arguments.timeout, arguments.report)
    except (CheckError, OSError, subprocess.SubprocessError) as error:
        print("release_launch_checks: %s" % error, file=sys.stderr)
        return 1
    verify = report.get("codesign_verify", {})
    print("seal check: %d launches, nothing changed inside the installation%s" % (
        len(report["launches"]), "; codesign --verify --deep --strict still passes" if verify.get("exit") == 0
        else "; %s" % verify["skipped"] if "skipped" in verify else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
