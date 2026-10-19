# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual macOS checks of the chat journal: a full disk, and coarse-timestamp file systems.

    python disk_images.py <angelo checkout> <scratch folder>

Makes small disk images with ``hdiutil`` in the scratch folder (user-level mounts, no admin rights), runs the
checks and detaches them.

* Full disk (HFS+, APFS): fills the volume, then saves until ENOSPC. Each failed save must leave the file
  unchanged, no temporary file and the writer reset; after space is freed the next save rewrites and the chat
  reloads exactly. Expected: both as stated.
* Coarse timestamps (FAT32, exFAT): those volumes report the change time equal to the modification time, so a
  same-size in-place edit with its time put back leaves every lstat field the probe cache compares unchanged
  (``checkpoint_inventory_cache``: "the change time cannot be set back by a program" holds on APFS only).
  On macOS this is not reachable for snapshots: setting the time writes an AppleDouble ``._`` file, and
  snapshot storage on those volumes already fails closed on the ``._`` files.
"""

import errno
import json
import os
import subprocess
import sys
import time
import uuid

checkout, scratch = sys.argv[1], os.path.abspath(sys.argv[2])
sys.path.insert(0, checkout + "/scripts/addons_core")
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402


def mounted(fs, size):
    tag = fs.replace("+", "").replace(" ", "").replace("-", "").lower()
    image, mount = os.path.join(scratch, tag + ".dmg"), os.path.join(scratch, "mnt-" + tag)
    if os.path.exists(image):
        os.unlink(image)
    subprocess.run(["hdiutil", "create", "-size", size, "-fs", fs, "-volname", "AJR" + tag[:6], "-layout", "NONE",
                    image], check=True, capture_output=True)
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", mount, image], check=True, capture_output=True)
    return mount


def detach(mount):
    subprocess.run(["hdiutil", "detach", mount], capture_output=True)


def full_disk(mount):
    folder = os.path.join(mount, "profile")
    store = SessionStore(folder, str(uuid.uuid4()))
    state = store.new("claude_subscription")
    state["messages"].extend({"role": "assistant", "text": "x" * 2000} for _ in range(50))
    store.save(state)
    with open(os.path.join(mount, "filler"), "wb") as handle:
        try:
            while True:
                handle.write(b"\0" * 4096)
                handle.flush()
        except OSError:
            pass
    outcomes = []
    for n in range(1, 400):
        before = open(store.path, "rb").read()
        reserve_invocation(state, "%s/c%d" % (state["id"], n), "%064x" % n, "doc")
        state["messages"].append({"role": "assistant", "text": "edit %d " % n + "y" * 1500})
        try:
            store.save(state)
        except OSError as error:
            leftovers = [name for name in os.listdir(os.path.dirname(store.path)) if name.endswith(".tmp")]
            outcomes.append((errno.errorcode.get(error.errno), open(store.path, "rb").read() == before,
                             not leftovers, not store._writer.ready))
            if len(outcomes) == 3:
                break
    os.unlink(os.path.join(mount, "filler"))
    store.save(state)
    loaded = SessionStore(folder, state["id"]).load()
    exact = loaded["messages"] == json.loads(json.dumps(state["messages"])) and not loaded["invocations"]["closed"]
    print("  failed saves (errno, file unchanged, no temporary file, writer reset):", outcomes)
    print("  after freeing space: %s; reload exact: %s" % (store.last_write, exact))


def coarse(mount):
    def state(path):
        info = os.lstat(path)
        return info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns
    path = os.path.join(mount, "checkpoint.json")
    with open(path, "wb") as handle:
        handle.write(b'{"protected": false}')
    before = state(path)
    time.sleep(2.2)
    with open(path, "r+b") as handle:
        handle.write(b'{"protected": true }')
    os.utime(path, ns=(before[5], before[5]))
    print("  same-size edit, time put back: every compared field unchanged:", state(path) == before,
          "| change time equals modification time:", before[5] == before[6])


for fs, check, size in (("HFS+", full_disk, "3m"), ("APFS", full_disk, "3m"),
                        ("MS-DOS FAT32", coarse, "40m"), ("ExFAT", coarse, "40m")):
    mount = mounted(fs, size)
    try:
        print(fs)
        check(mount)
    finally:
        detach(mount)
