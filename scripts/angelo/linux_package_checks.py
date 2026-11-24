# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux package's release checks (doc/angelo/releasing.rst, "Linux package")::

    python3 scripts/angelo/linux_package.py check <Angelo-<v>-linux-x64.tar.xz or its unpacked top folder>
        [--allow-unsigned] [--allow-no-update-key] [--launch] [--report FILE]

Each check holds for any release, read from the files themselves:

- **Layout:** one top folder named after the archive; the ``angelo`` launcher, ``blender``, the desktop file whose
  ``Icon`` is a shipped icon, and ``lib/``.
- **glibc floor:** every ELF file is x86-64 and needs no ``GLIBC_`` version newer than ``GLIBC_FLOOR`` (Blender's
  Rocky Linux 8 build environment, the floor the downloads page states) and no ``GLIBC_PRIVATE`` (``elf_info.py``), nor a
  ``GLIBCXX_``/``CXXABI_`` version newer than Rocky 8's system ``libstdc++`` unless the package bundles its own.
- **Links:** every symbolic link points inside the package.
- **Search paths:** every ``DT_RPATH``/``DT_RUNPATH`` entry starts with ``$ORIGIN`` (an absolute one names the
  builder's machine and makes the loader look there on every user's machine).
- **Bytecode:** only the build's hash-based bytecode ships (``release_checks.is_build_bytecode``).
- **Keys, licences, runtime:** ``update_keys.json`` holds an active key; the licence texts and source offer; the
  pinned agent runtime with its release marker (``agent_runtime.json``, platform ``linux``).
- **Manifest:** the release's file manifest is present and signed, and every file matches it (the updater's check).
- **Builder paths:** nothing names the builder's machine or carries a credential (``builder_paths.py``; the build
  script runs it with the build's own identity paths too).
- ``--launch`` (Linux hosts): ``angelo --background --factory-startup`` starts and exits cleanly.

Whether every ``DT_NEEDED`` resolves on clean distributions is ``linux_ldd_containers.sh``'s job (it needs
containers). Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elf_info  # noqa: E402  (lane C's reader; one ELF reader for every Linux check)
import linux_package  # noqa: E402
import release_checks  # noqa: E402

# Blender's Linux release environment is Rocky Linux 8 (build_files/build_environment/linux/linux_rocky8_setup.sh):
# glibc 2.28, and GCC 8's libstdc++ as the system C++ library (gcc-toolset links newer parts statically).
GLIBC_FLOOR = "2.28"
GLIBCXX_FLOOR = "3.4.25"
CXXABI_FLOOR = "1.3.11"
LICENCE_FILES = ("copyright.txt", "source_offer.txt", "license/license.md", "license/spdx/GPL-3.0-or-later.txt")


def unpack(tarball, into):
    """Unpack ``tarball`` into the folder ``into``; returns the one top folder. Refuses links or names that leave it."""
    into = Path(into)
    with tarfile.open(tarball, "r:xz") as archive:
        members = archive.getmembers()
        tops = {member.name.split("/", 1)[0] for member in members}
        expected = Path(tarball).name[:-len(".tar.xz")]
        if tops != {expected}:
            raise linux_package.PackageError("%s holds the top entries %s; a package holds exactly %s/"
                                             % (Path(tarball).name, ", ".join(sorted(tops)), expected))
        archive.extractall(into, filter="data")
    return into / expected


def layout_problems(top):
    top = Path(top)
    problems = linux_package.install_problems(top)
    desktop = top / linux_package.DESKTOP_FILE
    if desktop.is_file():
        lines = desktop.read_text(encoding="utf-8").splitlines()
        icon = next((line.split("=", 1)[1] for line in lines if line.startswith("Icon=")), "")
        if not icon or not (top / (icon + ".svg")).is_file():
            problems.append("%s names Icon=%s, which the package does not ship as %s.svg" % (desktop.name, icon, icon))
        if not any(line.startswith("Exec=") for line in lines):
            problems.append("%s has no Exec line" % desktop.name)
    launcher = top / linux_package.LAUNCHER
    if launcher.is_file() and not launcher.read_bytes().startswith(b"#!/bin/sh"):
        problems.append("%s is not a POSIX shell script" % launcher.name)
    return problems


