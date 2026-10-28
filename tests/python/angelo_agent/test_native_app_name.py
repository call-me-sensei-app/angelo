# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Interface text calls the running app Angelo, never Blender.

Angelo is built on Blender, so "Blender" stays where it names something other than the program the person is
using: the Blender Python API, stock Blender and the settings Angelo imports from it, upstream releases, the Blender
project and its resources, the file format and its terms, and the stock components Angelo bundles unchanged
(``ALLOWED``). Everything else that names the running program (its Undo, its interface, its windows, its jobs, what
it did or refused) says Angelo.

Interface text here is what the localization scanner (``scripts/angelo/i18n``) treats as text a person reads:
every marked message and widget literal of the add-on and startup scripts, operator and panel labels and
descriptions, the English text UI modules show untranslated, the notes of the release being built (published
notes are a record of what shipped and are not reworded), and the C++ text Angelo adds: what upstream files Angelo
edits pass to Blender's translation macros, and the operator, RNA and report text of Angelo's own C++ files, less
what Blender's own catalogues translate (Blender's wording). Model-facing text (``sources.MODEL_FACING`` and literals
whose ``# i18n:`` reason says the model reads them) is not interface text.

Blender's own interface text follows the same rule (``test_blenders_own_interface_text_calls_the_running_app_angelo``):
every message of Blender's catalogues that names Blender and that its sources still show, and every literal in a UI
position of its sources (widget and property text, reports, poll messages, operator docstrings, labels and
descriptions, translation macros, RNA text, enum items), whether or not the catalogues know it yet. A single word is a
name the program gives itself (keymap, folder, audio device, driver identity) and is left out.

``ALLOWED`` lists contexts, never whole strings: each occurrence of the word must sit inside one of them. Text whose
wording is a reviewed exception outside those classes (a Blender data-model term in an importer, a .blend version
message) carries ``app-name: <reason>`` in a comment on its line, or alone on the line before the statement it
covers, so a new string never inherits an allowance silently.
"""

import ast
import functools
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))

from i18n import po, scan, sources  # noqa: E402

WORD = re.compile(r"\bblender\b", re.IGNORECASE)
# Blender's own sources spell identifiers in capitals (``BLENDER_EEVEE``, enum ``"BLENDER"``); their text says Blender.
UPSTREAM_WORD = re.compile(r"\b[Bb]lender\b")

# (pattern, the class of context it names). A match covers the occurrences of "Blender" inside it.
ALLOWED = tuple((re.compile(pattern), reason) for pattern, reason in (
    (r"\bBlender Python\b", "the scripting API's name, which stock Blender shares"),
    (r"\b(?:stock|upstream|installed) Blender\b", "stock Blender, a separate program"),
    (r"\bBlender v?(?:\d+(?:\.\d+)*|\{version\}|\{:?\d*[ds]?\}|%[sd])|\b(?:[Vv]ersions? of|newer|older|newest|future|later) Blender\b"
     r"|(?<![Tt]his )(?<![Yy]our )(?<!running )(?<!current )\bBlender [Vv]ersions?\b",
     "a Blender release: a file's or an add-on's Blender version"),
    (r"\b(?:based|built) on Blender\b|\bBlender base\b", "what Angelo is built on"),
    (r"\bBlender Foundation\b|\bSupport Blender\b|\bBlender (?:Manual|Developer \w+)\b"
     r"|\bBlender (?:engine )?(?:credits|licen[cs]e)\b",
     "the Blender project, its credits, licence and resources"),
    (r"\b(?:[Yy]our|[Mm]y|[Ee]arlier|[Nn]o|[Ii]mport) Blender [Ss]ettings\b|\bBlender's folder\b"
     r"|\b(?:set for|over from) Blender\b",
     "a stock Blender installation's settings, which Angelo imports"),
    (r"\bBlender (?:asset library|[Uu]nits?|library file)\b|\bBlender [Ff]iles?\b(?! (?:Browser|View))",
     "the Blender file format and its terms"),
    (r"\bBlender(?:'s)? [Ee]xtensions?(?: repository)?\b|\S*\bblender\.org\b\S*|\bBlender(?:'s)? official web-site\b"
     r"|\bBlender Website\b|\bBlender(?:'s)? source(?: code| repository| root path)?\b|\bBlender (?:libraries )?[Rr]epository\b",
     "the Blender project's sites, extensions platform and source code"),
    (r"\bBlender's (?:\w+ )?(?:importer|exporter|import default)\b",
     "a stock Blender importer or exporter Angelo bundles unchanged"),
))

MARKER = re.compile(r"(?:#|//|/\*)\s*app-name:\s*(\S.*?)\s*(?:\*/)?$")
# An ``# i18n:`` reason that says a model, never a person, reads the text.
MODEL_REASON = re.compile(r"\b(?:returned to the model|model reads)\b")
NATIVE_ATTRIBUTES = {"bl_label", "bl_description", "bl_category"}
_STRINGS = r'((?:"(?:\\.|[^"\\\n])*"\s*)+)'
# A C++ message Angelo adds to an upstream file goes through Blender's translation macros
# (doc/angelo/localization.rst); upstream's RNA and enum text in the same files is Blender's own wording.
_CPP_MESSAGE = re.compile(r'\b(?:CTX_)?(?:IFACE|TIP|RPT|N)_\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*,\s*)?' + _STRINGS)
# In Angelo's own C++ files, operator names and descriptions, RNA UI text and reports are interface text too.
_CPP_OWN = re.compile(r'->(?:name|description)\s*=\s*' + _STRINGS
                      + r'|\b(?:RNA_def_\w+_ui_(?:text|description)|BKE_reportf?|WM_reportf?)\((?:[^;"]|' + _STRINGS
                      + r')*?\);')
_CPP_PIECE = re.compile(r'"((?:\\.|[^"\\\n])*)"')


def offending(text):
    """The occurrences of the word in ``text`` that no allowed context covers, as ``(start, end)`` spans."""
    covered = [match.span() for pattern, _ in ALLOWED for match in pattern.finditer(text)]
    return [match.span() for match in WORD.finditer(text)
            if not any(start <= match.start() and match.end() <= end for start, end in covered)]


def _blender_catalogue():
    """Messages Blender's own catalogues translate: Blender's text, which Angelo does not word."""
    known = set()
    for name in ("zh_HANS", "ja"):  # The two most complete, as the extractor and the translation suite use.
        known.update(entry.id for entry in po.read(str(ROOT / "locale/po" / f"{name}.po"))[1])
    return known


def markers(source):
    """``{line: reason}`` for the lines a reviewer marked ``app-name: <reason>`` in a comment: the comment's own line,
    or, when the comment stands alone, the statement or argument that follows it (until its brackets close)."""
    lines = source.splitlines()
    found = {}
    for number, line in enumerate(lines, 1):
        match = MARKER.search(line)
        if not match:
            continue
        reason = match.group(1).strip()
        if not line.lstrip().startswith(("#", "//", "/*")):
            found[number] = reason
            continue
        depth = 0
        for following in range(number + 1, min(number + 16, len(lines) + 1)):
            found[following] = reason
            code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", lines[following - 1])
            depth += sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")
            if depth <= 0:
                break
    return found


def _span(node):
    return range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1)


def python_texts(path, relative, *, ui, known=frozenset()):
    """``(line, text)`` for each piece of interface text in one Python file, less the reviewed exceptions."""
    source = path.read_text(encoding="utf-8")
    _, marked = scan.scan(source, relative, net=False)
    tree = ast.parse(source)
    scan._parents(tree)
    exemptions, reviewed = scan._exemptions(source), markers(source)
    literals = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    excepted = {node.value for node in literals if any(line in reviewed for line in _span(node))}
    texts = [(line, message) for message, line, _ in marked if message not in known and line not in reviewed]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NATIVE_ATTRIBUTES for t in node.targets):
            texts += [(sub.lineno, sub.value) for sub in ast.walk(node.value)
                      if isinstance(sub, ast.Constant) and isinstance(sub.value, str)]
        elif (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets)
              and isinstance(node.value, ast.Dict)):  # An add-on's name and description in Preferences > Add-ons.
            texts += [(value.lineno, value.value) for key, value in zip(node.value.keys, node.value.values)
                      if isinstance(key, ast.Constant) and key.value in ("name", "description")
                      and isinstance(value, ast.Constant) and isinstance(value.value, str)]
        elif (ui and isinstance(node, ast.Constant) and isinstance(node.value, str) and scan._net_candidate(node)
              and not any(MODEL_REASON.search(exemptions.get(line, "")) for line in _span(node))):
            texts.append((node.lineno, node.value))  # English a UI module shows as is (stored outcomes, details).
    return [(line, text) for line, text in texts if text not in excepted]


def cpp_texts(path, known, *, own):
    """``(line, text)`` for each interface message of a C++ file that Blender's catalogues do not translate."""
    source = path.read_text(encoding="utf-8")
    texts = []
    for pattern in (_CPP_MESSAGE, _CPP_OWN) if own else (_CPP_MESSAGE,):
        for match in pattern.finditer(source):
            arguments = [match.group(1)] if pattern is _CPP_MESSAGE else [
                literal.group(1) for literal in re.finditer(_STRINGS, match.group(0))]
            for argument in arguments:  # Adjacent literals are one message, as the compiler joins them.
                text = "".join(_CPP_PIECE.findall(argument))
                if text not in known:
                    texts.append((source.count("\n", 0, match.start()) + 1, text))
    return texts


def _release_lines(path, *, current):
    """The release notes' lines of the release being built (``current``) or of the ones already published.

    Published notes are a record of what shipped and are not reworded; the release being built is Angelo's own
    product version (``angelo_identity.json``).
    """
    version = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))["version"]["label"]
    lines = set()
    for release in json.loads(path.read_text(encoding="utf-8"))["releases"]:
        if (release["version"] == version) == current:
            lines.update([release["summary"], *release["highlights"]])
            for section in release["sections"]:
                lines.update([section["heading"], *section["items"]])
    return lines


