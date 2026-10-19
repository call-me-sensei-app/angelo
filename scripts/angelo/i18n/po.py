# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A small GNU gettext ``.po`` reader and writer, enough for Angelo's catalogues.

Dependency-free so host tests and the catalogue tools run anywhere. Handles
``msgctxt``, multi-line strings, translator and extracted comments, source
references, flags and obsolete (``#~``) entries. Plural ``msgid_plural`` is not
used: Blender's catalogues have no plural forms (see ``ui_text.plural_``).
"""

import re

ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}
_ESCAPE = re.compile(r"\\(.)")


def unescape(text):
    return _ESCAPE.sub(lambda match: ESCAPES.get(match.group(1), "\\" + match.group(1)), text)


def escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


class Entry:
    __slots__ = ("ctxt", "id", "str", "comments", "extracted", "refs", "flags", "obsolete")

    def __init__(self, id, str="", ctxt="", comments=(), extracted=(), refs=(), flags=(), obsolete=False):
        self.ctxt, self.id, self.str = ctxt, id, str
        self.comments, self.extracted, self.refs = list(comments), list(extracted), list(refs)
        self.flags, self.obsolete = list(flags), obsolete

    @property
    def key(self):
        return (self.ctxt, self.id)


def _quoted(line):
    start, end = line.index('"'), line.rindex('"')
    return unescape(line[start + 1:end])


def parse(text):
    """Return ``(header_text, entries)``; the header is the ``msgstr`` of the empty msgid."""
    entries, current, field = [], None, None
    pending = {"comments": [], "extracted": [], "refs": [], "flags": []}

    def flush():
        nonlocal current
        if current is not None:
            entries.append(current)
        current = None

    for raw in text.splitlines():
        line = raw.strip()
        obsolete = line.startswith("#~")
        if obsolete:
            line = line[2:].strip()
        if not line:
            flush()
            field = None
            pending = {"comments": [], "extracted": [], "refs": [], "flags": []}
            continue
        if line.startswith("#") and not obsolete:
            if current is not None and field == "str":
                flush()
                pending = {"comments": [], "extracted": [], "refs": [], "flags": []}
            if line.startswith("#."):
                pending["extracted"].append(line[2:].strip())
            elif line.startswith("#:"):
                pending["refs"].append(line[2:].strip())
            elif line.startswith("#,"):
                pending["flags"].extend(flag.strip() for flag in line[2:].split(",") if flag.strip())
            elif line.startswith("#|"):
                pass
            else:
                pending["comments"].append(line[1:].strip())
            continue
        if line.startswith("msgctxt"):
            flush()
            current = Entry("", ctxt=_quoted(line), obsolete=obsolete, **pending)
            pending = {"comments": [], "extracted": [], "refs": [], "flags": []}
            field = "ctxt"
        elif line.startswith("msgid"):
            if current is None or field == "str":
                flush()
                current = Entry("", obsolete=obsolete, **pending)
                pending = {"comments": [], "extracted": [], "refs": [], "flags": []}
            current.id = _quoted(line)
            field = "id"
        elif line.startswith("msgstr"):
            current.str = _quoted(line)
            field = "str"
        elif line.startswith('"') and current is not None:
            value = _quoted(line)
            if field == "ctxt":
                current.ctxt += value
            elif field == "id":
                current.id += value
            else:
                current.str += value
    flush()
    header = ""
    for entry in entries:
        if entry.id == "" and entry.ctxt == "":
            header = entry.str
            entries.remove(entry)
            break
    return header, entries


def _field(name, value):
    if "\n" in value and len(value) > 1:
        lines = value.split("\n")
        body = [f'"{escape(part)}\\n"' for part in lines[:-1]] + ([f'"{escape(lines[-1])}"'] if lines[-1] else [])
        return [f'{name} ""'] + body
    return [f'{name} "{escape(value)}"']


def dump(header, entries):
    out = []
    out += ['msgid ""', 'msgstr ""'] + [f'"{escape(part)}\\n"' for part in header.rstrip("\n").split("\n")]
    for entry in entries:
        out.append("")
        out += [f"# {comment}" if comment else "#" for comment in entry.comments]
        out += [f"#. {comment}" for comment in entry.extracted]
        out += [f"#: {ref}" for ref in entry.refs]
        if entry.flags:
            out.append("#, " + ", ".join(entry.flags))
        prefix = "#~ " if entry.obsolete else ""
        lines = []
        if entry.ctxt:
            lines += _field("msgctxt", entry.ctxt)
        lines += _field("msgid", entry.id) + _field("msgstr", entry.str)
        out += [prefix + line for line in lines]
    return "\n".join(out) + "\n"


def read(path):
    with open(path, encoding="utf-8") as handle:
        return parse(handle.read())


def write(path, header, entries):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump(header, entries))


def header_fields(header):
    fields = {}
    for line in header.split("\n"):
        if ":" in line:
            name, value = line.split(":", 1)
            fields[name.strip()] = value.strip()
    return fields