def is_elf(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_files(top):
    for folder, _dirs, files in os.walk(top):
        for name in sorted(files):
            path = Path(folder) / name
            if not path.is_symlink() and is_elf(path):
                yield path


def _newer(versions, prefix, floor):
    """The newest of ``versions`` named ``<prefix><x.y...>`` when it is newer than ``floor`` (else None)."""
    found = [name[len(prefix):] for name in versions if name.startswith(prefix) and name[len(prefix):][:1].isdigit()]
    key = lambda text: tuple(int(part) if part.isdigit() else 0 for part in text.split("."))   # noqa: E731
    newest = max(found, key=key) if found else None
    return newest if newest and key(newest) > key(floor) else None


def elf_problems(top):
    """The glibc floor, the C++ library floor and the search paths of every ELF file under ``top``."""
    top = Path(top)
    bundled = {path.name for path in top.rglob("*.so*")}
    problems = []
    for path in elf_files(top):
        rel = path.relative_to(top).as_posix()
        try:
            info = elf_info.read(path)
        except (OSError, ValueError) as error:   # struct.error is a ValueError
            problems.append("%s cannot be read as ELF: %s" % (rel, error))
            continue
        if info is None:
            problems.append("%s is not a 64-bit little-endian ELF file" % rel)
            continue
        if info.machine != "x86_64":
            problems.append("%s is built for %s, not x86_64" % (rel, info.machine))
        problems += elf_info.glibc_problems(info, GLIBC_FLOOR, rel)
        for entry in info.runpaths:
            if not entry.startswith(elf_info.ORIGIN):   # $ORIGIN or ${ORIGIN}, as the loader reads both
                problems.append("%s searches %s (RPATH/RUNPATH entries must start with $ORIGIN)" % (rel, entry))
        # GLIBCXX_/CXXABI_ come from the system libstdc++ unless the package bundles its own.
        cxx = {name for library, name in info.versions if library == "libstdc++.so.6" and library not in bundled}
        for prefix, floor in (("GLIBCXX_", GLIBCXX_FLOOR), ("CXXABI_", CXXABI_FLOOR)):
            newer = _newer(cxx, prefix, floor)
            if newer:
                problems.append("%s needs %s%s from libstdc++.so.6, newer than the %s%s floor (build in Blender's "
                                "Rocky Linux 8 environment)" % (rel, prefix, newer, prefix, floor))
    return sorted(set(problems))


def link_problems(top):
    """Symbolic links must stay inside the package: an absolute or escaping target names another machine."""
    top = Path(top).resolve()
    problems = []
    for folder, dirs, files in os.walk(top):
        for name in dirs + files:
            path = Path(folder) / name
            if path.is_symlink():
                target = os.readlink(path)
                resolved = (path.parent / target).resolve()
                if os.path.isabs(target) or (top not in resolved.parents and resolved != top):
                    problems.append("%s links to %s, outside the package" % (path.relative_to(top).as_posix(), target))
    return problems


def bytecode_problems(top):
    found = []
    for folder, _dirs, files in os.walk(top):
        for name in files:
            if name.endswith((".pyc", ".pyo")) and not release_checks.is_build_bytecode(os.path.join(folder, name)):
                found.append(Path(folder, name).relative_to(top).as_posix())
    return ["bytecode written at run time ships: %s" % rel for rel in sorted(found)]


def licence_problems(top):
    return ["%s is missing (the licences and the GPL source offer ship with every release)" % rel
            for rel in LICENCE_FILES if not (Path(top) / rel).is_file()]


def runtime_problems(top, runner=None):
    pin = release_checks.agent_runtime_pin()
    if "linux" not in pin["platforms"]:
        return ["agent_runtime.json has no linux platform: the Linux build cannot bundle the agent runtime"]
    problems = release_checks.agent_runtime_problems(release_checks.agent_runtime_folder(top, "linux"), "linux",
                                                     runner, exclusive=True)
    return problems + release_checks.release_marker_problems(top, "linux")


def manifest_problems(top, allow_unsigned=False, verify=None, keys=None):
    """The signed manifest: present and verifying, every file matching it (``sign_linux_release.py check-tree``)."""
    verify = verify or (lambda tree: linux_package.verify_tree(tree, keys))
    if allow_unsigned and not (Path(top) / linux_package.MANIFEST_NAME).is_file():
        return []
    return linux_package.signature_problems(top, verify)


def tree_state(top):
    """Every entry under ``top`` by what the signed manifest holds of it: a file's bytes (SHA-256) and executable bit, a
    link's target, a folder. Contents, never times: a same-size rewrite inside one clock tick still differs."""
    import hashlib
    state = {}
    for folder, dirs, files in os.walk(top):
        for name in dirs + files:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, top)
            if os.path.islink(path):
                state[rel] = ("link", os.readlink(path))
            elif os.path.isdir(path):
                state[rel] = ("folder",)
            else:
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(block)
                state[rel] = ("file", digest.hexdigest(), os.access(path, os.X_OK))
    return state


