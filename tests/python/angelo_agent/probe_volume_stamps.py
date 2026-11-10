# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How Windows stamps file times on NTFS, FAT32 and exFAT: the facts ``angelo_change_clock``'s volume steps rest on.

``angelo_change_clock`` judges a stat's times against its volume's step (2 s on FAT32 and exFAT, none on NTFS),
named from the file system, and a change time beyond ``now + max(TICK, step)`` fails closed. That rests on facts
nothing in the documentation pins down, so this measures them on a real Windows host (its first run, 2026-11-04,
is what ``angelo_change_clock``'s docstring states):

* whether ChangeTime is 0, equal to LastWriteTime, or its own value on each file system;
* the step each time is kept in (every value a multiple of 2 s, 1 s, 10 ms, 1 ms?) and its rounding direction against
  ``GetSystemTimeAsFileTime`` read just before the write and just after (below: rounded down; above: rounded up or
  ahead of the clock), with the largest distance either way;
* whether any stamp is ever ahead of the clock read right after it was observed;
* whether LastWriteTime moves when the handle closes (whether a file system stamps it only at close);
* whether CPython's ``st_ctime_ns`` (``os.fstat``) is the ChangeTime, and ``st_dev``'s low 32 bits the serial
  ``GetVolumeInformationByHandleW`` reports (what ``volume_step`` maps), and what ``volume_step`` answers.

It creates two small expandable VHDX disks with diskpart (an administrator; the held VM runs as LocalSystem), formats
one FAT32 and one exFAT, mounts each at an empty NTFS folder (any drive letter automount gave the new volume is
removed; automount itself is left as it is), and writes ``--writes`` files on each and in an NTFS folder of the work
directory. The disks are detached and deleted in a ``finally`` block, and ``--cleanup`` (which the wrapper always
runs last) detaches and deletes any a killed run left, from the manifest (its owner's PID and creation time, written
atomically before each disk is made); it leaves a running probe's folder alone. ``--max-seconds`` caps the run: no
write starts after it and every diskpart call has a timeout inside it. Standard library and ``angelo_change_clock``
only; it never touches Angelo's own state.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))
import angelo_change_clock as change_clock  # noqa: E402

EPOCH = 116444736000000000
STEPS = {"2s": 20_000_000, "1s": 10_000_000, "10ms": 100_000, "1ms": 10_000}   # FILETIME units.
MANIFEST = "disks.json"


def kernel32():
    from ctypes import wintypes
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    library.GetFileInformationByHandleEx.restype = wintypes.BOOL
    library.GetVolumeInformationByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    library.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    return library


def basic_info(api, fd):
    """FILE_BASIC_INFO: CreationTime, LastAccessTime, LastWriteTime, ChangeTime (FILETIME steps)."""
    import msvcrt
    import struct
    buffer = ctypes.create_string_buffer(40)
    if not api.GetFileInformationByHandleEx(msvcrt.get_osfhandle(fd), 0, buffer, 40):
        raise ctypes.WinError(ctypes.get_last_error())
    creation, access, write, change = struct.unpack_from("<qqqq", buffer, 0)
    return {"creation": creation, "access": access, "write": write, "change": change}


def volume_facts(api, fd):
    import msvcrt
    from ctypes import wintypes
    serial, name = wintypes.DWORD(), ctypes.create_unicode_buffer(64)
    if not api.GetVolumeInformationByHandleW(msvcrt.get_osfhandle(fd), None, 0, ctypes.byref(serial), None, None,
                                             name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"file_system": name.value, "serial": serial.value}


def diskpart(lines, timeout):
    """Run a diskpart script; returns (exit code, output)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as script:
        script.write("\n".join(lines) + "\n")
    try:
        done = subprocess.run(["diskpart.exe", "/s", script.name], capture_output=True, text=True,
                              errors="replace", timeout=max(5.0, timeout))
        return done.returncode, done.stdout[-3000:]
    except subprocess.TimeoutExpired:
        return 124, "diskpart timed out"
    finally:
        os.unlink(script.name)


def process_created(handle):
    """A process's creation time (FILETIME steps) from its handle, or None."""
    from ctypes import wintypes
    times = [wintypes.FILETIME() for _ in range(4)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    if not api.GetProcessTimes(wintypes.HANDLE(handle), *(ctypes.byref(value) for value in times)):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def owner_now():
    """This process as a manifest records its owner: its PID and creation time (a reused PID has another)."""
    return {"pid": os.getpid(), "created": process_created(ctypes.WinDLL("kernel32").GetCurrentProcess())}


def owner_alive(owner):
    """Whether the probe that wrote a manifest still runs; unknown (access refused) counts as running."""
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = wintypes.HANDLE
    handle = api.OpenProcess(0x1000, False, int(owner["pid"]))   # PROCESS_QUERY_LIMITED_INFORMATION.
    if not handle:
        return ctypes.get_last_error() != 87   # ERROR_INVALID_PARAMETER: no such process.
    try:
        code = wintypes.DWORD()
        if process_created(handle) != owner.get("created"):
            return False   # The PID now names another process.
        return not api.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code)) or code.value == 259
    finally:
        api.CloseHandle(wintypes.HANDLE(handle))


