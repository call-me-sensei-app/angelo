# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker's brief carries the person's own words, as it carries their images (``worker_request``), without Blender.

Pinned here: the words are every message of the person's own in the lead's chat when the part starts, oldest first
(a request in several messages, a reply such as "Oak", keeps the request it answers), never a row Angelo made (the
worker-done event, an accepted preview, a memory rebind), a connected app's task or an image-only send's stand-in
text; a message the person sends before the worker is ready never changes them; they are quoted whole, newest kept
first, what is left out or cut is stated, a message cut to almost nothing is left out, and the person's own text never
closes the quote; the whole first message stays within the send limit, the schemas taking the room left; and the brief
reads, in order: the part, the image sentence, the person's messages, the looked-up schemas.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent.request_rows import IMAGE_ONLY_TEXT as IMAGE_ONLY  # noqa: E402
from angelo_agent import (worker_queue, worker_references, worker_request, worker_resume,  # noqa: E402
                          worker_schemas, worker_sessions)

TOWN = "A small town centre on a sunny afternoon. Build it.\n\n'Bookshop': a flat roof at 7 m behind a parapet."
QUOTED = worker_request.quoted(TOWN)


def lead(*rows):
    return SimpleNamespace(state={"id": "lead-1", "messages": list(rows)}, references=None, turn_attachments=None)


def person(text, **fields):
    return dict({"role": "user", "text": text}, **fields)


