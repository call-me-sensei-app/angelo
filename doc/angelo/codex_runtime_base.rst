.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Native runtimes: Codex and Claude Code
**************************************

Angelo reuses established agent runtimes rather than building or maintaining
an independent agent engine.

Reuse, not a new agent engine
=============================

The Codex app-server runs as a local sidecar: the bundled agent runtime
(upstream Codex at the version ``agent_runtime.json`` pins, plus one
parameter, below) or, where none is bundled or ``ANGELO_AGENT_RUNTIME=off``,
the person's own unmodified Codex. Its public lifecycle is the foundation of
this integration:

* ``thread/start`` / ``thread/resume`` own native conversation persistence.
* ``turn/start`` owns inference, runtime context management and streaming.
* ``item/*`` notifications provide messages, public reasoning summaries and
  native web-tool activity; ``turn/completed`` supplies the actual terminal state.
* ``turn/interrupt`` cancels the native request.
* Dynamic host tools dispatch directly to Blender's main thread, without MCP.

The native runtime retains responsibility for authentication, model access,
context compaction, provider caching, retries and its inference/tool loop. Do
not reimplement those subsystems, parse private rollout files, extract OAuth
tokens, or emulate undocumented subscription HTTP endpoints. Follow the public
app-server protocol and validate runtime upgrades in the fixture/live tests.

Codex CLI source is Apache-2.0 licensed. Angelo bundles one modified build of
it, its agent runtime: upstream Codex plus an app-server parameter,
``cacheAffinityKey``, that lets new chats share a cached prompt prefix, until
upstream ships an equivalent (``gpt_launch.rst``, "Agent runtime and prompt
caching"; its license and the statement of the change ship in
``release/license/agent-runtime``). The add-on still copies no Codex source,
drives the runtime only through the public app-server protocol plus that one
parameter, and assumes nothing about the desktop frontend's source. Stock
Codex ignores the parameter, so the same protocol drives either build.

The small Angelo-specific layer
===============================

The host keeps only the responsibilities that the native runtime cannot own:

* Blender editor views, drafts, attached reference snapshots and request restore.
* Document identity, main-thread dispatch, exact-script review and scene approvals.
* One shared UI conversation with a native context/cursor per connection UUID.
* Cross-provider handoffs, visible usage approval and bounded read-only consulting.

Switching GPT to Claude does not migrate a Codex native thread into Claude Code.
Both native contexts resume independently. A bounded public handoff contains the
messages and verified results the returning context missed, excluding reasoning
internals. Host history and checkpoints remain in the same conversation. A
restore/reauthentication invalidates affected native contexts, not the UI history.

Claude is an adapter, not another custom engine
===============================================

Claude support uses Anthropic's unmodified CLI programmatic interface and native
session IDs/resume. The adapter converts native streaming/typed-plan output into
the same host UI/tool contract. Its few host-plan continuation rounds exist only
to feed actual Blender results back across the no-MCP boundary; Claude Code owns
the underlying agent and context behavior. Do not copy its proprietary engine.

Subscription users sign in through the native binary with their own account.
API and subscription connections remain distinct, with no billing fallback.
Offering subscription sign-in is subject to Anthropic's product and credential
conditions (see Sources); using an SDK is not itself permission to offer
subscription OAuth in a custom client. Keep all supported native
authentication methods available.

Prefix terminology
==================

Provider-qualified display IDs can use a prefix, but routing must retain the
explicit connection UUID as well as the actual native model ID. ``gpt-*`` or
``claude-*`` alone cannot distinguish a subscription from a paid API profile.
Do not send a made-up ``codex/`` or ``claude/`` prefix to a native CLI and assume
it will strip or interpret it.

Prompt-prefix caching is a different mechanism. Both providers document prefix
reuse, but a cache hit requires compatible model/provider/settings and matching
content. There is no portable GPT-to-Claude KV cache. Native resume preserves
each runtime's history; stable instructions/tool schemas and incremental
handoffs avoid unnecessarily retransmitting already-synced host history. We
make no cache-hit or cross-provider latency guarantee without measurements.

Sources
=======

* `Codex source and Apache license <https://github.com/openai/codex>`__
* `Codex app-server lifecycle <https://learn.chatgpt.com/docs/app-server#lifecycle-overview>`__
* `Native thread start/resume <https://learn.chatgpt.com/docs/app-server#start-or-resume-a-thread>`__
* `Claude programmatic interface <https://code.claude.com/docs/en/headless>`__
* `Claude sessions <https://code.claude.com/docs/en/agent-sdk/sessions>`__
* `Claude product/credential conditions <https://code.claude.com/docs/en/legal-and-compliance>`__
* `OpenAI prefix matching <https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics#how-it-works>`__
* `Anthropic prefix matching <https://platform.claude.com/docs/en/build-with-claude/prompt-caching>`__
