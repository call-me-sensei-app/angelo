# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which commit a Windows build machine's source tree is, and how a send changes that (windows_source_send.ps1).

A send is a folder the launcher uploads (gcp_windows_source.sh ``send_source``): the archives, the commit's objects,
``angelo-lfs-real.txt`` (the large files the build needs, which must arrive real), these tools and ``send.json``
(commit, mode, base, branch), written last. The machine keeps, in STATE (``C:\\angelo\\state``):

* ``sent.commit`` and ``sent.tree``: the commit the last send set out to make that tree. Every run that builds or
  tests the source refuses the tree unless it is exactly this commit (``verify``).
* ``confirmed.commit``: the commit the tree was last PROVEN to be, by release_checks.py source-clean's rule (HEAD,
  index, no tracked file differing, nothing untracked). Only after that proof does the machine copy it to the bucket
  (``input/confirmed-commit``), the one base the launcher computes a delta from. Twice a launcher-written marker
  ("uploaded commit") said a commit had arrived when the send carrying it had been cut off, and the next send was an
  empty delta from it.
* ``lfs-real.txt``: the sent commit's large files the build needs; the proof fails while any is a Git LFS pointer.

Commands (exit codes: 0 proven; 1 failed, records unchanged, so a retry or the next send starts from the same
confirmed commit; 3 the tree is not provably the commit, ``confirmed.commit`` forgotten, so the next send is all of
HEAD; 4 the send does not apply here, nothing changed):

* ``receive --folder F --tree T --state S --commit C``: apply send F (a delta only onto its own base, as confirmed
  here), install the commit, repair, clean and verify; record C as confirmed only then.
* ``confirmed --tree T --state S``: prove ``confirmed.commit`` again (it is published at every boot), or forget it.
* ``verify --tree T --state S``: refuse T unless it is the tree a send made and exactly ``sent.commit``; a machine
  no send made has nothing to check.
* ``leftovers --tree T``: list the untracked, non-ignored files a stage left in T (a report; it removes nothing).

Standard library only; Python 3.12 or newer.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:   # -I leaves the script's folder off sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_tree  # noqa: E402  (beside this file)

PROVEN, FAILED, NOT_THE_COMMIT, NOT_APPLICABLE = 0, 1, 3, 4


class NotApplicable(Exception):
    """The send cannot be applied to this tree; nothing was changed."""


def read(state, name):
    path = Path(state, name)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def write(state, name, value):
    Path(state).mkdir(parents=True, exist_ok=True)
    temporary = Path(state, name + ".new")
    temporary.write_text(value + "\n", encoding="utf-8")
    os.replace(temporary, Path(state, name))   # a record is whole or absent, even if the machine stops here


def forget(state, name):
    Path(state, name).unlink(missing_ok=True)


