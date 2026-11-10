# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's asset library never writes a file from nothing because it could not read it now.

Regression (1.0.10 audit re-check, P3, since 1.0.9): ``write_catalogs`` read any error on ``blender_assets.cats.txt``
as an empty file and rewrote it with Angelo's three lines, deleting the person's own catalogues and renames (a real
``chmod 200``). The same class swept in the folder's other files: a ``manifest.json`` that could not be read was
written back holding only the items converted then (every other item forgotten, so its copy is never removed), and a
``registration.json`` that could not be read counted as "on" (a library the person turned off was added back and
written). Also P3 (new in de49846ec13): a catalogue added while ``catalog-names.json`` could not be read was never
recorded, so it stopped following language changes. Developed on EIO and EACCES (a real chmod); EPERM, ESTALE and
ETIMEDOUT, the manifest and registration callers are held out. The break-it review of that fix added: a save in the
Asset Browser between a sync's read and its write (Blender saves by two renames, so the file is briefly missing) lost
the person's catalogues (since 1.0.9); a language change while the file couldn't be read was dropped for the session;
an unreadable registration at startup kept the library off for the session; a damaged one counted as on; a drag
wrote while writers halted; a damaged record blocked every catalogue write.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import errno
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_studio_assets import MODULES, Library  # noqa: E402

with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import studio_assets, studio_assets_host as host, studio_assets_space as space  # noqa: E402
    from angelo_agent import studio_files  # noqa: E402
    from angelo_agent import studio_drag  # noqa: E402

NUMBERS = (errno.EIO, errno.EACCES, errno.EPERM, errno.ESTALE, errno.ETIMEDOUT)
GERMAN, ENGLISH = {"3D": "Modelle", "IMAGE": "Bilder"}, {"3D": "Models", "IMAGE": "Images"}
IMAGE = studio_assets.CATALOGS["IMAGE"][0]
REAL_READ = Path.read_text
shared_replace = studio_files.shared_replace   # The module Studio's own files are read through.


@contextlib.contextmanager
def failing(name, number):
    """Reads of files called ``name`` fail with ``number``: ``Path.read_text`` and the shared reader, in every copy of
    the add-on loaded now (a test that patches ``angelo_agent.*`` by name loads another under its fake ``bpy``)."""
    def read_text(self, *arguments, **keywords):
        if self.name == name:
            raise OSError(number, os.strerror(number), str(self))
        return REAL_READ(self, *arguments, **keywords)

    def guarded(real):
        def opened(path, *arguments, **keywords):
            if Path(path).name == name:
                raise OSError(number, os.strerror(number), str(path))
            return real(path, *arguments, **keywords)
        return opened
    copies = {id(module): module for module in [shared_replace] + [
        module for key, module in list(sys.modules.items()) if key == "angelo_agent.shared_replace"]}
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(Path, "read_text", read_text))
        for module in copies.values():
            stack.enter_context(mock.patch.object(module, "open_shared", guarded(module.open_shared)))
        yield


def real_chmod_works():
    return sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() != 0


class Unreadable:
    """Ways a file can be there but not readable now: each error number, and a real write-only mode."""

    def ways(self, path):
        for number in NUMBERS:
            yield f"errno {errno.errorcode[number]}", failing(path.name, number)
        if real_chmod_works():
            yield "chmod 200", self.chmod(path)

    @staticmethod
    def chmod(path):
        class Mode:
            def __enter__(self):
                self.mode = path.stat().st_mode
                path.chmod(0o200)

            def __exit__(self, *_):
                path.chmod(self.mode)
        return Mode()


