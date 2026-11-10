# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The undo steps Angelo's C++ names reach the person translated, in plain words.

Edit > Undo History draws each step's name through ``CTX_IFACE_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, name)``
(``space_topbar.cc``, ``undo_history_draw_menu``), so a name Angelo's C++ stores in a step is looked up in the
"Operator" context at draw time. For that lookup to find a translation, the literal must be marked
``CTX_N_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, "...")`` (Blender's extractor reads it in that context; upstream marks its
own the same way, ``interface_template_id.cc``), its file must be one the extractor keeps (``sources.CPP_FILES``), and
the entry must be in ``angelo.pot`` and every catalogue. "Checkpoint Restored" (1.0.9, the person's word is
"snapshot") is the case that exposed the class (evidence only; nothing below is tuned to it).

Which files: Angelo's own C++ (a path naming Angelo, or a file whose copyright line is Hyperbond Studio's) and the
upstream files Angelo edits for interface text (``sources.CPP_FILES``, which held no upstream undo name when this was
written: a name a rebase brings into one is reviewed then). Tests (``*_test.cc``, ``tests/``) store names nobody sees.
Upstream Blender's own unmarked names elsewhere stay as upstream has them. Python ``undo_push(message=...)`` names
follow the add-on's English-undo convention and are not read here. Dependency-free (no ``bpy``, no build).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))

from i18n import languages, po, sources  # noqa: E402

SOURCE = ROOT / "source"
SUFFIXES = (".c", ".cc", ".cpp", ".mm")
OPERATOR = "Operator"
_LITERAL = r'"(?:\\.|[^"\\\n])*"'
# The name argument of each call that stores an undo step's name: a step's ``name`` copied in (``STRNCPY`` of a
# step's or ``us->name``), or a push that names its step (``ED_undo_push``, ``ED_undo_grouped_push``,
# ``BKE_undosys_step_push*`` after the stack and context).
_NAMING = re.compile(
    r"\bSTRNCPY(?:_UTF8)?\(\s*[^,;]*(?:\bstep\b|\bus\b)[^,;]*?\bname\s*,\s*(?P<a>[^;]*?)\)\s*;"
    r"|\bED_undo_(?:grouped_)?push\(\s*[^,;]*,\s*(?P<b>[^;]*?)\)\s*;"
    r"|\bBKE_undosys_step_push\w*\(\s*[^,;]*,\s*[^,;]*,\s*(?P<c>[^;]*?)(?:,\s*[^,;]*)?\)\s*;", re.S)
_MARKED = re.compile(r"^CTX_N_\(\s*(?P<context>\w+)\s*,\s*(?P<text>(?:" + _LITERAL + r"\s*)+)\)$", re.S)


def _copyright(path):
    with open(path, encoding="utf-8", errors="replace") as file:
        return file.read(400)


def angelo_files():
    """Angelo's own C/C++ sources (a path naming Angelo, or Hyperbond Studio's copyright line) and the upstream ones
    it edits for interface text (``sources.CPP_FILES``)."""
    found = set(sources.CPP_FILES)
    for path in SOURCE.rglob("*"):
        if path.suffix not in SUFFIXES or path.name.endswith("_test" + path.suffix) or "tests" in path.parts:
            continue
        if "angelo" in path.name.lower() or "Hyperbond Studio" in _copyright(path):
            found.add(path)
    return sorted(found)


def undo_names(text):
    """``(line, argument)`` of every undo-step name ``text`` stores: the argument's source, whitespace folded."""
    out = []
    for match in _NAMING.finditer(text):
        argument = next(group for group in match.group("a", "b", "c") if group is not None)
        out.append((text.count("\n", 0, match.start()) + 1, " ".join(argument.split())))
    return out


