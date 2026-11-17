# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent scripts run confined (script_confinement, doc/angelo/script_confinement.rst; maintainer, 2026-11-14:
"close it fully"). Each class of attack runs as agent code in a fresh interpreter with the confinement installed over
Angelo's real private-folder registry (``confinement_attacks``, ``attacks_table``): aliases of a key file, private
paths one by one, programs, native code, taking the guard off, and work left to run later. Ordinary script work must
still run, and Angelo's own code keeps its access once the script has ended.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attacks_table  # noqa: E402

ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"


def _child(*extra):
    with tempfile.TemporaryDirectory(prefix="confinement-") as work:
        done = subprocess.run([sys.executable, "-I", str(HERE / "confinement_attacks.py"), work, *extra],
                              capture_output=True, text=True, timeout=240, cwd=work)
        if done.returncode != 0:
            raise AssertionError("attack child failed:\n" + done.stdout[-3000:] + done.stderr[-5000:])
        return json.loads(done.stdout.strip().splitlines()[-1])


class ConfinementAttackTests(unittest.TestCase):
    results = None

    @classmethod
    def setUpClass(cls):
        cls.results = _child()

    def assert_refused(self, name, reasons=None):
        needs = attacks_table.NEEDS.get(name)
        if needs in self.results.get("unavailable", ()):
            return   # Not applicable here (Windows: no fork, no /dev/fd, links need a privilege); never a skip.
        result = self.results[name]
        self.assertTrue(result["refused"], "%s was not refused: %r" % (name, result))
        if reasons is not None:
            self.assertIn(result["reason"], reasons, name)

    def test_aliases_of_a_key_file_are_refused(self):
        from angelo_agent.script_confinement import PRIVATE
        for name in ("alias_symlink", "alias_folder_link", "alias_case", "alias_dotdot", "alias_dir_fd",
                     "alias_dev_fd", "alias_fdopen", "alias_new_symlink", "alias_new_folder_link", "alias_hard_link",
                     "chdir_relative", "pathlib_read", "sqlite_open", "mmap_held", "glob_private"):
            with self.subTest(attack=name):
                self.assert_refused(name, {PRIVATE})
        unicode = self.results["alias_unicode"]
        if sys.platform == "darwin":   # One file under both spellings there (APFS): refused.
            self.assert_refused("alias_unicode", {PRIVATE})
        else:   # Linux and NTFS keep both spellings apart: the other spelling is no file at all.
            self.assertTrue(unicode["refused"] or "FileNotFoundError" in unicode.get("error", ""), unicode)

    def test_every_private_path_is_refused_for_every_operation(self):
        from angelo_agent.script_confinement import PRIVATE
        targets = self.results["targets"]
        self.assertGreaterEqual(len(targets), 6, "the registry's folders and the named state files")
        for index, target in enumerate(targets):
            for name in attacks_table.PER_PATH:
                with self.subTest(path=target, operation=name):
                    self.assert_refused("%s[%d]" % (name, index), {PRIVATE})
        for target, intact in self.results["targets_intact"].items():
            with self.subTest(path=target):
                self.assertTrue(intact, "changed or removed")

    def test_startup_folders_are_read_only(self):
        from angelo_agent.script_confinement import STARTUP
        self.assert_refused("startup_write", {STARTUP})
        self.assert_refused("startup_rename_in", {STARTUP})
        self.assertEqual(self.results["config_intact"], [])
        self.assertNotIn("error", self.results["read_config"])
        self.assertFalse(self.results["read_config"]["refused"])

    def test_the_chats_own_workspace_is_open_but_nothing_around_it(self):
        """The chat's default workspace lies in the private state folder: scripts use it as the file tools do
        (workspace_files, in ALLOWED), while a profile folder in it, its chat folder and a link out stay private."""
        from angelo_agent.script_confinement import PRIVATE
        for name in ("workspace_profile", "workspace_beside", "workspace_up", "workspace_link_out"):
            with self.subTest(attack=name):
                self.assert_refused(name, {PRIVATE})
        self.assertFalse(self.results["workspace_files"]["refused"], self.results["workspace_files"])
        self.assertNotIn("error", self.results["workspace_files"])

    def test_tool_output_folders_are_read_only_and_untrusted(self):
        """What Angelo's tools save for scripts (downloads): read, imported, never changed; code from there is the
        agent's, not installed code."""
        from angelo_agent.script_confinement import TOOL_OUTPUT
        for name in ("tool_output_write", "tool_output_remove", "tool_output_rename_in"):
            with self.subTest(attack=name):
                self.assert_refused(name, {TOOL_OUTPUT})
        self.assertTrue(self.results["downloads_intact"])
        self.assertNotIn("error", self.results["read_tool_output"])
        self.assertFalse(self.results["read_tool_output"]["refused"])
        self.assertTrue(self.results["download_code_is_agents"])

    def test_programs_are_refused(self):
        from angelo_agent.script_confinement import PROGRAMS
        for name in ("subprocess_run", "os_system", "posix_spawn", "fork", "exec_program", "fork_exec_direct",
                     "multiprocessing"):
            with self.subTest(attack=name):
                self.assert_refused(name, {PROGRAMS})
        self.assertTrue(self.results["marker_absent"], "a program ran")

    def test_the_network_is_refused(self):
        from angelo_agent.script_confinement import NETWORK
        for name in ("network_connect", "network_resolve", "network_urllib", "network_http"):
            with self.subTest(attack=name):
                self.assert_refused(name, {NETWORK})

    def test_native_code_is_refused(self):
        from angelo_agent.script_confinement import NATIVE
        for name in ("ctypes_load", "ctypes_loaded_call", "ctypes_memory"):
            with self.subTest(attack=name):
                self.assert_refused(name, {NATIVE})
        # Loading an extension from outside the app; in the app a second module made from a loaded module's spec
        # (``_bisect`` is loaded) is refused first, as a way to get its original functions back.
        from angelo_agent.script_confinement import GUARD
        self.assert_refused("extension_outside", {NATIVE, GUARD})
        self.assertFalse(self.results["trusted_extension"]["refused"], self.results["trusted_extension"])

    def test_the_guard_cannot_be_taken_off(self):
        from angelo_agent.script_confinement import GUARD, PRIVATE
        added = self.results["unhook_add"]   # Dropped silently or refused: either way its hook never ran.
        self.assertTrue(added["refused"] or not added.get("error"), added)
        for name in ("unhook_gc", "unhook_trace", "unhook_frames", "unhook_code_swap",
                     "unhook_new_function", "unhook_code_replace", "add_roots"):
            with self.subTest(attack=name):
                self.assert_refused(name, {GUARD})
        # Ending its own confinement early: refused by the Python stand-in; in C there is nothing to end, so the read
        # after it is refused as private.
        self.assert_refused("end_early", {GUARD, PRIVATE})
        # Rebinding this module's names, os.path and builtins changes nothing the hook relies on.
        self.assert_refused("unhook_rebind_then_read", {PRIVATE})
        # Zeroing the grant guard's counter: the shared signal still says a script runs.
        self.assert_refused("unhook_counter_then_grant", {PRIVATE})

    def test_rebinding_the_script_file_name_changes_nothing(self):
        from angelo_agent.script_confinement import PRIVATE
        self.assertTrue(self.results["marker_restored"])
        after = self.results["after_rename"]
        # The Python stand-in confines it by the runner's frame; the C runner runs only code compiled under the name it
        # was given, so a script compiled under another name never runs at all.
        self.assertTrue(after["refused"] and after["reason"] == PRIVATE or "TypeError" in after.get("error", ""), after)

    def test_work_left_for_later_runs_confined(self):
        fired = self.results["timers_fired"]
        self.assertEqual(len(fired), 2, fired)
        for outcome in fired:
            self.assertTrue(outcome.startswith("refused"), fired)
        self.assertEqual(self.results["handlers_fired"], ["refused"])
        self.assertEqual(self.results["later_fired"], ["refused", "refused"])
        self.assertEqual(self.results["threads"], ["refused", "refused"])
        self.assertTrue(self.results["marker_absent"])

    def test_what_a_script_rebinds_is_put_back(self):
        findings = {name: " ".join(self.results[name].get("findings", [])) for name in
                    ("monkeypatch_left", "path_left", "builtin_left", "handler_builtin")}
        self.assertIn("angelo_agent.private_roots", findings["monkeypatch_left"])
        self.assertIn("sys.path", findings["path_left"])
        self.assertIn("builtins", findings["builtin_left"])
        self.assertIn("bpy.app.handlers.depsgraph_update_post", findings["handler_builtin"])

    def test_namespace_rebindings_of_every_kind_are_put_back(self):
        """Item assignment, update, exec into the namespace, object.__setattr__, a cleared namespace and a module
        swapped in sys.modules (the coordinator's ask: a rebinding by any route, and a namespace the script tries to
        take out of the journal by replacing it)."""
        for name, put_back in self.results["rebindings_put_back"].items():
            with self.subTest(name=name):
                self.assertTrue(put_back, "private_roots.%s is still the script's" % name)
        self.assertTrue(self.results["roots_module_put_back"], "sys.modules entry still the fake")
        self.assertEqual(self.results["cleared_namespace_put_back"], [], "names a cleared namespace lost")
        for name in ("rebind_dict_item", "rebind_dict_update", "rebind_exec_globals", "rebind_object_setattr",
                     "rebind_clear", "rebind_swap_module"):
            with self.subTest(attack=name):
                self.assertNotIn("error", self.results[name], self.results[name])

    def test_modules_first_imported_by_the_script_are_journaled(self):
        """A module the script imports is watched from the moment it is added to sys.modules: a rebinding in it is
        put back, and a value the script planted elsewhere that it copied while it defined itself gets the original.
        The journal's watch list cannot be changed by a script. (The C journal's; the Python stand-in, used where
        the C module is absent, is not run here.)"""
        if "journal" in self.results.get("unavailable", ()):
            return   # Not applicable: the stand-in (host tests without the C module); never a skip.
        for name in attacks_table.JOURNAL:
            with self.subTest(attack=name):
                self.assertNotIn("error", self.results[name], self.results[name])
        for name, put_back in self.results["fresh_put_back"].items():
            with self.subTest(name=name):
                self.assertTrue(put_back, "%s is still the script's" % name)
        self.assert_refused("rewatch")

    def test_ordinary_script_work_still_runs(self):
        for name in attacks_table.ALLOWED:
            with self.subTest(work=name):
                result = self.results[name]
                self.assertFalse(result["refused"], result)
                self.assertNotIn("error", result)

    def test_angelo_keeps_its_access_after_the_script(self):
        self.assertEqual(self.results["unconfined_read"], '{"key": "sk-original"}')
        self.assertFalse(self.results["confined_at_end"])
        for name in attacks_table.ALLOWED:
            self.assertFalse(self.results[name]["confined_after"], name)


