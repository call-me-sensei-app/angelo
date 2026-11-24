# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No lookup the confinement makes opens a remote name a script chose (BREAKIT review-confine-fixes-3cb71ae002d
MUST-1, review-confine-fixes-eddf9bcda75 MUST-1, review-confine-fixes-7a1bcdb652d; the class of
break-it-confine-fixes-b31e34840ca MUST-2).

On Windows touching `\\\\host\\share\\x` is the network effect itself (a DNS lookup, an SMB or WebDAV connection that
sends the person's NTLM hash). The judge refuses such a name by its text, but the confinement also looks names up for
other reasons (whose a file is, which installed file a code object names, where a module a region gained resolves).
So every file-system call in the confinement's code compiled on Windows goes through the opener or the stat-family
helpers (`bpy_angelo_guard_confine_remote`), which check the very wide name they hand the system, or sits after
`lookup_may_touch` in its own function (a write the write inventory lists at its site), or is listed here with its
reason. The calls looked for are the class, not a list of the ones fixed: Win32 and CRT calls that take a path,
every path-taking export of Blender's `BLI_fileops`, narrow C calls, C++ file streams, `std::filesystem` and the
ungated helpers for Angelo's own record. Every UTF-8 name is decoded once, strictly, by one decoder. Whether the
helpers really never reach the system is the gtest `ConfinementRemoteLookups` (a count of system calls, on Windows).
Reads sources; dependency-free.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INTERN = ROOT / "source" / "blender" / "python" / "intern"
BLENLIB = ROOT / "source" / "blender" / "blenlib"
OPENER = "bpy_angelo_guard_confine_remote.cc"

# Win32 and CRT calls that take a path and reach the file system (a DNS lookup and a connection for a UNC name), by
# family rather than by a list of names: any `CreateFile*`; a Win32 name, A or W, about files, folders, paths, volumes,
# drives, links or libraries (`SetCurrentDirectoryW`, `GetFileAttributesA`, `ReplaceFileW`); and the CRT's `_` and
# `_w` file functions with their `_s` and 64-bit forms (`_wfopen_s`, `_wstati64`, `_wchdir`). Text-only names and calls
# on a handle are not lookups (TEXT_ONLY, `*ByHandle*`).
WIN32_FAMILY = (r"CreateFile\w*|[A-Z][A-Za-z0-9]*(?:File|Directory|Path|Volume|Drive|Link|Library|Module)"
                r"[A-Za-z0-9]*[AW]"
                r"|_w?(?:fopen|freopen|f?s?open|stat|access|chmod|unlink|remove|rename|mkdir|rmdir|chdir|opendir|"
                r"findfirst|utime|spawn|exec|popen|system)\w*")
TEXT_ONLY = {"GetFullPathNameW", "GetFullPathNameA", "GetTempPathW", "GetTempPathA", "PathCchCanonicalizeEx",
             "PathCchCombineEx", "GetFinalPathNameByHandleW", "GetFinalPathNameByHandleA"}
# Narrow C calls (the CRT's, which reach the same system calls on Windows).
NARROW_CALLS = ("fopen", "freopen", "stat", "lstat", "open", "access", "realpath", "opendir", "remove", "rename",
                "unlink", "mkdir", "rmdir")
# Angelo's own record helpers (`_wfopen`, `MoveFileExW` with no check): each caller is listed with its reason.
UNGATED = ("open_ungated", "replace_ungated")
# Every way to turn UTF-8 into the wide name Windows is given: the confinement has one, `utf8_to_wide`
# (review-confine-fixes-7a1bcdb652d MUST-1, review-confine-fixes-4d9daef113a SHOULD-1). Blender's own decoder drops what
# it can't decode, so a second decoder beside a strict one reads one name two ways.
DECODERS = re.compile(r"\b(MultiByteToWideChar|alloc_utf16_from_8|UTF16_ENCODE|conv_utf_8_to_16|"
                      r"BLI_strncpy_wchar_from_utf8|mbstowcs\w*|mbsrtowcs\w*|mbtowc|mbrtowc|wstring_convert|"
                      r"codecvt\w*|u8path|char8_t)\b")
DECODES = {
    (OPENER, "utf8_to_wide", "MultiByteToWideChar"): "the one decoder, after utf8_strictly_valid",
    ("bpy_angelo_guard_confine_changes.cc", "changes_forget", "char8_t"):
        "the forgotten folder's walk (std::filesystem::path from char8_t), only after lookup_may_touch(folder_key) "
        "admitted it, so strictly valid UTF-8, which MSVC's decoder reads as utf8_to_wide does",
}


# What stands before a declared name: a type word (not `return`, `else`, `case`, `throw`, `new`, `delete`), then only
# spaces, a run of `*` or one `&`. A call is preceded by `=`, `(`, `,`, `;`, `!`, an operator (`&&` too) or nothing.
DECLARED = re.compile(r"(?:^|[\s(,;{])(?!(?:return|else|case|throw|new|delete|co_return)\b)[A-Za-z_][\w:<>]*"
                      r"(?:\s+|\s*\*+\s*|\s*&\s*)$")


def blenlib_file_calls():
    """Everything `BLI_fileops` exports that takes a path: each `BLI_` function with a `char *` or `wchar_t *`
    parameter, and each class it declares (`blender::fstream`, which opens a path with Blender's own decoder). Read
    from the headers, so a new one is covered without editing this test."""
    names = set()
    for header in ("BLI_fileops.hh", "BLI_fileops.h"):
        path = BLENLIB / header
        if not path.exists():
            continue
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for match in re.finditer(r"\b(BLI_\w+)\s*\(([^;{]*?)\)\s*(?:ATTR_\w+(?:\([^)]*\))?\s*)*;", text, re.S):
            if re.search(r"\b(char|wchar_t)\s*\*", match.group(2)):
                names.add(match.group(1))
        names.update(re.findall(r"\bclass\s+(\w+)\s*[:{]", text))
    return names


def call_pattern():
    blenlib = blenlib_file_calls()
    words = sorted(blenlib | set(UNGATED) | set(NARROW_CALLS), key=len, reverse=True)
    # A call by its name with any qualifier (`::f`, `std::f`, `angelo_guard::f`), never a member (`x.f`, `x->f`); a
    # Win32 family name; a C++ or Blender file stream by type; std::filesystem.
    return re.compile(r"(?<![\w.>])(?:\w+::)*(%s)\s*\(|(?<![\w.>])(?:\w+::)*(%s)\s*\(|"
                      r"(?<![\w.>])(?:\w+::)*((?:i|o)?fstream)\b|\b(std::filesystem)\b"
                      % ("|".join(map(re.escape, words)), WIN32_FAMILY))


# Every file-system call in code compiled on Windows, outside a gate: (file, function, call) -> why it is safe.
ALLOWED = {
    # The opener and the helpers: each checks the very wide name it hands the system (pinned below).
    (OPENER, "open_for_lookup", "CreateFileW"): "the opener: lookup_may_open on that name first",
    ("bpy_angelo_guard_confine_reparse.cc", "nt_open_file", "GetModuleHandleW"):
        "ntdll's own handle, to find NtOpenFile: names no file",
    ("bpy_angelo_guard_confine_tree.cc", "move_tree_verdict", "FindFirstFileExW"):
        "the moved folder's walk: network_lookup on the folder first; nothing it finds inside is followed",
    ("bpy_angelo_guard_confine_tree.cc", "move_tree_verdict", "FindNextFileW"):
        "the moved folder's walk: the entries of a folder the walk already reached without following anything",
    (OPENER, "lookup_stat", "BLI_wstat"): "a helper: touchable() decoded and checked that wide name first",
    (OPENER, "lookup_is_dir", "GetFileAttributesW"): "a helper: touchable() decoded and checked that wide name first",
    (OPENER, "lookup_is_file", "GetFileAttributesW"): "a helper: touchable() decoded and checked that wide name first",
    (OPENER, "lookup_fopen_read", "_wfopen"): "a helper: touchable() decoded and checked that wide name first",
    # The link walk's own probe (bpy_angelo_guard_confine_reparse.cc): one component in `\\?\` form, opened without
    # following it (FILE_FLAG_OPEN_REPARSE_POINT), on a prefix the walk has already proved real and local or on a
    # connected share; it never goes through open_for_lookup, which asks the walk.
    ("bpy_angelo_guard_confine_reparse.cc", "probe_component", "CreateFileW"):
        "the link walk's probe: a component never followed, under a prefix the walk proved (links.hh)",
    ("bpy_angelo_guard_confine_reparse.cc", "links_walk", "GetFileAttributesW"):
        "the walk's re-check of a cached folder: its own attributes (a reparse point is not followed), under a prefix "
        "this same walk proved",
    # Angelo's own change record and the keys it reads back.
    ("bpy_angelo_guard_confine_paths.cc", "open_ungated", "_wfopen"):
        "Angelo's change record (BLENDER_USER_CONFIG/angelo_agent_changed.txt), or a key a script wrote, opened only "
        "after lookup_stat succeeded on that same key (its callers, listed below)",
    ("bpy_angelo_guard_confine_paths.cc", "replace_ungated", "MoveFileExW"):
        "Angelo's change record: its temporary file renamed over it, both Angelo's own names",
    ("bpy_angelo_guard_confine_changes.cc", "stamp_now", "BLI_stat"): "the change record file (s.file), Angelo's own",
    ("bpy_angelo_guard_confine_changes.cc", "changed_elsewhere", "BLI_stat"):
        "the change record file (s.file), Angelo's own",
    ("bpy_angelo_guard_confine_changes.cc", "digest_of", "open_ungated"):
        "a key whose facts lookup_stat read first: every caller (settle_keys, settle_watched, changes_made, "
        "changes_wrote, changes_forget) asks facts_of on the same key before digest_of",
    ("bpy_angelo_guard_confine_changes.cc", "append", "open_ungated"): "the change record file (s.file)",
    ("bpy_angelo_guard_confine_changes.cc", "rewrite", "open_ungated"): "the change record's temporary file",
    ("bpy_angelo_guard_confine_changes.cc", "rewrite", "replace_ungated"): "the change record's temporary file",
    ("bpy_angelo_guard_confine_changes.cc", "read_file", "open_ungated"): "the change record file (s.file)",
    ("bpy_angelo_guard_confine_process.cc", "process_restore_now", "_wchdir"):
        "puts back the process's own working folder from before the region (read with _wgetcwd by folder_now): a name "
        "the system gave, never a script's",
    ("bpy_angelo_guard_confine_origins.cc", "absolute", "BLI_current_working_dir"):
        "the process's current folder read into a buffer: it names no file",
    # Made or removed by Angelo's own code on a name already checked.
    ("bpy_angelo_guard_confine_owned.cc", "guard_make_owned_dir", "CreateDirectoryW"):
        "a private temporary folder, made only after the judge answered Private for its parent (a remote name is "
        "refused Network by its text first)",
    ("bpy_angelo_guard_confine_beside.cc", "make_folder", "BLI_dir_create_recursive"):
        "a resolved anchor plus parts with no separator, `.`, `..` or `:`, after vouches::facts_of (the opener) ran "
        "on that very name",
    ("bpy_angelo_guard_confine_beside.cc", "make_below", "BLI_delete"):
        "the file this function just created (after lookup_may_touch)",
    ("bpy_angelo_guard_confine_writable.cc", "guard_create_beside", "BLI_delete"):
        "the file this function just created (after lookup_may_touch)",
    ("bpy_angelo_guard_confine_writable.cc", "guard_writable_store", "BLI_delete"):
        "only after the judge answered Private for the name (a remote name is refused Network by its text first)",
}
# Calls left at their own site, each after `lookup_may_touch` on its name in the same function: the write inventory
# lists these writes there (c_write_sinks.py), and the folder walk's children are on the folder's share.
GATED = {
    ("bpy_angelo_guard_confine_changes.cc", "changes_forget", "std::filesystem"): "the forgotten folder's walk",
    ("bpy_angelo_guard_confine_beside.cc", "make_below", "BLI_fopen"): "an exclusive create (c_write_sinks.py)",
    ("bpy_angelo_guard_confine_writable.cc", "guard_create_beside", "BLI_fopen"):
        "an exclusive create (c_write_sinks.py)",
}


def strip_comments(text):
    """`text` with every comment and every string or character literal's contents blanked (newlines and quotes kept):
    a call inside a comment or a string is no call, and a `//` inside a string is no comment."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif c == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", text[i:end]))
            i = end
        elif c in "\"'":
            end = i + 1
            while end < n and text[end] != c and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            out.append(c + " " * (min(end, n) - i - 1) + (text[end] if end < n else ""))
            i = end + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _defined(condition, macros):
    """Each `defined(X)` / `defined X` for X in `macros` in a condition: True where it is negated (`!`), else False."""
    return [bool(match.group(1)) for match in
            re.finditer(r"(!\s*)?\bdefined\s*\(?\s*(%s)\b" % "|".join(macros), condition)]


def _excludes_windows(word, condition):
    """Whether a branch's condition leaves Windows out: `#ifndef _WIN32`, `!defined(_WIN32)`, or a POSIX system named
    positively (`#ifdef __APPLE__`, `defined(__linux__)`) with no `_WIN32` beside it. A negated POSIX name
    (`#ifndef __APPLE__`, `!defined(__linux__)`) compiles on Windows."""
    windows = ("_WIN32", "WIN32")
    posix = ("__APPLE__", "__linux__", "__unix__")
    if word in ("ifdef", "ifndef"):
        name = condition.split()[0] if condition.split() else ""
        if name in windows:
            return word == "ifndef"
        return word == "ifdef" and name in posix
    win = _defined(condition, windows)
    if win:
        return all(win)   # Every `_WIN32` in it negated.
    return any(not negated for negated in _defined(condition, posix))


def windows_lines(text):
    """``(number, line, function)`` of the code compiled on Windows, comments blanked: lines inside a branch that
    leaves Windows out are dropped, and each line carries the name of the function it is in (the last definition
    that starts at the line's start above it). A macro's body is code too (``#define NAME``, its continuation lines
    included): a call it wraps is a call wherever the macro is used."""
    frames, found, function, in_define = [], [], "", None
    for number, line in enumerate(strip_comments(text).splitlines(), 1):
        stripped = line.strip()
        if in_define is not None:
            if not any(frame["excluded"] for frame in frames):
                found.append((number, line, in_define))
            in_define = in_define if stripped.endswith("\\") else None
            continue
        directive = re.match(r"#\s*(\w+)\s*(.*)", stripped)
        if directive and directive.group(1) == "define":
            name = re.match(r"(\w+)", directive.group(2))
            macro = "#define " + (name.group(1) if name else "")
            if not any(frame["excluded"] for frame in frames):
                found.append((number, directive.group(2)[len(name.group(1)) if name else 0:], macro))
            in_define = macro if stripped.endswith("\\") else None
            continue
        if directive:
            word, rest = directive.group(1), directive.group(2).strip()
            windows_branch = (word == "ifdef" and rest.split()[:1] in (["_WIN32"], ["WIN32"])) or \
                (word in ("if", "elif") and _defined(rest, ("_WIN32", "WIN32")) and
                 not all(_defined(rest, ("_WIN32", "WIN32"))))
            if word in ("if", "ifdef", "ifndef"):
                frames.append({"windows_seen": windows_branch, "excluded": _excludes_windows(word, rest)})
            elif word == "elif" and frames:
                frame = frames[-1]
                frame["excluded"] = frame["windows_seen"] or _excludes_windows("if", rest)
                frame["windows_seen"] = frame["windows_seen"] or windows_branch
            elif word == "else" and frames:
                frame = frames[-1]
                # After a Windows branch the rest is not Windows'; after branches that left Windows out, it is.
                frame["excluded"] = frame["windows_seen"]
            elif word == "endif" and frames:
                frames.pop()
            continue
        definition = re.match(r"^(?!\s)(?!return\b|if\b|for\b|while\b)[\w:<>*&,\s]*?\b(\w+)\s*\([^;]*$", line)
        if definition:
            function = definition.group(1)
        if stripped and not any(frame["excluded"] for frame in frames):
            found.append((number, line, function))
    return found


def windows_text(text):
    """The code compiled on Windows, comments blanked, every other line empty (line numbers kept)."""
    kept = {number: line for number, line, _ in windows_lines(text)}
    return "\n".join(kept.get(number, "") for number in range(1, text.count("\n") + 2))


def sources():
    """The guard's sources and headers (an inline helper in a header is code too), tests left out."""
    return sorted(path for path in INTERN.glob("bpy_angelo_guard*") if path.suffix in (".cc", ".hh")
                  and not path.stem.endswith("_test"))


def body(name, function):
    """The body of `function` as Windows compiles it in file `name`, comments blanked."""
    text = windows_text((INTERN / name).read_text(encoding="utf-8", errors="replace"))
    start = re.search(r"^(?!\s)[^\n;]*\b%s\s*\(" % re.escape(function), text, re.M)
    if start is None:
        raise AssertionError("%s: no definition of %s" % (name, function))
    at = text.index("{", start.end())
    depth, end = 0, at
    while True:
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return text[at:end + 1]
        end += 1


def file_calls():
    """``[(file, function, call, line)]`` of every file-system call in code compiled on Windows."""
    pattern, found = call_pattern(), []
    for path in sources():
        for number, line, function in windows_lines(path.read_text(encoding="utf-8", errors="replace")):
            for match in pattern.finditer(line):
                call = next(group for group in match.groups() if group)
                if call in TEXT_ONLY or "ByHandle" in call:
                    continue   # Text only, or a call on a handle: no name reaches the system.
                if match.lastindex in (1, 2) and DECLARED.search(line[:match.start()]):
                    continue   # A declaration or definition: a return type before the name (`FILE *open_ungated(`).
                found.append((path.name, function, call, number))
    return found


class RemoteLookups(unittest.TestCase):
    def test_the_reader_sees_the_class(self):
        names = blenlib_file_calls()
        for name in ("BLI_stat", "BLI_exists", "BLI_is_dir", "BLI_fopen", "BLI_delete", "BLI_rename",
                     "BLI_dir_create_recursive", "BLI_copy", "BLI_file_touch", "fstream"):
            self.assertIn(name, names)
        pattern = call_pattern()

        def seen(code):
            return [next(g for g in m.groups() if g) for m in pattern.finditer(strip_comments(code))
                    if not (m.lastindex in (1, 2) and DECLARED.search(strip_comments(code)[:m.start()]))]
        self.assertEqual(seen('/* c */ BLI_exists(p); std::ifstream in(p); open_ungated(p, "rb"); x = "a//b";'
                              " BLI_rename(a, b); ::stat(p, &s); // BLI_delete(p);\n"),
                         ["BLI_exists", "ifstream", "open_ungated", "BLI_rename", "stat"])
        # Any qualifier, Blender's stream, the Win32 families by rule (A and W, `_s` forms), not a member or a decl.
        self.assertEqual(seen('_wfopen_s(&f, w, L"rb"); fstream probe(p, std::ios::in); '
                              'angelo_guard::open_ungated(p, "rb"); GetFileAttributesA(p); SetCurrentDirectoryW(w); '
                              "_wstati64(w, &s); written = written && replace_ungated(a, b); x.open(p); y->stat(p); "
                              "FILE *open_ungated(const std::string &path, const char *mode);"),
                         ["_wfopen_s", "fstream", "open_ungated", "GetFileAttributesA", "SetCurrentDirectoryW",
                          "_wstati64", "replace_ungated"])
        guarded = ("#ifndef __APPLE__\na();\n#endif\n#if !defined(__linux__)\nb();\n#endif\n"
                   "#ifdef _WIN32\nc();\n#else\nd();\n#endif\n"
                   "#if defined(__APPLE__)\ne();\n#elif defined(_WIN32)\nf();\n#else\ng();\n#endif\n"
                   "#ifndef _WIN32\nh();\n#else\ni();\n#endif\n"
                   "#define PROBE(p) BLI_exists(p)\n#define LONG(p) \\\n  BLI_stat(p, nullptr)\n")
        lines = windows_lines(guarded)
        self.assertEqual([line.strip() for _, line, _ in lines],
                         ["a();", "b();", "c();", "f();", "i();", "(p) BLI_exists(p)", "(p) \\",
                          "BLI_stat(p, nullptr)"])
        self.assertEqual([function for _, _, function in lines][-3:], ["#define PROBE", "#define LONG", "#define LONG"])

    def test_every_file_call_is_checked_or_has_its_reason(self):
        calls = file_calls()
        stray = [call for call in calls if call[:3] not in ALLOWED and call[:3] not in GATED]
        self.assertEqual(stray, [], "a file-system call that bypasses the lookup helpers "
                                    "(bpy_angelo_guard_confine_remote.hh): check it, or give its reason here")
        used = {call[:3] for call in calls}
        self.assertEqual(sorted((set(ALLOWED) | set(GATED)) - used), [], "an exception that no longer exists")
        self.assertTrue(all(reason.strip() for reason in list(ALLOWED.values()) + list(GATED.values())))

    def test_gated_calls_come_after_the_check(self):
        for name, function, call in sorted(GATED):
            code = body(name, function)
            self.assertIn("lookup_may_touch(", code, (name, function))
            self.assertLess(code.index("lookup_may_touch("), code.index(call), (name, function))

    def test_each_helper_checks_the_name_it_hands_the_system(self):
        # Each walks as its call follows links: BLI_wstat and _wfopen follow the last component, GetFileAttributesW
        # describes a link itself (re-read NIT: walking it as followed refused a link to a share by its own name).
        for function, call, check in (("lookup_stat", "BLI_wstat(", "touchable(path, wide)"),
                                      ("lookup_is_dir", "GetFileAttributesW(", "touchable(path, wide, false)"),
                                      ("lookup_is_file", "GetFileAttributesW(", "touchable(path, wide, false)"),
                                      ("lookup_fopen_read", "_wfopen(", "touchable(path, wide)")):
            code = body(OPENER, function)
            self.assertIn(check, code, function)
            self.assertLess(code.index(check), code.index(call), function)
            self.assertIn(call + "wide.c_str()", code, function)   # The checked wide name, not the UTF-8 again.
        touch = body(OPENER, "touchable")
        self.assertLess(touch.index("utf8_to_wide(path, wide)"), touch.index("GetFullPathNameW"))
        self.assertGreaterEqual(touch.count("lookup_may_open("), 2)   # The text and the full name.
        self.assertIn("links_walk(full, follow_last) == links::Verdict::Safe", touch)   # And the links (Strict).
        self.assertIn("touchable(path, wide)", body(OPENER, "lookup_may_touch"))

    def test_one_strict_decoder(self):
        """A name is decoded once, strictly: a decoder that drops invalid bytes reads `\\x80\\\\host\\s\\x` as
        `\\\\host\\s\\x` (review-confine-fixes-7a1bcdb652d MUST-1). Any other UTF-8 to UTF-16 entry point
        (Blender's, the CRT's, the standard library's) in code compiled on Windows fails this
        (review-confine-fixes-4d9daef113a SHOULD-1)."""
        found = {(path.name, function, match.group(1))
                 for path in sources()
                 for _, line, function in windows_lines(path.read_text(encoding="utf-8", errors="replace"))
                 for match in DECODERS.finditer(line)}
        self.assertEqual(sorted(found), sorted(DECODES), "decode UTF-8 only with utf8_to_wide (remote.hh)")
        self.assertTrue(all(reason.strip() for reason in DECODES.values()))
        decoder = body(OPENER, "utf8_to_wide")
        self.assertEqual(decoder.count("MultiByteToWideChar("), decoder.count("MB_ERR_INVALID_CHARS"))
        self.assertLess(decoder.index("utf8_strictly_valid("), decoder.index("MultiByteToWideChar("))
        names = re.sub(r"\s+", " ", body(OPENER, "names_a_remote_file"))
        # An undecodable name is remote (refused by its text); a decoded one is judged by that same wide string.
        self.assertIn("if (!utf8_to_wide(path, wide)) { return true; }", names)
        self.assertIn("if (!windows_names_remote(wide)) { return false; }", names)
        # The network-only lookup too: a name that doesn't decode is the network, refused (head-7 SHOULD).
        lookup = re.sub(r"\s+", " ", body("bpy_angelo_guard_confine_wrap_lookup.cc", "network_lookup"))
        self.assertIn("if (!utf8_to_wide(path, wide)) { refusal = Refusal::Network; }", lookup)
        walk = body("bpy_angelo_guard_confine_changes.cc", "changes_forget")
        self.assertLess(walk.index("if (lookup_may_touch(folder_key)) {"), walk.index("char8_t"))

    def test_the_opener_checks_before_it_calls_the_system(self):
        code = body(OPENER, "open_for_lookup")
        self.assertIn("lookup_may_open(path)", code)
        self.assertLess(code.index("lookup_may_open(path)"), code.index("CreateFileW("))
        self.assertIn("return INVALID_HANDLE_VALUE;", code[:code.index("CreateFileW(")])

    def test_resolve_refuses_an_unconnected_share_before_any_prefix_is_opened(self):
        code = body("bpy_angelo_guard_confine_paths.cc", "resolve")
        self.assertIn("if (!lookup_may_open(full)) {", code)
        self.assertLess(code.index("lookup_may_open(full)"), code.index("open_any("))
        self.assertIn("return HANDLE(open_for_lookup(path, true));", body("bpy_angelo_guard_confine_paths.cc",
                                                                          "open_any"))

    def test_only_angelo_names_a_connected_share(self):
        """A share is noted as connected only from Angelo's own root folders named before the first script region
        (later roots' inputs may hold what a script appended to Angelo's own lists: review-confine-fixes-eddf9bcda75
        SHOULD-1): never from a script's text, an open handle's name or Windows' connection list."""
        noted = sorted((path.name, line.strip()) for path in sources()
                       if path.name not in (OPENER, "bpy_angelo_guard_confine_remote.hh")
                       for _, line, _ in windows_lines(path.read_text(encoding="utf-8", errors="replace"))
                       if re.search(r"\bnote_connected(_root)?\(", line))
        self.assertEqual(noted, sorted([
            ("bpy_angelo_guard_confine_folders.cc", "note_connected_root(folder);"),
            ("bpy_angelo_guard_confine_folders.cc", "note_connected_root(parent);"),
            ("bpy_angelo_guard_confine_reparse.cc", "note_connected(share);"),
        ]))   # Not from an open handle's name, nor from Windows' list (head-7 MUST-4: never noted for good).
        # The link walk notes a share only while Angelo resolves its own roots before the first region
        # (break-it-confine-reparse-b7d499c S3: the person's folder linked to a share they use).
        walk = body("bpy_angelo_guard_confine_reparse.cc", "links_walk")
        self.assertLess(walk.index("if (noting_root_links > 0) {"), walk.index("note_connected(share);"))
        self.assertIn("active_(!remote_region_has_begun())", windows_text(
            (INTERN / "bpy_angelo_guard_confine_reparse.cc").read_text(encoding="utf-8")))
        for function in ("add", "Families::add"):
            self.assertIn("const RootLinksNoted noting;", body("bpy_angelo_guard_confine_folders.cc", function))
        root = body(OPENER, "note_connected_root")
        self.assertLess(root.index("region_began.load("), root.index("note_connected("))
        self.assertIn("region_began.store(true", body(OPENER, "remote_region_began"))
        self.assertIn("remote_region_began();", body("bpy_angelo_guard_confine_owned.cc", "owned_region_began"))
        for setter in ("guard_add_roots", "guard_add_open_roots"):
            self.assertIn("refuse_if_confined(", body("bpy_angelo_guard_confine_roots.cc", setter), setter)

    def test_a_refusing_root_is_never_dropped(self):
        """Private, startup and tool-output roots refuse what they hold: one on a share nobody connected is held by its
        text, never dropped (review-confine-fixes-7a1bcdb652d SHOULD-2); trusted and open roots may drop it."""
        add = body("bpy_angelo_guard_confine_folders.cc", "add")   # Roots::add, the first `add` defined.
        self.assertIn("refusing ? unconnected_remote_spelling(folder)", add)
        self.assertIn("unconnected_remote_spelling(parent)", body("bpy_angelo_guard_confine_folders.cc",
                                                                  "Families::add"))
        roots = body("bpy_angelo_guard_confine_roots.cc", "guard_add_roots")
        for kind in ("private_roots", "startup_roots", "tool_roots"):
            self.assertIn("folders().%s.add(path, true)" % kind, roots, kind)
        self.assertIn("folders().trusted_roots.add(path);", roots)

    def test_the_stat_family_asks_the_network_check(self):
        """M7 (break-it-confine-reparse-b7d499c): the stat family raises no audit event, so each function is wrapped
        (its last component followed as it follows it), os.DirEntry's lookups too, and Blender's C lookups ask the
        same check through the file gate, after the hot-path count."""
        name = "bpy_angelo_guard_confine_wrap_lookup.cc"
        text = (INTERN / name).read_text(encoding="utf-8")   # The table's names are string literals: read as written.
        for function, follows in (("stat", "true, true"), ("lstat", "false, false"), ("access", "true, true"),
                                  ("readlink", "false, false"), ("_path_isdir", "true, false"),
                                  ("_path_islink", "false, false"), ("_path_lexists", "false, false"),
                                  ("_getfinalpathname", "true, false")):
            self.assertRegex(text, r'"%s", %s,' % (re.escape(function), follows), function)
        # os.DirEntry's methods live in their own file since iso-mac's MUST-3 split (the 500-line rule).
        entries = "bpy_angelo_guard_confine_wrap_lookup_entry.cc"
        entry_text = (INTERN / entries).read_text(encoding="utf-8")
        for method in ("is_dir", "is_file", "stat"):
            self.assertIn('{"%s", nullptr}' % method, entry_text)
        call = body(name, "lookup_call")
        self.assertLess(call.index("network_lookup("), call.index("PyObject_Call(wrap.original"))
        entry = body(entries, "entry_call")
        self.assertLess(entry.index("names_a_remote_file(path)"), entry.index("PyObject_Call(wrap.original"))
        storage = windows_text((BLENLIB / "intern" / "storage.cc").read_text(encoding="utf-8"))
        for function in ("BLI_exists", "BLI_stat", "BLI_is_dir", "BLI_is_file"):
            for match in re.finditer(r"^(?!\s)[^\n;]*\b%s\s*\(const char \*path[^\n]*\n\{\n([^\n]*)" % function,
                                     storage, re.M):
                self.assertIn("BLI_fileops_gate_refuses_lookup(path, true)", match.group(1), function)
        gate = (BLENLIB / "intern" / "fileops_c.cc").read_text(encoding="utf-8")
        refuses = gate[gate.index("bool BLI_fileops_gate_refuses_lookup("):]
        self.assertLess(refuses.index("BLI_fileops_confine_possible()"), refuses.index("file_gate->lookup_refused("))
        confine = (INTERN / "bpy_angelo_guard_confine.cc").read_text(encoding="utf-8")
        self.assertIn("install_wrappers_lookup() == 0", confine)
        self.assertIn("lookup_refused_by_gate,\n                                      move_refused_by_gate,\n", confine)

    def test_a_share_windows_has_connected_counts_as_connected(self):
        """A person's own link to a share they use keeps working (Ruling 11 (3)), narrowed by head-7 MUST-4: a drive
        letter mapped to the share counts at any time; a deviceless connection only if it was OK and listed when the
        first region began, and OK now. Nothing is noted for good; Windows is asked outside the lock."""
        may_open = body(OPENER, "lookup_may_open")
        self.assertLess(may_open.index("connected_shares()"), may_open.index("windows_connected_now(share)"))
        self.assertNotIn("note_connected", may_open)
        read = body(OPENER, "read_uses")
        self.assertIn("NetUseEnum(nullptr, 1,", read)
        self.assertIn("listed[i].ui1_local != nullptr && listed[i].ui1_local[0] != L'", read)   # Literals blanked.
        self.assertIn("listed[i].ui1_status == USE_OK", read)
        now = body(OPENER, "uses_now")
        self.assertIn("now - uses_read_at <= 1000", now)   # Read at most once a second.
        self.assertLess(now.index("}\n"), now.index("read_uses()"))   # The lock is let go before asking Windows.
        connected = body(OPENER, "windows_connected_now")
        self.assertLess(connected.index("if (use.mapped)"), connected.index("if (use.ok)"))
        self.assertIn("deviceless_at_start", connected)
        began = body(OPENER, "remote_region_began")
        self.assertLess(began.index("windows_uses_at_start();"), began.index("region_began.store(true"))
        # Taken before the first document loads (re-read SHOULD: the startup file came first), once.
        self.assertIn("if (uses_at_start_taken.exchange(true)) {", body(OPENER, "windows_uses_at_start"))
        # POSIX: the automounter's baseline is taken at the same point (its first refresh), before the startup file.
        raw = (INTERN / OPENER).read_text(encoding="utf-8")   # body() is Windows' view: the POSIX branch is raw text.
        snapshot = raw[raw.index("static void start_snapshot()"):]
        snapshot = snapshot[:snapshot.index("\n}\n")]
        self.assertIn("automount::refresh();", snapshot[snapshot.index("#else"):])
        # Taken off the startup path (Ruling 17: nothing costs anything while no script runs), on a detached thread
        # whose future the first region waits for before any script sees the flag.
        at_start = raw[raw.index("void remote_connections_at_start()"):]
        at_start = at_start[:at_start.index("\n}\n")]
        self.assertIn("}).detach();", at_start)
        began = raw[raw.index("void remote_region_began()"):]
        self.assertLess(began.index("start_snapshot_wait();"), began.index("region_began.store(true"))
        init = (BLENLIB.parent / "windowmanager" / "intern" / "wm_init_exit.cc").read_text(encoding="utf-8")
        self.assertLess(init.index("BPY_angelo_guard_connections_at_start();"),
                        init.index("wm_homefile_read_ex(C, &read_homefile_params"))
        self.assertIn("list(APPEND LIB netapi32)", (INTERN / "CMakeLists.txt").read_text(encoding="utf-8"))

    def test_a_share_counts_only_as_the_person_connected_it(self):
        """The whole rule, after the coordinator's ruling on head 10: a drive letter mapped to it (any time); a
        deviceless connection listed before the first region; a share one of Angelo's roots lies on, noted before the
        first region (Roots::add / Families::add; add_roots and add_open_roots refuse while confined); and the share of
        a document the person opens, noted by Blender's own file read only while no thread is confined and none on
        this stack (a script's open, or one it queued, notes nothing)."""
        settled = body("bpy_angelo_guard_confine_beside.cc", "BPY_angelo_guard_document_settled")
        gate = re.sub(r"\s+", " ", settled)
        self.assertIn("note_person_document(document, !scripts && BLI_fileops_confine_open.load(", gate)
        self.assertIn("== 0 && !python_on_stack());", gate)   # The person's own open: no Python asked for it.
        frames = body("bpy_angelo_guard_confine.cc", "python_on_stack")
        self.assertIn("PyThreadState_GetFrame(state)", frames)
        self.assertLess(settled.index("const bool scripts = confined_now();"), settled.index("note_person_document("))
        person = body(OPENER, "note_person_document")
        self.assertLess(person.index("!unconfined"), person.index("note_connected(wide);"))
        roots = (INTERN / "bpy_angelo_guard_confine_roots.cc").read_text(encoding="utf-8")
        for setter in ("add_roots", "add_open_roots"):
            self.assertIn('refuse_if_confined("%s")' % setter, roots)
        wm = (BLENLIB.parent / "windowmanager" / "intern" / "wm_files.cc").read_text(encoding="utf-8")
        self.assertGreaterEqual(wm.count("BPY_angelo_guard_document_settled();"), 2)   # Read, and save.

    def test_windows_copies_are_judged(self):
        """Head-10/11 re-read MUST: shutil.copy2 on Windows (3.12+) copies through _winapi.CopyFile2, raising no
        shutil.copyfile event: judged as that copy (source read, target written), and a copy that makes a link
        (COPY_FILE_COPY_SYMLINK, copy2(follow_symlinks=False)) refused like any link made."""
        raw = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertIn('{"_winapi.CopyFile2", {Kind::File, {{0, \'r\', -1}, {1, \'w\', -1}}}},', raw)
        tail = raw[raw.index("static int link_refused("):]
        copy = tail[tail.index('strcmp(name, "_winapi.CopyFile2") == 0'):]
        # An allowlist (head-12 re-read M1): only content-copy flags pass; every other bit, unknown ones too, refuses.
        block = copy[:1800]
        self.assertIn("if (value & ~allowed) {", block)
        self.assertIn(": ~0ul;", block)   # Flags it can't read: refused.
        allowed = block[block.index("constexpr unsigned long allowed ="):]
        allowed = allowed[:allowed.index(";")]
        for unsafe in ("copy_symlink", "reparse", "directory", "0x00200000", "0x00000080", "0x00000800"):
            self.assertNotIn(unsafe, allowed)

    def test_blenders_copy_never_makes_a_link_for_a_script(self):
        """Head-10/11 re-read SHOULD: POSIX BLI_copy copied a link as a link (exporter texture copies, packed
        files): for a confined thread it copies a link to a file as the file, and refuses anything else not plain."""
        fileops = (BLENLIB / "intern" / "fileops_c.cc").read_text(encoding="utf-8")
        copy = fileops[fileops.index("static int copy_single_file(const char *from, const char *to)\n{\n  FILE"):]
        self.assertLess(copy.index("if (confined && S_ISLNK(st.st_mode)) {"), copy.index("if (symlink(link_buffer, to))"))
        self.assertLess(copy.index("if (confined && !S_ISREG(st.st_mode) && !S_ISDIR(st.st_mode)) {"),
                        copy.index("if (mknod(to, st.st_mode, st.st_rdev))"))

    def test_the_agent_is_never_told_which_share(self):
        """Head-10/11 re-read SHOULD: the refusal the agent reads never names the share or invites connecting it (a
        script chose it); the person is told, on the console and through network_shares_refused (refused while
        confined)."""
        refused = body("bpy_angelo_guard_confine.cc", "refuse")
        self.assertIn("person_network_share_refused(share);", refused)
        self.assertNotIn("with_share", refused)
        self.assertNotIn("reason = ", refused[:refused.index("if (judged_unrecorded)")].split("unreadable)")[0])
        shares = body("bpy_angelo_guard_confine.cc", "guard_network_shares_refused")
        self.assertLess(shares.index("refuse_if_confined("), shares.index("PyList_New("))
        # Each refusal takes the next serial, repeats included (the UI tells the person once per refusal).
        self.assertIn("refused_shares.push_back({++refused_shares_serial, share});",
                      body("bpy_angelo_guard_confine.cc", "person_network_share_refused"))

    def test_previews_load_is_judged_when_asked(self):
        """Head-12 re-read SHOULD: bpy.utils.previews.load reads its file later on a thumbnail job; judged as a read
        when the script asks, as blf.load is."""
        previews = (INTERN / "bpy_utils_previews.cc").read_text(encoding="utf-8")
        load = previews[previews.index("static PyObject *bpy_utils_previews_load("):]
        self.assertLess(load.index("BPY_angelo_guard_read_refused_raise(filepath_data.value)"),
                        load.index("BKE_previewimg_cached_thumbnail_read("))

    def test_a_shortcut_resolved_for_a_script_never_tracks_or_rewrites(self):
        """Head-14 read SHOULD: BLI_file_alias_target's .lnk Resolve for a confined thread uses SLR_NOTRACK and
        SLR_NOLINKINFO (no link tracking or link info, which can ask the network) and no SLR_UPDATE (a rewrite of the
        .lnk nothing judged)."""
        storage = (BLENLIB / "intern" / "storage.cc").read_text(encoding="utf-8")
        resolve = storage[storage.index("Shortcut->Resolve(0,"):][:300]
        confined = resolve[:resolve.index(":")]
        self.assertIn("SLR_NOTRACK | SLR_NOLINKINFO", confined)
        self.assertNotIn("SLR_UPDATE", confined)

    def test_the_resolved_folders_miss_no_link_a_script_could_make(self):
        """Head-14 read SHOULD (the cache claim, pinned): the POSIX resolved-folder cache is kept across content writes
        because no confined code can make or move a link; each pillar of that claim is here. A link made by the
        person, Angelo's unconfined threads or another process is seen at the next region, as before."""
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        links = hook[hook.index("static int link_refused("):hook.index("static bool innermost_is_agent()")]
        self.assertIn('strcmp(name, "os.symlink") == 0 || strcmp(name, "_winapi.CreateJunction") == 0', links)
        self.assertIn('strcmp(name, "_winapi.CopyFile2") == 0', links)          # A copy that makes a link.
        self.assertIn("last_component_links(*source, dir_fd)", links)           # No move or hard link of a link.
        self.assertIn("move_tree_verdict(*source, dir_fd)", links)              # No move of a folder holding one.
        self.assertIn('strchr("mvl", arg.role) != nullptr) {\n          lookup_cache_forget();', hook)
        fileops = (BLENLIB / "intern" / "fileops_c.cc").read_text(encoding="utf-8")
        copy = fileops[fileops.index("static int copy_single_file(const char *from, const char *to)\n{\n  FILE"):]
        self.assertLess(copy.index("if (confined && S_ISLNK(st.st_mode)) {"), copy.index("if (symlink(link_buffer, to))"))
        for definition in re.finditer(r"int BLI_create_symlink\(", fileops):   # Compiled out on both systems.
            self.assertEqual(fileops[:definition.start()].rstrip().rsplit("\n", 1)[-1].strip(), "#  if 0")
        owned = body("bpy_angelo_guard_confine_owned.cc", "owned_region_began")
        self.assertIn("lookup_cache_forget();", owned)                          # Every region starts afresh.

    def test_every_hook_row_is_an_event_something_raises(self):
        """A row whose event nothing raises judges nothing and reads as coverage (sys.set_asyncgen_hooks_*, the
        documented plural names: CPython raises the singular ones; os.lchflags: CPython raises os.chflags for it).
        Every row is an event CPython's own source raises (cpython_audit_table, C and Lib; the documentation's names
        don't count), one Angelo's own code raises, or a known gap of the table's generator, with its reason."""
        import cpython_audit_table as table
        raised = set(table.C_EVENTS) | set(table.LIB_EVENTS)
        generator_gaps = {}   # The generator reads every C/C++ source and Lib's `<sys>.audit` (none left).
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        start = hook.index("static const std::unordered_map<std::string_view, Event> &events()")
        rows = re.findall(r'^\s*\{"([^"]+)",', hook[start:hook.index("};", start)], re.M)
        self.assertGreater(len(rows), 50)
        angelo = "".join(path.read_text(encoding="utf-8", errors="replace") for path in sources()
                         if path.name != "bpy_angelo_guard_confine_hook.cc")
        dead = []
        for row in rows:
            if row in raised or row in generator_gaps:
                continue
            if row.startswith("angelo.") and '"%s"' % row in angelo:
                continue   # Angelo's own event, raised by its wrappers.
            dead.append(row)
        self.assertEqual(dead, [], "hook rows no source raises")
        self.assertTrue(all(reason for reason in generator_gaps.values()))
        for event in ("_wmi.exec_query", "tempfile.mkstemp", "tempfile.mkdtemp"):   # The generator's former gaps.
            self.assertIn(event, raised)

    def test_a_path_with_no_reparse_point_is_told_by_one_open(self):
        """Windows cost (build3: the walk cost ~4.3x the baseline at 12 folders): one NtOpenFile of the whole name with
        OBJ_DONT_REPARSE over the volume's device path says there is no reparse point on the way; anything else (a
        reparse point, any other answer, a drive that is no device: a subst, a share) falls back to the component
        walk, which alone decides every refusal."""
        raw = (INTERN / "bpy_angelo_guard_confine_reparse.cc").read_text(encoding="utf-8")
        fast = raw[raw.index("bool no_reparse_on_the_way("):]
        fast = fast[:fast.index("\n}\n")]
        self.assertIn("dont_reparse = 0x00001000", fast)
        # Every failure is a look that failed, walked: a fault inside NtOpenFile too (head-15 crash on build3, review).
        self.assertIn("nt_open_guarded(", fast)
        guarded = raw[raw.index("LONG nt_open_guarded("):]
        self.assertIn("__except (EXCEPTION_EXECUTE_HANDLER) {", guarded[:1200])
        self.assertIn("if (handle != nullptr && handle != INVALID_HANDLE_VALUE) {", fast)
        self.assertIn("case_insensitive | dont_reparse", fast)
        self.assertIn('device.rfind(L"\\\\Device\\\\", 0) != 0', fast)          # Only a plain device path.
        self.assertIn("start.type != links::Rooted::Type::Drive", fast)            # No share, volume or bad root.
        self.assertIn("return result == name_not_found || result == path_not_found || result == not_a_directory;",
                      fast)                                                        # Everything else: walked.
        walk = raw[raw.index("links::Verdict links_walk("):]
        self.assertLess(walk.index("no_reparse_on_the_way(links::split_input(full), follow_last)"),
                        walk.index("links::walk(full, follow_last, walker)"))
        # Past each link the walk resolves, the rest is looked at the same way once (the link path's cost).
        self.assertIn("return no_reparse_on_the_way(at, follow);", walk)
        pure = (INTERN / "bpy_angelo_guard_confine_links.cc").read_text(encoding="utf-8")
        self.assertIn("if (walker.rest_plain && walker.rest_plain(current, pending, follow_last)) {", pure)

    def test_socketpair_allows_only_its_own_loopback_events(self):
        """36bcdd2 read MUST-4: the network allowance while socket.socketpair runs was thread-wide, so script code run
        inside it (a __del__, a gc callback, a signal handler) could connect anywhere. Only the pair's own events pass
        now: socket.__new__, and bind/connect to loopback, with every frame up to the call socket.py's own, never the
        agent's."""
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertIn("return socketpair_event_allowed(name, args) ? 0 : refuse(NETWORK, name);", hook)
        self.assertNotIn("making_socketpair > 0 ? 0", hook)
        io = (INTERN / "bpy_angelo_guard_confine_wrap_io.cc").read_text(encoding="utf-8")
        allowed = io[io.index("bool socketpair_event_allowed("):io.index("static PyObject *wrapped_socketpair(")]
        for piece in ('strcmp(name, "socket.__new__") == 0', 'strcmp(name, "socket.bind") == 0',
                      'strcmp(name, "socket.connect") == 0', "loopback(PyTuple_GET_ITEM(args, 1))",
                      "!agent_code(code) && file != nullptr && socketpair_file == file", "frame == socketpair_caller"):
            self.assertIn(piece, allowed)

    def test_overlapped_never_binds_connects_or_reuses_for_a_script(self):
        """36bcdd2 read MUST-3: _overlapped raises no audit event, so a script's own socket could be bound
        (BindLocal), connected (WSAConnect, Overlapped.ConnectEx), readied for reuse (DisconnectEx) or used to send to an
        address (WSASendTo), and a pipe opened by name (ConnectPipe): each is refused as the network while confined."""
        io = (INTERN / "bpy_angelo_guard_confine_wrap_io.cc").read_text(encoding="utf-8")
        for method in ("WSASendTo", "AcceptEx", "ConnectEx", "DisconnectEx"):
            self.assertRegex(io, r'\{"%s", \{[^}]*\}, (true|false), nullptr, true\},' % method)
        call = io[io.index("static PyObject *overlapped_call("):]
        self.assertLess(call.index("if (confined_now() && wrap.network) {"), call.index("PyObject_Call(wrap.original"))
        self.assertIn('{{"BindLocal", nullptr}, {"WSAConnect", nullptr}, {"ConnectPipe", nullptr}}', io)
        refused = io[io.index("static PyObject *overlapped_refused_call("):]
        self.assertLess(refused.index("refuse(NETWORK, what.c_str());"), refused.index("PyObject_Call(wrap.original"))
        cases = (INTERN.parents[3] / "tests" / "python" / "angelo_agent" / "confinement_lookup_cases.py").read_text(
            encoding="utf-8")
        self.assertNotIn('"BindLocal": "a socket', cases)   # No longer "covered by socket events".

    def test_the_link_walk_runs_only_with_strict_script_protection(self):
        """Ruling 17: the link walk costs a confined lookup measurably against Angelo's own speed, so it runs only
        with "Strict script protection" on (off by default), and then the lookup helpers and open_for_lookup walk only
        for a confined thread: nothing costs while no script runs. Off, every lookup keeps 2.0.1's check by the name's
        text, its full name included, and a judge keeps the name's text and its resolved path. The on-disk tests turn
        the walk on for every thread."""
        raw = (INTERN / OPENER).read_text(encoding="utf-8")
        walks = raw[raw.index("static bool walks_links()"):]
        self.assertIn("walk_always.load(std::memory_order_relaxed) || root_links_being_noted() ||\n"
                      "         (angelo_strict_script_protection() && confined_now())", walks[:300])
        # Angelo resolving its own roots before the first region walks whatever the switch says: the walk notes the
        # share a person's linked folder names (build3 head 18: roots_before_and_after_the_first_region).
        reparse = (INTERN / "bpy_angelo_guard_confine_reparse.cc").read_text(encoding="utf-8")
        self.assertIn("bool root_links_being_noted()\n{\n  return noting_root_links > 0;\n}", reparse)
        wanted = raw[raw.index("bool strict_lookups()"):]
        self.assertIn("walk_always.load(std::memory_order_relaxed) || angelo_strict_script_protection()", wanted[:200])
        strict = (INTERN / "bpy_angelo_guard_strict.cc").read_text(encoding="utf-8")
        self.assertIn("static std::atomic<bool> strict{false};", strict)   # Off by default.
        touch = body(OPENER, "touchable")
        # 2.0.1's full-name text check for every lookup, before (and whether or not) the walk runs.
        self.assertLess(touch.index("GetFullPathNameW"), touch.index("walks_links()"))
        self.assertLess(touch.index("if (!lookup_may_open(full)) {"), touch.index("walks_links()"))
        self.assertIn("return !walks_links() || links_walk(full, follow_last) == links::Verdict::Safe;", touch)
        # Every call of the walk outside its own file and its tests is behind one of the two gates, on its line or
        # the line before it (a judge: strict_lookups; a lookup: walks_links).
        for path in sorted(INTERN.glob("bpy_angelo_guard_confine*.cc")):
            if path.name.endswith("_test.cc") or path.name == "bpy_angelo_guard_confine_reparse.cc":
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines):
                if "links_walk(" in line and not line.lstrip().startswith(("/*", "*", "//")):
                    near = line + (lines[number - 1] if number else "")
                    self.assertTrue("walks_links()" in near or "strict_lookups()" in near,
                                    "%s:%d %s" % (path.name, number + 1, line.strip()))
        opener = raw[raw.index("void *open_for_lookup("):]
        self.assertIn("if (walks_links() && links_walk(path, follow_last) != links::Verdict::Safe) {", opener)
        disk = (INTERN / "bpy_angelo_guard_confine_reparse_win_test.cc").read_text(encoding="utf-8")
        self.assertIn("lookups_walk_always(true);", disk)

    def test_the_lookup_network_checks_run_only_with_strict_script_protection(self):
        """Ruling 17 (iso-linux's bench on df8356d: a confined stat +20% warm and +354% cold, a 12-deep judge +1222%,
        os.walk 16x): the stat family's and os.DirEntry's network check, Blender's own lookups for a script, and
        resolve's check of each component against the automounter run only with Strict script protection. Otherwise
        each wrapper passes the call straight on by vectorcall (no tuple, no bound method), so a lookup costs as in
        2.0.1; a pipe waited for and a lookup that reads a folder's names keep their checks either way."""
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        fast = body("bpy_angelo_guard_confine_wrap_lookup.cc", "lookup_fastcall")
        self.assertIn("if ((!wrap.local_pipe_only && !wrap.reads_names && !strict_lookups()) || !confined_now()) {", fast)
        self.assertLess(fast.index("PyObject_Vectorcall(wrap.original"), fast.index("lookup_call(index"))
        self.assertIn("(PyCFunction)(void (*)(void))lookup_fastcall, METH_FASTCALL | METH_KEYWORDS", lookup)
        self.assertNotIn("(PyCFunction)(void (*)(void))lookup_call,", lookup)
        checked = body("bpy_angelo_guard_confine_wrap_lookup.cc", "lookup_call")
        self.assertIn("if (named && !wrap.local_pipe_only && strict_lookups()) {", checked)
        self.assertLess(checked.index("strict_lookups()"), checked.index("network_lookup("))
        gate = body("bpy_angelo_guard_confine_wrap_lookup.cc", "lookup_refused_by_gate")
        self.assertLess(gate.index("strict_lookups()"), gate.index("network_lookup("))
        entry = (INTERN / "bpy_angelo_guard_confine_wrap_lookup_entry.cc").read_text(encoding="utf-8")
        method = entry[entry.index("PyObject *entry_method("):]
        method = method[:method.index("PyMethodDef entry_methods[]")]
        self.assertIn("if (!strict_lookups() || !confined_now()) {", method)
        self.assertLess(method.index("PyObject_Vectorcall(wrap.original"), method.index("entry_call(index"))
        self.assertIn("PyDescr_NewMethod(entry_type, &entry_methods[i])", entry)   # A descriptor, as the original.
        self.assertNotIn("PyInstanceMethod_New", entry)
        for name in ("is_dir", "is_file", "stat"):
            self.assertIn('{"%s", (PyCFunction)(void (*)(void))entry_method<' % name, entry)
        paths = (INTERN / "bpy_angelo_guard_confine_paths.cc").read_text(encoding="utf-8")
        self.assertIn("if (strict_lookups() && names_a_remote_file(candidate)) {", paths)
        folders = body("bpy_angelo_guard_confine_folders.cc", "Folders::judge")
        self.assertIn("if (names_a_remote_file(path)) {", folders)   # The whole name's text, always.
        remote = (INTERN / OPENER).read_text(encoding="utf-8")
        defined = remote[remote.index("bool strict_lookups()"):]
        self.assertLess(remote.index("bool strict_lookups()"), remote.index("#ifdef _WIN32\nbool utf8_to_wide"))
        self.assertIn("|| angelo_strict_script_protection();", defined[:200])
        # Installed only with the switch on (iso-linux on b55b844: the pass-through wrapper alone was +0.08 us a stat,
        # script or not): with it off os.stat and the rest are CPython's own.
        self.assertIn("return !wrap.local_pipe_only && !wrap.reads_names;", body(
            "bpy_angelo_guard_confine_wrap_lookup.cc", "strict_only"))
        install = body("bpy_angelo_guard_confine_wrap_lookup.cc", "install_wrappers_lookup")
        self.assertLess(install.index("install_lookup_wraps(false);"), install.index("if (strict_lookups()) {"))
        self.assertLess(install.index("if (strict_lookups()) {"), install.index("install_lookup_wraps(true);"))
        self.assertLess(install.index("install_lookup_wraps(true);"), install.index("install_wrappers_entry();"))
        late = body("bpy_angelo_guard_confine_wrap_lookup.cc", "install_strict_lookups")
        for call in ("replace_references_begin();", "install_lookup_wraps(true);", "install_wrappers_entry();",
                     "replace_references_end();"):
            self.assertIn(call, late)
        # Every change of the flag (the C setter, the preference's sync) goes through store_strict, which installs or
        # removes the wrappers; both callers refuse a confined thread first.
        store = body("bpy_angelo_guard_strict.cc", "store_strict")
        self.assertLess(store.index("install_strict_lookups();"), store.index("remove_strict_lookups();"))
        for caller in ("set_strict", "sync_strict"):
            code = body("bpy_angelo_guard_strict.cc", caller)
            self.assertLess(code.index("refuse_if_confined("), code.index("store_strict("), caller)
        # Off again: every attribute and reference the install replaced holds the original (one walk); the original
        # is kept, so a copy of a wrapper something held still calls through.
        raw_lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        removal = raw_lookup[raw_lookup.index("void remove_strict_lookups()"):]
        removal = removal[:removal.index("\n}\n")]
        for call in ("replace_references_begin();",
                     "put_back(wrap.installed_in, wrap.name, wrap.wrapper, wrap.original);",
                     'put_back("os", wrap.name, wrap.wrapper, wrap.original);',
                     "replace_references(wrap.wrapper, wrap.original);", "Py_CLEAR(wrap.wrapper);",
                     "remove_wrappers_entry();", "replace_references_end();"):
            self.assertIn(call, removal)
        self.assertNotIn("Py_CLEAR(wrap.original)", removal)
        raw_entry = (INTERN / "bpy_angelo_guard_confine_wrap_lookup_entry.cc").read_text(encoding="utf-8")
        entry = raw_entry[raw_entry.index("void remove_wrappers_entry()"):]
        entry = entry[:entry.index("\n}\n")]
        self.assertIn("PyDict_SetItemString(wrap.installed->tp_dict, wrap.name, wrap.original);", entry)
        self.assertNotIn("Py_CLEAR(wrap.original)", entry)

    def test_a_region_start_reads_the_automounter_again_only_after_a_change(self):
        """Ruling 17 (iso-linux on b55b844: a script-left callback +7.9%, about 12 us a region): a region's start with
        the baseline taken copies nothing and stats no map file unless the mount table signalled a change (Linux, one
        poll) or a second has passed; the full read then knows the signal was taken."""
        system = (INTERN / "bpy_angelo_guard_confine_automount_system.cc").read_text(encoding="utf-8")
        update = system[system.index("std::shared_ptr<const Table> update(const bool region)"):]
        update = update[:update.index("\n}\n")]
        settled = update.index("if (state.table && !state.roots_changed && state.has_baseline) {")
        self.assertLess(settled, update.index("seen = state;"))
        fast = update.index("if (!mounts_signalled && now - last_read.load(std::memory_order_relaxed) < 1000) {")
        self.assertLess(update.index("mounts_signalled = mount_table_changed();"), fast)
        self.assertLess(fast, update.index("std::any_of(seen.stamps.begin()"))   # No map file stat'd before it.
        self.assertIn("if (mounts_signalled || mount_table_changed()) {", update)

    def test_the_ownership_layer_is_installed_only_with_strict_script_protection(self):
        """Head 20 read R-4 (Ruling 17): the number-ownership layer's wrappers (the makers, FileIO.close, socket.detach,
        the descriptor wraps that close, replace, move or lock, the handle wraps, _overlapped's handle judges) are
        installed only with Strict script protection and removed when it turns off; off, nothing is owned and a
        descriptor or handle that is not a file's passes, as in 2.0.1. _overlapped's network refusals stay."""
        io = (INTERN / "bpy_angelo_guard_confine_wrap_io.cc").read_text(encoding="utf-8")
        made = (INTERN / "bpy_angelo_guard_confine_made.cc").read_text(encoding="utf-8")
        for name in ("dup", "dup2", "close", "lseek", "set_blocking", "set_inheritable", "lockf", "setmode",
                     "locking", "eventfd_read", "eventfd_write", "timerfd_gettime", "timerfd_settime"):
            row = io[io.index('"%s", {' % name):]
            self.assertIn(", true},", row[:row.index("\n")], name)
        for name in ("read", "write", "sendfile", "ftruncate", "_fcopyfile", "posix_fallocate"):
            row = io[io.index('"%s", {' % name):]
            self.assertNotIn("true}", row[:row.index("\n")], name)   # 2.0.1's judges: always.
        wrappers = io[io.index("int install_wrappers_io()"):]
        self.assertLess(wrappers.index("install_io_wraps(false);"), wrappers.index("if (strict_lookups()) {"))
        strict_io = io[io.index("void install_strict_io()"):]
        strict_io = strict_io[:strict_io.index("\n}\n")]
        for call in ("install_io_wraps(true);", "put_in_strict(wrap.module, wrap.name, wrapper, wrap.original);",
                     "install_overlapped_methods(true);"):
            self.assertIn(call, strict_io)
        self.assertIn("if (wrap.network == strict_part) {", io)   # Network refusals in the always part.
        makers = made[made.index("void install_makers()"):]
        self.assertIn("if (strict_lookups()) {\n    install_strict_makers();", makers[:300])
        strict_makers = made[made.index("void install_strict_makers()"):]
        for call in ('install_method("_io", "FileIO", "close"', 'install_method("_socket", "socket", "detach"',
                     "put_in_strict(wrap.module, wrap.name, wrapper, wrap.original);"):
            self.assertIn(call, strict_makers[:2500])
        self.assertIn("if (!ownership_tracked() || !confined_now() || !identity_of(number, handle, owned)) {", made)
        # Latched per run (S10): the first region's start reads the switch once; a switch turned on mid-run takes
        # effect from the next run, so no number the run made before it is refused.
        self.assertIn("tracked_this_run.store(strict_lookups(), std::memory_order_relaxed);", made)
        region = (INTERN / "bpy_angelo_guard_confine_region.cc").read_text(encoding="utf-8")
        self.assertLess(region.index("made_objects_forget(); /* No pipe"), region.index("ownership_run_began();"))
        # os.dup is a descriptor wrap and a maker: on every switch on (not only the first) the maker stands over the
        # descriptor wrap this switch just installed, never over a foreign function or itself, and the removal takes
        # the last installed off first (linux-c on f3e6a82: made_own_dup_works).
        self.assertIn("if ((current != wrap.cpython && !installed_by_strict(current)) || own_maker) {", made)
        self.assertIn("for (auto it = strict_installs.rbegin(); it != strict_installs.rend(); ++it) {", io)
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        strict_on = lookup[lookup.index("void install_strict_lookups()"):]
        self.assertIn("install_strict_io();", strict_on[:600])
        self.assertIn("install_strict_makers();", strict_on[:600])
        strict_off = lookup[lookup.index("void remove_strict_lookups()"):]
        self.assertIn("remove_strict_installs();", strict_off[:1500])
        # Off, a pipe, socket or handle that is no file's passes (2.0.1); on, only the script's own.
        for name, function in (("bpy_angelo_guard_confine_wrap_io.cc", "judge_descriptor"),
                               ("bpy_angelo_guard_confine_wrap_io.cc", "judge_handle")):
            self.assertIn("!ownership_tracked() ||", body(name, function), function)
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        self.assertIn("if (!ownership_tracked() || !descriptor_is_open(fd) || made_descriptor(fd)) {", hook)
        # The registry puts back every attribute, reference and type method it recorded.
        removal = io[io.index("void remove_strict_installs()"):]
        removal = removal[:removal.index("\n}\n")]
        for call in ("put_back(install.module.c_str(), install.name.c_str(), install.wrapper, install.original);",
                     "replace_references(install.wrapper, install.original);",
                     "PyDict_SetItemString(install.type->tp_dict, install.name.c_str(), install.original);"):
            self.assertIn(call, removal)
        # An open_osfhandle'd handle is owned through its descriptor only, and forgotten when that closes.
        self.assertIn('if (strcmp(wrap.name, "open_osfhandle") == 0 && first != nullptr && PyLong_Check(first)) {',
                      made)
        forget = made[made.index("void forget_descriptor(const int fd)"):]
        self.assertLess(forget.index("descriptor_handle(fd)"), forget.index("forget(owned_descriptors, fd);"))
        call = body("bpy_angelo_guard_confine_wrap_io.cc", "io_call")
        self.assertLess(call.index("descriptor_handle(closed)"), call.rindex("PyObject_Call(wrap.original"))

    def test_a_document_is_read_after_the_start_snapshot(self):
        """Head 20 read R-8: the start snapshot runs on its own thread; a document (the person's startup file, or any
        file read) waits for it first, so a share its own paths connect while it loads never counts as connected at
        start. The factory startup reads no file and never waits."""
        files = (INTERN.parents[1] / "windowmanager" / "intern" / "wm_files.cc").read_text(encoding="utf-8")
        read = files[files.index("bool WM_file_read(bContext *C,"):]
        self.assertLess(read.index("BPY_angelo_guard_connections_ready();"), read.index("wm_read_callback_pre_wrapper"))
        home = files[files.index("bool wm_homefile_read_ex(bContext *C,"):]
        self.assertLess(home.index("BPY_angelo_guard_connections_ready();"),
                        home.index("BKE_blendfile_read(filepath_startup, &params, &bf_reports);"))
        remote = (INTERN / OPENER).read_text(encoding="utf-8")
        self.assertIn("void remote_connections_ready()\n{\n  start_snapshot_wait();\n}", remote)

    def test_rna_calls_ask_the_thread_only_for_what_could_be_refused(self):
        """The 26th read's I1 (Ruling 17: the whole-window draw +9-10% with no script): every RNA function call a
        panel's draw makes (layout.prop, label, operator) and every argument it passes asked confined_now() first, which walks
        the Python stack once agent code exists. Each check now asks the property, type or function first and the
        thread only for one it could refuse; a layout's calls never ask it."""
        ops = (INTERN / "bpy_angelo_guard_confine_ops.cc").read_text(encoding="utf-8")
        def function(name):   # Its code, comments blanked (they name the calls they explain).
            code = ops[ops.index(name + "("):]
            return re.sub(r"/\*.*?\*/", "", code[:code.index("\n}\n")], flags=re.S)
        checks = (("const char *BPY_angelo_guard_path_property_refusal",
                   "(!path_subtype(prop) && place_kind(prop) == 0)"),
                  ("const char *BPY_angelo_guard_write_refusal", "preferences_pointer(ptr) ? PREFERENCES"),
                  ("const char *BPY_angelo_guard_value_refusal",
                   "!RNA_struct_is_a(ptr->type, RNA_FreestyleModuleSettings)"),
                  ("const char *BPY_angelo_guard_load_time_refusal",
                   'STREQ(RNA_property_identifier(prop), "use_module")'),
                  ("const char *BPY_angelo_guard_input_refusal", "!RNA_struct_is_a(ptr->type, RNA_WindowManager)"))
        for name, first in checks:
            code = function(name)
            self.assertLess(code.index(first), code.index("confined_now()"), name)
        refusal = function("const char *BPY_angelo_guard_input_refusal")
        self.assertLess(refusal.index("if (!BLI_fileops_confine_possible() && !WM_angelo_in_agent_modal()) {"),
                        refusal.index("RNA_struct_is_a(ptr->type, RNA_SpaceFileBrowser)"))

    def test_each_rna_check_asks_whether_anything_is_confined_first(self):
        """The Head 21 read's IC-1..IC-4 (Ruling 17; the Head 22 read's SHOULD-1): every per-call RNA check (each
        string, boolean, pointer and value a panel's draw passes, each keyframe and driver write) tests the atomic
        BLI_fileops_confine_possible() before any lookup it makes: a type test, a property's name or subtype, the
        place table, the value's text or the thread. Only null checks may come before it."""
        def body(path, signature):   # The function's code, comments blanked (they name the calls they explain).
            text = path.read_text(encoding="utf-8")
            code = text[text.index(signature):]
            return re.sub(r"/\*.*?\*/", "", code[:code.index("\n}\n")], flags=re.S)
        lookups = ("RNA_struct_is_a(", "RNA_property_identifier(", "RNA_property_subtype(", "path_subtype(",
                   "place_kind(", "preferences_pointer(", "file_browser_pointer(", "leaves_its_folder(",
                   "confined_now()", "BPY_angelo_guard_write_refusal(", "BPY_angelo_guard_value_refusal(")
        ops = INTERN / "bpy_angelo_guard_confine_ops.cc"
        checks = ((ops, "const char *BPY_angelo_guard_path_property_refusal("),
                  (ops, "const char *BPY_angelo_guard_write_refusal("),
                  (ops, "const char *BPY_angelo_guard_value_refusal("),
                  (ops, "const char *BPY_angelo_guard_load_time_refusal("),
                  (ops, "const char *BPY_angelo_guard_input_refusal("),
                  (INTERN / "bpy_angelo_guard_confine_output.cc", "const char *BPY_angelo_guard_output_path_refusal("),
                  (INTERN / "bpy_rna.cc", "static bool angelo_write_refused("),
                  (INTERN / "bpy_rna.cc", "static bool angelo_value_refused("))
        for path, signature in checks:
            code = body(path, signature)
            code = code[code.index("{"):]   # The body, past the parameter list.
            gate = code.index("!BLI_fileops_confine_possible()")
            used = [name for name in lookups if name in code]
            self.assertTrue(used, signature)
            for name in used:
                self.assertLess(gate, code.index(name), (signature, name))

    def test_a_dos_device_is_no_link_and_resolves_as_itself(self):
        """build3 head 23: os.devnull (`nul`) was refused to a confined script on Windows, by the link walk as a link it
        can't tell and by resolve as unresolvable (a device has no final name). A DOS device in the `\\.\` namespace
        is Safe for the walk and resolves as itself; the gtest a_dos_device_is_no_link checks the spellings."""
        reparse = (INTERN / "bpy_angelo_guard_confine_reparse.cc").read_text(encoding="utf-8")
        walk = reparse[reparse.index("links::Verdict links_walk("):]
        self.assertLess(walk.index("if (dos_device(full)) {"), walk.index("no_reparse_on_the_way("))
        device = reparse[reparse.index("bool dos_device("):]
        device = device[:device.index("\n}\n")]
        for name in ('L"NUL"', 'L"CON"', 'L"CONIN$"', 'L"CONOUT$"'):
            self.assertIn(name, device)
        # Not a printer or a port: those can be mapped to a network printer or redefined (the Head 21 read's ESC-1).
        for name in ('L"PRN"', 'L"AUX"', 'L"COM', 'L"LPT'):
            self.assertNotIn(name, device)
        paths = (INTERN / "bpy_angelo_guard_confine_paths.cc").read_text(encoding="utf-8")
        resolve = paths[paths.index("std::optional<std::string> resolve(const std::string &path, int /*dir_fd*/)"):]
        self.assertLess(resolve.index("if (dos_device(full)) {"), resolve.index("open_any("))
        cases = (INTERN.parents[3] / "tests" / "python" / "angelo_agent").joinpath(
            "confinement_lookup_cases.py").read_text(encoding="utf-8")
        # The audit probe resumes after a function that crashes it, keeping what finished (twelve Windows handle
        # functions crashed on build3 head 23, one restart each, past a fixed cap).
        self.assertIn('elif kind == "DONE":', cases)
        self.assertIn("crashed.append(started)", cases)
        self.assertNotIn("too many crashes", cases)

    def test_a_handles_inheritance_is_changed_only_as_a_descriptors_is(self):
        """build3 head 25b's inventory: nt.set_handle_inheritable (whether a program Angelo starts inherits a handle)
        is set_inheritable's handle form. Wrapped as it is with Strict script protection (a change: the script's own
        handle, or a file's it may write), in nt and in os's copy of it."""
        io = (INTERN / "bpy_angelo_guard_confine_wrap_io.cc").read_text(encoding="utf-8")
        handles = io[io.index("static HandleWrap handle_wraps[] = {"):]
        handles = handles[:handles.index("\n};\n")]
        self.assertIn('{"nt", "set_handle_inheritable", {0, -1}, true, nullptr},', handles)
        strict = io[io.index("void install_strict_io()"):]
        strict = strict[:strict.index("\n}\n")]
        self.assertIn('if (strcmp(wrap.module, "nt") == 0) {\n        put_in_strict("os", wrap.name, wrapper, wrap.original);',
                      strict)
        self.assertIn('{{"posix", "nt"}, "set_inheritable", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},', io)

    def test_a_confined_script_starts_no_program(self):
        """Ruling 19 dropped the command floor from 2.1.0; the 2.0.1 rule stays: every process event a confined thread
        raises is refused as PROGRAMS, whatever it starts."""
        hook = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        # Every audited route that starts, ends or reads another program (the Head 22 read's SHOULD-2: the whole table).
        for event in ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                      "os.forkpty", "os.startfile", "os.startfile/2", "_winapi.CreateProcess", "_winapi.OpenProcess",
                      "_winapi.TerminateProcess", "_wmi.exec_query", "pty.spawn", "webbrowser.open"):
            self.assertIn('{"%s", {Kind::Process}}' % event, hook)
        self.assertIn("case Kind::Process:\n      return refuse(PROGRAMS, name);", hook)
        self.assertNotIn("command_floor", hook)   # The floor is out (Ruling 19).
        # Foreign code, which can start anything: the whole ctypes and cffi families are Native, refused as NATIVE
        # (only a trusted DLL folder an installed package adds passes).
        for prefix in ("ctypes.", "_ctypes.", "cffi."):
            self.assertIn('{"%s", Kind::Native}' % prefix, hook)
        native = hook[hook.index("case Kind::Native:"):]
        native = native[:native.index("case Kind::Network:")]
        self.assertIn("return refuse(NATIVE, name);", native)
        self.assertEqual(native.count("return 0;"), 1)
        self.assertIn('strcmp(name, "os.add_dll_directory") == 0 && trusted_folder(args)', native)
        # Routes that raise no audit event: _posixsubprocess.fork_exec (subprocess and multiprocessing's spawn call it
        # after their own audit; a direct call raises none), refused before the original runs, and installed.
        wrap = (INTERN / "bpy_angelo_guard_confine_wrap.cc").read_text(encoding="utf-8")
        fork = wrap[wrap.index("static PyObject *wrapped_fork_exec("):]
        fork = fork[:fork.index("\n}\n")]
        self.assertLess(fork.index('if (confined_now()) {\n    refuse(PROGRAMS, "fork_exec");\n    return nullptr;'),
                        fork.index("call_original(FORK_EXEC"))
        self.assertIn('{{"_posixsubprocess", nullptr}, "fork_exec", FORK_EXEC, {"fork_exec", WRAP(wrapped_fork_exec)',
                      wrap)
        # And WMI's query refused by its own wrapper too, whatever the audit table says.
        native_wraps = (INTERN / "bpy_angelo_guard_confine_wrap_native.cc").read_text(encoding="utf-8")
        self.assertIn('{"_wmi", "exec_query", &PROGRAMS, nullptr}', native_wraps)

    def test_closes_cost_nothing_while_no_script_owns_a_number(self):
        """Ruling 17: FileIO.close (every io object's close) and socket.detach are replaced to forget a number a script
        owned; while no script owns one (between scripts, always), each passes straight to the original, and the
        ownership checks answer without a lock."""
        made = (INTERN / "bpy_angelo_guard_confine_made.cc").read_text(encoding="utf-8")
        for function, original in (("fileio_close", "original_fileio_close"), ("socket_detach", "original_socket_detach")):
            code = made[made.index("static PyObject *%s(" % function):]
            fast = "if (!anything_owned.load()) {\n    return PyObject_CallOneArg(%s, self);" % original
            self.assertIn(fast, code[:400], function)
        for function in ("void forget(", "bool owns("):
            code = made[made.index(function):]
            self.assertLess(code.index("if (!anything_owned.load()) {"), code.index("owned_lock"), function)
        own = made[made.index("void own("):]
        self.assertLess(own.index("anything_owned.store(true);"), own.index("numbers.emplace("))
        cleared = made[made.index("void made_objects_forget()"):]
        self.assertIn("anything_owned.store(false);", cleared[:700])

    def test_realpath_asks_the_whole_path_first(self):
        """realpath, strict or not, in both path modules (and pathlib's resolve through os.path.realpath): wrapped as a
        lookup that follows every link, so the whole path is checked before its first lstat (iso-mac on 1a17d20:
        a missing /net made the strict one raise before any check)."""
        lookup = (INTERN / "bpy_angelo_guard_confine_wrap_lookup.cc").read_text(encoding="utf-8")
        for module in ("posixpath", "ntpath"):
            self.assertIn('{{"%s", nullptr}, "realpath", true, false, false, nullptr},' % module, lookup)

    def test_no_confined_move_carries_a_link(self):
        """Head-7 M6 (the reviewer's general fix, replacing the open window): a confined move of a folder that holds a
        link at any depth is refused, from Python (os.rename, which os.replace and shutil.move raise too) and from
        Blender's own moves; the folder's path is asked of the network check before anything looks it up."""
        source = (INTERN / "bpy_angelo_guard_confine_tree.cc").read_text(encoding="utf-8")
        for system, first in (("windows", "FindFirstFileExW("), ("posix", "fstatat(")):
            text = windows_text(source) if system == "windows" else source.split("#else", 1)[1]
            walk = text[text.index("TreeVerdict move_tree_verdict("):]
            self.assertLess(walk.index("network_lookup(path,"), walk.index(first), system)
        self.assertIn("IsReparseTagNameSurrogate(tag)", windows_text(source))   # A cloud placeholder isn't a link.
        self.assertIn("info.st_dev != self.st_dev", source)                    # Nor is a mount inside missed.
        hooked = body("bpy_angelo_guard_confine_hook.cc", "link_refused")   # Literals blanked: the raw text below.
        self.assertIn("move_tree_refusal(move_tree_verdict(*source, dir_fd))", hooked)
        raw = (INTERN / "bpy_angelo_guard_confine_hook.cc").read_text(encoding="utf-8")
        tail = raw[raw.index("static int link_refused("):]
        # os.rename (os.replace raises it too) and shutil.move's own event, before its rename and copy fallback.
        self.assertIn('const bool moving = strcmp(name, "os.rename") == 0 || strcmp(name, "shutil.move") == 0;', tail)
        self.assertLess(tail.index("if (source && !source->empty() && moving) {\n    if (const char *reason = "
                                   "move_tree_refusal("), tail.index("static bool innermost_is_agent()"))
        confine = body("bpy_angelo_guard_confine.cc", "move_refused_by_gate")
        self.assertIn("confined_now() && move_tree_verdict(from, -1) != TreeVerdict::Plain", confine)
        self.assertFalse((INTERN / "bpy_angelo_guard_confine_window.cc").exists())   # No lock between judge and open.

    def test_an_unreadable_walk_is_its_own_reason(self):
        """Re-read NIT: a path refused because where its links lead can't be told says so, not "private"."""
        for name, function in (("bpy_angelo_guard_confine_folders.cc", "judge"),
                               ("bpy_angelo_guard_confine_wrap_lookup.cc", "network_lookup")):
            code = body(name, function)
            self.assertIn("judged_unreadable = false;", code, function)
            unreadable = code.index("case links::Verdict::Unreadable:")
            self.assertLess(code.index("judged_unreadable = true;", unreadable), code.index("Refusal::Private", unreadable))
        refused = body("bpy_angelo_guard_confine.cc", "refuse")
        self.assertIn("judged_unreadable && reason == PRIVATE", refused)
        self.assertIn("judged_unreadable = false;", refused)   # Read once: never a later refusal's text.

    def test_every_reason_mapping_names_network(self):
        """Wherever a refusal becomes a reason (a `switch` or a chain of comparisons), `Network` is one: a new route
        that maps the others but not it would refuse with no reason, or allow."""
        missing, found = [], 0
        for path in sources():
            lines = windows_lines(path.read_text(encoding="utf-8", errors="replace"))
            for index, (number, line, _) in enumerate(lines):
                if not re.search(r"(case Refusal::ToolOutput|== Refusal::ToolOutput)", line):
                    continue
                found += 1
                window = " ".join(text for _, text, _ in lines[max(0, index - 8):index + 8])
                if "Refusal::Network" not in window:
                    missing.append((path.name, number))
        self.assertGreaterEqual(found, 7)   # The reader finds the mappings it guards.
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
