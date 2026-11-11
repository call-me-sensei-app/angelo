# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A start that never completed makes the next launch enable only the add-ons that ship with Angelo
(``angelo_safe_start``; doc/angelo/app_identity.rst, "A start that did not finish"). No Blender.

Covered, as a state machine over real processes and files:

* a clean start leaves nothing behind, and nor does a crash after the start completed;
* a start whose process ended before completing is found once, by the next launch, and removed;
* a start still running in another process (its file locked) is never taken for a crash, so two instances can start
  at once, each with its own record; a held record whose process ends within the bounded wait (a crashed process
  still ending, as the Windows crash dialog's Restart leaves it) or is already gone is found, once, even while its
  lock lingers; the wait is one bound for every record;
* a record cut short (a process that died between creating and writing it) is not a crash, and goes once it is old;
* only third-party add-ons are left off (user add-ons, user extensions), never Angelo's own, Blender's hidden core
  ones or a system extension, in the preferences' order;
* the start completes once Angelo's start-up has run what it owes and the window has run a moment more, or after a
  longest wait;
* add-ons enabled later in the session (a preference read: Angelo's settings import, Revert to Saved Preferences)
  hold a record too, so a native crash there is found by the next launch; a read that finishes lets it go, and
  add-ons a launch left off are saved off and named once;
* the startup module wires it before any add-on loads and never in a background or factory-startup run;
* the notice is translatable and names what was left off.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))
import angelo_safe_start as safe  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

MODULES = str(ROOT / "scripts/modules")


LIVE = ("hold", "end-soon", "complete-soon", "unlock-soon")


def child(folder, then):
    """Start a record in another process; ``then`` is 'finish', 'die' (ended mid-start), 'hold' (still starting),
    'end-soon' (a crashed start still ending: it holds its record and dies half a second later, as a process whose
    crash dialog's Restart started the next launch does; reaped at once, as its parent reaps it), 'complete-soon' (a
    live start that completes 0.4 s later and keeps running) or 'unlock-soon' (a live owner whose lock goes 0.3 s
    later and whose record goes 0.3 s after that: a removal another process held up)."""
    code = ("import os, sys, time; sys.path.insert(0, %r); import angelo_safe_start as s\n"
            "held = s.begin(%r)\n"
            "print('BEGUN', flush=True)\n"
            "then = %r\n"
            "if then == 'finish':\n    held.release()\n"
            "elif then == 'die':\n    os._exit(3)\n"
            "elif then == 'end-soon':\n    time.sleep(0.5); os._exit(3)\n"
            "elif then == 'complete-soon':\n    time.sleep(0.4); held.release(); sys.stdin.readline()\n"
            "elif then == 'unlock-soon':\n"
            "    handle = held._handle; time.sleep(0.3); s._unlock(handle); time.sleep(0.3); handle.close()\n"
            "    held.path.unlink(); sys.stdin.readline()\n"
            "else:\n    sys.stdin.readline(); held.release()\n") % (MODULES, str(folder), then)
    process = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True)
    assert process.stdout.readline().strip() == "BEGUN"
    if then not in LIVE:
        process.wait(timeout=30)
    if then == "end-soon":   # Reaped the moment it ends, as a crashed app's parent reaps it: no zombie stays alive.
        threading.Thread(target=process.wait, daemon=True).start()
    return process


def dead_pid():
    """A process id that no process has now: a child that ended and was reaped, checked gone (never a reused id)."""
    for _ in range(20):
        done = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
        done.wait(timeout=30)
        if not safe._alive(done.pid):
            return done.pid
    raise AssertionError("no free process id found")


