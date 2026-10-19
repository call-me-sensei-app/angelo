# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual macOS checks: keeping an unreadable chat on volumes without hard links, full volumes
and a read-only folder (``session_kept.preserve``; ``session_journal.preserve`` in earlier builds).

    python keep_disks.py <angelo checkout> <scratch folder>

Expected: FAT32 and exFAT keep a synced copy (same bytes); full HFS+, APFS and exFAT
images and a read-only folder keep nothing, refuse every save and leave the chat unchanged; the failed copy
leaves a 0-byte temporary on APFS and exFAT, which a later load removes once stale.
"""
import os, subprocess, sys, uuid, json, stat
checkout, scratch = sys.argv[1], os.path.abspath(sys.argv[2])
sys.path.insert(0, checkout + "/scripts/addons_core")
from angelo_agent.sessions import SessionStore
from angelo_agent.invocations import reserve_invocation

def mounted(fs, size):
    tag = fs.replace("+", "").replace(" ", "").replace("-", "").lower()
    image, mount = os.path.join(scratch, tag + ".dmg"), os.path.join(scratch, "mnt-" + tag)
    if os.path.exists(image):
        os.unlink(image)
    subprocess.run(["hdiutil", "create", "-size", size, "-fs", fs, "-volname", "AJ2" + tag[:6], "-layout", "NONE", image],
                   check=True, capture_output=True)
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", mount, image], check=True, capture_output=True)
    return mount

def make_unreadable(folder, rows=40, size=1500):
    store = SessionStore(folder, str(uuid.uuid4()))
    state = store.new("claude_subscription")
    store.save(state)
    for i in range(rows):
        state["messages"].append({"role": "user", "text": "Request %d " % i + "z" * size})
        reserve_invocation(state, state["id"] + "/c%d" % i, "%064x" % i, "doc")
        store.save(state)
    with store.path.open("ab") as h:
        h.write(b'{"seq": %d, "projects": {}}\n' % (rows + 1))
    return store.path, state["id"]

def report(label, folder, path, sid):
    original = path.read_bytes()
    store = SessionStore(folder, sid)
    loaded = store.load()
    names = sorted(os.listdir(path.parent))
    kept = [n for n in names if n.endswith(".unreadable") and not n.startswith("._")]
    temps = [(n, os.path.getsize(os.path.join(path.parent, n))) for n in names if n.endswith(".tmp")]
    try:
        store.save(loaded)
        saved = store.last_write
    except OSError as e:
        saved = "refused: %s" % e
    print("%-28s kept=%s same_bytes=%s unkept=%s temps_left=%s save=%s chat_unchanged=%s" % (
        label, kept, kept and open(os.path.join(path.parent, kept[0]), "rb").read() == original, store._unkept, temps,
        saved, path.read_bytes() == original))

# 1. FAT32 / exFAT: no hard links -> synced copy.
for fs in ("MS-DOS FAT32", "ExFAT"):
    mount = mounted(fs, "40m")
    try:
        folder = os.path.join(mount, "profile")
        path, sid = make_unreadable(folder)
        report(fs, folder, path, sid)
    finally:
        subprocess.run(["hdiutil", "detach", mount], capture_output=True)

# 2. Full volumes: fill, then load the unreadable chat.
for fs in ("HFS+", "APFS", "ExFAT"):
    mount = mounted(fs, "4m" if fs != "ExFAT" else "16m")
    try:
        folder = os.path.join(mount, "profile")
        path, sid = make_unreadable(folder, rows=60, size=4000)
        try:
            with open(os.path.join(mount, "filler"), "wb") as handle:
                while True:
                    handle.write(b"\0" * 4096); handle.flush()
        except OSError:
            pass
        report(fs + " full", folder, path, sid)
    finally:
        subprocess.run(["hdiutil", "detach", mount], capture_output=True)

# 3. Read-only sessions folder (local APFS scratch).
folder = os.path.join(scratch, "ro-profile")
path, sid = make_unreadable(folder)
os.chmod(path.parent, 0o500)
try:
    report("read-only folder", folder, path, sid)
finally:
    os.chmod(path.parent, 0o700)
