# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A thin film on a Fresnel type that only OSL's closures make is compiled only into kernels that have OSL.

Cycles compiles its GPU kernels on the person's machine (Metal: the first render after an install or update). The
microfacet closure's Fresnel and albedo functions are inlined into the largest kernels, so a branch added there is
paid for by every GPU kernel, whether or not that kernel can ever take it. Blender 5.3 added a thin film to the
tinted dielectric (04c650019c8), a Fresnel type only OSL's MaterialX closures make. OSL is in the CPU kernel and
OptiX's OSL kernels; Metal, CUDA, HIP, oneAPI and OptiX's node kernel never make that type. On an M4 Pro the
first Metal render of the default scene went from about 80 s to 160-185 s (upstream #166491; measured 2026-11-13,
three rounds, in ``doc/angelo/performance.rst``). Behind ``__OSL__`` the same branches cost those kernels nothing
and change no picture.

Two things are checked, both read from the source, so no name is fixed here:

* which Fresnel types only OSL makes (every function that sets the type is called only from ``kernel/osl``). If a
  merge lets the node system make one, the first test says so: the guard must then come off, or GPU renders lose
  the film.
* that every read of such a type's thin film in ``kernel/closure`` sits under ``__OSL__``.

No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[3] / "intern/cycles/kernel"
SOURCES = (".h", ".cpp", ".cu", ".metal", ".mm")
SETS_TYPE = re.compile(r"\bfresnel_type\s*=\s*MicrofacetFresnel::(\w+)\s*;")
FUNCTION = re.compile(r"^ccl_device\w*\s+[\w\s:<>*&]*?\b(\w+)\s*\(", re.MULTILINE)
OSL_CONDITION = re.compile(r"^#\s*(ifdef\s+__OSL__|if\s+defined\s*\(\s*__OSL__\s*\))\s*(/[/*].*)?$")


def functions(text):
    """Every top-level kernel function of a header: (name, first line number, its lines)."""
    lines = text.splitlines()
    starts = [(text.count("\n", 0, found.start()), found.group(1)) for found in FUNCTION.finditer(text)]
    for index, (first, name) in enumerate(starts):
        last = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        yield name, first + 1, lines[first:last]


def setters():
    """Fresnel type -> the functions that set it, and for each the Fresnel struct it is given (or None)."""
    found = {}
    for path in sorted((KERNEL / "closure").glob("*.h")):
        for name, _first, lines in functions(path.read_text(encoding="utf-8")):
            body = "\n".join(lines)
            struct = re.search(r"ccl_private\s+(Fresnel\w+)\s*\*", body.split("{", 1)[0])
            for kind in SETS_TYPE.findall(body):
                found.setdefault(kind, {})[name] = struct.group(1) if struct else None
    return found


def callers(name):
    """Files that call a kernel function, relative to the kernel folder."""
    call = re.compile(r"(?<![\w>.])%s\s*\(" % re.escape(name))
    definition = re.compile(r"^ccl_device\w*\s+[\w\s:<>*&]*?\b%s\s*\(" % re.escape(name), re.MULTILINE)
    found = set()
    for path in sorted(KERNEL.rglob("*")):
        if path.suffix in SOURCES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(call.findall(text)) > len(definition.findall(text)):
                found.add(path.relative_to(KERNEL).as_posix())
    return found


def osl_only():
    """Fresnel type -> its struct, for the types every setter of which is called from ``kernel/osl`` alone."""
    found = {}
    for kind, by_function in setters().items():
        used = [callers(name) for name in by_function]
        if all(files and all(name.startswith("osl/") for name in files) for files in used):
            structs = {struct for struct in by_function.values() if struct}
            if structs:
                found[kind] = sorted(structs)
    return found


def under_osl(lines):
    """For each line: is it compiled only where ``__OSL__`` is defined?"""
    stack, state = [], []
    for line in lines:
        text = line.strip()
        if text.startswith("#"):
            directive = text[1:].strip()
            if directive.startswith("if"):
                stack.append(bool(OSL_CONDITION.match(text)))
            elif directive.startswith(("else", "elif")) and stack:
                stack[-1] = False   # The other side of an __OSL__ test is the kernel without it.
            elif directive.startswith("endif") and stack:
                stack.pop()
        state.append(any(stack))
    return state


