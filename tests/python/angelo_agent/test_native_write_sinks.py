# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every write, replace or removal Angelo's own code makes to a path from Python state is checked against C's facts
(guard piece 10, reviewer's sixth read MUST-A; doc/angelo/script_confinement.rst, "Angelo's own I/O").

**Scope: every function of the add-on** (``write_sinks`` says exactly what a sink is and how a target is classified):
an operator the person runs, a timer, a job's completion, an engine link's handler, a thread, an exit path. A target
from Python state (``state``) is checked in its function (``owned_paths.may_write``/``require_writable``/``owned``
naming it, which ask C: Angelo's private folders, what Angelo made, or a path vouched for at the person's own action),
or listed in ``ALLOWED`` with why it can't name a file of the person's. Runs outside Blender (ast only).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest

import destructive_sinks
import write_sinks

# Calls whose result is a folder of Angelo's or Blender's own, never a value a script can change in place.
FIXED_SOURCES = {
    "storage.data_directory": "Angelo's per-user data folder, from the platform's fixed location",
    "ui.state_directory": "Angelo's state folder, fixed at startup before any script",
    "ui.connection_directory": "Angelo's account store folder, fixed at startup before any script",
    "private_roots.scratch_root": "<data folder>/scratch, Angelo's own process and scratch folders",
    "bpy.utils.user_resource": "Blender's own per-user folders (C reads them from the build's fixed layout)",
    "bpy.utils.resource_path": "Blender's own install folders",
    "tempfile.gettempdir": "the process's temporary folder, fixed when Python starts",
    "Path.home": "the person's home folder, from the operating system",
    "os.path.expanduser": "the person's home folder, from the operating system",
    "shutil.which": "a program found on PATH (the environment, which no script changes) by the name given",
    "os.devnull": "the platform's null device, a constant of the os module",
    "bpy.app.binary_path": "the running Angelo's own program (BKE_appdir_program_path: set once at startup, read-only)",
    "os.environ": "C's view of the process environment (bpy_angelo_guard_confine_environ.cc): a script can't change it",
}

# (module, qualname) -> why a sink in it is confined by construction: neither reported nor followed to callers.
_ANCHORED = ("one entry of a folder the checkpoint store anchored (a descriptor or handle opened without following "
             "a link, its root checked by C when anchored: checkpoint_filesystem._private_directory's "
             "require_writable); entry_name refuses all but a single name, so nothing outside it is reached")
CONTAINED = {**{("checkpoint_filesystem", name): _ANCHORED
                for name in ("_open_file", "_replace_file", "_unlink_file", "_write_json", "remove_entry",
                             "publish_new_file", "make_directory", "chmod_file")},
             ("files", "_open_child"): _ANCHORED.replace("the checkpoint store anchored", "the file tools opened"),
             ("exclusive_rename", "_made_in_place"): "a new folder of one entry name (create_directory's entry_name) "
                                                     "in a folder the caller holds open as a descriptor, anchored by "
                                                     "checkpoint_filesystem._private_directory (whose root C checks)",
             ("user_copies", "remove_tree.retry"): "shutil.rmtree's retry inside the tree remove_tree was given (that "
                                                   "path is checked at remove_tree's callers: it is a helper whose "
                                                   "parameter reaches rmtree): makes a failed entry owner-writable "
                                                   "and tries it again; links are never followed",
             ("engine_setup", "set_up"): "links only a render add-on found at the click (found(), detected again "
                                         "from Blender's and Angelo's add-on folders, never a row from state), into "
                                         "Blender's add-ons folder; on a failed setup it removes only the link it "
                                         "made a moment earlier in the same call",
             ("moved_state_notice", "mark_told"): "an empty marker of a fixed name (NOTE_SEEN) in Blender's config "
                                                  "folder: no content to plant",
             ("studio_agent", "_publish_export"): "a hard link into the workspace folder the export holds open "
                                                  "(anchored by checkpoint_filesystem._private_directory, whose "
                                                  "root C checks), never over an existing name",
             **{("job_output_links", name): "a rename between two folders the job's staging holds open "
                                            "(descriptors or handles opened without following a link down from the "
                                            "workspace root, which C checked when anchoring it: "
                                            "checkpoint_filesystem._private_directory), never over an existing name"
                for name in ("_move", "publish_entry")},
             ("shared_replace", "_windows_replace"): "the rename shared_replace.replace hands to its retries, after it "
                                                     "asked C about the target (require_writable)",
             ("shared_replace", "remove.unlink"): "the removal shared_replace.remove hands to its retries, after it "
                                                  "asked C about the path (require_writable)"}

# (module, qualname) -> why its state target can't name a file of the person's.
_CARRY_OVER = ("the carry-over from an earlier Blender base: runs once, in the add-on's registration "
               "(version_carry_over.carry_over_on_startup, run only there), before the confinement is installed and "
               "before any script region (C's may_write answers yes then), on Blender's own folders; what its Mover "
               "holds never outlives that call, and the record it resumes from (<CONFIG>/angelo-carry-over.json) "
               "lies in Blender's config folder, a startup root no script can change, written only by this "
               "carry-over")
_OWN_DOCUMENT = ("saves the open document to its own path (Blender's: only a save changes it, and a script's save is "
                 "judged by the confinement), at the person's own request")
ALLOWED = {
    ("studio_assets_convert", "main"): "the converter child's entry (a Blender started with --python, where no "
                                       "script region begins, so C answers yes there): each .blend it writes is "
                                       "asked of C in the parent before the batch exists "
                                       "(studio_assets_host.convert_batch, p10c-4-export)",
    ("update_flow", "remove_previous"): "removes only <work>/previous.app or .<app>.previous-<n> beside the installed "
                                        "app (named by the installer, checked by name and place, never the app itself "
                                        "nor a link)",
    ("update_restart", "_save_file"): _OWN_DOCUMENT + " (Restart to update)",
    ("update_restart", "_after_prompt"): "the person's own save before Restart to update, through Blender's Save or "
                                         "Save As dialog (INVOKE_DEFAULT: the person confirms or picks the file)",
    ("engine_link_agent", "save"): _OWN_DOCUMENT + " (the engine link's save before a hand-off)",
    ("download_folders", "root"): "the open document's own downloads folder (<document folder>/assets/downloads), "
                                  "or one under Blender's temporary folder for an unsaved document: named by "
                                  "Blender's document path (only a load or save changes it), never by Python state "
                                  "(the folder names are literals there); v200 owns these lines this round",
    ("fab_convert_child", "main"): "the Fab converter child's entry (a sandboxed Blender started with --python, no "
                                   "add-on and no script region, so C answers yes there): the result path is its "
                                   "parent's argument, in the work folder fab_convert made for it",
    ("providers", "CodexProvider._scoped_event"): "a turn's liveness clock (.touch() of the activity object), no file",
    ("providers", "ClaudeProvider._read"): "a turn's liveness clock (turn.activity.touch, chosen in "
                                           "_dispatch_turn_event), no file",
    ("engine_link_ui", "_open"): _OWN_DOCUMENT + " (the card's Save and open)",
    ("fab_browser_download_file", "CompletedFile.retire"): "unlinks one entry of the landing folder it opens itself and "
                                                           "checks by identity (owner token, device and inode), the "
                                                           "entry's own identity checked too",
    ("headless_main", "main"): "the headless child's own private folder: made at its start from the parent's argument "
                               "(before any script region) and held only in main's locals, removed as it ends",
    ("worker_main", "main"): "the worker child's own private folder: made at its start from the parent's argument "
                             "(before any script region) and held only in main's locals, removed as it ends",
    ("settings_import", "undo"): "Undo of a settings import, at the person's click: only paths its manifest lists "
                                 "(written by the import into its backup folder, which scripts can read but never "
                                 "change) and only inside that manifest's destination roots, in one call",
    ("studio_assets_convert", "main.flush"): "the asset converter child, a plain script in a Blender with no "
                                             "confinement: the results path is its parent's argument",
    ("pycache_retention", "_run"): "bytecode-cache pruning's thread body: the folders startup computed from Blender's "
                                   "cache folder and Python's bytecode prefix (prune_stale_mirrors' reason)",
    ("data_migration", "migrate_legacy_data"): "the move of an earlier product's data folder: run once by "
                                               "migrate_on_startup in the add-on's registration, before any script "
                                               "region (C's may_write answers yes then), between fixed folders",
    ("state_migration", "migrate_state_folder"): "the move of an earlier state folder: run once by "
                                                 "migrate_on_startup in the add-on's registration, before any "
                                                 "script region, between Blender's own config folders",
    ("pycache_retention", "prune_stale_mirrors"): "bytecode-cache pruning: Blender's cache folder and Python's "
                                                  "bytecode prefix (fixed when the process started), computed at "
                                                  "startup and passed to its one thread; each tree it removes is "
                                                  "checked as a bytecode mirror first (is_bytecode_tree)",
    **{key: _CARRY_OVER for key in (
        ("carry_over_leave", "leave_behind"), ("carry_over_links", "finish_relink"), ("carry_over_links", "pin_links"),
        ("carry_over_moves", "Mover._split_unit"), ("carry_over_moves", "Mover.copy_across"),
        ("carry_over_moves", "Mover.keep"), ("carry_over_moves", "Mover.merge"),
        ("carry_over_moves", "Mover.merge_top"), ("carry_over_records", "write_json"),
        ("version_carry_over", "_carry_locked"), ("version_carry_over", "_drop_own_cache"),
        ("version_carry_over", "_move"), ("version_carry_over", "find_source"),
        ("version_carry_over", "write_record"))},
}

# (module, qualname, what) -> why dynamic dispatch there (write_sinks_dispatch: the reader can't follow it) reaches no
# write of a path from Python state.
_OPERATOR_ROW = ("an operator named by a row of Angelo's own menus or cards (its registry, never a script's text); a "
                 "Blender writer operator Angelo's package calls is checked by C at the operator call whatever its "
                 "name (v200's operator-call check, this round), and again at each file it writes when the path is "
                 "read from data while the job runs (render frames, movies, File Output nodes, caches and bakes: "
                 "BLI_angelo_writers.hh), and Angelo's own operators read their paths as state in their own bodies, "
                 "which this test reads")
DYNAMIC = {
    **{key: _OPERATOR_ROW for key in (
        ("chat_add_nav", "dispatch", "getattr(<computed>)()"), ("engine_status", "_call", "getattr(<computed>)()"),
        ("flow_menu", "_run", "getattr(<computed>)()"), ("flow_window", "act", "getattr(<computed>)()"),
        ("layout_after", "again.action", "getattr(<computed>)()"),
        ("learn_ui", "LearnCard.act", "getattr(<computed>)()"),
        ("providers_page_external", "act", "getattr(<computed>)()"),
        ("providers_page_host", "_act", "getattr(<computed>)()"), ("studio", "click", "getattr(<computed>)()"),
        ("studio_input", "ANGELO_OT_studio_input.modal", "getattr(<computed>)()"),
        ("ui", "ANGELO_OT_approve.execute", "getattr(<computed>)()"))},
    ("document_file_identity", "_native", "getattr(<computed>)()"):
        "a function of C's _angelo_saved module (saved_file and its siblings), which takes no path",
    ("fab_host", "human", "getattr(<computed>)()"): "a method of the Fab host by the name library_host gives "
                                                    "(its literal actions); the host's writes are read in its methods",
    ("program_commit_scope", "CommitScope._delete", "getattr(<computed>)()"): "the C bridge's delete_* by a literal "
                                                                              "name: it removes Blender data, no file",
    ("team_token_store", "TokenVault._call.run", "getattr(<computed>)()"): "the token backend's read/write/delete/"
                                                                           "erase: the operating system's keychain, "
                                                                           "no file path",
    ("timer_resolution", "_call", "getattr(<computed>)()"): "winmm's timeBeginPeriod/timeEndPeriod: no file",
    ("worker_consent", "_arriving", "getattr(<computed>)()"): "a controller's check named by its callers' literals "
                                                              "(stop_arrived, consent_arriving): no file",
    ("worker_consent_lead", "_show", "getattr(<computed>)()"): "a controller's card update named by its callers' "
                                                               "literals (approval_sent, declined): no file",
    ("job_host", "_execute", "getattr(save)"): "the job store's own save (JobStore.save, read in its own body: its "
                                               "file in the job store's folder)",
    ("job_ui", "_keep_pending", ".save"): "the job store's own save handed to job_recovery.recover (JobStore.save, "
                                          "read in its own body)",
    ("job_ui", "_reconcile", ".save"): "the job store's own save handed to job_recovery.recover (JobStore.save, read "
                                       "in its own body)",
    ("pycache_retention", "schedule.start", "pycache_retention._run"): "bytecode-cache pruning's thread, given the "
                                                                       "folders startup computed (ALLOWED's "
                                                                       "prune_stale_mirrors reason)",
    ("script_confinement_hook", "build", "os.open"): "the confinement keeps the real os.open to call from its own "
                                                     "guard, which resolves a dir_fd's folder and refuses what C "
                                                     "refuses",
}


def unbound_names(tree):
    """``(line, name)`` of every name ``tree`` loads that nothing in the module binds (an import, an assignment, a
    definition, a parameter, an exception or match name, a global) and that is no builtin: a guard written without
    its import passes the sink reader by name, then raises ``NameError`` at the write it was meant to check."""
    import builtins
    bound = set(dir(builtins)) | {"__file__", "__name__", "__path__", "__spec__", "__package__", "__loader__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return sorted({(node.lineno, node.id) for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound})


def _analysis():
    return write_sinks.Analysis().run(set(FIXED_SOURCES), set(CONTAINED))


class WriteSinksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sinks = _analysis()

    def test_every_state_target_is_checked_by_c_or_says_why(self):
        unexplained = sorted({"%s.%s:%d %s" % (module, qualname, line, kind)
                              for module, qualname, line, kind, cls, guarded in self.sinks
                              if cls == "state" and not guarded and (module, qualname) not in ALLOWED
                              and not kind.startswith("dynamic:")})
        self.assertEqual(unexplained, [], "%d writes from Python state: check them with owned_paths.require_writable "
                                          "(vouch where the path enters) or list them with a reason" % len(unexplained))

    def test_every_allowed_entry_is_still_a_sink(self):
        reached = {(module, qualname) for module, qualname, *_ in self.sinks}
        self.assertEqual(sorted(set(ALLOWED) - reached), [], "listed but no longer a sink: remove them")

    def test_no_dynamic_dispatch_onto_a_sink_unless_listed(self):
        found = {(module, qualname, kind[len("dynamic:"):]) for module, qualname, _line, kind, *_ in self.sinks
                 if kind.startswith("dynamic:")}
        self.assertEqual(sorted(found - set(DYNAMIC)), [], "a sink reached through a callable the reader can't follow: "
                                                           "call it directly, or list it in DYNAMIC with why")
        self.assertEqual(sorted(set(DYNAMIC) - found), [], "listed but no longer dynamic: remove them")

    def test_the_reviewers_case_is_checked_by_c(self):
        # engine_link_import (reviewer's sixth read): the working .blend named by the asset object it was given is a
        # sink the reader finds, and its function asks C first.
        found = [(q, k, guarded) for m, q, _l, k, cls, guarded in self.sinks
                 if m == "engine_link_import" and cls in ("param", "state")]
        self.assertTrue(found and all(guarded for _q, _k, guarded in found), found)

    def test_every_name_a_module_loads_is_bound(self):
        # p10f (Linux, 0ebc1e0239c): worker_queue._remove_folder's guard had no import; taking a queued part back and
        # a document replaced mid-run both raised NameError (test_blender_parallel_lead, _scene_switch).
        found = []
        for path in sorted(write_sinks.ADDON.rglob("*.py")):
            found += ["%s:%d %s" % (path.relative_to(write_sinks.ADDON), line, name)
                      for line, name in unbound_names(ast.parse(path.read_text(encoding="utf-8")))]
        self.assertEqual(found, [])
        self.assertEqual(unbound_names(ast.parse("def f(p):\n    owned_paths.require_writable(p)\n")),
                         [(2, "owned_paths")])

    def test_a_planted_sink_is_found_and_a_guard_clears_it(self):
        def analyse(source):
            module = destructive_sinks.Module.__new__(destructive_sinks.Module)
            module.name, module.imports, module.functions = "planted", {}, {}
            module.tree = ast.parse(source)
            for node in module.tree.body:
                module._collect(node, "")
            return [(q, k, cls, guarded) for _m, q, _l, k, cls, guarded in
                    write_sinks.Analysis(modules={"planted": module}).run()]
        self.assertEqual(analyse("class Thing:\n    def save(self):\n        open(self.path, 'w').write('x')\n"),
                         [("Thing.save", "open:w", "state", False)])
        self.assertEqual(analyse("import owned_paths\nclass Thing:\n    def save(self):\n"
                                 "        owned_paths.require_writable(self.path)\n"
                                 "        open(self.path, 'w').write('x')\n"),
                         [("Thing.save", "open:w", "state", True)])
        self.assertEqual(analyse("import os\ndef write(path):\n    os.replace(path + '.new', path)\n"
                                 "def caller(thing):\n    write(thing.target)\n"),
                         [("caller", "os.replace via planted.write", "state", False),
                          ("write", "os.replace", "param", False)])
        self.assertEqual(analyse("FOLDER = '/x'\nimport os\ndef clear():\n    os.unlink(os.path.join(FOLDER, 'a'))\n"),
                         [("clear", "os.unlink", "fixed", False)])
        self.assertEqual(analyse("import os, uuid\nFOLDER = '/x'\ndef save():\n"
                                 "    temporary = os.path.join(FOLDER, str(uuid.uuid4()))\n    os.unlink(temporary)\n"),
                         [("save", "os.unlink", "created", False)])
        # A program started through a helper whose argv is a local list: only argv[0] is the program; the other
        # arguments, from Python state, are not (and one report key never takes whichever entry a set put last).
        helper = ("import subprocess\ndef pack(binary, source):\n    command = [binary, '-i', source]\n"
                  "    subprocess.run(command)\n")
        self.assertEqual(analyse(helper + "class Thing:\n    def go(self):\n        pack('/bin/x', self.path)\n"),
                         [("Thing.go", "run:subprocess.run via planted.pack", "fixed", False),
                          ("pack", "run:subprocess.run", "param", False)])
        self.assertEqual(analyse(helper + "class Thing:\n    def go(self):\n        pack(self.tool, '/x')\n"),
                         [("Thing.go", "run:subprocess.run via planted.pack", "state", False),
                          ("pack", "run:subprocess.run", "param", False)])
        # A fresh name in a folder from Python state is still a new file there (p10c-4's design point b).
        self.assertEqual(analyse("import os, uuid\nclass Thing:\n    def save(self):\n"
                                 "        temporary = self.path + str(uuid.uuid4())\n        os.unlink(temporary)\n"),
                         [("Thing.save", "os.unlink", "state", False)])


if __name__ == "__main__":
    unittest.main()