def write_record(folder, pid, **fields):
    """A complete start record naming ``pid``, from this host unless ``fields`` say otherwise."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("%d-%s%s" % (os.getpid(), os.urandom(6).hex(), safe.SUFFIX))
    path.write_bytes(json.dumps(dict(safe.identity(), pid=pid, started=time.time(), **fields)).encode())
    return path


def lock_holder(path):
    """Another process that only holds ``path``'s lock (as the system may still show a dead owner's for a moment)."""
    code = ("import sys; sys.path.insert(0, %r); import angelo_safe_start as s\n"
            "handle = open(%r, 'r+b', buffering=0); s._lock(handle)\n"
            "print('LOCKED', flush=True); sys.stdin.readline()\n") % (MODULES, str(path))
    process = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True)
    assert process.stdout.readline().strip() == "LOCKED"
    return process


class WaitClock:
    """A clock that moves only when the code under test sleeps: the bounded wait is counted, never timed, so how
    busy the machine is cannot decide these tests (test_native_machine_clock)."""

    def __init__(self):
        self.now, self.naps = 0.0, 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.naps += 1


def finish(process):
    if process.poll() is None:
        process.stdin.write("\n")
        process.stdin.flush()
    process.wait(timeout=30)


class Folder(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / safe.FOLDER


class RecordTests(Folder):
    def test_a_clean_start_leaves_nothing(self):
        child(self.folder, "finish")
        self.assertEqual(safe.interrupted(self.folder), [])
        self.assertEqual(list(self.folder.glob("*" + safe.SUFFIX)), [])

    def test_a_start_that_ended_before_completing_is_found_once_and_removed(self):
        child(self.folder, "die")
        found = safe.interrupted(self.folder)
        self.assertEqual(len(found), 1)
        self.assertIn("pid", found[0])
        self.assertEqual(list(self.folder.glob("*" + safe.SUFFIX)), [])
        self.assertEqual(safe.interrupted(self.folder), [])

    def test_a_start_still_running_elsewhere_is_never_a_crash(self):
        running = child(self.folder, "hold")
        try:
            self.assertEqual(safe.interrupted(self.folder), [])
            self.assertEqual(len(list(self.folder.glob("*" + safe.SUFFIX))), 1)   # Its record stays.
            mine = safe.begin(self.folder)   # Two instances start at once, each with its own record.
            self.assertEqual(len(list(self.folder.glob("*" + safe.SUFFIX))), 2)
            mine.release()
        finally:
            running.stdin.write("\n")
            running.stdin.flush()
            running.wait(timeout=30)
        self.assertEqual(safe.interrupted(self.folder), [])

    def test_a_crash_after_the_start_completed_is_not_a_crash_at_start(self):
        code = ("import os, sys; sys.path.insert(0, %r); import angelo_safe_start as s\n"
                "s.begin(%r).release(); os._exit(3)\n") % (MODULES, str(self.folder))
        subprocess.run([sys.executable, "-I", "-c", code], timeout=30)
        self.assertEqual(safe.interrupted(self.folder), [])

    def test_a_record_cut_short_is_not_a_crash_and_goes_once_old(self):
        self.folder.mkdir(parents=True)
        cut = self.folder / ("1-cut" + safe.SUFFIX)
        cut.write_bytes(b'{"pid": 1, "sta')
        self.assertEqual(safe.interrupted(self.folder), [])
        self.assertTrue(cut.exists(), "a fresh one may be a start between creating and writing its record")
        old = time.time() - safe.INCOMPLETE_AGE - 5
        os.utime(cut, (old, old))
        self.assertEqual(safe.interrupted(self.folder), [])
        self.assertFalse(cut.exists())

    def test_the_record_is_written_complete_and_names_the_process(self):
        held = safe.begin(self.folder)
        try:
            record = json.loads(held.path.read_text(encoding="utf-8"))
            self.assertEqual(record["pid"], os.getpid())
            self.assertIn("started", record)
        finally:
            held.release()
        self.assertFalse(held.path.exists())
        held.release()   # Twice is harmless (an exit after completion).

    def test_an_unreadable_folder_never_stops_a_start(self):
        self.folder.parent.joinpath("blocked").write_text("a file, not a folder")
        blocked = self.folder.parent / "blocked" / safe.FOLDER
        self.assertEqual(safe.interrupted(blocked), [])
        self.assertIsNone(safe.begin(blocked))


class HeldRecordTests(Folder):
    """A record still locked when a launch looks: its start may be running, or a crashed process may still be ending
    (on Windows the crash dialog's Restart starts the next launch before the crashed process ends). The launch waits a
    bounded moment for the lock to go, takes a record whose owner is gone, and leaves a live start alone."""

    def test_a_crashed_start_that_ends_half_a_second_later_is_found(self):
        ending = child(self.folder, "end-soon")
        try:
            found = safe.interrupted(self.folder)
        finally:
            ending.wait(timeout=30)
        self.assertEqual([record["pid"] for record in found], [ending.pid])
        self.assertEqual(list(self.folder.glob("*" + safe.SUFFIX)), [])
        self.assertEqual(safe.interrupted(self.folder), [], "found once")

    def test_a_live_start_is_still_left_alone_after_the_wait(self):
        running = child(self.folder, "hold")
        clock = WaitClock()
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=0.6, clock=clock, sleep=clock.sleep), [])
            self.assertEqual(len(list(self.folder.glob("*" + safe.SUFFIX))), 1)
        finally:
            finish(running)
        self.assertGreaterEqual(clock.now, 0.6, "it waited the bound")
        self.assertLessEqual(clock.now, 0.6 + safe.HELD_POLL + 1e-9, "and no longer")

    def test_the_wait_is_one_bound_for_every_record(self):
        running = [child(self.folder, "hold") for _ in range(3)]
        clock = WaitClock()
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=0.5, clock=clock, sleep=clock.sleep), [])
        finally:
            for process in running:
                finish(process)
        self.assertLessEqual(clock.now, 0.5 + safe.HELD_POLL + 1e-9, "three live starts share one wait")
        self.assertLessEqual(safe.HELD_WAIT, 3.0, "a launch never waits long on another's start")

    def test_a_record_whose_owner_is_gone_is_found_though_still_locked(self):
        path = write_record(self.folder, dead_pid())
        holder = lock_holder(path)
        clock = WaitClock()
        try:
            found = safe.interrupted(self.folder, wait=5.0, clock=clock, sleep=clock.sleep)
        finally:
            finish(holder)
        self.assertEqual(len(found), 1)
        self.assertEqual(clock.naps, 0, "a gone owner is not waited for")
        self.assertEqual(safe.interrupted(self.folder), [], "found once")

    def test_a_found_record_that_cannot_be_removed_yet_is_not_found_again(self):
        """Windows removes no file another process still has open: the record is marked found, and goes later."""
        child(self.folder, "die")
        from unittest.mock import patch
        real = Path.unlink

        def refuse(path, *args, **kwargs):
            if path.name.endswith(safe.SUFFIX):
                raise PermissionError("still open elsewhere")
            return real(path, *args, **kwargs)
        with patch.object(Path, "unlink", refuse):
            self.assertEqual(len(safe.interrupted(self.folder)), 1)
            self.assertEqual(safe.interrupted(self.folder), [], "marked found: never a second safe start")
        self.assertEqual(safe.interrupted(self.folder), [])
        self.assertEqual(list(self.folder.iterdir()), [], "the record and its mark go once removable")

    def test_a_live_start_that_completes_during_the_wait_is_not_a_crash(self):
        """The owner releases its record while a launch waits on it: a healthy start, never one that crashed."""
        completing = child(self.folder, "complete-soon")
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=2.0), [])
        finally:
            finish(completing)
        self.assertEqual(safe.interrupted(self.folder), [])

    def test_a_live_owner_whose_lock_goes_before_its_record_is_not_a_crash(self):
        """Its lock goes, its record a moment later (a removal another reader held up): the owner still runs."""
        owner = child(self.folder, "unlock-soon")
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=2.0), [])
        finally:
            finish(owner)

    def test_a_released_record_reads_as_no_crash_even_if_it_cannot_be_removed(self):
        """Release empties the record while still holding it: a removal another process holds up leaves nothing that
        reads as a crash."""
        from unittest.mock import patch
        held = safe.begin(self.folder)
        with patch.object(Path, "unlink", side_effect=PermissionError("held open elsewhere")):
            held.release()
        self.assertEqual(held.path.read_bytes(), b"")
        self.assertEqual(safe.interrupted(self.folder), [])

    def test_a_record_from_another_host_is_left_alone_while_held(self):
        """A shared settings folder (roaming profile on a share, an NFS home): another machine's process id means
        nothing here, so its held record is neither judged by it nor waited for."""
        path = write_record(self.folder, dead_pid(), host="another-machine")
        holder = lock_holder(path)
        clock = WaitClock()
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=2.0, clock=clock, sleep=clock.sleep), [])
        finally:
            finish(holder)
        self.assertEqual(clock.naps, 0, "not waited for")
        self.assertTrue(path.exists())

    def test_a_record_from_another_boot_is_left_alone_while_held(self):
        """Another boot (or another process-id namespace) of this host: its process id means nothing now either."""
        if safe.identity().get("boot") is None:
            self.skipTest(environment_skip("no boot identity on this platform: the host decides"))
        path = write_record(self.folder, dead_pid(), boot="another-boot")
        holder = lock_holder(path)
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=1.0), [])
        finally:
            finish(holder)

    def test_a_record_naming_no_valid_process_is_left_alone_and_the_scan_goes_on(self):
        crashed = child(self.folder, "die")   # Found in the same scan, whatever the others hold.
        holders = [lock_holder(write_record(self.folder, pid)) for pid in (2 ** 70, 0, -5, True, "7", 1.5)]
        try:
            found = safe.interrupted(self.folder, wait=0.5)
        finally:
            for holder in holders:
                finish(holder)
        self.assertEqual([record["pid"] for record in found], [crashed.pid])

    def test_a_mark_whose_record_went_is_removed(self):
        self.folder.mkdir(parents=True)
        mark = self.folder / ("123-abc%s%s" % (safe.SUFFIX, safe.FOUND_MARK))
        mark.write_bytes(b"")
        self.assertEqual(safe.interrupted(self.folder), [])
        self.assertFalse(mark.exists())

    def test_its_own_record_is_never_found(self):
        mine = safe.begin(self.folder)
        try:
            self.assertEqual(safe.interrupted(self.folder, wait=0.2), [])
        finally:
            mine.release()

    def test_alive_knows_a_running_process_from_a_gone_one(self):
        self.assertTrue(safe._alive(os.getpid()))
        self.assertFalse(safe._alive(dead_pid()))

    def test_alive_when_it_cannot_tell(self):
        """Not knowing never makes a running start a crash: another user's process, an id the system refuses."""
        from unittest.mock import patch
        if sys.platform == "win32":
            self.assertTrue(safe._alive(4), "the System process: access denied, running")
            return
        for error in (PermissionError("another user's"), OverflowError("too large"), OSError("other")):
            with self.subTest(type(error).__name__), patch.object(os, "kill", side_effect=error):
                self.assertTrue(safe._alive(12345))

    @unittest.skipUnless(sys.platform == "win32", environment_skip("the Windows process check"))
    def test_on_windows_an_ended_process_whose_object_remains_is_gone(self):
        """The handle this test holds keeps the ended process's object: it has still ended."""
        ended = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
        while ended.poll() is None:
            time.sleep(0.05)
        self.assertFalse(safe._alive(ended.pid))
        running = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(safe._alive(running.pid))
        finally:
            running.kill()
            running.wait()

    def test_the_record_names_its_host(self):
        held = safe.begin(self.folder)
        try:
            record = json.loads(held.path.read_bytes())
        finally:
            held.release()
        self.assertEqual(record["host"], safe.identity()["host"])
        self.assertIn("boot", record)


class StandInMsvcrt:
    """``msvcrt`` for the Windows lock tests: records each lock call (mode, file offset, length), and hands every
    other attribute to the real module on Windows. Off Windows there is none: an attribute not stood in for raises,
    naming itself (``WindowsLockTests.test_the_stand_in_covers_every_msvcrt_call`` keeps the two in step)."""

    LK_NBLCK, LK_UNLCK = 2, 0   # The real module's values.
    OWN = frozenset({"LK_NBLCK", "LK_UNLCK", "locking"})
    WINDOWS_ONLY = frozenset({"_open_record"})   # Functions that need Windows itself; replaced off Windows.

    def __init__(self, calls):
        self._calls = calls
        self._real = __import__("msvcrt") if sys.platform == "win32" else None

    def locking(self, fd, mode, length):
        self._calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), length))

    def __getattr__(self, name):
        if self._real is None:
            raise AttributeError("the stand-in msvcrt has no %s off Windows: stand in for it" % name)
        return getattr(self._real, name)


