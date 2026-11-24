# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stat-family follow-up (break-it-confine-reparse-b7d499c, "M7 read 1a17d20c22c"): what the Blender suite proves
by running (confinement_lookup_cases), pinned in the sources so a change that undoes it fails here on every system.

- MUST-2: no confined thread makes a second module object of one whose functions Angelo replaced (it would carry the
  originals), whether or not the first is in ``sys.modules``, nor the same extension under another name; the list of
  modules is noted by every replacement, never written by hand; and the spec is read once, into Angelo's own.
- MUST-1: ``os.mkfifo``, ``os.mknod`` and ``os.chroot`` raise no audit event in CPython: their wrappers raise Angelo's,
  which the hook judges (a write with its folder descriptor; chroot refused).
- SHOULD: Blender's C lookups (every exported ``BLI_`` function taking a path whose body, or a static helper of its
  file, looks a name up) ask the file gate before the system; POSIX DirEntry calls ask the network check whatever the
  entry; the POSIX check's resolved folders are kept per generation, which a region's start and every change that can
  put a link where a folder was begin anew.
Reads sources; dependency-free.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INTERN = ROOT / "source" / "blender" / "python" / "intern"
BLENLIB = ROOT / "source" / "blender" / "blenlib" / "intern"


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", lambda match: re.sub(r"[^\n]", " ", match.group()), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def functions(text):
    """``{name: [(parameters, body, static)]}`` of every top-level function definition in C/C++/Objective-C++ text."""
    found = {}
    for match in re.finditer(r"^(?!\s)([^\n;()]*)\b(\w+)\s*\(([^)]*)\)\s*(?:const\s*)?\n?\{", text, re.M):
        at = text.index("{", match.end() - 1)
        depth, end = 0, at
        while end < len(text):
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            if depth == 0:
                break
            end += 1
        found.setdefault(match.group(2), []).append((match.group(3), text[at:end + 1],
                                                     match.group(1).lstrip().startswith("static")))
    return found


def body(name, function, folder=INTERN):
    definitions = functions(strip_comments((folder / name).read_text(encoding="utf-8", errors="replace")))
    if function not in definitions:
        raise AssertionError("%s: no definition of %s" % (name, function))
    return definitions[function][0][1]


def guard_sources():
    return sorted(path for path in INTERN.glob("bpy_angelo_guard_confine*.cc") if not path.stem.endswith("_test"))


# What looks a name up on the system: the stat family, access, free space, attributes, a folder opened for listing, a
# Windows handle opened by name, an alias or shortcut resolved. Opens for reading or writing are the file gate's
# `gate_refuses` (judged as reads and writes), not this check.
LOOKUP = re.compile(r"\b(stat|lstat|stat64|_wstat64|_wstat|BLI_wstat|access|uaccess|statfs|statvfs|"
                    r"GetFileAttributes\w*|GetDiskFreeSpace\w*|CreateFileW|getxattr|listxattr|opendir|_wopendir|"
                    r"realpath|readlink|FindFirstFileW|PathFileExistsW)\s*\("
                    r"|initFileURLWithFileSystemRepresentation:|URLByResolvingAliasFileAtURL:|->Load\(")
GATE = "BLI_fileops_gate_refuses_lookup("
# Lookups in exported functions not gated themselves, with the reason.
UNGATED = {
    "BLI_wstat": "reached through BLI_stat and BLI_file_stat_mode, which ask the gate first, and the confinement's own "
                 "opener, which checks the wide name it hands the system",
    "BLI_open_replacing": "a write, asked of the file gate's write judge (gate_refuses) before the file is made",
    "BLI_windows_update_pinned_launcher": "the person's own registration from Preferences, never a script's",
    "BLI_delete": "a write (gate_refuses)", "BLI_path_move": "a write (gate_refuses)",
    "BLI_copy": "a write (gate_refuses)",
}


