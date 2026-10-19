# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Update deltas: written by scripts/angelo/make_update_delta.py, applied by angelo_agent.update_delta.

Small stand-in bundles (an Info.plist, a "re-signed" library whose tail changed, a moved file, a new file, a
removed one, a symbolic link, file modes) are enough to exercise every rule; the real measurement on two signed
releases is in doc/angelo/updates_and_reports.rst, "Deltas".
"""

import io
import json
import os
import plistlib
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from angelo_agent import update_delta  # noqa: E402
from angelo_agent.update_delta import DeltaError  # noqa: E402
import make_update_delta  # noqa: E402

LIBRARY = os.urandom(5 * update_delta.BLOCK + 777)   # Above make_update_delta.PATCH_MIN_SIZE.


def make_app(root, version, extra=None):
    """A stand-in ``Angelo.app`` for ``version``; ``extra`` maps relative paths to bytes (None removes)."""
    app = Path(root) / "Angelo.app"
    files = {
        "Contents/Info.plist": plistlib.dumps({"CFBundleIdentifier": "studio.hyperbond.angelo",
                                               "CFBundleShortVersionString": version, "CFBundleExecutable": "Angelo"}),
        "Contents/MacOS/Angelo": b"\xcf\xfa\xed\xfe" + version.encode() * 50000,
        "Contents/Resources/lib/libbig.dylib": LIBRARY,
        "Contents/Resources/scripts/old_name.py": b"print('unchanged')\n" * 100,
        "Contents/Resources/scripts/gone.py": b"print('removed later')\n",
    }
    files.update(extra or {})
    for relative, data in files.items():
        if data is None:
            continue
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    os.chmod(app / "Contents/MacOS/Angelo", 0o755)
    os.symlink("lib/libbig.dylib", app / "Contents/Resources/libbig-link.dylib")
    return app


class DeltaCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        signed_tail = LIBRARY[:-500] + os.urandom(500)          # A re-signed library: only its signature differs.
        self.old = make_app(self.folder / "old", "1.0.0")
        self.new = make_app(self.folder / "new", "1.0.1", {
            "Contents/Resources/lib/libbig.dylib": signed_tail,
            "Contents/Resources/scripts/old_name.py": None,
            "Contents/Resources/scripts/new_name.py": b"print('unchanged')\n" * 100,   # Moved, same bytes.
            "Contents/Resources/scripts/gone.py": None,
            "Contents/Resources/scripts/added.py": b"print('new')\n",
        })
        os.chmod(self.new / "Contents/Resources/scripts/added.py", 0o600)

    def make(self, **options):
        path, report = make_update_delta.make("macos-arm64", "1.0.0", self.old, "1.0.1", self.new,
                                              self.folder / "out", **options)
        return path, report


class RoundTripTests(DeltaCase):
    def test_the_delta_rebuilds_the_new_app_exactly(self):
        path, report = self.make()
        self.assertEqual(path.name, "Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz")
        self.assertTrue(report["verified"])
        self.assertEqual(report["files"]["patch"], 1, "the re-signed library travels as a block patch")
        self.assertEqual(report["files"]["copy"], 1, "the moved file is copied from its old place")
        out = update_delta.apply(path, self.old, self.folder / "built" / "Angelo.app", "1.0.0", "1.0.1")
        self.assertEqual(update_delta.compare(out, update_delta.tree(self.new)), "")
        self.assertEqual(os.readlink(out / "Contents/Resources/libbig-link.dylib"), "lib/libbig.dylib")
        self.assertEqual(os.stat(out / "Contents/Resources/scripts/added.py").st_mode & 0o777, 0o600)
        self.assertFalse((out / "Contents/Resources/scripts/gone.py").exists())
        self.assertLess(report["size"], len(LIBRARY) // 2, "a block patch, not the whole library")

    def test_the_wrong_pair_of_versions_is_refused(self):
        path, _ = self.make()
        with self.assertRaises(DeltaError) as caught:
            update_delta.apply(path, self.old, self.folder / "built" / "Angelo.app", "1.0.0", "1.0.2")
        self.assertEqual(caught.exception.code, "version")
        self.assertFalse((self.folder / "built" / "Angelo.app").exists())

    def test_a_changed_installed_app_fails_the_delta_and_leaves_nothing(self):
        path, _ = self.make()
        (self.old / "Contents/Resources/scripts/old_name.py").write_bytes(b"edited by someone")
        with self.assertRaises(DeltaError) as caught:
            update_delta.apply(path, self.old, self.folder / "built" / "Angelo.app", "1.0.0", "1.0.1")
        self.assertEqual(caught.exception.code, "mismatch")
        self.assertFalse((self.folder / "built" / "Angelo.app").exists())

    def test_a_missing_base_file_fails_the_delta(self):
        path, _ = self.make()
        (self.old / "Contents/Resources/lib/libbig.dylib").unlink()
        with self.assertRaises(DeltaError) as caught:
            update_delta.apply(path, self.old, self.folder / "built" / "Angelo.app", "1.0.0", "1.0.1")
        self.assertEqual(caught.exception.code, "base")

    def test_an_existing_output_is_never_overwritten(self):
        path, _ = self.make()
        (self.folder / "built" / "Angelo.app").mkdir(parents=True)
        with self.assertRaises(DeltaError):
            update_delta.apply(path, self.old, self.folder / "built" / "Angelo.app", "1.0.0", "1.0.1")

    def test_the_tool_refuses_mislabelled_versions_and_an_existing_delta(self):
        with self.assertRaises(make_update_delta.DeltaToolError):
            make_update_delta.make("macos-arm64", "1.0.0", self.new, "1.0.1", self.new, self.folder / "out")
        self.make()
        with self.assertRaises(make_update_delta.DeltaToolError):
            self.make()


class HostileDeltaTests(DeltaCase):
    def write(self, entries, members=(), first=update_delta.MANIFEST, description=None):
        path = self.folder / "hostile.tar.xz"
        document = description or {"format": update_delta.FORMAT, "from": "1.0.0", "to": "1.0.1", "entries": entries}
        data = json.dumps(document).encode()
        with tarfile.open(path, "w:xz") as archive:
            info = tarfile.TarInfo(first)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            for name, payload in members:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return path

    def apply(self, path):
        return update_delta.apply(path, self.old, self.folder / "built" / "Angelo.app", "1.0.0", "1.0.1")

    def file(self, member="blobs/x", kind="blob", **extra):
        return dict({"type": "file", "mode": 0o644, "size": 1, "sha256": "0" * 64,
                     "from": dict({"kind": kind, "member": member}, **extra)})

    def test_paths_that_leave_the_bundle_are_refused(self):
        for bad in ("../escape", "/etc/passwd", "Contents/../../x", "Contents//x", "./x", "C:\\x", ""):
            with self.subTest(path=bad):
                with self.assertRaises(DeltaError):
                    self.apply(self.write({bad: self.file()}, [("blobs/x", b"x")]))
        with self.assertRaises(DeltaError):
            self.apply(self.write({"Contents": {"type": "dir", "mode": 0o755},
                                   "Contents/x": self.file(kind="copy", path="../../outside")}))
        self.assertFalse((self.folder / "built").exists() and any((self.folder / "built").iterdir()))

    def test_no_write_goes_through_a_symbolic_link(self):
        entries = {"Contents": {"type": "dir", "mode": 0o755},
                   "Contents/link": {"type": "link", "target": str(self.folder)},
                   "Contents/link/evil": self.file()}
        with self.assertRaises(DeltaError):
            self.apply(self.write(entries, [("blobs/x", b"x")]))
        self.assertFalse((self.folder / "evil").exists())

    def test_unexpected_missing_or_reused_members_are_refused(self):
        entries = {"Contents": {"type": "dir", "mode": 0o755}, "Contents/a": self.file()}
        with self.assertRaises(DeltaError):
            self.apply(self.write(entries, [("blobs/x", b"x"), ("blobs/other", b"y")]))
        with self.assertRaises(DeltaError):
            self.apply(self.write(entries))
        twice = {"Contents": {"type": "dir", "mode": 0o755},
                 "Contents/a": self.file("patches/0", "patch", path="Contents/Info.plist"),
                 "Contents/b": self.file("patches/0", "patch", path="Contents/Info.plist")}
        with self.assertRaises(DeltaError):
            self.apply(self.write(twice, [("patches/0", b"")]))

    def test_the_description_comes_first_and_has_a_known_format(self):
        with self.assertRaises(DeltaError):
            self.apply(self.write({}, first="blobs/x"))
        with self.assertRaises(DeltaError) as caught:
            self.apply(self.write({}, description={"format": "something-else", "entries": {}}))
        self.assertEqual(caught.exception.code, "format")

    def test_a_patch_beyond_its_file_is_refused(self):
        out = io.BytesIO(update_delta.PATCH_MAGIC + (10).to_bytes(8, "big") + (8).to_bytes(8, "big")
                         + (5).to_bytes(4, "big") + b"xxxxx")
        target = self.folder / "file"
        target.write_bytes(b"0123456789")
        out.seek(0)
        with self.assertRaises(DeltaError):
            update_delta._apply_patch(out, target, 10)


if __name__ == "__main__":
    unittest.main()
