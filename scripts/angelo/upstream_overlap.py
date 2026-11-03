#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What newer upstream Blender did to the code an Angelo change touches (AGENTS.md, "Newer Blender first").

Every Angelo change to Blender's own code is carried across each base move. One shaped like upstream's direction
merges cheaply or is dropped when upstream ships it; one that fights it costs again at every move. This tool shows,
before a Blender-side change is designed and in its pull request, what upstream has done since Angelo's fork base in
the same files, functions and RNA properties.

    upstream_overlap.py overlap [--branch REF] [--against REF|fork-base] [PATH ...] [--offline] [--format FMT]
    upstream_overlap.py watch [--offline]
    upstream_overlap.py fetch [--shallow]
    upstream_overlap.py backport COMMIT ... [--branch REF]
    upstream_overlap.py early [--target REF]

``overlap`` takes the Blender-side files a branch changes since it left ``--against`` (default ``origin/main``;
``fork-base`` for everything Angelo changed since its upstream base), or the PATHs given. ``watch`` lists the upstream
release branches, tags and alphas newer than the fork base and the release notes to read. ``backport`` and ``early``
are for features adopted before upstream releases them (``upstream_early.py``, ``early_adoption.json``).

Upstream lines, each a range that does not repeat the one before it:

* **series**: Angelo's own release branch (``blender-v<X.Y>-release`` from the version header) past the fork base,
  the corrective releases. Commits whose patch Angelo already carries are marked "in Angelo";
* **release**: the newest ``blender-v*-release`` branch past the series branch;
* **main**: the next alpha, ``main`` past the newest release branch.

The fork base is ``git merge-base <branch> <series branch>``. Versions come from ``BKE_blender_version.h`` at each tip.
A file is Blender-side when it, or the folder it is new in, exists at the fork base. Functions are the hunk headers
git computes with its built-in C/C++, Objective-C and Python patterns: an approximation, and the report says so.
RNA definitions are read from ``RNA_def_struct``/``RNA_def_property`` calls in ``makesrna/intern/rna_*.cc``, each
property under the struct defined before it.

