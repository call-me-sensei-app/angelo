# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adding a platform's installer to a version already published (publish_installers.py --add-to-version).

Dependency-free and offline, on the loopback stand-in for R2 of test_native_publish_installers (never a real
bucket). The files are named for the version this checkout builds, the only one an addition takes. Covered, in
both orders (the Windows installer after the disk image, and the other way round): the added platform's
``<installer>.sha256`` goes up first, listing its installer and its deltas, and ``SHA256SUMS`` is never rewritten;
another version than the checkout's is refused; no existing object is ever overwritten and a refused plan sends no
write (the same platform twice, a checksum file already there, a version not published, every state the
verification rejects: a file covered twice, a listed file missing, an installer no checksum covers); a publish cut
off at any write is refused with every object it left named, and publishes once they are deleted; a checksum
file that appears after the check stops the addition at its first write; and a first publish that gives every
installer its own ``.sha256`` before ``SHA256SUMS``. test_native_publish_add_verify covers the verification,
test_native_publish_checksum_backfill the missing files of versions published before 1.0.6.
"""

import hashlib
import io
import json
import sys
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_installers as tool  # noqa: E402
import published_files  # noqa: E402
import release_downloads as layout  # noqa: E402
from test_native_publish_installers import ENVIRON, Case, msi_signing  # noqa: E402

VERSION = layout.CHECKOUT_VERSION
FLAGS = {"macos-arm64": "--dmg", "windows-x64": "--msi"}
NAMES = {platform: layout.installer_name(VERSION, platform) for platform in FLAGS}
DMG, MSI = NAMES["macos-arm64"], NAMES["windows-x64"]
THIRD = {"linux-x64": (".AppImage", "application/octet-stream")}   # A platform the layout may gain.
# Other spellings of a version (leading zeros, Arabic-Indic and full-width digits, white space): never published.
MISSPELLED = ("01.0.0", "1.00.0", "1.0.01", "١.0.0", "1.0.１", "1.0.0 ", " 1.0.0", "1.0", "1.0.0.0")


def key(name):
    return layout.object_key(VERSION, name)


def shifted(version, step):
    """A version other than ``version``: the next major one (step 1) or an earlier one (step -1)."""
    major, minor, patch = (int(part) for part in version.split("."))
    if step > 0:
        return "%d.0.0" % (major + 1)
    return "%d.%d.%d" % ((major, minor, patch - 1) if patch else (major, minor - 1, 0) if minor else (major - 1, 0, 0))


def make_delta(folder, version):
    """A delta for ``version`` as check_delta reads it (its description only)."""
    source = shifted(version, -1)
    path = folder / layout.delta_name(source, version, "macos-arm64")
    description = json.dumps({"format": tool.DELTA_FORMAT, "from": source, "to": version}).encode()
    with tarfile.open(path, "w:xz") as archive:
        info = tarfile.TarInfo("delta.json")
        info.size = len(description)
        archive.addfile(info, io.BytesIO(description))
    return path


class AddCase(Case):
    def setUp(self):
        super().setUp()
        self.dmg = self.dmg.rename(self.folder / DMG)
        self.msi = self.msi.rename(self.folder / MSI)
        self.write_receipt(self.msi)
        msi_signing.write(self.msi, "signed")

    def path(self, platform):
        return {"macos-arm64": self.dmg, "windows-x64": self.msi}[platform]

    def run_platforms(self, platforms, *extra, **options):
        argv = ["--version", VERSION]
        for platform in platforms:
            argv += [FLAGS[platform], str(self.path(platform))]
            if platform == "windows-x64":
                argv += ["--msi-signing", "signed"]
        return self.run_tool(*argv, *extra, **options)

    def verify_only(self, platforms, *extra):
        self.r2.log.clear()
        code, _ = self.run_platforms(platforms, "--verify-only", *extra, environ={}, host="linux")
        self.assertEqual(self.r2.log, [], "public reads only")
        return code

    def served(self, name):
        return published_files.served(self.r2.target, VERSION, name)

    def digest(self, platform):
        return hashlib.sha256(self.path(platform).read_bytes()).hexdigest()

    def put_object(self, name, data):
        self.r2.objects[key(name)] = (data, layout.headers(name))

    def record_public(self):
        """Every request the custom domain gets from now on, ``(method, key)``, in order."""
        timeline, public = [], self.r2.public

        def recorded(handler, name):
            timeline.append((handler.command, name))
            return public(handler, name)

        self.r2.public = recorded
        return timeline

    @staticmethod
    def named_left(errors):
        """The keys a refusal names after "A publish cut off part way left", parsed exactly."""
        text = errors.split("A publish cut off part way left ", 1)[1].split(": delete exactly these", 1)[0]
        return sorted(text.split(", "))

    @staticmethod
    def named_written(errors):
        """The keys a stopped run says it wrote ("This run wrote ... before it stopped"), parsed exactly."""
        if "This run wrote " not in errors:
            return []
        return sorted(errors.split("This run wrote ", 1)[1].split(" before it stopped", 1)[0].split(", "))

    def refused_without_writes(self, words, platforms, *extra):
        """The run fails naming ``words``, sends no PUT and leaves every object exactly as it was."""
        before = dict(self.r2.objects)
        self.r2.log.clear()
        code, _ = self.run_platforms(platforms, *extra)
        self.assertEqual(code, 1)
        self.assertIn(words, self.errors)
        self.assertEqual(self.puts(), [], "a refused plan writes nothing")
        self.assertEqual(self.r2.objects, before)


class AddInstallerTests(AddCase):
    def test_a_published_version_gains_a_platform_with_its_own_checksum_file(self):
        for first, added in (("macos-arm64", "windows-x64"), ("windows-x64", "macos-arm64")):
            with self.subTest(first=first, added=added):
                self.r2.objects.clear()
                self.assertEqual(self.run_platforms([first])[0], 0, self.errors)
                sums = self.r2.objects[key(layout.SUMS)]
                self.r2.log.clear()
                code, out = self.run_platforms([added], "--add-to-version")
                self.assertEqual(code, 0, self.errors)
                own = NAMES[added] + ".sha256"
                self.assertEqual(self.puts(), [key(own), key(NAMES[added])], "the checksum file names what follows")
                for method, _, headers in self.r2.log:
                    if method == "PUT":
                        self.assertEqual(headers["If-None-Match"], "*")
                self.assertEqual(self.r2.objects[key(layout.SUMS)], sums, "SHA256SUMS is never rewritten")
                self.assertEqual(self.r2.objects[key(own)], (("%s  %s\n" % (self.digest(added), NAMES[added]))
                                                             .encode(), layout.headers(layout.SUMS)))
                result = json.loads(out)
                self.assertEqual(sorted(result["checksum_files"]),
                                 sorted(self.served(NAMES[p] + ".sha256") for p in (first, added)))
                self.assertEqual(result["website_sha256"], {tool.WEBSITE_KEYS[p]: self.digest(p)
                                                            for p in (first, added)}, "every installer it has")
                self.assertEqual(result["feed_downloads"], ["%s=%s" % (added, self.path(added))])
                self.assertEqual(len(result["verified"]), 5, "SHA256SUMS, both installers, both checksum files")
                self.assertEqual(self.verify_only([first, added]), 0, self.errors)

    def test_a_delta_goes_into_its_installers_checksum_file(self):
        delta = make_delta(self.folder, VERSION)
        self.assertEqual(self.run_platforms(["windows-x64"])[0], 0, self.errors)
        self.r2.log.clear()
        self.assertEqual(self.run_platforms(["macos-arm64"], "--add-to-version", "--delta", str(delta))[0], 0,
                         self.errors)
        self.assertEqual(self.puts(), [key(DMG + ".sha256"), key(DMG), key(delta.name)])
        digest = hashlib.sha256(delta.read_bytes()).hexdigest()
        self.assertEqual(self.r2.objects[key(DMG + ".sha256")][0].decode(),
                         layout.sums_text({DMG: self.digest("macos-arm64"), delta.name: digest}))

    def test_only_the_version_this_checkout_builds_takes_an_addition(self):
        """A closed version (an older one, or any other) never gains a platform, whatever its layout."""
        self.put_object(DMG, self.dmg.read_bytes())
        self.put_object(layout.SUMS, layout.sums_text({DMG: self.digest("macos-arm64")}).encode())
        timeline = self.record_public()
        for label in (shifted(VERSION, 1), shifted(VERSION, -1)):
            with self.subTest(checkout=label), mock.patch.object(layout, "CHECKOUT_VERSION", label):
                self.refused_without_writes("only the version this checkout builds (%s), not %s" % (label, VERSION),
                                            ["windows-x64"], "--add-to-version")
                self.assertEqual((self.r2.log, timeline), ([], []), "refused before any request, S3 or public")
                self.assertEqual(self.run_platforms(["windows-x64"], "--add-to-version", "--dry-run")[0], 1)
                self.assertEqual((self.r2.log, timeline), ([], []))
                self.assertEqual(self.verify_only(["macos-arm64"], "--add-to-version"), 0, "verifying stays open")
                del timeline[:]

    def test_an_identity_file_without_a_version_refuses_only_an_addition_and_cleanly(self):
        with mock.patch.object(layout, "CHECKOUT_VERSION", None):
            self.refused_without_writes("angelo_identity.json has no version.label", ["windows-x64"],
                                        "--add-to-version")
            self.assertEqual(self.r2.log, [])
            self.assertNotIn("Traceback", self.errors)
            self.assertEqual(self.run_platforms(["windows-x64"], "--dry-run")[0], 0, "a first publish needs none")

    def test_the_same_platform_twice_is_refused(self):
        for platform, other in (("macos-arm64", "windows-x64"), ("windows-x64", "macos-arm64")):
            with self.subTest(first=platform):
                self.r2.objects.clear()
                self.assertEqual(self.run_platforms([platform])[0], 0, self.errors)
                self.refused_without_writes("already published, and never overwritten: %s" % key(NAMES[platform]),
                                            [platform], "--add-to-version")
                self.refused_without_writes("already published, and never overwritten", [platform])
                self.assertEqual(self.run_platforms([other], "--add-to-version")[0], 0, self.errors)
                self.refused_without_writes("never overwritten: %s, %s" % (key(NAMES[other]),
                                                                           key(NAMES[other] + ".sha256")),
                                            [other], "--add-to-version")

    def test_no_existing_object_is_overwritten_and_a_refused_plan_writes_nothing(self):
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        self.refused_without_writes("never overwritten: %s, " % key(layout.SUMS), ["windows-x64"])
        self.assertIn("SHA256SUMS is written once; to add another platform's installer to the version, pass "
                      "--add-to-version", self.errors)
        self.r2.objects[key(MSI + ".sha256")] = (b"left behind", {})   # Not even a checksum file.
        self.refused_without_writes("%s: " % key(MSI + ".sha256"), ["windows-x64"], "--add-to-version")
        self.r2.objects.clear()
        self.refused_without_writes("is not published (no SHA256SUMS)", ["windows-x64"], "--add-to-version")
        self.put_object(DMG, self.dmg.read_bytes())   # A first publish cut short: no first publish of another.
        self.refused_without_writes("stopped before its SHA256SUMS", ["windows-x64"])
        for platform in FLAGS:   # A .sha256 without SHA256SUMS: no first publish, of its platform or another.
            for published in FLAGS:
                with self.subTest(checksum=platform, published=published):
                    self.r2.objects.clear()
                    self.put_object(NAMES[platform] + ".sha256", layout.sums_text({NAMES[platform]: "4" * 64}).encode())
                    self.refused_without_writes("never overwritten: %s" % key(NAMES[platform] + ".sha256"),
                                                [published])

    def test_a_sha256sums_gone_after_the_writes_is_named(self):
        upload = tool.upload

        def deleted_after_it_went_up(bucket, item, data=None):
            upload(bucket, item, data)
            if item.name == layout.SUMS:
                del self.r2.objects[item.key]

        with mock.patch.object(tool, "upload", deleted_after_it_went_up):
            self.assertEqual(self.run_platforms(["macos-arm64"])[0], 1)
        self.assertIn("after this run's writes (did another publish of it run at the same time?):\n  "
                      "SHA256SUMS is not published", self.errors)

    def test_every_state_the_verification_rejects_is_refused_before_any_write(self):
        sha = self.digest("macos-arm64")
        other = "%s-%s-linux-x64.AppImage" % (layout.PRODUCT, VERSION)
        states = {   # Each, written beside a first publish of the disk image: what it is and the words refused.
            "covered twice": ({DMG + ".sha256": layout.sums_text({DMG: "4" * 64})},
                              "%s is covered twice, by SHA256SUMS and by %s.sha256, with different checksums"
                              % (DMG, DMG)),
            "a checksum file without its installer": (
                {other + ".sha256": layout.sums_text({other: "1" * 64})},
                "%s.sha256 lists %s, which is not published" % (other, other)),
            "SHA256SUMS listing a missing file": ({layout.SUMS: layout.sums_text({DMG: sha, other: "2" * 64})},
                                                  "SHA256SUMS lists %s, which is not published" % other),
            "an installer no checksum covers": ({other: "cut off"}, "%s is published without a checksum file" % other),
            "a checksum file listing a stranger": ({other + ".sha256": layout.sums_text({other: "3" * 64, DMG: sha})},
                                                   "must list %s and only its platform's deltas" % other),
        }
        with mock.patch.dict(layout.INSTALLERS, THIRD):
            self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
            published = dict(self.r2.objects)
            for label, (objects, words) in states.items():
                with self.subTest(state=label):
                    self.r2.objects = dict(published)
                    for name, text in objects.items():
                        self.put_object(name, text.encode())
                    self.refused_without_writes(words, ["windows-x64"], "--add-to-version")
                    self.assertIn("not in a state its verification accepts, so nothing is added", self.errors)
                    if label != "an installer no checksum covers":   # The domain is never asked for that one.
                        self.assertEqual(self.verify_only(["macos-arm64"]), 1, "the verification rejects it too")

    def test_a_cut_off_addition_names_every_object_it_left_and_publishes_once_they_are_gone(self):
        delta = make_delta(self.folder, VERSION)
        add = (["macos-arm64"], "--add-to-version", "--delta", str(delta))
        self.assertEqual(self.run_platforms(["windows-x64"])[0], 0, self.errors)
        base = dict(self.r2.objects)
        self.r2.log.clear()
        self.assertEqual(self.run_platforms(*add)[0], 0, self.errors)
        order = self.puts()
        self.assertEqual(len(order), 3)
        for failing in order:
            with self.subTest(failing=failing):
                self.r2.objects, self.r2.fail = dict(base), {("PUT", failing): 500}
                self.assertEqual(self.run_platforms(*add)[0], 1)
                self.r2.fail = {}
                left = sorted(set(self.r2.objects) - set(base))
                self.assertEqual(self.named_written(self.errors), left, "the stopped run names what it wrote")
                if left:
                    self.assertEqual(self.named_left(self.errors), left, "and what the bucket shows it left")
                    self.assertEqual(self.verify_only(["windows-x64"]), 1, "the cut-off is found")
                    for rerun in (add, (["macos-arm64"], "--add-to-version")):
                        self.refused_without_writes("A publish cut off part way left", *rerun)
                        self.assertEqual(self.named_left(self.errors), left, "every one, and only those")
                    for name in left:
                        del self.r2.objects[name]
                self.assertEqual(self.run_platforms(*add)[0], 0, self.errors)
                self.assertEqual(self.verify_only(["windows-x64"]), 0, self.errors)

    def test_reading_the_bucket_again_after_a_failed_write_never_hides_the_failure(self):
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        self.r2.fail[("PUT", key(MSI))] = 500
        with mock.patch.object(published_files, "check_written", mock.Mock(side_effect=RuntimeError("boom"))):
            self.assertEqual(self.run_platforms(["windows-x64"], "--add-to-version")[0], 1)
        self.assertIn("PUT %s: HTTP 500" % key(MSI), self.errors)
        self.assertEqual(self.named_written(self.errors), [key(MSI + ".sha256")])
        self.assertIn("(the bucket could not be read again: RuntimeError)", self.errors)

    def test_a_checksum_file_that_appears_after_the_check_stops_the_addition_at_its_first_write(self):
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        foreign = layout.sums_text({MSI: "e" * 64}).encode()
        checked = tool.refuse_existing

        def racing(*arguments):
            absent = checked(*arguments)
            self.put_object(MSI + ".sha256", foreign)
            return absent

        self.r2.log.clear()
        with mock.patch.object(tool, "refuse_existing", racing):
            self.assertEqual(self.run_platforms(["windows-x64"], "--add-to-version")[0], 1)
        self.assertIn("appeared while publishing (HTTP 412)", self.errors)
        self.assertEqual(self.puts(), [key(MSI + ".sha256")], "no installer went up")
        self.assertEqual(self.r2.objects[key(MSI + ".sha256")][0], foreign, "not overwritten")

    def test_two_racing_first_publishes_are_caught_by_the_check_after_the_writes(self):
        """Run A (the disk image) and run B (the Windows installer) both pass the check; B's installer lands
        before A's SHA256SUMS, and B loses its SHA256SUMS with 412. A must not succeed over an uncovered MSI. B's
        own checksum file went up before its SHA256SUMS, so B leaves the version as an addition would: every file
        covered, by SHA256SUMS or by its own .sha256, which its verification accepts."""
        checked = tool.refuse_existing

        def b_publishes_its_installer_in_between(*arguments):
            absent = checked(*arguments)
            self.put_object(MSI, self.msi.read_bytes())
            return absent

        timeline = self.record_public()
        with mock.patch.object(tool, "refuse_existing", b_publishes_its_installer_in_between):
            self.assertEqual(self.run_platforms(["macos-arm64"])[0], 1)
        self.assertIn("not in a state its verification accepts, after this run's writes", self.errors)
        self.assertIn("%s is published without a checksum file" % MSI, self.errors)
        self.assertEqual(self.named_left(self.errors), [key(MSI)])
        self.assertEqual(timeline, [], "found through S3, before the custom domain is asked anything")
        self.refused_without_writes("%s is published without a checksum file" % MSI, ["windows-x64"],
                                    "--add-to-version")   # Until it is deleted, nothing is added.
        # Run B, whose check also passed before A wrote: its installer goes up, its SHA256SUMS meets A's (412).
        del self.r2.objects[key(MSI)]
        self.r2.log.clear()
        with mock.patch.object(tool, "refuse_existing", lambda *arguments: set()):
            self.assertEqual(self.run_platforms(["windows-x64"])[0], 1)
        self.assertEqual(self.puts(), [key(MSI), key(MSI + ".sha256"), key(layout.SUMS)])
        self.assertIn("%s appeared while publishing (HTTP 412)" % key(layout.SUMS), self.errors)
        self.assertEqual(self.named_written(self.errors), sorted([key(MSI), key(MSI + ".sha256")]),
                         "B names what it wrote")
        self.assertNotIn("A publish cut off part way left", self.errors, "nothing it left is uncovered")
        self.assertEqual(timeline, [], "the domain is not asked")
        self.assertEqual(self.verify_only(list(FLAGS)), 0, self.errors)
        timeline.clear()
        third = "%s-%s-linux-x64.AppImage" % (layout.product(), VERSION)

        def another_run_leaves_an_installer_in_between(*arguments):
            absent = checked(*arguments)
            self.put_object(third, b"another run, cut off")
            return absent

        for platform in FLAGS:   # The check after the writes runs for an addition too.
            with self.subTest(added=platform), mock.patch.dict(layout.INSTALLERS, THIRD):
                self.r2.objects.clear()
                other = [p for p in FLAGS if p != platform][0]
                self.assertEqual(self.run_platforms([other])[0], 0, self.errors)
                with mock.patch.object(tool, "refuse_existing", another_run_leaves_an_installer_in_between):
                    self.assertEqual(self.run_platforms([platform], "--add-to-version")[0], 1)
                self.assertIn("after this run's writes", self.errors)
                self.assertEqual(self.named_left(self.errors), [key(third)])


class FirstPublishLayoutTests(AddCase):
    def test_a_first_publish_writes_each_installer_its_checksum_file_then_sha256sums(self):
        """Every download's checksum is at its address plus .sha256 (a person asked for
        Angelo-1.0.2-macos-arm64.dmg.sha256 and got a 404), and SHA256SUMS still lists the version, written last."""
        code, out = self.run_platforms(list(FLAGS))
        self.assertEqual(code, 0, self.errors)
        own = [DMG + ".sha256", MSI + ".sha256"]
        self.assertEqual(sorted(self.r2.objects), sorted(key(name) for name in [DMG, MSI, layout.SUMS] + own))
        self.assertEqual(self.puts(), [key(DMG), key(MSI)] + [key(name) for name in own] + [key(layout.SUMS)])
        digests = {NAMES[p]: self.digest(p) for p in FLAGS}
        self.assertEqual(self.r2.objects[key(layout.SUMS)][0], ("%s  %s\n%s  %s\n" % (
            digests[DMG], DMG, digests[MSI], MSI)).encode())
        for name in (DMG, MSI):
            self.assertEqual(self.r2.objects[key(name + ".sha256")], (("%s  %s\n" % (digests[name], name)).encode(),
                                                                      layout.headers(layout.SUMS)))
        self.assertEqual(json.loads(out)["checksum_files"], [self.served(name) for name in own])

    def test_a_first_publish_with_a_delta_lists_it_in_both(self):
        delta = make_delta(self.folder, VERSION)
        self.assertEqual(self.run_platforms(["macos-arm64"], "--delta", str(delta))[0], 0, self.errors)
        entries = {DMG: self.digest("macos-arm64"), delta.name: hashlib.sha256(delta.read_bytes()).hexdigest()}
        self.assertEqual(self.r2.objects[key(DMG + ".sha256")][0].decode(), layout.sums_text(entries))
        self.assertEqual(self.r2.objects[key(layout.SUMS)][0].decode(), layout.sums_text(entries))
        self.assertEqual(self.verify_only(["macos-arm64"]), 0, self.errors)

    def test_a_version_published_before_checksum_files_existed_still_verifies(self):
        """The objects exactly as the earlier tool wrote 1.0.0: its installers and SHA256SUMS, nothing else."""
        for platforms in (["macos-arm64"], list(FLAGS)):
            with self.subTest(platforms=platforms):
                self.r2.objects.clear()
                for platform in platforms:
                    self.put_object(NAMES[platform], self.path(platform).read_bytes())
                self.put_object(layout.SUMS, layout.sums_text({NAMES[p]: self.digest(p) for p in platforms}).encode())
                self.assertEqual(self.verify_only(platforms), 0, self.errors)

    def test_the_dry_runs_are_offline_and_name_every_object(self):
        code, out = self.run_platforms(["windows-x64"], "--add-to-version", "--dry-run", environ={})
        self.assertEqual(code, 0, self.errors)
        self.assertEqual(self.r2.log, [])
        plan = json.loads(out)
        self.assertEqual([item["key"] for item in plan["objects"]], [key(MSI), key(MSI + ".sha256")])
        self.assertEqual(plan["checksum_files"], {MSI + ".sha256": "%s  %s\n" % (self.digest("windows-x64"), MSI)})
        self.assertNotIn("sha256sums", plan)
        first = json.loads(self.run_platforms(["windows-x64"], "--dry-run", environ={})[1])
        self.assertEqual(sorted(first), ["bucket", "checksum_files", "dry_run", "objects", "sha256sums"])
        self.assertEqual(first["checksum_files"], plan["checksum_files"], "the same file in either publish")
        self.assertEqual([item["key"] for item in first["objects"]],
                         [key(MSI), key(MSI + ".sha256"), key(layout.SUMS)])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool.main(["--check", "--add-to-version"], target=self.r2.target, environ=ENVIRON)


class LayoutTests(unittest.TestCase):
    def test_checksum_names_deltas_and_grouping(self):
        self.assertEqual(layout.checksum_name(MSI), MSI + ".sha256")
        delta = layout.delta_name(shifted(VERSION, -1), VERSION, "macos-arm64")
        for name in (layout.SUMS, MSI + ".sha256", "notes.txt", delta):
            with self.assertRaises(layout.LayoutError):
                layout.checksum_name(name)
        self.assertEqual(layout.headers(MSI + ".sha256"), layout.headers(layout.SUMS))
        for name in ("notes.txt.sha256", delta + ".sha256"):
            with self.assertRaises(layout.LayoutError):
                layout.headers(name)
        self.assertTrue(layout.is_delta_of(delta, VERSION, "macos-arm64"))
        others = ((delta, "windows-x64"), (delta.replace("-to-", "-to-9"), "macos-arm64"), (DMG, "macos-arm64"),
                  (layout.delta_name(VERSION, shifted(VERSION, 1), "macos-arm64"), "macos-arm64"))
        for name, platform in others:
            self.assertFalse(layout.is_delta_of(name, VERSION, platform), name)
        for source in ("x", shifted(VERSION, 1), VERSION, "", *MISSPELLED):   # A bad middle, each refused.
            name = "%s-%s-to-%s-macos-arm64.delta.tar.xz" % (layout.product(), source, VERSION)
            self.assertFalse(layout.is_delta_of(name, VERSION, "macos-arm64"), ascii(name))
            if source not in (shifted(VERSION, 1), VERSION):   # Spelled right, refused for its order instead.
                self.assertIsNone(tool.DELTA_NAME.fullmatch(name), ascii(name))
        installer = published_files.Item(VERSION, DMG, None, 1, "a" * 64, "macos-arm64")
        patch = published_files.Item(VERSION, delta, None, 1, "b" * 64, "macos-arm64")
        patch.source = shifted(VERSION, -1)
        msi = published_files.Item(VERSION, MSI, None, 1, "c" * 64, "windows-x64")
        files = tool.checksum_files(VERSION, [installer, msi, patch], b"", add=True)
        self.assertEqual([(entry.name, data.decode()) for entry, data in files],
                         [(DMG + ".sha256", layout.sums_text({DMG: "a" * 64, delta: "b" * 64})),
                          (MSI + ".sha256", layout.sums_text({MSI: "c" * 64}))])

    def test_a_version_has_one_spelling(self):
        """ASCII digits, no leading zero: app_version.parse alone takes these other spellings of a version."""
        for spelling in MISSPELLED:
            with self.subTest(spelling=ascii(spelling)):
                with self.assertRaises(layout.LayoutError):
                    layout.check_version(spelling)
                with self.assertRaises(layout.LayoutError):
                    layout.installer_name(spelling, "macos-arm64")
                with self.assertRaises(layout.LayoutError):
                    layout.delta_name(spelling, "99.0.0", "macos-arm64")
        for spelling in ("0.0.0", "1.0.0", "10.20.300", VERSION):
            self.assertEqual(layout.check_version(spelling), spelling)

    def test_a_malformed_identity_file_fails_only_what_needs_the_missing_field(self):
        """Missing fields read as None, never a KeyError at import; only a mode that needs one refuses, cleanly."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "identity.json"
        for text, expected in (('{"product": {"name": "Angelo"}}', ("Angelo", None)),
                               ('{"version": {"label": "1.0.1"}}', (None, "1.0.1")),
                               ('{"product": "Angelo", "version": []}', (None, None)),
                               ("[]", (None, None)), ("{", (None, None))):
            path.write_text(text)
            self.assertEqual(layout.load_identity(path), expected, text)
        self.assertEqual(layout.load_identity(path.with_name("absent.json")), (None, None))
        tree = Path(folder.name) / "checkout"   # The module imported for real beside an identity with no version.
        for relative in ("scripts/angelo/release_downloads.py", "scripts/addons_core/angelo_agent/app_version.py"):
            (tree / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, tree / relative)
        (tree / layout.IDENTITY_FILE).parent.mkdir(parents=True)
        (tree / layout.IDENTITY_FILE).write_text('{"product": {"name": "Angelo"}}')
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import release_downloads as layout\n"
                 "print(layout.installer_name('1.0.1', 'windows-x64'))\n"
                 "try:\n    layout.checkout_version()\nexcept layout.LayoutError as error:\n    print(error)")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(tree / "scripts/angelo")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout.splitlines(), ["Angelo-1.0.1-windows-x64.msi",
                                                      "%s has no version.label, the version this checkout builds"
                                                      % layout.IDENTITY_FILE])
        with mock.patch.object(layout, "CHECKOUT_VERSION", None):
            with self.assertRaisesRegex(layout.LayoutError, "angelo_identity.json has no version.label"):
                layout.checkout_version()
        for label in MISSPELLED:   # A label spelled another way is no version this checkout builds.
            with mock.patch.object(layout, "CHECKOUT_VERSION", label), self.assertRaises(layout.LayoutError):
                layout.checkout_version()
        with mock.patch.object(layout, "PRODUCT", None):
            with self.assertRaisesRegex(layout.LayoutError, "angelo_identity.json has no product.name"):
                layout.installer_name(VERSION, "macos-arm64")

    def test_checksum_files_are_read_only_in_the_form_written(self):
        entries = {DMG: "a" * 64, MSI: "b" * 64}
        self.assertEqual(layout.parse_sums(layout.sums_text(entries)), entries)
        line = "%s  %s\n" % ("a" * 64, MSI)
        unsorted = layout.sums_text({MSI: "b" * 64}) + layout.sums_text({DMG: "a" * 64})
        for text in ("", line[:-1], line.upper(), line[1:], line.replace("  ", " "), line.replace("  ", " *"),
                     line.replace("\n", "\r\n"), chr(0xFEFF) + line, line.replace(MSI, MSI + " "),
                     line.replace(MSI, "./" + MSI), unsorted, line * 2):
            with self.subTest(text=text):
                with self.assertRaises(layout.LayoutError):
                    layout.parse_sums(text)


if __name__ == "__main__":
    unittest.main()
