# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Links through a carry-over to a newer Blender base (``carry_over_links``): each keeps the place it named, and a
result that does not reach the state is never reported as moved. Audit findings on 08e2b0f7842 and the break-it
review of 6f32008ba58.

* A state folder kept elsewhere through a RELATIVE link (``../angelo-data``) moved as that link and dangled while the
  move said it succeeded: its target stays behind, so it is pinned to the absolute place first.
* Pinning measured against one rename at a time made a link between two entries that both move dangle on the
  per-entry path (a retained native profile, state already in the new folder): the whole carry-over decides, so such
  a link stays relative and still reaches its target, and only a link into what stays behind (a retained profile) is
  pinned.
* A relink of the state folder's own link stopped by a crash (Windows: the link removed, its twin not yet renamed)
  once ended ``done`` with empty chats: the next launch finishes the relink first, and an empty new folder where a
  linked one should have arrived is never reported as moved.

Dependency-free; the fixtures are ``test_native_version_carry_over``'s.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_native_version_carry_over as base
from host_test_policy import environment_skip
from test_native_version_carry_over import carry  # noqa: F401
from angelo_agent import carry_over_finish as finish, carry_over_links as links  # noqa: E402
from angelo_agent import carry_over_moves as moves_module  # noqa: E402

Base = base.VersionCarryOverTests