It fetches the three branches from the ``upstream`` remote (fetch-only) into ``refs/remotes/upstream/`` unless
``--offline``, and falls back to already-fetched refs when the network fails. A CI clone runs
``upstream_overlap.py fetch --shallow`` first: only the commits the previous series' first release cannot reach, with
every blob (about 180 MB and 30 s for 5.2), so the patches read locally. It prints the oldest commit date fetched;
fetch Angelo's own history after it, blobless and back to that date, and ``overlap --offline`` reads both. It never changes the working tree, the index or a local
branch, and exits 0 whenever it has a report: it informs and never blocks. Standard library only.
"""

import argparse
import dataclasses
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

REMOTE = "upstream"
VERSION_HEADER = "source/blender/blenkernel/BKE_blender_version.h"
NOTES_URL = "https://developer.blender.org/docs/release_notes/{}.{}/"
RELEASE_BRANCH = re.compile(r"blender-v(\d+)\.(\d+)-release")
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
RNA_FILES = "source/blender/makesrna/intern/rna_*.cc"
NETWORK_TIMEOUT = 180
PATHS_PER_CALL = 400  # keeps every command line far below Windows' 32 KB limit
# git applies its built-in hunk-header patterns only through attributes, and Blender's .gitattributes sets none.
FUNCTION_ATTRIBUTES = "".join(f"*.{ext} diff={driver}\n" for driver, exts in (
    ("cpp", "c cc cpp cxx h hh hpp inl glsl msl cl cu osl"), ("objc", "m mm"), ("python", "py")) for ext in exts.split())
_PY_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)")
_C_TYPE = re.compile(r"^\s*(?:typedef\s+)?(?:struct|class|enum|union)\s+(?:class\s+)?([A-Za-z_]\w*)\s*(?:[:{]|$)")
_C_CALL = re.compile(r"([A-Za-z_~][\w~]*(?:::[A-Za-z_~][\w~]*)*)\s*\(")
_NOT_FUNCTIONS = {"if", "for", "while", "switch", "return", "sizeof", "defined", "decltype", "static_assert"}
_RNA_STRUCT = re.compile(r'RNA_def_struct(?:_ptr)?\s*\(\s*[\w>.-]+\s*,\s*"(\w+)"')
_RNA_PROP = re.compile(r'RNA_def_property\s*\(\s*\w+\s*,\s*"(\w+)"')
CODE_SUFFIXES = {"py", "c", "cc", "cpp", "h", "hh", "hpp", "mm", "m"}
_RNA_FILE = re.compile(r"source/blender/makesrna/intern/rna_\w+\.cc")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


class GitError(RuntimeError):
    pass


def git(repo, *args, timeout=None, ok=(0,)):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    try:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired as error:
        raise GitError(f"git {args[0]} timed out after {timeout} s") from error
    if result.returncode not in ok:
        raise GitError(f"git {' '.join(args[:3])}: {result.stderr.strip() or result.returncode}")
    return result.stdout


def git_paths(repo, args, paths, ok=(0,)):
    """``git <args> -- <paths>`` in chunks, outputs joined; an empty path list runs nothing."""
    paths = sorted(paths)
    return "".join(git(repo, *args, "--", *paths[i:i + PATHS_PER_CALL], ok=ok)
                   for i in range(0, len(paths), PATHS_PER_CALL))


def is_ancestor(repo, commit, ref):
    """True when ``commit`` exists here and ``ref`` contains it."""
    return subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", commit, ref],
                          capture_output=True).returncode == 0


def rev(repo, ref):
    return git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}", ok=(0, 1)).strip() or None


def prefetch(repo, ref, paths):
    """In a blobless clone, fetch the blobs of ``paths`` at ``ref`` in one request, as git's own lazy fetch would one
    at a time (a search over many files would otherwise make a round trip per file)."""
    promisors = [line.split()[0].split(".")[1] for line in git(
        repo, "config", "--get-regexp", r"^remote\..*\.promisor$", ok=(0, 1)).splitlines() if line.endswith(" true")]
    if not promisors or not paths:
        return
    ids = "".join(f"{oid}\n" for oid in blob_ids(repo, ref, paths).values())
    for remote in promisors:
        subprocess.run(["git", "-C", repo, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "--quiet", "--no-tags",
                        "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "--stdin", remote],
                       input=ids, text=True, capture_output=True, timeout=NETWORK_TIMEOUT,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))


# ---- Versions and upstream refs ----

@dataclasses.dataclass(frozen=True)
class Version:
    number: int
    patch: int
    cycle: str
    suffix: str = ""

    @property
    def series(self):
        return self.number // 100, self.number % 100

    @property
    def notes_url(self):
        return NOTES_URL.format(*self.series)

    def __str__(self):
        major, minor = self.series
        return f"{major}.{minor}.{self.patch} {self.cycle}" + (f" {self.suffix}" if self.suffix else "")


def parse_version(text):
    def define(name, pattern):
        match = re.search(rf"^#define[ \t]+BLENDER_VERSION{name}[ \t]+({pattern})[ \t]*$", text, re.M)
        return match.group(1) if match else ""
    number = define("", r"\d+")
    if not number:
        return None
    return Version(int(number), int(define("_PATCH", r"\d+") or 0), define("_CYCLE", r"\w+"), define("_SUFFIX", r"\w+"))


def read_version(repo, ref):
    try:
        return parse_version(git(repo, "show", f"{ref}:{VERSION_HEADER}"))
    except GitError:
        return None


def release_series(name):
    match = RELEASE_BRANCH.fullmatch(name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def remote_refs(repo, remote):
    """{"heads": {branch: sha}, "tags": {tag: sha}} as the remote has them now, or None when it cannot be reached."""
    try:
        out = git(repo, "ls-remote", remote, "refs/heads/main", "refs/heads/blender-v*-release", "refs/tags/v*",
                  timeout=NETWORK_TIMEOUT)
    except GitError:
        return None
    refs = {"heads": {}, "tags": {}}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name.startswith("refs/heads/"):
            refs["heads"][name[len("refs/heads/"):]] = sha
        elif name.startswith("refs/tags/") and not name.endswith("^{}"):
            refs["tags"][name[len("refs/tags/"):]] = sha
    return refs


def local_refs(repo, remote):
    prefix = f"refs/remotes/{remote}/"
    heads = {}
    for line in git(repo, "for-each-ref", "--format=%(objectname) %(refname)", prefix).splitlines():
        sha, _, name = line.partition(" ")
        heads[name[len(prefix):]] = sha
    tags = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/tags/v*").split()
    return {"heads": heads, "tags": dict.fromkeys(tags, "")}


def fetch(repo, remote, branches, exclude=None):
    """Fetch ``branches`` into ``refs/remotes/<remote>/``; with ``exclude`` (a tag), shallow: only commits that tag
    cannot reach, every blob included."""
    specs = [f"+refs/heads/{b}:refs/remotes/{remote}/{b}" for b in branches]
    shallow = [f"--shallow-exclude=refs/tags/{exclude}"] if exclude else []
    try:
        git(repo, "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", *shallow, remote, *specs,
            timeout=NETWORK_TIMEOUT)
        return True
    except GitError:
        return False


def shallow_exclude(tags, series):
    """The first release of the series before ``series`` (``v5.1.0`` for 5.2): excluding what it reaches keeps the
    fork base, Angelo's series, the newest release branch and ``main`` whole. Excluding the series' own first release
    instead would cut the feature branches upstream merged after it."""
    older = [t for t in tags if (k := _tag_key(t)) and k[2] == 0 and k[:2] < series]
    return max(older, key=_tag_key, default=None)


def fetch_upstream(repo, head, remote=REMOTE, shallow=False):
    """Fetch Angelo's series branch, the newest release branch and ``main``; returns notes for the report."""
    head_version = read_version(repo, head)
    if head_version is None:
        raise GitError(f"{head[:11]} has no {VERSION_HEADER}")
    live = remote_refs(repo, remote)
    if live is None:
        return [f"{remote} unreachable; using already-fetched refs"]
    series = "blender-v{}.{}-release".format(*head_version.series)
    releases = [b for b in live["heads"] if release_series(b)]
    wanted = {series, "main", max(releases, key=release_series, default=series)} & set(live["heads"])
    exclude = shallow_exclude(live["tags"], head_version.series) if shallow else None
    if shallow and exclude is None:
        raise GitError(f"{remote} has no release tag before {head_version.series[0]}.{head_version.series[1]}")
    if not fetch(repo, remote, sorted(wanted), exclude):
        return [f"fetching from {remote} failed; using already-fetched refs"]
    return []