def write_manifest(work, manifest):
    """The manifest, atomically: a temporary file in the same folder, then a replace (never half written)."""
    temporary = work / (MANIFEST + "." + uuid.uuid4().hex[:8] + ".tmp")
    temporary.write_text(json.dumps(manifest), encoding="utf-8")
    os.replace(temporary, work / MANIFEST)


def make_disk(work, kind, size_mb, deadline, manifest):
    vhdx, mount = work / ("probe-%s.vhdx" % kind), work / ("mount-%s" % kind)
    mount.mkdir()
    manifest["disks"].append({"vhdx": str(vhdx), "mount": str(mount)})
    write_manifest(work, manifest)   # Before the disk exists: --cleanup finds it.
    # ``remove all`` drops any drive letter automount gave the new volume (automount itself is left as it is), so the
    # volume is reachable only at the probe's folder.
    code, output = diskpart([
        'create vdisk file="%s" maximum=%d type=expandable' % (vhdx, size_mb), 'select vdisk file="%s"' % vhdx,
        "attach vdisk", "create partition primary", "format fs=%s quick label=ANGELO%s" % (kind, kind[:3].upper()),
        "remove all noerr", 'assign mount="%s"' % mount], deadline - time.time())
    if code != 0 or not os.path.ismount(mount):
        raise RuntimeError("diskpart could not make the %s disk (exit %d): %s" % (kind, code, output))
    return mount, output


def remove_disks(disks, timeout=60):
    failures = []
    for disk in disks:
        vhdx, mount = Path(disk["vhdx"]), Path(disk["mount"])
        if vhdx.exists():
            # Detaching removes the volume; its mount folder is then an empty folder to remove.
            code, output = diskpart(['select vdisk file="%s"' % vhdx, "detach vdisk noerr"], timeout)
            try:
                vhdx.unlink()
            except OSError as error:
                failures.append("%s: %s (diskpart exit %d: %s)" % (vhdx, error, code, output[-300:]))
        try:
            if mount.exists():
                mount.rmdir()
        except OSError as error:
            failures.append("%s: %s" % (mount, error))
    return failures


def step_of(values):
    """The coarsest step every value is a multiple of, or "100ns"."""
    for name, step in STEPS.items():
        if values and all(value % step == 0 for value in values if value):
            return name
    return "100ns"


