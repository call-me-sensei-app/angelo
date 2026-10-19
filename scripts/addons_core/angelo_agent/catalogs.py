# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Catalog discovery across ready connections, without switching or inference."""

import queue
import threading
from types import SimpleNamespace
from pathlib import Path

from .providers import CodexProvider, ClaudeProvider
from .chat_settings import clean_options
from .routing import TOOLSET_VERSION
from .provider_registry import billing_label, is_coding, runtime_kind, PROVIDER_NAMES


class ModelCatalogs:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.entries, self.loading, self.errors, self.generations = {}, set(), {}, {}
        self.events = queue.Queue()
        self.closed = False
        self._providers = set()
        self._lock = threading.Lock()

    def seed(self, connection_id, models):
        if connection_id and models and connection_id not in self.loading and connection_id not in self.entries:
            self.entries[connection_id] = list(models)

    def invalidate(self, connection_id):
        self.generations[connection_id] = self.generations.get(connection_id, 0) + 1
        self.entries.pop(connection_id, None)
        self.errors.pop(connection_id, None)
        self.loading.discard(connection_id)

    def refresh(self, record, settings, api_key="", force=False):
        if not is_coding(record):
            return
        identity = record["id"]
        if self.closed or identity in self.loading or (not force and (identity in self.entries or identity in self.errors)):
            return
        generation = self.generations.get(identity, 0)
        self.loading.add(identity)
        self.errors.pop(identity, None)
        workspace = self.directory / "workspace" / "catalogs" / identity
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = SimpleNamespace(state={"provider": record["mode"], "native_id": None,
            "messages": [], "options": clean_options({}), "toolset_version": TOOLSET_VERSION},
            settings=dict(settings), api_key=api_key, store=SimpleNamespace(directory=self.directory),
            workspace=workspace, turn_options=None, turn_attachments=[], cancelled=False, models=[], voice=None,
            emit=lambda *args, **kwargs: None,
            request_tool=lambda cid, name, args, callback, **kwargs: callback({"error": "Catalog discovery cannot execute tools"}))

        def load():
            provider = None
            try:
                provider_type = CodexProvider if runtime_kind(record["mode"]) == "codex" else ClaudeProvider
                provider = provider_type(probe, probe.settings)
                with self._lock:
                    if self.closed:
                        return
                    self._providers.add(provider)
                self.events.put((identity, generation, provider.list_models(), ""))
            except Exception as error:
                value = str(error).replace(api_key, "[REDACTED]") if api_key else str(error)
                self.events.put((identity, generation, [], value[:400]))
            finally:
                if provider:
                    provider.close()
                    with self._lock:
                        self._providers.discard(provider)
        threading.Thread(target=load, daemon=True, name="AngeloConnectionCatalog").start()

    def pump(self):
        changed = False
        while True:
            try:
                identity, generation, models, error = self.events.get_nowait()
            except queue.Empty:
                return changed
            if generation != self.generations.get(identity, 0):
                continue
            self.loading.discard(identity)
            self.entries[identity] = models
            if error:
                self.errors[identity] = error
            else:
                self.errors.pop(identity, None)
            changed = True

    def choices(self, records):
        output = []
        for record in records:
            if not is_coding(record):
                continue
            for model in self.entries.get(record["id"], []):
                output.append(dict(model, connection_id=record["id"], provider=record["mode"],
                                   provider_id=record["provider"].lower(),
                                   provider_name=PROVIDER_NAMES.get(record["provider"], record["provider"]),
                                   connection_name=record["name"],
                                   billing=billing_label(record["mode"])))
        return output

    def close(self):
        with self._lock:
            self.closed = True
            providers = list(self._providers)
        for provider in providers:
            provider.controller.cancelled = True
            threading.Thread(target=provider.close, daemon=True).start()