class WindowsLockTests(Folder):
    """Windows locks are mandatory: a locked byte refuses every read of it. The record's lock is one byte past its end
    (Windows allows locks beyond end of file), so the record reads back while held, as on macOS and Linux."""

    @unittest.skipUnless(sys.platform == "win32", environment_skip("Windows file sharing"))
    def test_a_reader_never_holds_up_the_owners_removal(self):
        """A launch reading a record (``_open_record``) keeps no handle that refuses its owner's removal."""
        held = safe.begin(self.folder)
        reader = safe._open_record(held.path)
        try:
            held.release()   # Its removal is not refused while the reader has it open.
        finally:
            reader.close()
        self.assertFalse(held.path.exists(), "a refused removal would leave the (emptied) record behind")

    def windows_module(self, calls):
        import importlib.util
        from unittest.mock import patch
        stand_in = StandInMsvcrt(calls)   # Made on the real platform: on Windows it wraps the real module.
        with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": stand_in}):
            spec = importlib.util.spec_from_file_location("angelo_start_records_on_windows",
                                                          Path(MODULES) / "angelo_start_records.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        if sys.platform != "win32":   # Its share-delete open needs Windows' own CreateFileW and handles.
            module._open_record = lambda path: open(path, "rb", buffering=0)
        return module

    def test_the_stand_in_covers_every_msvcrt_call(self):
        """Every msvcrt attribute the records module uses is stood in for, or used only where Windows itself is
        needed (``WINDOWS_ONLY``, replaced off Windows): a new call fails here, on every platform, never as an
        error deep in another test on Windows."""
        tree = ast.parse((Path(MODULES) / "angelo_start_records.py").read_text(encoding="utf-8"))
        uses = {}
        for function in ast.walk(tree):
            if isinstance(function, ast.FunctionDef):
                for node in ast.walk(function):
                    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                            and node.value.id == "msvcrt"):
                        uses.setdefault(node.attr, set()).add(function.name)
        self.assertIn("locking", uses)
        uncovered = {name: sorted(where) for name, where in uses.items()
                     if name not in StandInMsvcrt.OWN and not where <= StandInMsvcrt.WINDOWS_ONLY}
        self.assertEqual(uncovered, {}, "stand in for them in StandInMsvcrt, or use them only in WINDOWS_ONLY")

    def test_the_lock_is_past_the_record_and_the_record_reads_while_held(self):
        calls = []
        windows = self.windows_module(calls)
        held = windows.begin(self.folder)
        record = json.loads(held.path.read_bytes())
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(calls, [(2, windows.LOCK_OFFSET, 1)])
        self.assertGreater(windows.LOCK_OFFSET, len(held.path.read_bytes()) * 1000)
        held.release()
        self.assertEqual(calls[-1], (0, windows.LOCK_OFFSET, 1))
        self.assertFalse(held.path.exists())

    def test_finding_a_record_locks_and_reads_it_from_its_start(self):
        calls = []
        windows = self.windows_module(calls)
        held = windows.begin(self.folder)
        held.close()   # As a crashed start leaves it.
        calls.clear()
        found = windows.interrupted(self.folder)
        self.assertEqual([r["pid"] for r in found], [os.getpid()], "the record is read from its first byte")
        self.assertEqual([c[1] for c in calls], [windows.LOCK_OFFSET, windows.LOCK_OFFSET])