@dataclasses.dataclass
class Line:
    role: str  # "series", "release" or "main"
    branch: str
    tip: str
    since: str  # exclusive start of the range
    version: Version
    count: int

    @property
    def title(self):
        return f"{self.version or '?'} ({self.branch})"


@dataclasses.dataclass
class Upstream:
    fork_base: str
    base_version: Version
    lines: list
    tags: list  # release tags newer than the fork base, oldest first
    branches: list  # release branches newer than the base series
    notes: list  # how this was resolved, when it matters (offline, fetch failure)


def _tag_key(tag):
    match = RELEASE_TAG.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def resolve_upstream(repo, head, remote=REMOTE, offline=False):
    notes = [] if offline else fetch_upstream(repo, head, remote)
    head_version = read_version(repo, head)
    if head_version is None:
        raise GitError(f"{head[:11]} has no {VERSION_HEADER}")
    series = "blender-v{}.{}-release".format(*head_version.series)
    local = local_refs(repo, remote)
    heads = local["heads"]
    live = None if offline or notes else remote_refs(repo, remote)
    tags = set(local["tags"]) | set((live or {}).get("tags", {}))
    if series not in heads:
        raise GitError(f"no {remote}/{series}; run once without --offline")
    fork_base = git(repo, "merge-base", head, heads[series], ok=(0, 1)).strip()
    if not fork_base:
        raise GitError(f"{head[:11]} shares no history with {remote}/{series} (a shallow clone needs deeper history)")
    base_version = read_version(repo, fork_base) or head_version
    newer = sorted((b for b in heads if release_series(b) and release_series(b) > head_version.series),
                   key=release_series)
    lines, since = [], fork_base
    for role, branch in (("series", series), ("release", newer[-1] if newer else None), ("main", "main")):
        if branch not in heads:
            continue
        tip = heads[branch]
        count = int(git(repo, "rev-list", "--count", "--no-merges", f"{since}..{tip}"))
        if count:
            lines.append(Line(role, branch, tip, since, read_version(repo, tip), count))
        since = tip
    if "main" not in heads:
        notes.append(f"no {remote}/main fetched; the next alpha is not shown")
    base_key = (*base_version.series, base_version.patch)
    newer_tags = sorted((t for t in tags if _tag_key(t) and _tag_key(t) > base_key), key=_tag_key)
    return Upstream(fork_base, base_version, lines, newer_tags, newer, notes)


# ---- Angelo's side ----

def hunk_symbol(context):
    """The function, method or type a hunk header names, or None."""
    if match := _PY_DEF.match(context):
        return match.group(1)
    for match in _C_CALL.finditer(context):
        name = match.group(1).split("::")[-1]
        if name not in _NOT_FUNCTIONS and not name.isupper():
            return name
    if match := _C_TYPE.match(context):
        return match.group(1)
    return None


