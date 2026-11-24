# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a native runtime child is launched with: its environment, its isolated profile and the Codex command.

``providers`` launches every Claude Code session and Codex app-server with
these, and re-exports them; ``runtime_prewarm`` compares a process started
before its request with the request's own launch (``launch_binding``). The
variables a child must not inherit are listed in ``runtime_environment``.
"""

import json
import os
import sys
import tomllib
import uuid
from pathlib import Path

from . import agent_runtime, owned_paths, product_env, runtime_environment, shared_replace, team_runtime
from .chat_settings import default_effort
from .cli_runtime import with_runtime_path
from .protocol import RpcError
from .provider_registry import TEAM_MODES, custom_provider, is_endpoint, runtime_wire, spec_for
from .storage import native_profile_directory, private_profile, valid_profile_directory


_PRODUCT = (product_env.PREFIX,) + product_env.LEGACY_PREFIXES


def child_environment(mode, api_key=""):
    # Inherited credentials, endpoints, model, caching and session variables
    # must not silently override the explicitly selected billing mode, model
    # or prompt-cache TTL (runtime_environment.py lists them with sources).
    env = runtime_environment.inherited(os.environ)
    # The app sets these per child; an inherited copy under any spelling is dropped.
    for suffix in ("XAI_API_KEY", "MINIMAX_API_KEY", "KIMI_API_KEY", "GEMINI_API_KEY", "TEAM_GRANT_KEY", "ENDPOINT_KEY"):
        product_env.clear(suffix, env)
    # Angelo's own secrets (``*_SECRET``: the cache-key secrets) reach no child, whose shell tools could read them.
    for name in [name for name in env if name.upper().startswith(_PRODUCT) and name.upper().endswith("SECRET")]:
        del env[name]
    team_runtime.apply_policy(env, mode)  # Refuses a forbidden personal runtime; adds the policy's TLS roots.
    if mode == "claude_api":
        env["ANTHROPIC_API_KEY"] = api_key
    elif mode in TEAM_MODES:
        team_runtime.apply_environment(env, mode, api_key)  # Its gateway route: the one endpoint a child receives.
    elif is_endpoint(mode):
        team_runtime.apply_endpoint_environment(env, mode, api_key)  # Its organization's server, and only its host.
    elif custom_provider(mode) and api_key:
        env[spec_for(mode)["env_key"]] = api_key
    return env


def _claude(mode):
    """Whether ``mode`` runs on Claude Code (its registry runtime: a custom endpoint's mode has no claude_ prefix)."""
    return spec_for(mode)["runtime"] == "claude"


def profile_environment(env, mode, state_dir, reuse_login=True, connection_id=None, native_profile=None):
    """``env`` with the runtime's own profile folder for this connection, which must lie in Angelo's private folders
    (``storage.private_profile``): it is checked before it is made, whatever ``state_dir`` or ``native_profile``
    says (both come from Python state)."""
    if connection_id:
        connection_id = str(uuid.UUID(connection_id))
    if native_profile:
        profile = private_profile(valid_profile_directory(native_profile, connection_id, mode))
        home = Path(profile)
        owned_paths.require_writable(home).mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CLAUDE_CONFIG_DIR" if _claude(mode) else "CODEX_HOME"] = str(home)
        if not _claude(mode):
            env.pop("CODEX_SQLITE_HOME", None)  # As below: state stays in the isolated profile.
        return env
    if custom_provider(mode) or (mode.startswith("codex_") and (mode == "codex_api" or not reuse_login)):
        home = private_profile(native_profile_directory(state_dir, "codex-profile", connection_id or mode))
        owned_paths.require_writable(home).mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CODEX_HOME"] = str(home)
        # Its SQLite state defaults to CODEX_HOME; an inherited path would mix
        # the isolated profile's threads into the person's own store.
        env.pop("CODEX_SQLITE_HOME", None)
    elif _claude(mode) and connection_id and not reuse_login:
        home = private_profile(native_profile_directory(state_dir, "claude-profile", connection_id))
        owned_paths.require_writable(home).mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CLAUDE_CONFIG_DIR"] = str(home)
    return env


def launch_binding(command, env, workspace):
    """What an app-server's process is: a prewarmed one serves a connect only when all of it is unchanged."""
    return tuple(command), tuple(sorted(env.items())), str(workspace)


# Angelo's dynamic tools whose unanswered calls keep a code-mode cell open, by the name each is declared with. Angelo's
# agent runtime holds such a cell instead of yielding it to a model that could only poll it with `wait`
# (release/license/agent-runtime/README.txt), up to HOLD_CEILING_SECONDS, and a `wait` holds again. Every Angelo
# tool is listed: each goes through the host's request_tool, where any call can wait for the person (an approval card,
# any edit in Ask mode), for its scene (document_scene.hold) or for a job, until its tool_deadlines expiry. A
# yielded wait let the model end its turn while the card was on screen, and the turn's end revoked it (1.0.8 B-007);
# the model has nothing else to do meanwhile, since the host runs calls one at a time. A call that answers within
# the yield is unaffected.
def _hold_dynamic_tools():
    from .tool_specs import TOOLS
    return tuple(tool["name"] for tool in TOOLS)


HOLD_DYNAMIC_TOOLS = _hold_dynamic_tools()
HOLD_CEILING_SECONDS = 35 * 60   # The runtime's own bound on one hold (host_call_hold.rs HOLD_CEILING).
# The fork's top-level setting listing dynamic tools that run one call at a time (its ANGELO.md). Codex 0.164 and later
# let a code-mode cell's Promise.all send several calls at once (openai/codex#54654); the host was written for one at a
# time (any call may wait for an approval, and overlapping edits would run in arrival order: agent_execution.rst,
# "Concurrent calls"), so every Angelo tool is listed, which keeps 0.163's order. A Codex without the key ignores it.
SERIAL_TOOLS_KEY = "serial_dynamic_tools"
# The setting that lists the dynamic tools whose pictures a code-mode cell forwards to the model without image()
# (image_proof: once per turn per picture): the fork's top-level key (its ANGELO.md). The pin's config_keys names it
# once the pinned build reads it, which is what turns it on; the release checks confirm the build reads it.
FORWARD_IMAGES_KEY = "code_mode_forward_dynamic_tool_images"


def forward_image_tools(executable):
    """The dynamic tools whose pictures ``executable``'s code mode forwards itself: every tool whose results can carry
    pictures (``image_proof.image_tools``), on a build that reads ``FORWARD_IMAGES_KEY``; empty for any other Codex."""
    from .agent_runtime import kind
    from .image_proof import forwarding_build, image_tools
    return image_tools() if forwarding_build(kind(executable)) else ()


# The only settings of the person's own config.toml an Angelo thread uses, through agent_runtime.IGNORE_USER_CONFIG
# (coordinator rulings, 2026-11-20): what Codex's sign-in configuration reads (the fork's ``auth_config`` and
# ``bootstrap_auth_config``: where the sign-in is kept, the organization's sign-in rules and the keyring backend;
# derived per fork commit by scripts/angelo/codex_config_inputs.py, recorded and checked in
# tests/python/angelo_agent/codex_config_keys.py), except what is ruled out (``NOT_PASSED_ON``: ``chatgpt_base_url``,
# the sign-in's own default; the proxy features, a route for every request, which Angelo's network contract decides),
# and an analytics or feedback opt-out. Every other key of the pinned schema is classified there too.
SIGN_IN_KEYS = ("cli_auth_credentials_store", "forced_login_method", "forced_chatgpt_workspace_id")
SIGN_IN_FEATURES = ("secret_auth_storage",)   # ``[features]`` booleans: the keyring backend.
OPT_OUTS = ("analytics", "feedback")   # ``<table>.enabled = false`` only: the person's opt-out stands in Angelo.


def toml_string(text):
    """``text`` as a TOML basic string: every character outside printable ASCII escaped (``\\uXXXX``, or
    ``\\UXXXXXXXX`` beyond the BMP), so Codex reads back exactly ``text`` (json.dumps writes surrogate pairs)."""
    out = []
    for character in text:
        code = ord(character)
        if character in '"\\':
            out.append("\\" + character)
        elif 0x20 <= code < 0x7F:
            out.append(character)
        else:
            out.append("\\u%04X" % code if code <= 0xFFFF else "\\U%08X" % code)
    return '"%s"' % "".join(out)


def passed_on(configuration):
    """The ``-c`` overrides that carry ``SIGN_IN_KEYS``, ``SIGN_IN_FEATURES`` and ``OPT_OUTS`` from the person's
    parsed config.toml: strings (a workspace id list of strings), booleans and a literal false, nothing else of it."""
    passed = {}
    for key in SIGN_IN_KEYS:
        value = configuration.get(key)
        if isinstance(value, str):
            passed[key] = toml_string(value)
        elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            passed[key] = "[%s]" % ", ".join(toml_string(item) for item in value)
    features = configuration.get("features")
    for name in SIGN_IN_FEATURES:
        value = features.get(name) if isinstance(features, dict) else None
        if isinstance(value, bool):
            passed["features." + name] = "true" if value else "false"
    for table in OPT_OUTS:
        section = configuration.get(table)
        if isinstance(section, dict) and section.get("enabled") is False:
            passed[table + ".enabled"] = "false"
    return passed


def codex_command(executable, mode, state_dir, reuse_login=True, connection_id=None, api_key="", native_profile=None):
    """Overrides are process-local; they do not edit the user's CLI configuration."""
    env = profile_environment(child_environment(mode, api_key), mode, state_dir, reuse_login, connection_id, native_profile)
    from .agent_runtime import is_agent_runtime
    # Angelo's own runtime: no loader variables, and on Linux programs only from root-owned folders.
    runtime_environment.for_agent_runtime(env, executable)
    system_path = is_agent_runtime(executable) and sys.platform.startswith("linux")
    home = Path(env.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        configuration = tomllib.loads(shared_replace.read_text(home / "config.toml"))   # The MCP setup replaces it.
    except (OSError, ValueError, RecursionError):   # Unreadable, malformed or nested past the parser: none of it.
        configuration = {}
    bundled_runtime = is_agent_runtime(executable)
    command = [executable, "app-server", "--listen", "stdio://"]
    if bundled_runtime:   # Never the person's own config.toml (agent_runtime.IGNORE_USER_CONFIG); refused without it.
        agent_runtime.require_user_config_ignored()
        command.insert(2, agent_runtime.IGNORE_USER_CONFIG)
    overrides = {
        "model_provider": '"openai"', "sandbox_mode": '"read-only"',
        "approval_policy": '"on-request"', "web_search": '"live"',
        "features.shell_tool": "false", "features.code_mode": "false",
        "features.code_mode_host": "true", "features.code_mode_only": "false",
        "features.sleep_tool": "false", "features.tool_suggest": "false",
        "features.remote_plugin": "false", "features.multi_agent": "false", "features.multi_agent_v2": "false",
        "features.goals": "false", "tools.view_image": "false", "features.view_image": "false",
        # Codex snapshots the person's login shell (``bash -lc``, measured on Ubuntu 24.04) for its shell tools, which
        # Angelo turns off: the snapshot would only start a program nothing uses.
        "features.shell_snapshot": "false",
        # The person's Codex ``notify`` program (config.toml) runs after every turn with the turn's text, whatever
        # features.hooks says: never for an Angelo thread (coordinator ruling, re-review of d67b4e25575 SHOULD-2).
        "notify": "[]",
        # Nothing of an Angelo thread goes where a Codex config points, and Angelo threads never mix into the
        # person's Codex state (coordinator rulings of 2026-11-20). The agent runtime ignores the person's config.toml
        # (agent_runtime.IGNORE_USER_CONFIG); these pins still hold against the layers it does load (a system-wide
        # /etc/codex/config.toml, a trusted project's .codex layer) and for the person's own Codex in a build without
        # the runtime: no OTEL export or prompt/response logging, no memories, no history.jsonl entries, approvals
        # stay with Angelo's client (which declines every one), and no project layer (the project root is the
        # workspace itself, Angelo's private folder). Every config key's class:
        # tests/python/angelo_agent/codex_config_keys.py.
        "otel.exporter": '"none"', "otel.trace_exporter": '"none"', "otel.metrics_exporter": '"statsig"',
        "otel.log_user_prompt": "false", "otel.log_agent_responses": "false", "otel.log_guardian_assessments": "false",
        "features.memories": "false", "memories.generate_memories": "false", "memories.use_memories": "false",
        "history.persistence": '"none"', "approvals_reviewer": '"user"', "project_root_markers": "[]",
        "features.apps": "false", "features.plugins": "false", "features.hooks": "false",
        "features.browser_use": "false", "features.computer_use": "false", "features.in_app_browser": "false",
        "features.image_generation": "false", "features.skill_search": "false",
        "features.skill_mcp_dependency_install": "false", "features.skip_host_skill_discovery": "true",
        # What a request without an effort of its own runs at; every turn sends its chat's (codex_turn_options).
        "model_reasoning_effort": json.dumps(default_effort()),
        # Developer messages Codex otherwise adds to every request of an Angelo
        # thread: the bundled skills list, shell-escalation (permissions) and
        # apps instructions, and the collaboration-mode note (about 6.8 KB in
        # codex-cli 0.160.0, doc/angelo/gpt_launch.rst). Angelo runs no shell,
        # skills or apps. Undocumented keys: a Codex that does not know one
        # ignores it with a warning and keeps the larger prefix.
        "skills.include_instructions": "false", "include_permissions_instructions": "false",
        "include_apps_instructions": "false", "include_collaboration_mode_instructions": "false",
        "include_environment_context_time": "false",  # No date or time zone in the opening (gpt_launch.rst).
        # Codex's own sub-agents (Angelo runs none): with them on, the catalog's multi-agent role, its collaboration
        # tools and a mode note ("do not spawn sub-agents unless ... asked for ... parallel agent work") went into every
        # request, about 8.3 KB of the first (gpt_launch.rst).
        "agents.enabled": "false",
        # A person's steer must never cancel a model request or a code-mode wait (steering_delivery): it is read at
        # the next one. Off by default; set so a person's own config.toml can't turn it on for Angelo's threads.
        "features.instant_interrupt": "false",
    }
    if HOLD_DYNAMIC_TOOLS:
        # Top level on purpose: a Codex without the key (the person's own) ignores it.
        overrides["code_mode_hold_dynamic_tools"] = json.dumps(list(HOLD_DYNAMIC_TOOLS))
        overrides[SERIAL_TOOLS_KEY] = json.dumps(list(HOLD_DYNAMIC_TOOLS))
    forwarded = forward_image_tools(executable)
    if forwarded:  # Only to a build that reads it (forward_image_tools): every other Codex launches as before.
        overrides[FORWARD_IMAGES_KEY] = json.dumps(list(forwarded))
    if mode == "codex_api":
        overrides["cli_auth_credentials_store"] = '"keyring"'
    if custom_provider(mode):
        spec = spec_for(mode)
        wire = runtime_wire(mode)
        if wire:   # A wire only Angelo's agent runtime has (Gemini): never the person's own Codex.
            if agent_runtime.kind(executable) == "own" or not agent_runtime.supports_wire(wire):
                raise RpcError("%s runs only on Angelo's agent runtime, which this build does not include. "
                               "Install the full Angelo app." % spec["name"])
        # No ChatGPT login, credential copying, protocol conversion or proxy. Keys (a team's grant key, its
        # base URL the gateway route) live only in the child's environment. The isolated profile must never
        # inherit the user's default provider.
        overrides.update({"model_provider": json.dumps(mode), "model": json.dumps(spec["models"][0]["id"]),
                          "web_search": '"live"' if spec.get("web_search") else '"disabled"',
                          "model_reasoning_effort": json.dumps(default_effort(spec["models"][0]["efforts"],
                                                                              spec["models"][0]["id"]))})
        if spec["models"][0].get("context_window"):
            overrides["model_context_window"] = str(spec["models"][0]["context_window"])
        overrides.update(team_runtime.model_provider_entry(mode))
        endpoint = is_endpoint(mode)
        if endpoint:   # No process-wide effort: a mapped model's turns state theirs, an unmapped one's none (its
            # server's own default, never a level the model may refuse; chat_settings.codex_turn_options).
            overrides.pop("model_reasoning_effort", None)
        if endpoint or wire:   # Each model gets the metadata of the model it is (endpoint_catalog), from the
            # catalog of its wire: an endpoint's mapped models, and a registry connection's on Angelo's own wire.
            from . import runtime_relay
            from .endpoint_catalog import write
            # A worker's launch is rebuilt by Angelo (runtime_plan), which writes the catalog for the child it runs.
            catalog = None if runtime_relay.relayed() else write(executable, spec, home, wire=wire, own=not endpoint)
            if catalog:
                overrides["model_catalog_json"] = json.dumps(catalog)
    if bundled_runtime:
        # Ignored, the person's config.toml still decides what their sign-in reads (SIGN_IN_KEYS, SIGN_IN_FEATURES:
        # where it is kept, the keyring backend, the organization's rules), which Angelo reuses,
        # and an analytics or feedback opt-out, never an opt-in. An Angelo setting above wins (an API connection keeps
        # its key in the keyring).
        overrides = {**passed_on(configuration), **overrides}
        # Codex's state database and logs for Angelo threads live in Angelo's private state folder, never in the
        # person's ~/.codex. The threads themselves (rollouts) are still written to CODEX_HOME/sessions beside the
        # person's own Codex sessions: a known limit until the fork has a thread-store folder (releasing.rst).
        state = private_profile(native_profile_directory(state_dir, "codex-state", connection_id or mode))
        owned_paths.require_writable(Path(state, "log")).mkdir(mode=0o700, parents=True, exist_ok=True)
        overrides["sqlite_home"] = toml_string(str(state))
        overrides["log_dir"] = toml_string(str(Path(state, "log")))
        configuration = {}   # Nothing else of it is read: its MCP servers and plugins never load (ignored).
    for name in configuration.get("mcp_servers", {}):
        if "." in name:
            raise RpcError("An MCP server name contains a dot; use an isolated Angelo Codex profile")
        overrides[f"mcp_servers.{name}.enabled"] = "false"
    for name in configuration.get("plugins", {}):
        if "." not in name:
            overrides[f"plugins.{name}.enabled"] = "false"
    for key, value in overrides.items():
        command += ["-c", f"{key}={value}"]
    return command, (env if system_path else with_runtime_path(env, executable))
