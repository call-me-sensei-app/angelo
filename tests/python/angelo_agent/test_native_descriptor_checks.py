# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A native function checks a descriptor it was given before the C runtime sees it.

On Windows the C runtime ends the process when ``_fstat64``, ``_read``, ``_write``, ``_lseeki64``, ``_dup``,
``_close`` or ``_get_osfhandle`` is given a descriptor that is negative, out of range or closed; POSIX returns
``EBADF``. A descriptor that reaches native code from Python is a number a caller chose, so a wrong one closed
Angelo where it should have refused a save or a Restore (2.0.0: ``BLO_write_file`` to a descriptor,
``writefile.cc``). ``BLI_file_descriptor_is_open`` (``BLI_fileops.hh``) asks without that risk.

What this reads from the sources of ``source/`` and ``intern/``, exactly (comments and string literals are
blanked first):

* A name counts as a descriptor someone else chose when it is
  - an integer parameter of the function (``int`` or another integer type, by value or by reference, with or
    without a default) or of a lambda written in it;
  - an integer local with no value of its own that is read before any is assigned, so a call filled it
    (``PyArg_ParseTuple(args, "i", &fd)``, a helper that takes it by reference; every name of ``int a, b;``);
  - an integer local or parameter assigned a value converted from a Python object (a call named
    ``Py..._As...``: ``PyLong_AsLong``, ``PyC_Long_AsI32``, ``PyObject_AsFileDescriptor``), or assigned straight
    from such a name; integer casts around either are read through.
* A call that gives such a name (plain, or inside an integer cast) as its descriptor argument to a C runtime
  descriptor function (``RUNTIME``, with at least the arguments that function takes) or to a function listed in
  ``PASS_THROUGH`` must come after ``BLI_file_descriptor_is_open(<that name>)`` in the text of the same function.
* A function in ``PASS_THROUGH`` hands its descriptor on unchecked by design (a wrapper, or one step of a stream
  whose opener checked), and the duty is then its callers': they are held to this for it as for the C runtime's
  own functions.
