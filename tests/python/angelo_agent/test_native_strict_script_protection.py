# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict script protection (``strict_protection``, the maintainer's Ruling 17): one opt-in preference, off by
default, that C reads as a plain atomic flag (``angelo_strict_script_protection()``).

Without Blender: the declared preference (off, stored with the preferences), the flag following the stored value
(the host's stand-in for C's own read), who may change it (never an agent script, off only with the person), where it
is drawn, the C side's shape (the getter, the setter that refuses a confined thread)
and its text in every catalogue. The real guard's refusals are ``test_blender_strict_script_protection.py``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
INTERN = ROOT / "source/blender/python/intern"
LOCALE = ROOT / "locale/angelo"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ADDON.parent))


def _import():
    """``strict_protection`` declares its property as it is imported: a stand-in ``bpy.props`` records it."""
    props = types.ModuleType("bpy.props")
    props.BoolProperty = lambda **declared: declared
    stand_in = types.ModuleType("bpy")
    stand_in.props = props
    saved = {name: sys.modules.pop(name, None) for name in ("bpy", "bpy.props")}
    sys.modules.update({"bpy": stand_in, "bpy.props": props})
    try:
        from angelo_agent import strict_protection
    finally:
        for name, module in saved.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module
    return strict_protection


strict = _import()
from angelo_agent import strict_flag  # noqa: E402
DECLARED = strict.StrictProtectionPreferences.__annotations__[strict.NAME]


