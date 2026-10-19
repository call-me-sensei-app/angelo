# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render, self-check and cache a scenario's references (host side; unbilled, no account).

    python3 tests/python/angelo_agent/scenario_references.py --scenario bridge [--blender PATH] [--force]
    python3 tests/python/angelo_agent/scenario_references.py --all

Runs ``scenario_job.py --job reference`` in a background stock Blender with an
isolated HOME, TMPDIR, CFFIXED_USER_HOME and BLENDER_USER_* (nothing touches
the person's Blender or Angelo folders), into
``scenario_specs.references_dir(id)``: the JPEG references (each within
``scenario_specs.REFERENCE_MAX_BYTES``), ``capture.npz``
(the ground truth's scoring capture), ``ground-truth.blend``,
``selfcheck.json`` and ``manifest.json`` (SHA-256 and size per image). A
folder is keyed by the digest of the scenario's sources, so an edited ground
truth gets new references and a run can never mix versions; an existing
complete folder is reused unless ``--force``.
"""

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scenario_specs as specs  # noqa: E402

STOCK_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
KEEP_ENV = ("PATH", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE")
# Windows (os.environ spells them in upper case there): what locates the system, its programs and the processor,
# never a user profile, as run_native_suites.INHERITED_ENV keeps them. Without SYSTEMROOT stock Blender's own
# extensions add-on could not create an SSL context at startup (ssl.SSLError on the bench VM).
KEEP_ENV_WINDOWS = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "OS", "USERNAME",
                    "PROGRAMDATA", "ALLUSERSPROFILE", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
                    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
                    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER")


def isolated_environment(scratch, platform=None):
    """A minimal environment whose home, temp and Blender user folders all live in ``scratch``.

    On Windows the home folder is ``USERPROFILE`` (Python's ``Path.home()`` ignores ``HOME`` there, and a job
    whose modules ask for it stops at once without it), with ``APPDATA`` and ``LOCALAPPDATA`` inside it, and the
    temporary folder is ``TEMP``/``TMP``; all of them point into ``scratch`` as ``HOME`` and ``TMPDIR`` do.
    """
    import os
    scratch = Path(scratch)
    for name in ("home", "tmp", "blender/config", "blender/scripts", "blender/datafiles", "blender/extensions"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    environment = {key: os.environ[key] for key in KEEP_ENV if key in os.environ}
    environment.update(HOME=str(scratch / "home"), CFFIXED_USER_HOME=str(scratch / "home"), TMPDIR=str(scratch / "tmp"),
                       BLENDER_USER_RESOURCES=str(scratch / "blender"),
                       BLENDER_USER_CONFIG=str(scratch / "blender/config"),
                       BLENDER_USER_SCRIPTS=str(scratch / "blender/scripts"),
                       BLENDER_USER_DATAFILES=str(scratch / "blender/datafiles"),
                       BLENDER_USER_EXTENSIONS=str(scratch / "blender/extensions"))
    if (platform or sys.platform) == "win32":
        roaming, local = scratch / "home/AppData/Roaming", scratch / "home/AppData/Local"
        for folder in (roaming, local):
            folder.mkdir(parents=True, exist_ok=True)
        environment.update({key: os.environ[key] for key in KEEP_ENV_WINDOWS if key in os.environ})
        environment.update(USERPROFILE=str(scratch / "home"), APPDATA=str(roaming), LOCALAPPDATA=str(local),
                           TEMP=str(scratch / "tmp"), TMP=str(scratch / "tmp"))
    return environment


def run_job(blender, arguments, scratch, log, timeout=1800, script=None, blend=None):
    """One background job (``scenario_job.py`` unless ``script``); returns its exit code (the log keeps Blender's output).

    ``blend``: a file Blender opens before the script runs (a scenario's start file), instead of the factory scene.
    """
    command = [blender, "-b", *([str(blend)] if blend else []), "--factory-startup", "--python-exit-code", "1",
               "--python", str(script or HERE / "scenario_job.py"), "--", *arguments]
    with open(log, "w") as handle:
        try:
            return subprocess.run(command, cwd=HERE, env=isolated_environment(scratch), stdout=handle,
                                  stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return "timeout"


def blender_version(blender):
    try:
        with tempfile.TemporaryDirectory(prefix="angelo-scenario-version-") as scratch:
            output = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=60,
                                    env=isolated_environment(scratch)).stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        return "unavailable: %s" % error
    return " ".join(line.strip() for line in output.splitlines()[:3])


def make(name, blender=STOCK_BLENDER, force=False, samples=64):
    """The scenario's reference folder, rendered now if missing (or ``force``); returns its manifest."""
    if specs.is_oneshot(name):   # Their own job: renders, crops of source pictures and where each came from.
        import oneshot_references
        return oneshot_references.make(name, blender, force, samples)
    folder = specs.references_dir(name)
    if not force and (folder / specs.MANIFEST).is_file():
        return json.loads((folder / specs.MANIFEST).read_text())
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=folder.name + "-", dir=folder.parent))
    scratch = work / ".scratch"
    start = getattr(specs.module(name), "START", None)
    if start:   # The start file first; the reference job then begins from it, as both sides of a trial do.
        code = run_job(blender, ["--scenario", name, "--out", str(work)], scratch, work / "start-job.log",
                       script=HERE / "scenario_start_job.py")
        if code != 0:
            raise RuntimeError("Start job for %s failed (%s); see %s" % (name, code, work / "start-job.log"))
    code = run_job(blender, ["--job", "reference", "--scenario", name, "--out", str(work), "--samples", str(samples)],
                   scratch, work / "job.log", blend=work / start if start else None)
    if code != 0:
        raise RuntimeError("Reference job for %s failed (%s); see %s" % (name, code, work / "job.log"))
    shutil.rmtree(scratch, ignore_errors=True)
    scenario = specs.module(name)
    images = {}
    for view, spec in scenario.CAMERAS.items():
        path = work / (view + ".jpg")
        data = path.read_bytes()
        if len(data) > specs.REFERENCE_MAX_BYTES:
            raise RuntimeError("Reference %s is %d bytes, over the %d-byte budget" % (path, len(data),
                                                                                    specs.REFERENCE_MAX_BYTES))
        images[view] = {"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                        "media_type": specs.media_type(path), "role": spec["role"], "label": spec["label"]}
    selfcheck = json.loads((work / "selfcheck.json").read_text())
    start = json.loads((work / "start.json").read_text()) if (work / "start.json").is_file() else None
    data = {"scenario": name, "title": scenario.TITLE, "digest": specs.digest(name),
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "blender": blender_version(blender), "resolution": list(specs.REFERENCE_RESOLUTION), "frame": scenario.FRAME,
            "images": images, "capture": "capture.npz", "ground_truth": "ground-truth.blend",
            "selfcheck": {"success": selfcheck["success"], "final": [selfcheck["final"]["passed"],
                                                                     selfcheck["final"]["total"]]}}
    if start is not None:   # The start file both sides append before the first message (scenario_specs.start_file).
        data["start"] = start
    (work / specs.MANIFEST).write_text(json.dumps(data, indent=2))
    if folder.exists():
        shutil.rmtree(folder)
    work.rename(folder)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=specs.SCENARIO_IDS + specs.ONESHOT_NAMES)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--blender", default=STOCK_BLENDER)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--samples", type=int, default=64)
    args = parser.parse_args(argv)
    names = specs.SCENARIO_IDS + specs.ONESHOT_NAMES if args.all else [args.scenario] if args.scenario else parser.error("--scenario or --all")
    for name in names:
        data = make(name, args.blender, args.force, args.samples)
        print("REFERENCES", name, specs.references_dir(name), json.dumps(data["selfcheck"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
