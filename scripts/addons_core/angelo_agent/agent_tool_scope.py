# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an agent is acting on this thread now: agent code (``grant_guard.inside_agent_code``) or an agent's tool
call (``BlenderExecutor.execute``, marked by ``as_agent_tool``), confined or not. A grant refusal met then is the
agent's: it never takes the person's unarmed press for a hint (``grant_hints.take``; the review of 69d589a, NIT)."""

import functools
import threading

_here = threading.local()   # Agent tool calls running on this thread now.


class agent_tool:
    """``with agent_tool(): ...``: an agent's tool call running on this thread."""

    def __enter__(self):
        _here.tools = getattr(_here, "tools", 0) + 1
        return self

    def __exit__(self, *_exc):
        _here.tools -= 1
        return False


def as_agent_tool(method):
    """``method``, run inside ``agent_tool`` (``BlenderExecutor.execute``: every agent tool call)."""
    @functools.wraps(method)
    def run(*args, **kwargs):
        with agent_tool():
            return method(*args, **kwargs)
    return run


def agent_acting():
    """Whether agent code or an agent's tool call is running on this thread now."""
    from . import grant_guard
    return grant_guard.inside_agent_code() or getattr(_here, "tools", 0) > 0
