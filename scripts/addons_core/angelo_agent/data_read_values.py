# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What ``angelo_read_data`` returns for one RNA property, and what it never returns.

Every value is read through the installed RNA definition of its own property
(``bl_rna.properties``): numbers, booleans, enum identifiers, bounded arrays,
names, and references to other structs. A value that is withheld is still
listed, with its type and a closed reason, so the model knows it exists and why
it cannot see it:

* ``PATH``: a file path, folder or file name by RNA subtype or property name,
  and any other text that looks like a path or a URL. Paths can carry user and
  client names to a model provider (doc/angelo/scene_intelligence.rst).
* ``SECRET``: a password field, a credential-like property name, or text the
  memory rules recognise as a credential (``memory_spec.unsafe_text``).
* ``SCRIPT``: script source, bytecode and driver expressions.
* ``SOCKET_TEXT``: the text of a string socket, on a node, a node group's
  interface or a Geometry Nodes modifier input, and the text of a String
  input node.
* ``BYTES``: raw byte strings (sculpt and weight buffers, packed data).
* ``LOADS_FILE``: a property whose read would load a file (an image, a movie,
  a font, a volume grid or a disk cache); nothing is loaded.
* ``NOT_EVALUATED``: a value Blender computes during evaluation that has not
  been computed yet (a pending subdivision, the bounds of an unevaluated
  object); a read never evaluates.
* ``VIEW_LAYER_OUT_OF_SYNC``: the view layer is out of date, and Blender would
  update it to answer.
* ``EXTENSION_CODE``: answering would run add-on (Python) code.
* ``RESOURCE_LIMIT``: the value is larger than a read may copy.

Values in the last five are computed by approved scripts instead; see "Getter
policy" in doc/angelo/scene_intelligence.rst.

