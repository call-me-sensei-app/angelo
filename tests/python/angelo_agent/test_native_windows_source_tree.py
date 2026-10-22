# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows build machine's source tree is exactly the commit sent: every name, and nothing else.

Found on the 1.0.1 release run: Windows ``tar.exe`` decoded each archive member name it read as raw bytes through the
OEM code page (CP437), so each of the commit's 12 non-ASCII paths came out as a garbled twin beside the correctly
named file the commit then wrote; ``release_checks.py source-clean`` refused the tree for those untracked files and
no MSI was built. ``source_tree.py`` now unpacks every archive, full and delta, and removes untracked files only once
HEAD and the index are provably the commit. These tests drive it, the launcher's own archive and delta commands
(``gcp_windows_source.sh``) and real git repositories, with names in several scripts and shapes that none of the
exposing paths have: the class is any name, not those 12.
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[2] / "scripts/angelo"
TOOL = SCRIPTS / "source_tree.py"
SENDING = SCRIPTS / "gcp_windows_source.sh"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import shipped_sources  # noqa: E402  (standard library only)

ENV = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_AUTHOR_NAME="Angelo Tests", GIT_AUTHOR_EMAIL="tests@angelo.invalid",
           GIT_COMMITTER_NAME="Angelo Tests", GIT_COMMITTER_EMAIL="tests@angelo.invalid", GIT_CONFIG_NOSYSTEM="1",
           GIT_CONFIG_GLOBAL=os.devnull)
# Names a Windows file system can hold, in scripts and shapes the exposing paths (Cyrillic, CJK and Arabic test
# files) do not use: Greek, Devanagari, Hebrew, Korean, Thai, emoji, Latin accents, a long name (a pax header), and
# characters that are glob syntax elsewhere. The long name's last part is over ustar's 100 bytes (a pax header), and
# each part's CP437 twin (the garbled name tar.exe left) stays within the 255 bytes a Linux file name may hold (CI):
# "ü" * 60 and "ж" * 50 made twins of 360 and 300 bytes, which only APFS and NTFS (counting characters) could create.
NAMES = ["plain.txt", "Ελληνικά/αρχείο.txt", "हिन्दी/फ़ाइल.dat", "עברית/קובץ.bin", "한국어/파일 이름.txt",
         "ไทย/ไฟล์.txt", "emoji/🙂 face.txt", "Café/naïve résumé.txt", "brackets/[x] {y} #%&'.txt",
         "deep/" + "ü" * 40 + "/" + "倀" * 34 + ".txt"]
assert len(NAMES[-1].rsplit("/", 1)[1].encode("utf-8")) > 100
assert all(len(part.encode("utf-8").decode("cp437").encode("utf-8")) <= 255 for part in NAMES[-1].split("/"))


def git(folder, *args, **kwargs):
    return subprocess.run(["git", "-C", str(folder), *args], env=ENV, check=True, capture_output=True, **kwargs).stdout