def parse_patch(text, wanted=None):
    """{path: (symbols, added line numbers)} from ``git diff/log -p -U0``. The path is the old one (the new one for an
    added file); ``wanted`` keeps only paths in that set, on either side of a rename."""
    files, current = {}, None
    for line in text.splitlines():
        if line.startswith("diff --git a/"):
            old, _, new = line[len("diff --git a/"):].partition(" b/")
            path = old if wanted is None or old in wanted else new
            current = files.setdefault(path, (set(), set())) if wanted is None or path in wanted else None
        elif current is not None and (match := _HUNK.match(line)):
            if symbol := hunk_symbol(match.group(3)):
                current[0].add(symbol)
            start, length = int(match.group(1)), int(match.group(2) or 1)
            current[1].update(range(start, start + length))
    return files


@dataclasses.dataclass
class Change:
    label: str
    files: dict  # Blender-side file Angelo modifies or deletes -> functions it changes there
    added: list  # files Angelo adds inside Blender's folders
    present: list  # every changed file still at the branch tip (where Angelo's uses are searched)
    own_lines: dict  # changed file -> line numbers Angelo wrote (None: the whole file)
    rna_files: list  # changed RNA definition files


def angelo_change(repo, head, base, paths, fork_base, attributes):
    """The Blender-side part of ``base..head`` (limited to PATHs when given), or the PATHs alone without a base."""
    fork_files = set(git(repo, "ls-tree", "-r", "--name-only", fork_base).splitlines())
    fork_dirs = {p.rpartition("/")[0] for p in fork_files} - {""}
    if base:
        args = ["diff", "--name-status", "--no-renames", base, head]
        status = git_paths(repo, args, paths) if paths else git(repo, *args)
        entries = [line.split("\t", 1) for line in status.splitlines() if "\t" in line]
    else:
        entries = [("M" if p in fork_files else "A", p) for p in paths]
    modified = [p for s, p in entries if s[0] in "MD" and p in fork_files]
    added = [p for s, p in entries if s[0] == "A" and p.rpartition("/")[0] in fork_dirs]
    present = [p for s, p in entries if s[0] != "D"]
    patch = parse_patch(git_paths(repo, ["-c", f"core.attributesFile={attributes}", "diff", "-U0", "--no-color",
                                         "--no-ext-diff", base, head], [p for s, p in entries if s[0] == "M"])) \
        if base else {}
    files = {p: patch.get(p, (set(), set()))[0] for p in modified}
    own_lines = {p: (patch[p][1] if p in patch else set()) for s, p in entries if s[0] == "M"}
    own_lines.update({p: None for s, p in entries if s[0] == "A" or not base})
    rna = [p for p in present if _RNA_FILE.fullmatch(p)]
    label = f"{base[:11]}..{head[:11]}" if base else ", ".join(paths[:3]) + (" …" if len(paths) > 3 else "")
    return Change(label, files, added, present, own_lines, rna)


# ---- Upstream's side ----

@dataclasses.dataclass
class Commit:
    sha: str
    date: str
    subject: str
    in_angelo: bool = False
    files: dict = dataclasses.field(default_factory=dict)  # Angelo path -> functions the commit changes there


def upstream_commits(repo, line, current, attributes, head):
    """The non-merge commits of ``line`` touching ``current`` ({path on this line: Angelo path}), newest first."""
    out = git_paths(repo, ["-c", f"core.attributesFile={attributes}", "log", "--no-merges", "-p", "-U0", "--no-color",
                           "--no-ext-diff", "--format=%x1e%H%x1f%as%x1f%s", f"{line.since}..{line.tip}"], current)
    commits = {}
    shallow = shallow_commits(repo)
    for record in out.split("\x1e")[1:]:
        header, _, patch = record.partition("\n")
        sha, date, subject = header.split("\x1f", 2)
        if sha in shallow:  # a shallow commit has no parent here, so its patch would add every file
            continue
        commit = commits.setdefault(sha, Commit(sha, date, subject))
        for path, (symbols, _) in parse_patch(patch, set(current)).items():
            commit.files.setdefault(current[path], set()).update(symbols)
    if line.role == "series" and commits:
        for mark in git(repo, "log", "--cherry-mark", "--right-only", "--no-merges", "--format=%m%H",
                        f"{head}...{line.tip}").split():
            if mark[0] == "=" and mark[1:] in commits:
                commits[mark[1:]].in_angelo = True
    return sorted(commits.values(), key=lambda c: (c.date, c.sha), reverse=True)


def shallow_commits(repo):
    path = git(repo, "rev-parse", "--git-path", "shallow").strip()
    path = path if os.path.isabs(path) else os.path.join(repo, path)
    try:
        with open(path, encoding="ascii") as handle:
            return set(handle.read().split())
    except OSError:
        return set()


