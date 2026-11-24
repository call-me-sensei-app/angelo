# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The confinement costs nothing on Blender's hot paths while no agent script runs (2.0.0 declaration 4, fixed in
2.1.0).

Piece 10 (a7b45a13c5f) added a note to every RNA property setter that fetched the file gate and called its
``confined`` through a function pointer on every set, script or not. 2.0.0's side-by-side with stock Blender 5.3
measured ``rna/foreach_set_buffer_750k`` at 1.846e8 thread instructions against stock's 1.664e8 (+11.0%). Now every
such hook asks ``BLI_fileops_confine_possible()`` first (three relaxed loads, BLI_fileops.hh): all zero means no
thread is confined, so the call is skipped. The thread states' part is kept by the type of the confinement's own
thread states (``ConfineState``), so no write to them can miss it; live agent code and "ask always" are separate
parts, so no error in one can cancel another (BREAKIT script-isolation-review-fable-2 A2-MUST-1).

The count must be nonzero whenever ``confined_now`` could answer yes, or a hot path skips a confined set. Besides
the thread states, ``confined_now`` answers yes when the stack test finds agent code, which a script's function can
reach after its region (2cfbccf9820 missed that: BREAKIT script-isolation-review-fable-1 A-MUST-1). So every source
of "agent code" holds the count while it exists, by identity: code made under an agent name (the watcher), code
renamed to one (the ``_fix_co_filename`` wrapper), each taint-table entry; a tainted name asks always.

These checks read the sources: every per-set, per-element or per-data-block hook in the hot files asks the count
before the gate or the guard, ``confined_now`` itself starts with it, its states are ``ConfineState``, and each of
``agent_code``'s sources keeps the count (a new source fails the check until it does). Whether the count is right
at run time is ``test_blender_script_confinement``'s (``leftover_code_confined_after_region``,
``renamed_code_confined_after_region``, ``rename_loop_counts_never_negative``). Instruction counts
are measured by the runs (``bench_rna_hot_paths``), not here. Dependency-free.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender"
FAST = "BLI_fileops_confine_possible()"
# Files whose guard hooks run per property set, per element or per data-block.
HOT = {
    "makesrna/intern/rna_access.cc": ("BLI_fileops_gate_get()",),
    "blenkernel/intern/lib_id.cc": ("BLI_fileops_gate_get()",),
    "python/intern/bpy_rna.cc": ("BPY_angelo_guard_write_refusal(", "BPY_angelo_guard_value_refusal("),
}


def functions(text):
    """``[(name, body)]`` of each top-level function definition (a line at column 0 ending a signature with ``{``
    on the next line or the same one, up to the next ``}`` at column 0)."""
    found, lines, index = [], text.splitlines(), 0
    while index < len(lines):
        line = lines[index]
        start = re.match(r"^[A-Za-z_][\w:<>\s\*&,]*?\b(\w+)\s*\(", line)
        if start and not line.rstrip().endswith(";"):
            end = index
            while end < len(lines) and lines[end] != "{" and not lines[end].rstrip().endswith("{"):
                if lines[end].rstrip().endswith(";"):
                    break
                end += 1
            if end < len(lines) and (lines[end] == "{" or lines[end].rstrip().endswith("{")):
                close = end + 1
                while close < len(lines) and lines[close] != "}":
                    close += 1
                found.append((start.group(1), "\n".join(lines[index:close + 1])))
                index = close + 1
                continue
        index += 1
    return found


def unguarded(text, calls):
    """Functions that make one of ``calls`` without asking ``FAST`` before the first of them."""
    bad = []
    for name, body in functions(text):
        positions = [body.find(call) for call in calls if call in body]
        if not positions:
            continue
        first = min(positions)
        fast = body.find(FAST)
        if fast < 0 or fast > first:
            bad.append(name)
    return bad


