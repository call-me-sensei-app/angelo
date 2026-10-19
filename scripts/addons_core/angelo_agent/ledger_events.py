# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider signals that decide which ledger a turn commits (``context_ledger``).

Each is a callback from a provider worker: the input it acknowledged, a
runtime compaction and a measured prompt size. They only record facts on the
host; ``Controller._commit_ledger`` acts on them on the main thread at the
end of the turn. A stale request fence or a stopped turn records nothing.
"""

import copy

from .request_fences import RequestFence

# Runtime-session fields that describe one native context: kept while it continues (``Controller.remember_runtime``).
CONTEXT_FIELDS = ("received", "context_tokens", "identifiers", "cache_ttl", "model", "forked", "context_window",
                  "context_full")
# Codex thread facts a provider may record for the chat's runtime session (``context_rebase``).
FACTS = frozenset({"context_window", "context_full"})


def record_accepted(host, *, request_fence=None):
    """Keep only the context facts carried by an acknowledged provider input.

    Later rounds can update the pending observation before their send is
    accepted. Snapshot its bounded digests now, without saving on a worker.
    """
    if request_fence is not None and (not request_fence.matches(host) or host.cancelled):
        return
    host._turn_delivered = True
    pending = host._pending_ledger
    if pending is not None:
        blocks = copy.copy(pending["blocks"])
        blocks.after = dict(blocks.after)
        blocks.jobs_sent = list(blocks.jobs_sent)
        host._accepted_ledger = dict(pending, blocks=blocks)


def record_compaction(host, *, request_fence=None):
    """The runtime summarized earlier messages away: the next message is complete."""
    if (request_fence is not None and (not isinstance(request_fence, RequestFence) or not request_fence.matches(host))
            or host.cancelled):
        return
    host._turn_compacted = True


def record_tokens(host, tokens, *, request_fence=None):
    """A Codex model request's prompt size: the thread's context size (``context_ledger.compacted``)."""
    if (type(tokens) is not int or tokens < 0 or request_fence is not None
            and (not isinstance(request_fence, RequestFence) or not request_fence.matches(host)) or host.cancelled):
        return
    host._turn_round_inputs.append(tokens)


def record_fact(host, name, value, *, request_fence=None):
    """A Codex thread fact for its runtime session (``context_rebase``): the context window Codex reports
    (``context_window``, a positive int) or a turn that window refused (``context_full``, True)."""
    if (name not in FACTS or value is None or request_fence is not None
            and (not isinstance(request_fence, RequestFence) or not request_fence.matches(host)) or host.cancelled):
        return
    host._turn_context_facts[name] = value