def thin_film_reads(lines, struct):
    """Line numbers (from 1) that read the thin film through a pointer to ``struct``: the direct cast, and every
    variable or parameter declared as one, for as long as its block lasts."""
    direct = re.compile(r"\(\s*ccl_private\s+%s\s*\*\s*\)\s*[\w>-]+\s*\)\s*->\s*thin_film\b" % struct)
    declared = re.compile(r"ccl_private\s+%s\s*\*\s*(\w+)" % struct)
    found, live, parameters, depth = [], [], [], 0   # live: (name, the depth of the block it lives in)
    for number, line in enumerate(lines, 1):
        code = line.split("//", 1)[0]
        for name in declared.findall(code):
            if depth:
                live.append((name, depth))
            else:
                parameters.append(name)   # Of a function whose body has not opened yet.
        if direct.search(code) or any(re.search(r"\b%s\s*->\s*thin_film\b" % re.escape(name), code)
                                      for name, _block in live):
            found.append(number)
        for character in code:
            if character == "{":
                depth += 1
                if depth == 1:
                    live += [(name, 1) for name in parameters]
                    parameters = []
            elif character == "}":
                depth -= 1
                live = [(name, block) for name, block in live if depth >= block]
            elif character == ";" and depth == 0:
                parameters = []   # A declaration without a body.
    return found


class OslOnlyThinFilm(unittest.TestCase):
    def test_the_tinted_dielectric_is_still_made_by_osl_alone(self):
        found = osl_only()
        self.assertIn("DIELECTRIC_TINT", found,
                      "a Fresnel type that only OSL's closures made is now made elsewhere too (the node system?): "
                      "take the __OSL__ guard off its thin-film branches in closure/bsdf_microfacet.h, or kernels "
                      "without OSL render it without the film. Setters and their callers: %r"
                      % {kind: {name: sorted(callers(name)) for name in names}
                         for kind, names in setters().items()})
        made_by_nodes = {kind for kind in setters() if kind not in found}
        self.assertIn("GENERALIZED_SCHLICK", made_by_nodes, "the reading of callers is broken: the Glass and "
                      "Principled nodes make this type")

    def test_its_thin_film_is_read_only_where_osl_is_compiled_in(self):
        unguarded = []
        for kind, structs in osl_only().items():
            for path in sorted((KERNEL / "closure").glob("*.h")):
                lines = path.read_text(encoding="utf-8").splitlines()
                guarded = under_osl(lines)
                for struct in structs:
                    for number in thin_film_reads(lines, struct):
                        if not guarded[number - 1]:
                            unguarded.append("%s:%d (%s, %s): %s" % (path.name, number, kind, struct,
                                                                     lines[number - 1].strip()))
        self.assertEqual(unguarded, [], "every GPU kernel without OSL compiles these branches and can never take "
                         "them; put them under #ifdef __OSL__")

    def test_a_thin_glass_transmission_closure_has_no_fresnel_type(self):
        # The albedo function's thin-film test is part of one if/else chain (it compiles far faster than a chain
        # split in two). A generalized Schlick or F82 closure with a thin film falls through that chain, and must
        # then fail every later test of it. The last one asks for the thin-glass transmission closure, so that
        # closure must never carry such a Fresnel type: it is made in one function, with none, and stays there.
        makers = []
        for path in sorted(KERNEL.rglob("*")):
            if path.suffix in SOURCES:
                for name, _first, lines in functions(path.read_text(encoding="utf-8", errors="replace")):
                    body = "\n".join(lines)
                    if re.search(r"\btype\s*=\s*CLOSURE_BSDF_THIN_GLASS_TRANSMISSION_ID\s*;", body):
                        makers.append((name, SETS_TYPE.findall(body), bool(re.search(r"^ccl_device\w*\s+void\s",
                                                                                     lines[0]))))
        self.assertEqual(makers, [("bsdf_thin_glass_transmission_setup", ["NONE"], True)],
                         "made in one function that returns nothing, with no Fresnel type")

    def test_the_readers_see_what_they_are_given(self):
        lines = ["void f(ccl_private FresnelX *given)", "{", "  a = given->thin_film.thickness;", "}",
                 "void g()", "{", "  if (t) {", "    ccl_private FresnelX *local = cast;",
                 "    b = local->thin_film;", "  }", "  else {", "    ccl_private FresnelY *local = cast;",
                 "    c = local->thin_film;", "  }",
                 "  d = ((ccl_private FresnelX *)bsdf->fresnel)->thin_film.thickness;", "}"]
        self.assertEqual(thin_film_reads(lines, "FresnelX"), [3, 9, 15])
        self.assertEqual(thin_film_reads(lines, "FresnelY"), [13])
        self.assertEqual(under_osl(["a", "#ifdef __OSL__", "b", "#  ifdef OTHER", "c", "#  endif", "#else", "d",
                                    "#endif", "#if defined(__OSL__)", "e", "#endif", "#ifdef __SVM__", "f",
                                    "#endif"]),
                         [False, True, True, True, True, True, False, False, False, True, True, False, False, False,
                          False])


if __name__ == "__main__":
    unittest.main()
