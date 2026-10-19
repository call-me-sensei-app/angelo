# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The markdown an assistant reply may use, parsed into blocks the chat draws natively. Pure Python.

API:
    parse(text) -> tuple[Block, ...]
        Block kinds: "heading" (level 1-3), "paragraph", "bullet" / "number" (``level`` is the indent depth,
        ``marker`` is "•" or "3."), "quote", "code" (``lang``; ``closed`` is False while a fence is still
        streaming), "rule".
    inline(text) -> (plain, spans)
        ``plain`` is the text with emphasis markers removed; ``spans`` are (start, end, kind, url) with kind
        "code" (inline code, drawn as a mono chip) or "link" (``url`` is a safe http(s) URL).

Never executes or fetches anything: a link is only a label and a checked URL. Partial markdown (a reply still
streaming) parses to something sensible: an unclosed fence is an open code block, an unclosed backtick or
link bracket stays literal text.
"""

import re
from collections import namedtuple

from .markdown_text import safe_url

Block = namedtuple("Block", "kind text level marker lang closed")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_QUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
_RULE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^\s)]+)\)|<(https?://[^\s>]+)>")
_EMPHASIS = re.compile(r"\*\*|__")


def _block(kind, text="", level=0, marker="", lang="", closed=True):
    return Block(kind, text, level, marker, lang, closed)


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
        else:
            prefix = (block.marker + " ") if block.kind in {"bullet", "number"} else ""
            rows.append("  " * block.level * (block.kind in {"bullet", "number"}) + prefix + inline(block.text)[0])
    return "\n".join(rows)
