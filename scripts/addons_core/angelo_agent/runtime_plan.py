# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How ``runtime_service.RuntimeService`` builds a worker chat's launch (``plan``): the command and environment from
Angelo's own settings and key for the chat's connection, with the same code a chat in the window uses; nothing the
worker sends names a program, a flag outside that grammar or an environment variable. Standard library only and no
``bpy``.
"""

import uuid
from types import SimpleNamespace

from . import runtime_environment
from .protocol import RpcError

CODEX_MODEL_PROVIDER = "openai"


class Plans:
    """Mixed into ``RuntimeService``: ``executable`` and ``_recorded`` are its."""

    def plan(self, request, chat):
        """``{kind, command, env, cwd, key, model_provider, secrets, login}`` from Angelo's own records."""
        from .provider_registry import CUSTOM_PROVIDER_MODES, runtime_kind
        kind = runtime_kind(chat.mode)
        if request.get("provider_kind") != kind:
            raise RpcError("The worker asked for another runtime than its chat's connection uses")
        if request.get("workspace") != str(chat.workspace):
            raise RpcError("The worker asked for another working folder than its chat's")
        settings, executable = dict(chat.settings), self.executable(kind, chat.settings)
        from .agent_runtime import launched
        runtime = launched(executable) if kind == "codex" else None
        command, env = (self._codex(request, chat, settings, executable) if kind == "codex"
                        else self._claude(request, chat, settings, executable))
        names = runtime_environment.CREDENTIALS
        secrets = [chat.api_key] + [value for name, value in env.items() if name.upper() in names
                                    or name.upper().endswith(("_API_KEY", "_TOKEN", "_GRANT_KEY"))]
        from .runtime_launch import launch_binding
        return {"kind": kind, "command": command, "env": env, "cwd": str(chat.workspace), "secrets": secrets,
                "key": (request.get("session"),) + launch_binding(command, env, chat.workspace),
                "model_provider": chat.mode if chat.mode in CUSTOM_PROVIDER_MODES else CODEX_MODEL_PROVIDER,
                "login": kind == "codex" and chat.mode == "codex_api" and bool(chat.api_key), "runtime": runtime}

    def _codex(self, request, chat, settings, executable):
        from .runtime_launch import codex_command
        command, env = codex_command(executable, chat.mode, settings.get("auth_directory", chat.store_directory),
                                     settings.get("reuse_codex_login", True), settings.get("connection_id"),
                                     chat.api_key, settings.get("native_profile"))
        if request.get("text_only") is True:
            command += ["-c", 'web_search="disabled"']
        return command, env

    def _claude(self, request, chat, settings, executable):
        from .claude_transport import check_command_budget, claude_arguments, launch_values
        from .cli_runtime import with_runtime_path
        from .runtime_launch import child_environment, profile_environment
        arguments = request.get("arguments")
        options = request.get("session_options") or {}
        if not isinstance(arguments, list) or not all(isinstance(part, str) for part in arguments):
            raise RpcError("The Claude launch is not one Angelo builds")
        ttl = options.get("prompt_cache_ttl")
        if ttl not in (None,) + runtime_environment.PROMPT_CACHE_TTLS or not all(
                type(options.get(name, False)) is bool for name in ("claude_direct_tools", "single_model")):
            raise RpcError("The Claude session options are not ones Angelo sets")
        values = launch_values(arguments)
        if values["resume"] and values["resume"] not in self._recorded(request.get("session")):
            values["resume"] = str(uuid.uuid4())   # Not one Angelo saw this chat open: a session that cannot exist.
        command = claude_arguments(executable, values)
        check_command_budget(command)
        env = with_runtime_path(profile_environment(child_environment(chat.mode, chat.api_key), chat.mode,
                                                    settings.get("auth_directory", chat.store_directory),
                                                    settings.get("reuse_codex_login", True),
                                                    settings.get("connection_id"), settings.get("native_profile")),
                                executable)
        runtime_environment.session_environment(env, SimpleNamespace(
            prompt_cache_ttl=ttl, claude_direct_tools=options.get("claude_direct_tools", False),
            single_model=options.get("single_model", False)))
        runtime_environment.require_angelo_profile(chat.mode, env, settings.get("connection_id"))
        return command, env
