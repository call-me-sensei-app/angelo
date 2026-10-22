# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make a Windows build machine's source tree exactly the commit sent to it (windows_cloud_bootstrap.ps1).

The source travels as archives of HEAD (gcp_windows_validate.sh sends this file beside them; nothing is pushed).
Each step is a command:

* ``extract --into TREE [--deleted LIST] ARCHIVE...``: first removes the files LIST names (one path per line,
  UTF-8, as ``git -c core.quotePath=false diff --name-only`` writes them), then extracts each archive in order
  (source_extract.py: exact UTF-8 names and spelling, only changed files written and stamped now, type changes made
  way for, nothing outside the tree). Windows ``tar.exe`` decoded each name through the OEM code page (CP437) and
  wrote a garbled twin of every non-ASCII path, which the release then refused (``release_checks.py source-clean``).
* ``commit --tree TREE --commit SHA --pack PACK [--branch NAME]``: installs the commit object and its tree (no
  history) as a shallow commit whose HEAD is exactly SHA, reads it into the index and writes the files no archive
  created (existing files are kept), so Blender's build info reads the true hash, branch and date.
* ``clean --tree TREE --commit SHA``: removes every untracked, non-ignored file (left by an earlier extraction or a
  file deleted since), only once HEAD is SHA, the index is SHA's tree and no tracked file differs from it. With a
  stale HEAD or index, newly sent files are untracked too (an earlier clean-up deleted them that way), so it then
  refuses, removes nothing and exits 3.
* ``verify --tree TREE --commit SHA [--real LIST]``: the tree is exactly SHA by ``release_checks.py source-clean``'s
  rule and in its words (shipped_sources.py, beside this file), submodules aside (they are pinned separately); every
  tracked path has the commit's exact spelling on disk; no file LIST names (the large files the build needs) is a
  Git LFS pointer, which git status cannot see. Exit 3 lists each difference.
* ``sendable --tree REPO [--rev REV]`` (on the workstation, before a send): refuses a commit the machine could never
  make exact: one that tracks a symbolic link (refused as an archive member) or two paths differing only in letter
  case, file or folder (one Windows name for both; each send would rename one onto the other).
* ``settle --tree TREE --commit SHA``: once HEAD and the index are SHA, restores every tracked file that differs from
  the commit's own objects (not a Git LFS file: that would leave its pointer), runs ``clean``, then ``verify``. A
  send cut off half-way, then followed by another, converges on the new commit instead of keeping stray content.

Exit codes: 0 done, 1 failed, 3 refused or not the commit. Standard library only; Python 3.12 or newer (tarfile filters).
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:   # -I leaves the script's folder off sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipped_sources  # noqa: E402  (beside this file; standard library only)
from source_extract import (SourceTreeError, Speller, extract, inside, lexical,  # noqa: E402,F401  (beside this file)
                            remove_listed, shown)

REFUSED = 3
FULL_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")   # SHA-1 or SHA-256 object names, in full


def trust(tree):
    """Let this process's git calls open ``tree`` although another account owns it, and only that tree.

    The build machine's tree belongs to the account that received it; the desktop and bench sessions run as other
    users, whom git refuses with "dubious ownership". safe.directory counts only from protected configuration, the
    command line included, which GIT_CONFIG_COUNT is: so the tools' own entry points add it, for their tree alone,
    and nothing else (release_checks.py on a workstation) changes.
    """
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    os.environ["GIT_CONFIG_KEY_%d" % count] = "safe.directory"
    os.environ["GIT_CONFIG_VALUE_%d" % count] = Path(tree).resolve().as_posix()
    os.environ["GIT_CONFIG_COUNT"] = str(count + 1)


def git(tree, *args, stdin=None, input=None, env=None, check=True):
    done = subprocess.run(["git", "-C", str(tree), *args], stdin=stdin, input=input, capture_output=True,
                          env=dict(os.environ, **(env or {})))
    if check and done.returncode != 0:
        raise SourceTreeError("git %s exited with %d: %s" % (
            args[0], done.returncode, done.stderr.decode("utf-8", "replace").strip()[:400]))
    return done