def moves(repo, since, tip):
    """{old path: new path, or None when deleted} from ``since`` to ``tip``, renames detected over the whole tree."""
    fields = git(repo, "-c", "diff.renameLimit=0", "diff", "--name-status", "-z", "-M", "--diff-filter=DR",
                 since, tip).split("\0")
    result, i = {}, 0
    while i < len(fields) - 1:
        if fields[i].startswith("R"):
            result[fields[i + 1]] = fields[i + 2]
            i += 3
        else:
            result[fields[i + 1]] = None
            i += 2
    return result


def read_files(repo, ref, paths):
    """{path: text} at ``ref`` through one ``git cat-file --batch``; a path missing there is left out."""
    paths = sorted(paths)
    if not paths:
        return {}
    request = "".join(f"{ref}:{p}\n" for p in paths).encode("utf-8")
    result = subprocess.run(["git", "-C", repo, "cat-file", "--batch"], input=request, capture_output=True,
                            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if result.returncode:
        raise GitError(f"git cat-file: {result.stderr.decode('utf-8', 'replace').strip()}")
    data, offset, files = result.stdout, 0, {}
    for path in paths:
        end = data.index(b"\n", offset)
        header = data[offset:end].split()
        offset = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            files[path] = data[offset:offset + size].decode("utf-8", "replace")
            offset += size + 1
    return files


def _words(symbols):
    return re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, sorted(symbols))) + r")(?![A-Za-z0-9_])")


def blob_ids(repo, ref, paths):
    """{path: blob id} at ``ref`` for the paths that exist there."""
    ids = {}
    for line in git_paths(repo, ["ls-tree", ref], paths).splitlines():
        meta, _, path = line.partition("\t")
        ids[path] = meta.split()[2]
    return ids


def present_symbols(repo, tip, file_symbols):
    """{(path, symbol)} found as whole words at ``tip`` for {path: symbols}."""
    found = set()
    for path, text in read_files(repo, tip, [p for p, group in file_symbols.items() if group]).items():
        found.update((path, m) for m in set(_words(file_symbols[path]).findall(text)))
    return found


def where_symbols(repo, tip, symbols):
    """{symbol: files at ``tip`` that name it} in Blender's source folders."""
    args = ["grep", "-l", "-F", *[a for s in sorted(symbols) for a in ("-e", s)], tip, "--",
            "source", "intern", "scripts/startup", "scripts/modules"]
    files = [line[len(tip) + 1:] for line in git(repo, *args, ok=(0, 1)).splitlines()]
    hits = defaultdict(set)
    for path, symbol in present_symbols(repo, tip, {f: symbols for f in files}):
        hits[symbol].add(path)
    return hits


def rna_definitions(repo, ref, paths):
    """{(struct, property)} that ``paths`` define at ``ref``; ``(struct, "")`` stands for the struct itself. A property a
    file defines before any struct (in helpers given ``srna``, as the node files do) has the struct ``None``."""
    if not paths:
        return set()
    out = git_paths(repo, ["grep", "-E", r"RNA_def_struct(_ptr)?[[:space:]]*\(|RNA_def_property[[:space:]]*\(",
                           ref], paths, ok=(0, 1))
    found, struct, current = set(), None, None
    for line in out.splitlines():
        path, _, line = line[len(ref) + 1:].partition(":")
        if path != current:
            current, struct = path, None
        if match := _RNA_STRUCT.search(line):
            struct = match.group(1)
            found.add((struct, ""))
        elif match := _RNA_PROP.search(line):
            found.add((struct, match.group(1)))
    return found


def rna_uses(repo, head, removed, change):
    """{(struct, property): ["path:line", ...]} where code lines Angelo wrote name a removed property."""
    names = sorted({p for _, p in removed if len(p) > 2})
    sources = [p for p in change.present if p.rpartition(".")[2] in CODE_SUFFIXES]
    if not names or not sources:
        return {}
    # Fixed strings keep git grep fast with many names; the exact pattern is checked here.
    alternatives = "|".join(map(re.escape, names))
    # Python names RNA as attributes or whole strings; C and C++ only as whole strings (``.name`` there is a struct
    # member, and a word inside a longer string is prose).
    quoted = re.compile(r"\"(" + alternatives + r")\"|'(" + alternatives + r")'")
    attribute = re.compile(r"\.(" + alternatives + r")(?![A-Za-z0-9_])")
    prefetch(repo, head, sources)
    args = ["grep", "-I", "-n", "-F", *[a for n in names for a in ("-e", n)], head]
    uses = defaultdict(list)
    for line in git_paths(repo, args, sources, ok=(0, 1)).splitlines():
        path, number, text = line[len(head) + 1:].split(":", 2)
        own = change.own_lines.get(path, set())
        if own is not None and int(number) not in own:
            continue
        found = {n for groups in quoted.findall(text) for n in groups if n}
        if path.endswith(".py"):
            found |= set(attribute.findall(text))
        for name in found:
            for key in removed:
                if key[1] == name:
                    uses[key].append(f"{path}:{number}")
    return uses


