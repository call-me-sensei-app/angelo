# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long export_snapshot.take holds Blender's main thread on Windows (doc/angelo/engine_preflight.rst).

    python -B bench_export_snapshot.py OUT.json [DIR] [--module PATH]

export_snapshot.take(source, target) runs on the main thread right after a web export (web_readback._start) and after
an engine link's write-back (engine_preflight_link.request). macOS clones the file (clonefile: no data copied);
Windows has no such call in it, so it copies with shutil.copyfile. This times, in DIR (default: %TEMP%), for files
just written as an exporter writes them (so their pages are in the cache, as after a real export):

* ``take``: export_snapshot.take itself, from this tree (or ``--module``: another export_snapshot.py, as main's);
* ``copyfile``: shutil.copyfile alone; ``copyfile2``: _winapi.CopyFile2 (the system's own copy, which also clones
  on ReFS / Dev Drive); ``link``: os.link (a hard link: no data copied, but it shares later in-place writes).

Sizes 1, 10, 50, 200 and 500 MB of random bytes, REPS each, in ABAB order across methods. Standard library only.
"""

import ctypes
import importlib.util
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time

SIZES_MB = (1, 10, 50, 200, 500)
REPS = 5
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts", "addons_core",
                      "angelo_agent", "export_snapshot.py")


def load_take(path):
    spec = importlib.util.spec_from_file_location("export_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.take, os.path.abspath(path)


def volume(path):
    """File system name and drive type of the volume holding ``path``."""
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    name = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, name, 64)
    kinds = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
    return {"root": root, "file_system": name.value if ok else "unknown",
            "drive_type": kinds.get(ctypes.windll.kernel32.GetDriveTypeW(root), "unknown")}


def write(path, megabytes):
    chunk = os.urandom(1 << 20)
    with open(path, "wb") as f:
        for _ in range(megabytes):
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())


def main(argv):
    module = SOURCE
    if "--module" in argv:
        index = argv.index("--module")
        module = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]
    out = argv[0]
    base = argv[1] if len(argv) > 1 else tempfile.gettempdir()
    work = tempfile.mkdtemp(prefix="angelo-snapshot-bench-", dir=base)
    take, take_source = load_take(module)
    import _winapi
    methods = {"take": lambda s, t: take(s, t), "copyfile": shutil.copyfile,
               "link": os.link}
    if hasattr(_winapi, "CopyFile2"):
        methods["copyfile2"] = lambda s, t: _winapi.CopyFile2(s, t, 0)
    report = {"python": sys.version, "platform": platform.platform(), "cpu_count": os.cpu_count(),
              "volume": volume(work), "take_from": take_source, "reps": REPS, "cases": []}
    try:
        for megabytes in SIZES_MB:
            source = os.path.join(work, "export-%d.glb" % megabytes)
            times = {name: [] for name in methods}
            for rep in range(REPS):
                write(source, megabytes)   # As an export leaves it: just written, in the cache.
                order = list(methods) if rep % 2 == 0 else list(reversed(list(methods)))
                for name in order:
                    target = os.path.join(work, "copy-%s-%d" % (name, rep))
                    started = time.perf_counter()
                    methods[name](source, target)
                    times[name].append((time.perf_counter() - started) * 1000.0)
                    os.remove(target)
            case = {"megabytes": megabytes}
            for name, values in times.items():
                case[name] = {"median_ms": round(statistics.median(values), 2), "min_ms": round(min(values), 2),
                              "max_ms": round(max(values), 2), "runs_ms": [round(v, 2) for v in values]}
            report["cases"].append(case)
            print(json.dumps(case), flush=True)
            os.remove(source)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