def interface_texts():
    """Every piece of interface text Angelo words, as ``(origin, text)``."""
    known = _blender_catalogue()
    found = []
    for path in sorted(sources.ADDON.rglob("*.py")):
        if path.stem in sources.MODEL_FACING:
            continue
        relative = path.relative_to(ROOT).as_posix()
        found += [(f"{relative}:{line}", text)
                  for line, text in python_texts(path, relative, ui=path.stem in sources.UI_MODULES)]
    for relative in sources.STARTUP_FILES:
        found += [(f"{relative}:{line}", text) for line, text in python_texts(ROOT / relative, relative, ui=True)]
    for relative in sources.SHARED_PYTHON_FILES:  # Blender's scripts Angelo edits: only what Blender does not word.
        source = (ROOT / relative).read_text(encoding="utf-8")
        _, marked = scan.scan(source, relative, net=False)
        found += [(f"{relative}:{line}", message) for message, line, _ in marked
                  if message not in known and line not in markers(source)]
    own = {path for suffix in ("cc", "mm") for path in (ROOT / "source/blender").rglob(f"*angelo*.{suffix}")}
    for path in sorted({*sources.CPP_FILES, *own}):  # Upstream files Angelo edits, and its own.
        relative = path.relative_to(ROOT).as_posix()
        found += [(f"{relative}:{line}", text) for line, text in cpp_texts(path, known, own=path in own)]
    notes = ROOT / "scripts/addons_core/angelo_agent/assets/release_notes.json"
    published, current = _release_lines(notes, current=False), _release_lines(notes, current=True)
    found += [(origin, message) for _, message, origin in sources.EXTRA_MSGIDS
              if message not in published or message in current]
    return found


