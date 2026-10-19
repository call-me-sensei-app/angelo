#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Repro: children of the bundled Python that still write bytecode inside the app.

The fix keeps the app's own interpreter and the children it starts the usual way out of the bundle, and precompiles
only what a child imports before ``sitecustomize`` runs (124 modules). A child that never runs the bundled
``sitecustomize``, or imports more before it, writes ``__pycache__`` next to the bundled sources again:

* ``pip install`` of a source distribution, started exactly as Angelo starts a child (``bpy.app.python_args`` and the
  inherited ``ANGELO_PYCACHE_PREFIX``): pip's isolated build environment puts its own ``sitecustomize`` first on
  ``PYTHONPATH`` for the build backend, which shadows the bundled one (offline: an in-tree backend, no requirements);
* the same shadowing with any ``PYTHONPATH`` holding a ``sitecustomize.py``; a venv made from the bundled Python;
* ``-W``/``-X dev``/``PYTHONWARNINGS`` (``warnings`` is imported before ``site``); a user-site ``.pth`` file
  (``python3.13`` from a Terminal, no ``-I``); ``-S``.

Runs on an APFS clone of ``--app`` (never the app itself). Exit 1 while any case writes inside the clone.
Precompiling the whole bundled library (``python3.13 -I -m compileall -j0 --invalidation-mode checked-hash
lib/python3.13``, about +34 MB) makes every case write nothing.

    python3 tests/python/angelo_agent/sealed_bundle_review/repro_child_writes.py --app <build folder>/bin/Angelo.app
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

BACKEND = '''
import base64, hashlib, os, zipfile
FILES = {"tinypkg/__init__.py": b"VALUE = 1\\n",
         "tinypkg-0.1.dist-info/METADATA": b"Metadata-Version: 2.1\\nName: tinypkg\\nVersion: 0.1\\n",
         "tinypkg-0.1.dist-info/WHEEL": b"Wheel-Version: 1.0\\nGenerator: review\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"}
def get_requires_for_build_wheel(config_settings=None):
    return []
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name, record = "tinypkg-0.1-py3-none-any.whl", []
    with zipfile.ZipFile(os.path.join(wheel_directory, name), "w") as wheel:
        for path, data in FILES.items():
            wheel.writestr(path, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.append("%s,sha256=%s,%d" % (path, digest, len(data)))
        wheel.writestr("tinypkg-0.1.dist-info/RECORD", "\\n".join(record + ["tinypkg-0.1.dist-info/RECORD,,"]) + "\\n")
    return name
'''
PYPROJECT = ('[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n\n'
             '[project]\nname = "tinypkg"\nversion = "0.1"\n')


def inventory(root):
    return {os.path.relpath(os.path.join(f, n), root) for f, d, fs in os.walk(root) for n in d + fs}


def remove_added(root, added):
    for rel in sorted(added, key=len, reverse=True):
        path = os.path.join(root, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            if not os.listdir(path):
                os.rmdir(path)
        elif os.path.lexists(path):
            os.remove(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, help="an Angelo.app (macOS); a clone of it is used")
    arguments = parser.parse_args(argv)
    work = tempfile.mkdtemp(prefix="angelo-child-writes-")
    try:
        clone = os.path.join(work, "Angelo.app")
        if subprocess.run(["cp", "-cR", arguments.app, clone]).returncode:
            shutil.copytree(arguments.app, clone, symlinks=True)
        python = os.path.join(clone, "Contents/Resources/5.2/python/bin/python3.13")
        cache, home = os.path.join(work, "angelo-cache"), os.path.join(work, "home")
        package, shadow = os.path.join(work, "tinypkg"), os.path.join(work, "shadow")
        for folder in (package, shadow, home):
            os.makedirs(folder)
        open(os.path.join(package, "backend.py"), "w").write(BACKEND)
        open(os.path.join(package, "pyproject.toml"), "w").write(PYPROJECT)
        open(os.path.join(shadow, "sitecustomize.py"), "w").write("pass\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "TMPDIR": work}
        user_site = subprocess.run([python, "-c", "import site; print(site.getusersitepackages())"], env=env,
                                   capture_output=True, text=True).stdout.strip()
        angelo_child = [python, "-I", "-X", "pycache_prefix=" + cache]  # bpy.app.python_args
        angelo_env = dict(env, ANGELO_PYCACHE_PREFIX=cache)
        venv = os.path.join(work, "venv")
        subprocess.run(angelo_child + ["-m", "venv", "--without-pip", venv], env=angelo_env, check=True)
        imports = "import json, email.mime.text, urllib.request"
        cases = [
            ("pip install <sdist> from an Angelo child (isolated build)", angelo_env,
             angelo_child + ["-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
                             "--target", os.path.join(work, "target"), package]),
            ("a sitecustomize.py on PYTHONPATH (Angelo child env)", dict(angelo_env, PYTHONPATH=shadow),
             [python, "-c", imports]),
            ("a venv made from the bundled Python", angelo_env, [os.path.join(venv, "bin/python"), "-c", imports]),
            ("-W ignore", env, [python, "-W", "ignore", "-c", "pass"]),
            ("-X dev", env, [python, "-X", "dev", "-c", "pass"]),
            ("a user-site .pth (Terminal, no -I)", dict(env, _PTH="import json"), [python, "-c", "pass"]),
            ("-S", env, [python, "-S", "-c", imports]),
        ]
        failures = 0
        for name, case_env, command in cases:
            pth = None
            if "_PTH" in case_env:
                os.makedirs(user_site, exist_ok=True)
                pth = os.path.join(user_site, "review.pth")
                open(pth, "w").write(case_env.pop("_PTH") + "\n")
            before = inventory(clone)
            result = subprocess.run(command, env=case_env, cwd=work, capture_output=True, text=True, timeout=600)
            added = sorted(inventory(clone) - before)
            if pth:
                os.remove(pth)
            remove_added(clone, added)
            pycs = [path for path in added if path.endswith(".pyc")]
            failures += bool(added)
            print("%s %-58s exit %d, %d entries written inside the app%s" % (
                "FAIL" if added else "ok  ", name, result.returncode, len(added),
                (": " + ", ".join(path.rsplit("/", 1)[-1] for path in pycs[:3])) if pycs else ""))
        return 1 if failures else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
