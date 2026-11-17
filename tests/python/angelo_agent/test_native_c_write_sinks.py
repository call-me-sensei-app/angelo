# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every place Blender's C and C++ open a file for writing is accounted for (``c_write_sinks``; the re-read of
0a0d4e95743, MUST-3): a writer whose path comes from data asks the writers' gate with its declared inputs before it
writes, in its own function or where its caller sets them; every other site says why its path is not from data a
script sets. A new writing call fails here until it is gated or explained. Runs outside Blender (source text only).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest

import c_write_sinks as sinks


class CWriteSinksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = sinks.scan()

    def test_every_writing_call_is_accounted_for(self):
        unlisted = sorted("%s (line %d)" % (key, places[0][1]) for key, places in self.found.items()
                          if key not in sinks.SITES)
        self.assertEqual(unlisted, [], "gate these writes with their declared inputs, or say why their path is "
                                       "never from data (c_write_sinks.SITES)")

    def test_no_listed_site_is_gone(self):
        self.assertEqual(sorted(set(sinks.SITES) - set(self.found)), [], "a listed site no longer exists")

    def test_judged_sites_ask_before_they_write(self):
        for key, why in sinks.SITES.items():
            if why != sinks.JUDGED or key not in self.found:
                continue
            for relative, line, body in self.found[key]:
                with self.subTest(site=key, line=line):
                    self.assertRegex(body, sinks.GATE, "no gate asked between the function's start and the write")

    def test_callers_named_ask(self):
        for key, why in sinks.SITES.items():
            if not isinstance(why, tuple):
                continue
            relative, name = why[1].split("::", 1)
            with self.subTest(site=key, caller=why[1]):
                body = sinks.function_body(relative, name)
                self.assertIsNotNone(body, "the caller named is gone")
                self.assertRegex(body, sinks.GATE, "the caller named asks no gate")

    def test_the_reviews_writers_are_judged(self):
        """The two the re-read found ungated (multires displacements on every save; Save Sequence), and the bake
        unpack that removes and writes a bake's folders."""
        for key in ("blenkernel/intern/customdata.cc::CustomData_external_write::cdf_write_open",
                    "editors/space_image/image_ops.cc::image_save_sequence_exec::IMB_save_image"):
            with self.subTest(site=key):
                self.assertEqual(sinks.SITES.get(key), sinks.JUDGED)
        unpack = sinks.function_body("blenkernel/intern/bake_geometry_nodes_modifier_pack.cc",
                                     "unpack_geometry_nodes_bake")
        self.assertEqual(unpack.count("if (angelo_refused(bake_path)) {"), 4, "every unpack branch, before deleting")
        # A refused unpack leaves the bake as it was: its local folder is adopted only once the unpack may go ahead,
        # and an image keeps a packed file whose unpack failed (the re-read of cf5befe2877, M2's class).
        self.assertEqual(unpack.count("        return UnpackGeometryNodesBakeResult::Error;\n      }\n      adopt(resolved);"),
                         4, "the bake's folder changes only after the check, in every branch")
        self.assertNotIn("bake.directory = BLI_strdup", unpack.split("auto adopt = ")[0],
                         "nothing before adopt() rewrites the bake's folder")
        image = sinks.function_body("blenkernel/intern/packedFile.cc", "BKE_packedfile_unpack_image")
        self.assertIn("ret_value = RET_ERROR; /* Kept packed. */", image)


if __name__ == "__main__":
    unittest.main()