UPSTREAM_DIRS = ("source", "intern", "scripts/startup", "scripts/modules", "scripts/addons_core")
UPSTREAM_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hh", ".mm", ".py")
# A literal and the literals adjacent to it, joined as the compiler or Python joins them; comments and docstrings skipped.
_UPSTREAM_TOKEN = re.compile(r'(?P<skip>//[^\n]*|/\*.*?\*/|"""(?:\\.|.)*?"""|\'\'\'(?:\\.|.)*?\'\'\'|(?<![\w"\'])#[^\n]*)'
                             r'|(?P<group>(?:[rbuRBU]{0,2}(?:"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')\s*)+)', re.S)
_UPSTREAM_PIECE = re.compile(r'"((?:\\.|[^"\\\n])*)"|\'((?:\\.|[^\'\\\n])*)\'')


def _unescape(text):
    return re.sub(r"\\(.)", lambda match: {"n": "\n", "t": "\t"}.get(match.group(1), match.group(1)), text)


@functools.lru_cache(maxsize=None)
def upstream_sources():
    """``(relative path, source)`` of Blender's own sources that name Blender or carry a reviewed marker."""
    found = []
    for folder in UPSTREAM_DIRS:
        for path in sorted((ROOT / folder).rglob("*")):
            relative = path.relative_to(ROOT).as_posix()
            if (path.suffix not in UPSTREAM_SUFFIXES or "angelo" in relative.lower() or "/tests/" in relative
                    or not path.is_file()):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            if UPSTREAM_WORD.search(source) or "app-name:" in source:
                found.append((relative, source))
    return tuple(found)