class LeftOffTests(unittest.TestCase):
    def test_only_third_party_add_ons_are_left_off_in_the_preferences_order(self):
        shipped = {"angelo_agent", "io_scene_fbx", "bl_ext.system.tool"}.__contains__
        modules = ["angelo_agent", "hair_tool", "io_scene_fbx", "bl_ext.user_default.crashy", "bl_ext.system.tool",
                   "node_wrangler_custom"]
        self.assertEqual(safe.third_party(modules, shipped),
                         ["hair_tool", "bl_ext.user_default.crashy", "node_wrangler_custom"])
        self.assertEqual(safe.third_party([], shipped), [])

    def test_a_module_is_shipped_by_where_it_lies_or_its_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            system, user = root / "app" / "scripts" / "addons_core", root / "user" / "scripts" / "addons"
            for folder, name in ((system, "angelo_agent"), (user, "hair_tool")):
                (folder / name).mkdir(parents=True)
                (folder / name / "__init__.py").write_text("bl_info = {}\n")
            (user / "single.py").write_text("bl_info = {}\n")
            shipped = safe.shipped_check([system, user], [root / "app"], {"system": True, "user_default": False},
                                        hidden_core={"bl_pkg"})
            self.assertTrue(shipped("angelo_agent"))
            self.assertTrue(shipped("bl_pkg"))
            self.assertTrue(shipped("bl_ext.system.tool"))
            self.assertFalse(shipped("hair_tool"))
            self.assertFalse(shipped("single"))
            self.assertFalse(shipped("bl_ext.user_default.crashy"))
            self.assertFalse(shipped("bl_ext.unknown_repo.x"))
            self.assertTrue(shipped("not_installed"), "nothing to load: an absent add-on is never named")