class ConfinementCostTests(unittest.TestCase):
    def test_overhead_is_measured_and_small_with_no_script(self):
        """Measured, not tuned: the numbers go in the doc. The bound only catches a hook that judges every event."""
        numbers = _child("bench")
        print("\nconfinement cost (us per open / per id()):", {key: round(value, 3) for key, value in numbers.items()
                                                                 if key.endswith("_us")})
        self.assertLess(numbers["idle_open_us"], 200)
        self.assertLess(numbers["idle_id_us"], 5)


class ConfinementTextTests(unittest.TestCase):
    """The refusals are English to the agent and translated where the person reads them."""

    def test_every_refusal_is_in_every_catalogue(self):
        from angelo_agent.script_confinement import REASONS
        locale = HERE.parents[2] / "locale/angelo"
        catalogues = [locale / "angelo.pot", locale / "src/ar.po"] + sorted((locale / "po").glob("*.po"))
        for catalogue in catalogues:
            text = catalogue.read_text(encoding="utf-8").replace("\r\n", "\n")   # A Windows checkout's line ends.
            for reason in REASONS:
                with self.subTest(catalogue=catalogue.name, reason=reason[:40]):
                    escaped = reason.replace('"', '\\"')
                    self.assertIn('msgid "%s"\nmsgstr "' % escaped, text)
                    if catalogue.suffix == ".po":
                        self.assertNotIn('msgid "%s"\nmsgstr ""\n' % escaped, text, "untranslated")

    def test_the_person_sees_the_refusal_not_an_exception_name(self):
        from angelo_agent import activity_text
        from angelo_agent.script_confinement import PRIVATE, PROGRAMS
        for reason in (PRIVATE, PROGRAMS):
            with self.subTest(reason=reason[:40]):
                self.assertEqual(activity_text.friendly_error("ScriptConfined: %s (/x) (line 3)" % reason), reason)


