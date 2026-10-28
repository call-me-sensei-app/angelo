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
import tomllib
import uuid
from pathlib import Path

from . import product_env, runtime_environment, team_runtime
from .cli_runtime import with_runtime_path
from .protocol import RpcError
from .provider_registry import CUSTOM_PROVIDER_MODES, TEAM_MODES, spec_for
from .storage import native_profile_directory, valid_profile_directory


_PRODUCT = (product_env.PREFIX,) + product_env.LEGACY_PREFIXES


def child_environment(mode, api_key=""):
    # Inherited credentials, endpoints, model, caching and session variables
    # must not silently override the explicitly selected billing mode, model
    # or prompt-cache TTL (runtime_environment.py lists them with sources).
    env = runtime_environment.inherited(os.environ)
    # The app sets these per child; an inherited copy under any spelling is dropped.
    for suffix in ("XAI_API_KEY", "MINIMAX_API_KEY", "KIMI_API_KEY", "TEAM_GRANT_KEY"):
        product_env.clear(suffix, env)
    # Angelo's own secrets (``*_SECRET``: the cache-key secrets) reach no child, whose shell tools could read them.
    for name in [name for name in env if name.upper().startswith(_PRODUCT) and name.upper().endswith("SECRET")]:
        del env[name]
    team_runtime.apply_policy(env, mode)  # Refuses a forbidden personal runtime; adds the policy's TLS roots.
    if mode == "claude_api":
        env["ANTHROPIC_API_KEY"] = api_key
    elif mode in TEAM_MODES:
        team_runtime.apply_environment(env, mode, api_key)  # Its gateway route: the one endpoint a child receives.
    elif mode in CUSTOM_PROVIDER_MODES and api_key:
        env[spec_for(mode)["env_key"]] = api_key
    return env


def profile_environment(env, mode, state_dir, reuse_login=True, connection_id=None, native_profile=None):
    if connection_id:
        connection_id = str(uuid.UUID(connection_id))
    if native_profile:
        profile = valid_profile_directory(native_profile, connection_id, mode)
        home = Path(profile)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CLAUDE_CONFIG_DIR" if mode.startswith("claude_") else "CODEX_HOME"] = str(home)
        if not mode.startswith("claude_"):
            env.pop("CODEX_SQLITE_HOME", None)  # As below: state stays in the isolated profile.
        return env
    if mode in CUSTOM_PROVIDER_MODES or (mode.startswith("codex_") and (mode == "codex_api" or not reuse_login)):
        home = native_profile_directory(state_dir, "codex-profile", connection_id or mode)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CODEX_HOME"] = str(home)
        # Its SQLite state defaults to CODEX_HOME; an inherited path would mix
        # the isolated profile's threads into the person's own store.
        env.pop("CODEX_SQLITE_HOME", None)
    elif mode.startswith("claude_") and connection_id and not reuse_login:
        home = native_profile_directory(state_dir, "claude-profile", connection_id)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CLAUDE_CONFIG_DIR"] = str(home)
    return env


def launch_binding(command, env, workspace):
    """What an app-server's process is: a prewarmed one serves a connect only when all of it is unchanged."""
    return tuple(command), tuple(sorted(env.items())), str(workspace)


# Angelo's dynamic tools whose calls wait for something by design (a worker, a job, a person), by the name the model
# calls them in code mode. Angelo's agent runtime keeps a code-mode cell open while such a call is unanswered instead
# of yielding it to a model that could only poll it with `wait` (release/license/agent-runtime/README.txt). A tool
# that answers at once gains nothing here; one listed here gives the model no chance to start other work while it
# waits, so list only calls that are waits. Each waiting tool's owner adds its name.
HOLD_DYNAMIC_TOOLS = (
    "angelo_workers_status",  # Parallel edit: with wait=true it answers when a worker finishes (up to 30 min).
)


def codex_command(executable, mode, state_dir, reuse_login=True, connection_id=None, api_key="", native_profile=None):
    """Overrides are process-local; they do not edit the user's CLI configuration."""
    env = profile_environment(child_environment(mode, api_key), mode, state_dir, reuse_login, connection_id, native_profile)
    home = Path(env.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        configuration = tomllib.loads((home / "config.toml").read_text())
    except (OSError, ValueError):
        configuration = {}
    command = [executable, "app-server", "--listen", "stdio://"]
    overrides = {
        "model_provider": '"openai"', "sandbox_mode": '"read-only"',
        "approval_policy": '"on-request"', "web_search": '"live"',
        "features.shell_tool": "false", "features.code_mode": "false",
        "features.code_mode_host": "true", "features.code_mode_only": "false",
        "features.sleep_tool": "false", "features.tool_suggest": "false",
        "features.remote_plugin": "false", "features.multi_agent": "false", "features.multi_agent_v2": "false",
        "features.goals": "false", "tools.view_image": "false", "features.view_image": "false",
        "features.apps": "false", "features.plugins": "false", "features.hooks": "false",
        "features.browser_use": "false", "features.computer_use": "false", "features.in_app_browser": "false",
        "features.image_generation": "false", "features.skill_search": "false",
        "features.skill_mcp_dependency_install": "false", "features.skip_host_skill_discovery": "true",
        "model_reasoning_effort": '"low"',
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
    }
    if HOLD_DYNAMIC_TOOLS:
        # Top level on purpose: a Codex without the key (the person's own) ignores it.
        overrides["code_mode_hold_dynamic_tools"] = json.dumps(list(HOLD_DYNAMIC_TOOLS))
    if mode == "codex_api":
        overrides["cli_auth_credentials_store"] = '"keyring"'
    if mode in CUSTOM_PROVIDER_MODES:
        spec = spec_for(mode)
        # No ChatGPT login, credential copying, protocol conversion or proxy. Keys (a team's grant key, its
        # base URL the gateway route) live only in the child's environment. The isolated profile must never
        # inherit the user's default provider.
        overrides.update({"model_provider": json.dumps(mode), "model": json.dumps(spec["models"][0]["id"]),
                          "web_search": '"live"' if spec.get("web_search") else '"disabled"',
                          "model_reasoning_effort": json.dumps(spec["models"][0]["default_effort"])})
        if spec["models"][0].get("context_window"):
            overrides["model_context_window"] = str(spec["models"][0]["context_window"])
        overrides.update(team_runtime.model_provider_entry(mode))
    for name in configuration.get("mcp_servers", {}):
        if "." in name:
            raise RpcError("An MCP server name contains a dot; use an isolated Angelo Codex profile")
        overrides[f"mcp_servers.{name}.enabled"] = "false"
    for name in configuration.get("plugins", {}):
        if "." not in name:
            overrides[f"plugins.{name}.enabled"] = "false"
    for key, value in overrides.items():
        command += ["-c", f"{key}={value}"]
    return command, with_runtime_path(env, executable)