def measure(api, folder, writes, deadline):
    samples, facts = [], None
    for index in range(writes):
        if time.time() >= deadline:
            break
        if index % 17 == 0:
            time.sleep((index % 31) / 1000)   # Vary the phase against the clock's tick.
        path = folder / ("probe-%d.bin" % index)
        before = change_clock.system_time()
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))
        try:
            os.write(fd, b"x" * (1 + index % 7))
            open_info, open_stat = basic_info(api, fd), os.fstat(fd)
            seen_open = change_clock.system_time()
            if facts is None:
                facts = volume_facts(api, fd)
                facts.update(st_dev=open_stat.st_dev, volume_step=change_clock.volume_step(open_stat.st_dev),
                             low_32_bits_match=(open_stat.st_dev & 0xFFFFFFFF) == facts["serial"])
            time.sleep(0.02)
        finally:
            os.close(fd)
        closed = change_clock.system_time()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            closed_info, closed_stat = basic_info(api, fd), os.fstat(fd)
            seen_closed = change_clock.system_time()
        finally:
            os.close(fd)
        samples.append({"before": before, "seen_open": seen_open, "closed": closed, "seen_closed": seen_closed,
                        "open": open_info, "closed_info": closed_info,
                        "st_ctime_ft": open_stat.st_ctime_ns // 100 + EPOCH,
                        "st_mtime_ft": closed_stat.st_mtime_ns // 100 + EPOCH})
        try:
            path.unlink()
        except OSError:
            pass
    return facts, samples


def summarize(facts, samples):
    if not samples:
        return {"facts": facts, "writes": 0}
    out = {"facts": facts, "writes": len(samples)}
    for field in ("creation", "write", "change"):
        values = [sample["closed_info"][field] for sample in samples]
        below = [sample["before"] - sample["closed_info"][field] for sample in samples
                 if sample["closed_info"][field] < sample["before"]]
        above = [sample["closed_info"][field] - sample["closed"] for sample in samples
                 if sample["closed_info"][field] > sample["closed"]]
        ahead = [sample["closed_info"][field] - sample["seen_closed"] for sample in samples
                 if sample["closed_info"][field] > sample["seen_closed"]]
        out[field] = {"zero": sum(1 for value in values if value == 0), "step": step_of(values),
                      "below_clock_before": len(below), "max_below_ms": max(below, default=0) / 1e4,
                      "above_clock_after": len(above), "max_above_ms": max(above, default=0) / 1e4,
                      "ahead_of_clock_when_seen": len(ahead), "max_ahead_ms": max(ahead, default=0) / 1e4}
    changes = [(sample["closed_info"]["change"], sample["closed_info"]["write"]) for sample in samples]
    out["change_vs_write"] = {"zero": sum(1 for change, _write in changes if change == 0),
                              "equal": sum(1 for change, write in changes if change and change == write),
                              "own": sum(1 for change, write in changes if change and change != write)}
    out["write_moved_at_close"] = sum(1 for sample in samples
                                      if sample["closed_info"]["write"] != sample["open"]["write"])
    out["st_ctime_is_change_time"] = sum(1 for sample in samples if sample["st_ctime_ft"] == sample["open"]["change"])
    out["st_mtime_is_write_time"] = sum(1 for sample in samples
                                        if sample["st_mtime_ft"] == sample["closed_info"]["write"])
    return out


def summary_text(report):
    lines = ["Angelo volume stamp probe: %s, Python %s, %s writes per volume" % (
        report["platform"], report["python"], report["settings"]["writes"])]
    listing = report.get("volume_listing")
    if listing:
        lines.append("volume listing: %.3f s, done %s, {serial: (step, when)} %s" % (
            listing["s"], listing["done"], listing["steps"]))
    for name, volume in report["volumes"].items():
        if "error" in volume:
            lines.append("%-6s ERROR %s" % (name, volume["error"]))
            continue
        facts = volume.get("facts") or {}
        lines.append("%-6s %s serial %s, st_dev low bits match %s, volume_step %s, %d writes" % (
            name, facts.get("file_system"), facts.get("serial"), facts.get("low_32_bits_match"),
            facts.get("volume_step"), volume["writes"]))
        if not volume["writes"]:
            continue
        lines.append("       ChangeTime vs LastWriteTime: %s; LastWriteTime moved at close: %d; st_ctime is "
                     "ChangeTime: %d; st_mtime is LastWriteTime: %d" % (
                         volume["change_vs_write"], volume["write_moved_at_close"],
                         volume["st_ctime_is_change_time"], volume["st_mtime_is_write_time"]))
        for field in ("write", "change", "creation"):
            entry = volume[field]
            lines.append("       %-8s step %-5s zero %3d | below clock %3d (max %.3f ms) | above clock %3d (max %.3f "
                         "ms) | ahead when seen %3d (max %.3f ms)" % (
                             field, entry["step"], entry["zero"], entry["below_clock_before"], entry["max_below_ms"],
                             entry["above_clock_after"], entry["max_above_ms"], entry["ahead_of_clock_when_seen"],
                             entry["max_ahead_ms"]))
    if report["cleanup_failures"]:
        lines.append("CLEANUP FAILED: %s" % "; ".join(report["cleanup_failures"]))
    if report["capped"]:
        lines.append("CAPPED at --max-seconds %d" % report["settings"]["max_seconds"])
    return "\n".join(lines)


def run(args):
    started = time.time()
    deadline = started + args.max_seconds
    base, name = Path(args.work_dir or tempfile.gettempdir()), uuid.uuid4().hex[:8]
    # Made under another name with its owner already recorded, then renamed: --cleanup never sees it without one.
    making, work = base / (".making-volume-probe-" + name), base / ("angelo-volume-probe-" + name)
    making.mkdir(parents=True)
    manifest = {"owner": owner_now(), "disks": []}
    write_manifest(making, manifest)
    os.rename(making, work)
    api, volumes, failures, volume_listing = kernel32(), {}, [], None
    try:
        ntfs = work / "ntfs"
        ntfs.mkdir()
        targets = [("ntfs", ntfs)]
        for kind in ("fat32", "exfat"):
            try:
                mount, _output = make_disk(work, kind, args.size_mb, deadline, manifest)
                targets.append((kind, mount))
            except (OSError, RuntimeError) as error:
                volumes[kind] = {"error": str(error)}
        began = time.monotonic()
        change_clock.prime()   # The background listing, with the new disks in it, answers before the writes start.
        listed = change_clock.listed(60)
        volume_listing = {"done": listed, "s": time.monotonic() - began,
                          "steps": {str(serial): value for serial, value in change_clock._volumes.items()}}
        for name, folder in targets:
            try:
                facts, samples = measure(api, folder, args.writes, deadline)
                volumes[name] = dict(summarize(facts, samples), samples=samples if args.keep_samples else None)
            except OSError as error:
                volumes[name] = {"error": "%s: %s" % (type(error).__name__, error)}
    finally:
        failures = remove_disks(manifest["disks"])
        if not failures:   # Else the folder and its manifest stay for --cleanup.
            shutil.rmtree(work, ignore_errors=True)
    report = {"schema": "angelo-volume-probe/1", "platform": sys.platform, "python": sys.version.split()[0],
              "wall_s": time.time() - started, "capped": time.time() >= deadline,
              "settings": {"writes": args.writes, "size_mb": args.size_mb, "max_seconds": args.max_seconds},
              "volumes": volumes, "volume_listing": volume_listing, "cleanup_failures": failures}
    text = summary_text(report)
    report["summary"] = text
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    out.with_suffix(".summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print("wrote %s" % out)
    ok = not failures and all("error" not in volume and volume["writes"] for volume in volumes.values())
    return 0 if ok and len(volumes) == 3 else 1


def cleanup(args):
    """Detach and delete what a killed run left: each probe work folder under the work directory whose owner (the PID
    and creation time its manifest records) no longer runs. A folder whose manifest cannot be read is left alone and
    reported when a disk is beside it; without one it is removed."""
    base, failures = Path(args.work_dir or tempfile.gettempdir()), []
    for work in sorted(base.glob("angelo-volume-probe-*")):
        try:
            manifest = json.loads((work / MANIFEST).read_text(encoding="utf-8"))
            owner, disks = manifest["owner"], list(manifest["disks"])
        except (OSError, ValueError, KeyError, TypeError):
            if list(work.glob("*.vhdx")):
                failures.append("%s: a test disk beside an unreadable manifest; left as it is" % work)
            else:
                shutil.rmtree(work, ignore_errors=True)
            continue
        if owner_alive(owner):
            print("cleanup: %s belongs to a running probe (PID %s); left as it is" % (work, owner.get("pid")))
            continue
        left = remove_disks(disks)
        if not left:
            shutil.rmtree(work, ignore_errors=True)
        failures += left
    for failure in failures:
        print("cleanup: %s" % failure)
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "volume_stamps.json"))
    parser.add_argument("--writes", type=int, default=200)
    parser.add_argument("--size-mb", type=int, default=256, help="Each disk's size (FAT32 needs more than 32 MB)")
    parser.add_argument("--max-seconds", type=int, default=600)
    parser.add_argument("--work-dir", default="", help="Where the disks and folders go (an NTFS folder)")
    parser.add_argument("--keep-samples", action="store_true", help="Write every sample into the JSON")
    parser.add_argument("--cleanup", action="store_true", help="Only detach and delete what a killed run left")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("refused: this probes Windows file systems", file=sys.stderr)
        return 2
    return cleanup(args) if args.cleanup else run(args)


if __name__ == "__main__":
    sys.exit(main())
