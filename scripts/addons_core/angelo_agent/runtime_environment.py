# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a native runtime child inherits from the environment that started Angelo.

Angelo chooses each child's account, endpoint, model, effort and prompt-cache
TTL itself (``providers.py``). A variable left in the shell that launched
Blender must not silently change any of them: a different billing route, a
remapped model alias, disabled or re-timed prompt caching, a different context
window or a nested-session marker each change what a request costs or which
account pays. Those variables are dropped; everything else, including the
proxy, CA-bundle and mTLS settings a person needs on their network, passes.

Names come from the documentation read 2026-10-08:
Claude Code https://code.claude.com/docs/en/env-vars and
https://code.claude.com/docs/en/prompt-caching; Codex
https://learn.chatgpt.com/docs/config-file/environment-variables and
https://learn.chatgpt.com/docs/config-file/config-reference.
Update this list when either runtime documents a new variable of these kinds.
"""

import uuid

from .protocol import RpcError
from .storage import valid_profile_directory

# Credentials and identities: the connection supplies its own or none.
CREDENTIALS = frozenset({
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
    "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_WORKSPACE_ID",
    "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    "AWS_BEARER_TOKEN_BEDROCK",
    "XAI_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "MINIMAX_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "ARK_API_KEY", "TRIPO_API_KEY", "MESHY_API_KEY",
    # Where Claude Code reads a stored sign-in, independently of CLAUDE_CONFIG_DIR, and the
    # credentials an enclosing Claude Code session hands its children (Claude Code 2.1.293's
    # code; not in its env-vars page): an empty CLAUDE_SECURESTORAGE_CONFIG_DIR selects the
    # person's own default Keychain entry.
    "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_HOST_CREDS_FILE", "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
})

# Endpoints, providers and extra request content: they move billing or change requests.
ENDPOINTS = frozenset({
    "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
    "CODEX_OSS_BASE_URL", "CODEX_OSS_PORT",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "ANTHROPIC_BETAS", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_EXTRA_BODY",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
})
ENDPOINT_PREFIXES = ("ANTHROPIC_BEDROCK_", "ANTHROPIC_VERTEX_", "ANTHROPIC_FOUNDRY_", "ANTHROPIC_AWS_")

# Model choice, effort, thinking, output size, Fast and automatic model fallback.
MODELS = frozenset({
    "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION",
    "CLAUDE_CODE_SUBAGENT_MODEL", "MAX_THINKING_TOKENS", "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "CLAUDE_CODE_DISABLE_THINKING",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_DISABLE_FAST_MODE",
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK", "CLAUDE_CODE_DISABLE_MODEL_ACCESS_FALLBACK",
    "CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP", "FALLBACK_FOR_ALL_PRIMARY_MODELS",
})
# ANTHROPIC_DEFAULT_MODEL and the opus/sonnet/haiku/fable alias targets and labels.
MODEL_PREFIXES = ("ANTHROPIC_DEFAULT_", "ANTHROPIC_CUSTOM_MODEL_OPTION")

# Prompt caching and context size: Angelo sets the TTL per session itself.
CACHE_AND_CONTEXT = frozenset({
    "CLAUDE_CODE_PROMPT_CACHE_TTL", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "FORCE_PROMPT_CACHING_5M",
    "ENABLE_PROMPT_CACHING_1H", "ENABLE_PROMPT_CACHING_1H_BEDROCK", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_DISABLE_1M_CONTEXT",
    # Undocumented: "5m" writes the conversation's message breakpoints at 5
    # minutes while the system prompt keeps the session's TTL, splitting the
    # session's one TTL (read from Claude Code 2.1.301, 2026-10-18).
    "CLAUDE_CODE_MESSAGES_PROMPT_CACHE_TTL",
    # Removes a block from Claude Code's system prompt, so the cached prefix differs.
    "CLAUDE_CODE_ATTRIBUTION_HEADER",
})
# DISABLE_PROMPT_CACHING and its _1H, _HAIKU, _SONNET, _OPUS and _FABLE variants.
CACHE_PREFIXES = ("DISABLE_PROMPT_CACHING",)

# Context Claude Code adds to a new session's first message that Angelo's agent
# cannot use; every session sets these values (session_environment), and an
# inherited copy, which could force either back on, is dropped:
# * auto memory: about 2.4 KB telling the model to keep memory files with a
#   Write tool an Angelo session does not have (its tools are WebSearch and
#   WebFetch; task memory is Angelo's own);
# * the git status snapshot (and commit/PR guidance) of the repository the chat
#   workspace sits in: up to about 2.7 KB of unrelated file names.
# The first message is written to the prompt cache at the session's TTL rate
# (2x input at 1h), so both cost every new chat (``performance.rst``, "Fixed
# opening of a new chat"). Documented at https://code.claude.com/docs/en/env-vars
# (read 2026-10-14): each variable takes precedence over its settings key.
SESSION_CONTEXT = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1"}
# Tool-input streaming: the chat shows a plan's progress and reply as Claude Code streams its input, on by
# default on the Anthropic API (claude_stream). An inherited "0" would hold both back to the finished plan;
# the runtime's own default decides (off on gateway connections, whose proxies may refuse the field).
STREAMING = frozenset({"CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING"})

# Markers of an enclosing agent session (Blender launched from Claude Code or a
# cloud session); a child must start as its own top-level session.
SESSIONS = frozenset({
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_REMOTE",
    "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_HOST_GATEWAY_LINEAGE",
})

# Claude Code's bare mode, which --bare sets: its built-in tools are only the shell and
# file tools, so an inherited value would remove WebSearch and WebFetch from every route.
TOOL_POOL = frozenset({"CLAUDE_CODE_SIMPLE"})

DROPPED = (CREDENTIALS | ENDPOINTS | MODELS | CACHE_AND_CONTEXT | frozenset(SESSION_CONTEXT) | STREAMING | SESSIONS
           | TOOL_POOL)
DROPPED_PREFIXES = ENDPOINT_PREFIXES + MODEL_PREFIXES + CACHE_PREFIXES

# Documented network and trust settings that always pass (pinned by tests).
NETWORK = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
           "all_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE", "CLAUDE_CODE_CERT_STORE",
           "CLAUDE_CODE_CLIENT_CERT", "CLAUDE_CODE_CLIENT_KEY", "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE",
           "CLAUDE_CODE_PROXY_RESOLVES_HOSTS")

PROMPT_CACHE_TTLS = ("5m", "1h")


def dropped(name):
    """Whether an inherited variable is withheld from native children (case-insensitive, as on Windows)."""
    upper = name.upper()
    return upper in DROPPED or upper.startswith(DROPPED_PREFIXES)


def inherited(environment):
    """A copy of ``environment`` without the variables native children must not inherit."""
    return {name: value for name, value in environment.items() if not dropped(name)}


def session_environment(environment, context):
    """The launch environment of one native Claude session from Angelo's own decisions.

    ``context.prompt_cache_ttl`` sets the TTL; ``context.single_model`` (test
    harnesses only) turns off Claude Code's
    automatic refusal fallback so a measurement stays on the requested model.
    People keep the fallback; receipts show when it served a turn. Every
    session leaves out the context in ``SESSION_CONTEXT``.
    """
    environment.update(SESSION_CONTEXT)
    if getattr(context, "claude_direct_tools", False) is True:   # The opt-in direct-tools route (claude_direct).
        from .claude_direct import SESSION_ENVIRONMENT
        environment.update(SESSION_ENVIRONMENT)
    apply_prompt_cache_ttl(environment, getattr(context, "prompt_cache_ttl", None))
    if getattr(context, "single_model", False):
        environment["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"] = "1"
    return environment


# Claude modes that authenticate with an API key (the person's own, or a team grant key).
CLAUDE_KEY_MODES = frozenset({"claude_api", "claude_team"})


def require_angelo_profile(mode, environment, connection_id):
    """Refuse an API-key or team Claude launch outside its connection's own ``claude-profile/<id>`` folder.

    No Claude route runs ``--bare``: bare mode's built-in tools are only the
    shell and file tools and ``--tools`` filters within them, so WebSearch and
    WebFetch would be gone (``providers.claude_session``). A key or team session
    is kept off the person's own Claude sign-in instead: its key is
    ``ANTHROPIC_API_KEY``, always used with ``-p``; the inherited credential
    variables are dropped (``CREDENTIALS``); and it runs in this profile, to which
    Claude Code keys its Keychain entry and credentials file
    (https://code.claude.com/docs/en/authentication). Only a profile Angelo made
    for this connection (``providers.profile_environment``) is certain to hold no
    sign-in of the person's, so a chat without a connection record (a key typed
    into the legacy per-mode operator) is refused.
    """
    if mode not in CLAUDE_KEY_MODES:
        return
    stray = sorted(name for name in environment if name.upper() in CREDENTIALS - {"ANTHROPIC_API_KEY"})
    if stray:
        raise RpcError("Claude launch refused: it carries another sign-in's variables (%s)" % ", ".join(stray))
    try:
        profile = connection_id and valid_profile_directory(
            environment.get("CLAUDE_CONFIG_DIR"), str(uuid.UUID(connection_id)), mode)
    except ValueError:
        profile = None
    if not profile:
        raise RpcError("This Claude connection has no Angelo profile of its own. "
                       "Add it in Preferences > AI Providers, then reconnect")


def apply_prompt_cache_ttl(environment, ttl):
    """Set Claude Code's main-conversation TTL for one native session; None keeps its default.

    In Claude Code (2.1.293 and 2.1.301 read 2026-10-18) the variable outranks
    every other source it consults (a ``promptCacheTtl`` setting,
    ``ENABLE_PROMPT_CACHING_1H``, its automatic choice) except
    ``FORCE_PROMPT_CACHING_5M``, which is never inherited, so an explicit TTL
    is the one the session writes.
    """
    if ttl is None:
        return environment
    if ttl not in PROMPT_CACHE_TTLS:
        raise ValueError("Prompt cache TTL must be 5m or 1h")
    environment["CLAUDE_CODE_PROMPT_CACHE_TTL"] = ttl
    return environment
