# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The markdown an assistant reply may use, parsed into blocks the chat draws natively. Pure Python.

API:
    parse(text) -> tuple[Block, ...]
        Block kinds: "heading" (level 1-3), "paragraph", "bullet" / "number" (``level`` is the indent depth,
        ``marker`` is "•" or "3."), "quote", "code" (``lang``; ``closed`` is False while a fence is still
        streaming), "rule", "table" (``cells``: the header row then the body rows, each a tuple of raw cell
        markdown, every row as wide as the header; ``aligns``: "left", "right", "center" or "" per column;
        ``text`` is the table's source lines).
    inline(text) -> (plain, spans)
        ``plain`` is the text with emphasis markers removed; ``spans`` are (start, end, kind, url) with kind
        "code" (inline code, drawn as a mono chip) or "link" (``url`` is a safe http(s) URL).

Never executes or fetches anything: a link is only a label and a checked URL. Partial markdown (a reply still
streaming) parses to something sensible: an unclosed fence is an open code block, an unclosed backtick or
link bracket stays literal text, and a table whose delimiter row is still arriving (the reply's last line, not yet
ended) is a table of its header.

Tables follow GitHub's rules where they matter for a reply: a header row, then a delimiter row of ``---`` cells
(``:---``, ``---:`` and ``:---:`` align a column) with as many cells as the header; leading and trailing pipes are
optional and ``\\|`` is a literal pipe. A body row may be short (padded with empty cells) or long (extra cells
dropped). The table ends at a blank line or the first line without a pipe. A line with pipes that is not followed
by a delimiter row stays a paragraph.
"""

import re
from collections import namedtuple

from .markdown_text import safe_url

Block = namedtuple("Block", "kind text level marker lang closed cells aligns", defaults=((), ()))

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_QUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
_RULE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^\s)]+)\)|<(https?://[^\s>]+)>")
_EMPHASIS = re.compile(r"\*\*|__")
_PIPE = re.compile(r"(?<!\\)\|")                # An unescaped pipe: a cell boundary.
_DELIMITER_CELL = re.compile(r"^:?-+:?$")
_PARTIAL_DELIMITER_CELL = re.compile(r"^:?-*:?$")  # A delimiter cell still streaming ("", ":", ":-", "--").


def _block(kind, text="", level=0, marker="", lang="", closed=True, cells=(), aligns=()):
    return Block(kind, text, level, marker, lang, closed, cells, aligns)


def _cells(line):
    """A table row's cells: outer pipes optional, ``\\|`` a literal pipe, each cell stripped."""
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _PIPE.split(row)]


def _align(cell):
    left, right = cell.startswith(":"), cell.endswith(":") and len(cell) > 1
    return "center" if left and right else "right" if right else "left" if left else ""


def _delimiter(line, count, partial):
    """The column alignments of a delimiter row for a header of ``count`` cells, or None when it is not one.

    ``partial``: the line is the reply's unfinished last line, so its cells may still be arriving."""
    if not _PIPE.search(line) or ("-" not in line and not partial) or set(line.strip()) - set("|:- \t"):
        return None
    cells = [cell.replace(" ", "") for cell in _cells(line)]
    if partial:
        if len(cells) > count or not all(_PARTIAL_DELIMITER_CELL.match(cell) for cell in cells):
            return None
        cells += [""] * (count - len(cells))
    elif len(cells) != count or not all(_DELIMITER_CELL.match(cell) for cell in cells):
        return None
    return tuple(_align(cell) for cell in cells)


def _table(lines, index):
    """A table whose header is ``lines[index]``: (block, the index after it), or None when no table starts there."""
    header = lines[index]
    if index + 1 >= len(lines) or not _PIPE.search(header):
        return None
    head = _cells(header)
    aligns = _delimiter(lines[index + 1], len(head), partial=index + 1 == len(lines) - 1)
    if aligns is None:
        return None
    rows, end = [tuple(head)], index + 2
    while end < len(lines) and lines[end].strip() and _PIPE.search(lines[end]) and not _FENCE.match(lines[end]):
        cells = _cells(lines[end])[:len(head)]
        rows.append(tuple(cells + [""] * (len(head) - len(cells))))
        end += 1
    closed = end < len(lines)   # Its delimiter or last row may still be streaming while it ends the reply.
    return _block("table", "\n".join(lines[index:end]), closed=closed, cells=tuple(rows), aligns=aligns), end