def stored(value, registered=True):
    """Blender's preferences holding the add-on's ``strict_script_protection`` (absent: an older add-on's)."""
    prefs = types.SimpleNamespace(**({strict.NAME: value} if value is not None else {}))
    addon = types.SimpleNamespace(preferences=prefs) if registered else None
    fake = types.ModuleType("bpy")
    fake.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
        addons={"angelo_agent": addon} if registered else {}))
    return patch.dict(sys.modules, {"bpy": fake}), prefs


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(strict_flag, "_guard", return_value=None)   # The host: no C module, the fallback flag.
        guard.start()
        self.addCleanup(guard.stop)
        strict_flag._fallback[0] = False
        self.addCleanup(strict_flag._fallback.__setitem__, 0, False)   # No test leaves the process's flag on.
        self.addCleanup(strict.__dict__["_path"].__setitem__, 0, None)

    def test_it_is_off_by_default_and_for_preferences_that_never_saved_it(self):
        self.assertIs(strict.DEFAULT, False)
        self.assertIs(DECLARED["default"], False)
        context, _prefs = stored(False)   # The property's default: an update's preferences that never held it.
        with context:
            self.assertIs(strict.sync(), False)
            self.assertIs(strict.enabled(), False)

    def test_an_unreadable_preference_keeps_the_flag(self):
        """The review of 93ef5b1, S6: a failed read never turns protection off."""
        strict_flag._fallback[0] = True
        for value, registered in ((None, True), (None, False)):   # No such preference; no add-on entry.
            context, _prefs = stored(value, registered)
            with context:
                self.assertIs(strict.sync(), True)

    def test_it_is_a_stored_preference_saved_with_the_persons_preferences(self):
        """A plain stored BoolProperty of the add-on's preferences: Blender saves it with them (no getter or setter
        that keeps it elsewhere, never SKIP_SAVE), and only an allowed change is taken (``update``)."""
        self.assertNotIn("get", DECLARED)
        self.assertNotIn("set", DECLARED)
        self.assertNotIn("SKIP_SAVE", DECLARED.get("options", ()))
        self.assertIs(DECLARED["update"], strict._update)
        source = (ADDON / "ui.py").read_text(encoding="utf-8")
        bases = re.search(r"class ANGELO_Preferences\(([^)]*)\)", source).group(1)
        self.assertIn("strict_protection.StrictProtectionPreferences", bases)
        self.assertIn("AddonPreferences", bases)

    def test_the_flag_follows_the_saved_preference(self):
        for value in (True, False, True):
            context, _prefs = stored(value)
            with context:
                self.assertIs(strict.sync(), value)
                self.assertIs(strict.enabled(), value)

    def test_the_flag_is_synced_at_registration_after_every_preference_read_and_on_each_change(self):
        from angelo_agent import prefs_reload
        context, _prefs = stored(True)
        with context, patch.object(prefs_reload, "READ_LISTENERS", []):
            strict.register()
            self.assertIs(strict.enabled(), True)
            self.assertEqual(prefs_reload.READ_LISTENERS, [strict._read])
            strict.register()   # Registered again: one listener.
            self.assertEqual(prefs_reload.READ_LISTENERS, [strict._read])
            strict_flag._fallback[0] = False
            for listener in prefs_reload.READ_LISTENERS:   # A preference read (prefs_reload._reset_all).
                listener()
            self.assertIs(strict.enabled(), True)
            strict.unregister()
            self.assertEqual(prefs_reload.READ_LISTENERS, [])
        registration = (ADDON / "ui.py").read_text(encoding="utf-8")
        self.assertIn("strict_protection.register(state_directory())", registration)
        self.assertIn("strict_protection.unregister()", registration)

    def test_on_survives_a_crash_that_lost_the_preferences_save(self):
        """The review of 93ef5b1, S5: each change is saved at once in the state folder; at start On from either wins,
        and Off from the file never turns saved preferences off."""
        with tempfile.TemporaryDirectory() as folder, patch("angelo_agent.owned_paths.require_writable",
                                                             side_effect=lambda path: path):
            path = Path(folder) / strict.FILE
            for in_file, in_prefs, expected in ((True, False, True), (False, True, True), (None, False, False),
                                                (False, False, False)):
                with self.subTest(file=in_file, prefs=in_prefs):
                    path.unlink(missing_ok=True)
                    if in_file is not None:
                        path.write_text(json.dumps({"version": 1, "on": in_file}))
                    strict_flag._fallback[0] = False
                    context, prefs = stored(in_prefs)
                    with context:
                        self.assertIs(strict.attach(folder), expected)
                        self.assertIs(getattr(prefs, strict.NAME), expected)
                    self.assertIs(json.loads(path.read_text())["on"], expected, "the file says what is in effect")

    def test_the_first_save_makes_a_state_folder_not_made_yet(self):
        """An explicit state folder (``ANGELO_STATE_DIR``) is not made for Angelo as the default one is, and Strict's
        save at registration comes first: it failed with "No such file or directory" on the .tmp (Windows build2 on
        1e19738, every run of test_blender_strict_person_click), so the choice was kept only in the preferences until
        a later change. The save makes the folder, as Angelo's other state stores do (``reviewed_scripts``)."""
        with tempfile.TemporaryDirectory() as parent, patch("angelo_agent.owned_paths.require_writable",
                                                             side_effect=lambda path: path):
            for on in (True, False):
                with self.subTest(on=on):
                    folder = Path(parent) / ("state-%s" % on) / "nested"
                    strict_flag._fallback[0] = False
                    context, _prefs = stored(on)
                    with context:
                        self.assertIs(strict.attach(folder), on)
                    self.assertIs(json.loads((folder / strict.FILE).read_text())["on"], on)
                    self.assertEqual(sorted(p.name for p in folder.iterdir()), [strict.FILE], "no .tmp left")
                    if sys.platform != "win32":
                        self.assertEqual(folder.stat().st_mode & 0o777, 0o700, "Angelo's own folder, as its others")

    def test_an_agent_script_never_turns_it_off_and_off_takes_the_person(self):
        """On narrows (open to anything; C refuses a confined thread itself) and spends the press's ticket; Off is C's
        to allow (here its host stand-in: the person's press or scope) and never while a script runs, each refusal
        with its real reason."""
        from angelo_agent import grant_guard
        cases = (   # (script running, the person's press, from, to, refusal, tickets asked)
            (True, True, True, False, grant_guard.SCRIPT_RUNS, 0), (True, False, False, True, None, 1),
            (False, False, False, True, None, 1), (False, False, True, False, grant_guard.NOT_YOURS, 1),
            (False, True, True, False, None, 1))   # The person turns it off: taken (the positive case).
        for running, person, current, value, reason, asked in cases:
            with self.subTest(running=running, person=person, value=value), \
                    patch.object(grant_guard, "script_running", return_value=running), \
                    patch.object(grant_guard, "person_set", return_value=person) as taken, \
                    patch.object(grant_guard, "_authority",
                                 return_value=types.SimpleNamespace(available=lambda: False)), \
                    patch.object(strict, "_tell") as told:
                context, prefs = stored(current)
                with context:
                    strict.sync()
                    setattr(prefs, strict.NAME, value)   # What the RNA write leaves before its update runs.
                    strict._update(prefs, None)
                    self.assertIs(getattr(prefs, strict.NAME), current if reason else value)
                    self.assertIs(strict.enabled(), current if reason else value)
                self.assertEqual(taken.call_count, asked)
                self.assertEqual([call.args[0] for call in told.call_args_list], [reason] if reason else [])

    def test_a_preference_read_turns_it_off_without_a_press(self):
        """The reviews' S8/S9: a preference read (Load Factory Preferences) is the other legitimate cause of Off; the
        listener's sync takes no press (C knows the read: ``BPY_angelo_guard_preferences_read``)."""
        strict_flag._fallback[0] = True
        context, _prefs = stored(False)
        with context:
            strict._read()
        self.assertIs(strict.enabled(), False)

    def test_a_refused_off_keeps_the_flag_and_gives_the_preference_on_back(self):
        """The re-read of 9b3f450, M3 and its NIT: Off from neither the person nor their read keeps C on, and the
        preference left off past its update gets On back, so the switch agrees and the next sync is quiet."""
        class Guard:
            def strict_script_protection(self):
                return True

            def sync_strict_script_protection(self):
                raise PermissionError("Only your own click in Angelo can turn Strict script protection off.")
        context, prefs = stored(False)
        with context, patch.object(strict_flag, "_guard", return_value=Guard()), patch("builtins.print") as printed:
            self.assertIs(strict.sync(), True)
        self.assertIs(getattr(prefs, strict.NAME), True)
        self.assertIn("stays on", " ".join(str(part) for part in printed.call_args[0]))

    def test_a_refusal_is_shown_with_its_reason(self):
        self.assertIn("{reason}", strict.REFUSED)
        source = (ADDON / "strict_protection.py").read_text(encoding="utf-8")
        tell = source[source.index("def _tell("):source.index("class StrictProtectionPreferences")]
        self.assertIn('grant_hints.refused("prop." + NAME)', tell)
        self.assertIn("rpt_(REFUSED, reason=rpt_(reason))", tell)
        self.assertIn("studio_library_ops.report('WARNING', message)", tell)