class CompletionTests(unittest.TestCase):
    def test_complete_a_moment_after_angelos_start_up_is_done(self):
        done = safe.Completion(started=100.0)
        self.assertFalse(done.step(101.0, ready=False))
        self.assertFalse(done.step(150.0, ready=True))
        self.assertFalse(done.step(150.0 + safe.SETTLE_SECONDS - 0.1, ready=True))
        self.assertTrue(done.step(150.0 + safe.SETTLE_SECONDS, ready=True))

    def test_ready_once_counts_even_if_a_later_check_says_otherwise(self):
        done = safe.Completion(started=0.0)
        self.assertFalse(done.step(1.0, ready=True))
        self.assertTrue(done.step(1.0 + safe.SETTLE_SECONDS, ready=False))

    def test_a_start_that_never_settles_completes_after_the_longest_wait(self):
        done = safe.Completion(started=0.0)
        self.assertFalse(done.step(safe.LONGEST_START - 1, ready=False))
        self.assertFalse(done.step(safe.LONGEST_START, ready=False))
        self.assertTrue(done.step(safe.LONGEST_START + safe.SETTLE_SECONDS, ready=False))

    def test_applies_only_to_a_window_with_the_persons_preferences(self):
        self.assertTrue(safe.applies(background=False, factory_startup=False))
        self.assertFalse(safe.applies(background=True, factory_startup=False))
        self.assertFalse(safe.applies(background=False, factory_startup=True))