@dataclasses.dataclass
class LineReport:
    line: Line
    commits: list
    moved: dict  # Angelo path -> new path, or None when deleted
    collisions: list  # files Angelo adds that this line adds at the same path
    vanished: dict  # (Angelo path, function) -> files that name it at this tip (empty: nowhere, or not searched)
    rna_same: list  # (struct, property) Angelo adds that this line defines too
    rna_gone: dict  # (struct, property) this line removes -> where Angelo's own lines use the name
    carried: list = dataclasses.field(default_factory=list)  # Angelo files identical to this line's tip


def analyse(repo, change, upstream, head, base, attributes):
    current = {p: p for p in change.files}  # path on the line being read -> Angelo path
    prefetch(repo, head, change.rna_files + change.added)
    rna_ours = rna_definitions(repo, head, change.rna_files) - rna_definitions(
        repo, base or upstream.fork_base, change.rna_files)
    flagged, reports = set(), []
    for line in upstream.lines:
        moved = {}
        for old, new in moves(repo, line.since, line.tip).items():
            if old in current:
                ours = current.pop(old)
                moved[ours] = new
                if new:
                    current[new] = ours
        commits = upstream_commits(repo, line, current, attributes, head) if current else []
        ours = blob_ids(repo, head, change.added)
        theirs = blob_ids(repo, line.tip, change.added)
        # The same content at the same path is upstream's file that Angelo carries, not a collision.
        collisions = [p for p in change.added if p in theirs and theirs[p] != ours.get(p) and p not in flagged]
        live = {c: change.files[a] for c, a in current.items() if change.files[a]}
        found = present_symbols(repo, line.tip, live)
        gone = sorted((c, s) for c, group in live.items() for s in group
                      if (c, s) not in found and (current[c], s) not in flagged)
        hits = where_symbols(repo, line.tip, {s for _, s in gone}) if gone else {}
        vanished = {(current[c], s): sorted(hits.get(s, ()))[:4] for c, s in gone}
        flagged.update(vanished)
        rna_changed = git(repo, "diff", "--name-only", line.since, line.tip, "--", RNA_FILES).split()
        rna_old = rna_definitions(repo, line.since, rna_changed)
        rna_new = rna_definitions(repo, line.tip, rna_changed)
        rna_tip = rna_definitions(repo, line.tip, [c for c, a in current.items() if a in change.rna_files])
        rna_same = sorted((rna_ours & (rna_tip | rna_new)) - flagged, key=str)
        removed = {k for k in rna_old - rna_new if k[1] and k[0]}
        if removed:
            # A name another struct still defines is ambiguous in a use; only names RNA no longer has are searched.
            still = {p for _, p in rna_definitions(repo, line.tip, [RNA_FILES])}
            removed = {k for k in removed if k[1] not in still}
        rna_gone = dict(rna_uses(repo, head, removed, change))
        flagged.update(collisions, rna_same)  # each finding is reported on the first line that has it
        # A file identical to this line's tip holds no Angelo change of its own (backports only): later lines are
        # upstream's to bring, so it is not compared again.
        tip_ids = blob_ids(repo, line.tip, current)
        head_ids = blob_ids(repo, head, list(current.values()))
        carried = sorted(a for c, a in current.items() if c in tip_ids and tip_ids[c] == head_ids.get(a))
        for path in [c for c, a in current.items() if a in carried]:
            del current[path]
        reports.append(LineReport(line, commits, moved, collisions, vanished, rna_same, rna_gone, carried))
    return reports


# ---- Rendering ----

def _count(number, noun):
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _rna_name(struct, prop):
    return f"{struct or '?'}.{prop}" if prop else struct


def _shared(change, path, commit):
    return sorted(change.files.get(path, set()) & commit.files.get(path, set()))