* Code no Windows build compiles is not read, because there the same call reports ``EBADF``: a branch whose
  condition asks for another system in each ``||`` alternative (``#ifndef WIN32``, ``!defined(_WIN32) && ...``,
  ``defined(__APPLE__)``), an ``#elif`` or ``#else`` after a branch that is the Windows test alone
  (``#ifdef WIN32``), and a source named for another system. A compiler test (``_MSC_VER``) or a feature test
  makes nothing off Windows, in its branch or its ``#else``.

What it does not see, so it is an aid and not a proof:

* Order is the order of the text, not of execution: a check inside one branch counts for a call after it.
* A descriptor kept in a struct or a class member (the function that stored it is the one to ask), one a function
  opened or duplicated itself, and one that reaches a call through anything but a name or an integer cast of one
  (arithmetic, a ternary, another function's return value that is not a Python conversion).
* A function whose ``{`` is not alone on its line but for a comment (the code style puts it there for every
  function with a body), and a condition that needs evaluating (``#if 0``, a macro that expands to a system test).
* A class's own function named like a C runtime one and called with as many arguments is read as the runtime's.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]

RUNTIME = ("fstat _fstat _fstat32 _fstat64 _fstati64 _fstat64i32 lseek _lseek _lseeki64 read _read write _write "
           "pread pwrite dup _dup dup2 _dup2 close _close fsync _commit fcntl _get_osfhandle ftruncate _chsize "
           "_chsize_s fdopen _fdopen fdatasync _setmode _filelength _filelengthi64 _locking flock fchmod _eof "
           "_tell _telli64 isatty _isatty _futime _futime32 _futime64 _fstat32i64 _wfdopen").split()

# Functions that give their descriptor to the C runtime unchecked, and why that is theirs to do.
PASS_THROUGH = {
    "BLI_fstat": "Blender's fstat under one name",
    "BLI_lseek": "Blender's lseek under one name",
    "BLI_read": "Blender's read under one name, called once per block of a file being read",
    "BLI_file_descriptor_size": "Blender's size of an open file",
    "BLI_mmap_open": "Blender's mapping of an open file",
    "BLI_filereader_new_file": "Blender's reader of an open file",
    "BLI_filereader_new_mmap": "Blender's reader of an open file",
    "BLO_file_reader_uncompressed_from_descriptor": "Blender's reader of an open file",
    "blo_filedata_from_file_descriptor": "Blender's read of an open file",
    "IMB_load_image_from_file_descriptor": "Blender's image read of an open file",
    "WM_checkpoint_descriptor_write": "one write of a snapshot stream; the capture that opened the stream checks",
    "WM_checkpoint_write_all": "the writes of a snapshot stream; the capture that opened the stream checks",
    "crash_out": "the crash handler's own log file, which it opened; a signal handler calls nothing it can avoid",
}
# How many arguments the C runtime's function takes, where it is more than one: a call with fewer is another
# function of that name (a class's own ``read(value)``).
ARGUMENTS = {name: count for count, names in (
    (2, "fstat _fstat _fstat32 _fstat64 _fstati64 _fstat64i32 _fstat32i64 dup2 _dup2 fcntl ftruncate _chsize "
        "_chsize_s fdopen _fdopen _wfdopen _setmode flock fchmod _futime _futime32 _futime64"),
    (3, "lseek _lseek _lseeki64 read _read write _write _locking"), (4, "pread pwrite"))
    for name in names.split()}
# Which arguments are descriptors, where it is not the first alone.
POSITIONS = {"dup2": (0, 1), "_dup2": (0, 1), "blo_filedata_from_file_descriptor": (2,)}
# Sources of one windowing system or one POSIX system, by name: never built on Windows.
OFF_WINDOWS = re.compile(r"Wayland|X11|Cocoa|SDL|_apple|_unix|_linux|\.mm$")

CHECK = "BLI_file_descriptor_is_open"
SUFFIXES = (".cc", ".c", ".hh", ".h", ".cpp", ".mm")
# An integer type a descriptor can be held in: ``int`` above all, and whatever converts to it without a word.
INTEGER = (r"(?:(?:unsigned|signed)\s+)?(?:int|long(?:\s+long)?|short|u?int(?:8|16|32|64)_t|u?intptr_t|"
           r"s?size_t|uint)")
PARAMETER = re.compile(r"\b(?:const\s+)?%s\s*&?\s*(\w+)\s*(?=[,)=])" % INTEGER)   # By value or reference; a default.
LAMBDA = re.compile(r"\]\s*\(([^()]*)\)")
DECLARED = re.compile(r"(?<![\w.>])(?:(?:const|static)\s+)*%s\s+(?=\w)" % INTEGER)
FROM_PYTHON = re.compile(r"\b_?Py\w*_As\w+\s*\(")   # PyLong_AsLong, PyNumber_AsSsize_t, PyC_Long_AsI32, ...
ASSIGNED = re.compile(r"\b(\w+)\s*=(?!=)\s*([^;=]+);")
CAST = re.compile(r"(?:static_cast\s*<[^<>]*>\s*\(\s*(\w+)\s*\)|%s\s*\(\s*(\w+)\s*\)|\(\s*%s\s*\)\s*(\w+))$"
                  % (INTEGER, INTEGER))
OPENING = re.compile(r"^(\s*)\{\s*(?://.*|/\*.*?\*/\s*)?$")
NOT_CODE = re.compile(r'''"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|/\*.*?\*/|//[^\n]*''', re.DOTALL)
CONTROL = re.compile(r"^(?:\}?\s*else\b|if\b|for\b|while\b|switch\b|do\b|try\b|catch\b)")
CALLED = re.compile(r"(?<![\w.>])(?:::)?(\w+)\s*\(")
DIRECTIVE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")
COMMENT = re.compile(r"/\*.*?\*/|//.*")
WIN32, POSIX = r"_?WIN32", r"__APPLE__|__linux__|__unix__|__FreeBSD__|__OpenBSD__|__NetBSD__"
ONLY_WIN32 = re.compile(r"[\s()]*(?:defined)?[\s()]*_?WIN32[\s()]*")


def _tested(macros, text, negated):
    """Whether ``text`` (one ``||`` alternative of a condition) tests one of ``macros``, negated or not."""
    under_not = r"!\s*(?:defined\s*\(\s*(?:%s)\s*\)|defined\s+(?:%s)\b|(?:%s)\b)" % (macros, macros, macros)
    if negated:
        return bool(re.search(under_not, text))
    return bool(re.search(r"\b(?:%s)\b" % macros, re.sub(under_not, " ", text)))


def _branch(kind, rest):
    """A condition as (no Windows build takes its branch, every Windows build takes it). The first only when each
    ``||`` alternative asks for a system that is not Windows (``!defined(WIN32)``, ``defined(__APPLE__)``, alone or
    with more); the second only when an alternative is the Windows test alone. A compiler (``_MSC_VER``) or a
    feature decides neither: Windows is built with more than one compiler."""
    text = {"ifdef": "defined(%s)", "ifndef": "!defined(%s)"}.get(kind, "%s") % COMMENT.sub("", rest).strip()
    alternatives = text.split("||")
    return (all(_tested(WIN32, part, True) or _tested(POSIX, part, False) for part in alternatives),
            any(ONLY_WIN32.fullmatch(part) for part in alternatives))


def off_windows(lines):
    """For each line, whether no Windows build compiles it: inside a branch whose condition asks for another
    system (``_branch``), or in an ``#elif`` or ``#else`` after a branch that every Windows build takes."""
    found, stack = [], []   # Each frame: [this branch is off Windows, a branch every Windows build takes came].
    for line in lines:
        match = DIRECTIVE.match(line)
        if match:
            kind, rest = match.groups()
            if kind in ("if", "ifdef", "ifndef"):
                stack.append(list(_branch(kind, rest)))
            elif kind == "elif" and stack:
                off, always = _branch(kind, rest)
                stack[-1] = [stack[-1][1] or off, stack[-1][1] or always]
            elif kind == "else" and stack:
                stack[-1] = [stack[-1][1], stack[-1][1]]
            elif kind == "endif" and stack:
                stack.pop()
        found.append(any(frame[0] for frame in stack))
    return found


def functions(lines):
    """Each function body in ``lines`` as (its name, its ``int`` parameters, the index of its first body line,
    its body lines): a signature followed by ``{`` alone on a line, to the ``}`` at that indentation."""
    for index, line in enumerate(lines):
        match = OPENING.match(line)
        if not match:
            continue
        start = index - 1
        while (start >= 0 and lines[start].strip() and not lines[start].lstrip().startswith(("#", "//"))
               and not lines[start].rstrip().endswith((";", "}", "{", "*/"))):
            start -= 1
        signature = " ".join(part.strip() for part in lines[start + 1:index])
        if not signature or "(" not in signature or CONTROL.match(signature):
            continue
        close, end = match.group(1) + "}", index + 1
        while end < len(lines) and not (lines[end].startswith(close) and lines[end][len(close):len(close) + 1] in ("", ";", " ")):
            end += 1
        name = re.findall(r"[\w~]+", signature.split("(")[0] or "?")[-1:]
        yield (name[0] if name else "?"), set(PARAMETER.findall(signature)), index + 1, lines[index + 1:end]


def arguments(text, start):
    """The top-level arguments of the call whose ``(`` is at ``text[start]``."""
    depth, parts, current = 0, [], []
    for character in text[start:]:
        if character in "([{":
            depth += 1
            if depth == 1:
                continue
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                parts.append("".join(current).strip())
                return parts
        if character == "," and depth == 1:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    return parts


def code_only(text):
    """``text`` with its comments and its string and character literals blanked, every offset and line kept: a
    word in a comment is not a use of a name."""
    return NOT_CODE.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)


def plain(expression):
    """``expression`` without the integer casts around a name: ``int(fd)``, ``static_cast<int>(fd)``, ``(int)fd``."""
    expression = expression.strip()
    while True:
        match = CAST.fullmatch(expression)
        if not match:
            return expression
        expression = next(group for group in match.groups() if group)


def declared(joined):
    """The integer locals declared in a function body, every name of each declaration: (name, whether it is given
    a value there, the offset after its declaration)."""
    for match in DECLARED.finditer(joined):
        depth, parts, current, end = 0, [], [], len(joined)
        for offset in range(match.end(), len(joined)):
            character = joined[offset]
            if character in "([":
                depth += 1
            elif character in ")]":
                depth -= 1
            if depth < 0 or character in ";{":   # The end of the statement (or of a parameter list).
                end = offset
                break
            if character == "," and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(character)
        parts.append("".join(current))
        for part in parts:
            name = re.match(r"\s*[*&]?\s*(\w+)\s*(.?)", part)
            if name:
                yield name.group(1), name.group(2) in ("=", "(", "{"), end


def supplied(parameters, joined):
    """The names in a function body that hold a descriptor someone else chose (the module text says which)."""
    names, local = set(parameters), {}
    for match in LAMBDA.finditer(joined):
        names.update(PARAMETER.findall(match.group(1) + ")"))
    for name, valued, end in declared(joined):
        local.setdefault(name, (valued, end))
    for name, (valued, end) in local.items():   # No value of its own, and read before any is assigned: a call fills it.
        use = re.compile(r"\b%s\b" % re.escape(name)).search(joined, end)
        if not valued and use and not re.match(r"\s*=(?!=)", joined[use.end():]):
            names.add(name)
    targets = set(local) | names
    while True:   # A value converted from a Python object, and a name assigned straight from a supplied one.
        more = {target for target, value in ASSIGNED.findall(joined)
                if target in targets and (FROM_PYTHON.search(value) or plain(value) in names)} - names
        if not more:
            return names
        names |= more


def unchecked(path, text):
    """Every use in ``text`` that breaks the rule: (line, function, the call, the name)."""
    lines = text.split("\n")
    skipped, listed = off_windows(lines), set(RUNTIME) | set(PASS_THROUGH)
    for name, parameters, first, body in functions(lines):
        if name in PASS_THROUGH or name == CHECK:   # The check itself asks the runtime, guarded.
            continue
        joined, checked_at = code_only("\n".join(body)), {}
        parameters = supplied(parameters, joined)
        if not parameters:
            continue
        for match in re.finditer(re.escape(CHECK) + r"\(\s*(\w+)\s*\)", joined):
            checked_at.setdefault(match.group(1), match.start())
        for match in CALLED.finditer(joined):
            if match.group(1) not in listed:
                continue
            line = first + joined.count("\n", 0, match.start())
            if skipped[line]:
                continue
            given = arguments(joined, match.end() - 1)
            if len(given) < ARGUMENTS.get(match.group(1), 1):
                continue
            for position in POSITIONS.get(match.group(1), (0,)):
                argument = plain(given[position]) if position < len(given) else None
                if argument in parameters and not checked_at.get(argument, len(joined)) < match.start():
                    yield line + 1, name, match.group(1), argument


def sources(root=ROOT):
    for folder in ("source", "intern"):
        for path in sorted((root / folder).rglob("*")):
            relative = path.relative_to(root).as_posix()
            if (path.suffix in SUFFIXES and path.is_file() and "/tests/" not in relative
                    and not path.stem.endswith("_test") and not OFF_WINDOWS.search(path.name)):
                yield relative, path.read_text(encoding="utf-8", errors="replace")


class RuleTests(unittest.TestCase):
    """The reading itself, on small sources."""

    def found(self, text):
        return [(name, call, argument) for _, name, call, argument in unchecked("x.cc", text)]

    def test_a_parameter_given_to_the_runtime_unchecked_is_found(self):
        text = "bool stamp(const int fd, Stamp &stamp)\n{\n  return _fstat64(fd, &stamp.info) == 0;\n}\n"
        self.assertEqual(self.found(text), [("stamp", "_fstat64", "fd")])

    def test_a_check_before_the_call_satisfies_it_and_one_after_does_not(self):
        before = ("bool stamp(const int fd)\n{\n  if (!BLI_file_descriptor_is_open(fd)) {\n    return false;\n  }\n"
                  "  return BLI_lseek(fd, 0, SEEK_SET) == 0;\n}\n")
        after = "bool stamp(const int fd)\n{\n  return BLI_lseek(fd, 0, SEEK_SET) == 0 && BLI_file_descriptor_is_open(fd);\n}\n"
        self.assertEqual(self.found(before), [])
        self.assertEqual(self.found(after), [("stamp", "BLI_lseek", "fd")])

    def test_a_check_of_another_name_does_not_count(self):
        text = ("bool copy(int from, int to)\n{\n  if (!BLI_file_descriptor_is_open(from)) {\n    return false;\n  }\n"
                "  return _dup2(from, to) == 0;\n}\n")
        self.assertEqual(self.found(text), [("copy", "_dup2", "to")])

    def test_only_the_descriptor_argument_counts(self):
        text = "int64_t seek(FileReader *reader, int64_t offset, int whence)\n{\n  return BLI_lseek(reader->filedes, offset, whence);\n}\n"
        self.assertEqual(self.found(text), [])

    def test_a_wrapped_call_and_a_later_argument_are_read(self):
        text = ("FileData *open(const char *path,\n               const int descriptor)\n{\n"
                "  return blo_filedata_from_file_descriptor(path,\n                                           nullptr,\n"
                "                                           descriptor);\n}\n")
        self.assertEqual(self.found(text), [("open", "blo_filedata_from_file_descriptor", "descriptor")])

    def test_code_built_only_off_windows_is_not_read(self):
        text = ("bool stamp(const int fd)\n{\n#ifdef WIN32\n  return _get_osfhandle(fd) != -1;\n#else\n"
                "  struct stat info;\n  return fstat(fd, &info) == 0;\n#endif\n}\n")
        self.assertEqual(self.found(text), [("stamp", "_get_osfhandle", "fd")])
        only = "#ifndef WIN32\nbool stamp(const int fd)\n{\n  struct stat info;\n  return fstat(fd, &info) == 0;\n}\n#endif\n"
        self.assertEqual(self.found(only), [])

    def test_a_pass_through_is_exempt_and_its_callers_are_not(self):
        text = ("int64_t BLI_read(int fd, void *buf, size_t nbytes)\n{\n  return read(fd, buf, nbytes);\n}\n\n"
                "bool header(const int file)\n{\n  char bytes[4];\n  return BLI_read(file, bytes, 4) == 4;\n}\n")
        self.assertEqual(self.found(text), [("header", "BLI_read", "file")])

    def test_a_member_or_a_local_is_not_a_parameter(self):
        text = ("bool Reader::fill(const size_t bytes)\n{\n  const int copy = _dup(descriptor_);\n"
                "  return _read(copy, data_, unsigned(bytes)) >= 0;\n}\n")
        self.assertEqual(self.found(text), [])

    def test_a_local_that_python_filled_is_a_descriptor_someone_chose(self):
        parsed = ("static PyObject *request(PyObject * /*self*/, PyObject *args)\n{\n  int borrowed;\n"
                  "  if (!PyArg_ParseTuple(args, \"iU:request\", &borrowed, &nonce)) {\n    return nullptr;\n  }\n"
                  "  owner->descriptor = _dup(borrowed);\n  Py_RETURN_NONE;\n}\n")
        self.assertEqual(self.found(parsed), [("request", "_dup", "borrowed")])
        helper = ("static PyObject *scan(PyObject * /*self*/, PyObject *args)\n{\n  int descriptor;\n"
                  "  if (!integer_value(descriptor_object, descriptor, false)) {\n    return nullptr;\n  }\n"
                  "  return PyLong_FromLongLong(_lseeki64(descriptor, 0, SEEK_CUR));\n}\n")
        self.assertEqual(self.found(helper), [("scan", "_lseeki64", "descriptor")])
        converted = ("static bool borrow(PyObject *result, int &fd)\n{\n  const long descriptor = PyLong_AsLong(result);\n"
                     "  fd = int(descriptor);\n  struct _stat64 info;\n  return _fstat64(fd, &info) == 0;\n}\n")
        self.assertEqual(self.found(converted), [("borrow", "_fstat64", "fd")])

    def test_an_object_python_filled_is_not_a_descriptor(self):
        text = ("static PyObject *release(PyObject * /*self*/, PyObject *args)\n{\n  PyObject *capsule;\n"
                "  if (!PyArg_ParseTuple(args, \"O\", &capsule)) {\n    return nullptr;\n  }\n  return close(capsule);\n}\n")
        self.assertEqual(self.found(text), [])

    def test_a_lambda_parameter_is_read_as_a_parameter(self):
        text = ("bool encode(Writer &writer)\n{\n  writer.sink = [](int descriptor, const void *data, size_t size) {\n"
                "    return _write(descriptor, data, unsigned(size));\n  };\n  return true;\n}\n")
        self.assertEqual(self.found(text), [("encode", "_write", "descriptor")])

    def test_windows_code_with_another_negated_condition_is_read(self):
        text = ("#if defined(WIN32) && !defined(NDEBUG)\nbool console(const int fd)\n{\n  return _isatty(fd) != 0;\n}\n#endif\n")
        self.assertEqual(self.found(text), [("console", "_isatty", "fd")])
        off = "#if !defined(_WIN32) && !defined(NDEBUG)\nbool console(const int fd)\n{\n  return isatty(fd) != 0;\n}\n#endif\n"
        self.assertEqual(self.found(off), [])

    def test_a_branch_for_another_compiler_is_still_windows_code(self):
        """``_MSC_VER`` says which compiler, not which system: neither its branch nor its ``#else`` is off Windows."""
        negated = ("bool f(const int fd)\n{\n#ifndef _MSC_VER\n  return false;\n#else\n"
                   "  return _get_osfhandle(fd) != -1;\n#endif\n}\n")
        self.assertEqual(self.found(negated), [("f", "_get_osfhandle", "fd")])
        spelled = negated.replace("#ifndef _MSC_VER", "#if !defined(_MSC_VER)")
        self.assertEqual(self.found(spelled), [("f", "_get_osfhandle", "fd")])
        positive = ("bool f(const int fd)\n{\n#if defined(_MSC_VER)\n  return true;\n#else\n"
                    "  return _get_osfhandle(fd) != -1;\n#endif\n}\n")
        self.assertEqual(self.found(positive), [("f", "_get_osfhandle", "fd")])

    def test_only_a_branch_no_windows_build_takes_is_skipped(self):
        def branch(condition):
            return self.found("#if %s\nbool f(const int fd)\n{\n  return _isatty(fd) != 0;\n}\n#endif\n" % condition)
        for condition in ("!defined(WIN32)", "!defined(_WIN32) && defined(WITH_X)", "defined(__APPLE__)",
                          "defined(__linux__) || defined(__APPLE__)"):
            self.assertEqual(branch(condition), [], condition)
        for condition in ("!defined(__APPLE__)", "!defined(WIN32) || defined(WITH_X)", "defined(WITH_X)",
                          "defined(__APPLE__) || defined(WIN32)", "defined(WIN32) && !defined(_MSC_VER)"):
            self.assertEqual(branch(condition), [("f", "_isatty", "fd")], condition)
        chain = ("bool f(const int fd)\n{\n#if defined(WIN32) && defined(WITH_X)\n  return true;\n#else\n"
                 "  return _isatty(fd) != 0;\n#endif\n}\n")   # A Windows build without WITH_X takes the #else.
        self.assertEqual(self.found(chain), [("f", "_isatty", "fd")])

    def test_every_name_of_one_declaration_is_a_local(self):
        text = ("static PyObject *open_pair(PyObject * /*self*/, PyObject *args)\n{\n  int flags, fd;\n"
                "  if (!PyArg_ParseTuple(args, \"ii\", &flags, &fd)) {\n    return nullptr;\n  }\n"
                "  return PyLong_FromLong(_dup(fd));\n}\n")
        self.assertEqual(self.found(text), [("open_pair", "_dup", "fd")])

    def test_a_local_another_function_fills_is_a_descriptor_someone_chose(self):
        """Whatever the function is called: a local with no value of its own that is handed to a call was filled
        by that call."""
        for fill in ("integer_field(dict, \"fd\", fd)", "read_setting(&fd)", "parse(text, fd, &error)"):
            text = "static bool load(Reader &reader)\n{\n  int fd;\n  if (!%s) {\n    return false;\n  }\n  return _close(fd) == 0;\n}\n" % fill
            self.assertEqual(self.found(text), [("load", "_close", "fd")], fill)
        own = "static bool load(const char *path)\n{\n  int fd;\n  fd = BLI_open(path, O_RDONLY, 0);\n  return _close(fd) == 0;\n}\n"
        self.assertEqual(self.found(own), [])

    def test_a_value_converted_from_a_python_object_is_a_descriptor_someone_chose(self):
        for value in ("static_cast<int>(PyLong_AsLong(o))", "PyC_Long_AsI32(o)", "int(PyNumber_AsSsize_t(o, nullptr))",
                      "_PyLong_AsInt(o)"):
            text = "static bool take(PyObject *o)\n{\n  const int fd = %s;\n  return _close(fd) == 0;\n}\n" % value
            self.assertEqual(self.found(text), [("take", "_close", "fd")], value)

    def test_any_integer_parameter_counts_with_or_without_a_default(self):
        for parameter in ("int32_t fd", "const intptr_t fd", "long fd", "int fd = -1", "unsigned int fd"):
            text = "bool f(%s, const bool keep)\n{\n  return keep || _close(fd) == 0;\n}\n" % parameter
            self.assertEqual(self.found(text), [("f", "_close", "fd")], parameter)

    def test_a_brace_with_a_comment_after_it_opens_a_function(self):
        text = "bool f(const int fd)\n{ /* Windows only. */\n  return _close(fd) == 0;\n}\n"
        self.assertEqual(self.found(text), [("f", "_close", "fd")])

    def test_a_cast_at_the_call_is_read_through(self):
        for argument in ("int(fd)", "static_cast<int>(fd)", "(int)fd"):
            text = "bool f(const intptr_t fd)\n{\n  return _close(%s) == 0;\n}\n" % argument
            self.assertEqual(self.found(text), [("f", "_close", "fd")], argument)

    def test_a_word_in_a_comment_or_a_string_is_not_a_use(self):
        text = ("Image *load(const char *path)\n{\n  int file;\n  /* Is it on the file-system? */\n"
                "  printf(\"file %s\\n\", path);\n  file = BLI_open(path, O_RDONLY, 0);\n  close(file);\n  return nullptr;\n}\n")
        self.assertEqual(self.found(text), [])
        commented = "bool f(const int fd)\n{\n  /* BLI_file_descriptor_is_open(fd) would be asked here. */\n  return _close(fd) == 0;\n}\n"
        self.assertEqual(self.found(commented), [("f", "_close", "fd")])

    def test_a_call_with_fewer_arguments_than_the_runtimes_is_another_function(self):
        text = ("bool count(uint32_t &value)\n{\n  int32_t signed_value;\n  if (!read(signed_value)) {\n"
                "    return false;\n  }\n  value = uint32_t(signed_value);\n  return true;\n}\n")
        self.assertEqual(self.found(text), [])

    def test_the_wide_and_mixed_width_names_are_listed(self):
        for call in ("_fstat32i64(fd, &info)", "_wfdopen(fd, L\"rb\")"):
            text = "bool f(const int fd)\n{\n  struct _stat64 info;\n  return %s != 0;\n}\n" % call
            self.assertEqual([row[2] for row in self.found(text)], ["fd"], call)


class SourceTests(unittest.TestCase):
    def test_every_descriptor_a_function_was_given_is_checked_before_the_runtime_sees_it(self):
        found = ["%s:%d: %s gives its parameter %s to %s before %s(%s)" % (path, line, name, argument, call, CHECK, argument)
                 for path, text in sources() for line, name, call, argument in unchecked(path, text)]
        self.assertEqual(found, [], "\n" + "\n".join(found))

    def test_every_pass_through_is_a_function_of_the_sources(self):
        text = "\n".join(text for _, text in sources())
        missing = sorted(name for name in PASS_THROUGH if not re.search(r"\b%s\s*\(" % re.escape(name), text))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
