# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Git answers about the tree under test, never about a repository around it.

A tree under test is not always a checkout. A ``git archive`` snapshot has no ``.git``, and when it is unpacked
inside another checkout, ``git -C <tree> ...`` walks up and answers for THAT repository: its HEAD, its tracked
files, its LFS list. A test then fails on the wrong repository's facts, or passes on them, and a report records
the wrong commit as the source it ran (a 2.0.0 merge slot: ``test_native_sign_windows`` in a snapshot folder
kept in the developer's outer checkout, "path ... does not exist in 'fe3cef9280dc...'", that checkout's HEAD).

Two things, so the class is closed and not only the calls found:

* **The ceiling** (``confine``, called by ``suite_isolation``, which every host test, Blender suite and driver
  imports first): the folder above this tree goes into ``GIT_CEILING_DIRECTORIES``, so git itself stops
  looking for a repository there. Every git call of the process and of what it starts with its environment,
  product code a test runs included, then says "not a git repository" where it used to answer for the
  repository around. A checkout or a linked worktree is found as ever: its own folder is where git starts.
* **The question** (``own``, ``needed``, ``git``, ``command``, ``revision``): what a test or a runner uses to ask git about the tree
  under test. Inside another repository that is ``NotOwnCheckout``, "not a git checkout of the tree under
  test", an assertion's failure with its reason, never an answer. In a plain folder outside any repository
  there is nothing of git's to ask (``own`` is False, ``git`` returns None), and a test that needs the
  repository is skipped by ``needed`` under the one reason a verdict counts, never passed with that part left
  out. ``revision`` is the tree's own HEAD or None, for a report's "source revision".

No file of this folder asks git about the tree under test by itself (``test_native_own_checkout``). Tests that
make their own repositories in temporary folders run git on those as they did.
Standard library only.
"""

import contextlib
import os
import shutil
import subprocess
from pathlib import Path

VARIABLE = "GIT_CEILING_DIRECTORIES"
MESSAGE = "not a git checkout of the tree under test"
SKIPPED = "not a git checkout"                 # The skip's whole reason, after host_test_policy's "[environment] ".
REQUIRE = "ANGELO_TESTS_REQUIRE_CHECKOUT"      # "1": a run that must be in a checkout fails where it would skip.


class NotOwnCheckout(AssertionError):
    """The tree under test lies inside another repository: git would answer for that one."""


def ceiling(root):
    """The folder git must not look above ``root`` in: ``root``'s parent, as git reads such an entry."""
    return os.path.realpath(Path(root).parent).replace("\\", "/")


def confine(root):
    """Stop git, for this process and what it starts, from finding a repository above ``root``."""
    entry, entries = ceiling(root), [e for e in os.environ.get(VARIABLE, "").split(os.pathsep) if e]
    if entry not in entries:
        os.environ[VARIABLE] = os.pathsep.join(entries + [entry])


@contextlib.contextmanager
def confined(root):
    """``confine(root)`` for a block; the environment is as it was afterwards."""
    before = os.environ.get(VARIABLE)
    confine(root)
    try:
        yield
    finally:
        _restore(before)


@contextlib.contextmanager
def unconfined():
    """A block without the ceiling: what git answers when nothing stops it (a test's control)."""
    before = os.environ.pop(VARIABLE, None)
    try:
        yield
    finally:
        _restore(before)


def _restore(before):
    if before is None:
        os.environ.pop(VARIABLE, None)
    else:
        os.environ[VARIABLE] = before


def top(root, timeout=30):
    """The top level of the repository git finds from ``root`` when nothing stops it, or None: git is missing,
    fails, or finds none. Asked without the ceiling, so a tree inside another repository is told from a tree in
    none."""
    executable = shutil.which("git")
    if not executable:
        return None
    environment = {name: value for name, value in os.environ.items() if name != VARIABLE}
    try:   # Popen, not subprocess.run: harness tests replace ``run`` to count the commands of a trial.
        process = subprocess.Popen([executable, "-C", str(root), "rev-parse", "--show-toplevel"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        try:
            output, _errors = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    found = (output or "").strip()
    return Path(found) if process.returncode == 0 and found else None


def _same(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def own(root):
    """True when ``root`` is the top of its own checkout (a linked worktree is); False where git finds no
    repository at all. ``NotOwnCheckout`` when git would answer for another tree: a repository around ``root``."""
    found = top(root)
    if found is None:
        return False
    if _same(found, root):
        return True
    raise NotOwnCheckout(
        "%s: git answers for the repository at %s, which is not %s. Run this in a checkout or a `git worktree` of "
        "the tree, or unpack the tree outside any other repository." % (MESSAGE, found, root))


def needed(test, root):
    """For a test that cannot say anything without ``root``'s own repository: its subject is the repository, or
    it runs a product script that asks git where its repository is. True in ``root``'s own checkout;
    ``NotOwnCheckout`` (the test fails, by name) inside another repository. Where there is no repository the
    test is skipped under one exact reason, ``[environment] not a git checkout`` (``SKIPPED``), so a verdict
    classifies and counts it: such a skip is allowed for a source archive and never for a release cut, whose own
    runs are in real checkouts. A run that sets ``ANGELO_TESTS_REQUIRE_CHECKOUT=1`` gets a failure in its place."""
    if own(root):
        return True
    if os.environ.get(REQUIRE) == "1":
        raise NotOwnCheckout("%s: there is no repository here, and %s=1 says this run needs one (a cut's own run "
                             "is in a real checkout)" % (MESSAGE, REQUIRE))
    from host_test_policy import environment_skip
    test.skipTest(environment_skip(SKIPPED))


def command(root, *arguments):
    """The command line of ``git`` about ``root``'s own checkout, for a caller that runs it its own way; None
    where there is no repository to ask; ``NotOwnCheckout`` inside another one."""
    return ["git", "-C", str(root), *arguments] if own(root) else None


def git(root, *arguments, **named):
    """``subprocess.run`` of ``command(root, *arguments)`` with ``named``; None where there is no repository to
    ask; ``NotOwnCheckout`` inside another one."""
    line = command(root, *arguments)
    return None if line is None else subprocess.run(line, **named)


def revision(root, timeout=20):
    """``root``'s own HEAD, or None: never the HEAD of a repository around it (a report's source revision)."""
    try:
        done = git(root, "rev-parse", "HEAD", capture_output=True, text=True, timeout=timeout,
                   env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
    except (NotOwnCheckout, OSError, subprocess.SubprocessError):
        return None
    if done is None or done.returncode != 0:
        return None
    return done.stdout.strip() or None