class HotPathTests(unittest.TestCase):
    def test_every_hot_hook_asks_the_count_first(self):
        for relative, calls in HOT.items():
            text = (SOURCE / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                hooks = [name for name, body in functions(text) if any(call in body for call in calls)]
                self.assertTrue(hooks, "no hook found: the check reads nothing")
                self.assertEqual(unguarded(text, calls), [], relative)

    def test_the_setters_notes_are_the_hooks_checked(self):
        """The two notes every RNA setter calls are among the functions the first check reads."""
        names = [name for name, _body in functions((SOURCE / "makesrna/intern/rna_access.cc").read_text(
            encoding="utf-8"))]
        self.assertIn("angelo_note_property_set", names)
        self.assertIn("angelo_note_string_set", names)

    def test_confined_now_starts_with_the_count(self):
        text = (SOURCE / "python/intern/bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        body = dict(functions(text))["confined_now"]
        self.assertLess(body.find(FAST), body.find("depth > 0"), body)
        self.assertGreater(body.find(FAST), 0, body)

    def test_the_thread_states_keep_the_count(self):
        header = (SOURCE / "python/intern/bpy_angelo_guard_confine_intern.hh").read_text(encoding="utf-8")
        confine = (SOURCE / "python/intern/bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        # Declared without constinit, defined with it: readers elsewhere use the thread wrapper (macOS bench).
        self.assertIn("extern thread_local ConfineState<int> depth;", header)
        self.assertIn("extern thread_local ConfineState<bool> pending;", header)
        self.assertNotIn("extern thread_local constinit", header)
        self.assertIn("constinit thread_local ConfineState<int> depth;", confine)
        self.assertIn("constinit thread_local ConfineState<bool> pending;", confine)
        self.assertIn("static constinit thread_local ConfineState<int> held;", confine)
        self.assertIsNone(re.search(r"thread_local\s+(int|bool)\s+(depth|pending|held)\b", header + confine))
        self.assertIn("BLI_fileops_confine_open.fetch_add(now ? 1 : -1", header)   # Every assignment syncs.
        # Only the thread states move the states' count: no other holder can cancel a thread's own state
        # (BREAKIT script-isolation-review-fable-2 A2-MUST-1).
        self.assertEqual(re.findall(r"BLI_fileops_confine_open\.fetch_(?:add|sub)", confine), [], confine[:0])
        arm = confine[confine.index("void BPY_angelo_guard_arm()"):]
        self.assertNotIn("fetch_add", arm[:arm.index("\n}") + 2])   # A driver's code is counted while it exists.

    def test_the_count_is_three_parts_that_never_offset(self):
        """States, live agent code and "ask always" are kept apart, and ``possible`` reads all three: an error in one
        holder (a code given back twice) can't bring another holder's nonzero to zero (A2-MUST-1)."""
        header = (ROOT / "source/blender/blenlib/BLI_fileops.hh").read_text(encoding="utf-8")
        possible = header[header.index("inline bool BLI_fileops_confine_possible()"):]
        possible = possible[:possible.index("\n}") + 2]
        for part in ("BLI_fileops_confine_open.load", "BLI_fileops_confine_codes.load",
                     "BLI_fileops_confine_always.load"):
            self.assertIn(part, possible)
        self.assertNotIn("-", possible.replace("->", ""))
        confine = (SOURCE / "python/intern/bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        bodies = dict(functions(confine))
        give = bodies["codes_give_back"]   # Never below zero: an extra give-back makes every path ask for good.
        self.assertIn("<= 0", give)
        self.assertIn("confine_ask_always()", give)
        # Every writer of the codes part is one of the two (NIT-4: by place, not by a count of call sites).
        writers = [name for name, body in functions(confine)
                   if re.search(r"BLI_fileops_confine_codes\.fetch_(?:add|sub)", body)]
        self.assertEqual(sorted(writers), ["codes_add", "codes_give_back"])

    def test_confined_now_reads_only_counted_states(self):
        """After the count, ``confined_now`` decides from the thread states and the stack test only."""
        text = (SOURCE / "python/intern/bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        body = dict(functions(text))["confined_now"]
        body = body[body.index(FAST) + len(FAST):]
        names = set(re.findall(r"\b[a-z_][a-z_0-9]*\b", re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)))
        decided = names - {"if", "return", "true", "false", "const", "bool", "load", "std", "memory_order_relaxed",
                           "python_here", "nullptr"}
        self.assertEqual(decided, {"depth", "pending", "held", "armed", "frames_confined"}, body)

    def test_every_source_of_agent_code_keeps_the_count(self):
        text = (SOURCE / "python/intern/bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        bodies = dict(functions(text))
        code = re.sub(r"/\*.*?\*/", "", bodies["agent_code"], flags=re.S)
        sources = set(re.findall(r"\b(MARK|DRIVER|tainted_names|counted_codes)\b", code))
        self.assertEqual(sources, {"MARK", "DRIVER", "tainted_names", "counted_codes"})
        # Only those: no other table or flag makes code the agent's without the check below seeing it. Agent code by
        # identity is the one set the watcher releases on DESTROY (BREAKIT break-it-confine-count-14a2d702b19
        # MUST-1, SHOULD-2: no separate weakly held taint table).
        others = set(re.findall(r"\b[a-z_][a-z_0-9]*\b", code)) - {
            "bool", "agent_code", "pycodeobject", "code", "pyobject", "name", "co_filename", "name_is", "if",
            "return", "true", "false", "nullptr", "const", "mark", "driver", "tainted_names", "counted_codes",
            "count", "identity_incomplete", "load", "std", "memory_order_relaxed", "trusted_name"}
        self.assertEqual(others, set(), code)
        frames = bodies["frames_confined"]
        self.assertIn("agent_code(code)", frames)
        # Named code, while it lives, counted by identity: the watcher reads the name only when code is made, and
        # gives back only what the set holds (a renamed code object can't take back what it never added).
        watch = bodies["agent_code_watch"]
        create = watch[watch.index("PY_CODE_EVENT_CREATE"):watch.index("else")]
        self.assertIn("named_agent(code)", create)
        self.assertIn("born_confined(code)", create)   # Taint at birth (break-it-confine-depth-5dc512c07e1 MUST-A).
        self.assertIn("code_count(code)", create)
        born = bodies["born_confined"]
        for part in ("confined_now()", "installed_bytes_verified", "loading_frozen > 0",
                     "installed_import_globals() != nullptr", "PyErr_GetRaisedException()",
                     "PyErr_SetRaisedException(held)"):
            self.assertIn(part, born)
        self.assertNotIn("named_agent", watch[watch.index("else"):])
        self.assertIn("code_uncount(code)", watch[watch.index("else"):])
        named = bodies["named_agent"]
        for part in ("name_is(code->co_filename, MARK)", "name_is(code->co_filename, DRIVER)"):
            self.assertIn(part, named)
        self.assertIn("counted_codes->insert(code).second", bodies["code_count"])
        self.assertIn("counted_codes->erase(code) != 0", bodies["code_uncount"])
        self.assertIn("PyCode_AddWatcher(agent_code_watch)", bodies["agent_code_watch_install"])
        self.assertIn("agent_code_watch_install()", bodies["BPY_angelo_guard_confine_add"])
        # Taint: the whole tree of nested code, however deep, into the same set (counted, released on DESTROY).
        self.assertIn("walk_code_tree(code, [](PyCodeObject *item) { return code_count(item); });", bodies["taint"])
        walk = re.sub(r"/\*.*?\*/", "", bodies["walk_code_tree"], flags=re.S)
        self.assertIsNone(re.search(r"\b(level|depth)\b|> *\d", walk), walk)   # No bound that lets code through.
        self.assertIn("stack.push_back(constant)", walk)
        self.assertIn("confine_ask_always()", walk)   # Only an allocation failure stops it, and then all paths ask.
        for part in ("PyTuple_Check(item)", "PyFrozenSet_Check(item)", "identity_incomplete.store(true)"):
            self.assertIn(part, walk)   # Code inside tuple and frozenset constants too (MUST-A (b)).
        # A tree that can't be walked to the end is refused, never run (SHOULD-1 of the same review), and from then
        # on code from no trusted file is agent code.
        self.assertIn("identity_incomplete.load(std::memory_order_relaxed) && !trusted_name(name)", code)
        hook = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine_hook.cc").read_text(
            encoding="utf-8")))["on_exec"]
        self.assertIn('if (!taint(code)) {', hook)
        self.assertIn('return refuse(GUARD, "exec");', hook)
        io = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine_wrap_io.cc").read_text(
            encoding="utf-8")))
        self.assertIn("if (!taint(code)) {", io["wrapped_compile"])
        fix = io["wrapped_fix_co_filename"]
        self.assertIn("if (mixed_names(code)) {", fix)   # Nested code under another name: refused.
        self.assertIn("agent_code((PyCodeObject *)code) ? taint(code) : count_agent_code_tree(code)", fix)
        # The exemption is decided by the bytes (break-it-confine-fixes-b31e34840ca MUST-1): provenance sets the mark
        # only where it proved an installed file's own content, and every audit event clears it.
        provenance = (SOURCE / "python/intern/bpy_angelo_guard_confine_provenance.cc").read_text(encoding="utf-8")
        self.assertEqual(provenance.count("installed_bytes_verified = true;"), 3, provenance[:0])
        hook_body = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine_hook.cc").read_text(
            encoding="utf-8")))["hook"]
        self.assertLess(hook_body.index("installed_bytes_verified = false;"), hook_body.index('strcmp(event, "exec")'))
        # Cleared at the thread's next event, read only while the hook has per-event work (Ruling 17: one relaxed load
        # for the usual event; the coordinator's (Q)): every assignment keeps its share of the word.
        work = hook_body.index("if (hook_event_work.load(std::memory_order_relaxed) != 0) {")
        self.assertLess(work, hook_body.index("if (installed_bytes_verified) {"))
        self.assertLess(hook_body.index("if (installed_bytes_verified) {"),
                        hook_body.index("installed_bytes_verified = false;"))
        header = (SOURCE / "python/intern/bpy_angelo_guard_confine_intern.hh").read_text(encoding="utf-8")
        self.assertIn("extern thread_local CountedFlag installed_bytes_verified;", header)
        self.assertIn("extern std::atomic<int> hook_event_work;", header)
        self.assertNotIn("thread_local bool installed_bytes_verified", header + text)
        self.assertNotIn("installed_bytes_verified_threads", header + text + hook_body)
        self.assertIn("constinit thread_local CountedFlag installed_bytes_verified;", text)
        self.assertIn("hook_event_work.fetch_add(value ? 1 : -1", text)
        # Strict's share: added before the flag turns on, taken off after it turns off (nonzero whenever it is on).
        strict_c = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_strict.cc").read_text(
            encoding="utf-8")))["store_strict"]
        self.assertLess(strict_c.index("hook_event_work.fetch_add(HOOK_WORK_STRICT"),
                        strict_c.index("strict.store(on, std::memory_order_relaxed);"))
        self.assertLess(strict_c.index("strict.store(on, std::memory_order_relaxed);"),
                        strict_c.index("hook_event_work.fetch_sub(HOOK_WORK_STRICT"))
        # The mark covers the compile it verified and ends when that compile returns (review-confine-fixes N-1).
        compile_body = io["wrapped_compile"]
        self.assertLess(compile_body.index("PyObject_Call(original_compile"),
                        compile_body.index("installed_bytes_verified = false;"))
        self.assertLess(compile_body.index("installed_bytes_verified = false;"), compile_body.index("taint(code)"))
        for path in sorted((SOURCE / "python/intern").glob("*.cc")):
            self.assertNotIn("import_loading_trusted_file", path.read_text(encoding="utf-8"), path.name)
        run = (SOURCE / "python/intern/bpy_angelo_guard_confine_run.cc").read_text(encoding="utf-8")
        self.assertLess(run.index("if (!taint(code)) {"), run.index("armed.store(true);\n  journal_watch_loaded();"))
        # A tainted name asks for good, before the name is in the set (code made under it earlier is agent code).
        name = bodies["taint_name"]
        self.assertLess(name.index("confine_ask_always()"), name.index("PySet_Add(tainted_names"))
        # And no other place adds to the tables.
        self.assertEqual(text.count("PySet_Add(tainted_names"), 1)
        self.assertEqual(text.count("counted_codes->insert("), 1)   # Only code_count adds to the set.
        python = SOURCE / "python/intern"
        for other in sorted(python.glob("bpy_angelo_guard*.cc")):
            body = other.read_text(encoding="utf-8")
            with self.subTest(file=other.name):
                self.assertNotIn("tainted_codes", body)
                if other.name != "bpy_angelo_guard_confine.cc":
                    self.assertNotIn("tainted_names", body)
                    self.assertNotIn("counted_codes", body)

    def test_a_sub_interpreter_makes_every_path_ask(self):
        """The watcher and the set are the main interpreter's: every path asks always once any other interpreter
        exists (BREAKIT break-it-confine-count-14a2d702b19 SHOULD-3): with Strict on, by any event raised in another
        interpreter (one call each); always, by CPython's event for a new one and by any import of the `_interpreters` family, whose
        `create()` raises no event (the perf re-read of 6a573ad, MUST-1; proven in the app by
        test_blender_confinement_subinterpreter)."""
        hook = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine_hook.cc").read_text(
            encoding="utf-8")))["hook"]
        body = hook[hook.index("{") + 1:]
        first = body[:body.index("return")]
        # The per-event compare is Strict's (Ruling 17: measurable on macOS): off, it is not on the hot path; and it
        # sits behind the one per-event load.
        self.assertLess(first.index("if (hook_event_work.load(std::memory_order_relaxed) != 0) {"),
                        first.index("if (angelo_strict_script_protection()) {"))
        strict = first.index("if (angelo_strict_script_protection()) {")
        self.assertLess(strict, first.index("PyInterpreterState_Get()"))
        self.assertLess(first.index("if (interpreter != main) {"), first.index("installed_bytes_verified"))
        self.assertEqual(first.count("PyInterpreterState_Get()"), 1)
        self.assertNotIn("PyInterpreterState_Get()", first[:strict])
        self.assertIn('strcmp(event, "cpython.PyInterpreterState_New") == 0', first)
        self.assertIn('strcmp(event, "import") == 0 && interpreters_module(args)', first)
        self.assertEqual(first.count("confine_ask_always()"), 3, first)
        self.assertEqual(body.count("PyInterpreterState_Main()"), 1, "Main() once, cached (one call per event)")
        install = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine.cc").read_text(
            encoding="utf-8")))["BPY_angelo_guard_confine_install"]
        self.assertIn("PySys_AddAuditHook(hook, nullptr)", install)   # Before Python starts: no interpreter unseen.
        manifest = (Path(__file__).resolve().parent / "native_suite_manifest.py").read_text(encoding="utf-8")
        self.assertIn('Suite("test_blender_confinement_subinterpreter.py", BACKGROUND,', manifest)

    def test_only_the_interpreter_family_turns_always_on(self):
        """The family by full name, last component or file (`module_is_one_of`, native.hh): a third-party
        ``_interpolate`` keeps the hot paths' counts (NIT-1 of c1f066be23f's re-read), an alias ``x._interpreters``
        made by ``_imp`` in any context does not (MUST-1 of f3b1e5f's re-read). The same matcher refuses an aliased
        native-file module (``x._dbm``) while confined, at the ``import`` event and at creation."""
        native = (SOURCE / "python/intern/bpy_angelo_guard_confine_native.hh").read_text(encoding="utf-8")
        table = native[native.index("INTERPRETER_MODULES[] = {"):]
        names = re.findall(r'"([^"]+)"', table[:table.index("};")])
        self.assertEqual(set(names), {"_interpreters", "_interpchannels", "_interpqueues", "_xxsubinterpreters",
                                      "_xxinterpchannels", "concurrent.interpreters"})
        matcher = native[native.index("inline bool module_is_one_of("):]
        for part in ("name == module", "name[module.size()] == '.'",
                     "extension && module.find('.') == std::string_view::npos", "last == module", "stem == module"):
            self.assertIn(part, matcher)
        ext = native[native.index("inline bool extension_origin("):]
        self.assertIn('origin == "built-in" || ends(".so") || ends(".pyd")', ext[:ext.index("\n}\n")])

        def matches(name, origin="", extension=True, table=names):   # The C rule, applied to the C list.
            last = name.rsplit(".", 1)[-1]
            stem = re.split(r"[/\\]", origin)[-1].split(".")[0] if origin else ""
            return any(name == entry or name.startswith(entry + ".") or
                       (extension and "." not in entry and (last == entry or (stem and stem == entry)))
                       for entry in table)
        for name, origin in (("_interpreters", ""), ("_interpqueues", ""), ("concurrent.interpreters._crossinterp", ""),
                             ("x._interpreters", ""), ("a.b._xxsubinterpreters", ""),
                             ("anything", "/lib/python3.13/lib-dynload/_interpreters.cpython-313-darwin.so"),
                             ("anything", "C:\\Angelo\\5.3\\python\\DLLs\\_interpchannels.pyd")):
            self.assertTrue(matches(name, origin), (name, origin))
        for name, origin in (("_interpolate", ""), ("_interpretersx", ""), ("_interp", ""), ("x.interpreters", ""),
                             ("concurrent.futures", ""), ("numpy", "/site-packages/numpy/__init__.py")):
            self.assertFalse(matches(name, origin), (name, origin))
        # The re-read of 502fd7f, NIT-1: a pure-Python module is matched by its full name only, so the bundled
        # `_pyrepl.readline` (a .py, or no file at the import event) is not refused, while an extension made under
        # another name still is.
        natives = re.findall(r'"([^"]+)"', native[native.index("NATIVE_FILE_MODULES[] = {"):].split("};")[0])
        self.assertIn("readline", natives)
        for origin in ("/lib/python3.13/_pyrepl/readline.py", ""):
            self.assertFalse(matches("_pyrepl.readline", origin, extension=origin.endswith((".so", ".pyd")),
                                     table=natives), origin)
        self.assertTrue(matches("x._lsprof", "/lib/python3.13/lib-dynload/_lsprof.cpython-313-darwin.so",
                                extension=True, table=natives))
        self.assertTrue(matches("x._lsprof", "", extension=True, table=natives))   # Made by _imp: an extension.
        self.assertTrue(matches("readline", "", extension=False, table=natives))   # Full name: always.
        hook = (SOURCE / "python/intern/bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        bodies = dict(functions(hook))
        self.assertIn("module_is_one_of(name, file, extension_origin(file), INTERPRETER_MODULES)",
                      bodies["interpreters_module"])
        self.assertIn("module_is_one_of(name, file, extension_origin(file), NATIVE_FILE_MODULES)",
                      bodies["native_file_module"])
        self.assertNotIn('"_interp", 7', hook)
        wrap = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine_wrap.cc").read_text(encoding="utf-8")))
        guarded = wrap["guarded_imp"]
        unconfined = guarded[:guarded.index("std::string text;")]
        self.assertIn("INTERPRETER_MODULES)) {\n        confine_ask_always();", unconfined)   # Any context.
        self.assertIn("module_is_one_of(text, origin, true, INTERPRETER_MODULES)", guarded)
        self.assertIn("const bool native = module_is_one_of(text, origin, true, NATIVE_FILE_MODULES);", guarded)
        install = dict(functions((SOURCE / "python/intern/bpy_angelo_guard_confine_pipeline_install.cc").read_text(
            encoding="utf-8")))["hooked"]
        creation = install[install.index("if (create) {"):install.index('name.rfind("pxr.", 0) == 0')]
        self.assertIn("module_is_one_of(name, origin_text, true, INTERPRETER_MODULES)", creation)
        self.assertIn("confine_ask_always();", creation)
        self.assertNotIn("confined_now()", creation)   # Any context.

    def test_the_hook_dispatches_on_the_first_character(self):
        """The coordinator's (Q): every event name the hook compares before its table is guarded by its first
        character (the full list pinned), and an event whose first character starts no name in `events()` or
        `FAMILY_PREFIXES` returns before the table is hashed; the filter is built from those same lists."""
        hook_source = (SOURCE / "python/intern/bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        bodies = dict(functions(hook_source))
        body = bodies["hook"]
        before_table = body[:body.index("const auto &table = events();")]
        compared = re.findall(r'str(?:n)?cmp\(event, "([^"]+)"', before_table)
        self.assertEqual(sorted(set(compared)), sorted({"cpython.PyInterpreterState_New", "import", "exec",
                                                        "sys.unraisablehook", "sys.excepthook", "compile",
                                                        "code.__new__", "marshal.load"}))
        for name in set(compared):
            with self.subTest(name=name):
                guard = "event[0] == '%s' &&" % name[0]
                index = before_table.index('"%s"' % name)
                self.assertIn(guard, before_table[max(0, before_table.rfind("if (", 0, index) - 2):index])
        self.assertLess(body.index("if (!handled_first_char(event)) {"), body.index("const auto &table = events();"))
        handled = bodies["handled_first_char"]
        self.assertIn("for (const auto &item : events())", handled)
        self.assertIn("for (const auto &item : FAMILY_PREFIXES)", handled)
        self.assertIn("for (const auto &[prefix, kind] : FAMILY_PREFIXES)", bodies["family"])

    def test_every_renamer_of_live_code_keeps_the_count(self):
        """Whatever can change what ``agent_code`` answers for a code object that already exists keeps the count
        (A2-SHOULD-2): today that is only ``_imp._fix_co_filename`` (``co_filename`` is read-only elsewhere), whose
        wrapper counts the renamed tree after the original call, and nothing else calls the original."""
        python = SOURCE / "python/intern"
        wrap = (python / "bpy_angelo_guard_confine_wrap_io.cc").read_text(encoding="utf-8")
        body = dict(functions(wrap))["wrapped_fix_co_filename"]
        self.assertLess(body.index("PyObject_Call(original_fix_co_filename"), body.index("count_agent_code_tree("))
        callers = [f.name for f in sorted(python.glob("*.cc"))
                   if "PyObject_Call(original_fix_co_filename" in f.read_text(encoding="utf-8")]
        self.assertEqual(callers, ["bpy_angelo_guard_confine_wrap_io.cc"])
        writes = [f.name for f in sorted(python.glob("*.cc"))
                  if re.search(r"->co_filename\s*=[^=]", f.read_text(encoding="utf-8"))]
        self.assertEqual(writes, [])
        confine = (python / "bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        tree = dict(functions(confine))["count_agent_code_tree"]
        self.assertIn("walk_code_tree(code,", tree)
        for part in ("named_agent(", "code_count("):
            self.assertIn(part, tree)

    def test_the_reader_finds_an_unguarded_hook(self):
        text = ("static void note(int *p)\n{\n  const BLI_FileGate *gate = BLI_fileops_gate_get();\n}\n"
                "static void fine(int *p)\n{\n  if (!" + FAST + ") {\n    return;\n  }\n"
                "  const BLI_FileGate *gate = BLI_fileops_gate_get();\n}\n")
        self.assertEqual(unguarded(text, ("BLI_fileops_gate_get()",)), ["note"])


if __name__ == "__main__":
    unittest.main()