class SweepTests(unittest.TestCase):
    """The review of 93ef5b1, M1: a change a script's run left on the preference is put back at its end (On protects,
    so a run that turned it off is put back on)."""

    def sweep(self, prefs):
        from angelo_agent import script_confinement_sweep as sweep
        bpy = types.SimpleNamespace(app=types.SimpleNamespace(handlers=types.SimpleNamespace(), driver_namespace={}),
                                    data=types.SimpleNamespace(texts=[]))
        self.resynced = []
        return sweep.blender_sweep(bpy, lambda f: f, lambda f: False, ("allow_local_cli",),
                                   lambda now, before: bool(now) and not bool(before), lambda: prefs, "angelo_agent",
                                   protective=(strict.NAME,), resync=lambda: self.resynced.append(True))

    def test_a_run_that_turned_it_off_is_put_back_on(self):
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=True)
        begin, end = self.sweep(prefs)
        snapshot = begin()
        prefs.strict_script_protection = False   # property_unset, or any route past the update.
        self.assertIn("setting strict_script_protection", end(snapshot))
        self.assertIs(prefs.strict_script_protection, True)
        self.assertEqual(self.resynced, [True], "C re-synced at the run's end")

    def test_a_changed_definition_is_reported(self):
        """The review of a121c9a, M2's backstop: C refuses it; a definition that changed anyway is reported."""
        pointers = {strict.NAME: 1, "allow_local_cli": 2}
        properties = {name: types.SimpleNamespace(as_pointer=lambda name=name: pointers[name]) for name in pointers}
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=True,
                                      bl_rna=types.SimpleNamespace(properties=properties))
        begin, end = self.sweep(prefs)
        snapshot = begin()
        pointers[strict.NAME] = 3   # Redefined: a new PropertyRNA.
        self.assertIn("a setting's definition changed", end(snapshot))

    def test_a_run_that_turned_it_on_keeps_it(self):
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=False)
        begin, end = self.sweep(prefs)
        snapshot = begin()
        prefs.strict_script_protection = True
        self.assertEqual(end(snapshot), [])
        self.assertIs(prefs.strict_script_protection, True)

    def test_off_at_the_start_reads_no_strict_definition_and_resyncs_nothing(self):
        """Ruling 17 (the macOS bench, 2026-11-22): with Strict off at a run's start, a run can only turn it on, which
        narrows; Strict's own definition is not read and C is not re-synced, so a default run pays for neither."""
        reads = []
        properties = types.SimpleNamespace(get=lambda name: reads.append(name))
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=False,
                                      bl_rna=types.SimpleNamespace(properties=properties))
        begin, end = self.sweep(prefs)
        self.assertEqual(end(begin()), [])
        self.assertNotIn(strict.NAME, reads, "no Strict definition read")
        self.assertEqual(self.resynced, [], "no re-sync")

    def test_off_at_the_start_reads_no_definition_at_all(self):
        """Ruling 17 (the v210-rc macOS bench, 2026-11-22): the widening definitions cost ~19 us a run; with Strict
        off, the sweep reads none (C refuses a confined script's definition change either way)."""
        reads = []
        properties = types.SimpleNamespace(get=lambda name: reads.append(name))
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=False,
                                      bl_rna=types.SimpleNamespace(properties=properties))
        begin, end = self.sweep(prefs)
        self.assertEqual(end(begin()), [])
        self.assertEqual(reads, [])

    def test_on_at_the_start_reports_a_changed_widening_definition(self):
        """The perf re-read of 0874e40, SHOULD-1, while Strict protects: a widening setting redefined to read back its
        old value is caught by its definition."""
        pointers = {strict.NAME: 1, "allow_local_cli": 2}
        properties = {name: types.SimpleNamespace(as_pointer=lambda name=name: pointers[name]) for name in pointers}
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=True,
                                      bl_rna=types.SimpleNamespace(properties=properties))
        begin, end = self.sweep(prefs)
        snapshot = begin()
        pointers["allow_local_cli"] = 3
        self.assertIn("a setting's definition changed", end(snapshot))

    def test_on_at_the_start_keeps_both_checks(self):
        reads = []
        properties = types.SimpleNamespace(get=lambda name: reads.append(name))
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=True,
                                      bl_rna=types.SimpleNamespace(properties=properties))
        begin, end = self.sweep(prefs)
        self.assertEqual(end(begin()), [])
        self.assertTrue(reads, "definitions read at both ends")
        self.assertEqual(self.resynced, [True])

    def test_off_by_cs_flag_never_reads_the_strict_setting(self):
        """The coordinator's (Q): whether Strict protects comes from C's flag (one atomic load); off, the sweep never
        reads Strict's RNA setting at all."""
        from angelo_agent import script_confinement_sweep as sweep

        class Prefs:
            allow_local_cli = False

            @property
            def strict_script_protection(self):
                raise AssertionError("read Strict's setting while C's flag is off")
        bpy = types.SimpleNamespace(app=types.SimpleNamespace(handlers=types.SimpleNamespace(), driver_namespace={}),
                                    data=types.SimpleNamespace(texts=[]))
        resynced = []
        begin, end = sweep.blender_sweep(bpy, lambda f: f, lambda f: False, ("allow_local_cli",),
                                         lambda now, before: bool(now) and not bool(before), lambda: Prefs(),
                                         "angelo_agent", protective=(strict.NAME,), resync=lambda: resynced.append(1),
                                         protecting_now=lambda: False)
        self.assertEqual(end(begin()), [])
        self.assertEqual(resynced, [])

    def test_on_by_cs_flag_puts_it_back(self):
        from angelo_agent import script_confinement_sweep as sweep
        prefs = types.SimpleNamespace(allow_local_cli=False, strict_script_protection=True)
        bpy = types.SimpleNamespace(app=types.SimpleNamespace(handlers=types.SimpleNamespace(), driver_namespace={}),
                                    data=types.SimpleNamespace(texts=[]))
        resynced = []
        begin, end = sweep.blender_sweep(bpy, lambda f: f, lambda f: False, ("allow_local_cli",),
                                         lambda now, before: bool(now) and not bool(before), lambda: prefs,
                                         "angelo_agent", protective=(strict.NAME,), resync=lambda: resynced.append(1),
                                         protecting_now=lambda: True)
        snapshot = begin()
        prefs.strict_script_protection = False
        self.assertIn("setting strict_script_protection", end(snapshot))
        self.assertIs(prefs.strict_script_protection, True)
        self.assertEqual(resynced, [1])

    def test_the_app_registers_it_with_the_sweep(self):
        source = (ADDON / "script_confinement_blender.py").read_text(encoding="utf-8")
        self.assertIn("protective=(STRICT,), resync=strict_sync, protecting_now=strict_enabled", source)
        self.assertIn("from .strict_flag import enabled as strict_enabled", source)

    def test_the_in_app_suite_drives_the_sweep_the_app_registers(self):
        """The re-read of 2328f07, NIT-4: the in-app check runs install_sweeps' own code (its set_sweep captured) and
        drives the Blender sweep it hands over, never one the suite builds itself."""
        suite = (ROOT / "tests/python/angelo_agent/test_blender_strict_script_protection.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?<!registered_)blender_sweep\(", suite), "no sweep built by the suite")
        self.assertIn("script_confinement_blender.install_sweeps(bpy)", suite)
        self.assertIn("begin, end = registered_blender_sweep()", suite)
        helper = suite[suite.index("def registered_blender_sweep():"):suite.index("\ndef main():")]
        self.assertIn("script_confinement._guard = _CapturingGuard(real, captured)", helper)
        self.assertIn("script_confinement._guard = real", helper[helper.index("finally:"):])


