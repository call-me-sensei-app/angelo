# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No confined move carries a link to a new path (test_blender_script_confinement; break-it-confine-reparse-b7d499c
head-7 M6, the reviewer's general fix; bpy_angelo_guard_confine_tree.hh).

No confined code makes a link, nor moves one; a move of a folder that holds one at any depth would put that link where
an open judged before the move, Blender's later read of a path (an image's pixels, a UDIM tile, a cache) or a native
library's own open could go through it. So such a move is refused, saying why, and no lock is held between a judge and
its open. Ordinary moves keep working, without waiting: a plain folder, a file, and a pool of threads copying files
(``shutil.copy2``) into a folder that is then renamed. The links point at a local folder: nothing here touches the
network. Run only inside Blender, from the suite.
"""

import os
import sys
import tempfile
import time

_POOL = """
import os, shutil, time
from concurrent.futures import ThreadPoolExecutor
staging = os.path.join(F, 'staging')
os.mkdir(staging)
def copy(i):
    shutil.copy2(os.path.join(F, 'sources', 's%d.txt' % i), os.path.join(staging, 'c%d.txt' % i))
started = time.perf_counter()
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(copy, range(40)))
os.replace(staging, os.path.join(F, 'published'))
OUT['ms'] = (time.perf_counter() - started) * 1000
OUT['count'] = len(os.listdir(os.path.join(F, 'published')))
"""


def _link(target, at):
    """A folder link (POSIX: a symbolic link; Windows: a junction, which needs no right). False when none can be made."""
    try:
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(target, at)
        else:
            os.symlink(target, at, target_is_directory=True)
        return True
    except OSError:
        return False


def _copy_cases(run, check, folder):
    """``shutil.copy2`` (and ``copy``, ``copytree``) on Windows copy through ``_winapi.CopyFile2``, raising no
    ``shutil.copyfile`` event (head-10/11 re-read MUST): a private source, a network source and a copy that makes a
    link are refused on every system, by whichever event the copy raises there. Each case writes its own target (a
    copy that wrongly went through must not decide the next case: iso-win on red ae20673)."""
    def target(label):
        return os.path.join(folder, "copied-" + label)
    remote = r"\\angelo-probe.invalid\s\x.txt" if sys.platform == "win32" else "/net/angelo-probe.invalid/s/x.txt"
    for label, source in (("private", "import shutil\nshutil.copy2(KEY, T)"),
                          ("network", "import shutil\nshutil.copy2(R, T)"),
                          ("private_copy", "import shutil\nshutil.copy(KEY, T)")):
        outcome = run(source, T=target(label), R=remote)
        check("copy_refused_%s" % label, bool(outcome.get("refused")) and not os.path.lexists(target(label)),
              outcome)
    plain = os.path.join(folder, "plain.txt")
    with open(plain, "w") as f:
        f.write("x")
    # The ordinary copy first, from a file nothing else touches (no link to it, no preview reading it).
    outcome = run("import shutil\nshutil.copy2(P, T)", P=plain, T=target("plain"))
    check("copy_of_a_plain_file_works", outcome.get("refused") is None and "error" not in outcome and
          os.path.isfile(target("plain")) and not os.path.islink(target("plain")), outcome)
    linked = os.path.join(folder, "linked.txt")
    with open(linked, "w") as f:
        f.write("x")
    link = os.path.join(folder, "plain-link.txt")
    try:
        os.symlink(linked, link)
    except OSError:
        link = None
    if link is not None:
        outcome = run("import shutil\nshutil.copy2(L, T, follow_symlinks=False)", L=link, T=target("link"))
        check("copy_refused_making_a_link", bool(outcome.get("refused")) and not os.path.lexists(target("link")),
              outcome)
    if sys.platform == "win32":
        # CopyFile2's own flags (head-12 re-read M1): copying a junction as a junction (OPEN_AND_COPY_REPARSE_POINT
        # with DIRECTORY) makes a link at the target, as COPY_SYMLINK does; only content-copy flags pass.
        junction = os.path.join(folder, "junction")
        os.mkdir(os.path.join(folder, "inside"))
        if _link(os.path.join(folder, "inside"), junction):
            for label, flags in (("reparse_directory", 0x00200000 | 0x00000080), ("reparse", 0x00200000),
                                 ("unknown_bit", 0x40000000)):
                outcome = run("import _winapi\n_winapi.CopyFile2(J, T, F)", J=junction, T=target(label), F=flags)
                check("copyfile2_refused_%s" % label, bool(outcome.get("refused")) and
                      not os.path.lexists(target(label)), outcome)
    # bpy.utils.previews.load reads its file later, on a thumbnail job: judged when it is asked (re-read SHOULD).
    outcome = run("import bpy.utils.previews\ncollection = bpy.utils.previews.new()\n"
                  "try:\n    collection.load('key', KEY, 'IMAGE')\nfinally:\n    bpy.utils.previews.remove(collection)\n")
    check("previews_load_of_a_private_file_refused", bool(outcome.get("refused")), outcome)
    outcome = run("import bpy.utils.previews\ncollection = bpy.utils.previews.new()\n"
                  "try:\n    collection.load('plain', P, 'IMAGE')\nfinally:\n    bpy.utils.previews.remove(collection)\n",
                  P=plain)
    check("previews_load_of_a_plain_file_works", outcome.get("refused") is None and "error" not in outcome, outcome)


def run_move_cases(run, check, skip, numbers=None):
    numbers = numbers if numbers is not None else {}
    with tempfile.TemporaryDirectory(prefix="angelo-copy-cases-") as folder:
        _copy_cases(run, check, folder)
    with tempfile.TemporaryDirectory(prefix="angelo-move-cases-") as folder:
        elsewhere = os.path.join(folder, "elsewhere")
        os.mkdir(elsewhere)
        deep = os.path.join(folder, "project", "textures", "set", "a")
        os.makedirs(deep)
        if not _link(elsewhere, os.path.join(deep, "shared")):
            skip("moving_a_folder_holding_a_deep_link_is_refused", "no folder link could be made here")
        else:
            for source in ("os.rename(P, P + '-moved')", "os.replace(P, P + '-moved')",
                           "import shutil\nshutil.move(P, P + '-moved')"):
                outcome = run(source, P=os.path.join(folder, "project"))
                refused = outcome.get("refused") or ""
                check("moving_a_folder_holding_a_deep_link_is_refused[%s]" % source.split("\n")[-1].split("(")[0],
                      "holds a symbolic link" in refused and os.path.isdir(os.path.join(folder, "project")) and
                      not os.path.exists(os.path.join(folder, "project-moved")), outcome)
        # Ordinary moves: a plain folder there and back, a file.
        plain = os.path.join(folder, "plain", "sub")
        os.makedirs(plain)
        with open(os.path.join(plain, "x.txt"), "w") as f:
            f.write("x")
        out = {}
        outcome = run("import os, time\nstarted = time.perf_counter()\nos.rename(P, P + '-moved')\n"
                      "os.rename(P + '-moved', P)\nos.replace(os.path.join(P, 'sub', 'x.txt'), os.path.join(P, 'y.txt'))\n"
                      "OUT['ms'] = (time.perf_counter() - started) * 1000\n",
                      P=os.path.join(folder, "plain"), OUT=out)
        check("moving_a_plain_folder_and_a_file_works", outcome.get("refused") is None and "error" not in outcome and
              os.path.isfile(os.path.join(folder, "plain", "y.txt")), outcome)
        if "ms" in out:
            numbers["move_plain_folder_ms"] = out["ms"]
        # A pool of threads copying files into a folder that is then renamed (the case that leaked the old lock).
        os.mkdir(os.path.join(folder, "sources"))
        for i in range(40):
            with open(os.path.join(folder, "sources", "s%d.txt" % i), "w") as f:
                f.write("x" * 1000)
        out = {}
        started = time.perf_counter()
        outcome = run(_POOL, F=folder, OUT=out)
        check("a_pool_copy_then_rename_works_without_waiting",
              outcome.get("refused") is None and "error" not in outcome and out.get("count") == 40 and
              time.perf_counter() - started < 5, (outcome, out))
        if "ms" in out:
            numbers["move_pool_copy_then_rename_ms"] = out["ms"]
        # And after it, every later confined rename still works (nothing is left held by a finished thread).
        outcome = run("import os\nos.rename(P, P + '-again')\nos.rename(P + '-again', P)\n",
                      P=os.path.join(folder, "published"))
        check("renames_after_a_pool_still_work", outcome.get("refused") is None and "error" not in outcome, outcome)
