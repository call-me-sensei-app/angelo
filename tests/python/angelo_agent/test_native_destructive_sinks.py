# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Angelo's exit and cleanup paths destroy comes from C's record of what Angelo made, never from Python state
alone (guard piece 9, doc/angelo/script_confinement.rst, "Angelo's own I/O").

**Scope: every destructive call reachable from an exit or cleanup entry point of the add-on** (``atexit``,
``threading._register_atexit``, ``weakref.finalize``, and methods named ``cleanup``, ``close``, ``stop``, ``drain``,
``shutdown``, ``release``, ``__del__``, ``__exit__``), following the calls the source names (``destructive_sinks``
says exactly which): not every destructive call in the package. Each one either goes through the record
(``owned_paths``, ``owned_replace``, or an ``owned_paths.owned`` check in its function) or is listed in ``ALLOWED``
with why its target can't come from what a script changed. Runs outside Blender (ast only).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest

import destructive_sinks

# (module, function) -> why its destructive call needs no record.
ALLOWED = {
    ("checkpoint_filesystem", "_unlink_file"): "one entry of an anchored store folder (held open by descriptor, its "
                                               "identity checked): entry_name refuses all but a single name",
    ("checkpoint_filesystem", "remove_entry"): "one entry of an anchored store folder: entry_name refuses all but a "
                                               "single name, so nothing outside the folder is reached",
    ("goal_store", "GoalStore._write"): "unlinks only this call's own temporary, created O_CREAT|O_EXCL under a fresh "
                                        "uuid name; the save itself goes through owned_replace",
    ("usage_meter", "UsageLedger._write"): "unlinks only this call's own temporary, created O_CREAT|O_EXCL under a "
                                           "fresh uuid name; the save itself goes through owned_replace",
    ("headless_working_copy", "_unlink"): "the copy claim() made a moment ago when placing it failed, or the .blend1 "
                                          "backup beside a copy discard() found on C's record",
    ("session_kept", "_copy_new"): "its target is a fresh name it created O_CREAT|O_EXCL itself (never an existing "
                                   "file), rewritten or removed only on failure",
    ("private_roots", "_write_list"): "replaces only a file of Angelo's own fixed list name (RETAINED_PROFILES or "
                                      "its last-good copy) from a mkstemp temporary, in whatever folder the store "
                                      "names: never a person's file; the profile folders it lists are removed only "
                                      "on C's record (fab_browser_profile: owned_paths.owned)",
}


class DestructiveSinksTests(unittest.TestCase):
    def setUp(self):
        self.modules = destructive_sinks.load()

    def test_every_reachable_destructive_call_goes_through_the_record_or_says_why(self):
        found = destructive_sinks.sinks(self.modules)
        unexplained = sorted({"%s.%s:%d %s (from %s)" % (module, qualname, line, call, "/".join(".".join(e)
                                                                                               for e in entries[:2]))
                              for module, qualname, line, call, guarded, entries in found
                              if not guarded and (module, qualname) not in ALLOWED})
        self.assertEqual(unexplained, [], "route these through owned_paths/owned_replace or list them with a reason")

    def test_every_allowed_entry_is_still_a_sink(self):
        reached = {(module, qualname) for module, qualname, *_ in destructive_sinks.sinks(self.modules)}
        self.assertEqual(sorted(set(ALLOWED) - reached), [], "listed but no longer reached: remove them")

    def test_the_reader_finds_the_entry_points_it_names(self):
        entries = destructive_sinks.entries(self.modules)
        # One of each kind it claims to read, so a change to how the add-on registers them can't empty the scope.
        self.assertIn(("job_render_child", "_end_all"), entries)              # atexit.register(name)
        self.assertIn(("library_fetch", "_unhold"), entries)                  # weakref.finalize(obj, name, ...)
        self.assertTrue(any(qualname.endswith(".close") for _m, qualname in entries))
        self.assertGreater(len(destructive_sinks.reachable(self.modules)), len(entries))

    def test_a_planted_sink_is_found(self):
        # The reader itself: a cleanup method that removes a folder named by its own state is reported.
        import ast
        module = destructive_sinks.Module.__new__(destructive_sinks.Module)
        module.name, module.imports, module.functions = "planted", {}, {}
        module.tree = ast.parse("import shutil\nclass Thing:\n    def close(self):\n"
                                "        shutil.rmtree(self.folder)\n")
        for node in module.tree.body:
            module._collect(node, "")
        found = destructive_sinks.sinks({"planted": module})
        self.assertEqual([(m, q, call, guarded) for m, q, _l, call, guarded, _e in found],
                         [("planted", "Thing.close", "shutil.rmtree", False)])


if __name__ == "__main__":
    unittest.main()
