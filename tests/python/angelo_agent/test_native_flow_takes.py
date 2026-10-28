# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Redo: a generator step's take number makes a fresh generation of an unchanged request, without Blender."""

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.flow_graph import input_key
from angelo_agent.flow_spec import FORMAT, FlowError, MAX_TAKE, as_definition, normalize, revision

ACCOUNT = str(uuid.UUID(int=7))


def flow(**fields):
    return normalize({"format": FORMAT, "nodes": [
        {"id": "Sheet", "type": "GENERATE_IMAGE",
         "fields": dict({"connection_id": ACCOUNT, "model": "gpt-image-2.5-sunburst", "prompt": "a parts sheet"}, **fields)}],
        "links": []})


def sheet(value):
    return value["nodes"][0]


class TakeTests(unittest.TestCase):
    def test_the_first_take_keeps_every_existing_key_and_revision(self):
        first, explicit = flow(), flow(take=0)
        self.assertNotIn("take", sheet(first))
        self.assertEqual(first, explicit)
        self.assertEqual(revision(first), revision(explicit))
        self.assertEqual(input_key(sheet(first), "a parts sheet", []), input_key(sheet(explicit), "a parts sheet", []))

    def test_a_new_take_is_a_new_request(self):
        keys = {input_key(sheet(flow(take=take)), "a parts sheet", []) for take in (0, 1, 2)}
        self.assertEqual(len(keys), 3)

    def test_a_take_round_trips_and_is_bounded(self):
        again = normalize(as_definition(flow(take=3)))
        self.assertEqual(sheet(again)["take"], 3)
        for bad in (-1, MAX_TAKE + 1, 1.5, True, "2"):
            with self.subTest(take=bad), self.assertRaises(FlowError):
                flow(take=bad)


if __name__ == "__main__":
    unittest.main()
