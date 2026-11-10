# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Messages built away from the UI that the UI translates where it shows them.

Worker-thread and bpy-free modules (the team account, the managed policy)
produce sentences for the person long before a draw call shows them. A
``Text`` is that English sentence (it *is* a ``str``, so logs, tests and
existing string handling keep working) and also remembers its template and
named fields, so ``shown`` can translate the template and fill the fields at
display time (ui_text's rules: whole sentences, named placeholders, translate
only where shown). Templates are marked with ``ui_text.n_`` so the extractor
finds them. A plain ``str`` passed to ``shown`` is looked up as a whole.

Concatenating a ``Text`` gives a plain ``str`` (English only): compose with a
template instead.

``describe_error``/``error_message`` are the one boundary between an exception
and the words a person reads (a report, a Studio notice): an exception never
reaches the screen as Python wrote it ("[Errno 13] Permission denied: '/x'",
"'name'"). See doc/angelo/localization.rst, "Errors shown to the person".
"""

import errno
import re
import sys
import traceback

from .ui_text import iface_, n_, notice

FAILED = n_("That didn't work because of an unexpected problem. The details are in the system console.")
CONNECTION_FAILED = n_("Couldn't reach the service. Check your internet connection and try again.")
FILE_FAILED = n_("Couldn't use a file or folder: {reason}.")
# A file error by the operating system's error number, as a whole sentence the person can act on (never its path).
FILE_REASONS = {errno.ENOENT: n_("Couldn't find a file or folder that this needs."),
                errno.EACCES: n_("Angelo isn't allowed to use a file or folder that this needs. Check its permissions."),
                errno.ENOSPC: n_("The disk is full. Free some space and try again."),
                errno.EROFS: n_("The disk is read-only, so nothing could be saved there.")}
FILE_REASONS[errno.EPERM] = FILE_REASONS[errno.EACCES]
FILE_REASONS[getattr(errno, "EDQUOT", errno.ENOSPC)] = FILE_REASONS[errno.ENOSPC]
_PACKAGE = __name__.rpartition(".")[0]
# The type Blender writes before each report of a refused operator ("Error: ", "Invalid Input Error: ", translated,
# "Erreur : "): a short label with no sentence punctuation before its colon.
_REPORT_PREFIX = re.compile(r"^[^:.!?。！？\n]{1,40}?\s?:\s")
# Blender's own report types (``BKE_report_type_str``), in English: a refusal relayed without its final newline.
_REPORT_TYPES = re.compile(r"^(?:Debug|Info|Operator|Property|Warning|Error|Invalid Input Error|Invalid Context Error|"
                           r"Out Of Memory Error): ")


class Text(str):
    def __new__(cls, template, **fields):
        text = super().__new__(cls, template.format(**fields) if fields else template)
        text.template, text.fields = template, dict(fields)
        return text

    def __reduce__(self):
        return (_rebuild, (self.template, tuple(self.fields.items())))


def _rebuild(template, fields):
    return Text(template, **dict(fields))


def error_notice(error):
    """The body of a chat error row for ``error``: a ``Text`` message keeps its template for display, any other is its text."""
    first = error.args[0] if error.args else None
    return notice(first.template, **first.fields) if isinstance(first, Text) else {"text": str(error)}


def shown(value):
    """``value`` in the interface language; fields that are themselves ``Text`` are translated too."""
    if isinstance(value, Text):
        fields = {key: shown(item) if isinstance(item, Text) else item for key, item in value.fields.items()}
        return iface_(value.template, **fields)
    return iface_(value) if value else value


def describe_error(error, failed=FAILED, redact=None):
    """What a person reads about ``error``: a ``Text`` (or a sentence ``shown`` looks up whole), never Python's wording.

    * A ``Text`` message, or an error with its own ``template`` and ``fields`` (``studio_state.Templated``): itself.
    * A refused operator (``bpy.ops`` raises its reports): the refusal's whole sentence, already in the interface
      language because the refusing operator translated its report.
    * Angelo's own refusal (one of its error classes, or a plain ``ValueError``/``RuntimeError``, raised with one
      sentence): that sentence, looked up whole where shown. Raise a ``Text`` instead to have it extracted.
    * A connection failure or a file error (``OSError``): a whole sentence for its kind; never a path or errno.
    * Anything else is unexpected: ``failed`` (an ``n_`` sentence), with the details written to the system console.

    Works on any thread (no translation here); ``redact`` filters any text of the error's own that is kept.
    """
    redact = redact or (lambda value: value)
    first = error.args[0] if getattr(error, "args", None) else None
    if isinstance(first, Text):
        return first
    template, fields = getattr(error, "template", None), getattr(error, "fields", None)
    if isinstance(template, str) and template and isinstance(fields, dict):
        return Text(template, **{key: redact(value) if isinstance(value, str) else value for key, value in fields.items()})
    sentence = _refusal(error)
    if sentence:
        return redact(sentence)
    _log(error, redact)
    if isinstance(error, (ConnectionError, TimeoutError)):
        return Text(CONNECTION_FAILED)
    if isinstance(error, OSError):
        if error.errno in FILE_REASONS:
            return Text(FILE_REASONS[error.errno])
        return Text(FILE_FAILED, reason=error.strerror) if error.strerror else Text(failed)
    return Text(failed)


def error_message(error, failed=FAILED, redact=None):
    """``describe_error`` in the interface language now: the message of an operator's report."""
    return shown(describe_error(error, failed, redact))


def operator_reason(message):
    """The whole reason of a refused operator: every report ``bpy.ops`` raised, one per line, without the "Error: "
    Blender puts before each. Blender ends each report with a newline; text that doesn't and doesn't start with one
    of Blender's report types (an error raised with one sentence) is kept whole, colons included."""
    text = str(message)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not text.endswith("\n") and not (lines and _REPORT_TYPES.match(lines[0])):
        return "\n".join(lines)
    return "\n".join(_REPORT_PREFIX.sub("", line, count=1) for line in lines)


def _refusal(error):
    """The sentence of a refusal written for the person, else ``""``.

    That is an error class of Angelo's own (``FlowError``, ``FetchError``...) or a plain ``ValueError`` or
    ``RuntimeError``, raised with one sentence. A refused operator's ``RuntimeError`` gives its whole reason. Text
    that starts in lower case is Python's own wording raised as a plain ``ValueError`` (``int("x")``,
    ``list.index``, unpacking), which Angelo's sentences never are; scripts without letter case pass. Other
    classes from Python or a library (``KeyError``, ``UnicodeDecodeError``, ``json.JSONDecodeError``, an ``OSError``
    with a path) describe the program, not something the person can act on.
    """
    kind = type(error)
    if not (kind in (ValueError, RuntimeError) or kind.__module__.startswith(_PACKAGE + ".")):
        return ""
    if len(error.args) != 1 or not isinstance(error.args[0], str):
        return ""
    sentence = operator_reason(error.args[0]) if kind is RuntimeError else error.args[0].strip()
    if sentence.startswith("Operator bpy.ops.") or sentence[:1].islower():
        return ""   # Blender's poll failure, or Python's own wording ("invalid literal for int()..."): no sentence.
    return sentence


def _log(error, redact):
    try:
        trace = redact("".join(traceback.format_exception(error)))
        sys.stderr.write("Angelo: an error shown as a short message had these details:\n%s" % trace)  # i18n: log line
    except (AttributeError, OSError, ValueError):
        pass   # No usable console: the person still reads the short message.
