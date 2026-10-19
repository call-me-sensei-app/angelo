# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stable prompt-cache routing keys for Codex threads: ``cacheAffinityKey`` (doc/angelo/gpt_launch.rst).

The ChatGPT backend reuses a cached prompt prefix by session affinity, not by
identical bytes: a thread's requests carry a routing key (``prompt_cache_key``
and the ``session-id`` header), which stock Codex sets to the thread's own ID,
so a new chat's first request never reads what an earlier chat cached. Angelo's
agent runtime (``agent_runtime``) accepts an explicit key instead. Every new
chat whose startup context is the same as an earlier chat's sends the same key
and reads that chat's cached opening; a different startup context gets a
different key, so unrelated prefixes never compete for one route.

``derive`` is a one-way digest of exactly what the chats sharing a cached
opening have in common, and of nothing else: the account, the model (caches are
per model), the static prefix and ``TOOLSET_VERSION``. The account is
``Runtime.account`` (a digest of its type and email; the email is never kept)
with the connection mode, digested with HMAC-SHA256 under the per-user secret
(``cache_affinity_secret``): every other input is public, so without the secret
anyone who guessed an email could recompute the key. With it, nobody outside
this user's Angelo can link a key to an account. Reasoning effort is not an
input: it is a request parameter, not prompt bytes, so a key per effort could
only route a chat away from a warm cache. The static prefix (``prefix_parts``)
is what Codex sends before the first per-chat content: the base instructions,
the dynamic tools, the model provider, sandbox and approval settings, and the
turn's output schema. The working directory, the date and the time zone reach
the model only in Codex's environment context, after that prefix; the
connection's ID, the profile, the state folder and every process, chat and
thread identity reach it not at all, so none of them is an input: two launches
with different state folders, or two connections of one account, send the same
key for the same prefix. The key is ``angelo-`` and 40 hexadecimal digits,
within the runtime's 1-64 visible ASCII characters. It is a stable pseudonym of
(account, model, prefix): the same on every request of every chat that shares
them, and so linkable across those requests by whoever sees them (OpenAI), but
it holds no email, path or connection identifier. ``ANGELO_CACHE_KEY_TRACE=1``
prints each key with the digests of its public inputs (``trace``; never the
account's) to the app's standard error.

``check`` asks the app-server itself, once per process, in two steps that create
nothing and reach no model: a ``thread/fork`` of a random thread ID with an
invalid key must be refused with exactly the patched build's validation error
(``VALIDATION``), and the same fork with a valid key must get past validation to
"no rollout found". Stock Codex ignores the unknown field and answers "no
rollout found" to the first step. A process that exits during the check, or
does not answer it within ``PROBE_TIMEOUT``, is reported as that, not as the
wrong build (``CheckFailed``: a translatable chat error naming the agent runtime).
"""

import hashlib
import hmac
import json
import re
import sys
import uuid

from . import product_env
from .protocol import RpcError
from .ui_text import n_, notice

PREFIX = "angelo-"
DIGITS = 40
DOMAIN = "angelo-cache-affinity/3"  # A new derivation changes this, so old and new keys never collide.
MAX_LENGTH = 64
_VALID = re.compile(r"^[\x21-\x7e]{1,%d}$" % MAX_LENGTH)
INVALID_PROBE_KEY = " "  # Not a visible ASCII character: the patched build refuses it.
VALID_PROBE_KEY = "angelo-probe"
VALIDATION = "cacheAffinityKey must be 1-64 visible ASCII characters"  # The patched build's refusal, exactly.
NO_ROLLOUT = "no rollout found"  # Any build's answer to a fork of a thread that does not exist.
PROBE_TIMEOUT = 10
# The support check's verdicts (``check``).
SUPPORTED, NOT_PATCHED, EXITED, NO_ANSWER = "supported", "not patched", "exited", "no answer"
_EXITED = ("Agent process exited", "Agent process has disconnected")  # protocol.JsonRpcProcess's words.
FAILURES = {
    EXITED: n_("Angelo's agent runtime stopped while Angelo was checking it. Send your message again. If this "
               "keeps happening, reinstall Angelo."),
    NO_ANSWER: n_("Angelo's agent runtime did not answer Angelo's check within {seconds} seconds. Send your "
                  "message again."),
    NOT_PATCHED: n_("Angelo's agent runtime is not the Codex build this version of Angelo runs. Reinstall Angelo."),
}


def valid(key):
    """Whether ``key`` is what the runtime accepts: 1-64 visible ASCII characters."""
    return isinstance(key, str) and bool(_VALID.match(key))


# What Codex sends before the first per-chat content, by request field (doc/angelo/gpt_launch.rst, "Agent
# runtime and prompt caching"). ``cwd`` is deliberately absent: it reaches the model only in the environment
# context that follows the prefix, and it is a per-launch state folder in the benchmarks. So is the turn's
# ``effort``: a request parameter, not prompt bytes (none of these fields depends on it).
PREFIX_THREAD = {"instructions": ("baseInstructions",), "tools": ("dynamicTools",),
                 "settings": ("model", "modelProvider", "sandbox", "approvalPolicy")}
PREFIX_TURN = {"schema": ("outputSchema",)}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def prefix_parts(start, turn):
    """``{part: digest}`` of the static prefix of a thread opened with ``start`` and run with ``turn``."""
    parts = {name: _digest({key: start.get(key) for key in keys}) for name, keys in PREFIX_THREAD.items()}
    parts.update({name: _digest({key: turn.get(key) for key in keys}) for name, keys in PREFIX_TURN.items()})
    return parts


def account_digest(mode, identity, secret=None):
    """The account as a key input: HMAC-SHA256 of the mode and ``Runtime.account`` under the per-user secret."""
    if secret is None:
        from .cache_affinity_secret import secret as stored
        secret = stored()
    material = json.dumps([DOMAIN, str(mode or ""), str(identity or "")], separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def inputs(account, start, turn, toolset_version, secret=None):
    """Everything ``derive`` reads: the keyed account digest, the model, the prefix digests and the toolset."""
    mode, identity = account
    return {"account": account_digest(mode, identity, secret),
            "model": str(start.get("model") or turn.get("model") or ""),
            "prefix": prefix_parts(start, turn), "toolset": int(toolset_version)}


def derive(values):
    """The routing key for the chats ``inputs`` describes."""
    return PREFIX + _digest([DOMAIN, values])[:DIGITS]


def trace(key, values, environ=None):
    """``ANGELO_CACHE_KEY_TRACE=1``: one line per key on standard error with its public inputs' digests.

    The account's digest is left out: a trace line is shared to compare runs, and the key already tells
    whether two runs had the same account.
    """
    if product_env.current("CACHE_KEY_TRACE", environ) == "1":
        shown = {"model": values["model"], "toolset": values["toolset"],
                 "prefix": {name: digest[:16] for name, digest in values["prefix"].items()}}
        print("ANGELO_CACHE_KEY " + json.dumps({"key": key, **shown}, sort_keys=True), file=sys.stderr, flush=True)


def _ask(rpc, key, thread):
    """What a ``thread/fork`` of ``thread`` with ``key`` answered: the error's text, ``""`` for an answer,
    or ``EXITED`` / ``NO_ANSWER``."""
    try:
        rpc.request("thread/fork", {"threadId": thread, "cacheAffinityKey": key}, timeout=PROBE_TIMEOUT)
    except TimeoutError:
        return NO_ANSWER
    except RpcError as error:
        return EXITED if str(error) in _EXITED else str(error)
    except (OSError, ValueError):  # The pipe to an exited process is closed.
        return EXITED
    return ""  # A fork of a thread that does not exist cannot succeed: the key was not checked.


def check(rpc):
    """``SUPPORTED``, ``NOT_PATCHED``, ``EXITED`` or ``NO_ANSWER`` for this app-server (module docstring)."""
    thread = str(uuid.uuid4())
    refused = _ask(rpc, INVALID_PROBE_KEY, thread)
    if refused in (EXITED, NO_ANSWER):
        return refused
    if refused != VALIDATION:
        return NOT_PATCHED
    accepted = _ask(rpc, VALID_PROBE_KEY, thread)
    if accepted in (EXITED, NO_ANSWER):
        return accepted
    return SUPPORTED if accepted.startswith(NO_ROLLOUT) else NOT_PATCHED


def supported(rpc):
    """Whether this app-server accepts ``cacheAffinityKey`` (no thread is created, nothing is billed)."""
    return check(rpc) == SUPPORTED


class CheckFailed(RpcError):
    """The agent runtime failed its support check: the English sentence, and the template the chat translates."""

    def __init__(self, verdict):
        message = notice(FAILURES[verdict], **({"seconds": PROBE_TIMEOUT} if verdict == NO_ANSWER else {}))
        super().__init__(message["text"])
        self.verdict, self.i18n = verdict, message.get("i18n")