def changed_entries(before, after):
    """What a run added, removed or changed inside the install (the signed manifest must still match it, or the
    updater treats the install as modified and stops updating it)."""
    return (["added: %s" % rel for rel in sorted(set(after) - set(before))]
            + ["removed: %s" % rel for rel in sorted(set(before) - set(after))]
            + ["changed: %s" % rel for rel in sorted(set(before) & set(after)) if before[rel] != after[rel]])


def launch_problems(top, home=None):
    """``angelo --background --factory-startup`` starts and exits cleanly with a fresh home folder, and writes
    nothing inside the install (doc/angelo/releasing.rst, "Nothing is written inside the app")."""
    before = tree_state(top)
    with tempfile.TemporaryDirectory(prefix="angelo-launch-home-") as scratch:
        env = dict(os.environ, HOME=home or scratch, XDG_CONFIG_HOME=os.path.join(home or scratch, ".config"),
                   XDG_CACHE_HOME=os.path.join(home or scratch, ".cache"))
        try:
            result = subprocess.run([str(Path(top) / linux_package.LAUNCHER), "--background", "--factory-startup",
                                     "--python-expr", "import sys; sys.exit(0)"],
                                    capture_output=True, text=True, timeout=300, env=env)
        except (OSError, subprocess.TimeoutExpired) as error:
            return ["angelo --background did not finish: %s" % error]
    problems = []
    if result.returncode != 0:
        problems.append("angelo --background --factory-startup exited %d: %s"
                        % (result.returncode, result.stderr[-2000:]))
    problems += ["the launch wrote inside the install, %s" % entry for entry in changed_entries(before, tree_state(top))]
    return problems


def builder_path_problems(top, report=None, upstream_root=None):
    """``builder_paths.py`` on the unpacked package; ``upstream_root``: the pinned library tree (LIBDIR), so upstream's
    own build paths are excused only in files byte-identical to it."""
    import builder_paths  # noqa: E402  (beside this file)
    argv = [str(top)] + (["--report", str(report)] if report else [])
    argv += ["--upstream-root", str(upstream_root)] if upstream_root else []
    return [] if builder_paths.main(argv) == 0 else ["builder paths or credentials ship (builder_paths.py above)"]


def check(top, allow_unsigned=False, allow_no_update_key=False, launch=False, runner=None, report=None, keys=None,
          upstream_root=None):
    """Every problem with the unpacked package ``top``, by check name."""
    results = {
        "layout": layout_problems(top),
        "elf": elf_problems(top),
        "links": link_problems(top),
        "bytecode": bytecode_problems(top),
        "licences": licence_problems(top),
        "update_keys": release_checks.update_key_problems(top, allow_none=allow_no_update_key),
        "agent_runtime": runtime_problems(top, runner),
        "manifest": manifest_problems(top, allow_unsigned, keys=keys),
        "builder_paths": builder_path_problems(top, report, upstream_root),
    }
    if launch:
        results["launch"] = launch_problems(top)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="linux_package.py check", description=__doc__.split("\n\n")[0])
    parser.add_argument("package", help="the .tar.xz, or its unpacked top folder")
    parser.add_argument("--allow-unsigned", action="store_true", help="a development package")
    parser.add_argument("--allow-no-update-key", action="store_true", help="a rehearsal")
    parser.add_argument("--launch", action="store_true", help="start the app in the background (Linux hosts)")
    parser.add_argument("--no-run", action="store_true", help="do not run the agent runtime's binaries")
    parser.add_argument("--report", help="write the results as JSON")
    parser.add_argument("--keys", help="a rehearsal's copy of linux_release_keys.json holding its own key")
    parser.add_argument("--upstream-root", help="the pinned lib/linux_x64 the build used (builder-paths' upstream check)")
    arguments = parser.parse_args(argv)
    package = Path(arguments.package)
    with tempfile.TemporaryDirectory(prefix="angelo-linux-check-") as scratch:
        try:
            top = unpack(package, scratch) if package.is_file() else package
        except (linux_package.PackageError, tarfile.TarError, OSError) as error:
            print("linux package: %s" % error, file=sys.stderr)
            return 1
        results = check(top, arguments.allow_unsigned, arguments.allow_no_update_key, arguments.launch,
                        None if arguments.no_run else release_checks._run, keys=arguments.keys,
                        upstream_root=arguments.upstream_root)
    failed = 0
    for name, problems in results.items():
        for problem in problems:
            print("linux package %s: %s" % (name, problem), file=sys.stderr)
        print("linux package %s: %s" % (name, "FAIL (%d)" % len(problems) if problems else "ok"))
        failed += bool(problems)
    if arguments.report:
        Path(arguments.report).write_text(json.dumps({"package": package.name, "results": results}, indent=2) + "\n",
                                          encoding="utf-8")
    return 1 if failed else 0
