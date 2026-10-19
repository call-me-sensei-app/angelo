# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find user-facing strings that bypass Blender's translation system.

Pure ``ast`` analysis with no ``bpy``, so it runs as a host test. Angelo shows
text through four routes, and each has one correct spelling:

* **Blender-native widgets** (``layout.label(text=...)``, ``bl_label``,
  property ``name=``/``description=``, enum items, ``poll_message_set``): a
  *literal* is translated by Blender and found by its extractor. Text that is
  built (f-string, ``+``, ``%``, ``.format``, ``.join``) can never match a
  catalogue entry, so it must go through ``iface_``/``tip_``/``rpt_``.
* **Canvas text** (``text(...)``, ``centered(...)``, ``blf.draw``, the card
  builders): nothing translates it by itself, so it must be wrapped.
* **Chat notices** stored in history use ``notice(template, **fields)``.
* **Constants** shown later are marked with ``n_`` and translated where drawn.

Everything else in a UI module that reads like a sentence must be wrapped or
listed in ``locale/angelo/untranslated.json`` with a reason (model-facing text,
log lines, data keys). That net is what makes a new untranslatable string fail
the host test instead of reaching users in English.
"""

import ast
import re

WRAPPERS = {"n_", "iface_", "tip_", "rpt_", "plural_", "notice", "pgettext", "pgettext_iface", "pgettext_tip",
            "pgettext_rpt", "pgettext_n", "data_"}
PLURAL_ARGS = (1, 2)
# Canvas sinks: (function name) -> positional message index; keywords are checked by name below.
CANVAS_SINKS = {"text": 0, "centered": 0, "elide": 0, "label": 0, "button": 0, "paragraph": 0, "caption": 0,
                "draw_label": 0, "draw_text": 0, "pill": 0, "heading": 0}
# Native widget keywords whose *literal* Blender translates and whose built text it cannot.
NATIVE_KEYWORDS = {"text", "heading", "placeholder", "description", "name", "title", "message", "confirm_text", "label",
                   "items"}
# Calls whose text LITERALS Blender translates itself and whose extractor finds them.
NATIVE_CALLS = {"label", "operator", "prop", "menu", "popover", "separator", "poll_message_set", "prop_enum", "prop_search",
                "StringProperty", "BoolProperty", "IntProperty", "FloatProperty", "EnumProperty", "PointerProperty",
                "CollectionProperty", "FloatVectorProperty", "IntVectorProperty", "BoolVectorProperty"}
# Calls that need the text translated by Angelo: Blender stores a report's message untranslated and draws it as is
# (only C++ ``RPT_()``/Python ``rpt_()`` translate), and its extractor does not collect dialog titles and texts.
EXPLICIT_CALLS = {"report", "invoke_confirm", "invoke_props_dialog", "invoke_props_popup", "popup_menu", "popup_menu_pie"}
LAYOUT_CALLS = {"label", "operator", "prop", "menu", "popover", "prop_enum", "prop_search"}
OPERATOR_CALLS = {"operator", "operator_menu_hold", "operator_menu_enum"}
PROPERTY_CALLS = {"StringProperty", "BoolProperty", "IntProperty", "FloatProperty", "EnumProperty", "PointerProperty",
                  "CollectionProperty", "FloatVectorProperty", "IntVectorProperty", "BoolVectorProperty"}
NATIVE_CLASS_ATTRIBUTES = {"bl_label", "bl_description", "bl_category"}
NON_DISPLAY_CALLS = {"getattr", "hasattr", "setattr", "delattr", "isinstance", "issubclass", "open", "Path", "get", "pop",
                     "setdefault", "startswith", "endswith", "replace", "split", "rsplit", "strip", "lstrip", "rstrip",
                     "find", "index", "count", "join", "encode", "decode", "compile", "match", "search", "fullmatch",
                     "sub", "findall", "getenv", "environ", "import_module", "__import__", "dumps", "loads", "format_map",
                     "register_class", "unregister_class", "append_path", "exists", "mkdir", "unlink", "run", "Popen",
                     "check_output", "call", "warn", "debug", "info", "warning", "error", "exception", "critical", "print",
                     "ops", "frozenset", "set", "dict", "tuple", "list", "sorted", "min", "max", "type", "id", "str",
                     "bytes", "int", "float", "bool", "len", "enumerate", "zip", "map", "filter", "any", "all", "next"}
NON_DISPLAY_KEYWORDS = {"icon", "type", "subtype", "options", "default", "identifier", "idname", "operator", "kind", "key",
                        "mode", "action", "event", "role", "name_ctxt", "text_ctxt", "tooltip_ctxt", "encoding", "errors",
                        "sep", "end", "prefix", "suffix", "path", "filename", "module", "language", "context", "value",
                        "state", "status", "reason", "code", "tool", "id", "owner", "scope", "source", "provider", "model"}
CLASS_CONSTANTS = {"bl_idname", "bl_space_type", "bl_region_type", "bl_category", "bl_options", "bl_context", "bl_parent_id",
                   "bl_ui_units_x", "bl_translation_context"}
_WORDS = re.compile(r"[A-Za-zÀ-ɏ]{2,}")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/]*$")


def human(value, single_word_ok=False):
    """True when ``value`` reads as interface text rather than an identifier, path, code or format spec."""
    stripped = value.strip()
    if len(stripped) < 2 or not _WORDS.search(stripped):
        return False
    if _IDENTIFIER.match(stripped) and (" " not in stripped):
        return single_word_ok and stripped[0].isupper() and stripped.isalpha()
    if stripped.startswith(("http://", "https://", "/", "~/", "$", "%(", "<", "[", "{\"")) and " " not in stripped:
        return False
    return len(_WORDS.findall(stripped)) >= 2 or (single_word_ok and stripped[0].isupper())


def _marked_choice(argument):
    """A value marked where it is defined (a name, attribute, lookup or call), or a choice between such values.

    ``iface_(TITLE_IMPORTANT if critical else TITLE)`` picks one whole marked message; a literal inside the
    choice is not, because Blender's extractor reads only a call's direct literal: mark it with ``n_``.
    """
    if isinstance(argument, ast.IfExp):
        return _marked_choice(argument.body) and _marked_choice(argument.orelse)
    return isinstance(argument, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))


def function_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def is_built(node):
    """A string made at run time: f-string, concatenation, ``%``, ``.format`` or ``.join``."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(part, ast.Constant) and human(str(part.value)) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return any(isinstance(sub, ast.Constant) and isinstance(sub.value, str) and human(sub.value, True)
                   for sub in ast.walk(node)) and not _numeric_only(node)
    if isinstance(node, ast.Call) and function_name(node) in {"format", "join"} and isinstance(node.func, ast.Attribute):
        receiver = node.func.value
        return isinstance(receiver, ast.Constant) and isinstance(receiver.value, str) and human(receiver.value, True)
    return False