def tool(*args):
    return subprocess.run([sys.executable, "-I", "-B", str(TOOL), *map(str, args)], env=ENV, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def tracked(folder):
    return sorted(name for name in git(folder, "-c", "core.quotePath=false", "ls-files", "-z").decode("utf-8").split("\0")
                  if name)


def files(folder):
    """Every file below ``folder`` (outside .git), as /-separated names."""
    found = []
    for top, dirs, names in os.walk(folder):
        dirs[:] = [name for name in dirs if name != ".git"]
        found += [Path(top, name).relative_to(folder).as_posix() for name in names]
    return sorted(found)


def commit_files(repo, contents, message):
    for name, data in contents.items():
        path = Path(repo, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").decode().strip()


def archive(repo, target, rev="HEAD"):
    """The launcher's full send of ``rev``: git archive --format=tar.gz (gcp_windows_source.sh pack_head)."""
    git(repo, "archive", "--format=tar.gz", "-o", str(target), rev)
    return target


def commit_pack(repo, target):
    """The launcher's commit pack: the commit object and its tree, no history."""
    objects = git(repo, "rev-parse", "HEAD") + git(repo, "rev-list", "--objects", "HEAD^{tree}")
    listed = b"".join(line.split(b" ")[0] + b"\n" for line in objects.splitlines())
    Path(target).write_bytes(git(repo, "pack-objects", "--stdout", "-q", input=listed))
    return target


@unittest.skipUnless(shutil.which("git"), "needs git")
class SourceTreeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.repo = self.folder / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        contents = {name: ("%s\n" % name).encode("utf-8") for name in NAMES}
        contents[".gitignore"] = b"*.log\n"
        self.sha = commit_files(self.repo, contents, "names")
        self.machine = self.folder / "machine"

    def extract(self, *archives, deleted=None):
        extra = ["--deleted", deleted] if deleted else []
        return tool("extract", "--into", self.machine, *extra, *archives)

    def install(self, sha=None):
        sha = sha or self.sha
        done = tool("commit", "--tree", self.machine, "--commit", sha, "--pack", commit_pack(self.repo, self.folder / "c.pack"))
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_every_utf8_name_round_trips_with_no_twin(self):
        done = self.extract(archive(self.repo, self.folder / "src.tar.gz"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(files(self.machine), tracked(self.repo), "exactly the commit's names: none garbled, none extra")
        for name in NAMES:
            self.assertEqual(Path(self.machine, name).read_bytes(), ("%s\n" % name).encode("utf-8"))

    @unittest.skipIf(os.name == "nt", "the large-file archive is made on the Mac, with its tar")
    def test_the_large_file_archive_made_with_tar_round_trips_too(self):
        listed = "".join(name + "\n" for name in NAMES)
        subprocess.run(["tar", "-czf", str(self.folder / "lfs.tar.gz"), "-T", "-"], cwd=self.repo, input=listed.encode(),
                       check=True, env=dict(ENV, COPYFILE_DISABLE="1"))
        done = self.extract(self.folder / "lfs.tar.gz")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(files(self.machine), sorted(NAMES))

    def test_an_old_extractions_garbled_twins_and_stale_files_are_cleaned_once_the_tree_is_the_commit(self):
        self.assertEqual(self.extract(archive(self.repo, self.folder / "src.tar.gz")).returncode, 0)
        # What tar.exe left: each non-ASCII name decoded through CP437, beside the real file; and a file the commit
        # no longer has. An ignored file (a log) is not the commit's business and stays.
        twins = [name.encode("utf-8").decode("cp437") for name in NAMES if not name.isascii()]
        for name in twins + ["stale/removed since.txt"]:
            Path(self.machine, name).parent.mkdir(parents=True, exist_ok=True)
            Path(self.machine, name).write_text("left over\n")
        Path(self.machine, "build.log").write_text("ignored\n")
        git(self.machine, "init", "-q")
        self.install()
        self.assertIn("git does not track", "\n".join(shipped_sources.source_clean(self.machine, self.sha)))
        done = tool("clean", "--tree", self.machine, "--commit", self.sha)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("%d untracked file(s) removed" % (len(twins) + 1), done.stdout)
        self.assertEqual(files(self.machine), sorted(tracked(self.repo) + ["build.log"]))
        self.assertEqual(shipped_sources.source_clean(self.machine, self.sha), [], "release_checks.py source-clean passes")

    def test_files_no_archive_created_come_from_the_commit(self):
        self.assertEqual(self.extract(archive(self.repo, self.folder / "src.tar.gz")).returncode, 0)
        gone = Path(self.machine, NAMES[1])
        gone.unlink()
        git(self.machine, "init", "-q")
        self.install()
        self.assertEqual(gone.read_bytes(), ("%s\n" % NAMES[1]).encode("utf-8"))
        self.assertEqual(git(self.machine, "rev-parse", "HEAD").decode().strip(), self.sha)

    def test_every_commit_sent_without_its_parent_stays_shallow_so_history_walks_stop_there(self):
        # Blender's build info runs `git branch --contains` from a detached HEAD, walking every branch: an earlier
        # send's commit (still on its branch) must not lead git to a parent the machine never received.
        old = commit_files(self.repo, {"plain.txt": b"old\n"}, "old")
        self.assertEqual(self.extract(archive(self.repo, self.folder / "old.tar.gz")).returncode, 0)
        git(self.machine, "init", "-q")
        done = tool("commit", "--tree", self.machine, "--commit", old, "--pack", commit_pack(self.repo, self.folder / "o.pack"),
                    "--branch", "integration/old")
        self.assertEqual(done.returncode, 0, done.stderr)
        new = commit_files(self.repo, {"plain.txt": b"new\n"}, "new")
        self.assertEqual(self.extract(archive(self.repo, self.folder / "new.tar.gz")).returncode, 0)
        self.install(new)
        shallow = Path(self.machine, ".git", "shallow")
        self.assertEqual(shallow.read_text().split(), [old], "new's parent is held; old's is not")
        git(self.machine, "branch", "--contains", new[:12])   # exits nonzero on a missing parent
        self.assertEqual(len(git(self.machine, "log", "--all", "--format=%H").split()), 2)
        # A repository an older install left listing only its newest commit is mended by the next install.
        shallow.write_text(new + "\n")
        self.install(new)
        self.assertEqual(shallow.read_text().split(), [old])
        git(self.machine, "branch", "--contains", new[:12])

    def assert_refused(self, tree, sha, reason, survivors):
        done = tool("clean", "--tree", tree, "--commit", sha)
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("refused, nothing removed", done.stdout)
        self.assertIn(reason, done.stdout)
        for path in survivors:
            self.assertTrue(path.exists(), "%s was removed although the clean-up refused" % path)

    def test_the_clean_up_refuses_whenever_head_or_the_index_is_not_the_commit(self):
        old = self.sha
        self.assertEqual(self.extract(archive(self.repo, self.folder / "old.tar.gz")).returncode, 0)
        git(self.machine, "init", "-q")
        self.install(old)
        self.assertEqual(tool("clean", "--tree", self.machine, "--commit", old).returncode, 0)
        new = commit_files(self.repo, {"new/新しい.txt": b"sent\n", "plain.txt": b"changed\n"}, "new")
        sent = Path(self.machine, "new/新しい.txt")
        self.assertEqual(self.extract(archive(self.repo, self.folder / "new.tar.gz")).returncode, 0)
        # HEAD is still the old commit: the newly sent file is untracked there, and must survive.
        self.assert_refused(self.machine, new, "HEAD is", [sent])
        # HEAD is the new commit but the index is the old tree.
        commit_pack(self.repo, self.folder / "new.pack")
        git(self.machine, "index-pack", "--stdin", input=Path(self.folder / "new.pack").read_bytes())
        Path(self.machine, ".git", "shallow").write_text("%s\n%s\n" % (old, new))
        git(self.machine, "update-ref", "HEAD", new)
        self.assert_refused(self.machine, new, "the index is not the commit's tree", [sent])
        # HEAD and index are the new commit, but a tracked file differs from it.
        git(self.machine, "read-tree", new)
        Path(self.machine, "plain.txt").write_text("edited on the machine\n")
        Path(self.machine, "untracked here.txt").write_text("x\n")
        self.assert_refused(self.machine, new, "tracked file(s) differ", [sent, Path(self.machine, "untracked here.txt")])
        # Not the top of its own repository.
        Path(self.machine, "plain.txt").write_bytes(b"changed\n")
        self.assert_refused(self.machine / "new", new, "not the top of its own git repository", [sent])
        # Only a full commit hash is accepted.
        self.assertEqual(tool("clean", "--tree", self.machine, "--commit", new[:12]).returncode, 1)
        # Once all of it holds, the untracked file goes and the sent one stays.
        done = tool("clean", "--tree", self.machine, "--commit", new)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(sent.exists())
        self.assertFalse(Path(self.machine, "untracked here.txt").exists())

    def test_nothing_outside_the_tree_is_extracted_or_removed(self):
        outside = self.folder / "outside.txt"
        outside.write_text("keep\n")
        evil = self.folder / "evil.tar"
        with tarfile.open(evil, "w") as tar:
            info = tarfile.TarInfo("../outside.txt")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"bad"))
        done = self.extract(evil)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(outside.read_text(), "keep\n")
        for line in ("../outside.txt", "/etc/hosts", '"quoted\\303\\251.txt"'):
            listing = self.folder / "deleted.txt"
            listing.write_text(line + "\n", encoding="utf-8")
            done = self.extract(deleted=listing)
            self.assertEqual(done.returncode, 1, line)
            self.assertIn("not a relative path inside the tree", done.stderr)
        self.assertEqual(outside.read_text(), "keep\n")


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "the launcher's delta is bash and git, on the Mac")
class DeltaSendTests(unittest.TestCase):
    def test_a_delta_with_non_ascii_deletions_case_and_shape_changes_leaves_exactly_the_new_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            repo, machine, work = folder / "repo", folder / "machine", folder / "work"
            repo.mkdir()
            work.mkdir()
            git(repo, "init", "-q")
            base = commit_files(repo, {"a.txt": b"a\n", "olddir/x.txt": b"x\n", "Readme.TXT": b"r\n",
                                       "данные/старый файл.txt": b"old\n", "keep/照片 [1].png": b"v1\n",
                                       "Ünïcödé/stays.txt": b"s\n"}, "base")
            self.assertEqual(tool("extract", "--into", machine, archive(repo, folder / "base.tar.gz")).returncode, 0)
            # The next commit: a non-ASCII file deleted, one changed, one added; a case-only rename (NTFS keeps the
            # old spelling on overwrite); a folder that became a file.
            git(repo, "rm", "-q", "данные/старый файл.txt", "olddir/x.txt")
            git(repo, "rm", "-q", "--cached", "Readme.TXT")
            Path(repo, "Readme.TXT").rename(repo / "temporary")
            Path(repo, "temporary").rename(repo / "readme.txt")
            commit_files(repo, {"keep/照片 [1].png": b"v2\n", "新しい/ファイル.txt": b"new\n", "olddir": b"now a file\n",
                                "readme.txt": b"r\n"}, "next")
            script = 'set -euo pipefail; ROOT="$1"; source "$2"; cd "$3"; head="$(git rev-parse HEAD)"; delta_lists "$4" "$5" "$head"; pack_delta "$5" "$head"'
            subprocess.run(["bash", "-c", script, "delta", str(folder), str(SENDING), str(repo), base, str(work)],
                           env=ENV, check=True, capture_output=True)
            deleted = Path(work, "angelo-deleted.txt").read_text(encoding="utf-8").split()
            self.assertIn("данные/старый", " ".join(deleted), "UTF-8, not git's octal escapes")
            done = tool("extract", "--into", machine, "--deleted", work / "angelo-deleted.txt", work / "angelo-delta.tar.gz")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(files(machine), tracked(repo))
            self.assertEqual(Path(machine, "keep/照片 [1].png").read_bytes(), b"v2\n")
            self.assertFalse(Path(machine, "данные").exists(), "an emptied folder goes with its last file")

    def test_every_send_unpacks_with_the_tool_never_tar_exe(self):
        boot = (SCRIPTS / "windows_cloud_bootstrap.ps1").read_text(encoding="utf-8")
        sending = SENDING.read_text(encoding="utf-8")
        sync = sending[sending.index("cat > \"$work/sync.ps1\""):sending.index("\nPS1\n")]
        for name, text in (("windows_cloud_bootstrap.ps1", boot), ("sync.ps1", sync)):
            self.assertNotRegex(text, r"(?im)^[^#\n]*\btar(\.exe)?\s+-\w*x", "%s unpacks with tar" % name)
        # Every send, first or later, full or delta, from redo or sync, is received by source_send.py receive, which
        # unpacks with source_tree.extract.
        self.assertEqual(boot.count("Receive-Send $folder"), 1)
        self.assertIn('Receive-Send \\$folder "C:\\angelo\\src"', sync)
        receive = (SCRIPTS / "source_send.py").read_text(encoding="utf-8")
        self.assertIn("source_tree.extract(tree,", receive)
        self.assertIn('"${SOURCE_TOOLS[@]}" "$prefix/"', sending, "the tools travel in every send's folder")


if __name__ == "__main__":
    unittest.main()