class ConfinementWiringTests(unittest.TestCase):
    """Source pins: the one executor runs agent code through ``run_confined``, the add-on and the headless mains
    install it, and the grant guard reads the confinement's signal first."""

    def test_the_executor_runs_agent_code_confined(self):
        source = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        self.assertIn('arguments["code"], namespace, prepared.compiled,', source)
        # The read-back runs inside the confinement (``finish``), never on the script's objects afterwards.
        self.assertIn('lambda names: script_errors.read_back(names.get("result"))', source)
        self.assertNotIn('read_back(namespace.get("result"))', source)
        self.assertNotIn("exec(code, namespace)", source)

    def test_the_file_name_is_the_one_scripts_compile_under(self):
        from angelo_agent import script_confinement, script_source
        self.assertEqual(script_confinement.MARK, script_source.SCRIPT_FILE)

    def test_installed_before_any_agent_script(self):
        for name in ("__init__.py", "headless_main.py"):
            with self.subTest(module=name):
                self.assertIn("script_confinement_blender.install_app(bpy)",
                              (ADDON / name).read_text(encoding="utf-8"))

    def test_every_native_gate_is_wired(self):
        """The C confinement (``_bpy._angelo_guard``): installed before Python starts, and asked at every place Blender
        keeps a callable for later, runs an operator, sets a file path, opens a file, saves an image or starts a job.
        """
        source = HERE.parents[2] / "source/blender"
        pins = {
            "python/intern/bpy_interface.cc": ["BPY_angelo_guard_confine_install();"],
            "python/intern/bpy.cc": ['"_angelo_guard", BPY_angelo_guard_module()'],
            "python/intern/bpy_app_timers.cc": ["BPY_angelo_guard_defer_if_confined(function)"],
            "python/intern/bpy_msgbus.cc": ["BPY_angelo_guard_defer_if_confined(callback_notify)"],
            "python/intern/bpy_rna_callback.cc": ["BPY_angelo_guard_defer_if_confined(cb_func)"],
            "python/intern/bpy_operator_function.cc": ["BPY_angelo_guard_operator_refusal(C, ot, &ptr)"],
            "python/intern/bpy_rna.cc": ["BPY_angelo_guard_path_property_refusal(prop, param)"],
            "windowmanager/intern/wm_jobs.cc": ["gate->enter(wm_job->angelo_confined)",
                                                "wm_job->angelo_confined = gate->depth()"],
            "imbuf/intern/writeimage.cc": ["gate->refused(filepath, true)"],
        }
        for name, needles in pins.items():
            text = (source / name).read_text(encoding="utf-8")
            for needle in needles:
                with self.subTest(file=name, needle=needle):
                    self.assertIn(needle, text)
        install = (source / "python/intern/bpy_interface.cc").read_text(encoding="utf-8")
        self.assertLess(install.index("BPY_angelo_guard_confine_install();"),
                        install.index("status = Py_InitializeFromConfig(&config);"), "before Python starts")
        fileops = (source / "blenlib/intern/fileops_c.cc").read_text(encoding="utf-8")
        import re
        for function in ("BLI_fopen", "BLI_gzopen", "BLI_open", "BLI_open_replacing", "BLI_rename",
                         "BLI_rename_overwrite", "BLI_delete", "BLI_delete_soft", "BLI_copy", "BLI_file_touch",
                         "BLI_dir_create_recursive"):
            bodies = re.findall(r"\b%s\([^)]*\)\n\{\n(.*?)\n\}" % function, fileops, re.S)
            with self.subTest(function=function):
                self.assertTrue(bodies, "defined")
                for body in bodies:
                    self.assertIn("gate_refuses(", body.split("\n", 3)[0] + body.split("\n", 3)[1])

    def test_operators_that_reload_the_add_ons_are_refused(self):
        """read_factory_settings and read_homefile naming an app template run ``addon_utils.reset_all()`` inside the
        region, whose end puts the old classes back and breaks every add-on (the 2.0.0 Windows and Linux legs of
        script_document_replaced): refused by C and by the Python mirror alike."""
        from angelo_agent import script_confinement_blender as blender
        self.assertIn("wm.read_factory_settings", blender.DENIED_OPERATORS)
        self.assertIn('idname == "wm.read_homefile" and "app_template" in kwargs',
                      (ADDON / "script_confinement_blender.py").read_text(encoding="utf-8"))
        source = HERE.parents[2] / "source/blender/python/intern"
        self.assertIn('"WM_OT_read_factory_settings"',
                      (source / "bpy_angelo_guard_confine.cc").read_text(encoding="utf-8"))
        ops = (source / "bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        self.assertIn('STREQ(ot->idname, "WM_OT_read_homefile")', ops)
        self.assertIn('RNA_struct_find_property(ptr, "app_template")', ops)

    def test_what_the_person_names_is_scoped_as_the_review_ruled(self):
        """The review of piece 10: the open document's folder is never vouched (the file tools anchor on it through
        ``document_folder``), a file-browser pick ends when its operator's execute returns, a path the person types
        into a file or folder property counts until Python sets that property, and every Blender writer Angelo's
        code calls is checked at the operator call (``check_existing``), not per operator."""
        source = HERE.parents[2] / "source/blender"
        self.assertNotIn("owned_paths.vouch(Path(project_path).parent", (ADDON / "ui.py").read_text(encoding="utf-8"))
        self.assertIn("require_anchorable(path, within)",
                      (ADDON / "checkpoint_filesystem.py").read_text(encoding="utf-8"))
        self.assertIn("document_folder(within)", (ADDON / "owned_paths.py").read_text(encoding="utf-8"))
        wm = (source / "windowmanager/intern/wm_event_system.cc").read_text(encoding="utf-8")
        execute = wm.index("handler->op->type->exec(C, handler->op)")
        self.assertLess(execute, wm.index("BPY_angelo_guard_picks_done();", execute))
        for name, needle in (("editors/interface/interface.cc", "angelo_note_typed(&but->rnapoin, but->rnaprop);"),
                             ("editors/space_buttons/buttons_ops.cc", "angelo_note_typed(&fbo->ptr, fbo->prop);"),
                             ("python/intern/bpy_rna.cc", "BPY_angelo_guard_forget_typed(ptr->owner_id, ptr->data,")):
            with self.subTest(file=name):
                self.assertIn(needle, (source / name).read_text(encoding="utf-8"))
        ops = (source / "python/intern/bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        self.assertIn('RNA_struct_find_property(ptr, "check_existing")', ops)
        # Angelo's own calls only (its package's code on the stack), every writer by check_existing.
        self.assertLess(ops.index("if (!angelo_on_stack()) {"), ops.index("const char *refusal = writer_refusal(ptr);"))
        # The re-review: a browser an agent opened names nothing; an NTFS stream on a named name refuses (Windows).
        # One predicate for the browser's tag, its refusal and popups (the re-read of 9dcfb883ebb, SHOULD-2).
        self.assertEqual(wm.count("handler->angelo_from_agent = BPY_angelo_guard_thread_confined() || "
                                  "WM_angelo_in_agent_modal();"), 2)
        self.assertIn('return WM_angelo_in_agent_modal() &&\n                   (file_browser ||\n'
                      '                    (RNA_struct_is_a(ptr->type, RNA_WindowManager) && '
                      'STREQ(function, "fileselect_add"))) ?', ops)
        # SHOULD-7: an open browser's settings and its operator's properties are refused to a confined write.
        self.assertIn("RNA_struct_is_a(ptr->type, RNA_FileSelectParams)", ops)
        self.assertIn("return file_browser_pointer(ptr) ? POINTER : nullptr;", ops)
        self.assertIn("data == op->properties", wm)
        self.assertIn("!angelo_fileselect_from_agent(wm, ophandle)", wm)
        self.assertIn('"fileselect_add"', ops)
        self.assertIn("render_writer_refusal(C, ot, ptr)", ops)
        writable = (source / "python/intern/bpy_angelo_guard_confine_writable.cc").read_text(encoding="utf-8")
        self.assertIn("names_a_stream(path)", writable)
        self.assertGreaterEqual(writable.count("names_a_stream(path)"), 3, "may_write_for, name and may_run")
        # A typed path is keyed by its data-block's session id, never only an address (the re-read, NIT).
        self.assertIn('SNPRINTF(tag, "id%u:%p/%s", owner->session_uid, data,', writable)
        self.assertNotIn("fprintf(file", writable, "nothing is written to a store file")
        self.assertIn("if (folder && persist) {", writable)

    def test_reread_four_parts_every_design_needs(self):
        """The re-read of 0a0d4e95743 (review 4): a tag is the data's type and property (MUST-5); a file browser's
        functions are refused to a script (SHOULD-3); a job-less task runs with no job (SHOULD-8); a new file below
        an anchor is made only where the writer's resolved path is (SHOULD-9). The writers it found ungated are in
        test_native_c_write_sinks (MUST-3)."""
        root = HERE.parents[2] / "source/blender"
        access = (root / "makesrna/intern/rna_access.cc").read_text(encoding="utf-8")
        self.assertIn("gate->script_property_set(ptr->owner_id->session_uid, ptr->type, RNA_property_identifier(prop));",
                      access)
        self.assertNotIn("script_string_set", access)
        writers = (root / "blenlib/BLI_angelo_writers.hh").read_text(encoding="utf-8")
        self.assertIn("constexpr Input(const void *owner, const void *data, const char *type, const char *prop)",
                      writers, "four values always: no input leaves its type out")
        gate = (root / "python/intern/bpy_angelo_guard_confine_writers.cc").read_text(encoding="utf-8")
        self.assertIn("return true; /* A wrong declaration over-judges, loudly;", gate)
        ops = (root / "python/intern/bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        self.assertIn("RNA_struct_is_a(ptr->type, RNA_SpaceFileBrowser) ||", ops)
        self.assertIn("  if (file_browser) {\n    return POINTER;", ops)
        pool = (root / "blenlib/intern/task_pool.cc").read_text(encoding="utf-8")
        self.assertNotIn("if (angelo_job == nullptr) {", pool, "no early return keeps the stealing thread's job")
        self.assertIn("  const angelo_writers::JobScope job(angelo_job);\n  run(pool, taskdata);", pool)
        beside = (root / "python/intern/bpy_angelo_guard_confine_beside.cc").read_text(encoding="utf-8")
        self.assertIn("if (write_key(target, false) != key) {", beside)
        # The re-read of cf5befe2877, M2: the person's confirm in any operator's file browser (never an agent's) is
        # their pick for that run, so Blender's own writers there (Save External, image save) are never refused.
        wm = (root / "windowmanager/intern/wm_event_system.cc").read_text(encoding="utf-8")
        picked = wm[wm.index("static void angelo_note_picked(void *ophandle)"):]
        picked = picked[:picked.index("\n}\n")]
        self.assertIn("if (eventval == EVT_FILESELECT_EXEC && !angelo_fileselect_from_agent(wm, ophandle)) {", wm)
        # The re-read of 70600d6438b, MUST-1: only a browser that saves (or an Angelo operator's) names a pick; a
        # cancel or a window that won't close drops it; a pick counts only for an unconfined caller.
        self.assertIn('RNA_struct_find_property(op->ptr, "check_existing")', picked)
        self.assertIn("ELEM(eventval, EVT_FILESELECT_CANCEL, EVT_FILESELECT_EXTERNAL_CANCEL)", wm)
        self.assertEqual(wm.count("BPY_angelo_guard_picks_done();"), 3, "after the run, on a cancel, on a refused close")
        writable = (root / "python/intern/bpy_angelo_guard_confine_writable.cc").read_text(encoding="utf-8")
        self.assertIn("return vouches::matches(key, now, picks_count())", writable)
        self.assertIn("if (confined_now() || WM_angelo_in_agent_modal()) {", writable)

    def test_the_data_decides_not_the_place(self):
        """The re-read of f9f24cc6ffd (round 3): what a confined script sets is tagged where every string is set (C's
        funnel, not Python's alone), and every data-block made on its thread is its own; writers ask by these names."""
        root = HERE.parents[2] / "source/blender"
        header = (root / "python/intern/bpy_angelo_guard_confine.hh").read_text(encoding="utf-8")
        for declaration in ("bool BPY_angelo_guard_script_set(const ID *owner, const char *type, const char *prop);",
                            "bool BPY_angelo_guard_script_originated(const ID *id);",
                            "bool BPY_angelo_guard_output_allowed(const char *path, const ID *owner, const void *data, "
                            "const char *prop);"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, header)
        access = (root / "makesrna/intern/rna_access.cc").read_text(encoding="utf-8")
        self.assertEqual(access.count("  angelo_note_string_set(ptr, prop_rna_or_id);"), 2, "string set and bytes")
        lib_id = (root / "blenkernel/intern/lib_id.cc").read_text(encoding="utf-8")
        self.assertIn("gate->script_id_made(id->session_uid, angelo_read_source);", lib_id)
        readfile = (root / "blenloader/intern/readfile.cc").read_text(encoding="utf-8")
        self.assertEqual(readfile.count("BKE_lib_libblock_session_uid_read_source(nullptr);"), 2,
                         "read data is judged by the file it came from, at both numbering sites")
        output = (root / "python/intern/bpy_angelo_guard_confine_output.cc").read_text(encoding="utf-8")
        self.assertIn("return nullptr; /* A path: tagged as the script's; writers judge what they write from it. */",
                      output)
        self.assertIn("RNA_struct_is_a(ptr->type, RNA_OperatorProperties)", output,
                      "an operator's arguments: its judge")
        beside = (root / "python/intern/bpy_angelo_guard_confine_beside.cc").read_text(encoding="utf-8")
        self.assertIn("vouches::matches_typed(key, typed_tag(owner, data, prop))", beside, "typed for THAT property")
        ops = (root / "python/intern/bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        self.assertIn("  if (CTX_wm_space_file(C) != nullptr) {\n    return POINTER;", ops,
                      "any operator in a file browser's context: refused to a script (SHOULD-6)")
        text = (root / "makesrna/intern/rna_text.cc").read_text(encoding="utf-8")
        self.assertIn('RNA_def_property(srna, "filepath", PROP_STRING, PROP_FILEPATH);', text)
        rna = (root / "python/intern/bpy_rna.cc").read_text(encoding="utf-8")
        self.assertLess(rna.index("BPY_angelo_guard_output_path_refusal(ptr, prop, param)"),
                        rna.index("BPY_angelo_guard_forget_typed(ptr->owner_id, ptr->data,"),
                        "judged before what the person typed is forgotten")
        for name in ("download_folders.py", "engine_link_assets.py"):
            with self.subTest(file=name):
                self.assertIn("owned_paths.make_dir_beside(", (ADDON / name).read_text(encoding="utf-8"))

    def test_the_grant_guard_reads_the_confinement_first(self):
        source = (ADDON / "grant_guard.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("script_confinement.confined()"), 2, "script_running and inside_agent_code")


if __name__ == "__main__":
    unittest.main()
