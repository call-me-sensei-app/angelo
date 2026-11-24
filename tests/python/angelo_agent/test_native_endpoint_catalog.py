# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A custom provider's model catalog for Codex (``endpoint_catalog``): concurrent first launches, failed listings,
the listing's isolation, reasoning-level text and every profile read from its own wire's catalog (the break-it review
of b73cd8d25d1: SHOULD-1, SHOULD-2, NIT-1 to NIT-3). Fakes stand in for the runtime; nothing is billed or sent."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import host_waits  # noqa: E402
from angelo_agent import endpoint_catalog, provider_registry, shared_replace  # noqa: E402

LEVELS = [{"effort": "low", "description": "Thinks least"},
          {"effort": "medium", "description": "Google's default thinking level"},
          {"effort": "high", "description": "Thinks longest"}]
CARBON = {"slug": "gemini-4-carbon", "default_reasoning_level": "medium", "supported_reasoning_levels": LEVELS,
          "truncation_policy": {"mode": "tokens", "limit": 10000}}
GPT = {"slug": "gpt-6.1-sol", "use_responses_lite": True, "truncation_policy": {"mode": "tokens", "limit": 10000}}


class Runtime:
    """A fake runtime: Carbon for its Gemini wire, GPT for its bundled catalog; records each listing's call."""

    def __init__(self, fail=0, gate=None):
        self.calls, self.fail, self.gate, self.lock = [], fail, gate, threading.Lock()

    def __call__(self, argv, **kwargs):
        with self.lock:
            self.calls.append((list(argv), kwargs))
            failing = self.fail > 0
            self.fail -= failing
        if self.gate is not None:
            self.gate.wait(host_waits.CAP_S)
        if failing:
            return SimpleNamespace(returncode=1, stdout="")
        models = [GPT] if "--bundled" in argv else [CARBON]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"models": models}))


