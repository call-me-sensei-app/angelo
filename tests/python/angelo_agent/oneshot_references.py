# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""References of the one-shot tests: rendered from a ground truth, or cut from a source picture (host side).

``scenario_references.make`` hands a one-shot test to ``make`` here. It runs
``oneshot_job.py --job reference`` in a background stock Blender (the same
isolation as the scenario references) and writes the test's reference folder
(``scenario_specs.references_dir``): the JPEG renders of every fixed camera,
the crops of the test's source pictures, ``ground-truth.blend``,
``capture.npz``, ``selfcheck.json`` and ``manifest.json``. A test with a
start file (``START``) has it made first (``scenario_start_job.py``) and kept
in the folder; the reference job then begins from it, as both sides of a
trial do, and the manifest records it.

The manifest says where every image came from, with its SHA-256:

* ``{"kind": "render", ...}``: rendered from the test's own ground truth
  (the module and the Blender that made it are named);
* ``{"kind": "crop", ...}``: cut from a source picture, with that picture's
  file name and SHA-256, the pixel box and the enlargement.

Source pictures are never downloaded from a public place and never part of this repository (the Windows bench
machines get them from their private bucket: final_runs_sources). A
test names the file and its SHA-256 (``SOURCE_IMAGES``); the file is looked
for in ``source_root()`` and refused unless its hash matches, so a run can
only ever use the exact picture the test was written for.
"""

import datetime
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import scenario_references as references
import scenario_specs as specs

HERE = Path(__file__).resolve().parent
SOURCES_ENV = "ANGELO_ONESHOT_SOURCES"
SOURCE_ROOT = Path.home() / ".angelo-bench" / "oneshot" / "sources"


def source_root():
    """Where source pictures are kept: ``ANGELO_ONESHOT_SOURCES``, else ``~/.angelo-bench/oneshot/sources``."""
    return Path(os.environ.get(SOURCES_ENV) or SOURCE_ROOT)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files(scenario):
    """``{file name: verified path}`` of a test's source pictures; raises when one is missing or not the same file."""
    found = {}
    for spec in getattr(scenario, "SOURCE_IMAGES", {}).values():
        path = source_root() / spec["file"]
        if not path.is_file():
            raise FileNotFoundError("Source picture %s is not in %s (expected SHA-256 %s); copy it there or set %s"
                                    % (spec["file"], source_root(), spec["sha256"], SOURCES_ENV))
        if sha256(path) != spec["sha256"]:
            raise ValueError("Source picture %s is not the one this test was written for (SHA-256 %s expected)"
                             % (path, spec["sha256"]))
        found[spec["file"]] = str(path)
    return found


def _entry(path, role, label, source):
    data = path.read_bytes()
    if len(data) > specs.REFERENCE_MAX_BYTES:
        raise RuntimeError("Reference %s is %d bytes, over the %d-byte budget" % (path, len(data),
                                                                                specs.REFERENCE_MAX_BYTES))
    return {"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "media_type": specs.media_type(path), "role": role, "label": label, "source": source}


def make(name, blender=references.STOCK_BLENDER, force=False, samples=64):
    """The test's reference folder, made now if missing (or ``force``); returns its manifest."""
    name = specs.SHARED_REFERENCES.get(name, name)   # A shared folder is made by, and for, the test that owns it.
    folder = specs.references_dir(name)
    if not force and (folder / specs.MANIFEST).is_file():
        return json.loads((folder / specs.MANIFEST).read_text())
    scenario = specs.module(name)
    sources = source_files(scenario)   # Before any process: a missing picture is refused at once.
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=folder.name + "-", dir=folder.parent))
    scratch = work / ".scratch"
    start = getattr(scenario, "START", None)
    if start:   # The start file first (as for a scenario); the reference job then begins from it, as both sides do.
        code = references.run_job(blender, ["--scenario", name, "--out", str(work)], scratch, work / "start-job.log",
                                  script=HERE / "scenario_start_job.py")
        if code != 0:
            raise RuntimeError("Start job for %s failed (%s); see %s" % (name, code, work / "start-job.log"))
    code = references.run_job(blender, ["--job", "reference", "--scenario", name, "--out", str(work),
                                        "--samples", str(samples), "--sources", json.dumps(sources)],
                              scratch, work / "job.log", script=HERE / "oneshot_job.py",
                              blend=work / start if start else None)
    if code != 0:
        raise RuntimeError("Reference job for %s failed (%s); see %s" % (name, code, work / "job.log"))
    shutil.rmtree(scratch, ignore_errors=True)
    version = references.blender_version(blender)
    images = {}
    for view, spec in scenario.CAMERAS.items():
        images[view] = _entry(work / (view + ".jpg"), spec["role"], spec["label"], {
            "kind": "render", "ground_truth": "scenario_%s.py" % name, "camera": view, "blender": version,
            "is_target_look": getattr(scenario, "GROUND_TRUTH", "reference") == "reference"})
    crops = json.loads((work / "crops.json").read_text())
    for key, spec in getattr(scenario, "SOURCE_IMAGES", {}).items():
        images[key] = _entry(work / (key + ".jpg"), "source", spec["label"], {
            "kind": "crop", "of": spec["file"], "source_sha256": spec["sha256"], "origin": spec.get("origin", ""),
            "box": list(spec["box"]) if spec.get("box") else None, "scale": spec.get("scale", 1), **crops[key]})
    selfcheck = json.loads((work / "selfcheck.json").read_text())
    data = {"scenario": name, "title": scenario.TITLE, "digest": specs.digest(name),
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "blender": version, "resolution": list(specs.REFERENCE_RESOLUTION), "frame": scenario.FRAME,
            "images": images, "capture": "capture.npz", "ground_truth": "ground-truth.blend",
            "ground_truth_role": getattr(scenario, "GROUND_TRUTH", "reference"),
            "selfcheck": {"success": selfcheck["success"], "final": [selfcheck["final"]["passed"],
                                                                     selfcheck["final"]["total"]]}}
    if start:   # The start file both sides append before the message (scenario_specs.start_file).
        data["start"] = json.loads((work / "start.json").read_text())
    (work / specs.MANIFEST).write_text(json.dumps(data, indent=2))
    if folder.exists():
        shutil.rmtree(folder)
    work.rename(folder)
    return data


def attached(name):
    """``[{view, file, sha256, bytes, source}]`` of the images the brief attaches (None before they are made)."""
    data = specs.manifest(name)
    if data is None:
        return None
    return [dict(view=view, **{key: data["images"][view][key] for key in ("file", "sha256", "bytes", "source")})
            for view in specs.module(name).TURNS[0]["references"]]