class StatFollowUp(unittest.TestCase):
    def test_a_wrapped_module_is_never_made_again(self):
        """MUST-2: the check reads the steady spec and refuses a wrapped module before the original runs; the call
        made is given that spec, never the script's."""
        guarded = body("bpy_angelo_guard_confine_wrap.cc", "guarded_imp")
        steady = guarded.index("steady_spec(")
        self.assertLess(steady, guarded.index("names_a_wrapped_module("))
        self.assertLess(guarded.index("names_a_wrapped_module("), guarded.index("call_original(which, steady_args"))
        self.assertIn("which == CREATE_DYNAMIC", guarded)   # An extension under another name.
        # The only call with the script's own arguments is the unconfined one, before the spec is read.
        self.assertEqual([m.start() < steady for m in re.finditer(r"call_original\(which, args", guarded)], [True])
        replace = body("bpy_angelo_guard_confine_wrap.cc", "replace_references")
        self.assertTrue(replace.lstrip("{ \n").startswith("note_wrapped_owner(original);"), replace)
        owners = (INTERN / "bpy_angelo_guard_confine_wrap_owners.cc").read_text(encoding="utf-8")
        self.assertIn("PyCFunction_GET_SELF(original)", owners)
        self.assertIn("PyType_GetModule((PyTypeObject *)holder)", owners)   # A heap type's defining module.
        # Each install site notes the module it imported (head 14 MUST-2: `_sqlite3.Connection.__module__` is
        # `sqlite3`, a package of Python source).
        put = body("bpy_angelo_guard_confine_wrap_io.cc", "put_in")
        self.assertTrue(put.lstrip("{ \n").startswith("note_install_site(module_name);"), put)
        self.assertIn('note_install_site("_sqlite3");', body("bpy_angelo_guard_confine_wrap_native.cc", "seal_sqlite"))
        self.assertIn("note_install_site(module_name);", body("bpy_angelo_guard_confine_wrap.cc", "install_wrappers"))
        # Angelo's own Python replacing a class in a C module notes it too (the suite's surface check found it).
        blender = (ROOT / "scripts" / "addons_core" / "angelo_agent" / "script_confinement_blender.py").read_text(
            encoding="utf-8")
        self.assertIn('guard.note_replaced_module("_multiprocessing")', blender)
        # The outermost `_imp` wrapper (the pipeline hook) reads the spec once and passes Angelo's own inward
        # (head 14 SHOULD-1).
        hooked = body("bpy_angelo_guard_confine_pipeline_install.cc", "hooked")
        self.assertLess(hooked.index("steady_spec(first, name)"), hooked.index('name.rfind("pxr.", 0) == 0'))
        self.assertIn("PyObject_Call(hook_originals[which], call_args, kwargs)", hooked)
        self.assertNotIn("PyObject_Call(hook_originals[which], args", hooked)
        self.assertIn('"__objclass__"', owners)
        self.assertIn("init_names.count(last_component(name))", owners)

    def test_every_type_changed_in_place_notes_its_module(self):
        """A method or slot replaced in a type's own dict (no `replace_references`) notes the type's module too: the
        function making each such write in the guard calls `note_wrapped_owner`. Angelo's own types and the type of
        every type are not a module's functions."""
        own = {("bpy_angelo_guard_confine_attr.cc", "tp_"), ("bpy_angelo_guard_confine_work.cc", "tp_new"),
               ("bpy_angelo_guard_confine_types.cc", "tp_new")}
        missing = []
        for path in guard_sources():
            text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            dicts = set(re.findall(r"\b(\w+)\s*=\s*[^;]*->tp_dict\b", text))
            bodies = [body for versions in functions(text).values() for _, body, _ in versions]
            for match in re.finditer(r"->(tp_init|tp_new|tp_call)\s*=(?!=)|PyDict_SetItemString\(\s*([^,]+),", text):
                slot = match.group(1)
                target = match.group(2)
                if target is not None and "tp_dict" not in target and target.strip() not in dicts:
                    continue
                if slot and any(path.name == name and slot.startswith(kind) for name, kind in own):
                    continue
                line = text[text.rfind("\n", 0, match.start()) + 1:text.find("\n", match.start())]
                enclosing = next((body for body in bodies if line in body and match.group(0) in body), "")
                if "note_wrapped_owner(" not in enclosing and "note_install_site(" not in enclosing:
                    missing.append("%s:%d %s" % (path.name, text.count("\n", 0, match.start()) + 1, line.strip()))
        self.assertEqual(missing, [])

    def test_changes_without_an_event_raise_angelos(self):
        """MUST-1: each wrapped change raises its own event before the original, and the hook's table judges each
        (a FIFO or node made: a write with `dir_fd` at index 1; chroot: the process, refused)."""
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        events = {name: (module, event) for module, name, event in
                  re.findall(r'\{"(\w+)", "(\w+)", "(angelo\.[\w.]+)", nullptr\}', lookup)}
        self.assertEqual(events, {"mkfifo": ("posix", "angelo.os.mkfifo"), "mknod": ("posix", "angelo.os.mknod"),
                                  "chroot": ("posix", "angelo.os.chroot")})
        call = body("bpy_angelo_guard_confine_wrap_lookup.cc", "audit_call")
        self.assertLess(call.index("PySys_Audit(wrap.event"), call.index("PyObject_Call(wrap.original"))
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertIn('{"angelo.os.mkfifo", {Kind::File, {{0, \'m\', 1}}}}', hook)
        self.assertIn('{"angelo.os.mknod", {Kind::File, {{0, \'m\', 1}}}}', hook)
        self.assertIn('{"angelo.os.chroot", {Kind::ProcessState}}', hook)

    def test_windows_name_lookups_judge_names_and_pipes(self):
        """head-12 M3/M4/M5: long and short names and `_findfirstfile` are judged as reads of their folder (a private
        folder's names stay private) with `*`/`?` refused; a pipe waited for must be this machine's, before the call."""
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        for module, name in (("_winapi", "GetLongPathName"), ("_winapi", "GetShortPathName"), ("nt", "_findfirstfile")):
            self.assertIn('{{"%s", nullptr}, "%s", true, false, false, nullptr, true}' % (module, name), lookup)
        self.assertIn('{{"_winapi", nullptr}, "WaitNamedPipe", true, false, false, nullptr, false, true}', lookup)
        call = body("bpy_angelo_guard_confine_wrap_lookup.cc", "lookup_call")
        original = call.index("PyObject_Call(wrap.original")
        self.assertLess(call.index("!local_pipe_name(path)"), original)
        self.assertLess(call.index('find_first_of("*?")'), original)
        self.assertLess(call.index("judge_entry_path(value, false)"), call.rindex("PyObject_Call(wrap.original"))
        pipe = body("bpy_angelo_guard_confine_wrap_lookup.cc", "local_pipe_name")
        self.assertIn('rest.find("..") == std::string::npos', pipe)

    def test_descriptors_by_number_are_judged_or_refused(self):
        """head-12 private-file class: a descriptor's copy, space, replacement, close, position, flags or lock judges
        its file (wrap_io.cc); a range closed, a new session, a terminal's group and a namespace are refused."""
        io = (INTERN / "bpy_angelo_guard_confine_wrap_io.cc").read_text(encoding="utf-8")
        for row in ('{{"posix", nullptr}, "_fcopyfile", {0, -1}, {1, -1}, nullptr}',
                    '{{"posix", nullptr}, "posix_fallocate", {-1, -1}, {0, -1}, nullptr}',
                    '{{"posix", "nt"}, "dup2", {0, -1}, {-1, -1}, nullptr, {1, -1}, true}',
                    '{{"posix", "nt"}, "close", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true}',
                    '{{"posix", "nt"}, "lseek", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true}',
                    '{{"posix", nullptr}, "lockf", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true}'):
            self.assertIn(row, io)
        handles = dict(re.findall(r'\{"(?:_winapi|_overlapped)", "(\w+)", \{(-?\d, -?\d)\}, (?:true|false), nullptr\}', io))
        self.assertEqual(handles, {"ReadFile": "0, -1", "WriteFile": "0, -1", "CloseHandle": "0, -1",
                                   "DuplicateHandle": "1, -1", "PeekNamedPipe": "0, -1", "ConnectNamedPipe": "0, -1",
                                   "SetNamedPipeHandleState": "0, -1", "SetEvent": "0, -1", "ResetEvent": "0, -1",
                                   "ReleaseMutex": "0, -1", "CreateIoCompletionPort": "0, 1",
                                   "GetQueuedCompletionStatus": "0, -1", "PostQueuedCompletionStatus": "0, -1",
                                   "RegisterWaitWithQueue": "0, 1", "UnregisterWait": "0, -1",
                                   "UnregisterWaitEx": "0, 1"})
        # A row may end with `, true`: refused while confined as the network (36bcdd2 read MUST-3).
        overlapped = dict(re.findall(r'\{"(\w+)", \{(\d, -?\d)\}, (?:true|false), nullptr(?:, true)?\}', io))
        self.assertEqual(set(overlapped), {"ReadFile", "ReadFileInto", "WSARecv", "WSARecvInto", "WSARecvFrom",
                                           "WSARecvFromInto", "WriteFile", "WSASend", "WSASendTo", "AcceptEx",
                                           "ConnectEx", "DisconnectEx", "TransmitFile", "ConnectNamedPipe"})
        self.assertEqual((overlapped["AcceptEx"], overlapped["TransmitFile"]), ("0, 1", "0, 1"))
        call = body("bpy_angelo_guard_confine_wrap_io.cc", "overlapped_call")
        self.assertLess(call.index("judge_handle("), call.index("PyObject_Call(wrap.original"))
        # msvcrt.open_osfhandle is judged like any handle, never refused whole (build3: multiprocessing's spawn makes
        # its own pipe a descriptor before its start, which must then be refused as a program).
        self.assertIn('{"msvcrt", "open_osfhandle", {0, -1}, false, nullptr}', io)
        hook_rows = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertNotIn('{"msvcrt.open_osfhandle", {Kind::Guard}}', hook_rows)
        handle = body("bpy_angelo_guard_confine_wrap_io.cc", "handle_call")
        # Past the Strict-off pass-through (R-4): the handle is judged before the call that uses it.
        self.assertLess(handle.index("judge_handle("), handle.rindex("PyObject_Call(wrap.original"))
        self.assertIn('return refuse(PRIVATE, "a file handle whose file can\'t be told")',
                      body("bpy_angelo_guard_confine_wrap_io.cc", "judge_handle"))
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        refused = dict(re.findall(r'\{"(\w+)", "[^"]*", (true|false), nullptr\}', lookup))
        self.assertEqual(refused, {"closerange": "false", "login_tty": "true", "tcsetpgrp": "true", "setns": "true"})
        call = body("bpy_angelo_guard_confine_wrap_lookup.cc", "refused_call")
        self.assertLess(call.index("refuse("), call.index("PyObject_Call(wrap.original"))
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        for row in ('{"os.lockf", {Kind::File, {{0, \'f\', -1}}}}',
                    '{"msvcrt.get_osfhandle", {Kind::File, {{0, \'f\', -1}}}}',
                    '{"pathlib.Path.glob", {Kind::File, {{0, \'r\', -1}}}}',
                    '{"pathlib.Path.rglob", {Kind::File, {{0, \'r\', -1}}}}',
                    '{"pathlib.Path.walk", {Kind::File, {{0, \'r\', -1}}}}'):
            self.assertIn(row, hook)

    def test_blenders_c_lookups_ask_the_gate(self):
        """Every exported `BLI_` function taking a path that looks a name up (in its body, or through a static
        helper of its file) asks the gate before the first lookup, or is listed with its reason."""
        unasked = []
        for path in sorted(list(BLENLIB.glob("*.cc")) + list(BLENLIB.glob("*.mm"))):
            definitions = functions(strip_comments(path.read_text(encoding="utf-8", errors="replace")))

            def looks_up(name, seen):
                if name in seen or name not in definitions:
                    return None
                seen.add(name)
                for _, text, _ in definitions[name]:
                    if LOOKUP.search(text):
                        return True
                    for called in re.findall(r"\b(\w+)\s*\(", text):
                        if called in definitions and all(s for _, _, s in definitions[called]) and \
                                looks_up(called, seen):
                            return True
                return None

            for name, versions in definitions.items():
                if not name.startswith("BLI_") or name in UNGATED:
                    continue
                for parameters, text, _ in versions:
                    if not re.search(r"\b(char|wchar_t)\s*\*", parameters):
                        continue
                    direct = LOOKUP.search(text)
                    helper = next((m for m in re.finditer(r"\b(\w+)\s*\(", text)
                                   if m.group(1) in definitions and not m.group(1).startswith("BLI_") and
                                   looks_up(m.group(1), {name})), None)
                    first = min([m.start() for m in (direct, helper) if m is not None], default=None)
                    if first is None:
                        continue
                    gate = text.find(GATE)
                    if gate == -1 or gate > first:
                        unasked.append("%s:%s" % (path.name, name))
        self.assertEqual(unasked, [])
        for name in UNGATED:   # Each reason names a function that still exists.
            self.assertTrue(any(re.search(r"\b%s\s*\(" % name, path.read_text(errors="replace"))
                                for path in list(BLENLIB.glob("*.cc")) + list(BLENLIB.glob("*.mm"))), name)

    def test_the_inventory_finds_an_event_the_hook_does_not_judge(self):
        """The inventory's class, on every system: an event a probed function raises (Windows' `_winapi.CopyFile2`,
        which shutil.copy2 raises with no shutil event) or the standard library raises, unknown to the hook (unknown
        events pass), fails it; a function that raises none and is not classified fails it too."""
        import confinement_lookup_cases as cases
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        report = {"_winapi.CopyFile2": {"events": ["_winapi.CopyFile2"]}, "posix.listdir": {"events": ["os.listdir"]},
                  "posix.stat": {"events": []}, "posix.angelo_new": {"events": []},
                  "posix.close": {"events": [], "descriptor": True}}
        without = "\n".join(line for line in hook.splitlines() if '"_winapi.CopyFile2"' not in line)
        unclassified, unjudged = cases.classify(report, ["shutil.copyfile", "angelo.new.event"],
                                                cases.judged_events(without))
        self.assertEqual(unjudged, ["_winapi.CopyFile2: ['_winapi.CopyFile2']", "stdlib angelo.new.event"])
        self.assertEqual(unclassified, ["posix.angelo_new"])
        # A signature that can't be read, with no probe arguments: never called, so unclassified, wrapped or not.
        unreadable = {"nt.startfile": {"events": [], "unreadable": True},
                      "posix.stat": {"events": [], "unreadable": True}}
        self.assertEqual(cases.classify(unreadable, [], None)[0],
                         ["nt.startfile (signature unreadable)", "posix.stat (signature unreadable)"])
        import confinement_audit_probe as probe
        self.assertEqual(probe.PROBE_ARGS["startfile"]("x", "y", 3), ("x",))   # So startfile is called (audited).
        self.assertIn('{"os.startfile", {Kind::Process}}', hook)
        _, unjudged = cases.classify(report, ["shutil.copyfile"], cases.judged_events(hook))
        self.assertEqual(unjudged, [])
        self.assertIn("socket.", cases.judged_events(hook)[1])   # A family refused whole counts as judged.

    def test_every_cpython_event_is_judged_or_names_no_file(self):
        """The table of CPython's own events (generated from the 3.13.13 tarball whose SHA-256 python.org publishes)
        holds the C events no call reaches: each is judged or names no file. An event of CPython's that neither the hook
        nor the reasons name (here fcntl's, judged now) is reported."""
        import confinement_lookup_cases as cases
        import cpython_audit_table as table
        self.assertEqual(table.TARBALL_SHA256, "2ab91ff401783ccca64f75d10c882e957bdfd60e2bf5a72f8421793729b78a71")
        self.assertEqual(table.VERSION, "3.13.13")
        self.assertIn("_winapi.CopyFile2", table.C_EVENTS)
        events, unexplained, _ = cases.cpython_events()
        self.assertEqual(unexplained, [])
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertEqual(cases.classify({}, sorted(events), cases.judged_events(hook))[1], [])
        without = "\n".join(line for line in hook.splitlines() if '"fcntl.' not in line)
        self.assertEqual(cases.classify({}, sorted(events), cases.judged_events(without))[1],
                         ["stdlib fcntl.fcntl", "stdlib fcntl.flock", "stdlib fcntl.ioctl", "stdlib fcntl.lockf"])
        for event in ("fcntl.fcntl", "fcntl.ioctl", "fcntl.flock", "fcntl.lockf"):
            self.assertIn('{"%s", {Kind::File, {{0, \'f\', -1}}}}' % event, hook)

    def test_numbers_are_the_scripts_while_handed_and_unended(self):
        """Head-16 read MUST-1/2: a number with no file is used, and any number closed, replaced, moved or locked, only
        while it is the one a maker handed confined code and nothing ended or passed it on; an object's identity is
        only a second check (macOS reuses a pipe's, Linux shares one among every eventfd); nothing is held open."""
        made = strip_comments((INTERN / "bpy_angelo_guard_confine_made.cc").read_text(encoding="utf-8"))
        self.assertNotIn("DuplicateHandle(", made)   # No duplicate keeps a pipe from its end.
        self.assertNotIn("CompareObjectHandles", made)
        self.assertIn("std::unordered_map<intptr_t, Owned> owned_descriptors;", made)
        owns = body("bpy_angelo_guard_confine_made.cc", "owns")
        self.assertIn("found == numbers.end() || found->second.first != now.first", owns)
        self.assertIn("held_by(holder, number)", owns)
        makers = set(re.findall(r'\{"(\w+)", "(\w+)", (?:DESCRIPTORS|HANDLES), nullptr\}', made))
        for maker in (("posix", "pipe"), ("posix", "open"), ("posix", "dup"), ("posix", "eventfd"), ("nt", "pipe"),
                      ("_winapi", "CreatePipe"), ("_winapi", "CreateNamedPipe"), ("_winapi", "DuplicateHandle"),
                      ("_overlapped", "CreateEvent"), ("msvcrt", "get_osfhandle")):
            self.assertIn(maker, makers)
        close = body("bpy_angelo_guard_confine_made.cc", "fileio_close")
        # Past the nothing-owned fast path (Ruling 17): the number is forgotten before the close that frees it.
        self.assertLess(close.index("forget_descriptor(fd)"), close.rindex("original_fileio_close"))
        io_name, hook_name = "bpy_angelo_guard_confine_wrap_io.cc", "bpy_angelo_guard_confine_hook.cc"
        io = (INTERN / io_name).read_text(encoding="utf-8")
        for row in ('"dup2", {0, -1}, {-1, -1}, nullptr, {1, -1}, true}',
                    '"close", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true}',
                    '"lseek", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true}',
                    '"dup", {0, -1}, {-1, -1}, nullptr, {-1, -1}, true}',
                    '"eventfd_write", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true}',
                    '"eventfd_read", {0, -1}, {-1, -1}'):
            self.assertIn(row, io)
        call = body(io_name, "io_call")
        self.assertLess(call.index("judge_change("), call.rindex("PyObject_Call(wrap.original"))   # Past the R-4 exits.
        self.assertLess(call.index("PyObject_Call(wrap.original"), call.index("forget_descriptor(closed)"))
        self.assertIn('strcmp(wrap.name, "close") == 0 || strcmp(wrap.name, "dup2") == 0', call)
        self.assertIn("forget_handle(closed_handle)", body(io_name, "handle_call"))
        for name, function, made_call in ((io_name, "judge_descriptor", "made_descriptor(fd)"),
                                          (io_name, "judge_handle", "made_handle(handle)"),
                                          (hook_name, "judge_path", "made_descriptor(fd)")):
            whole = strip_comments((INTERN / name).read_text(encoding="utf-8"))
            start = re.search(r"^(?:static )?\w+ \*?%s\(" % function, whole, re.M).start()
            text = whole[start:whole.index("\n}\n", start)]   # The definition, to its closing brace at column 0.
            self.assertLess(text.index(made_call), text.index("the script didn't make"), function)
        self.assertIn("note_made_socket_pair(result);", body(io_name, "wrapped_socketpair"))
        region = (INTERN / "bpy_angelo_guard_confine_region.cc").read_text(encoding="utf-8")
        self.assertEqual(region.count("made_objects_forget();"), 2)
        confine = (INTERN / "bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        self.assertLess(confine.index("install_wrappers_io() == 0"), confine.index("(install_makers(), true)"))
        # fcntl.ioctl: only requests that read (TIOCSTI injects into a terminal).
        ioctl = body(hook_name, "ioctl_refused")
        self.assertIn("value == (unsigned long)FIONREAD || value == (unsigned long)TIOCGWINSZ", ioctl)
        # An original captured in a function's defaults or a class (multiprocessing's Connection) is replaced too.
        walk = body("bpy_angelo_guard_confine_wrap.cc", "walk_references")
        self.assertIn("replace_in_defaults(value);", walk)
        self.assertIn("replace_in_class(value);", walk)

    def test_posix_direntry_calls_ask_the_network_check(self):
        """SHOULD: a POSIX entry's `stat`, `is_dir` and `is_file` can ask the system (no type from the listing, a link,
        a name changed since): each asks the network check; Windows keeps its links-only walk."""
        entry = (INTERN / "bpy_angelo_guard_confine_wrap_lookup_entry.cc").read_text(encoding="utf-8")
        entry = entry[entry.index("PyObject *entry_call("):entry.index("/** \\} */", entry.index("PyObject *entry_call("))]
        posix = entry[entry.index("#else"):entry.index("#endif")]
        self.assertIn("const bool walk = true;", posix)
        self.assertIn("network_lookup(path, dir_fd, follow)", entry)

    def test_a_descriptor_relative_lookup_is_judged_in_its_folder(self):
        """Head 14 MUST-3: a name relative to a folder descriptor (`dir_fd=`, an entry of `os.scandir(fd)`, what
        `os.fwalk` and `glob`'s `dir_fd` ask) is judged as the joined path, not in the working folder; a descriptor
        whose folder can't be told is refused. A bare-name entry finds its descriptor where install found CPython
        keeps it (two listings through known, different descriptors, one matching field); not found, it is refused."""
        lookup = re.sub(r"\s+", " ", body("bpy_angelo_guard_confine_wrap_lookup.cc", "network_lookup"))
        self.assertIn("if (dir_fd >= 0 && path[0] != '/') { const std::optional<std::string> folder = "
                      "descriptor_path(dir_fd); if (!folder) { judged_unreadable = true; looking_up--; "
                      "return Refusal::Private; }", lookup)
        self.assertLess(lookup.index("descriptor_path(dir_fd)"), lookup.index("names_a_remote_file(named)"))
        entry = (INTERN / "bpy_angelo_guard_confine_wrap_lookup_entry.cc").read_text(encoding="utf-8")
        call = re.sub(r"\s+", " ", entry[entry.index("PyObject *entry_call("):])
        self.assertIn("if (path.find('/') == std::string::npos) {", call)
        self.assertIn("dir_fd = entry_dir_fd(entry); if (dir_fd < 0) { judged_unreadable = true; "
                      "refusal = Refusal::Private;", call)
        find = re.sub(r"\s+", " ", entry[entry.index("void find_entry_fd_offset("):entry.index("int entry_dir_fd(")])
        self.assertIn("entry_fd_offset = matches == 1 ? found : -1;", find)
        self.assertIn("in_a == first && in_b == second", find)
        install = entry[entry.index("int install_wrappers_entry()"):]
        self.assertLess(install.index("find_entry_fd_offset(scandir)"), install.index("PyDict_SetItemString("))
        self.assertIn("install_wrappers_entry();", body("bpy_angelo_guard_confine_wrap_lookup.cc",
                                                        "install_wrappers_lookup"))

    def test_resolved_folders_are_per_generation(self):
        """SHOULD (cost): the POSIX check keeps what `resolve` found for absolute folders, only when no network was
        reached, and forgets it at a region's start, at every judged move, removal or link, and at every C move."""
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        self.assertIn("resolve_folder(folder, joined.empty() ? dir_fd : -1)",
                      body("bpy_angelo_guard_confine_wrap_lookup.cc", "network_lookup"))
        keep = lookup[lookup.index("std::optional<std::string> resolve_folder("):]
        self.assertIn("keyed && resolved && !resolve_reached_network", keep)
        self.assertIn("dir_fd < 0 && !folder.empty() && folder[0] == '/'", keep)
        self.assertIn("lookup_cache_forget();", body("bpy_angelo_guard_confine_owned.cc", "owned_region_began"))
        # Blender's C forgets them only when a write changes which folders exist or where they are (a folder made,
        # anything removed, a move): BLI_FileGate::folders_changed; a content write keeps them (head-14 read SHOULD).
        self.assertNotIn("lookup_cache_forget();", body("bpy_angelo_guard_confine.cc", "gate_refused"))
        confine = (INTERN / "bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        self.assertIn("move_refused_by_gate,\n                                      lookup_cache_forget};", confine)
        # Blender's C makes no link: only its moves start a new generation, never a plain write (head 14 SHOULD-2).
        self.assertIn("lookup_cache_forget();", body("bpy_angelo_guard_confine.cc", "move_refused_by_gate"))
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertRegex(hook, r'strchr\("mvl", arg\.role\) != nullptr\) \{\n\s*lookup_cache_forget\(\);')


if __name__ == "__main__":
    unittest.main()