FAKE_BPY = (
    "import types\n"
    "saved = []\n"
    "timers = types.SimpleNamespace(register=lambda *a, **k: None, is_registered=lambda f: False)\n"
    "bpy = types.SimpleNamespace(utils=types.SimpleNamespace(user_resource=lambda kind: %r),\n"
    "    app=types.SimpleNamespace(timers=timers),\n"
    "    ops=types.SimpleNamespace(wm=types.SimpleNamespace(save_userpref=lambda: saved.append(1))))\n"
    "sys.modules['bpy'] = bpy\n")


def in_session(config, body):
    """Run ``body`` in a fresh process whose stand-in ``bpy`` puts Blender's config folder at ``config``; returns its
    exit code and output."""
    code = ("import os, sys; sys.path.insert(0, %r)\n" % MODULES) + FAKE_BPY % str(config) + (
        "import angelo_safe_start as s\n") + body
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30)
    return done.returncode, done.stdout + done.stderr


class SessionTests(Folder):
    """Add-ons enabled after the start completed: Blender enables them again on every preference read."""

    def setUp(self):
        super().setUp()
        self.config = self.folder.parent

    def records(self):
        return list(self.folder.glob("*" + safe.SUFFIX))

    def test_a_crash_while_add_ons_load_mid_session_is_found_by_the_next_launch(self):
        code, output = in_session(self.config, "s.start(bpy); s.stop()\n"
                                               "assert not list(s.Path(%r).glob('*.start'))\n"
                                               "with s.enabling():\n    os._exit(3)\n" % str(self.folder))
        self.assertEqual(code, 3, output)
        self.assertEqual(len(safe.interrupted(self.folder)), 1)

    def test_a_read_that_finishes_lets_its_record_go_after_settling(self):
        code, output = in_session(self.config, (
            "s.start(bpy); s.stop()\n"
            "with s.enabling():\n    pass\n"
            "assert len(list(s.Path(%r).glob('*.start'))) == 1, 'held while the add-ons may still crash'\n"
            "done = s._state['completion']\n"
            "assert not done.step(done.ready_at + s.SETTLE_SECONDS - 0.1, False)\n"
            "assert done.step(done.ready_at + s.SETTLE_SECONDS, False)\n"
            "s._completed()\n"
            "assert not list(s.Path(%r).glob('*.start'))\n"
            "assert saved == [], 'nothing was left off: nothing to save'\n"
            "print('OK')\n") % (str(self.folder), str(self.folder)))
        self.assertEqual((code, output.strip()), (0, "OK"))

    def test_a_read_during_the_start_keeps_the_starts_own_record(self):
        code, output = in_session(self.config, (
            "s.start(bpy)\n"
            "held = s._state['held']\n"
            "with s.enabling():\n    pass\n"
            "assert s._state['held'] is held and s._state['completion'].ready_at is None\n"
            "assert len(list(s.Path(%r).glob('*.start'))) == 1\n"
            "s.stop(); print('OK')\n") % str(self.folder))
        self.assertEqual((code, output.strip()), (0, "OK"))

    def test_a_run_that_keeps_no_record_keeps_none_for_a_read(self):
        code, output = in_session(self.config, "with s.enabling():\n    pass\nprint('OK')\n")
        self.assertEqual((code, output.strip()), (0, "OK"))
        self.assertEqual(self.records(), [])

    def test_add_ons_left_off_are_saved_off_once(self):
        code, output = in_session(self.config, (
            "s.start(bpy)\n"
            "s._state['left_off'] = ['hair_tool']\n"
            "s._completed()\n"
            "with s.enabling():\n    pass\n"
            "s._completed()\n"
            "assert saved == [1], saved\n"
            "assert not list(s.Path(%r).glob('*.start'))\n"
            "print('OK')\n") % str(self.folder))
        self.assertEqual((code, output.strip()), (0, "OK"))


