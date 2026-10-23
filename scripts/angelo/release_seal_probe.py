# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The seal check's headless probe, run inside the app being checked (``release_launch_checks.seal_check``).

``Angelo --background --python release_seal_probe.py``: enables the add-on as a window launch does, then starts
the children that could write bytecode inside the app, then checks the build's precompiled bytecode, and prints
``ANGELO_SEAL_PROBE <json>``. The children:

* one started as Blender's extension system starts one (``sys.executable`` with ``bpy.app.python_args``);
* ``pip install`` of a source distribution, started the same way: pip's isolated build environment puts its own
  ``sitecustomize`` first, which hides the bundled one (offline: an in-tree backend, no requirements);
* one with ``-W``, which imports ``warnings`` before ``site``;
* a ``multiprocessing`` worker (``spawn``), as Blender's remote asset downloads start one;
* optimizing children started without ``bpy.app.python_args`` (as an add-on or a tool starts one): ``-O`` and
  ``-OO`` (the modules imported before ``sitecustomize`` read ``opt-1``/``opt-2`` bytecode, which the app does not
  ship), and ``pip install`` of the source distribution with ``PYTHONOPTIMIZE=1`` (its isolated build bypasses
  ``sitecustomize`` while optimizing). Each inherits the bytecode folder Angelo's process exported
  (``PYTHONPYCACHEPREFIX``) before its interpreter starts, so it writes there, never beside a source;
* a ``-O`` child whose environment is replaced (``env={...}``, a common pattern in add-ons and tools): it sees no
  exported folder, so the startup modules' shipped ``opt-1`` bytecode (``precompile_optimized``) is what keeps it
  from writing beside their sources.

