# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The install step that puts the pinned wheels (certifi, pip) over the bundled library's copies.

``build_files/utils/angelo_python_packages.py`` runs with the installed interpreter during ``make install``
(``angelo_python_packages.cmake``). These tests feed it small synthetic wheels: it replaces the old version and
its metadata, does nothing on a rerun, and refuses (changing nothing) a wheel whose hash, RECORD or paths are
not what the pin and the wheel format promise.
"""

import base64
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "build_files/utils"))
import angelo_python_packages as packages  # noqa: E402


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def make_wheel(folder, name="demo", version="2.0", files=None, record_override=None, extra=None):
    """A wheel at ``folder`` and its pin entry."""
    files = dict(files or {"%s/__init__.py" % name: b"__version__ = '%s'\n" % version.encode(),
                           "%s/data.pem" % name: b"new roots\n"})
    dist_info = "%s-%s.dist-info" % (name, version)
    files["%s/METADATA" % dist_info] = b"Name: %s\nVersion: %s\n" % (name.encode(), version.encode())
    files.update(extra or {})
    record = "".join("%s,%s,%d\n" % (path, record_hash(data), len(data)) for path, data in files.items())
    record += "%s/RECORD,,\n" % dist_info
    files["%s/RECORD" % dist_info] = (record_override or record).encode()
    wheel = Path(folder) / ("%s-%s-py3-none-any.whl" % (name, version))
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return {"name": name, "version": version, "url": "https://example.invalid/x/" + wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="angelo-packages-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.wheels, self.site = self.root / "wheels", self.root / "site-packages"
        self.wheels.mkdir()
        (self.site / "demo").mkdir(parents=True)
        (self.site / "demo" / "old_only.py").write_text("old\n")
        (self.site / "demo" / "data.pem").write_text("old roots\n")
        (self.site / "Demo-1.0.dist-info").mkdir()           # Another spelling of the same project.
        (self.site / "other").mkdir()
        (self.site / "other" / "keep.py").write_text("untouched\n")

    def run_main(self, pin):
        pins = self.root / "pins.json"
        pins.write_text(json.dumps({"packages": [pin]}))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(packages.sysconfig, "get_path", return_value=str(self.site)), \
                redirect_stdout(out), redirect_stderr(err):
            code = packages.main([str(pins), str(self.wheels)])
        return code, out.getvalue() + err.getvalue()

    def snapshot(self):
        return sorted(str(path.relative_to(self.site)) for path in self.site.rglob("*"))

    def test_replaces_the_old_version_entirely_and_a_rerun_changes_nothing(self):
        code, text = self.run_main(make_wheel(self.wheels))
        self.assertEqual(code, 0, text)
        self.assertEqual((self.site / "demo" / "data.pem").read_bytes(), b"new roots\n")
        self.assertFalse((self.site / "demo" / "old_only.py").exists(), "no file of the old version survives")
        self.assertFalse((self.site / "Demo-1.0.dist-info").exists())
        self.assertTrue((self.site / "demo-2.0.dist-info" / "RECORD").exists())
        self.assertEqual((self.site / "other" / "keep.py").read_text(), "untouched\n")
        before = self.snapshot()
        code, text = self.run_main(make_wheel(self.wheels))
        self.assertEqual((code, "already installed" in text), (0, True), text)
        self.assertEqual(self.snapshot(), before)

    def test_refuses_without_changing_anything(self):
        cases = {
            "hash": lambda: dict(make_wheel(self.wheels), sha256="0" * 64),
            "record": lambda: make_wheel(self.wheels, record_override="demo/__init__.py,sha256=AAAA,1\n"),
            "traversal": lambda: make_wheel(self.wheels, extra={"../escape.py": b"x"}),
            "data folder": lambda: make_wheel(self.wheels, extra={"demo-2.0.data/scripts/demo": b"#!/bin/sh\n"}),
            "other project": lambda: dict(make_wheel(self.wheels), name="certifi"),
        }
        before = self.snapshot()
        for label, pin in cases.items():
            with self.subTest(label):
                code, text = self.run_main(pin())
                self.assertEqual(code, 1, text)
                self.assertEqual(self.snapshot(), before, "%s: nothing changes" % label)
                self.assertFalse((self.root / "escape.py").exists())


class PinnedWheelsTests(unittest.TestCase):
    def test_the_pinned_wheels_install_when_present(self):
        # The real pins (CI fetches them; scripts/angelo/trust_store.py fetch-wheels): accepted as they are.
        import os
        folder = os.environ.get("ANGELO_PYTHON_WHEELS_DIR")
        if not folder:
            self.skipTest("set ANGELO_PYTHON_WHEELS_DIR (trust_store.py fetch-wheels)")
        pin = json.loads((ROOT / "build_files/cmake/angelo_python_packages.json").read_text())
        for package in pin["packages"]:
            dist_info, members = packages.wheel_members(packages.wheel_path(folder, package), package)
            self.assertEqual(dist_info, "%s-%s.dist-info" % (package["name"], package["version"]))
            self.assertTrue(any(name.endswith("certifi/cacert.pem") for name in members), package["name"])


if __name__ == "__main__":
    unittest.main()
