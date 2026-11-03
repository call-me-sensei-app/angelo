# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading an engine project's folders is bounded and never on Angelo's main thread (``engine_link_reads``,
doc/angelo/engine_link.rst, "Reading the project"), on the real ``engine_link_assets``, ``engine_link_tool_open`` and
``engine_link``.

Covered (audit of PR #129, 2026-11-03): the by-name lookup's 20,000-entry bound was checked only after a folder was
read whole and sorted (a 100,000-entry folder was read whole), synchronously on the host path. The class: every folder
walk of the engine link reads entry by entry and stops at its entry and time bounds (the by-name lookup, the
hard-link check for Angelo's working file, the path scrubber's root and home listings), and resolving an asset, for
the agent's OPEN and for an editor's own Edit in Angelo, runs off the main thread, the editor's requests kept in order.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import machine_clock  # noqa: E402
import test_native_engine_link_tool as base  # noqa: E402  (its stand-in bpy and fixtures)

engine_link, host, assets, EDIT, RefusalError = base.engine_link, base.host, base.assets, base.EDIT, base.RefusalError
reads, opening, godot = assets.reads, host.opening, base.godot_link_assets


class Entry:
    """A folder entry as ``os.scandir`` gives it."""

    def __init__(self, folder, name, directory=False, inode=None):
        self.name, self.path, self._directory = name, os.path.join(folder, name), directory
        self._inode = inode if inode is not None else hash(self.path) & 0xFFFFFFFF

    def is_dir(self, follow_symlinks=True):
        return self._directory

    def is_symlink(self):
        return False

    def is_junction(self):
        return False

    def inode(self):
        return self._inode

    def stat(self, follow_symlinks=True):
        return os.stat_result((0o100644, self._inode, 0, 1, 0, 0, 0, 0, 0, 0))


class Huge:
    """``os.scandir`` for folders of ``size`` entries each (named f000000...), counting the entries handed out; the
    folders listed in ``folders`` hold that many subfolders instead. Nothing is listed until asked for."""

    def __init__(self, size, folders=(), delay=0.0):
        self.size, self.folders, self.delay, self.read, self.closed = size, set(folders), delay, 0, 0

    def __call__(self, folder):
        folder = str(folder)
        directory = folder in self.folders

        def listing():
            for index in range(self.size):
                self.read += 1
                if self.delay:
                    time.sleep(self.delay)
                yield Entry(folder, ("d%06d" if directory else "f%06d.txt") % index, directory)

        return Lister(listing(), self)


class Lister:
    def __init__(self, entries, owner):
        self.entries, self.owner = entries, owner

    def __iter__(self):
        return self.entries

    def close(self):
        self.owner.closed += 1


class BoundedWalkTests(unittest.TestCase):
    def test_a_huge_folder_is_read_only_up_to_the_lookup_bound(self):
        """The audit's case: one folder of 100,000 entries; and, held out, the bound reached across many folders."""
        for size, folders in ((100000, ()), (300, {"/p/" + "d%06d" % i for i in range(300)} | {"/p"})):
            with self.subTest(size=size), mock.patch.object(assets.os, "scandir", Huge(size, folders)) as listed:
                found, complete = assets.named(godot, "/p", "barrel")
                self.assertEqual((found, complete), ([], False))
                self.assertLessEqual(listed.read, assets.NAME_SCAN + 1)
                self.assertGreaterEqual(listed.closed, 1)   # The listing it stopped in is closed, not left open.

    def test_the_hard_link_check_reads_no_more_than_its_bound_and_counts_as_held_past_it(self):
        info = os.stat_result((0o100644, 7, 0, 2, 0, 0, 0, 0, 0, 0))
        with mock.patch.object(assets.os, "scandir", Huge(200000)) as listed, \
                mock.patch.object(assets.os, "stat", lambda path, **_: os.stat_result((0o100644, hash(str(path)) &
                                                                                       0xFFFFFFFF, 0, 1, 0, 0, 0, 0, 0,
                                                                                       0))):
            self.assertTrue(assets._holds_inode(Path("/w"), info))
        self.assertLessEqual(listed.read, assets.WORKING_SCAN_LIMIT + 1)

    def test_a_walk_past_its_time_stops_and_is_never_taken_as_complete(self):
        slow = Huge(5000, delay=0.002)
        with mock.patch.object(assets.os, "scandir", slow):
            lookup = reads.Lookup(lambda: assets.named(godot, "/p", "barrel"), seconds=0.05)
            lookup.wait(5)
        self.assertEqual(lookup.outcome, ("late", None))   # Unfinished for time: refused as unreadable (9-L1).
        self.assertLess(slow.read, 5000)
        info = os.stat_result((0o100644, 7, 0, 2, 0, 0, 0, 0, 0, 0))
        with mock.patch.object(assets.os, "scandir", Huge(5000, delay=0.002)), \
                mock.patch.object(assets.os, "stat", lambda path, **_: os.stat_result((0o100644, hash(str(path)) &
                                                                                       0xFFFFFFFF, 0, 1, 0, 0, 0, 0, 0,
                                                                                       0))):
            lookup = reads.Lookup(lambda: assets._holds_inode(Path("/w"), info), seconds=0.05)
            lookup.wait(5)
        self.assertEqual(lookup.outcome, ("late", None))   # Unchecked: refused as unreadable, never "outside".

    def test_an_entry_gone_while_checked_still_counts_as_held(self):
        """The per-entry rule survives reading entry by entry: only a folder gone before it is listed is skipped."""
        info = os.stat_result((0o100644, 7, 0, 2, 0, 0, 0, 0, 0, 0))

        def stat(path, **_):
            raise FileNotFoundError(path)
        with mock.patch.object(assets.os, "scandir", Huge(3)), mock.patch.object(assets.os, "stat", stat):
            self.assertTrue(assets._holds_inode(Path("/w"), info))

    def test_unreals_project_check_reads_no_more_than_its_bound(self):
        from angelo_agent import unreal_link_assets as unreal
        with mock.patch.object(assets.os, "scandir", Huge(100000)) as listed:
            self.assertFalse(unreal.is_project("/u"))
        self.assertLessEqual(listed.read, unreal.PROJECT_SCAN)

    def test_the_scrubbers_root_and_home_listings_are_bounded_and_fail_closed(self):
        from angelo_agent import engine_link_facts as facts
        self.addCleanup(facts._places_read.clear)
        facts._places_read.clear()
        with mock.patch("os.scandir", Huge(facts.PLACES_SCAN + 50)) as listed:
            self.assertIsNone(facts._places())   # No slash inside a word is then trusted.
        self.assertLessEqual(listed.read, facts.PLACES_SCAN + 1)


class InboxCostTests(unittest.TestCase):
    def test_a_pass_costs_in_proportion_to_the_requests_waiting(self):
        """Break-it 10-N3: a flood of acknowledgements from one editor never costs a pass more than linear time."""
        def seconds(count):
            server = mock.Mock()
            server.drain.return_value = [(1, {"type": "status", "code": "previewed"})] * count
            server.info_of.return_value = {}
            started = time.perf_counter()
            handled = sum(1 for _ in reads.Inbox().ready(server, lambda *_: None))
            self.assertEqual(handled, count)
            return time.perf_counter() - started
        # 8x the requests: linear costs about 8x, the quadratic pass about 64x. The bound sits between with room for a
        # loaded machine's noise (the best of five), so only the shape of the cost decides it.
        small, large = min(seconds(4000) for _ in range(5)), min(seconds(32000) for _ in range(5))
        self.assertLess(large / max(small, 1e-6), 24.0)


class OffMainThreadTests(unittest.TestCase):
    setUp, asked, link = base.ToolTests.setUp, base.ToolTests.asked, base.ToolTests.link   # Its fixtures alone.

    def held(self):
        """``engine_link_names.resolve`` held until released: a project on a slow drive."""
        gate, real = threading.Event(), opening.names.resolve
        self.addCleanup(gate.set)

        def resolve(*args):
            gate.wait(10)
            return real(*args)
        self.enterContext(mock.patch.object(opening.names, "resolve", resolve))
        return gate

    def open(self, asset="res://crate.glb"):
        started = time.monotonic()
        result = host.execute(None, EDIT, {"action": "OPEN", "asset": asset}, base.SimpleNamespace())
        return result, time.monotonic() - started

    def test_a_slow_project_never_holds_the_main_thread_and_the_card_comes_after(self):
        self.server.editor(1)
        events, gate = self.asked(), self.held()
        result, waited = self.open()
        # Held, the read takes the gate's 10 s; the call must return long before that. Half of it leaves room for a cold
        # first import on a slow machine (a fixed 0.5 s failed there) and still tells waiting from not waiting.
        self.assertLess(waited, 5.0)
        self.assertFalse(isinstance(result, dict))   # Pending: the controller polls it.
        self.assertIsNone(result.poll())
        self.assertEqual(events, [])
        gate.set()
        found = None
        for _ in range(200):
            found = result.poll()
            if found is not None:
                break
            time.sleep(0.01)
        self.assertEqual((found["asked"], events), ("res://crate.glb", ["res://crate.glb"]))

    def test_a_refusal_after_reading_is_the_same_unchanged_result_a_plan_corrects(self):
        from angelo_agent.claude_outcomes import correctable_failure
        self.server.editor(1)
        gate = self.held()
        result, _ = self.open("res://missing.glb")
        gate.set()
        while (found := result.poll()) is None:
            time.sleep(0.01)
        self.assertEqual((found["error_code"], found["scene_changed"]), ("VALIDATION", False))
        self.assertIn("doesn't exist yet", found["error"])
        self.assertIsNotNone(correctable_failure([{"tool": EDIT, "arguments": {"action": "OPEN", "asset": "x"}}],
                                                 [found]))

    def test_a_project_unread_in_time_is_refused_and_nothing_is_asked(self):
        self.server.editor(1)
        events, _gate = self.asked(), self.held()
        with mock.patch.object(reads, "READ_S", 0.1):
            result, _ = self.open()
            time.sleep(0.3)
            found = result.poll()
        self.assertEqual((found["error_code"], found["scene_changed"], events), ("VALIDATION", False, []))
        self.assertIn("couldn't read the engine's project in time", found["error"])

    def test_an_editors_request_resolves_off_the_main_thread_in_the_order_sent(self):
        """Held out from OPEN: the editor's own Edit in Angelo, and a close it sent after it, which waits its turn."""
        self.server.editor(1)
        events, gate, sent = self.asked(), threading.Event(), []
        self.addCleanup(gate.set)
        real = godot.resolve

        def resolve(request):
            gate.wait(10)
            return real(request)
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        sent.extend([(1, {"type": "open", "id": "a", "asset": "res://crate.glb", "project": str(self.project)}),
                     (1, {"type": "close", "id": "b", "asset": "res://crate.glb"})])
        with mock.patch.object(godot, "resolve", resolve):
            window = machine_clock.start(self)
            engine_link._tick()
            window.assert_within(0.5, "the tick waits for no resolve")
            self.assertEqual((events, self.server.sent), ([], []))   # Nothing answered yet, the close included.
            gate.set()
            for _ in range(200):
                engine_link._tick()
                if self.server.sent:
                    break
                time.sleep(0.01)
        self.assertEqual([header["type"] for _client, header in self.server.sent], ["pending", "closed"])
        self.assertEqual(events, ["res://crate.glb"])

    def test_a_slow_editor_never_holds_another_editors_requests(self):
        """Break-it 9-M2: only an editor's own later requests wait for its slow one; another's acknowledgements, which
        PUSH and SAVE wait for, and its own requests flow at once."""
        self.server.editor(1)
        self.server.editor(2)
        self.link()
        gate, sent = threading.Event(), []
        self.addCleanup(gate.set)
        real = godot.resolve
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        sent.extend([(1, {"type": "open", "id": "a", "asset": "res://crate.blend", "project": str(self.project)}),
                     (2, {"type": "status", "asset": "res://crate.glb", "code": "previewed", "version": 1}),
                     (1, {"type": "close", "id": "b", "asset": "res://crate.blend"}),
                     (2, {"type": "close", "id": "c", "asset": "res://other.glb"})])   # Not the linked one.
        with mock.patch.object(godot, "resolve", lambda request: gate.wait(10) and real(request)):
            window = machine_clock.start(self)
            engine_link._tick()
            window.assert_within(0.5, "the tick waits for no resolve")
            self.assertEqual([(client, header["type"]) for client, header in self.server.sent], [(2, "closed")])
            self.assertIn(2, engine_link._state["facts"]["editors"])   # Its acknowledgement was read.
            gate.set()
            for _ in range(200):
                engine_link._tick()
                if len(self.server.sent) >= 3:
                    break
                time.sleep(0.01)
        self.assertEqual([(client, header["type"]) for client, header in self.server.sent if client == 1],
                         [(1, "pending"), (1, "closed")])   # Editor 1's own order held.

    def test_the_card_answers_the_request_sent_last_whichever_is_read_first(self):
        """Break-it 10-N2: editor 1 asks first on a slow drive, editor 2 next on a fast one; 2's card stays, 1's
        request is refused as superseded (as when both resolve at once), and 1's acknowledgements pass its open."""
        self.server.editor(1)
        self.server.editor(2)
        (self.project / "barrel.glb").write_bytes(b"x")
        events, gate, sent = self.asked(), threading.Event(), []
        self.addCleanup(gate.set)
        real = godot.resolve

        def resolve(request):
            if request["asset"] == "res://crate.glb":
                gate.wait(10)
            return real(request)
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        sent.extend([(1, {"type": "open", "id": "a", "asset": "res://crate.glb", "project": str(self.project)}),
                     (2, {"type": "open", "id": "b", "asset": "res://barrel.glb", "project": str(self.project)}),
                     (1, {"type": "status", "code": "previewed", "asset": "res://crate.glb", "version": 1})])
        with mock.patch.object(godot, "resolve", resolve):
            engine_link._tick()
            self.assertEqual(events, ["res://barrel.glb"])
            held = [entry[1]["type"] for entry in reads.inbox._queue if not entry[4]]
            self.assertEqual(held, ["open"])   # Editor 1's status passed its own slow open; only the open waits.
            gate.set()
            for _ in range(200):
                engine_link._tick()
                if any(client == 1 for client, _header in self.server.sent):
                    break
                time.sleep(0.01)
        self.assertEqual(engine_link.pending()["asset"].ident, "res://barrel.glb")
        self.assertEqual(events, ["res://barrel.glb"])
        self.assertIn((1, "refused", "superseded"), [(client, header["type"], header.get("code"))
                                                     for client, header in self.server.sent])

    def test_an_earlier_request_read_after_the_later_card_was_answered_raises_none(self):
        """Break-it 10-N2b: the person answered the card of the request sent last; one sent before it, read later,
        never puts a card back in front of them."""
        self.server.editor(1)
        self.server.editor(2)
        (self.project / "barrel.glb").write_bytes(b"x")
        events, gate, sent = self.asked(), threading.Event(), []
        self.addCleanup(gate.set)
        real = godot.resolve
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        sent.extend([(1, {"type": "open", "id": "a", "asset": "res://crate.glb", "project": str(self.project)}),
                     (2, {"type": "open", "id": "b", "asset": "res://barrel.glb", "project": str(self.project)})])
        def resolve(request):   # Editor 1's project is on a slow drive.
            return (request["asset"] != "res://crate.glb" or gate.wait(10)) and real(request)
        with mock.patch.object(godot, "resolve", resolve):
            engine_link._tick()
            engine_link.decline()   # The person's Not now on editor 2's card.
            gate.set()
            for _ in range(200):
                engine_link._tick()
                if any(client == 1 for client, _header in self.server.sent):
                    break
                time.sleep(0.01)
        self.assertIsNone(engine_link.pending())
        self.assertEqual(events, ["res://barrel.glb"])
        self.assertIn((1, "refused", "superseded"), [(client, header["type"], header.get("code"))
                                                     for client, header in self.server.sent])

    def test_a_resolve_that_cant_start_is_refused_and_the_link_goes_on(self):
        """Break-it 10-N4: no thread to read on (stalled drives' threads piled up) never stops the link's timer."""
        self.server.editor(1)
        sent = [(1, {"type": "open", "id": "a", "asset": "res://crate.glb", "project": str(self.project)})]
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        with mock.patch.object(reads.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            self.assertEqual(engine_link._tick(), engine_link.POLL_SECONDS)
            (_client, header), = self.server.sent
            self.assertEqual((header["type"], header["code"]), ("refused", "unreadable"))
            with self.assertRaises(RefusalError) as refused:
                host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.glb"}, base.SimpleNamespace())
        self.assertEqual(refused.exception.code, "INTERNAL_ERROR")

    def test_an_editors_request_unread_in_time_is_refused_with_its_reason(self):
        self.server.editor(1)
        gate, sent = threading.Event(), [(1, {"type": "open", "id": "a", "asset": "res://crate.glb",
                                              "project": str(self.project)})]
        self.addCleanup(gate.set)
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        with mock.patch.object(godot, "resolve", lambda request: gate.wait(10)), \
                mock.patch.object(reads, "READ_S", 0.1):
            engine_link._tick()
            time.sleep(0.3)
            engine_link._tick()
        (_client, header), = self.server.sent
        self.assertEqual((header["type"], header["code"]), ("refused", "unreadable"))
        self.assertIn("couldn't read the project in time", header["message"])

    def test_a_request_waiting_for_its_asset_never_reaches_a_link_started_again(self):
        self.server.editor(1)
        gate, sent = threading.Event(), [(1, {"type": "open", "id": "a", "asset": "res://crate.glb",
                                              "project": str(self.project)})]
        self.addCleanup(gate.set)
        self.server.drain = lambda: [sent.pop(0) for _ in list(sent)]
        with mock.patch.object(godot, "resolve", lambda request: gate.wait(10)):
            engine_link._tick()
        restarted = base.FakeServer(self.project)
        restarted.editor(1)
        engine_link._state["server"] = restarted
        gate.set()
        time.sleep(0.05)
        engine_link._tick()
        self.assertEqual((self.server.sent, restarted.sent), ([], []))


if __name__ == "__main__":
    unittest.main()