class Case(unittest.TestCase):
    def setUp(self):
        endpoint_catalog._cache.clear()
        getattr(endpoint_catalog, "_told", set()).clear()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        for patcher in (patch.object(endpoint_catalog, "_signature", return_value=("codex", 1, 1)),
                        patch.object(endpoint_catalog.owned_paths, "require_runnable", side_effect=lambda path: path)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def written(self, path):
        return json.loads(Path(path).read_text())["models"]


class ConcurrentWrites(Case):
    def test_concurrent_first_launches_never_share_a_temporary_file(self):
        """Two launches at once (prewarm and connect, two chats of one connection) each write the catalog: neither
        fails and the file is whole. A fixed temporary name let one replace the other's and fail."""
        spec, runtime = provider_registry.SPECS["gemini_api"], Runtime()
        endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini")   # Listed once, before the race.
        real_replace, both_waiting, errors, paths = shared_replace.replace, threading.Barrier(2), [], []

        def replace(source, target, *args, **kwargs):
            both_waiting.wait(host_waits.CAP_S)   # Each writer holds its own temporary file before either replaces.
            return real_replace(source, target, *args, **kwargs)

        def launch():
            try:
                paths.append(endpoint_catalog.write("codex", spec, self.home.name, runtime, wire="gemini", own=True))
            except Exception as error:   # noqa: BLE001 - the failure under test.
                errors.append(error)
        # The in-process lock off: this proves the temporary names alone keep writers apart (two processes).
        with patch.object(endpoint_catalog.shared_replace, "replace", replace), \
                patch.object(endpoint_catalog, "_writes", contextlib.nullcontext(), create=True):
            threads = [threading.Thread(target=launch) for _ in range(2)]
            [thread.start() for thread in threads]
            [thread.join(host_waits.CAP_S) for thread in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(paths), 2)
        self.assertEqual([m["slug"] for m in self.written(paths[0])], ["gemini-4-carbon", "gemini-4-neon"])
        self.assertEqual(sorted(os.listdir(self.home.name)), [endpoint_catalog.FILE_NAME])   # No temporary left.

    def test_a_failed_write_leaves_no_temporary_file(self):
        spec = provider_registry.SPECS["gemini_api"]
        with patch.object(endpoint_catalog.shared_replace, "replace", side_effect=OSError("refused")), \
                self.assertRaises(OSError):
            endpoint_catalog.write("codex", spec, self.home.name, Runtime(), wire="gemini", own=True)
        self.assertEqual(os.listdir(self.home.name), [])


class Listings(Case):
    def test_concurrent_first_listings_share_one_run(self):
        gate, runtime, found = threading.Event(), None, []
        runtime = Runtime(gate=gate)
        threads = [threading.Thread(target=lambda: found.append(
            endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini"))) for _ in range(3)]
        [thread.start() for thread in threads]
        host_waits.until(lambda: runtime.calls, what="the first listing")
        gate.set()
        [thread.join(host_waits.CAP_S) for thread in threads]
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual([list(each) for each in found], [["gemini-4-carbon"]] * 3)

    def test_a_failed_listing_is_told_once_and_tried_again_later(self):
        runtime, now = Runtime(fail=2), [1000.0]
        with patch.object(endpoint_catalog.time, "monotonic", side_effect=lambda: now[0]), \
                patch("builtins.print") as told:
            self.assertEqual(endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini"), {})
            self.assertEqual(endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini"), {})
            self.assertEqual(len(runtime.calls), 1, "a failure is not listed again at once")
            now[0] += endpoint_catalog.RETRY_SECONDS + 1
            self.assertEqual(endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini"), {})
            now[0] += endpoint_catalog.RETRY_SECONDS + 1
            self.assertEqual(list(endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini")),
                             ["gemini-4-carbon"])
            self.assertEqual(list(endpoint_catalog.bundled("codex", self.home.name, runtime, "gemini")),
                             ["gemini-4-carbon"])
        self.assertEqual(len(runtime.calls), 3, "a success is kept")
        self.assertEqual(told.call_count, 1)
        self.assertIn("exit status 1", told.call_args[0][0])
        self.assertIn("gemini", told.call_args[0][0])

    def test_a_refused_runtime_is_never_cached_as_no_catalog(self):
        with patch.object(endpoint_catalog.owned_paths, "require_runnable", side_effect=PermissionError("C: no")), \
                self.assertRaises(PermissionError):
            endpoint_catalog.bundled("codex", self.home.name, Runtime(), "gemini")
        self.assertEqual(endpoint_catalog._cache, {})

    def test_the_listing_reads_nobody_s_configuration(self):
        """``debug models`` has no --ignore-user-config: it runs from an empty CODEX_HOME and folder of its own, a
        private scratch folder (private_roots), removed after; never the profile a configuration may be in."""
        Path(self.home.name, "config.toml").write_text('model_provider = "elsewhere"\n')
        seen = []
        runtime = Runtime()
        listing = runtime.__call__

        def run(argv, **kwargs):
            seen.append(sorted(os.listdir(kwargs["cwd"])))   # What the listing's folder holds while it runs.
            return listing(argv, **kwargs)
        endpoint_catalog.bundled("codex", self.home.name, run, "gemini")
        argv, kwargs = runtime.calls[0]
        self.assertEqual(kwargs["cwd"], kwargs["env"]["CODEX_HOME"])
        self.assertTrue(Path(kwargs["cwd"]).name.startswith(endpoint_catalog.LISTING_PREFIX))
        self.assertNotEqual(Path(kwargs["cwd"]), Path(self.home.name))
        self.assertNotIn("config.toml", seen[0])
        self.assertFalse(Path(kwargs["cwd"]).exists())
        self.assertEqual(argv[-2:], ["debug", "models"])


class Entries(Case):
    def test_neon_s_levels_claim_no_other_model_s_default(self):
        neon = endpoint_catalog.entry("gemini-4-neon", {"gemini-4-carbon": CARBON})
        self.assertEqual(neon["default_reasoning_level"], "high")
        texts = {level["effort"]: level["description"] for level in neon["supported_reasoning_levels"]}
        self.assertNotIn("default", texts["medium"])
        self.assertIn("default", texts["high"])
        self.assertEqual(endpoint_catalog.entry("gemini-4-carbon", {"gemini-4-carbon": CARBON}), CARBON)

    def test_a_gemini_endpoint_model_mapped_to_a_gpt_profile_keeps_the_gpt_entry(self):
        """A profile's entry comes from its own wire's catalog, whatever the endpoint's format."""
        spec = {"models": [{"id": "proxy-sol", "profile": "gpt-6.1-sol"}]}
        path = endpoint_catalog.write("codex", spec, self.home.name, Runtime(), wire="gemini")
        models = self.written(path)
        self.assertEqual(models[0], dict(GPT, slug="proxy-sol", display_name="proxy-sol"))

    def test_an_unmapped_runtime_model_name_keeps_its_built_in_entry_beside_a_mapped_one(self):
        spec = {"models": [{"id": "house-neon", "profile": "gemini-4-neon"}, {"id": "gemini-4-carbon", "profile": None}]}
        path = endpoint_catalog.write("codex", spec, self.home.name, Runtime(), wire="gemini")
        self.assertEqual([m["slug"] for m in self.written(path)], ["house-neon", "gemini-4-carbon"])
        self.assertEqual(self.written(path)[1], CARBON)

    def test_registry_wires(self):
        self.assertEqual(provider_registry.registry_wire("gemini-4-neon"), "gemini")
        self.assertEqual(provider_registry.registry_wire("gemini-4-carbon"), "gemini")
        self.assertIsNone(provider_registry.registry_wire("gpt-6.1-sol"))
        self.assertIsNone(provider_registry.registry_wire("grok-4.6"))


if __name__ == "__main__":
    unittest.main()