The value of a withheld property is never fetched: the decision uses only the
RNA definition and, for ordinary text, the text itself. Names (a struct's RNA
name property, identifiers and labels) are returned as the other scene reads
return them. Nothing here needs ``bpy``: host tests pass RNA stand-ins.
"""

import json
import math
import re

from .memory_spec import unsafe_text

MAX_TEXT = 256
MAX_ELEMENTS = 64
MAX_CHOICES = 64
PATH, SECRET, SCRIPT, SOCKET_TEXT, BYTES, LOADS_FILE = "PATH", "SECRET", "SCRIPT", "SOCKET_TEXT", "BYTES", "LOADS_FILE"
NOT_EVALUATED, VIEW_LAYER_OUT_OF_SYNC = "NOT_EVALUATED", "VIEW_LAYER_OUT_OF_SYNC"
EXTENSION_CODE, RESOURCE_LIMIT = "EXTENSION_CODE", "RESOURCE_LIMIT"
PATH_SUBTYPES = frozenset({"FILE_PATH", "DIR_PATH", "FILE_NAME"})
# RNA paths into the document (F-Curve and driver targets), not file paths.
RNA_PATH_NAMES = frozenset({"data_path", "rna_path"})
_PATH_WORDS = ("filepath", "file_path", "filename", "file_name", "directory", "dirpath", "dir_path", "folder")
_PATH_ENDINGS = ("_path", "_dir", "_url", "_uri")
_PATH_NAMES = frozenset({"path", "url", "uri", "dir"})
_SCRIPT_WORDS = ("expression", "bytecode", "script")
_SECRET_WORDS = ("password", "passwd", "secret", "token", "api_key", "apikey", "credential", "private_key")
# Labels: returned unscreened, as every other scene read returns names.
NAME_PROPERTIES = frozenset({"name", "name_full", "identifier", "label", "bl_idname", "bl_label"})
_PATH_LIKE = re.compile(r"[/\\]|^~|^[A-Za-z]:|^\.\.?$")
# Acquire-backed getters are replaced by exact cache-only copies in data_read_access.
# Other Image fields (display_aspect, render_slots and view flags) read native storage.
IMAGE_LOADING = frozenset({"size", "channels", "depth", "is_float", "resolution", "file_format", "frame_duration"})
IMAGE_NEVER = frozenset({"pixels"})


_NAMES = {}


def struct_names(rna):
    """The RNA identifier of a struct definition and of every base it derives from."""
    if rna is None:
        return ()
    names = getattr(rna, "names", None)  # A copied declaration (TypeDecl) carries them.
    if type(names) is tuple and names:
        return names
    try:
        names = _NAMES.get(rna)
    except TypeError:  # An unhashable stand-in definition.
        return (rna.identifier, *rna.bases)
    if names is None:
        names = (rna.identifier, *rna.bases)
        if len(_NAMES) < 4096:
            _NAMES[rna] = names
    return names


_REASONS = {}


def text_reason(names, prop, *, socket_inputs=False):
    """Why a STRING property's value is withheld before it is read, or None.

    ``names``: ``struct_names`` of the struct that owns ``prop``. ``socket_inputs``:
    the struct is a Geometry Nodes modifier input. Decided by the definition alone,
    so a decision is reused for the same identifier, subtype and owner names.
    """
    key = (prop.identifier, getattr(prop, "subtype", "NONE"), socket_inputs, tuple(names))
    try:
        return _REASONS[key]
    except KeyError:
        pass
    reason = _text_reason(names, prop, socket_inputs)
    if len(_REASONS) < 16384:
        _REASONS[key] = reason
    return reason


def _text_reason(names, prop, socket_inputs):
    identifier = prop.identifier.lower()
    subtype = getattr(prop, "subtype", "NONE")
    if subtype == "PASSWORD" or any(word in identifier for word in _SECRET_WORDS):
        return SECRET
    if subtype == "BYTE_STRING":
        return BYTES
    if subtype in PATH_SUBTYPES:
        return PATH
    if identifier not in RNA_PATH_NAMES and (identifier in _PATH_NAMES or identifier.endswith(_PATH_ENDINGS)
                                             or any(word in identifier for word in _PATH_WORDS)):
        return PATH
    if any(word in identifier for word in _SCRIPT_WORDS):
        return SCRIPT
    if identifier == "default_value" and any(name in ("NodeSocket", "NodeTreeInterfaceSocket") for name in names):
        return SOCKET_TEXT
    if socket_inputs and identifier == "value":
        return SOCKET_TEXT
    if identifier == "string" and "FunctionNodeInputString" in names:
        return SOCKET_TEXT  # The String input node holds the text its socket outputs.
    return None


def screened_text(value, *, is_name=False):
    """``(text, reason)`` for a string that was read: bounded, or withheld when it looks like a path or a secret."""
    value = str(value)
    if not is_name:
        if _PATH_LIKE.search(value):
            return None, PATH
        if unsafe_text(value):
            return None, SECRET
    return value[:MAX_TEXT], None


def label_text(owner, prop, names, read):
    """The text of a struct's label property (its RNA name property, an identifier), or None when it is withheld.

    A label is returned as the other scene reads return names, unless its
    definition says it is something else: a packed image file's "name" is its
    file path, and a few structs name themselves by other text, which is
    screened like any value.
    """
    if prop is None or prop.type != "STRING" or text_reason(names, prop):
        return None
    value = read(owner, prop)
    return screened_text(value, is_name=prop.identifier in NAME_PROPERTIES)[0]


def choice_text(identifier):
    """An enum identifier, or None when an add-on used a path as one (file previews)."""
    return None if _PATH_LIKE.search(str(identifier)) else str(identifier)[:MAX_TEXT]


def image_reason(owner, prop):
    """Properties never fetched; exact metadata cache admission belongs to data_read_access."""
    if prop.identifier in IMAGE_NEVER:
        return LOADS_FILE
    return None


def number(value):
    """A finite JSON number; non-finite floats become None."""
    if type(value) is bool or type(value) is int:
        return value
    value = float(value)
    return round(value, 6) if math.isfinite(value) else None


def _flatten(value, dimensions, budget):
    """Nested lists of at most ``budget[0]`` numbers from an RNA array of any dimension."""
    if dimensions > 1:
        return [_flatten(row, dimensions - 1, budget) for row in value if budget[0] > 0]
    row = []
    for item in value:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        row.append(number(item))
    return row


def array(value, prop, *, actual_length=None):
    """``{"value": nested numbers, "length": n}`` plus ``truncated`` when more than ``MAX_ELEMENTS`` exist."""
    dimensions = sum(1 for size in getattr(prop, "array_dimensions", ()) if size) or 1
    length = int(getattr(prop, "array_length", 0)) or (actual_length if actual_length is not None else len(value))
    budget = [MAX_ELEMENTS]
    result = {"value": _flatten(value, dimensions, budget), "length": length}
    if length > MAX_ELEMENTS:
        result["truncated"] = True
    return result


def is_array(prop):
    """A numeric RNA property holding several values (a fixed vector or matrix, or a dynamic array)."""
    return prop.type in ("BOOLEAN", "INT", "FLOAT") and (int(getattr(prop, "array_length", 0)) > 0
                                                         or any(getattr(prop, "array_dimensions", ()))
                                                         or getattr(prop, "has_dynamic_length", False))


def choices(source):
    """Format already screened, admitted detached enum choices."""
    return [{"id": identifier[:MAX_TEXT], "label": label[:64]}
            for identifier, label, _number in source[:MAX_CHOICES]], len(source) > MAX_CHOICES


def informative_label(prop):
    """The interface label when it says more than the identifier does, else None.

    ``samples`` / "Samples" adds nothing; ``adaptive_threshold`` / "Noise
    Threshold" and a modifier input ``Socket_2`` / "Height" do.
    """
    label = str(getattr(prop, "name", "") or "")
    if not label or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") == prop.identifier.lower().strip("_"):
        return None
    return label[:64]


def limits(source):
    """Hard range of a numeric property, when it has a finite one worth stating."""
    result = {}
    for key, name in (("hard_min", "min"), ("hard_max", "max")):
        value = source.get(key) if source is not None else None
        if isinstance(value, (int, float)) and math.isfinite(value) and abs(value) < 1e30:
            result[name] = number(value)
    return result


# json.dumps with these arguments builds an encoder per call; one shared encoder gives the same text.
_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
# JSONEncoder.encode also builds its C encoder per call; this one is built once with the same
# settings. Rows are trees this module builds, so no circular-reference markers are kept.
_C_ENCODE = (json.encoder.c_make_encoder(None, _ENCODER.default, json.encoder.encode_basestring, None,
                                         ":", ",", False, False, False)
             if json.encoder.c_make_encoder is not None else None)


def encoded(value):
    """The characters a value takes in the answer's compact JSON."""
    if _C_ENCODE is None:
        return len(_ENCODER.encode(value))
    return len("".join(_C_ENCODE(value, 0)))


_ESCAPED = re.compile(r'[\x00-\x1f\\"]')  # What JSON (ensure_ascii=False) escapes in a string.


def _text_size(text):
    """``encoded(text)`` for a string: its characters and quotes when nothing in it is escaped."""
    return len(text) + 2 if _ESCAPED.search(text) is None else encoded(text)


def _number_size(value):
    """``encoded(value)`` for what ``values.number`` returns (None, a bool, an int or a float)."""
    if value is None:
        return 4
    if value is True:
        return 4
    if value is False:
        return 5
    return len(repr(value))  # JSON writes ints and finite floats as their repr.