def load_send(folder, commit):
    """``send.json`` of ``folder`` when it is a complete send of ``commit``; raises NotApplicable otherwise."""
    try:
        send = json.loads(Path(folder, "send.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NotApplicable("%s is not a complete send (send.json: %s)" % (folder, error)) from error
    if send.get("commit") != commit or not source_tree.FULL_SHA.fullmatch(commit or ""):
        raise NotApplicable("the send in %s is of %r, but the machine was sent %r" % (folder, send.get("commit"), commit))
    if send.get("mode") not in ("full", "delta"):
        raise NotApplicable("send mode %r is neither full nor delta" % send.get("mode"))
    return send


def receive(folder, tree, state, commit):
    """Apply the send in ``folder`` to ``tree`` and prove it is ``commit``; returns the exit code."""
    folder, tree = Path(folder), Path(tree)
    send = load_send(folder, commit)
    real = source_tree.read_list(folder / "angelo-lfs-real.txt")
    # Received and proven already (the machine stopped before its boot recorded the send): done, not a mismatch.
    if (tree / ".git").is_dir() and source_tree._text(source_tree.git(tree, "rev-parse", "-q", "--verify", "HEAD",
                                                                       check=False)) == commit:
        if not source_tree.verify(tree, commit, real):
            record_sent(state, tree, commit, real)
            write(state, "confirmed.commit", commit)
            print("receive: %s is already exactly %s; nothing to apply" % (tree, commit))
            return PROVEN
    confirmed = read(state, "confirmed.commit")
    if send["mode"] == "delta":
        # A delta holds only what changed since its base: onto any other tree it would leave that tree's differences.
        if not confirmed or confirmed != send.get("base"):
            raise NotApplicable("the send is a delta from %s, but this tree is confirmed at %s" % (
                send.get("base"), confirmed or "no commit"))
        archives, deleted = [folder / "angelo-delta.tar.gz"], folder / "angelo-deleted.txt"
    else:
        archives, deleted = [folder / "angelo-src.tar.gz"], None
    # Real files over the archive's LFS pointers, every send: only those that differ are written (a renamed or
    # re-moded large file arrives real, never as the pointer a delta carries for it).
    archives.append(folder / "angelo-lfs.tar.gz")
    # From here the tree changes. confirmed.commit stays until the proof: a send cut off now is retried (or followed)
    # from the same base, and settle() makes whatever it left exact.
    record_sent(state, tree, commit, real)
    source_tree.extract(tree, [str(path) for path in archives], str(deleted) if deleted else None)
    if not (tree / ".git").is_dir():
        source_tree.git(tree, "init", "-q")
    source_tree.install_commit(tree, commit, folder / "angelo-commit.pack", send.get("branch") or "HEAD")
    problems = source_tree.settle(tree, commit, real)
    if problems:
        forget(state, "confirmed.commit")
        source_tree.report(problems, tree, commit)
        print("receive: %s is not provably %s; no commit is confirmed (the next send is all of HEAD)" % (tree, commit))
        return NOT_THE_COMMIT
    write(state, "confirmed.commit", commit)
    print("receive: %s confirmed at %s (%s send)" % (tree, commit, send["mode"]))
    return PROVEN


def record_sent(state, tree, commit, real):
    write(state, "sent.commit", commit)
    write(state, "sent.tree", str(Path(tree).resolve()))
    write(state, "lfs-real.txt", "\n".join(real))


def real_files(state):
    return source_tree.read_list(Path(state, "lfs-real.txt"))


def confirmed(tree, state):
    """Prove ``confirmed.commit`` again; forget it unless the tree still is that commit."""
    commit = read(state, "confirmed.commit")
    if not commit:
        print("confirmed: no commit is confirmed for %s" % tree)
        return NOT_THE_COMMIT
    problems = source_tree.verify(tree, commit, real_files(state))
    if problems:
        forget(state, "confirmed.commit")
        source_tree.report(problems, tree, commit)
        print("confirmed: %s is no longer %s; forgotten" % (tree, commit))
        return NOT_THE_COMMIT
    print("confirmed: %s is %s" % (tree, commit))
    return PROVEN


def verify_sent(tree, state):
    """Refuse ``tree`` unless it is exactly the commit last sent to it (source-clean's rule and words)."""
    if not Path(state).is_dir():
        print("source: %s has no send record (not a build machine); not checked" % state)
        return PROVEN
    made = read(state, "sent.tree")
    if made and not (Path(tree).exists() and Path(made).exists() and os.path.samefile(made, tree)):
        # The sent commit says nothing about another tree: proving nothing, it must not pass as proven.
        print("source: %s is not the tree a send made (%s): no commit is proven for it" % (tree, made))
        return NOT_THE_COMMIT
    commit = read(state, "sent.commit")
    if not commit:
        print("source: no send was received for %s: it is not a commit that was sent" % tree)
        return NOT_THE_COMMIT
    return source_tree.report(source_tree.verify(tree, commit, real_files(state)), tree, commit)


def leftovers(tree):
    """Report the untracked, non-ignored files in ``tree`` (what a stage wrote into the source); removes nothing."""
    found = source_tree.untracked(tree) if (Path(tree) / ".git").is_dir() else []
    print("leftovers: %d untracked file(s) in %s%s" % (len(found), tree, (": " + source_tree.shown(found)) if found else ""))
    return PROVEN


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("receive", "confirmed", "verify", "leftovers"):
        command = commands.add_parser(name)
        command.add_argument("--tree", required=True)
        command.add_argument("--state", required=name != "leftovers")
        if name == "receive":
            command.add_argument("--folder", required=True)
            command.add_argument("--commit", required=True)
    arguments = parser.parse_args(argv)
    source_tree.trust(arguments.tree)
    try:
        if arguments.command == "receive":
            return receive(arguments.folder, arguments.tree, arguments.state, arguments.commit)
        if arguments.command == "confirmed":
            return confirmed(arguments.tree, arguments.state)
        if arguments.command == "leftovers":
            return leftovers(arguments.tree)
        return verify_sent(arguments.tree, arguments.state)
    except NotApplicable as error:
        print("source_send %s: does not apply, nothing changed: %s" % (arguments.command, error))
        return NOT_APPLICABLE
    except (source_tree.SourceTreeError, OSError, subprocess.SubprocessError) as error:
        print("source_send %s: %s" % (arguments.command, error))
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
