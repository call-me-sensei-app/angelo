# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pilot's MCP side carried to another machine: bundle it on the Mac, install it on the Windows VM.

The head-to-head's MCP side is stock Blender with the pilot's frozen MCP add-on
and server (its ``frozen-runtime`` folder: ``mcp_addon/``,
the server's ``mcp_server/blmcp/`` source and the dependency versions its
virtualenv held, ``mcp-import-and-versions.json``). ``bundle`` packs exactly
those files, with digests, and a requirements file pinning every dependency to
the frozen version; ``install`` (on the VM) unpacks it and builds the server's
own virtualenv with the frozen Python version through ``uv``, so the add-on and
server code are byte-identical and the dependencies version-identical (a
platform-only dependency, such as ``pywin32`` on Windows, is added by the
resolver and recorded). Standard library only; never reads a credential::

    python final_runs_mcp.py bundle --runtime FROZEN_RUNTIME --into mcp-runtime.tar.gz     # on the Mac
    python final_runs_mcp.py install --bundle mcp-runtime.tar.gz --into DIR --uv uv.exe    # on the VM
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

SERVER = "blender-mcp"
PYPROJECT = """[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "blender-mcp"
version = "%s"
requires-python = ">=3.10"

[project.scripts]
blender-mcp = "blmcp:main"

[tool.setuptools.packages.find]
where = ["."]

[tool.setuptools.package-data]
"blmcp" = ["data/prompts.yml", "data/api/**/*.rst", "data/manual/**/*.rst"]
"""


def _normal(requirement):
    """``Name==1.0`` as PEP 503 normalizes the name (``PyYAML`` and ``pyyaml``, ``a_b`` and ``a-b`` are one)."""
    name, _, version = requirement.strip().partition("==")
    return "%s==%s" % ("-".join(name.lower().replace("_", "-").replace(".", "-").split("-")), version)


def _skip(path):
    return "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == ".DS_Store"


def _files(root):
    return sorted(path for path in Path(root).rglob("*") if path.is_file() and not _skip(path.relative_to(root)))


def bundle(runtime, into):
    """``mcp-runtime.tar.gz`` from a frozen pilot runtime; returns its manifest."""
    runtime = Path(runtime)
    versions = json.loads((runtime / "mcp-import-and-versions.json").read_text())
    pins = dict(versions["dependencies"])
    server_version = pins.pop(SERVER)
    python = versions["python"].split()[0]
    manifest = {"source": str(runtime), "python": python, "server_version": server_version,
                "requirements": ["%s==%s" % pair for pair in sorted(pins.items())], "files": {}}
    members = []
    for prefix, root in (("mcp_addon", runtime / "mcp_addon"), ("mcp_server/blmcp", runtime / "mcp_server/blmcp")):
        for path in _files(root):
            name = "%s/%s" % (prefix, path.relative_to(root).as_posix())
            manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            members.append((name, path.read_bytes()))
    members += [("mcp_server/pyproject.toml", (PYPROJECT % server_version).encode()),
                ("requirements-frozen.txt", ("\n".join(manifest["requirements"]) + "\n").encode())]
    members.append(("BUNDLE.json", json.dumps(manifest, indent=2).encode()))
    # Byte for byte the same bundle from the same runtime (no time in the gzip header either): the VM installs a
    # bundle once per digest, so every send of an unchanged runtime finds the install a campaign was set up with.
    with open(into, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed, \
            tarfile.open(fileobj=packed, mode="w") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    return manifest


def uv_environment(into, environ=None):
    """uv's settings for an install every account that may read ``into`` can run.

    By default uv keeps the interpreters it downloads in the installing account's own profile, and a virtualenv
    runs on that interpreter: the Windows stage installs as SYSTEM, whose profile the bench's user cannot read, so
    the server could not start in the bench session. Here the interpreter is uv's own build of the frozen version
    (``only-managed``), kept in ``into/python``, and every package file is copied (never linked from uv's cache,
    whose file permissions a hard link would share).
    """
    return dict(os.environ if environ is None else environ, UV_PYTHON_INSTALL_DIR=str(Path(into) / "python"),
                UV_PYTHON_PREFERENCE="only-managed", UV_LINK_MODE="copy")


def venv_home(venv):
    """The folder of the interpreter a virtualenv runs on (``home`` in its ``pyvenv.cfg``)."""
    for line in (Path(venv) / "pyvenv.cfg").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return Path(value.strip())
    raise RuntimeError("%s names no base interpreter" % (Path(venv) / "pyvenv.cfg"))


def install(bundle_path, into, uv="uv", run=subprocess.run):
    """Unpack the bundle into ``into`` and build ``mcp_server/.venv``; returns the installed server's facts.

    The runtime is self-contained: its interpreter is in ``into/python`` (``uv_environment``), and an install
    whose virtualenv runs on an interpreter anywhere else is refused.
    """
    into = Path(into)
    if (into / "BUNDLE.json").exists():
        raise FileExistsError("Never install over an earlier runtime: %s" % into)
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path) as archive:
        archive.extractall(into, filter="data")
    manifest = json.loads((into / "BUNDLE.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((into / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("The bundle's %s differs from its manifest" % name)
    venv = into / "mcp_server/.venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    environment = uv_environment(into)
    run([uv, "venv", "--python", manifest["python"], str(venv)], check=True, env=environment)
    run([uv, "pip", "install", "--python", str(python), "-r", str(into / "requirements-frozen.txt")], check=True,
        env=environment)
    run([uv, "pip", "install", "--python", str(python), "--no-deps", str(into / "mcp_server")], check=True,
        env=environment)
    # ``pip list`` names every package with its version; ``pip freeze`` writes the server, installed from its
    # folder, as ``blender-mcp @ file:///...`` with no version.
    listed = json.loads(run([uv, "pip", "list", "--format", "json", "--python", str(python)], check=True,
                            capture_output=True, text=True, env=environment).stdout)
    installed = sorted(_normal("%s==%s" % (package["name"], package["version"])) for package in listed)
    pinned = {_normal(line) for line in manifest["requirements"]} | {"%s==%s" % (SERVER, manifest["server_version"])}
    interpreter = venv_home(venv)
    facts = {"server_dir": str(into / "mcp_server"), "addon_dir": str(into / "mcp_addon"), "python": manifest["python"],
             "interpreter_dir": str(interpreter), "missing": sorted(pinned - set(installed)),
             "added": sorted(set(installed) - pinned)}
    problem = ("Not installed at the frozen version: %s" % ", ".join(facts["missing"]) if facts["missing"] else
               "The server's interpreter %s is outside the runtime's own %s" % (interpreter, into / "python")
               if not interpreter.resolve().is_relative_to((into / "python").resolve()) else "")
    if problem:   # INSTALLED.json marks a finished install only: the next attempt starts again.
        (into / "INSTALL-FAILED.json").write_text(json.dumps(dict(facts, installed=installed, problem=problem),
                                                             indent=2))
        raise RuntimeError(problem)
    (into / "INSTALLED.json").write_text(json.dumps(dict(facts, installed=installed), indent=2))
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("bundle", "install"))
    parser.add_argument("--runtime", help="bundle: the pilot's frozen runtime folder")
    parser.add_argument("--bundle", help="install: mcp-runtime.tar.gz")
    parser.add_argument("--into", required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args(argv)
    result = bundle(args.runtime, args.into) if args.action == "bundle" else install(args.bundle, args.into, args.uv)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
