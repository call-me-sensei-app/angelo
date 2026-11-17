# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The write-sink reader's rules, each on planted source (guard piece 10 item 5; write_sinks, test_native_write_sinks):
an unsafe module can't satisfy the coverage test.

* ``fixed`` is a literal or immutable value bound once at module level and never rebound, or a path built from a fixed
  source: never a name by its UPPER_CASE spelling, a mutable container, a name some code rebinds, or another library's
  data (``bpy.context...``) unless listed fixed.
* Dynamic dispatch onto a sink is reported; a callable the source names (a default, ``x or sink``, a choice in place,
  a thread's target with written arguments) is followed as the call it is.
* Folders made, modes, owners and times changed, archives written and ``os.open`` flags held in a name are sinks.
* The reading is the same under every hash seed. Runs outside Blender (ast only).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import destructive_sinks
import write_sinks

HERE = Path(__file__).resolve().parent
FIXED = {"bpy.utils.user_resource", "bpy.app.binary_path"}


def analyse(sources, fixed=FIXED):
    """``{(module, qualname, kind): (class, guarded)}`` of planted modules (``{name: source}``)."""
    folder = Path(tempfile.mkdtemp())
    try:
        for name, source in sources.items():
            (folder / (name + ".py")).write_text(textwrap.dedent(source), encoding="utf-8")
        found = write_sinks.Analysis(modules=destructive_sinks.load(folder)).run(set(fixed))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {(m, q, k): (c, g) for m, q, _l, k, c, g in found}


def classes(sources, fixed=FIXED):
    return {(m, q, k.split(" via ")[0]): c for (m, q, k), (c, _g) in analyse(sources, fixed).items()}


class FixedRuleTests(unittest.TestCase):
    def assertState(self, sources, key):
        self.assertEqual(classes(sources).get(key), "state", key)

    def test_a_literal_bound_once_is_fixed(self):
        found = classes({"m": """
            import os
            FOLDER = "/x"
            names = ("a", "b")
            def clear():
                os.unlink(os.path.join(FOLDER, names[0]))
            """})
        self.assertEqual(found[("m", "clear", "os.unlink")], "fixed")

    def test_an_upper_case_name_is_no_constant_by_its_spelling(self):
        self.assertState({"m": """
            import os
            FOLDERS = ["/x"]
            def clear():
                os.unlink(FOLDERS[0])
            """}, ("m", "clear", "os.unlink"))
        self.assertState({"m": """
            import os
            FOLDER = "/x"
            def point(path):
                global FOLDER
                FOLDER = path
            def clear():
                os.unlink(FOLDER)
            """}, ("m", "clear", "os.unlink"))
        self.assertState({"m": """
            import os
            FOLDER = "/x"
            FOLDER = "/y"
            def clear():
                os.unlink(FOLDER)
            """}, ("m", "clear", "os.unlink"))

    def test_a_module_constant_rebound_from_another_module_is_state(self):
        self.assertState({"a": 'FOLDER = "/x"\n', "b": """
            import os
            from . import a
            def point(path):
                a.FOLDER = path
            def clear():
                os.unlink(a.FOLDER)
            """}, ("b", "clear", "os.unlink"))
        self.assertState({"a": 'FOLDERS = {"x": "/x"}\n', "b": """
            import os
            from .a import FOLDERS
            def clear():
                os.unlink(FOLDERS["x"])
            """}, ("b", "clear", "os.unlink"))

    def test_another_library_s_data_is_state_unless_listed(self):
        found = classes({"m": """
            import os, subprocess, bpy
            from pathlib import Path
            ROOT = Path(bpy.utils.user_resource("CONFIG")) / "angelo"
            def render():
                os.unlink(bpy.context.scene.render.filepath)
            def mine():
                os.unlink(ROOT / "x")
            def start():
                subprocess.Popen([bpy.app.binary_path, "-b"])
            """})
        self.assertEqual(found[("m", "render", "os.unlink")], "state")
        self.assertEqual(found[("m", "mine", "os.unlink")], "fixed")
        self.assertEqual(found[("m", "start", "run:subprocess.Popen")], "fixed")
        self.assertEqual(classes({"m": "import os, bpy\ndef start():\n    os.unlink(bpy.app.binary_path)\n"},
                                 fixed=set())[("m", "start", "os.unlink")], "state")


class DispatchRuleTests(unittest.TestCase):
    def test_dynamic_dispatch_onto_a_sink_is_reported(self):
        found = analyse({"m": """
            import functools, os, shutil, threading
            class Thing:
                def __init__(self):
                    self.remove = os.unlink
                def name(self, name, path):
                    getattr(os, name)(path)
                def later(self, a, b, folders):
                    functools.partial(os.replace, a)(b)
                    threading.Thread(target=shutil.rmtree, args=folders).start()
            """})
        dynamic = {(q, k) for (_m, q, k) in found if k.startswith("dynamic:")}
        self.assertEqual(dynamic, {("Thing.__init__", "dynamic:os.unlink"),
                                   ("Thing.name", "dynamic:getattr(<computed>)()"),
                                   ("Thing.later", "dynamic:os.replace"), ("Thing.later", "dynamic:shutil.rmtree")})

    def test_a_callable_the_source_names_is_followed(self):
        found = classes({"m": """
            import os, shutil, subprocess, threading
            def start(argv, popen=subprocess.Popen):
                popen(argv)
            class Thing:
                def run(self, run=None):
                    run = run or subprocess.run
                    run([self.tool])
                def copy(self, folder):
                    (shutil.copytree if folder else shutil.copy2)("/a", self.target)
                def later(self):
                    threading.Thread(target=os.unlink, args=(self.path,)).start()
            def caller(thing):
                start([thing.tool])
            """})
        self.assertEqual(found[("m", "caller", "run:subprocess.Popen")], "state")
        self.assertEqual(found[("m", "Thing.run", "run:subprocess.run")], "state")
        self.assertEqual(found[("m", "Thing.copy", "shutil.copytree")], "state")
        self.assertEqual(found[("m", "Thing.copy", "shutil.copy2")], "state")
        self.assertEqual(found[("m", "Thing.later", "os.unlink")], "state")
        self.assertFalse([k for k in found if k[2].startswith("dynamic:")], found)

    def test_a_comparison_or_a_known_name_is_no_dispatch(self):
        found = analyse({"m": """
            import os
            CONVERTERS = ("to_list", "to_dict")
            def plain(value, kind, translations):
                anchored = os.open in os.supports_dir_fd
                for name in ("to_dict", "to_list"):
                    getattr(value, name)()
                for name in CONVERTERS:
                    getattr(value, name)()
                return getattr(translations, "pgettext_" + kind)(value)
            """})
        self.assertFalse([k for k in found if k[2].startswith("dynamic:")], found)


class NewSinkTests(unittest.TestCase):
    def test_folders_modes_times_and_archives_are_sinks(self):
        calls = {"zipfile.ZipFile:w": "zipfile.ZipFile(self.path, 'w')",
                 "tarfile.open:w:gz": "tarfile.open(self.path, 'w:gz')",
                 "shutil.make_archive": "shutil.make_archive(self.path, 'zip', '/x')",
                 ".touch": "self.path.touch()", ".mkdir": "self.path.mkdir(exist_ok=True)",
                 "os.mkdir": "os.mkdir(self.path)", "os.makedirs": "os.makedirs(self.path)",
                 "os.truncate": "os.truncate(self.path, 0)", "os.chmod": "os.chmod(self.path, 0o600)",
                 "os.chown": "os.chown(self.path, 1, 1)", "os.utime": "os.utime(self.path)",
                 ".chmod": "self.path.chmod(0o600)", ".symlink_to": "self.path.symlink_to('/x')",
                 ".extractall": "archive.extractall(self.path)"}
        body = "\n".join("    def f%d(self, archive):\n        %s" % (i, call) for i, call in enumerate(calls.values()))
        found = classes({"m": "import os, shutil, tarfile, zipfile\nclass Thing:\n" + body + "\n"})
        for i, kind in enumerate(calls):
            self.assertEqual(found.get(("m", "Thing.f%d" % i, kind)), "state", kind)

    def test_reads_are_no_sinks(self):
        found = analyse({"m": """
            import tarfile, zipfile
            class Thing:
                def read(self, rows):
                    zipfile.ZipFile(self.path)
                    zipfile.ZipFile(self.path, 'r')
                    tarfile.open(self.path, 'r:xz')
                    rows.touch(1)
            """})
        self.assertEqual(found, {})

    def test_native_module_writers_are_sinks(self):
        """A writer of Blender's own Python modules that is handed a path (C's ``_bpy._angelo_saved.write_only``,
        ``imbuf.write``, audaspace's ``Sound.write`` on a sound the function made) writes that path like ``open``; a
        file object's ``write(data)`` writes no path."""
        found = classes({"m": """
            import aud
            import imbuf
            from _bpy import _angelo_saved
            class Thing:
                def kept(self, ids):
                    _angelo_saved.write_only(self.path, ids)
                def image(self, picture):
                    imbuf.write(picture, filepath=self.path)
                def sound(self, source):
                    sound = aud.Sound(source)
                    sound.write(self.path, rate=48000, channels=2)
                def resampled(self, source):
                    sound = aud.Sound.file(source).resample(48000)
                    sound.write(filename=self.path)
                def data(self, out):
                    out.write(self.path)
            """})
        self.assertEqual(found.get(("m", "Thing.kept", "_bpy._angelo_saved.write_only")), "state", found)
        self.assertEqual(found.get(("m", "Thing.image", "imbuf.write")), "state", found)
        self.assertEqual(found.get(("m", "Thing.sound", "aud.Sound.write")), "state", found)
        self.assertEqual(found.get(("m", "Thing.resampled", "aud.Sound.write")), "state", found)
        self.assertFalse([k for k in found if k[1] == "Thing.data"], found)

    def test_os_open_flags_held_in_a_name(self):
        found = classes({"m": """
            import os
            class Thing:
                def write(self):
                    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                    os.open(self.path, flags)
                def read(self):
                    flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW_ANY", 0) if os.name == "posix" else 0)
                    os.open(self.path, flags)
            def opener(path, flags=os.O_RDONLY):
                return os.open(path, flags)
            def reader(thing):
                opener(thing.path)
            def writer(thing):
                opener(thing.path, os.O_WRONLY | os.O_CREAT)
            """})
        self.assertEqual(found.get(("m", "Thing.write", "os.open")), "state")
        self.assertNotIn(("m", "Thing.read", "os.open"), found)
        self.assertNotIn(("m", "reader", "os.open"), found)
        self.assertEqual(found.get(("m", "writer", "os.open")), "state")


class DeterminismTests(unittest.TestCase):
    def test_the_add_on_reads_the_same_under_every_hash_seed(self):
        script = ("import sys; sys.argv = ['x']; import test_native_write_sinks as t\n"
                  "print('\\n'.join(map(repr, t._analysis())))\n")
        runs = []
        for seed in ("0", "1", "4242"):   # Started together: each reads the whole add-on.
            environ = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(HERE))
            runs.append(subprocess.Popen([sys.executable, "-c", script], cwd=HERE, env=environ, text=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        outputs = []
        for run in runs:
            out, err = run.communicate(timeout=600)
            self.assertEqual(run.returncode, 0, err[-2000:])
            outputs.append(out)
        self.assertTrue(outputs[0].strip())
        self.assertEqual(outputs[1], outputs[0])
        self.assertEqual(outputs[2], outputs[0])


if __name__ == "__main__":
    unittest.main()