class CatalogueFile(Unreadable, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / studio_assets.CATS_FILE
        self.record = self.directory / studio_assets.WRITTEN_NAMES
        studio_assets.write_catalogs(self.directory, GERMAN)
        own = ("0a0b0c0d-0000-4000-8000-000000000001:Props/Rocks:Rocks",
               f"0a0b0c0d-0000-4000-8000-000000000002:{studio_assets.TAB_PATH}/Bilder/Himmel:Himmel")
        self.path.write_text(self.path.read_text(encoding="utf-8") + "\n".join(own) + "\n", encoding="utf-8")

    def test_an_unreadable_catalogue_file_is_never_rewritten(self):
        saved, record = self.path.read_bytes(), self.record.read_bytes()
        for way, unreadable in self.ways(self.path):
            with self.subTest(way=way):
                with unreadable:
                    self.assertIsNone(studio_assets.write_catalogs(self.directory, ENGLISH, renamed=True))
                self.assertEqual(self.path.read_bytes(), saved, "the person's catalogues are kept")
                self.assertEqual(self.record.read_bytes(), record)
        studio_assets.write_catalogs(self.directory, ENGLISH, renamed=True)   # Readable again: Angelo's lines follow.
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Angelo Media Studio/Models:Models", text)
        self.assertIn("Props/Rocks:Rocks", text)
        self.assertIn("Bilder/Himmel:Himmel", text)

    def test_a_catalogue_file_that_is_not_utf8_keeps_its_bytes(self):
        line = "0a0b0c0d-0000-4000-8000-000000000003:Caf\xe9:Caf\xe9".encode("latin-1")
        self.path.write_bytes(self.path.read_bytes() + line + b"\n")
        self.assertTrue(studio_assets.write_catalogs(self.directory, ENGLISH, renamed=True))
        data = self.path.read_bytes()
        self.assertIn(line, data)
        self.assertIn(b"Angelo Media Studio/Models:Models", data)

    def test_a_first_sync_over_an_unreadable_file_writes_no_record(self):
        self.record.unlink()
        saved = self.path.read_bytes()
        with failing(self.path.name, errno.EIO):
            self.assertIsNone(studio_assets.write_catalogs(self.directory, ENGLISH, renamed=True))
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertFalse(self.record.exists())

    def test_a_missing_file_is_still_written(self):
        self.path.unlink()
        self.assertTrue(studio_assets.write_catalogs(self.directory, ENGLISH, renamed=True))
        self.assertIn("Angelo Media Studio/Images:Images", self.path.read_text(encoding="utf-8"))


class UnreadableRecord(Unreadable, unittest.TestCase):
    """A catalogue missing while the record can't be read waits for a sync that can record it (audit P3, case 5)."""

    def test_a_catalogue_added_later_is_recorded_and_follows_the_language(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, record = directory / studio_assets.CATS_FILE, directory / studio_assets.WRITTEN_NAMES
            studio_assets.write_catalogs(directory, ENGLISH)
            for way, unreadable in self.ways(record):
                with self.subTest(way=way):
                    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                                               if not line.startswith(IMAGE)) + "\n", encoding="utf-8")
                    saved, kept = path.read_bytes(), record.read_bytes()
                    with unreadable:
                        self.assertIsNone(studio_assets.write_catalogs(directory, ENGLISH, renamed=False))
                    self.assertEqual(path.read_bytes(), saved, "nothing added that could not be recorded")
                    self.assertEqual(record.read_bytes(), kept)
                    studio_assets.write_catalogs(directory, ENGLISH, renamed=False)   # Readable: added, recorded.
                    self.assertIn(f"{IMAGE}:Angelo Media Studio/Images:Images", path.read_text(encoding="utf-8"))
                    studio_assets.write_catalogs(directory, GERMAN, renamed=True)   # This Angelo's language changed.
                    self.assertIn(f"{IMAGE}:Angelo Media Studio/Bilder:Bilder", path.read_text(encoding="utf-8"))
                    studio_assets.write_catalogs(directory, ENGLISH, renamed=True)


    def test_a_damaged_record_is_rebuilt_and_never_blocks_the_catalogues(self):
        for damaged in (b"\xff\xfe not utf-8", b"", b"{broken", b"[1, 2]"):
            with self.subTest(damaged=damaged), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                path, record = directory / studio_assets.CATS_FILE, directory / studio_assets.WRITTEN_NAMES
                studio_assets.write_catalogs(directory, ENGLISH)
                path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                                           if not line.startswith(IMAGE)) + "\n", encoding="utf-8")
                record.write_bytes(damaged)
                self.assertTrue(studio_assets.write_catalogs(directory, ENGLISH, renamed=False))
                self.assertIn(f"{IMAGE}:Angelo Media Studio/Images:Images", path.read_text(encoding="utf-8"))
                self.assertIsInstance(studio_assets.read_registration(directory), dict)