def parse(text):
    """Blocks of a reply, in order; a paragraph joins its soft-wrapped lines with spaces."""
    blocks, paragraph, quote = [], [], []

    def flush():
        if paragraph:
            blocks.append(_block("paragraph", " ".join(part.strip() for part in paragraph)))
            paragraph.clear()
        if quote:
            blocks.append(_block("quote", "\n".join(quote).strip()))
            quote.clear()

    lines = str(text or "").replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        fence = _FENCE.match(line)
        if fence:
            flush()
            mark, body = fence.group(1), []
            closed = False
            while index < len(lines):
                current = lines[index]
                index += 1
                if current.strip().startswith(mark[0] * len(mark)) and not current.strip().strip(mark[0]):
                    closed = True
                    break
                body.append(current)
            blocks.append(_block("code", "\n".join(body).rstrip("\n"), lang=fence.group(2), closed=closed))
            continue
        if not line.strip():
            flush()
            continue
        quoted = _QUOTE.match(line)
        if quoted:
            if paragraph:
                flush()
            quote.append(quoted.group(1))
            continue
        if quote:
            flush()
        heading = _HEADING.match(line)
        if heading:
            flush()
            blocks.append(_block("heading", heading.group(2), level=min(3, len(heading.group(1)))))
            continue
        if _RULE.match(line):
            flush()
            blocks.append(_block("rule"))
            continue
        table = _table(lines, index - 1)
        if table:
            flush()   # A paragraph line right above the header stays its own paragraph.
            blocks.append(table[0])
            index = table[1]
            continue
        bullet, number = _BULLET.match(line), _NUMBER.match(line)
        if bullet or number:
            flush()
            indent = len((bullet or number).group(1).expandtabs(4))
            level = min(3, indent // 2)
            if bullet:
                blocks.append(_block("bullet", bullet.group(2), level=level, marker="•" if level % 2 == 0 else "◦"))
            else:
                blocks.append(_block("number", number.group(3), level=level, marker=number.group(2) + "."))
            continue
        if blocks and blocks[-1].kind in {"bullet", "number"} and not paragraph and line[:1].isspace():
            last = blocks.pop()  # A lazy continuation of the list item above it.
            blocks.append(last._replace(text=last.text + " " + line.strip()))
            continue
        paragraph.append(line)
    flush()
    return tuple(blocks)


def inline(text):
    """``text`` without emphasis markers, with its inline code and link spans in plain-text offsets."""
    source, plain, spans, index = str(text or ""), [], [], 0
    length = 0
    while index < len(source):
        char = source[index]
        if char == "`":
            run = len(source[index:]) - len(source[index:].lstrip("`"))
            close = source.find("`" * run, index + run)
            if close != -1:
                code = source[index + run:close]
                if code.strip():
                    code = code.strip() if code.startswith(" ") and code.endswith(" ") else code
                    spans.append((length, length + len(code), "code", ""))
                    plain.append(code)
                    length += len(code)
                index = close + run
                continue
            plain.append(source[index:index + run])  # Unclosed: a stream may still close it; literal for now.
            length += run
            index += run
            continue
        link = _LINK.match(source, index) if char in "[<" else None
        if link:
            label, url = (link.group(1), link.group(2)) if link.group(1) else (link.group(3), link.group(3))
            label = _EMPHASIS.sub("", label)
            if safe_url(url):
                spans.append((length, length + len(label), "link", url))
            plain.append(label)
            length += len(label)
            index = link.end()
            continue
        emphasis = _EMPHASIS.match(source, index)
        if emphasis:
            index = emphasis.end()
            continue
        plain.append(char)
        length += 1
        index += 1
    return "".join(plain), spans


def plain_text(text):
    """The reply as readable plain text (copy fallback and accessibility): markers removed, code kept."""
    rows = []
    for block in parse(text):
        if block.kind == "code":
            rows.append(block.text)
        elif block.kind == "rule":
            rows.append("")
        elif block.kind == "table":   # Tab-separated: reads as columns, and pastes into a spreadsheet as cells.
            rows.extend("\t".join(inline(cell)[0] for cell in row) for row in block.cells)
        else:
            prefix = (block.marker + " ") if block.kind in {"bullet", "number"} else ""
            rows.append("  " * block.level * (block.kind in {"bullet", "number"}) + prefix + inline(block.text)[0])
    return "\n".join(rows)