def _text(done):
    return done.stdout.decode("utf-8", "replace").strip()


def _names(done):
    return [name for name in done.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def _full_sha(commit):
    if not FULL_SHA.fullmatch(commit or ""):
        raise SourceTreeError("%r is not a full commit hash" % commit)
    return commit


def install_commit(tree, commit, pack, branch="HEAD"):
    """Install ``commit`` (the commit and its tree, from ``pack``) as the shallow HEAD of ``tree``'s repository."""
    _full_sha(commit)
    git_dir = Path(tree, ".git")
    if not git_dir.is_dir():
        raise SourceTreeError("%s has no .git folder of its own" % tree)
    with open(pack, "rb") as stream:
        git(tree, "index-pack", "--stdin", stdin=stream)
    git_dir.joinpath("shallow").write_bytes("".join(name + "\n" for name in shallow_boundary(tree)).encode("ascii"))
    if branch and branch != "HEAD":
        git(tree, "update-ref", "refs/heads/" + branch, commit)
        git(tree, "symbolic-ref", "HEAD", "refs/heads/" + branch)
    else:
        git(tree, "update-ref", "--no-deref", "HEAD", commit)
    git(tree, "read-tree", commit)
    # Files no archive created come from the commit itself; existing files are kept, so checkout-index reports each
    # of them as existing and exits nonzero: the clean step's checks are what decide whether the tree is the commit.
    git(tree, "checkout-index", "--all", "--quiet", env={"GIT_LFS_SKIP_SMUDGE": "1"}, check=False)
    head = _text(git(tree, "rev-parse", "HEAD"))
    if head != commit:
        raise SourceTreeError("the checkout's HEAD is %r, not %s" % (head, commit))
    print("commit: %s on %s (shallow: the commit and its tree)" % (commit, branch or "HEAD"))


def shallow_boundary(tree):
    """Every commit ``tree``'s repository holds without one of its parents: what its ``shallow`` file must list.

    Each send brings one commit and no history, and earlier sends' commits stay (an old branch still names one). git
    walks history only to a commit the shallow file lists; past any other it reads a parent that is not there and
    fails ("Could not read <parent>"), as Blender's build info did from a detached HEAD (``git branch --contains``
    walks every branch). Listing only the newest commit left the earlier ones walkable, so the file is derived from
    the objects themselves at every install, which also mends a repository an older install left that way.
    """
    listing = _text(git(tree, "cat-file", "--batch-all-objects", "--batch-check=%(objecttype) %(objectname)"))
    commits = sorted(line.split()[1] for line in listing.splitlines() if line.startswith("commit "))
    parents = {}
    for commit in commits:
        body = git(tree, "cat-file", "commit", commit).stdout.decode("utf-8", "replace").split("\n\n", 1)[0]
        parents[commit] = [line.split()[1] for line in body.splitlines() if line.startswith("parent ")]
    wanted = sorted({parent for names in parents.values() for parent in names})
    checked = _text(git(tree, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                        input="".join(name + "\n" for name in wanted).encode("ascii")))
    held = {line.split()[0] for line in checked.splitlines() if line.endswith(" commit")}
    return [commit for commit in commits if any(parent not in held for parent in parents[commit])]


def identity_problems(tree, commit):
    """Why ``tree``'s repository is not ``commit`` itself (its top, HEAD and index); empty when it is."""
    top = git(tree, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0 or not os.path.samefile(_text(top), tree):
        return ["%s is not the top of its own git repository" % tree]
    head = git(tree, "rev-parse", "--verify", "-q", "HEAD", check=False)
    if _text(head) != commit:
        return ["HEAD is %r, not the commit sent (%s)" % (_text(head) or "unborn", commit)]
    index = git(tree, "write-tree", check=False)
    wanted = _text(git(tree, "rev-parse", commit + "^{tree}"))
    if index.returncode != 0 or _text(index) != wanted:
        return ["the index is not the commit's tree (%s, not %s)" % (_text(index) or "unwritable", wanted)]
    return []


def refusal(tree, commit):
    """Why ``tree`` is not yet provably ``commit`` (so cleaning it could delete sent files); empty when it is."""
    problems = identity_problems(tree, commit)
    if problems:
        return problems
    # The index read from the commit has no file times yet: refresh it so only a real content change counts. The
    # libraries folder (a submodule) is brought to its pin only after this, so submodules are not compared.
    git(tree, "update-index", "-q", "--ignore-submodules", "--refresh", check=False)
    differ = _names(git(tree, "diff-files", "--name-only", "-z", "--ignore-submodules=all", check=False))
    if differ:
        return ["%d tracked file(s) differ from the commit: %s" % (len(differ), shown(differ))]
    return []


def untracked(tree):
    return _names(git(tree, "ls-files", "-z", "--others", "--exclude-standard"))


def clean(tree, commit):
    """Remove every untracked, non-ignored file once ``tree`` is provably ``commit``; returns the exit code."""
    _full_sha(commit)
    problems = refusal(tree, commit)
    if problems:
        print("clean: refused, nothing removed: %s" % "; ".join(problems))
        return REFUSED
    found = untracked(tree)
    if found:
        git(tree, "clean", "-f", "-d", "-q")   # One -f: a nested repository is kept (and reported below).
    left = untracked(tree)
    print("clean: %d untracked file(s) removed%s" % (len(found) - len(left), (": " + shown(found)) if found else ""))
    if left:
        print("clean: %d untracked path(s) stay (git clean keeps nested repositories): %s" % (len(left), shown(left)))
        return REFUSED
    return 0


def repair(tree, commit):
    """Restore each tracked file that differs from ``commit`` from its objects, once HEAD and the index are it.

    A Git LFS file is left alone: its object here is the pointer, which would stand in for the real file (the large
    files arrive real in their own archive). Returns how many files were restored.
    """
    if identity_problems(tree, commit):
        return 0
    git(tree, "update-index", "-q", "--ignore-submodules", "--refresh", check=False)
    differ = _names(git(tree, "diff-files", "--name-only", "-z", "--ignore-submodules=all", check=False))
    if not differ:
        return 0
    attributes = _git_input(tree, ["check-attr", "-z", "--stdin", "filter"], differ)
    fields = attributes.stdout.decode("utf-8", "surrogateescape").split("\0")
    large = {fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] == "lfs"}
    plain = [name for name in differ if name not in large]
    if plain:
        _git_input(tree, ["checkout-index", "-f", "-q", "-z", "--stdin"], plain, GIT_LFS_SKIP_SMUDGE="1")
    print("repair: %d tracked file(s) restored from the commit%s" % (len(plain), (": " + shown(plain)) if plain else ""))
    return len(plain)


def _git_input(tree, args, names, **env):
    """git with ``names`` on its standard input, NUL-separated (paths need no quoting that way)."""
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, env=dict(os.environ, **env),
                          input=b"".join(name.encode("utf-8", "surrogateescape") + b"\0" for name in names))