class WorkerTests(unittest.TestCase):
    """A worker Blender loads no preferences (--factory-startup): it takes the flag of the Angelo that started it,
    and a warm one launched with another value is never taken or reused (the review of 93ef5b1, S1)."""

    def setUp(self):
        guard = patch.object(strict_flag, "_guard", return_value=None)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(strict_flag._fallback.__setitem__, 0, False)

    def test_a_worker_is_started_with_the_flag_this_angelo_has(self):
        from angelo_agent import worker_process
        for on in (False, True):
            strict_flag._fallback[0] = on
            environ = worker_process.worker_environment({"PATH": "/bin", "HOME": "/h"}, "/p", platform="darwin")
            self.assertEqual(environ[strict_flag.ENVIRONMENT], "1" if on else "0")
        source = (ADDON / "worker_process.py").read_text(encoding="utf-8")
        start = source[source.index("launch = strict_flag.carried()"):]
        self.assertLess(start.index('self.strict = launch[strict_flag.ENVIRONMENT] == "1"'),
                        start.index("options = dict("))
        self.assertIn("**launch)", start[:start.index("stdin=")])
        self.assertEqual(strict_flag.ENVIRONMENT, "ANGELO_STRICT_SCRIPT_PROTECTION")

    def test_a_worker_takes_it_before_its_part_opens_and_its_host_serves(self):
        strict_flag._fallback[0] = False
        self.assertIs(strict_flag.from_environment({strict_flag.ENVIRONMENT: "0"}), False)
        self.assertIs(strict_flag.from_environment({}), False)
        self.assertIs(strict_flag.from_environment({strict_flag.ENVIRONMENT: "1"}), True)
        headless = (ADDON / "headless_main.py").read_text(encoding="utf-8")
        register = headless[headless.index("def _register("):headless.index("def _prepare_preferences(")]
        self.assertLess(register.index("script_confinement_blender.install_app(bpy)"),
                        register.index("strict_flag.from_environment(os.environ)"))
        serve = (ADDON / "worker_main.py").read_text(encoding="utf-8")
        serve = serve[serve.index("def _serve("):]
        self.assertLess(serve.index("_register(bpy, ui)"), serve.index("open_part(bpy, options.file)"))
        self.assertLess(serve.index("open_part(bpy, options.file)"), serve.index("WorkerHost("))

    def test_a_warm_worker_launched_with_another_value_is_never_taken_or_reused(self):
        strict_flag._fallback[0] = True
        for launched, expected in ((None, True), (True, True), (False, False)):
            self.assertIs(strict_flag.matches(types.SimpleNamespace(strict=launched)), expected)
        self.assertIs(strict_flag.matches(object()), True, "not launched yet: it takes the flag then")
        pool = (ADDON / "worker_pool.py").read_text(encoding="utf-8")
        take = pool[pool.index("def take("):pool.index("def reuse(")]
        self.assertLess(take.index('if not strict_flag.matches(entry["worker"]):'), take.index("_pool.remove(entry)"))
        self.assertIn("_release(entry)", take)
        reuse = pool[pool.index("def reuse("):pool.index("def _schedule_adoption(")]
        self.assertIn("or not strict_flag.matches(worker)", reuse)