class WiringTests(unittest.TestCase):
    def test_the_startup_module_starts_before_add_ons_and_ends_on_exit(self):
        source = (ROOT / "scripts/startup/angelo_start_guard.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("angelo_safe_start.start(", functions["register"])
        self.assertIn("angelo_safe_start.stop(", functions["unregister"])

    def test_no_startup_module_shares_a_name_with_a_module(self):
        """A startup module's ``import <name>`` finds itself when a module of the same name lies in scripts/modules:
        Blender imports startup modules by name, so the first one wins (an earlier draft of this guard did that)."""
        startup = {p.stem for p in (ROOT / "scripts/startup").glob("*.py")}
        modules = {p.stem for p in (ROOT / "scripts/modules").glob("*.py")} | {
            p.name for p in (ROOT / "scripts/modules").iterdir() if (p / "__init__.py").is_file()}
        self.assertEqual(sorted(startup & modules), [])

    def test_the_module_needs_no_bpy_to_import(self):
        for name in ("angelo_safe_start.py", "angelo_start_records.py"):
            tree = ast.parse((ROOT / "scripts/modules" / name).read_text(encoding="utf-8"))
            top = {a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                   for a in n.names} | {n.module for n in tree.body if isinstance(n, ast.ImportFrom)}
            self.assertNotIn("bpy", top, name)

    def test_every_preference_read_holds_a_record_while_its_add_ons_load(self):
        """Blender enables the add-ons again on every preference read through ``addon_utils.reset_all``, which Angelo
        wraps for the session (``prefs_reload``): the settings import, Revert to Saved Preferences, factory
        preferences, an application template."""
        source = (ROOT / "scripts/addons_core/angelo_agent/prefs_reload.py").read_text(encoding="utf-8")
        self.assertIn("angelo_safe_start.enabling()", source)
        tree = ast.parse(source)
        reset = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_reset_all")
        held = [w for w in ast.walk(reset) if isinstance(w, ast.With)
                and any("enabling" in ast.unparse(item.context_expr) for item in w.items)]
        self.assertTrue(held, "reset_all enables the add-ons inside angelo_safe_start.enabling()")
        self.assertIn("_blender[1](", ast.unparse(held[0]))

    def test_the_start_waits_for_angelos_start_up_through_one_public_check(self):
        source = (ROOT / "scripts/modules/angelo_safe_start.py").read_text(encoding="utf-8")
        self.assertIn("startup_done", source)
        intro = ast.parse((ROOT / "scripts/addons_core/angelo_agent/intro.py").read_text(encoding="utf-8"))
        self.assertIn("startup_done", {n.name for n in intro.body if isinstance(n, ast.FunctionDef)})


class NoticeTests(unittest.TestCase):
    def test_the_notice_is_translatable_and_names_what_was_left_off(self):
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        source = (ROOT / "scripts/addons_core/angelo_agent/safe_start_notice.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        marked = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "n_"]
        self.assertGreaterEqual(len(marked), 2, "title and body go through the i18n pipeline (n_ / iface_)")
        texts = [n.args[0].value for n in marked if n.args and isinstance(n.args[0], ast.Constant)]
        self.assertTrue(any("{names}" in text for text in texts), texts)
        self.assertIn("iface_(", source)


if __name__ == "__main__":
    unittest.main()