def spelling_problems(tree):
    """Tracked paths spelled otherwise on disk (a case-insensitive file system kept an older spelling)."""
    speller = Speller(tree)
    found = []
    for name in _names(git(tree, "ls-files", "-z", check=False)):
        wrong = speller.misspelled(name)
        if wrong:
            found.append("%s (on disk: %s)" % (name, wrong[1]))
    if not found:
        return []
    return ["%d tracked path(s) are spelled otherwise on disk: %s" % (len(found), shown(found))]


def verify(tree, commit, real=()):
    """Every way ``tree`` differs from ``commit``, in release_checks.py source-clean's words; empty when it is it.

    ``real``: the large files the build needs, which must hold their content, never a Git LFS pointer.
    """
    _full_sha(commit)
    return (shipped_sources.source_clean(tree, commit, ignore_submodules=True) + spelling_problems(tree)
            + shipped_sources.pointer_problems(tree, real))


def settle(tree, commit, real=()):
    """Repair and clean ``tree`` toward ``commit`` when that is provably safe, then verify it."""
    if not identity_problems(tree, commit):
        speller = Speller(tree)
        for name in _names(git(tree, "ls-files", "-z", check=False)):
            speller.fix(name)
        repair(tree, commit)
        clean(tree, commit)
    return verify(tree, commit, real)