class SurfaceTests(unittest.TestCase):
    def test_it_is_drawn_beside_always_allow_scripts(self):
        ui = (ADDON / "ui.py").read_text(encoding="utf-8")
        for draw in ("        draw_always_allow(self.layout, self)\n"
                     "        self.layout.prop(self, \"strict_script_protection\")",
                     "    draw_always_allow(layout, prefs)"):
            self.assertIn(draw, ui)
        self.assertEqual(ui.count('prop(prefs, "strict_script_protection")'), 1, "the AI Providers fallback")
        from angelo_agent import providers_page_model as model
        names = {"helper_standing_consent": 1, "always_allow_scripts": 1, strict.NAME: 1}
        properties = {name: types.SimpleNamespace(name=name.replace("_", " ").capitalize(), description="Does it")
                      for name in names}
        prefs = types.SimpleNamespace(helper_standing_consent=False, always_allow_scripts=False,
                                      strict_script_protection=True,
                                      bl_rna=types.SimpleNamespace(properties=properties))
        policy = types.SimpleNamespace(allow_auto_approval=True, require_ask_for_edits=False)
        rows = model._permissions(prefs, policy)["rows"]
        values = [row.get("value") for row in rows]
        self.assertLess(values.index("always_allow_scripts"), values.index(strict.NAME))
        row = rows[values.index(strict.NAME)]
        self.assertEqual((row["kind"], row["action"], row["on"]), ("switch", "toggle", True))
        del properties[strict.NAME]   # Preferences without it (an older add-on build): no row, no error.
        self.assertNotIn(strict.NAME, [row.get("value") for row in model._permissions(prefs, policy)["rows"]])

    def test_its_description_says_what_it_adds_and_that_scripts_can_run_slower(self):
        text = DECLARED["description"]
        for part in ("every path and link", "pipes and handles", "put back", "slower"):
            self.assertIn(part, text)
        self.assertNotIn("thread", text, "2.1.0 stops no script thread in either mode (PARK_SCRIPT_THREADS is off)")
        self.assertEqual(DECLARED["name"], "Strict script protection")