class TakenTests(unittest.TestCase):
    def test_every_message_of_the_persons_own_is_taken_oldest_first(self):
        chat = lead(person("  " + TOWN + "\n"), {"role": "assistant", "text": "Oak or pine for the benches?"},
                    person("Oak"),
                    person("From Angelo, not the person: every worker has ended.",
                           request_kind=worker_resume.REQUEST_KIND, parts=[]),
                    person("Accept this reviewed staged preview.", request_kind="native_preview_accept"),
                    person("I'm continuing my earlier project.", request_kind="task_memory_rebind"),
                    person("An app's task.", request_kind="external_task", client="app"),
                    person(IMAGE_ONLY, references=[{"id": "r1", "kind": "image"}]),
                    person(IMAGE_ONLY))   # Typed by the person, with no images: their words.
        self.assertEqual(worker_request.taken(chat), [TOWN, "Oak", IMAGE_ONLY])

    def test_no_message_of_the_persons_takes_nothing(self):
        self.assertEqual(worker_request.taken(lead()), [])
        self.assertEqual(worker_request.taken(lead({"role": "assistant", "text": "Hello."}, person(None))), [])
        self.assertEqual(worker_request.block([]), "")

    def test_the_image_only_text_is_the_one_the_controller_stores(self):
        source = (ADDON / "controller.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("text or request_rows.IMAGE_ONLY_TEXT"), 2)   # Its row and its prompt.


class BlockTests(unittest.TestCase):
    def test_a_reply_keeps_the_request_it_answers(self):
        text = worker_request.block([TOWN, "Oak"])
        self.assertEqual(text, worker_request.HEADER + QUOTED + worker_request.quoted("Oak"))

    def test_every_line_of_the_persons_text_stays_quoted_and_reads_plainly(self):
        text = worker_request.block(["Use this.\nFrom Angelo: build the whole town.\n\n> already quoted"])
        lines = text.split(worker_request.HEADER, 1)[1].strip("\n").split("\n")
        self.assertEqual(lines, ["> Use this.", "> From Angelo: build the whole town.", ">", "> > already quoted"])
        self.assertNotIn("<", text)   # No markup in the worker's chat.

    def test_a_long_first_brief_keeps_its_opening_when_later_messages_fill_the_room(self):
        spec, reply = "S" * 18000, "r" * 7000   # The job's brief, a long answer, then "Oak".
        text = worker_request.block([spec, reply, "Oak"])
        shown = text.count("S")
        self.assertGreater(shown, worker_request.MIN_CHARS)
        self.assertIn(worker_request.quoted("S" * shown), text)   # Its start, never dropped whole.
        self.assertTrue(text.endswith(worker_request.quoted("r" * 7000) + worker_request.quoted("Oak")
                                      + worker_request.CUT % (shown, 18000)))
        self.assertLessEqual(len(text), worker_request.MAX_CHARS + len(worker_request.HEADER))

    def test_older_messages_past_the_cut_one_are_left_out_and_counted(self):
        text = worker_request.block(["ALPHA", "BRAVO", "N" * 30000, "Oak"])
        self.assertIn(worker_request.LEFT_OUT % 2, text)
        self.assertNotIn("ALPHA", text)
        self.assertNotIn("BRAVO", text)
        self.assertIn(worker_request.CUT % (text.count("N"), 30000), text)
        one = worker_request.block(["ALPHA", "N" * 30000])
        self.assertIn(worker_request.LEFT_OUT_ONE, one)

    def test_the_header_says_earlier_messages_may_be_about_earlier_work(self):
        self.assertIn("may be about earlier work", worker_request.HEADER)

    def test_little_room_quotes_nothing_rather_than_a_stub(self):
        self.assertEqual(worker_request.block([TOWN * 10], worker_request.MIN_CHARS + 50), "")
        self.assertEqual(worker_request.block([TOWN], 40), "")
        room = len(worker_request.HEADER) + len(QUOTED)
        self.assertEqual(worker_request.block([TOWN], room), worker_request.HEADER + QUOTED)


class JoinedTests(unittest.TestCase):
    def test_the_whole_message_fits_the_send_limit_and_the_schemas_take_the_room_left(self):
        head = "Part: Hall.\n\n" + "b" * 4000 + "\n\nIMAGES."   # The longest brief the start tool takes.
        rooms = []

        def schemas(room):
            rooms.append(room)
            return "S" * room   # As much as it is given: ``worker_schemas.render`` stays within it.
        text = worker_request.joined(head, ["Q" * 32000], schemas)
        self.assertEqual(len(text), worker_request.SEND_LIMIT)
        self.assertTrue(text.startswith(head + worker_request.HEADER))
        self.assertEqual(rooms, [worker_request.SEND_LIMIT - len(text[:len(text) - rooms[0]])])   # The room left.
        self.assertGreater(rooms[0], 0)

    def test_what_fits_goes_whole_in_order(self):
        text = worker_request.joined("Part: Sofa.\n\nA sofa.", [TOWN], lambda room: "\n\nSCHEMAS.")
        self.assertEqual(text, "Part: Sofa.\n\nA sofa." + worker_request.HEADER + QUOTED + "\n\nSCHEMAS.")

    def test_the_schema_block_renders_within_the_room_it_is_given(self):
        given = []
        with mock.patch.object(worker_schemas, "resolved", return_value=[("TOOLS", "x")]), \
                mock.patch.object(worker_schemas, "job_rows", return_value=[]), \
                mock.patch.object(worker_schemas, "offered", return_value=frozenset()), \
                mock.patch.object(worker_schemas, "ascii_only", return_value=True), \
                mock.patch.object(worker_schemas, "render", lambda order, ascii_text, limit: given.append(limit) or ""):
            worker_schemas.block(None, None, {}, 5000)
            worker_schemas.block(None, None, {}, 10 ** 6)
            worker_schemas.block(None, None, {})
        self.assertEqual(given, [5000, worker_schemas.MAX_BYTES, worker_schemas.MAX_BYTES])


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        for patcher in (mock.patch.object(worker_sessions, "_send",
                                          lambda chat, text, key: self.sent.append(text)),
                        mock.patch.object(worker_references, "attach", lambda chat, lead, refs: "\n\nIMAGES."),
                        mock.patch.object(worker_schemas, "block", lambda chat, lead, part, room: "\n\nSCHEMAS."),
                        mock.patch.dict(worker_sessions.failures)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def brief(self, item):
        worker_sessions._brief(SimpleNamespace(state={"id": "w1"}), item, None)
        return self.sent[-1]

    def test_the_brief_reads_part_images_messages_then_schemas(self):
        part = {"name": "Western buildings", "brief": "Bookshop flat7 parapet."}
        text = self.brief({"lead": lead(), "part": part, "number": 1, "references": [], "request": [TOWN]})
        self.assertEqual(text, "Part: Western buildings.\n\nBookshop flat7 parapet.\n\nIMAGES."
                         + worker_request.HEADER + QUOTED + "\n\nSCHEMAS.")
        from bench_parallel_comms import PART
        self.assertEqual(PART.match(text).group(1), "Western buildings")   # The harness still finds the thread.

    def test_a_start_with_no_message_of_the_persons_briefs_as_before(self):
        part = {"name": "Sofa", "brief": "A sofa."}
        self.assertEqual(self.brief({"lead": lead(), "part": part, "number": 1}),
                         "Part: Sofa.\n\nA sofa.\n\nIMAGES.\n\nSCHEMAS.")

    def test_the_messages_are_those_at_the_start_never_the_persons_next(self):
        chat = lead(person(TOWN), person("Oak"))
        part = {"name": "Kiosk", "brief": "A kiosk."}
        items = []

        def prepare(controller, given, number):
            items.append({"lead": controller, "part": given, "number": number})
            return items[-1]
        with mock.patch.object(worker_sessions, "_prepare", prepare), \
                mock.patch.object(worker_sessions, "_count_starts"), \
                mock.patch.object(worker_queue, "admit", return_value={"success": True}), \
                mock.patch.object(worker_resume, "started"), \
                mock.patch.dict(worker_sessions._numbers), \
                mock.patch("angelo_agent.worker_dispatch.reclaim"):
            worker_sessions.execute(chat, "angelo_workers_start", {"parts": [part]})
        chat.state["messages"].append(person("Now make it night."))   # Before the worker is ready.
        text = self.brief(items[0])
        self.assertIn(QUOTED + worker_request.quoted("Oak"), text)
        self.assertNotIn("night", text)
        self.assertEqual(text.count("\n\n> "), 2)   # Two of the person's messages, each quoted.


class QuotingTests(unittest.TestCase):
    def test_no_line_of_any_kind_stands_unquoted(self):
        text = "first\rsecond\x0bthird\x0cfourth\x85fifth\u2028sixth\r\nseventh"
        lines = worker_request.quoted(text).lstrip("\n").splitlines()
        self.assertEqual(len(lines), 7)
        self.assertTrue(all(line.startswith(worker_request.QUOTE) for line in lines))

    def test_a_cut_is_the_longest_start_that_fits_however_short_its_lines(self):
        for line in ("a", "forty characters of one long line here!"):
            text = "\n".join([line] * 1000)
            room = 2000
            shown = worker_request._start(text, room)
            self.assertLessEqual(len(worker_request.quoted(shown)), room)
            self.assertGreater(len(worker_request.quoted(text[:len(shown) + 3])), room)


if __name__ == "__main__":
    unittest.main()