def unsendable(tree, rev="HEAD"):
    """Why commit ``rev`` of the repository at ``tree`` cannot be made exact on the build machine; empty when it can."""
    listed = git(tree, "ls-tree", "-r", "-z", "--full-tree", rev)
    links, spelled, clashes = [], {}, set()
    for entry in listed.stdout.split(b"\0"):
        if not entry:
            continue
        meta, raw = entry.split(b"\t", 1)
        name = raw.decode("utf-8", "surrogateescape")
        if meta.split()[0] == b"120000":
            links.append(name)
        parts = name.split("/")
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            first = spelled.setdefault(path.casefold(), path)
            if first != path:
                clashes.add("%s / %s" % (first, path))
    problems = []
    if links:
        problems.append("%d tracked symbolic link(s): %s" % (len(links), shown(links)))
    if clashes:
        problems.append("%d pair(s) of tracked paths differ only in letter case, one name on Windows: %s"
                        % (len(clashes), shown(sorted(clashes))))
    return problems


def read_list(path):
    """The paths of a list file (one per line, UTF-8); none when it is absent."""
    path = Path(path)
    return [line for line in path.read_bytes().decode("utf-8").split("\n") if line] if path.is_file() else []


def report(problems, tree, commit):
    for problem in problems:
        print("source: %s" % problem)
    if not problems:
        print("source: %s is exactly the commit %s" % (tree, commit))
    return REFUSED if problems else 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    unpack = commands.add_parser("extract", help="remove listed files, then extract archives with exact names")
    unpack.add_argument("--into", required=True)
    unpack.add_argument("--deleted", help="the files to remove first: one UTF-8 path per line")
    unpack.add_argument("archives", nargs="*")
    install = commands.add_parser("commit", help="install the sent commit as the tree's shallow HEAD")
    install.add_argument("--tree", required=True)
    install.add_argument("--commit", required=True)
    install.add_argument("--pack", required=True, help="git pack-objects output: the commit and its tree")
    install.add_argument("--branch", default="HEAD", help="the branch HEAD names (HEAD: a detached HEAD)")
    sendable = commands.add_parser("sendable", help="refuse a commit the build machine could never make exact")
    sendable.add_argument("--tree", required=True)
    sendable.add_argument("--rev", default="HEAD")
    for name, text in (("clean", "remove untracked files, only when the tree is provably the commit"),
                       ("verify", "refuse a tree that is not exactly the commit (source-clean's rule)"),
                       ("settle", "repair, clean, then verify a tree toward the commit")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--tree", required=True)
        command.add_argument("--commit", required=True)
        if name != "clean":
            command.add_argument("--real", help="the large files the build needs: one path per line, UTF-8")
    arguments = parser.parse_args(argv)
    trust(arguments.into if arguments.command == "extract" else arguments.tree)
    try:
        if arguments.command == "sendable":
            problems = unsendable(arguments.tree, arguments.rev)
            for problem in problems:
                print("send: %s cannot be made exact on the build machine: %s" % (arguments.rev, problem),
                      file=sys.stderr)
            return 1 if problems else 0
        if arguments.command == "extract":
            extract(arguments.into, arguments.archives, arguments.deleted)
            return 0
        if arguments.command == "commit":
            install_commit(arguments.tree, arguments.commit, arguments.pack, arguments.branch)
            return 0
        if arguments.command in ("verify", "settle"):
            real = read_list(arguments.real) if arguments.real else []
            check = verify if arguments.command == "verify" else settle
            return report(check(arguments.tree, arguments.commit, real), arguments.tree, arguments.commit)
        return clean(arguments.tree, arguments.commit)
    except SourceTreeError as error:
        print("source_tree %s: %s" % (arguments.command, error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