def _literal_text(node):
    """A string literal, or a choice between literals (Blender's extractor reads both with the call's context)."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return isinstance(node, ast.IfExp) and _literal_text(node.body) and _literal_text(node.orelse)


def _assembled(node):
    """Any string made at run time from literal pieces, whether or not they read as words."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return any(isinstance(sub, ast.Constant) and isinstance(sub.value, str) for sub in ast.walk(node))
    return (isinstance(node, ast.Call) and function_name(node) in {"format", "join"} and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Constant))


def _numeric_only(node):
    return all(not (isinstance(sub, ast.Constant) and isinstance(sub.value, str) and human(sub.value, True))
               for sub in ast.walk(node))


class Finding:
    def __init__(self, path, line, kind, text):
        self.path, self.line, self.kind, self.text = path, line, kind, text

    def key(self):
        return f"{self.path}::{self.text}"

    def __repr__(self):
        return f"{self.path}:{self.line} [{self.kind}] {self.text!r}"


def _parents(tree):
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node


def _ancestors(node):
    while True:
        node = getattr(node, "_parent", None)
        if node is None:
            return
        yield node


def _call_context(node):
    """(closest enclosing Call, the argument slot ``node`` occupies: index int or keyword name)."""
    child = node
    for parent in _ancestors(node):
        if isinstance(parent, ast.Call):
            if child in parent.args:
                return parent, parent.args.index(child)
            for keyword in parent.keywords:
                if keyword is child or keyword.value is child:
                    return parent, keyword.arg
        if isinstance(parent, (ast.Compare, ast.Subscript, ast.Assert, ast.Raise, ast.JoinedStr)) and parent is not child:
            if isinstance(parent, ast.JoinedStr):
                child = parent
                continue
            return None, None
        child = parent
    return None, None


