# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-shot tests' source pictures on the Windows bench machines (doc/angelo/final_runs.rst).

A one-shot test that cuts its reference from a picture (``SOURCE_IMAGES`` in its
scenario module: the tree and the cottage) names the file and its SHA-256; the
picture itself is never part of this repository and is never downloaded from a
public place (``oneshot_references``). Those pins are the manifest here:

* on the Mac, ``paths`` checks a folder against them before
  ``gcp_windows_validate.sh oneshot-sources`` uploads exactly the pinned files to
  the machine's private bucket (``bench/oneshot-sources/``);
* on the VM, ``Prepare-Bench`` downloads that folder and ``check`` verifies it
  again; the bench session then runs with ``ANGELO_ONESHOT_SOURCES`` set to it.

Standard library and the tests' own modules only; it reads no credential::

    python final_runs_sources.py manifest                # {file: sha256} of every pinned picture
    python final_runs_sources.py check --dir FOLDER      # exit 1 unless each is there with its hash
    python final_runs_sources.py paths --dir FOLDER      # check, then the pinned files' paths, one per line
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scenario_specs as specs  # noqa: E402


def manifest():
    """``{file name: sha256}`` of every source picture a one-shot test pins (one file may serve several tests)."""
    pins = {}
    for name in specs.ONESHOT_NAMES:
        for spec in getattr(specs.module(name), "SOURCE_IMAGES", {}).values():
            if pins.setdefault(spec["file"], spec["sha256"]) != spec["sha256"]:
                raise ValueError("%s is pinned with two different SHA-256 values" % spec["file"])
    return dict(sorted(pins.items()))


def check(folder, pins=None):
    """What ``folder`` holds against the pins: ``ok`` only when every pinned picture is there with its hash."""
    folder, pins = Path(folder), manifest() if pins is None else pins
    found, missing, mismatched = {}, [], []
    for name, expected in pins.items():
        path = folder / name
        if not path.is_file():
            missing.append(name)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == expected:
            found[name] = actual
        else:
            mismatched.append(name)
    unexpected = sorted(path.name for path in folder.iterdir() if path.is_file() and path.name not in pins) \
        if folder.is_dir() else []
    return {"folder": str(folder), "ok": not missing and not mismatched, "files": found, "missing": missing,
            "mismatched": mismatched, "unexpected": unexpected}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("manifest", "check", "paths"))
    parser.add_argument("--dir", help="check, paths: the folder holding the pictures")
    args = parser.parse_args(argv)
    if args.action == "manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    if not args.dir:
        parser.error("--dir is required")
    result = check(args.dir)
    if args.action == "check" or not result["ok"]:
        print(json.dumps(result, indent=2), file=sys.stdout if result["ok"] else sys.stderr)
        return 0 if result["ok"] else 1
    for name in result["files"]:
        print(Path(args.dir) / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