class LibraryFiles(Unreadable, unittest.TestCase):
    """The manifest and the registration: a file there but not readable now is never written from nothing."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Library(temporary.name)
        self.record = self.library.model("kept")
        self.library.studio.reload()
        self.directory = studio_assets.asset_directory(self.library.root)
        self.directory.mkdir(parents=True)
        studio_assets.write_catalogs(self.directory, ENGLISH)
        self.manifest = self.directory / studio_assets.MANIFEST
        gone = "00000000-0000-4000-8000-0000000000aa"   # An item deleted since: its copy waits for removal.
        studio_assets.write_manifest(self.directory, {gone: {"fingerprint": "x", "blend": gone + ".blend"}})
        studio_assets.write_registration(self.directory, removed=False)

    def run_sync(self):
        ran = []

        def run(_binary, batch, _stop, _yield_to=None):
            ran.extend(batch)
            return {item["id"]: {"ok": True} for item in batch}
        changed = host.sync(self.library.root, self.directory, ENGLISH, "/nonexistent/Angelo", "one",
                            threading.Event(), run)
        return changed, ran

    def test_readers_tell_missing_and_damaged_from_unreadable(self):
        for name, reader, damaged in ((studio_assets.MANIFEST, studio_assets.read_manifest, {}),
                                      (studio_assets.REGISTRATION, studio_assets.read_registration,
                                       studio_assets.DAMAGED)):
            with self.subTest(name=name):
                path = self.directory / name
                for way, unreadable in self.ways(path):
                    with self.subTest(way=way), unreadable:
                        self.assertIsNone(reader(self.directory))
                path.write_text("{damaged", encoding="utf-8")
                self.assertEqual(reader(self.directory), damaged)   # A manifest is rebuilt; a Turn off may be lost.
                path.unlink()
                self.assertEqual(reader(self.directory), {})

    def test_an_unreadable_manifest_writes_nothing(self):
        saved = self.manifest.read_bytes()
        for way, unreadable in self.ways(self.manifest):
            with self.subTest(way=way):
                with unreadable:
                    changed, ran = self.run_sync()
                self.assertFalse(changed)
                self.assertEqual(ran, [], "no item converted while its manifest entry is unknown")
                self.assertEqual(self.manifest.read_bytes(), saved, "the gone item's entry is kept")
        changed, ran = self.run_sync()   # Readable again: the new item is written, the gone one removed.
        self.assertEqual([item["id"] for item in ran], [self.record["id"]])
        self.assertEqual(set(studio_assets.read_manifest(self.directory)), {self.record["id"]})

    def test_a_drag_over_an_unreadable_manifest_drags_the_files(self):
        saved = self.manifest.read_bytes()
        convert = mock.Mock(side_effect=AssertionError("converted from an unknown manifest"))
        with mock.patch.dict(sys.modules, MODULES), \
                mock.patch.object(studio_drag, "_library", lambda: (self.library.root, self.directory, object())), \
                mock.patch("angelo_agent.studio_assets_host.convert_batch", convert):
            for way, unreadable in self.ways(self.manifest):
                with self.subTest(way=way), unreadable:
                    self.assertFalse(studio_drag.ensure_written([self.record["id"]]))
        self.assertEqual(self.manifest.read_bytes(), saved)

    def preferences(self, present):
        """Preferences' asset libraries: with this folder's entry (on) or without it (the person removed it)."""
        libraries = mock.MagicMock()
        entry = SimpleNamespace(path=str(self.directory), name=studio_assets.LIBRARY_NAME)
        libraries.__iter__.side_effect = lambda: iter([entry] if present else [])
        return contextlib.ExitStack(), libraries

    def hosted(self, libraries):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(host, "directories", lambda: (self.library.root, self.directory)))
        stack.enter_context(mock.patch.object(host, "_libraries", lambda: libraries))
        return stack

    def test_an_unreadable_registration_is_never_on_and_is_asked_again(self):
        registration = self.directory / studio_assets.REGISTRATION
        studio_assets.write_registration(self.directory, removed=True)   # The person turned it off.
        saved, manifest = registration.read_bytes(), self.manifest.read_bytes()
        _stack, absent = self.preferences(False)
        with self.hosted(absent):
            for way, unreadable in self.ways(registration):
                with self.subTest(way=way):
                    with unreadable:
                        self.assertIsNone(host.ensure_registered(), "not known yet: asked again, never added")
                        self.assertTrue(host.halted(self.directory))
                        self.assertEqual(self.run_sync(), (False, []))
                    absent.new.assert_not_called()
                    self.assertEqual(registration.read_bytes(), saved)
                    self.assertEqual(self.manifest.read_bytes(), manifest)
            studio_assets.write_registration(self.directory, removed=False)   # Readable again, and on.
            self.assertFalse(host.halted(self.directory))
            self.assertTrue(host.ensure_registered())
            absent.new.assert_called_once()

    def test_the_tick_asks_an_unknown_registration_again_once_a_minute(self):
        calls = []
        saved = dict(host._state)
        self.addCleanup(lambda: (host._state.clear(), host._state.update(saved)))
        with mock.patch.object(host, "ensure_registered", lambda: calls.append(1)):   # Returns None: unknown.
            host._state.update(registered=None, asked=host.time.monotonic())
            host._tick()
            self.assertEqual(calls, [], "never read again on every tick")
            host._state["asked"] = host.time.monotonic() - host.RETRY
            host._tick()
            self.assertEqual(calls, [1])
            self.assertIsNone(host._state["registered"])

    def test_with_the_entry_in_preferences_an_unknown_registration_is_written_as_on(self):
        registration = self.directory / studio_assets.REGISTRATION
        _stack, present = self.preferences(True)
        with self.hosted(present):
            for way, unreadable in list(self.ways(registration)) + [("damaged", contextlib.nullcontext())]:
                with self.subTest(way=way):
                    registration.write_text("" if way == "damaged" else '{"removed": true}', encoding="utf-8")
                    with unreadable:
                        self.assertTrue(host.ensure_registered())
                    self.assertEqual(studio_assets.read_registration(self.directory), {
                        "directory": str(self.directory), "removed": False})
            present.new.assert_not_called()

    def test_a_damaged_registration_never_adds_the_library_back(self):
        registration = self.directory / studio_assets.REGISTRATION
        _stack, absent = self.preferences(False)
        with self.hosted(absent):
            for damaged in ("", "{not json", "[1]", "\udcff"):
                with self.subTest(damaged=damaged):
                    registration.write_bytes(damaged.encode("utf-8", "surrogateescape"))
                    self.assertTrue(host.halted(self.directory), "writers halt until the entry decides")
                    self.assertFalse(host.ensure_registered())
                    self.assertTrue(studio_assets.read_registration(self.directory)["removed"])
            absent.new.assert_not_called()

    def test_free_follows_this_angelos_turn_off_when_the_registration_cant_be_read(self):
        registration = self.directory / studio_assets.REGISTRATION
        studio_assets.write_registration(self.directory, removed=True)
        for way, unreadable in self.ways(registration):
            with self.subTest(way=way), unreadable:
                result = space.free(self.directory, active=False)
                self.assertIsInstance(result, tuple, "never 'busy' for a record that stays unreadable")
        studio_assets.write_registration(self.directory, removed=False)   # Readable and on: nothing deleted.
        self.assertEqual(space.free(self.directory, active=False), space.ON)

    def test_a_drag_halts_with_the_writers(self):
        registration = self.directory / studio_assets.REGISTRATION
        convert = mock.Mock(side_effect=AssertionError("written while writers halt"))
        with mock.patch.dict(sys.modules, MODULES), \
                mock.patch.object(studio_drag, "_library", lambda: (self.library.root, self.directory, object())), \
                mock.patch("angelo_agent.studio_assets_host.convert_batch", convert):
            for way, unreadable in self.ways(registration):
                with self.subTest(way=way), unreadable:
                    self.assertFalse(studio_drag.ensure_written([self.record["id"]]))
            studio_assets.write_registration(self.directory, removed=True)
            self.assertFalse(studio_drag.ensure_written([self.record["id"]]))

    def test_a_language_change_waits_for_a_sync_that_can_write_it(self):
        cats = self.directory / studio_assets.CATS_FILE
        saved = dict(host._state)
        self.addCleanup(lambda: (host._state.clear(), host._state.update(saved)))
        host._state["catalog_names"] = dict(ENGLISH)

        def sync(names):
            return host.sync(self.library.root, self.directory, names, "/nonexistent/Angelo", "one",
                             threading.Event(), lambda *_a: {})
        for way, unreadable in self.ways(cats):
            with self.subTest(way=way):
                with unreadable:
                    sync(GERMAN)   # This Angelo switched to German while the file can't be read.
                sync(GERMAN)
                sync(GERMAN)
                text = cats.read_text(encoding="utf-8")
                self.assertIn("Angelo Media Studio/Modelle:Modelle", text)
                self.assertIn("Angelo Media Studio/Bilder:Bilder", text)
                sync(ENGLISH)


class BlenderSaves(unittest.TestCase):
    """Blender saves the catalogue file by two renames (cats -> cats~, then cats.writing -> cats): a save landing
    inside a sync is never overwritten, nor is a file the sync read while it was missing (review H1, since 1.0.9)."""

    PERSON = "0a0b0c0d-0000-4000-8000-0000000000ff:Props/Newest:Newest"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / studio_assets.CATS_FILE
        studio_assets.write_catalogs(self.directory, ENGLISH)

    def save(self, first=True, second=True):
        """Blender's save of the person's newest catalogue, or one of its two renames."""
        if first:
            os.replace(self.path, self.path.with_name(self.path.name + "~"))
        if second:
            text = (self.path.with_name(self.path.name + "~").read_text(encoding="utf-8") + self.PERSON + "\n")
            writing = self.path.with_name(self.path.name + ".writing")
            writing.write_text(text, encoding="utf-8")
            os.replace(writing, self.path)

    def during_write(self, action):
        """Run ``action`` between the sync's read and its write."""
        real = studio_assets.catalog_text

        def text(*arguments, **keywords):
            action()
            return real(*arguments, **keywords)
        return mock.patch.object(studio_assets, "catalog_text", text)

    def test_a_save_between_the_read_and_the_write_is_kept(self):
        with self.during_write(self.save):
            self.assertIsNone(studio_assets.write_catalogs(self.directory, GERMAN, renamed=True))
        self.assertIn(self.PERSON, self.path.read_text(encoding="utf-8"))
        studio_assets.write_catalogs(self.directory, GERMAN, renamed=True)   # The next sync: both.
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(self.PERSON, text)
        self.assertIn("Angelo Media Studio/Modelle:Modelle", text)

    def test_a_file_read_while_missing_is_never_put_over_the_saved_one(self):
        self.save(second=False)   # The sync reads in the gap: cats is cats~ now.
        with self.during_write(lambda: self.save(first=False)):
            self.assertIsNone(studio_assets.write_catalogs(self.directory, GERMAN, renamed=True))
        self.assertIn(self.PERSON, self.path.read_text(encoding="utf-8"))

    def test_a_same_size_rewrite_that_keeps_its_time_stamp_is_kept(self):
        """A coarse clock (NTFS ~15.6 ms ticks, FAT/exFAT 2 s steps) can leave inode, size and time unchanged across an
        in-place rewrite: the stamp is held still here, so only the bytes tell."""
        stamp = (int(os.stat(self.path).st_mtime) - 10) * 10**9   # Whole seconds: every file system keeps them.
        os.utime(self.path, ns=(stamp, stamp))
        before = os.stat(self.path)

        def rewrite():
            data = self.path.read_bytes().replace(b"Images:Images", b"Pixels:Pixels")
            with open(self.path, "r+b") as handle:   # In place: the same inode; bytes, so Windows adds no CR.
                handle.write(data)
            os.utime(self.path, ns=(stamp, stamp))
            after = os.stat(self.path)
            assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size,
                                                                       before.st_mtime_ns)
        with self.during_write(rewrite):
            self.assertIsNone(studio_assets.write_catalogs(self.directory, GERMAN, renamed=True))
        self.assertIn("Pixels:Pixels", self.path.read_text(encoding="utf-8"))

    def test_a_file_that_cant_be_read_again_before_the_write_is_not_written(self):
        saved = self.path.read_bytes()
        stack = contextlib.ExitStack()
        with self.during_write(lambda: stack.enter_context(failing(self.path.name, errno.EIO))), stack:
            self.assertIsNone(studio_assets.write_catalogs(self.directory, GERMAN, renamed=True))
        self.assertEqual(self.path.read_bytes(), saved)

    def test_a_first_write_that_fails_on_a_volume_without_links_leaves_no_file(self):
        """No hard links (exFAT, FAT32): an exclusive create written in place; a failed write (a full disk) removes
        the file it created, never leaving it half-written."""
        self.path.unlink()
        real = Path.read_bytes

        def full(self, *arguments, **keywords):
            if self.name.endswith(".tmp"):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            return real(self, *arguments, **keywords)
        posix = mock.patch.object(studio_files, "sys", SimpleNamespace(platform="linux"))   # The link path everywhere.
        with posix, mock.patch.object(studio_files.os, "link", side_effect=OSError(errno.ENOTSUP, "no links")), \
                mock.patch.object(Path, "read_bytes", full), self.assertRaises(OSError):
            studio_assets.write_catalogs(self.directory, GERMAN, renamed=True)
        self.assertFalse(self.path.exists())
        with mock.patch.object(studio_files, "sys", SimpleNamespace(platform="linux")), \
                mock.patch.object(studio_files.os, "link", side_effect=OSError(errno.ENOTSUP, "no links")):
            self.assertTrue(studio_assets.write_catalogs(self.directory, GERMAN, renamed=True))
        self.assertIn("Angelo Media Studio/Modelle:Modelle", self.path.read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.directory.iterdir() if p.name.endswith(".tmp")], [])

    def test_a_save_that_moved_the_file_away_mid_sync_is_left_to_finish(self):
        with self.during_write(lambda: self.save(second=False)):
            self.assertIsNone(studio_assets.write_catalogs(self.directory, GERMAN, renamed=True))
        self.assertFalse(self.path.exists(), "nothing put in the save's way")
        self.save(first=False)
        self.assertIn(self.PERSON, self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
