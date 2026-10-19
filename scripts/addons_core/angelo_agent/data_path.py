# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The closed path grammar of ``angelo_read_data``. Pure: no Blender, no ``eval``.

A path names one place in the open document, the way a person writes it in
Blender's Python console, but only this much of that syntax exists::

    path  = root *step
    root  = "scene" / "data"            ; also "bpy.context.scene" / "bpy.data"
    step  = "." name                    ; an RNA property of the struct reached so far
          / "[" integer "]"             ; an item of a collection, negative from the end
          / "[" string "]"              ; an item of a collection by its name
    name  = (ALPHA / "_") *(ALPHA / DIGIT / "_"), not starting with "_"

A string is quoted with ``"`` or ``'`` and escapes only its own quote and the
backslash. Nothing else parses: no calls, operators, slices, arithmetic,
comprehensions, f-strings, attribute names starting with an underscore, or
whitespace outside a string. The parser reads the text one character at a time
and produces plain steps; ``data_read`` then resolves each step against the
installed RNA definition, never with ``getattr`` on a name the model chose and
never by evaluating anything.
"""

MAX_PATH_CHARS = 512
MAX_STEPS = 16
MAX_NAME_CHARS = 64
MAX_KEY_CHARS = 255  # Blender's longest datablock name.
MAX_INDEX = 9999999
SCENE, DATA = "scene", "data"
# Longest first: "bpy.context.scene" must not be read as the root "bpy" plus steps.
ROOT_SPELLINGS = (("bpy.context.scene", SCENE), ("bpy.data", DATA), ("scene", SCENE), ("data", DATA))
ATTRIBUTE, INDEX, KEY = "attribute", "index", "key"
GRAMMAR = ('Use scene or data (bpy.data), then .property names and [index] or ["name"] keys only, for example '
           'scene.cycles or data.node_groups["Rock"].nodes[0].inputs.')


class DataPathError(ValueError):
    """A path outside the grammar. ``code`` is stable; ``position`` is the offending character (0-based)."""

    def __init__(self, code, position, detail):
        self.code, self.position = code, position
        super().__init__(detail)


def _name_start(character):
    return character.isascii() and (character.isalpha() or character == "_")


def _name_part(character):
    return character.isascii() and (character.isalnum() or character == "_")


def _name(text, position):
    """The identifier starting at ``position`` and the position after it."""
    end = position
    while end < len(text) and _name_part(text[end]):
        end += 1
    name = text[position:end]
    if not name or not _name_start(name[0]):
        raise DataPathError("SYNTAX", position, "A property name is expected here. " + GRAMMAR)
    if name.startswith("_"):
        raise DataPathError("PRIVATE_NAME", position, "Names starting with an underscore are not data properties.")
    if len(name) > MAX_NAME_CHARS:
        raise DataPathError("SYNTAX", position, "A property name has at most %d characters." % MAX_NAME_CHARS)
    return name, end


def _string(text, position):
    """The quoted key starting at ``position`` (its opening quote) and the position after its closing quote."""
    quote, characters, index = text[position], [], position + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            following = text[index + 1:index + 2]
            if following not in (quote, "\\"):
                raise DataPathError("SYNTAX", index, "Inside a key, a backslash escapes only the quote or a backslash.")
            characters.append(following)
            index += 2
            continue
        if character == quote:
            key = "".join(characters)
            if not key or len(key) > MAX_KEY_CHARS:
                raise DataPathError("SYNTAX", position, "A key has 1 to %d characters." % MAX_KEY_CHARS)
            return key, index + 1
        if ord(character) < 32 or ord(character) == 127:
            raise DataPathError("SYNTAX", index, "A key holds printable characters only.")
        characters.append(character)
        index += 1
    raise DataPathError("SYNTAX", position, "This key has no closing quote.")


def _bracket(text, position):
    """The step of the ``[...]`` starting at ``position`` and the position after its ``]``."""
    index = position + 1
    if index >= len(text):
        raise DataPathError("SYNTAX", position, "This [ has no index or key. " + GRAMMAR)
    if text[index] in "\"'":
        key, index = _string(text, index)
        step = (KEY, key)
    else:
        end = index + 1 if text[index] == "-" else index
        while end < len(text) and text[end].isascii() and text[end].isdigit():
            end += 1
        digits = text[index:end]
        if digits in ("", "-"):
            hint = ("Slices are not read; use offset and limit for a page. " if text[index] == ":" else
                    "Expressions are not read. " if text[index] not in "]" else "")
            raise DataPathError("SYNTAX", index, hint + "Inside [ ] write a whole number or a quoted name.")
        if len(digits.lstrip("-")) > 7:
            raise DataPathError("SYNTAX", index, "An index is at most %d." % MAX_INDEX)
        step, index = (INDEX, int(digits)), end
    if text[index:index + 1] != "]":
        hint = "Slices are not read; use offset and limit for a page. " if text[index:index + 1] == ":" else ""
        raise DataPathError("SYNTAX", index, hint + "A ] is expected here; [ ] holds one whole number or one quoted name.")
    return step, index + 1


def parse(text):
    """``(root, steps)`` for a path: ``root`` is ``SCENE`` or ``DATA``, each step ``(kind, value)``.

    Raises ``DataPathError``. Nothing is looked up: an unknown property name
    parses and is refused later, against the RNA definition.
    """
    if not isinstance(text, str):
        raise DataPathError("SYNTAX", 0, "A path is text. " + GRAMMAR)
    text = text.strip()
    if not text:
        raise DataPathError("EMPTY", 0, "Name a path. " + GRAMMAR)
    if len(text) > MAX_PATH_CHARS:
        raise DataPathError("TOO_LONG", MAX_PATH_CHARS, "A path has at most %d characters." % MAX_PATH_CHARS)
    root, position = None, 0
    for spelling, name in ROOT_SPELLINGS:
        if text.startswith(spelling) and not _name_part(text[len(spelling):len(spelling) + 1]):
            root, position = name, len(spelling)
            break
    if root is None:
        raise DataPathError("ROOT", 0, "A path starts at scene (the bound scene) or data (the open file's "
                                       "datablocks, bpy.data). " + GRAMMAR)
    steps = []
    while position < len(text):
        character = text[position]
        if character == ".":
            name, position = _name(text, position + 1)
            steps.append((ATTRIBUTE, name))
        elif character == "[":
            step, position = _bracket(text, position)
            steps.append(step)
        elif character == "(":
            raise DataPathError("CALL", position, "Calls are not read: nothing is invoked or evaluated. " + GRAMMAR)
        else:
            raise DataPathError("SYNTAX", position, "Unexpected %r. %s" % (character, GRAMMAR))
        if len(steps) > MAX_STEPS:
            raise DataPathError("TOO_DEEP", position, "A path has at most %d steps after its root." % MAX_STEPS)
    return root, steps


def quote(key):
    """A key as the grammar writes it, in double quotes."""
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(root, steps):
    """The canonical spelling of a parsed path (what results echo and ``parse`` reads back)."""
    parts = [root]
    for kind, value in steps:
        parts.append("." + value if kind == ATTRIBUTE else "[%d]" % value if kind == INDEX else "[" + quote(value) + "]")
    return "".join(parts)
