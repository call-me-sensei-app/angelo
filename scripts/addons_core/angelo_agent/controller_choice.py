# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which connection and model a chat's next request uses, and choosing them while a turn runs (``Controller``).

A choice made while a turn runs never touches the running request: its connection, billing and model stay as they
started, and the choice is kept as the next binding (``_next_binding``, ``state["next_route"]``), applied before the
next message is sent (``apply_next_connection``). The chat says so once. 1.0.9 raised "The current request is still
running; queue the connection for the next message" here when the person sent during a turn, shown to them as
"Something went wrong".
"""

import threading

from . import ui_text
from .chat_settings import stored_options
from .routing import MODES, TOOLSET_VERSION, route_key

DEFERRED_CHOICE = ui_text.n_("Your new choice takes over from your next message; this turn finishes as it started.")


class ConnectionChoice:
    @property
    def selection_connection_id(self):
        return (self.state.get("next_route") or {}).get("connection_id", self.state.get("connection_id"))

    @property
    def selection_provider(self):
        return (self.state.get("next_route") or {}).get("provider", self.state["provider"])

    @property
    def selection_models(self):
        return self._next_binding["models"] if self._next_binding else self.models

    @property
    def selection_settings(self):
        return self._next_binding["settings"] if self._next_binding else self.settings

    def turn_running(self):
        """A turn is under way: from its start until its end is handled, or a worker lane preparing a message
        (``worker_routing.end_turn`` reads the same)."""
        return bool(self.busy or (self._worker and self._worker.is_alive())
                    or isinstance(getattr(self, "_delivery_stops", None), int))

    def select(self, provider, settings, api_key="", connection_id=None, models=None):
        if self.turn_running():
            # The running request keeps its connection and billing; the choice takes over after it (as
            # ``choose_connection``), never an error. 1.0.9: raised here, shown as "Something went wrong".
            if self._defer_binding(provider, settings, api_key, connection_id, models):
                self._append_row({"role": "system", **ui_text.notice(DEFERRED_CHOICE)})
            return
        self._bind(provider, settings, api_key, connection_id, models)

    def _bind(self, provider, settings, api_key="", connection_id=None, models=None):
        """Bind the chat to ``provider``/``connection_id`` now: only between requests."""
        if provider not in MODES:
            raise ValueError("Unknown provider profile")
        connection_id = connection_id if connection_id is not None else self.state.get("connection_id")
        old_key = route_key(self.state.get("connection_id"), self.state["provider"])
        new_key = route_key(connection_id, provider)
        changed = provider != self.state["provider"] or new_key != old_key or settings != self.settings or api_key != self.api_key
        self.remember_runtime()
        if changed and self.provider:
            threading.Thread(target=self.provider.close, daemon=True).start()
            self.provider = None
        # Native threads belong to one credential/profile scope, not just a model.
        profile_changed = bool(self.settings) and (
            settings.get("reuse_codex_login", True) != self.settings.get("reuse_codex_login", True)
            or settings.get("connection_id") != self.settings.get("connection_id")
            or settings.get("auth_directory") != self.settings.get("auth_directory")
            or settings.get("native_profile") != self.settings.get("native_profile"))
        key_changed = provider.endswith("_api") and api_key != self.api_key
        if changed:
            self._epoch += 1
            self._runtime_context = None
            self.models_loading = False
            self._items.clear()
            if new_key == old_key and (profile_changed or key_changed):
                self._drop_contexts(new_key)
            saved = self.state["runtime_sessions"].get(new_key, {})
            self.state["context_slot"], self._context_model = None, saved.get("model")
            self.state["native_id"] = saved.get("native_id")
            self.state["toolset_version"] = saved.get("toolset_version", TOOLSET_VERSION)
            self.models = list(models if models is not None else self._route_models.get(new_key, []))
        self.settings = dict(settings)
        self.api_key = api_key
        self.state["provider"] = provider
        self.state["connection_id"] = connection_id
        self.state["next_route"] = None
        self._next_binding = None
        self.connection_error = False
        if models is not None:
            self.models = list(models)
        self.version += 1

    def choose_connection(self, provider, settings, api_key, connection_id, models=None, model=""):
        """Selection changes this chat's NEXT request, never its active billing."""
        if provider not in MODES:
            raise ValueError("Unknown provider profile")
        choices = list(models or self._route_models.get(route_key(connection_id, provider), []))
        selected = next((m for m in choices if m["id"] == model), None) if model else next((m for m in choices if m["default"]), None)
        if model and not selected:
            raise ValueError("Refresh the models available to this connection")
        options = stored_options(self.state.get("options"))
        changed = connection_id != self.selection_connection_id or provider != self.selection_provider
        effort = options["effort"]
        if selected and selected["efforts"] and effort not in selected["efforts"]:
            effort = selected["default_effort"] if selected["default_effort"] in selected["efforts"] else selected["efforts"][0]
        self.state["options"] = stored_options(options | {"model": model, "effort": effort,
            "fast": not changed and options["fast"] and bool(selected and selected["fast_tier"])})
        running = self.turn_running()
        if running:
            deferred = self._defer_binding(provider, settings, api_key, connection_id, choices)
            if deferred or model != self._turn_model_choice():
                self._append_row({"role": "system", **ui_text.notice(DEFERRED_CHOICE)})
        else:
            self.select(provider, settings, api_key, connection_id, choices)
        self.version += 1
        self.save()

    def _turn_model_choice(self):
        """The model the running request was sent with, as the picker names it ("" for the connection's default)."""
        return (self.turn_options or {}).get("model", "") if self._turn_route is None else self._turn_route.model

    def _defer_binding(self, provider, settings, api_key, connection_id, models):
        """A connection or model chosen during a turn: kept for the next message, and the chat says so once."""
        current = (provider, dict(settings), api_key, connection_id)
        if (current == (self.state["provider"], self.settings, self.api_key, self.state.get("connection_id"))
                and self._next_binding is None):
            return False   # The same choice as the running request's: nothing changes.
        binding = {"provider": provider, "settings": dict(settings), "api_key": api_key,
                   "connection_id": connection_id, "models": list(models or [])}
        changed = binding != self._next_binding
        self._next_binding = binding
        self.state["next_route"] = {"provider": provider, "connection_id": connection_id}
        self.version += 1
        return changed

    def apply_next_connection(self):
        # Not ``turn_running``: Controller.send calls this on a worker lane with its preparation marked.
        if self._next_binding and not self.busy and not (self._worker and self._worker.is_alive()):
            self._bind(**self._next_binding)
            self.save()
            return True
        return False