class NativeTests(unittest.TestCase):
    """The C side, as source (the in-app test runs it): the flag's one definition (bpy_angelo_guard_strict.cc)."""

    def setUp(self):
        self.strict = (INTERN / "bpy_angelo_guard_strict.cc").read_text(encoding="utf-8")

    def test_the_getter_is_a_relaxed_atomic_load_declared_for_the_confinement(self):
        header = (INTERN / "bpy_angelo_guard_strict.hh").read_text(encoding="utf-8")
        self.assertIn("bool angelo_strict_script_protection();", header)
        self.assertIn("static std::atomic<bool> strict{false};", self.strict)
        getter = re.search(r"bool angelo_strict_script_protection\(\)\n\{\n(.*?)\n\}", self.strict, re.S).group(1)
        self.assertEqual(getter.strip(), "return blender::angelo_guard::strict.load(std::memory_order_relaxed);")
        self.assertNotIn("RNA_", self.strict, "C never reads RNA for it")

    def test_the_setter_refuses_a_confined_thread_and_off_follows_the_syncs_rule(self):
        setter = re.search(r"static PyObject \*set_strict\(.*?\n\}", self.strict, re.S).group(0)
        self.assertLess(setter.index("refuse_if_confined("), setter.index("store_strict("))
        self.assertLess(setter.index("on == 0 && strict.load(std::memory_order_relaxed) && !off_allowed()"),
                        setter.index("store_strict("))
        self.assertIn('{"set_strict_script_protection", set_strict, METH_O,', self.strict)

    def test_the_sync_reads_the_saved_preference_itself_and_off_has_only_its_two_causes(self):
        """The reviews of 93ef5b1 and a121c9a, S2/S6/S8/S9: no caller hands C the value; Off only for the person
        (their scope or their press's ticket, taken in C) or a preference read; an entry without preferences reads as
        the default (Off), and only a missing entry keeps the flag."""
        sync = re.search(r"static PyObject \*sync_strict\(.*?\n\}", self.strict, re.S).group(0)
        self.assertNotIn("PyArg", sync)
        self.assertLess(sync.index("refuse_if_confined("), sync.index("store_strict("))
        self.assertLess(sync.index("!off_allowed()"), sync.index("store_strict("))
        allowed = re.search(r"static bool off_allowed\(\)\n\{.*?\n\}", self.strict, re.S).group(0)
        for cause in ("person_read", "Scope::control() == PAGE_CONTROL", "Scope::harness()",
                      "authority().take(TICKET, \"ui\""):
            self.assertIn(cause, allowed)
        self.assertNotIn("Scope::person()", self.strict, "Off is this setting's own control's, never any click's")
        self.assertIn('TICKET = "prop.strict_script_protection"', self.strict)
        stored = re.search(r"static int stored_preference\(\)\n\{.*?\n\}", self.strict, re.S).group(0)
        self.assertIn("BKE_addon_find(&U.addons, ADDON)", stored)
        self.assertEqual(stored.count("return -1;"), 1, "only a missing entry keeps the flag")
        self.assertIn("addon->prop ? IDP_GetPropertyFromGroup(addon->prop, PREFERENCE) : nullptr", stored)
        self.assertIn('static const char *const ADDON = "angelo_agent";', self.strict)
        self.assertIn(f'static const char *const PREFERENCE = "{strict.NAME}";', self.strict)
        store = re.search(r"static void store_strict\(const bool on\)\n\{.*?\n\}", self.strict, re.S).group(0)
        # Acts only on a change (S10): both callers hold the GIL, which serialises the load then the store (the re-read
        # of 2328f07, NIT-1: the GIL, not the main thread, is what makes it correct); Strict's share of the hook's work
        # word is added before the flag turns on and taken off after it turns off (the coordinator's (Q)).
        lead = self.strict[:self.strict.index("static void store_strict(const bool on)")]
        lead = lead[lead.rindex("/**"):]
        self.assertIn("the GIL is what makes the\n * load-then-store below correct: it serialises every call", lead, "NIT-1")
        self.assertIn("if (strict.load(std::memory_order_relaxed) == on) {\n    return;\n  }", store, "S10")
        self.assertLess(store.index("hook_event_work.fetch_add(HOOK_WORK_STRICT"), store.index("strict.store(on"))
        self.assertLess(store.index("strict.store(on"), store.index("hook_event_work.fetch_sub(HOOK_WORK_STRICT"))
        wm = (ROOT / "source/blender/windowmanager/intern/wm_files.cc").read_text(encoding="utf-8")
        read = wm[wm.index("BPY_angelo_guard_preferences_read(true);"):
                  wm.index("BPY_angelo_guard_preferences_read(false);")]
        self.assertIn('"addon_utils.reset_all()"', read)

    def test_only_a_read_the_person_started_counts_and_tickets_are_preferences_own(self):
        """The re-read of 9b3f450, M3: a preference read Python started (no press) is no cause for Off, and a
        property ticket is minted only for a press on an add-on preference (a same-named property elsewhere mints
        none)."""
        mark = self.strict[self.strict.index("void BPY_angelo_guard_preferences_read("):]
        self.assertIn("python = PyEval_GetFrame() != nullptr;", mark)
        self.assertIn("reads.push_back(!python);", mark)
        allowed = re.search(r"static bool off_allowed\(\)\n\{.*?\n\}", self.strict, re.S).group(0)
        self.assertIn("!preferences_reads.empty() && preferences_reads.back()", allowed)
        handlers = (ROOT / "source/blender/editors/interface/interface_handlers.cc").read_text(encoding="utf-8")
        gate = handlers[handlers.index("if (but->rnaprop && but->rnapoin.type"):]
        gate = gate[:gate.index("BPY_angelo_guard_note_button(")]
        self.assertIn("RNA_struct_is_a(but->rnapoin.type, RNA_AddonPreferences)", gate)
        self.assertIn("BPY_angelo_guard_note_unarmed_button", handlers[handlers.index(gate):][:3000])

    def test_off_is_bound_to_this_settings_own_controls(self):
        """The re-read of a121c9a, S8: a grant scope records the control it was opened for, and Off accepts only its
        page switch (the digest grant_guard gives its value) or its checkbox's ticket."""
        import hashlib
        from angelo_agent import grant_guard
        digest = grant_guard.value_digest(strict.NAME)
        self.assertEqual(digest, hashlib.sha256(repr(strict.NAME).encode("utf-8")).hexdigest())
        self.assertIn('"page.toggle\\n%s"' % digest, self.strict)
        page = (ADDON / "providers_page_host.py").read_text(encoding="utf-8")
        self.assertIn('grant_guard.ticketed("page.", action, value, _act', page)
        self.assertIn('"action": "toggle", "value": "strict_script_protection"',
                      " ".join((ADDON / "providers_page_model.py").read_text(encoding="utf-8").split()))
        authority = (INTERN / "bpy_angelo_guard_authority.cc").read_text(encoding="utf-8")
        grant = re.search(r"static PyObject \*guard_grant\(.*?\n\}", authority, re.S).group(0)
        self.assertLess(grant.index("authority().take(idname, digest, now())"),
                        grant.index('const Scope::Control control(idname + "\\n" + digest);'))
        self.assertLess(grant.index("Scope::Control control("),
                        grant.index("call_in_scope(args, 2, kwargs, Scope::PERSON)"))

    def test_no_script_changes_a_preferences_definition(self):
        """The review of a121c9a, M2, as a class: redefining (class attribute set or delete), defining through
        bpy.props, RemoveProperty, register_class and unregister_class all ask C on a preferences type."""
        rna = (INTERN / "bpy_rna.cc").read_text(encoding="utf-8")
        props = (INTERN / "bpy_props.cc").read_text(encoding="utf-8")
        for source, function in ((rna, "pyrna_struct_meta_idprop_setattro"), (rna, "pyrna_register_class"),
                                 (rna, "pyrna_unregister_class"), (props, "BPy_RemoveProperty"),
                                 (props, "bpy_prop_deferred_data_or_srna")):
            with self.subTest(function=function):
                body = re.search(r"^(?:static )?[\w *]+\b%s\([^;{]*\)\n\{.*?^\}" % function, source, re.S | re.M)
                self.assertIsNotNone(body, function)
                self.assertIn("BPY_angelo_guard_definition_refusal(srna)", body.group(0))
        ops = (INTERN / "bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        refusal = ops[ops.index("const char *BPY_angelo_guard_definition_refusal("):]
        refusal = refusal[:refusal.index("\n}\n")]
        self.assertIn("!confined_now()", refusal)
        self.assertIn("preference_type(", refusal)

    def test_every_struct_mutation_in_bpy_rna_asks_the_write_refusal(self):
        """The review of 93ef5b1, M1, as a class: every Python entry point of a struct that changes its data (an ID
        property, a reset, an override flag) asks ``angelo_write_refused`` first, as attribute writes do."""
        rna = (INTERN / "bpy_rna.cc").read_text(encoding="utf-8")
        for name in ("pyrna_struct_ass_subscript", "pyrna_struct_property_unset",
                     "pyrna_struct_property_overridable_library_set", "pyrna_struct_id_properties_ensure",
                     "pyrna_struct_id_properties_clear", "pyrna_struct_pop"):
            with self.subTest(function=name):
                body = re.search(r"^static \w+ \*?%s\(.*?^\}" % name, rna, re.S | re.M).group(0)
                self.assertIn("angelo_write_refused(&self->ptr.value(),", body)
        anim = (INTERN / "bpy_rna_anim.cc").read_text(encoding="utf-8")   # Keys and drivers change values later.
        for name in ("keyframe_insert", "keyframe_delete", "driver_add", "driver_remove"):
            with self.subTest(function=name):
                body = re.search(r"^PyObject \*pyrna_struct_%s\(.*?^\}" % name, anim, re.S | re.M).group(0)
                self.assertLess(body.index('angelo_anim_refused(&self->ptr.value(), "%s")' % name),
                                body.index("_parse(" if "keyframe" in name else "PyArg_Parse"))

    def test_it_is_built_and_registered(self):
        self.assertIn("  bpy_angelo_guard_strict.cc\n", (INTERN / "CMakeLists.txt").read_text(encoding="utf-8"))
        self.assertIn("PyModule_AddFunctions(module, strict_methods);",
                      (INTERN / "bpy_angelo_guard_confine.cc").read_text(encoding="utf-8"))

    def test_a_confined_preference_write_is_refused_in_c(self):
        """``BPY_angelo_guard_write_refusal`` refuses a confined thread's write to any add-on preference."""
        ops = (INTERN / "bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        self.assertRegex(ops, r"for \(StructRNA \*kind : \{RNA_Preferences, RNA_AddonPreferences,")


class DefinitionTests(unittest.TestCase):
    """The re-read of 9b3f450, S13: a definition a script changed in place never reaches a later registration."""

    def test_fresh_writes_every_kept_definition_anew(self):
        from angelo_agent import preference_definitions

        class Deferred:
            def __init__(self, function, keywords):
                self.function, self.keywords = function, keywords

        def BoolProperty(**keywords):   # noqa: N802 - bpy.props' name.
            return Deferred(BoolProperty, keywords)

        class Mixin:
            __annotations__ = {"guarded": BoolProperty(name="Guarded", update=print)}

        class Preferences(Mixin):
            __annotations__ = {"plain": BoolProperty(name="Plain")}
        fresh = preference_definitions.keep(Preferences)
        Mixin.__annotations__["guarded"].keywords.pop("update")             # In place: what the journal can't see.
        Preferences.__annotations__["plain"] = BoolProperty()                # Replaced inside the dict.
        self.assertEqual(fresh(), 2)
        self.assertIs(Mixin.__annotations__["guarded"].keywords["update"], print)
        self.assertEqual(Preferences.__annotations__["plain"].keywords, {"name": "Plain"})
        Mixin.__annotations__["guarded"].keywords["name"] = "changed"   # A fresh one is its own: the record is kept.
        fresh()
        self.assertEqual(Mixin.__annotations__["guarded"].keywords["name"], "Guarded")

    def test_every_registration_writes_them_anew_first(self):
        ui = (ADDON / "ui.py").read_text(encoding="utf-8")
        self.assertIn("fresh_preference_definitions = preference_definitions.keep(ANGELO_Preferences)", ui)
        register = ui[ui.index("def register():"):]
        self.assertLess(register.index("fresh_preference_definitions()"), register.index("register_class(cls)"))
        headless = (ADDON / "headless_main.py").read_text(encoding="utf-8")
        self.assertLess(headless.index("ui.fresh_preference_definitions()"),
                        headless.index("for cls in (ui.ANGELO_Preferences"))


class TranslationTests(unittest.TestCase):
    def test_every_catalogue_translates_its_name_and_description(self):
        messages = (DECLARED["name"], DECLARED["description"])
        template = (LOCALE / "angelo.pot").read_text(encoding="utf-8")
        catalogues = sorted((LOCALE / "po").glob("*.po")) + [LOCALE / "src/ar.po"]
        self.assertEqual(len(catalogues), 21, "20 languages, Arabic in reading order too")
        for message in messages:
            quoted = '\nmsgid "%s"\nmsgstr "' % message.replace('"', '\\"')
            self.assertIn(quoted, template)
            for path in catalogues:
                text = path.read_text(encoding="utf-8")
                found = re.search(re.escape(quoted) + r'(.*)"\n', text)
                self.assertTrue(found and found.group(1).strip(), "%s: %s" % (path.name, message[:40]))


if __name__ == "__main__":
    unittest.main()