def upstream_texts(known):
    """``(origin, text)`` for each message of Blender's catalogues that names Blender and that Blender's own sources
    still show: the upstream interface text Angelo reworded or deliberately kept (with a reviewed marker)."""
    wanted = {message for message in known if WORD.search(message)}
    found = []
    for relative, source in upstream_sources():
            reviewed = markers(source)
            for match in _UPSTREAM_TOKEN.finditer(source):
                if match.group("skip") is not None:
                    continue
                text = _unescape("".join(a or b for a, b in _UPSTREAM_PIECE.findall(match.group("group"))))
                # A single word is a name the program gives itself (keymap, folder, audio device, driver identity),
                # not a sentence or label of its interface.
                if text in wanted and re.search(r"\s", text):
                    first = source.count("\n", 0, match.start()) + 1
                    if not any(line in reviewed for line in range(first, first + match.group(0).count("\n") + 1)):
                        found.append((f"{relative}:{first}", text))
    return found


UPSTREAM_PYTHON = ("scripts/startup", "scripts/addons_core", "intern/cycles/blender/addon")
UI_KEYWORDS = {"text", "name", "description", "title", "message", "heading", "placeholder", "confirm_text"}
UI_CALLS = {"iface_", "tip_", "rpt_", "n_", "pgettext", "pgettext_iface", "pgettext_tip", "pgettext_rpt", "pgettext_n",
            "poll_message_set", "label"}
_C_UI = re.compile(r'\b(?:CTX_)?(?:IFACE|TIP|RPT|N)_\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*,\s*)?' + _STRINGS
                   + r'|->(?:name|description)\s*=\s*' + _STRINGS
                   + r'|\b(?:RNA_def_\w+|BKE_reportf?|WM_reportf?|WM_global_reportf?)\((?:[^;"]|' + _STRINGS + r')*?\);'
                   + r'|\{\s*[^{}";]{1,80},\s*"[^"]*"\s*,\s*[^{}";]{1,60},\s*' + _STRINGS + r',\s*' + _STRINGS + r'\}')