@unittest.skipUnless(base.anchored_files_supported(), "descriptor-relative storage required")
class LinkTests(unittest.TestCase):
    setUp, carry, seed = Base.setUp, Base.carry, Base.seed

    def link(self, path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target, target_is_directory=True)

    def linked_state(self):
        data = self.old_config.parent / "angelo-data"   # <product>/5.2/angelo-data, beside the version's config.
        self.seed(data)
        self.link(self.old, os.path.join(os.pardir, "angelo-data"))
        return data

    def test_a_state_folder_linked_relatively_still_reaches_its_data_after_the_move(self):
        data = self.linked_state()
        moved = self.carry()
        self.assertEqual(moved.status, "migrated", moved.message)
        self.assertTrue(self.new.is_symlink())
        self.assertTrue(os.path.samefile(self.new, data))
        self.assertTrue((self.new / "sessions").is_dir())

    def test_a_link_leading_out_of_the_whole_move_keeps_its_place_and_one_inside_stays_relative(self):
        self.seed()
        shared = self.product / "5.2/shared-assets"
        shared.mkdir(parents=True)
        (shared / "brick.png").write_bytes(base.PNG)
        self.link(self.old / "workspace/shared", os.path.join(os.pardir, os.pardir, os.pardir, "shared-assets"))
        (self.old / "workspace/alias.md").symlink_to("notes.md")
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue(os.path.samefile(self.new / "workspace/shared", shared))
        pinned = os.readlink(self.new / "workspace/shared")
        self.assertTrue(os.path.isabs(pinned) and os.pardir not in Path(pinned).parts, pinned)
        self.assertEqual(os.readlink(self.new / "workspace/alias.md"), "notes.md")

    def test_on_the_per_entry_path_links_between_moving_entries_stay_relative_and_reach_their_targets(self):
        self.seed()
        profile = self.old / "claude-profile"   # A retained native sign-in profile: the per-entry path, it stays.
        profile.mkdir()
        (profile / "settings.json").write_text("{}")
        (self.old / "references-extra").mkdir()
        (self.old / "references-extra" / "a.png").write_bytes(base.PNG)
        self.link(self.old / "workspace/refs", os.path.join(os.pardir, "references-extra"))   # Both move.
        self.link(self.old / "workspace/profile", os.path.join(os.pardir, "claude-profile"))   # Its target stays.
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue(profile.is_dir(), "the retained profile stays where it was")
        self.assertEqual(os.readlink(self.new / "workspace/refs"), os.path.join(os.pardir, "references-extra"))
        self.assertTrue(os.path.samefile(self.new / "workspace/refs", self.new / "references-extra"))
        self.assertTrue(os.path.isabs(os.readlink(self.new / "workspace/profile")))
        self.assertTrue(os.path.samefile(self.new / "workspace/profile", profile))

    def test_a_relink_of_the_state_folders_link_stopped_by_a_crash_finishes_through_the_carry_over(self):
        data = self.linked_state()
        twin = self.old.with_name(links.RELINKING + self.old.name)
        os.unlink(self.old)   # As a Windows relink a crash stopped: the link removed, its absolute twin left.
        twin.symlink_to(data, target_is_directory=True)
        self.config.mkdir(parents=True, exist_ok=True)
        carry.write_record(self.config, {   # The first launch planned the move before it was stopped.
            "from": str(self.old), "from_version": "5.2", "into": str(self.new), "chats": [], "unit": "move",
            "back": [], "began": time.time(), "returned": {}, "touched": False, "moved_at": None, "linked": True,
            "status": carry.MOVING, "attempts": 1, "at": time.time()})
        moved = self.carry()
        self.assertEqual(moved.status, "migrated", moved.message)
        self.assertTrue(os.path.samefile(self.new, data))
        self.assertTrue((self.new / "sessions").is_dir(), "the chats arrived")
        self.assertFalse(os.path.lexists(twin))

    def test_a_twin_beside_something_else_at_its_name_is_kept_and_the_move_never_ends_done(self):
        data = self.linked_state()
        twin = links.twin_of(self.old)
        os.unlink(self.old)
        twin.symlink_to(data, target_is_directory=True)   # The only way to the state now.
        self.old.mkdir()   # The earlier version opened since and made a folder of its own there.
        (self.old / "intro.json").write_text("{}")
        result = self.carry()
        self.assertNotEqual(result.status, "migrated", result.message)
        self.assertTrue(links.is_link(twin), "the twin is never removed")
        self.assertTrue((twin / "sessions").is_dir())
        record = carry.read_record(self.config)
        self.assertTrue(record is None or record["status"] != carry.DONE)

    def test_on_the_per_entry_path_of_a_linked_state_a_link_into_its_profile_is_pinned(self):
        data = self.linked_state()
        (data / "claude-profile").mkdir()
        (data / "claude-profile" / "settings.json").write_text("{}")
        self.link(data / "workspace/p", os.path.join(os.pardir, "claude-profile"))
        self.new.mkdir(parents=True)   # This version already has state: the per-entry path through the link.
        (self.new / "intro.json").write_text("{}")
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue((data / "claude-profile").is_dir(), "the profile stays in the linked folder")
        self.assertTrue(os.path.isabs(os.readlink(self.new / "workspace/p")))
        self.assertTrue(os.path.samefile(self.new / "workspace/p", data / "claude-profile"))

    def test_without_the_privilege_for_links_a_folder_gets_a_junction_or_the_link_stays(self):
        target = Path(self.temp.name) / "target"
        target.mkdir()
        refused = OSError(1314, "A required privilege is not held by the client")
        refused.winerror = links.NO_SYMLINK_PRIVILEGE
        made = []
        with patch.object(links.os, "symlink", side_effect=refused), \
                patch.object(links, "_junction", lambda where, path: made.append((where, path))):
            self.assertTrue(links._make_twin(str(target), Path(self.temp.name) / "twin", True))
        self.assertEqual(made, [(str(target), Path(self.temp.name) / "twin")])
        with patch.object(links.os, "symlink", side_effect=refused), \
                patch.object(links, "_junction", side_effect=OSError("a share a junction cannot name")):
            self.assertFalse(links._make_twin(str(target), Path(self.temp.name) / "twin", True), "kept as it is")

    def test_a_windows_swap_still_refused_puts_the_original_link_back(self):
        target = Path(self.temp.name) / "target"
        target.mkdir()
        link, twin = Path(self.temp.name) / "state", Path(self.temp.name) / (links.RELINKING + "state")
        link.symlink_to("target", target_is_directory=True)
        twin.symlink_to(target, target_is_directory=True)
        with patch.object(links.os, "rename", side_effect=PermissionError(13, "held")), self.assertRaises(OSError):
            links._swap(twin, link, "target", True, sleep=lambda delay: None)
        self.assertEqual(os.readlink(link), "target", "the original link is back")
        self.assertFalse(os.path.lexists(twin))

    def test_an_empty_folder_where_a_linked_state_should_have_arrived_is_never_done(self):
        with self.assertRaises(OSError):
            finish.check_reachable(self.new, linked=False)   # Nothing at all after the move.
        self.new.mkdir(parents=True)   # As a session left it when the link never arrived.
        with self.assertRaises(OSError):
            finish.check_reachable(self.new, linked=True)
        finish.check_reachable(self.new, linked=False)   # An empty folder of this version's own is no failure.
        (self.new / "sessions").mkdir()
        finish.check_reachable(self.new, linked=True)

    def test_a_result_that_dangles_is_never_reported_as_moved(self):
        self.linked_state()
        with patch.object(links, "pin_links", lambda entry, target, plan: 0):   # As 08e2b0f7842 moved it.
            stopped = self.carry()
        self.assertEqual(stopped.status, "failed", stopped.message)
        self.assertIn("does not lead to a folder", stopped.message)
        self.assertNotEqual(carry.read_record(self.config)["status"], carry.DONE)

    def test_a_twin_left_by_a_crash_takes_the_name_and_anything_else_of_that_name_is_left_alone(self):
        target = Path(self.temp.name) / "target"
        target.mkdir()
        twin = Path(self.temp.name) / (links.RELINKING + "state")
        twin.symlink_to(target, target_is_directory=True)
        links.finish_relink(Path(self.temp.name) / "state")
        self.assertTrue(os.path.samefile(Path(self.temp.name) / "state", target))
        self.assertFalse(os.path.lexists(twin))
        persons = Path(self.temp.name) / (links.RELINKING + "mine")
        persons.mkdir()   # Not a link: the person's own folder of that name.
        links.finish_relink(Path(self.temp.name) / "mine")
        self.assertTrue(persons.is_dir())

    def test_a_junction_moved_across_volumes_names_the_same_folder_or_is_refused(self):
        # Windows only for real (CreateJunction, and readlink's \\?\ prefix): the prefix rule and the verification
        # run here, a symbolic link standing in for each junction.
        self.assertEqual(links.plain_target("\\\\?\\C:\\data"), "C:\\data")
        self.assertEqual(links.plain_target("\\\\?\\UNC\\server\\share"), "\\\\server\\share")
        self.assertEqual(links.plain_target("C:\\data"), "C:\\data")
        data = Path(self.temp.name) / "external"
        data.mkdir()
        source, temporary = Path(self.temp.name) / "j", Path(self.temp.name) / "j-new"
        source.symlink_to(data, target_is_directory=True)
        with patch.object(links, "_junction", lambda target, path: Path(path).symlink_to(target)):
            links.copy_junction(source, temporary)
        self.assertTrue(os.path.samefile(temporary, data))
        os.unlink(temporary)
        elsewhere = Path(self.temp.name) / "elsewhere"
        elsewhere.mkdir()
        with patch.object(links, "_junction", lambda target, path: Path(path).symlink_to(elsewhere)), \
                self.assertRaises(OSError):
            links.copy_junction(source, temporary)
        self.assertFalse(os.path.lexists(temporary), "a junction naming another folder never takes the name")

    def prefixed(self):
        """``os.readlink`` as on Windows: an absolute target comes back with the ``\\\\?\\`` prefix (on Windows it
        already does: never prefixed twice)."""
        real = os.readlink

        def readlink(path):
            text = real(path)
            return links.LONG + text if os.path.isabs(text) and not text.startswith(links.LONG) else text
        return patch.object(links.os, "readlink", readlink)

    def standing_in(self):
        """CreateJunction: a symbolic link to the plain target stands in (Windows only for real)."""
        return patch.object(links, "_junction", lambda target, path: Path(path).symlink_to(target))

    def test_a_junction_copy_through_prefixed_readlink_text_reaches_the_same_folder(self):
        data = Path(self.temp.name) / "external"
        data.mkdir()
        source, temporary = Path(self.temp.name) / "j", Path(self.temp.name) / "j-new"
        source.symlink_to(data, target_is_directory=True)
        with self.prefixed(), self.standing_in():
            links.copy_junction(source, temporary)
        self.assertTrue(os.path.samefile(temporary, data))

    def test_a_junction_to_a_detached_disk_is_copied_by_its_target_text(self):
        gone = Path(self.temp.name) / "detached-disk" / "data"   # Never made: the disk is not attached.
        source, temporary = Path(self.temp.name) / "j", Path(self.temp.name) / "j-new"
        source.symlink_to(gone, target_is_directory=True)
        with self.prefixed(), self.standing_in():
            links.copy_junction(source, temporary)
        self.assertEqual(links.plain_target(os.readlink(temporary)), str(gone), "Windows reads it back prefixed")
        os.unlink(temporary)
        with self.prefixed(), patch.object(links, "_junction", lambda t, path: Path(path).symlink_to(t + "-other")), \
                self.assertRaises(OSError):
            links.copy_junction(source, temporary)
        self.assertFalse(os.path.lexists(temporary))

    def test_a_dangling_link_and_its_prefixed_leftover_twin_never_block_a_launch(self):
        link = Path(self.temp.name) / "state"
        link.symlink_to(os.path.join(os.pardir, "detached-disk", "angelo-data"))
        twin = links.twin_of(link)
        twin.symlink_to(links._resolved(os.path.join(os.path.dirname(str(link)), os.readlink(link))))
        with self.prefixed():
            links.finish_relink(link)
        self.assertFalse(os.path.lexists(twin))

    def test_a_junction_nested_in_a_folder_copied_across_volumes_stays_a_junction(self):
        data = Path(self.temp.name) / "external"
        data.mkdir()
        (data / "big.bin").write_bytes(b"x" * 64)
        source = Path(self.temp.name) / "old" / "workspace"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "notes.md").write_text("mine")
        (source / "sub" / "assets").symlink_to(data, target_is_directory=True)   # A junction on Windows.
        target = Path(self.temp.name) / "new" / "workspace"
        target.parent.mkdir()
        mover = moves_module.Mover(Path(self.temp.name) / "kept")
        junction = lambda path: Path(path).name == "assets" and Path(path).is_symlink()  # noqa: E731
        with patch.object(links, "is_junction", junction), self.prefixed(), self.standing_in(), self.spied() as made:
            mover.copy_across(source, target)
        # The mechanism, which POSIX's copytree(symlinks=True) would hide: the junction is made again by copy_junction.
        self.assertEqual(made, [(source / "sub" / "assets", target.with_name(moves_module.CARRYING + "workspace")
                                 / "sub" / "assets")])
        self.assertTrue((target / "sub" / "assets").is_symlink(), "made again as a junction, not a copy")
        self.assertTrue(os.path.samefile(target / "sub" / "assets", data))
        self.assertEqual((target / "sub" / "notes.md").read_text(), "mine")
        self.assertFalse(source.exists())
        self.assertEqual((data / "big.bin").read_bytes(), b"x" * 64, "the folder it names is untouched")

    def spied(self):
        """``copy_junction`` as it is, recording each ``(source, made at)``."""
        import contextlib
        real, made = links.copy_junction, []

        @contextlib.contextmanager
        def spy():
            def recording(source, temporary):
                made.append((Path(source), Path(temporary)))
                return real(source, temporary)
            with patch.object(links, "copy_junction", recording):
                yield made
        return spy()

    @unittest.skipUnless(os.name == "nt", environment_skip(
        "a real junction (_winapi.CreateJunction, no privilege needed): Windows only"))
    def test_a_real_junction_nested_in_a_folder_copied_across_volumes_is_made_again_never_copied(self):
        import _winapi
        data = Path(self.temp.name) / "external"
        data.mkdir()
        (data / "big.bin").write_bytes(b"x" * 64)
        source = Path(self.temp.name) / "old" / "workspace"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "notes.md").write_text("mine")
        _winapi.CreateJunction(str(data), str(source / "sub" / "assets"))
        self.assertTrue(os.path.isjunction(source / "sub" / "assets"))
        target = Path(self.temp.name) / "new" / "workspace"
        target.parent.mkdir()
        with self.spied() as made:
            moves_module.Mover(Path(self.temp.name) / "kept").copy_across(source, target)
        nested = target / "sub" / "assets"
        self.assertEqual([found for found, _ in made], [source / "sub" / "assets"])
        self.assertTrue(os.path.isjunction(nested), "made again as a junction")
        self.assertTrue(os.path.samefile(nested, data))
        self.assertEqual((target / "sub" / "notes.md").read_text(), "mine")
        self.assertFalse(source.exists())
        self.assertEqual(sorted(p.name for p in data.iterdir()), ["big.bin"], "the folder it names is untouched")
        self.assertEqual((data / "big.bin").read_bytes(), b"x" * 64)

    def unc_readlink(self, names):
        """``os.readlink`` as on Windows for a network share: ``names`` maps a link's name to its text there (with the
        ``LONG_UNC`` prefix); any other link reads as it is."""
        real = os.readlink
        return patch.object(links.os, "readlink", lambda path: names.get(Path(path).name) or real(path))

    def test_a_dangling_link_and_its_unc_leftover_twin_never_block_a_launch(self):
        # A state folder on a redirected or roaming network share: the link's own text resolves to \\server\share\...,
        # the twin's reads \\?\UNC\server\share\... (Windows). The real finish_relink and _leftover.
        link = Path(self.temp.name) / "state"
        link.symlink_to(os.path.join(os.pardir, "share-not-reachable", "angelo-data"))
        twin = links.twin_of(link)
        twin.symlink_to(Path(self.temp.name) / "anything")
        share = "\\\\server\\share\\angelo-data"
        with self.unc_readlink({twin.name: links.LONG_UNC + "server\\share\\angelo-data"}), \
                patch.object(links, "_resolved", lambda place: share):
            links.finish_relink(link)
        self.assertFalse(os.path.lexists(twin))
        self.assertTrue(link.is_symlink())
        twin.symlink_to(Path(self.temp.name) / "anything")
        with self.unc_readlink({twin.name: links.LONG_UNC + "server\\other\\angelo-data"}), \
                patch.object(links, "_resolved", lambda place: share), self.assertRaises(OSError):
            links.finish_relink(link)   # A twin naming another share is not this link's: kept, and named.
        self.assertTrue(os.path.lexists(twin))

    def test_a_junction_to_a_detached_unc_folder_is_copied_by_its_target_text(self):
        source, temporary = Path(self.temp.name) / "j", Path(self.temp.name) / "j-new"
        source.symlink_to(Path(self.temp.name) / "not-attached")
        unc = {"j": links.LONG_UNC + "server\\share\\data"}
        with self.unc_readlink(unc), self.standing_in():
            links.copy_junction(source, temporary)
        self.assertEqual(links.plain_target(os.readlink(temporary)), "\\\\server\\share\\data")
        os.unlink(temporary)
        with self.unc_readlink(unc), patch.object(links, "_junction", lambda t, path: Path(path).symlink_to(t + "x")), \
                self.assertRaises(OSError):
            links.copy_junction(source, temporary)
        self.assertFalse(os.path.lexists(temporary))

    def test_a_disk_mounted_at_a_folder_is_refused_before_anything_is_copied_naming_it(self):
        source = Path(self.temp.name) / "old" / "workspace"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "notes.md").write_text("mine")
        (source / "sub" / "disk").symlink_to(Path(self.temp.name), target_is_directory=True)   # A mount point.
        target = Path(self.temp.name) / "new" / "workspace"
        target.parent.mkdir()
        volume = "\\\\?\\Volume{0b1c2d3e-0000-0000-0000-000000000000}\\"
        with patch.object(links, "is_junction", lambda path: Path(path).name == "disk"), \
                self.unc_readlink({"disk": volume}), self.assertRaises(OSError) as raised:
            moves_module.Mover(Path(self.temp.name) / "kept").copy_across(source, target)
        self.assertIn(str(source / "sub" / "disk"), str(raised.exception))
        self.assertIn("Volume{", str(raised.exception))
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), [], "nothing was copied")
        self.assertEqual((source / "sub" / "notes.md").read_text(), "mine")
        with patch.object(links, "is_junction", lambda path: True), self.unc_readlink({"disk": volume}), \
                self.assertRaises(OSError):
            links.copy_junction(source / "sub" / "disk", Path(self.temp.name) / "disk-new")   # At the top too.
        self.assertFalse(os.path.lexists(Path(self.temp.name) / "disk-new"))

    def test_the_ai_providers_card_has_the_records_notices_own_title(self):
        from angelo_agent import data_migration, state_migration
        from angelo_agent.folder_moves import MigrationResult
        quiet = [patch.object(module, "attention_message", lambda: None) for module in (data_migration, state_migration)]
        for each in quiet:
            each.start()
            self.addCleanup(each.stop)
        with patch.object(carry, "last_result", MigrationResult(finish.RECORDS, "records left")):
            self.assertEqual(carry.attention_notices(), [(finish.RECORDS_TITLE, "records left")])
        with patch.object(carry, "last_result", MigrationResult(carry.DEFERRED, "still moving")):
            self.assertEqual(carry.attention_notices(), [(carry.EARLIER_TITLE, "still moving")])

    def test_a_linked_native_profile_stays_where_it_is(self):
        self.seed()
        real = self.product / "profiles/claude"
        real.mkdir(parents=True)
        (self.old / "claude-profile").symlink_to(real, target_is_directory=True)   # A junction on Windows.
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue((self.old / "claude-profile").is_symlink(), "its CLI's sign-in is keyed to this path")
        self.assertFalse(os.path.lexists(self.new / "claude-profile"))

    def test_a_resumed_move_stopped_by_a_kept_twin_works_on_the_twin_and_names_it(self):
        data = self.linked_state()
        twin = links.twin_of(self.old)
        os.unlink(self.old)
        twin.symlink_to(data, target_is_directory=True)
        self.old.mkdir()
        self.new.mkdir(parents=True)   # Something (Cycles' GPU listing) wrote here meanwhile.
        (self.new / "render-device.json").write_text("{}")
        self.config.mkdir(parents=True, exist_ok=True)
        carry.write_record(self.config, {
            "from": str(self.old), "from_version": "5.2", "into": str(self.new), "chats": [], "unit": "move",
            "back": [], "began": time.time(), "returned": {}, "touched": False, "moved_at": None, "linked": True,
            "status": carry.MOVING, "attempts": carry.RETRIES_BEFORE_HELP, "at": time.time()})
        result = self.carry()
        self.assertEqual(result.status, "failed")
        self.assertEqual(carry.deferred_source, twin, "this session works on the state through the twin")
        self.assertIn(str(twin), result.message)
        self.assertTrue(links.is_link(twin))

    def test_a_dangling_link_and_its_leftover_twin_never_block_a_launch(self):
        link = Path(self.temp.name) / "state"
        link.symlink_to(os.path.join(os.pardir, "detached-disk", "angelo-data"))   # A disk not attached.
        twin = links.twin_of(link)
        twin.symlink_to(links._resolved(os.path.join(os.path.dirname(str(link)), os.readlink(link))))
        links.finish_relink(link)
        self.assertFalse(os.path.lexists(twin))
        self.assertTrue(link.is_symlink())

    def test_paths_compare_as_the_file_system_does(self):
        plan = links.Plan(Path("/a/old"), Path("/a/new"), frozenset({"claude-profile"}))
        self.assertEqual(plan.destination("/a/old/workspace/x"), os.path.normpath("/a/new/workspace/x"))
        self.assertEqual(plan.destination("/a/old/claude-profile/x"), "/a/old/claude-profile/x")
        self.assertEqual(plan.destination("/elsewhere/x"), "/elsewhere/x")


if __name__ == "__main__":
    unittest.main()
