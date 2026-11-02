# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a chat is told about Angelo's tools: only the tools it has, declared or deferred. No bpy.

Worker chats were told about tools they did not have (angelo_view, then angelo_job_start, angelo_delegate,
angelo_blend_file, angelo_set_workspace_mode), and a worker offered a tool it could not run planned with it, was
refused and ended its turn without building its part (live trial 2026-10-24). So instruction text is made of blocks,
and a block that names an ``angelo_*`` tool a chat neither declares nor defers is left out of that chat's text
(``told``). Which tools a block names is read from its text (``named``, and ``families`` for a family written
with a wildcard, which a chat needs one member of), never listed by hand.

Blocks that name a tool a worker's chat lacks follow the text every chat of a runtime shares, so a lead and its
workers send the same prefix byte for byte and share its prompt cache (claude_text.CLAUDE_TAIL,
providers.codex_tail). ``test_native_tool_mentions`` pins both: no kind of chat is told of a tool it lacks, and the
shared text names only tools every worker has.
"""

import fnmatch
import re

NAME = re.compile(r"(?<![A-Za-z0-9])angelo_[a-z0-9_]*[a-z0-9]")   # more__angelo_job_start names angelo_job_start.
# A family written with a wildcard (angelo_studio_*, angelo_*sculpt*): its members are the catalog's tools it matches.
FAMILY = re.compile(r"(?<![A-Za-z0-9])angelo_[a-z0-9_*]*\*[a-z0-9_*]*")


def _catalog(catalog):
    if catalog is None:
        from .tool_specs import TOOL_MAP
        return TOOL_MAP
    return catalog


def named(text, catalog=None):
    """The tools ``text`` names: ``angelo_*`` words that are tools of ``catalog`` (default: every Angelo tool)."""
    catalog = _catalog(catalog)
    return {name for name in NAME.findall(text) if name in catalog}


def families(text, catalog=None):
    """The tool families ``text`` names with a wildcard, each as the set of ``catalog`` tools it matches (a pattern
    matching none names nothing)."""
    catalog = _catalog(catalog)
    found = (frozenset(fnmatch.filter(catalog, pattern)) for pattern in FAMILY.findall(text))
    return [members for members in found if members]


def fits(text, allowed, catalog=None):
    """Whether a chat with ``allowed`` (None: every tool) has every tool ``text`` names and a member of every family."""
    if allowed is None:
        return True
    have = set(allowed)
    return named(text, catalog) <= have and all(members & have for members in families(text, catalog))


class Kept(str):
    """A block every chat reads: never left out, so a tool it names that a chat lacks fails test_native_tool_mentions
    instead of silently dropping the block's other text."""


def told(blocks, allowed, catalog=None):
    """``blocks`` joined in order, leaving out each that names a tool outside ``allowed`` (None: every tool)."""
    if allowed is None:
        return "".join(blocks)
    return "".join(block for block in blocks if isinstance(block, Kept) or fits(block, allowed, catalog))


def split(text, *gated):
    """``text`` as blocks: each of ``gated`` (each found once, in order) its own block, the text between them
    ``Kept``. Joined, they are ``text`` byte for byte."""
    blocks, rest = [], text
    for piece in gated:
        before, found, rest = rest.partition(piece)
        if not found or piece in rest:
            raise ValueError("a gated block must occur once, in order: %r" % piece[:60])
        blocks += [Kept(before), piece]
    return tuple(blocks + [Kept(rest)])


def has_any(names, allowed):
    """Whether a chat with ``allowed`` (None: every tool) has any of ``names`` (a family named in prose)."""
    return allowed is None or bool(set(names) & set(allowed))


_SENTENCE_END = re.compile(r"(?<=\.) (?=[A-Z])")


def described(text, allowed):
    """A tool's description without its sentences that name a tool outside ``allowed`` (None: every tool); a
    description naming none is returned as it is."""
    if fits(text, allowed):
        return text
    return " ".join(sentence for sentence in _SENTENCE_END.split(text) if fits(sentence, allowed))