def _python_ui_literals(source):
    """``(line, text)`` for the literals Blender shows from an upstream Python file: widget and property text, reports,
    poll messages, marked messages, operator labels and descriptions, and operator and panel docstrings."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.body and isinstance(node.body[0], ast.Expr) and any(
                isinstance(s, ast.Assign) and any(getattr(t, "id", None) == "bl_idname" for t in s.targets) for s in node.body):
            doc = node.body[0].value
            if isinstance(doc, ast.Constant) and isinstance(doc.value, str):
                found.append((doc.lineno, doc.value))  # Blender shows an operator's docstring as its description.
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", None) in NATIVE_ATTRIBUTES for t in node.targets):
            found += [(s.lineno, s.value) for s in ast.walk(node.value) if isinstance(s, ast.Constant) and isinstance(s.value, str)]
        elif isinstance(node, ast.Call):
            name = scan.function_name(node)
            values = [k.value for k in node.keywords if k.arg in UI_KEYWORDS]
            if name in UI_CALLS and node.args:
                values.append(node.args[0])
            if name == "report" and len(node.args) > 1:
                values.append(node.args[1])
            for value in values:
                found += [(s.lineno, s.value) for s in ast.walk(value) if isinstance(s, ast.Constant) and isinstance(s.value, str)]
    return found


def upstream_ui_texts():
    """``(origin, text)`` for interface text in Blender's own sources in UI positions, whether or not Blender's
    catalogues know it yet (newer upstream text, operator docstrings, reports that never reached the catalogue)."""
    found = []
    for relative, source in upstream_sources():
            reviewed = markers(source)
            if relative.endswith(".py"):
                if not relative.startswith(UPSTREAM_PYTHON):
                    continue
                try:
                    literals = _python_ui_literals(source)
                except SyntaxError:
                    continue
                spans = literals
            else:
                spans = []
                for match in _C_UI.finditer(source):
                    first = source.count("\n", 0, match.start()) + 1
                    for literal in re.finditer(_STRINGS, match.group(0)):
                        line = first + match.group(0).count("\n", 0, literal.start())
                        spans.append((line, _unescape("".join(_CPP_PIECE.findall(literal.group(1))))))
            for line, text in spans:
                if UPSTREAM_WORD.search(text) and re.search(r"\s", text) and line not in reviewed:
                    found.append((f"{relative}:{line}", text))
    return found


def _label(tree, class_name):
    return next(statement.value.value for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == class_name
                for statement in node.body if isinstance(statement, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "bl_label" for t in statement.targets))


class AppNameTests(unittest.TestCase):
    def test_interface_text_calls_the_running_app_angelo(self):
        texts = interface_texts()
        self.assertGreater(len(texts), 1000, "the scan found too little interface text to be meaningful")
        wrong = [f"{origin} {text!r}" for origin, text in texts if offending(text)]
        self.assertFalse(wrong, "Interface text that means the running app says Angelo. If the word names stock "
                                "Blender, its API, format, project or bundled parts, the context must be one of "
                                "ALLOWED (add a context class with its reason, never a whole string), or a reviewer "
                                "marks the line '# app-name: <reason>':\n" + "\n".join(wrong))

    def test_blenders_own_interface_text_calls_the_running_app_angelo(self):
        """Blender's own text in its sources (Preferences, operators, reports, importers) follows the same rule; the
        old wording stays only in Blender's catalogues, and the new is Angelo's (``sources.EXTRA_MSGIDS``)."""
        texts = upstream_texts(_blender_catalogue())
        texts += [entry for entry in upstream_ui_texts() if entry not in texts]
        self.assertGreater(len(texts), 50, "the scan found too little of Blender's text to be meaningful")
        wrong = [f"{origin} {text!r}" for origin, text in texts if offending(text)]
        self.assertFalse(wrong, "Blender's own text that means the running app says Angelo (add the new wording to "
                                "sources.EXTRA_MSGIDS for translation). If the word names stock Blender, its format, "
                                "project, versions or data model, the context must be one of ALLOWED, or a reviewer "
                                "marks the line 'app-name: <reason>':\n" + "\n".join(wrong))

    def test_contexts_that_mean_the_running_app_are_caught(self):
        for text in ("Undo in Blender reverses them; anything outside Blender can't be undone.",
                     "Scene edits have Blender Undo, external effects do not.", "Enable Blender's Global Undo first",
                     "Blender's interface stays locked while it runs", "Restart Blender to finish the update",
                     "Blender closed unexpectedly", "Blender ended the render early; see its Info log",
                     "Finish the active Blender tool first", "Models that work in Blender with you",
                     "BLENDER", "No bound Blender window", "Blender's menus are locked while Blender renders",
                     "Open Blender's settings to turn this on", "Blender settings changed; restart to apply",
                     "Blender's editors stay locked until it ends", "Blender's Asset Browser is busy",
                     "All of Blender is locked while it runs", "Blender features are locked during a job"):
            with self.subTest(text=text):
                self.assertTrue(offending(text))

    def test_contexts_that_name_stock_blender_stay(self):
        for text in ("Based on Blender {version}", "Blender Foundation and contributors · GPL", "Support Blender",
                     "Ran Blender Python", "Import your Blender settings?", "Use my Blender settings",
                     "No earlier Blender settings were found on this computer", "Import Blender Settings…",
                     "Add-ons, extensions, presets and other files that have the same name in Blender's folder",
                     "close to stock Blender", "Fixes from the upcoming Blender 5.2.3 and 5.3",
                     "Media Studio's Blender asset library", "Set its position to {position} (Blender units).",
                     "Blender's glTF exporter didn't write the file", "Blender's import default is unchanged",
                     "Open a window with information about Angelo and its Blender base", "Save it as a .blend file"):
            with self.subTest(text=text):
                self.assertEqual([], offending(text))

    def test_every_allowed_context_and_reviewed_exception_states_its_reason(self):
        for pattern, reason in ALLOWED:
            self.assertIsNone(pattern.search("Blender"), "a context is more than the bare word: " + pattern.pattern)
            self.assertGreater(len(reason.split()), 3, pattern.pattern)
        files = [(path.name, path.read_text(encoding="utf-8")) for path in [
            *sorted(sources.ADDON.rglob("*.py")),
            *(ROOT / relative for relative in (*sources.STARTUP_FILES, *sources.SHARED_PYTHON_FILES))]]
        for name, source in files + list(upstream_sources()):
            for line, reason in markers(source).items():
                with self.subTest(file=name, line=line):
                    self.assertGreater(len(reason.split()), 3, "say why this wording names Blender")

    def test_a_reviewed_exception_covers_only_its_own_line(self):
        source = ('A = n_("Blender\'s menus stay")  # app-name: names the stock interface kept beside Angelo\'s\n'
                  '# app-name: names the stock interface kept beside Angelo\'s\n'
                  'C = n_("Blender\'s editors stay")\n'
                  'B = n_("Blender\'s menus are locked")\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reviewed.py"
            path.write_text(source, encoding="utf-8")
            texts = [text for _, text in python_texts(path, path.name, ui=True)]
        self.assertEqual(texts, ["Blender's menus are locked"])

    def test_the_undo_step_is_named_like_the_operator_that_applies_a_change(self):
        """Undo History shows a script's step under ``script_errors.STEP``; it must stay the Apply operator's label."""
        ui = ast.parse((sources.ADDON / "ui.py").read_text(encoding="utf-8"))
        errors = ast.parse((sources.ADDON / "script_errors.py").read_text(encoding="utf-8"))
        step = next(node.value.elts[0].value for node in errors.body if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Tuple) and node.targets[0].elts[0].id == "STEP")
        self.assertEqual(step, _label(ui, "ANGELO_OT_apply_approved_tool"))
        self.assertEqual([], offending(step))


if __name__ == "__main__":
    unittest.main()