def render(change, upstream, reports, fmt="text", limit=12):
    md = fmt != "text"
    out = []
    emit = out.append
    item, sub = ("- ", "  - ") if md else ("  ", "      ")
    code = (lambda s: f"`{s}`") if md else (lambda s: s)
    title = f"Upstream overlap for {change.label}"
    emit(f"### {title}" if md else title)
    emit(f"{_count(len(change.files), 'Blender-side file')} changed and {len(change.added)} added in Blender's folders; fork base "
         f"{upstream.base_version} ({upstream.fork_base[:11]}). Functions are read from git's hunk headers "
         "(approximate).")
    for note in upstream.notes:
        emit(f"{item}Note: {note}")
    if not change.files and not change.added and not change.rna_files:
        emit("No Blender-side files: nothing to compare.")
        return "\n".join(out) + "\n"
    for report in reports:
        line = report.line
        by_file = defaultdict(list)
        for commit in report.commits:
            for path in commit.files:
                by_file[path].append(commit)
        heading = (f"{line.title}: {_count(line.count, 'commit')}; {len(report.commits)} touch "
                   f"{_count(len(by_file), 'file')} of this change")
        emit("")
        emit(f"#### {heading}" if md else heading)
        for path, new in sorted(report.moved.items()):
            emit(f"{item}{'MOVED' if new else 'DELETED'} {code(path)}" + (f" → {code(new)}" if new else ""))
        if report.carried:
            verb = "equals" if len(report.carried) == 1 else "equal"
            emit(f"{item}CARRIED: {_count(len(report.carried), 'file')} {verb} this tip (backports only, no Angelo "
                 f"change of its own): " + ", ".join(map(code, report.carried[:limit or None]))
                 + (f" (+{len(report.carried) - limit})" if limit and len(report.carried) > limit else ""))
        for path in report.collisions:
            emit(f"{item}SAME PATH: upstream adds {code(path)} too")
        for (path, symbol), where in report.vanished.items():
            emit(f"{item}GONE: {code(symbol)} is no longer in {code(path)}"
                 + (f"; now named in {', '.join(map(code, where))}" if where else ""))
        for struct, prop in report.rna_same:
            emit(f"{item}RNA: upstream defines {code(_rna_name(struct, prop))} too")
        for (struct, prop), uses in sorted(report.rna_gone.items()):
            emit(f"{item}RNA: {code(_rna_name(struct, prop))} is removed or renamed; Angelo's lines name it (a use, or the same word for something else) at "
                 + ", ".join(map(code, uses[:3])) + (f" (+{len(uses) - 3})" if len(uses) > 3 else ""))
        # Files with upstream work Angelo does not carry yet come first, those touching Angelo's functions first.
        ranked = sorted(by_file.items(), key=lambda kv: (
            -sum(bool(_shared(change, kv[0], c)) for c in kv[1] if not c.in_angelo),
            -sum(not c.in_angelo for c in kv[1]), kv[0]))
        for path, commits in ranked[:limit or None]:
            hits = sum(bool(_shared(change, path, c)) for c in commits)
            carried = sum(c.in_angelo for c in commits)
            emit(f"{item}{code(path)}: {_count(len(commits), 'commit')}"
                 + (f", {hits} in functions Angelo changes" if hits else "")
                 + (f", {carried} already in Angelo" if carried else ""))
            for commit in sorted(commits, key=lambda c: (c.in_angelo, not _shared(change, path, c)))[:3]:
                shared = _shared(change, path, commit)
                emit(f"{sub}{commit.sha[:11]} {commit.date} {commit.subject[:90]}"
                     + (f" [{', '.join(shared[:3])}]" if shared else "") + (" (in Angelo)" if commit.in_angelo else ""))
        if limit and len(ranked) > limit:
            emit(f"{item}… {len(ranked) - limit} more files (--limit 0 lists all)")
    notes = sorted({r.line.version.notes_url for r in reports
                    if r.line.version and r.line.version.series != upstream.base_version.series})
    if notes:
        emit("")
        emit("Release notes to read: " + " ".join(notes))
    return "\n".join(out) + "\n"


def annotations(change, reports):
    """GitHub workflow commands: one notice per changed file that newer upstream moved or touched."""
    lines = []
    for path in sorted(change.files):
        parts = []
        for report in reports:
            if path in report.moved:
                new = report.moved[path]
                parts.append(f"{report.line.title}: " + (f"moved to {new}" if new else "deleted"))
            elif touching := [c for c in report.commits if path in c.files]:
                parts.append(f"{report.line.title}: {_count(len(touching), 'commit')}")
        if parts:
            lines.append(f"::notice file={path},title=Newer upstream Blender::" + "; ".join(parts))
    return "".join(line + "\n" for line in lines)


