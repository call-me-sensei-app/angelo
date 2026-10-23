# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Install the pinned pure-Python wheels over the bundled library's copies, inside the installation.

The bundled Python's packages come from the pinned precompiled libraries, which are rebuilt rarely; a package
whose staleness is a security problem (certifi, the root store Angelo's TLS uses, and pip, which vendors its
own copy) must not wait for that. ``build_files/cmake/angelo_python_packages.json`` pins each one (version,
PyPI URL, SHA-256); the install step (``build_files/cmake/angelo_python_packages.cmake``) runs this file with the
installed interpreter, before the bytecode step::

    <installed python> -I -B angelo_python_packages.py <pins json> <wheel folder>

Each wheel must match its pinned SHA-256, and every file in it the hash its RECORD lists. The installed copy
of the same project (its ``*.dist-info`` folders, matched by normalized name) and the wheel's top-level folders
are removed first, so no file of the old version survives; then the wheel is unpacked into ``purelib``. A
project already installed at the pinned version with every file matching its RECORD is left alone (incremental
installs). Only pure ``purelib`` wheels are accepted: one with a ``.data`` folder (scripts, headers) or a path
leaving the folder is refused. Standard library only.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import sys
import sysconfig
import zipfile


def normalized(name):
    return re.sub(r"[-_.]+", "_", name).lower()


def wheel_path(folder, package):
    return os.path.join(folder, package["url"].rsplit("/", 1)[-1])


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _safe(name):
    parts = name.split("/")
    return name and not name.startswith("/") and "\\" not in name and ".." not in parts and ":" not in parts[0]


def wheel_members(path, package):
    """``(dist-info folder, {relative path: bytes})`` of a verified wheel, or raises ``ValueError``."""
    if sha256_file(path) != package["sha256"]:
        raise ValueError("%s does not match its pinned SHA-256" % os.path.basename(path))
    with zipfile.ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        records = [name for name in names if re.fullmatch(r"[^/]+\.dist-info/RECORD", name)]
        if len(records) != 1:
            raise ValueError("%s has %d RECORD files" % (os.path.basename(path), len(records)))
        dist_info = records[0].split("/", 1)[0]
        if normalized(dist_info[:-len(".dist-info")].rsplit("-", 1)[0]) != normalized(package["name"]) or \
                dist_info[:-len(".dist-info")].rsplit("-", 1)[1] != package["version"]:
            raise ValueError("%s is not %s %s" % (dist_info, package["name"], package["version"]))
        listed = {}
        for line in archive.read(records[0]).decode("utf-8").splitlines():
            if line.strip():
                name, digest = line.rsplit(",", 2)[:2]
                listed[name] = digest
        members = {}
        for name in names:
            if not _safe(name) or name.split("/", 1)[0].endswith(".data"):
                raise ValueError("%s holds %s, which is not a plain purelib file" % (os.path.basename(path), name))
            data = archive.read(name)
            if name != records[0] and listed.get(name) != record_hash(data):
                raise ValueError("%s: %s does not match its RECORD" % (os.path.basename(path), name))
            members[name] = data
    missing = sorted(set(listed) - set(members))
    if missing:
        raise ValueError("%s lists files it lacks: %s" % (os.path.basename(path), ", ".join(missing[:5])))
    return dist_info, members


def installed_dist_infos(site, name):
    wanted = normalized(name)
    return sorted(entry for entry in os.listdir(site) if entry.endswith(".dist-info")
                  and normalized(entry[:-len(".dist-info")].rsplit("-", 1)[0]) == wanted)


def up_to_date(site, members):
    for name, data in members.items():
        target = os.path.join(site, *name.split("/"))
        try:
            with open(target, "rb") as handle:
                if handle.read() != data:
                    return False
        except OSError:
            return False
    return True


def install(site, package, members):
    """Replace ``package`` in ``site`` with ``members``; returns what was removed."""
    removed = []
    tops = {name.split("/", 1)[0] for name in members}
    for entry in sorted(set(installed_dist_infos(site, package["name"])) | {top for top in tops if "/" not in top}):
        path = os.path.join(site, entry)
        if entry in tops and not any(name.startswith(entry + "/") for name in members):
            continue   # A top-level module file is simply overwritten.
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
            removed.append(entry)
    for name, data in sorted(members.items()):
        target = os.path.join(site, *name.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    return removed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: angelo_python_packages.py <pins json> <wheel folder>", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as handle:
        packages = json.load(handle)["packages"]
    site = sysconfig.get_path("purelib")
    if not os.path.isdir(site):
        print("Angelo packages: no site-packages at %s" % site, file=sys.stderr)
        return 1
    for package in packages:
        try:
            dist_info, members = wheel_members(wheel_path(argv[1], package), package)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            print("Angelo packages: %s %s: %s" % (package["name"], package["version"], error), file=sys.stderr)
            return 1
        if installed_dist_infos(site, package["name"]) == [dist_info] and up_to_date(site, members):
            print("Angelo packages: %s %s already installed" % (package["name"], package["version"]))
            continue
        removed = install(site, package, members)
        print("Angelo packages: %s %s installed (replaced %s)" % (package["name"], package["version"],
                                                                 ", ".join(removed) or "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
