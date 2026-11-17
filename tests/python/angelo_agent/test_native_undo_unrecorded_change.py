# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pin how memfile Undo handles changes no undo step recorded; the GUI suites prove the behaviour.

Memfile Undo reused every ID whose stored chunks matched the step it came from, which holds only while the live Main
is that step. A change with no undo step (a script, add-on, handler or timer) broke that: a reused ID still pointed
at one the read freed, and the next depsgraph build or outliner draw crashed (learn-l3, 2026-11-12; stock Blender
5.3.0 too). readfile now reads again, at its old address, every ID that ``read_undo_ids_to_reread`` returns: the IDs
whose pointers changed in place since the last memfile write or read (recorded by remapping, and the owner a freed ID
points back at), and the IDs pointing at one the step does not store (a walk, run only when some old ID is not
stored, checked over the old ID map). Each memfile records the session UIDs it stores as it is written; a linked ID
matches by name only inside a stored Library. No step is ever read whole for this.
test_blender_undo_unrecorded_change and test_blender_undo_unrecorded_modes run it; doc/angelo/agent_execution.rst,
"Undo after an unrecorded change".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
READFILE = ROOT / "source/blender/blenloader/intern/readfile.cc"
WRITEFILE = ROOT / "source/blender/blenloader/intern/writefile.cc"
UNDOFILE = ROOT / "source/blender/blenloader/intern/undofile.cc"
MEMFILE_UNDO = ROOT / "source/blender/editors/undo/memfile_undo.cc"
BLENDER_UNDO = ROOT / "source/blender/blenkernel/intern/blender_undo.cc"
MAIN_HEADER = ROOT / "source/blender/blenkernel/BKE_main.hh"
LIB_ID = ROOT / "source/blender/blenkernel/intern/lib_id.cc"
LIB_ID_DELETE = ROOT / "source/blender/blenkernel/intern/lib_id_delete.cc"
LIB_REMAP = ROOT / "source/blender/blenkernel/intern/lib_remap.cc"
ANGELO_UNDO = ROOT / "source/blender/editors/undo/angelo_undo.cc"
REREAD = "angelo_memfile_undo_reread_session_uids"


def code(text):
    """``text`` without comments, so a pin never matches its own explanation."""
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)


def function(path, signature):
    source = path.read_text(encoding="utf-8")
    start = source.index(signature)
    return code(source[start:source.index("\n}\n", start) + 2])


class ReadfileRereadTests(unittest.TestCase):
    def setUp(self):
        self.body = function(READFILE, "static const Set<uint> &read_undo_ids_to_reread(")

    def test_an_id_to_read_again_is_never_reused_as_identical(self):
        restore = function(READFILE, "static bool read_libblock_undo_restore(")
        self.assertRegex(restore, r"if \(id_old != nullptr && read_libblock_is_identical\(fd, bhead\) &&\s*"
                                  r"!read_undo_ids_to_reread\(fd\)\.contains\(id_old->session_uid\)\)")

    def test_ids_changed_in_place_are_read_again(self):
        self.assertIn("old_bmain->%s" % REREAD, self.body)

    def test_the_stored_ids_come_from_the_writer_and_headers_only_without_them(self):
        self.assertIn("fd->undo_memfile->stored_ids", self.body)
        self.assertIn("if (stored_ids == nullptr) {", self.body)
        self.assertIn("scanned = read_undo_stored_session_uids(fd);", self.body)
        self.assertIn("fd->undo_memfile = memfile;", function(READFILE, "FileData *blo_filedata_from_memfile("))

    def test_the_walk_runs_only_when_an_old_id_is_not_stored(self):
        self.assertIn("const bool walk = not_stored > 0 || old_bmain->angelo_memfile_undo_id_left_main;", self.body)
        self.assertLess(self.body.index("not_stored++"), self.body.index("BKE_library_foreach_ID_link("))
        self.assertIn("if (walk) {", self.body)

    def test_the_gate_reads_session_uids_from_the_old_id_map_not_the_ids(self):
        """One hash lookup per old ID over the map the read built; an ID is read only when its UID is missing."""
        self.assertIn("BKE_main_idmap_foreach_uid(fd->old_idmap_uid,", self.body)
        self.assertIn("if (!stored.contains(session_uid) && !read_undo_kept_by_every_read(id) && !is_stored(id)) {",
                      self.body)

    def test_every_old_id_of_every_split_main_is_checked_tagged_or_not(self):
        self.assertIn("for (Main *main : *old_bmain->split_mains) {", self.body)
        self.assertNotIn("recalc_after_undo_push", self.body)
        self.assertIn("if ((target->tag & ID_TAG_NO_MAIN) || !is_stored(*target)) {", self.body)
        self.assertIn("fd->undo_reread_session_uids = std::move(reread);", self.body, "computed once per read")

    def test_a_linked_id_matches_by_name_only_inside_a_stored_library(self):
        """The read matches libraries by session UID only: a replaced Library's IDs are never stored by name."""
        self.assertIn("!stored.contains(id.lib->id.session_uid)", self.body)
        self.assertIn("return step_linked_names().contains(linked_key(id.lib->id.session_uid, id.name));", self.body)
        self.assertIn("if (!linked_names.has_value()) {", self.body, "read from the step only when needed")

    def test_ui_and_no_undo_types_are_kept_by_every_read(self):
        body = function(READFILE, "static bool read_undo_kept_by_every_read(")
        self.assertIn("ELEM(code, ID_WM, ID_SCR, ID_WS)", body)
        self.assertIn("IDTYPE_FLAGS_NO_MEMFILE_UNDO", body)

    def test_linking_needs_no_mark_the_walk_covers_it(self):
        self.assertNotIn("angelo_memfile_undo", function(READFILE, "void BLO_library_link_end("))