def render_watch(upstream):
    out = [f"Angelo's fork base: {upstream.base_version} ({upstream.fork_base[:11]})"]
    out += [f"  note: {note}" for note in upstream.notes]
    for line in upstream.lines:
        out.append(f"  {line.role:<8} {line.branch:<22} {line.version or '?'}: {_count(line.count, 'commit')} past "
                   f"{line.since[:11]}, tip {line.tip[:11]}")
    if upstream.tags:
        out.append("  release tags newer than the fork base: " + ", ".join(upstream.tags))
    if upstream.branches:
        out.append("  release branches newer than the fork base: " + ", ".join(upstream.branches))
    for major, minor in sorted({l.version.series for l in upstream.lines if l.version}
                               - {upstream.base_version.series}):
        out.append(f"  release notes {major}.{minor}: {NOTES_URL.format(major, minor)}")
    return "\n".join(out) + "\n"


def early_command(args, head, upstream):
    import upstream_early
    if args.command == "backport":
        commits = [rev(args.repo, c) or c for c in args.commits]
        results = upstream_early.backport(git, args.repo, head, upstream.fork_base, commits)
        sys.stdout.write(upstream_early.render_backport(results, args.branch))
        return 0
    newest = [l for l in upstream.lines if l.role == "release"] or [l for l in upstream.lines if l.role == "series"]
    target = rev(args.repo, args.target) if args.target else (newest[-1].tip if newest else None)
    if target is None:
        raise GitError(f"unknown target {args.target or 'release branch'}")
    version = read_version(args.repo, target)
    series = "{}.{}".format(*version.series) if version else "?"
    try:
        items = upstream_early.load_registry(*([args.registry] if args.registry else []))
    except (OSError, ValueError) as error:
        raise GitError(str(error)) from error
    report = upstream_early.compare(git, is_ancestor, args.repo, items, target, series, upstream.fork_base)
    sys.stdout.write(upstream_early.render_compare(report, f"{version or '?'} ({target[:11]})", series))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("overlap", "watch", "fetch", "backport", "early"):
        command = sub.add_parser(name)
        command.add_argument("--repo", default=".")
        command.add_argument("--remote", default=REMOTE)
        command.add_argument("--branch", default="HEAD", help="the Angelo branch or commit (default HEAD)")
        command.add_argument("--offline", action="store_true", help="use already-fetched upstream refs only")
    sub.choices["fetch"].add_argument("--shallow", action="store_true",
                                      help="shallow, back to the previous series' first release (CI clones); "
                                           "prints the oldest commit date fetched")
    sub.choices["backport"].add_argument("commits", nargs="+", help="upstream commits to check against --branch")
    sub.choices["early"].add_argument("--target", help="the upstream ref Angelo moves to (default: the newest "
                                                       "release branch)")
    sub.choices["early"].add_argument("--registry", default=None, help=argparse.SUPPRESS)
    overlap = sub.choices["overlap"]
    overlap.add_argument("--against", default="origin/main",
                         help="where the branch left from (default origin/main), or fork-base")
    overlap.add_argument("--format", choices=("text", "markdown", "github"), default="text")
    overlap.add_argument("--limit", type=int, default=12, help="files listed per upstream line (0: all)")
    overlap.add_argument("paths", nargs="*", help="only these paths (alone, the files as they are at the branch)")
    args = parser.parse_args(argv)
    try:
        head = rev(args.repo, args.branch)
        if head is None:
            raise GitError(f"unknown ref {args.branch}")
        if args.command == "fetch":
            for note in fetch_upstream(args.repo, head, args.remote, args.shallow):
                print(f"upstream_overlap: {note}", file=sys.stderr)
            if args.shallow:  # the date the branch's own history has to reach for the fork base to be found
                dates = git(args.repo, "log", "--format=%cs", f"--remotes={args.remote}/*").split()
                print(min(dates, default=""))
            return 0
        upstream = resolve_upstream(args.repo, head, args.remote, args.offline)
        if args.command == "watch":
            sys.stdout.write(render_watch(upstream))
            return 0
        if args.command in ("backport", "early"):
            return early_command(args, head, upstream)
        base = None
        if not args.paths or args.branch != "HEAD":
            against = upstream.fork_base if args.against == "fork-base" else rev(args.repo, args.against)
            if against is None:
                raise GitError(f"unknown ref {args.against}")
            base = git(args.repo, "merge-base", against, head).strip()
        with tempfile.NamedTemporaryFile("w", suffix=".gitattributes", delete=False) as handle:
            handle.write(FUNCTION_ATTRIBUTES)
        try:
            change = angelo_change(args.repo, head, base, args.paths, upstream.fork_base, handle.name)
            reports = analyse(args.repo, change, upstream, head, base, handle.name)
        finally:
            os.unlink(handle.name)
    except GitError as error:
        print(f"upstream_overlap: {error}", file=sys.stderr)
        return 2
    text = render(change, upstream, reports, args.format, args.limit)
    if args.format == "github":
        sys.stdout.write(annotations(change, reports))
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text)
            return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