The bytecode check: every hash-based ``.pyc`` in the bundled library and the scripts matches its source, and
every ``.py`` that compiles has one (``build_files/utils/angelo_python_bytecode.py``). Not the add-on's module:
Blender only, standard library otherwise.
"""

import importlib.util
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import sysconfig
import tempfile

BACKEND = '''
import base64, hashlib, os, zipfile
NAME = "angelo_probe_pkg"
FILES = {NAME + "/__init__.py": b"VALUE = 1\\n",
         NAME + "-0.1.dist-info/METADATA": b"Metadata-Version: 2.1\\nName: angelo-probe-pkg\\nVersion: 0.1\\n",
         NAME + "-0.1.dist-info/WHEEL": (b"Wheel-Version: 1.0\\nGenerator: probe\\nRoot-Is-Purelib: true\\n"
                                         b"Tag: py3-none-any\\n")}
def get_requires_for_build_wheel(config_settings=None):
    return []
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    wheel_name, record = NAME + "-0.1-py3-none-any.whl", []
    with zipfile.ZipFile(os.path.join(wheel_directory, wheel_name), "w") as wheel:
        for path, data in FILES.items():
            wheel.writestr(path, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.append("%s,sha256=%s,%d" % (path, digest, len(data)))
        wheel.writestr(NAME + "-0.1.dist-info/RECORD", "\\n".join(record + [NAME + "-0.1.dist-info/RECORD,,"]) + "\\n")
    return wheel_name
'''
PYPROJECT = ('[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n\n'
             '[project]\nname = "angelo-probe-pkg"\nversion = "0.1"\n')


def run(argv, cwd, env=None):
    child = subprocess.run(argv, capture_output=True, text=True, timeout=600, cwd=cwd, env=env)
    return {"exit": child.returncode, "stdout": child.stdout.strip()[-200:], "error": child.stderr.strip()[-400:]}


def bytecode(roots):
    """Every hash-based .pyc must match its source, and every .py that compiles must have one."""
    report = {"files": 0, "stale": [], "missing": [], "uncompilable": 0}
    for root in roots:
        for folder, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name != "__pycache__"]
            for name in names:
                if not name.endswith(".py"):
                    continue
                source = os.path.join(folder, name)
                cfile = os.path.join(folder, "__pycache__", "%s.%s.pyc" % (name[:-3], sys.implementation.cache_tag))
                with open(source, "rb") as handle:
                    data = handle.read()
                try:
                    with open(cfile, "rb") as handle:
                        header = handle.read(16)
                except OSError:
                    header = b""
                relative = os.path.relpath(source, os.path.dirname(root))
                if len(header) == 16 and int.from_bytes(header[4:8], "little") & 1:  # Hash-based (PEP 552).
                    report["files"] += 1
                    if header[8:] != importlib.util.source_hash(data):
                        report["stale"].append(relative)
                    continue
                try:
                    compile(data, source, "exec", dont_inherit=True)
                except (SyntaxError, ValueError):
                    report["uncompilable"] += 1  # Cannot be imported either, so never compiled at run time.
                else:
                    report["missing"].append(relative)
    report["stale"], report["missing"] = report["stale"][:20], report["missing"][:20]
    return report


def probe():
    import addon_utils
    import bpy
    result = {"pycache_prefix": sys.pycache_prefix, "writes_bytecode": not sys.flags.dont_write_bytecode,
              "python_args": list(bpy.app.python_args)}
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    result["addon"] = bool(addon_utils.check("angelo_agent")[1])
    work = tempfile.mkdtemp(prefix="angelo-seal-probe-")
    angelo_child = [sys.executable, *bpy.app.python_args]
    child = run(angelo_child + ["-c", "import sys, json, email.mime.text, urllib.request; print(sys.pycache_prefix)"],
                work)
    child["pycache_prefix"] = child.pop("stdout")
    result["python_args_child"] = child
    package = os.path.join(work, "package")
    os.makedirs(package)
    with open(os.path.join(package, "backend.py"), "w", encoding="utf-8") as handle:
        handle.write(BACKEND)
    with open(os.path.join(package, "pyproject.toml"), "w", encoding="utf-8") as handle:
        handle.write(PYPROJECT)
    result["pip_sdist_child"] = run(angelo_child + ["-m", "pip", "install", "--no-index", "--no-deps",
                                                    "--no-cache-dir", "--disable-pip-version-check",
                                                    "--target", os.path.join(work, "target"), package], work)
    result["warnings_child"] = run([sys.executable, "-W", "ignore", "-c", "import json, email.mime.text"], work)
    # Started with this process's environment only: the exported folder is all that keeps them out of the app.
    report = "import sys, json, email.mime.text, urllib.request; print(sys.pycache_prefix)"
    optimizing = {"-O": run([sys.executable, "-O", "-c", report], work),
                  "-OO": run([sys.executable, "-OO", "-c", report], work)}
    for child in optimizing.values():
        child["pycache_prefix"] = child.pop("stdout")
    optimizing["PYTHONOPTIMIZE=1 pip"] = run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                                              "--no-cache-dir", "--disable-pip-version-check",
                                              "--target", os.path.join(work, "target-optimized"), package],
                                             work, dict(os.environ, PYTHONOPTIMIZE="1"))
    # Only what the platform needs to start a process; nothing of Angelo's or Python's.
    replaced = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP")
                if key in os.environ}
    optimizing["-O, replaced environment"] = run([sys.executable, "-O", "-c", report], work, replaced)
    result["optimize_children"] = optimizing
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        # Where the worker would keep a module's bytecode: below the same folder, never next to the source.
        result["multiprocessing_child"] = {"platform": pool.apply(platform.platform),
                                           "bytecode_for": pool.apply(importlib.util.cache_from_source,
                                                                      ("/angelo-probe/module.py",))}
    python = os.path.dirname(os.path.dirname(os.path.realpath(sys.executable)))
    result["bytecode"] = bytecode([sysconfig.get_path("stdlib"), os.path.join(os.path.dirname(python), "scripts")])
    print("ANGELO_SEAL_PROBE " + json.dumps(result), flush=True)


# A spawned worker runs this file again as __mp_main__ (it has a __file__): only the launch itself probes.
if __name__ == "__main__":
    probe()