class StoredIDsTests(unittest.TestCase):
    def test_the_writer_records_every_id_it_writes_into_the_memfile(self):
        body = function(WRITEFILE, "static void mywrite_id_begin(")
        self.assertIn("BLO_memfile_stored_id_add(wd->mem.written_memfile, *id);", body)

    def test_the_record_holds_session_uids_only_counts_in_the_undo_size_and_is_freed(self):
        add = function(UNDOFILE, "void BLO_memfile_stored_id_add(")
        self.assertIn("memfile->stored_ids->session_uids.add(id.session_uid);", add)
        self.assertNotIn("std::string", add, "no per-push string per linked ID")
        self.assertIn("session_uids.size_in_bytes()", function(UNDOFILE, "size_t BLO_memfile_undo_size("))
        self.assertIn("session_uids.reserve(", function(UNDOFILE, "void BLO_memfile_write_init("))
        self.assertIn("MEM_SAFE_DELETE(memfile->stored_ids);", function(UNDOFILE, "void BLO_memfile_free("))
        self.assertIn("mfu->undo_size = BLO_memfile_undo_size(&mfu->memfile);",
                      function(BLENDER_UNDO, "MemFileUndoData *BKE_memfile_undo_encode("))


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.decode = function(MEMFILE_UNDO, "static UndoDecodeResult memfile_undosys_step_decode(")

    def test_no_whole_read_for_an_unrecorded_change(self):
        self.assertNotIn("angelo_memfile_undo", self.decode)
        self.assertNotIn("BLO_memfile_stores_any_session_uid", self.decode)

    def test_a_failed_decode_that_installed_the_read_main_syncs_too(self):
        failed = self.decode[self.decode.index("if (!read_ok || !bmesh_ok)"):]
        failed = failed[:failed.index("return read_ok ? UndoDecodeResult::FailedStateInstalled")]
        self.assertRegex(failed, r"if \(read_ok\) \{\s*BKE_main_memfile_undo_synced\(bmain\);")

    def test_a_successful_read_syncs_before_the_hooks_after_it(self):
        sync = self.decode.rindex("BKE_main_memfile_undo_synced(bmain);")
        self.assertLess(self.decode.index("BKE_memfile_undo_decode("), sync)
        self.assertLess(sync, self.decode.index("asset_noundo_delta_after_decode("))
        self.assertLess(sync, self.decode.index("main_undo_state_restore("))
        self.assertLess(self.decode.index("if (!read_ok || !bmesh_ok)"), sync, "a failed read returns first")

    def test_the_upstream_flag_keeps_its_meaning(self):
        """Angelo's record is its own: upstream's flag drives the push side ("MemFile Internal (pre)")."""
        self.assertNotIn("is_memfile_undo_written", self.decode)


class RecordTests(unittest.TestCase):
    def test_it_syncs_where_a_memfile_is_written(self):
        for path in (BLENDER_UNDO, ANGELO_UNDO):
            with self.subTest(path=path.name):
                text = code(path.read_text(encoding="utf-8"))
                written = list(re.finditer(r"(\w+)->is_memfile_undo_written = true;", text))
                self.assertTrue(written)
                for match in written:
                    self.assertIn("BKE_main_memfile_undo_synced(%s);" % match.group(1),
                                  text[match.end():match.end() + 120])

    def test_remapping_records_the_owner_and_a_freed_ids_owner(self):
        body = function(LIB_REMAP, "static void foreach_libblock_remap_callback_apply(")
        self.assertRegex(body, r"if \(id_remap_data->type != ID_REMAP_TYPE_CLEANUP\) \{\s*"
                               r"BKE_main_memfile_undo_id_changed_in_place\(id_remap_data->bmain, id_owner\);\s*\}\s*"
                               r"else if \(\(cb_flag & IDWALK_CB_LOOPBACK\) != 0\) \{\s*"
                               r"BKE_main_memfile_undo_id_changed_in_place\(id_remap_data->bmain, old_id\);")

    def test_records_only_while_memfile_undo_runs_and_never_ui_or_no_undo_types(self):
        body = function(LIB_ID, "void BKE_main_memfile_undo_id_changed_in_place(")
        self.assertIn("!bmain->angelo_memfile_undo_recording", body)
        self.assertNotIn("USER_GLOBALUNDO", body, "steps from before global undo was turned off can still be read")
        self.assertNotIn("undosteps", body)
        self.assertIn("ELEM(id_code, ID_WM, ID_SCR, ID_WS)", body)
        self.assertIn("IDTYPE_FLAGS_NO_MEMFILE_UNDO", body)
        self.assertIn("Set<uint> %s;" % REREAD, MAIN_HEADER.read_text(encoding="utf-8"))
        self.assertIn("bool angelo_memfile_undo_recording = false;", MAIN_HEADER.read_text(encoding="utf-8"))

    def test_an_id_leaving_main_alive_forces_the_walk(self):
        self.assertIn("bmain->angelo_memfile_undo_id_left_main = true;",
                      function(LIB_ID, "void BKE_libblock_management_main_remove("))

    def test_removals_record_no_session_uids_of_their_own(self):
        self.assertNotIn("BKE_main_memfile_undo", function(LIB_ID_DELETE, "static size_t id_delete("))
        self.assertNotIn("BKE_main_memfile_undo", function(LIB_ID, "void BKE_lib_id_clear_library_data("))


if __name__ == "__main__":
    unittest.main()