def judged(argument):
    """``("unmarked", text)`` for a bare literal, ``(context, text)`` for a ``CTX_N_`` mark, None for a name taken
    from elsewhere (another step's or an operator's, translated where it was made)."""
    if re.fullmatch(r"(?:" + _LITERAL + r"\s*)+", argument):
        return "unmarked", "".join(re.findall(r'"((?:\\.|[^"\\\n])*)"', argument))
    marked = _MARKED.match(argument)
    if marked:
        context = {"BLT_I18NCONTEXT_OPERATOR_DEFAULT": OPERATOR}.get(marked.group("context"), marked.group("context"))
        return context, "".join(re.findall(r'"((?:\\.|[^"\\\n])*)"', marked.group("text")))
    return None


def findings(files):
    """Every Angelo undo-step name that would show untranslated: ``(where, problem, text)``."""
    template = {entry.key for entry in po.read(str(sources.POT))[1] if not entry.obsolete}
    catalogues = {language.po: {entry.key: entry.str for entry in
                                po.read(str(sources.LOCALE_DIR / "po" / f"{language.po}.po"))[1] if not entry.obsolete}
                  for language in languages.LANGUAGES}
    extracted = set(sources.CPP_FILES)
    out = []
    for path in files:
        for line, argument in undo_names(path.read_text(encoding="utf-8", errors="replace")):
            verdict = judged(argument)
            if verdict is None:
                continue
            where, (context, text) = "%s:%d" % (path.relative_to(ROOT).as_posix(), line), verdict
            if context != OPERATOR:
                out.append((where, "not CTX_N_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, ...)", text))
            elif path not in extracted:
                out.append((where, "file not in sources.CPP_FILES, so the extractor drops it", text))
            elif (OPERATOR, text) not in template:
                out.append((where, "not in angelo.pot with msgctxt Operator", text))
            else:
                out += [(where, "untranslated in " + name, text) for name, entries in catalogues.items()
                        if not entries.get((OPERATOR, text))]
    return out


SHAPES = ('void f(UndoStep *us, MemFileUndoStep *step, Capture *capture, bContext *C, UndoStack *stack) {\n'
          '  STRNCPY(step->step.name, "Plain Name");\n'
          '  STRNCPY(capture->before->step.name,\n'
          '          CTX_N_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, "Marked Name"));\n'
          '  STRNCPY_UTF8(us->name, "Upstream " "Joined");\n'
          '  STRNCPY(capture->before->step.name, capture->active->name);\n'
          '  ED_undo_push(C, "Pushed");\n'
          '  ED_undo_grouped_push(C, CTX_N_(BLT_I18NCONTEXT_ID_ID, "Grouped"));\n'
          '  BKE_undosys_step_push_with_type(stack, C, "Typed", type);\n'
          '  STRNCPY(window->title, "Not a step");\n'
          '}\n')


class UndoStepNameTests(unittest.TestCase):
    def test_angelos_undo_step_names_are_marked_and_translated(self):
        # The scanner reads every naming shape (held-out source, none of it Angelo's) and finds Angelo's files by
        # what they are: a path naming Angelo, its copyright line; never upstream's own.
        self.assertEqual([("unmarked", "Plain Name"), (OPERATOR, "Marked Name"), ("unmarked", "Upstream Joined"), None,
                          ("unmarked", "Pushed"), ("BLT_I18NCONTEXT_ID_ID", "Grouped"), ("unmarked", "Typed")],
                         [judged(argument) for _, argument in undo_names(SHAPES)])
        files = angelo_files()
        names = {path.relative_to(ROOT).as_posix() for path in files}
        self.assertIn("source/blender/editors/undo/angelo_undo.cc", names)
        self.assertIn("source/blender/editors/sculpt_paint/mesh/sculpt_main_undo.cc", names)
        self.assertNotIn("source/blender/editors/undo/ed_undo.cc", names)
        found = findings(files)
        self.assertEqual([], found, "Store the name as CTX_N_(BLT_I18NCONTEXT_OPERATOR_DEFAULT, \"...\"), list the file "
                                    "in sources.CPP_FILES and translate it in angelo.pot and every catalogue:\n"
                         + "\n".join("%s %s: %r" % item for item in found))


if __name__ == "__main__":
    unittest.main()