EXEMPT = re.compile(r"#\s*i18n:\s*(\S.*)$")


def _exemptions(source):
    """Lines carrying ``# i18n: <reason>``: strings there are deliberately not translated."""
    lines = {}
    for number, line in enumerate(source.splitlines(), 1):
        match = EXEMPT.search(line)
        if match:
            lines[number] = match.group(1).strip()
    return lines


def _notice_dicts(tree):
    """Chat messages ``{"role": "system"|"error"|"tool", "text": <English>}`` that skip ``notice``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
        role, value = keys.get("role"), keys.get("text")
        if value is None or not (isinstance(role, ast.Constant) and role.value in {"system", "error"}):
            continue
        if any(k is None for k in node.keys):  # {**notice(...), "role": ...}: already carries a template.
            continue
        if is_built(value) or (isinstance(value, ast.Constant) and isinstance(value.value, str) and human(value.value, True)):
            yield node, ast.unparse(value)[:120]


def scan(source, path, *, net=True, notices=True):
    """Return ``(findings, msgids)`` for one module: bypasses and the marked message ids."""
    tree = ast.parse(source)
    _parents(tree)
    findings, msgids = [], []
    exempt = _exemptions(source)

    def add(node, kind, text):
        end = getattr(node, "end_lineno", node.lineno)
        if any(line in exempt for line in range(node.lineno, end + 1)):
            return
        findings.append(Finding(path, node.lineno, kind, text))

    if notices:
        for node, text in _notice_dicts(tree):
            add(node, "notice", text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = function_name(node)
            if name in WRAPPERS:
                for index in (PLURAL_ARGS if name == "plural_" else (0,)):
                    if index < len(node.args):
                        argument = node.args[index]
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            msgids.append((argument.value, node.lineno, name))
                        elif not _marked_choice(argument):
                            add(node, "fragment", ast.unparse(argument)[:120])
            if name in PROPERTY_CALLS:  # A property's name, description and enum items: Blender translates them.
                msgids += [(k.value.value, k.value.lineno, "property") for k in node.keywords if k.arg in ("name", "description")
                           and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str) and k.value.value.strip()]
                items = next((k.value for k in node.keywords if k.arg == "items" and isinstance(k.value, (ast.List, ast.Tuple))), None)
                for item in (items.elts if items is not None else ()):
                    if isinstance(item, ast.Tuple) and len(item.elts) >= 3:
                        msgids += [(part.value, part.lineno, "property") for part in item.elts[1:3]
                                   if isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value.strip()
                                   and part.value != getattr(item.elts[0], "value", None)]
            if (name in LAYOUT_CALLS or name in OPERATOR_CALLS) and isinstance(node.func, ast.Attribute):
                # A native widget's literal is a message too, in the operator's context for an operator button.
                if not any(k.arg == "translate" for k in node.keywords):
                    msgids += [(k.value.value, node.lineno, "operator" if name in OPERATOR_CALLS else "native")
                               for k in node.keywords if k.arg in ("text", "heading") and isinstance(k.value, ast.Constant)
                               and isinstance(k.value.value, str) and k.value.value.strip()]
                # Text translated by Angelo goes in untouched: Blender must not look it up a second time.
                pre = [k for k in node.keywords if k.arg in ("text", "heading", "placeholder")
                       and isinstance(k.value, ast.Call) and function_name(k.value) in {"iface_", "tip_", "plural_"}]
                off = any(k.arg == "translate" and isinstance(k.value, ast.Constant) and k.value.value is False
                          for k in node.keywords)
                if pre and not off:
                    add(node, "double", ast.unparse(pre[0].value)[:100] + "  -> add translate=False")
                # Text assembled from data (names, counts, separators): Blender would look the whole string up
                # in its catalogue, which never matches and pollutes the extracted template.
                data = [k for k in node.keywords if k.arg in ("text", "heading") and _assembled(k.value)
                        and not is_built(k.value)]
                if data and not off:
                    add(node, "data", ast.unparse(data[0].value)[:100] + "  -> add translate=False")
                # Blender looks an operator button's text up in the *operator's* context ("Operator"), but text held
                # in a variable or marked n_ is in the default context: translate it with iface_ and pass it untouched.
                if name in OPERATOR_CALLS and not off and not any(k.arg == "text_ctxt" for k in node.keywords):
                    held = [k for k in node.keywords if k.arg == "text" and not _literal_text(k.value)
                            and not (isinstance(k.value, ast.Call) and function_name(k.value) in {"iface_", "tip_", "plural_"})
                            and not _assembled(k.value)]
                    if held:
                        add(node, "context", ast.unparse(held[0].value)[:100]
                            + "  -> iface_(...) with translate=False (or translate=False for data)")
            if name in NATIVE_CALLS or name in CANVAS_SINKS or name in EXPLICIT_CALLS:
                for index, argument in enumerate(node.args):
                    if is_built(argument) and (name in CANVAS_SINKS and index == CANVAS_SINKS[name]
                                               or name == "report" and index == 1):
                        add(argument, "built", ast.unparse(argument)[:120])
                for keyword in node.keywords:
                    if keyword.arg in NATIVE_KEYWORDS and is_built(keyword.value):
                        add(keyword.value, "built", ast.unparse(keyword.value)[:120])
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NATIVE_CLASS_ATTRIBUTES
                                                  for t in node.targets):
            if is_built(node.value):
                add(node.value, "built", ast.unparse(node.value)[:120])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and net:
            literal = _net_candidate(node)
            if literal:
                add(node, "bare", node.value)
    return findings, [message for message in msgids if message[1] not in exempt]


def _net_candidate(node):
    """Is this literal interface text that no translation route covers?"""
    value = node.value
    parent = node._parent
    if isinstance(parent, ast.Expr):  # Docstrings and bare expression statements.
        return False
    call, slot = _call_context(node)
    for ancestor in _ancestors(node):
        if isinstance(ancestor, ast.Call) and function_name(ancestor) in WRAPPERS:
            return False
        if isinstance(ancestor, (ast.Raise, ast.Assert, ast.Compare, ast.Subscript, ast.ImportFrom, ast.Import)):
            return False
        if isinstance(ancestor, ast.Call):
            callee = function_name(ancestor)
            if callee in NON_DISPLAY_CALLS and callee not in CANVAS_SINKS and callee not in NATIVE_CALLS \
                    and callee not in EXPLICIT_CALLS:
                return False
        if isinstance(ancestor, ast.Dict) and node in ancestor.keys:
            return False
        if isinstance(ancestor, (ast.FunctionDef, ast.ClassDef)):
            break
    for ancestor in _ancestors(node):
        if isinstance(ancestor, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CLASS_CONSTANTS
                                                    for t in ancestor.targets):
            return False
    sink = call is not None and (function_name(call) in CANVAS_SINKS and slot == CANVAS_SINKS[function_name(call)])
    native = call is not None and isinstance(slot, str) and slot in NATIVE_KEYWORDS and function_name(call) in NATIVE_CALLS
    if native:
        return False  # Blender translates a literal here and its extractor finds it.
    if isinstance(slot, str) and slot in NON_DISPLAY_KEYWORDS and not sink:
        return False
    for ancestor in _ancestors(node):
        if isinstance(ancestor, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NATIVE_CLASS_ATTRIBUTES
                                                    for t in ancestor.targets):
            return False
        if isinstance(ancestor, (ast.FunctionDef, ast.ClassDef)):
            break
    return human(value, single_word_ok=sink)


LOOKUP_CALLS = {"get", "pop", "setdefault", "startswith", "endswith", "index", "count", "getattr", "hasattr", "setattr"}


def literal_roles(source):
    """``(lookups, others)``: string literals used only to look data up (``x["name"]``, ``d.get("error")``, dict
    keys, comparisons) and those used anywhere else.

    Blender's extractor walks every literal inside a translated argument, so ``text=record["name"]`` gives it the
    message "name". A message whose literal is only ever a lookup key is not interface text.
    """
    tree = ast.parse(source)
    _parents(tree)
    lookups, others = set(), set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        parent = node._parent
        key = (isinstance(parent, ast.Subscript) and parent.slice is node
               or isinstance(parent, ast.Dict) and node in parent.keys
               or isinstance(parent, ast.Compare)
               or isinstance(parent, ast.Call) and function_name(parent) in LOOKUP_CALLS and node in parent.args[:2]
               and not (function_name(parent) == "get" and parent.args.index(node) == 1))
        (lookups if key else others).add(node.value)
    return lookups - others, others
