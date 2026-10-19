# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's agent runtime and its cache-affinity keys (agent_runtime, cache_affinity, codex_connect, codex_runtime).

Selection: ``ANGELO_AGENT_RUNTIME`` (a path, or ``off``), then the runtime bundled beside the app, then the
person's own Codex; a path that is not an executable file is an error. Every agent-runtime process must report
the pinned version with its code-mode host beside it. ChatGPT is offered without a CLI when Angelo carries the
runtime. Keys: stable for one account and static prefix, different for another prefix, bounded, and free of
anything readable (the account under the per-user secret, which these tests supply through
``ANGELO_CACHE_KEY_SECRET`` so they never write the developer's data folder). Support is checked on the process
itself in two steps, and a key is sent only on a supporting process's ``thread/start``, never on a resume. A
keyed process never warms, resumes or forks a base: new chats start keyed threads and no warm-up request or
receipt exists. The adversarial review's reproductions are in ``test_native_agent_runtime_review.py``.
"""

import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (agent_runtime, cache_affinity, cli_runtime, codex_connect, codex_opening,  # noqa: E402
                          codex_runtime, first_run, product_env, providers)
from angelo_agent.codex_runtime import Runtime  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from angelo_agent.telemetry import TelemetryStore  # noqa: E402
from test_native_codex_runtime import (FakeProcess, PoolCase, Provider, Recorder, app_server,  # noqa: E402
                                       prediction_fork, threads_counter, wait_for)

PINNED_AGENT = "angelo_blender/%s (Mac OS; arm64)" % agent_runtime.PIN["version"]  # The pinned runtime.

PATCHED = RpcError("cacheAffinityKey must be 1-64 visible ASCII characters")
STOCK = RpcError("no rollout found for thread id 6bd5c6e3-39e9-4d8e-b121-59c6ef360abf")
KEY = re.compile(r"^angelo-[0-9a-f]{40}$")
# Tests never create the per-user secret in the developer's own data folder (cache_affinity_secret).
TEST_SECRET = "5e" * 32
_environment = patch.dict(os.environ, {"ANGELO_CACHE_KEY_SECRET": TEST_SECRET})


def setUpModule():
    _environment.start()


def tearDownModule():
    _environment.stop()


def patched_fork(params):
    """The patched build, as measured: an invalid key is refused before the thread is looked up; a fork of a
    thread that does not exist then answers "no rollout found"; a prediction fork is as stock."""
    key = params.get("cacheAffinityKey")
    if key is not None and not cache_affinity.valid(key):
        raise PATCHED
    if not params.get("experimentalPredictionMode"):
        raise RpcError("no rollout found for thread id %s" % params.get("threadId"))
    return prediction_fork(params)


def executable_file(folder, name):
    path = Path(folder, name)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = executable_file(self.temp.name, "codex")
        self.other = executable_file(self.temp.name, "other-codex")
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        product_env.clear(agent_runtime.OVERRIDE)
        agent_runtime._present.cache_clear()  # Looked up once per process; each test is its own process here.
        self.addCleanup(agent_runtime._present.cache_clear)

    def test_the_agent_runtime_runs_codex_before_any_other(self):
        os.environ["ANGELO_AGENT_RUNTIME"] = self.runtime
        with patch.object(cli_runtime.shutil, "which", return_value=self.other):
            self.assertEqual(cli_runtime.find_executable("codex"), self.runtime)
            self.assertEqual(cli_runtime.find_executable("codex", self.other), self.runtime,
                             "The agent runtime wins over a configured executable")
            self.assertEqual(providers.find_executable("codex"), self.runtime)
            self.assertEqual(cli_runtime.find_executable("claude", self.other), self.other, "Claude Code is unaffected")
        self.assertTrue(agent_runtime.is_agent_runtime(self.runtime))

    def test_an_override_that_is_not_an_executable_file_is_an_error_never_a_fallback(self):
        plain = Path(self.temp.name, "plain")
        plain.write_text("")
        for value in (str(plain), str(Path(self.temp.name, "missing")), "codex", self.temp.name):
            os.environ["ANGELO_AGENT_RUNTIME"] = value
            with self.subTest(value=value), patch.object(cli_runtime.shutil, "which", return_value=self.other):
                with self.assertRaises(RpcError) as caught:
                    cli_runtime.find_executable("codex", self.other)
                self.assertIn("ANGELO_AGENT_RUNTIME", str(caught.exception))
                self.assertFalse(agent_runtime.is_agent_runtime(self.other))

    def test_without_an_agent_runtime_codex_is_found_as_before(self):
        self.assertIsNone(agent_runtime.executable())
        self.assertEqual(cli_runtime.find_executable("codex", self.other), self.other)
        self.assertFalse(agent_runtime.is_agent_runtime(self.other))

    def test_the_same_file_under_another_spelling_is_the_agent_runtime(self):
        os.environ["ANGELO_AGENT_RUNTIME"] = self.runtime
        link = Path(self.temp.name, "link")
        link.symlink_to(self.runtime)
        self.assertTrue(agent_runtime.is_agent_runtime(str(link)))
        self.assertFalse(agent_runtime.is_agent_runtime(self.other))
        self.assertFalse(agent_runtime.is_agent_runtime(""))

    def test_the_bundled_runtime_comes_after_the_override_and_off_turns_it_off(self):
        with patch.object(agent_runtime, "bundled", return_value=self.runtime):
            self.assertEqual(cli_runtime.find_executable("codex", self.other), self.runtime, "Bundled wins")
            os.environ["ANGELO_AGENT_RUNTIME"] = self.other
            self.assertEqual(cli_runtime.find_executable("codex"), self.other, "The override wins over the bundle")
            os.environ["ANGELO_AGENT_RUNTIME"] = "off"
            self.assertEqual(cli_runtime.find_executable("codex", self.other), self.other)
            self.assertFalse(agent_runtime.is_agent_runtime(self.runtime))

    def test_the_bundled_runtime_is_found_beside_the_app(self):
        self.addCleanup(agent_runtime._bundled.cache_clear)
        app = Path(self.temp.name, "Angelo.app/Contents")
        (app / "MacOS").mkdir(parents=True)
        (app / "Helpers").mkdir()
        binary = executable_file(app / "MacOS", "Angelo")
        module = app / "Resources/5.2/scripts/addons_core/angelo_agent/agent_runtime.py"
        self.assertIsNone(agent_runtime.bundled("darwin", binary), "A development build bundles none")
        agent_runtime._bundled.cache_clear()
        codex = executable_file(app / "Helpers", "codex")
        self.assertEqual(agent_runtime.bundled("darwin", binary), codex)
        self.assertEqual(agent_runtime.bundled("darwin", "", str(module)), codex, "Found from the add-on's place")
        windows = Path(self.temp.name, "Angelo")
        (windows / "agent-runtime").mkdir(parents=True)
        exe = executable_file(windows / "agent-runtime", "codex.exe")
        self.assertEqual(agent_runtime.bundled("win32", str(windows / "Angelo.exe")), exe)
        self.assertEqual(agent_runtime.bundled("win32", "", str(windows / "5.2/scripts/addons_core/angelo_agent/x.py")),
                         exe)
        self.assertIsNone(agent_runtime.bundled("linux", binary), "No other platform bundles one")

    def test_chatgpt_is_offered_without_a_cli_only_where_angelo_carries_the_runtime(self):
        missing = {"codex_subscription": first_run.MISSING_RUNTIME, "claude_subscription": first_run.MISSING_RUNTIME}
        ids = lambda: [row["id"] for row in first_run.connect_options(missing)]  # noqa: E731
        with patch.object(agent_runtime, "bundled", return_value=None):
            self.assertEqual(ids(), ["api_key"], "Without it, the install hint stands")
            os.environ["ANGELO_AGENT_RUNTIME"] = self.runtime
            self.assertEqual(ids(), ["codex_subscription", "api_key"])
            self.assertTrue(first_run.runtime_available("codex_subscription", None))
            self.assertFalse(first_run.runtime_available("claude_subscription", None), "Claude Code is never built in")
            os.environ["ANGELO_AGENT_RUNTIME"] = "off"
            self.assertEqual(ids(), ["api_key"])


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        files = agent_runtime.layout() or {"required": ["codex", "codex-code-mode-host"]}
        self.codex = executable_file(self.temp.name, files["required"][0])
        self.host = Path(executable_file(self.temp.name, files["required"][1]))

    def agent(self, version):
        return {"userAgent": "angelo_blender/%s (Mac OS 26.3.0; arm64) unknown (Angelo; 0.1.0)" % version}

    def test_only_the_pinned_version_with_its_host_runs(self):
        pinned = agent_runtime.PIN["version"]
        self.assertEqual(agent_runtime.reported_version(self.agent(pinned)["userAgent"]), pinned)
        agent_runtime.verify(self.codex, self.agent(pinned))
        for version in ("0.162.0", "0.162.0-alpha.2", "0.163.0-alpha.17.2"):
            with self.subTest(version=version), self.assertRaises(RpcError) as caught:
                agent_runtime.verify(self.codex, self.agent(version))
            self.assertIn(pinned, str(caught.exception))
        with self.assertRaises(RpcError):
            agent_runtime.verify(self.codex, {})
        if agent_runtime.layout() is not None:
            self.host.unlink()
            with self.assertRaises(RpcError) as caught:
                agent_runtime.verify(self.codex, self.agent(pinned))
            self.assertIn(self.host.name, str(caught.exception))

    def test_initialize_verifies_an_agent_runtime_and_notices_drift_only_for_others(self):
        notes = []
        context = type("Context", (), {"emit": lambda self, kind, data=None: notes.append(kind)})()
        cli_runtime.note_codex_runtime(context, self.agent(agent_runtime.PIN["version"]), self.codex)
        with self.assertRaises(RpcError):
            cli_runtime.note_codex_runtime(context, self.agent("0.162.0"), self.codex)
        cli_runtime.note_codex_runtime(context, self.agent("0.162.0"))  # The person's own Codex, as before.
        self.assertEqual(notes, [])

    def test_the_pin_names_upstreams_files_and_the_fork(self):
        pin = agent_runtime.PIN
        self.assertEqual(pin["upstream"]["tag"], "rust-v" + pin["version"])
        self.assertRegex(pin["fork"]["commit"], r"^[0-9a-f]{40}$")
        self.assertIn("cacheAffinityKey", pin["features"])
        self.assertEqual(pin["platforms"]["macos"]["required"], ["codex", "codex-code-mode-host"])
        self.assertEqual(pin["platforms"]["macos"]["folder"], "Contents/Helpers")
        self.assertEqual(pin["platforms"]["windows"]["required"], ["codex.exe", "codex-code-mode-host.exe",
                                                                   "codex-command-runner.exe",
                                                                   "codex-windows-sandbox-setup.exe"])
        self.assertEqual(pin["platforms"]["windows"]["optional"], [], "a release never ships without its helpers")
        self.assertNotIn("windows", pin.get("_pending", {}), "the Windows runtime is built and pinned")
        self.assertNotIn("codex.exe", pin["platforms"]["windows"]["upstream_signed"], "Angelo signs its own build")


class KeyTests(unittest.TestCase):
    EMAIL = "artist@example.com"
    START = {"cwd": "/state/a/workspace", "modelProvider": "openai", "sandbox": "read-only",
             "approvalPolicy": "on-request", "serviceTier": "default", "model": "gpt-6.1-sol",
             "baseInstructions": "Build what the person asks.", "dynamicTools": [{"name": "angelo_scene_search"}]}
    TURN = {"model": "gpt-6.1-sol", "effort": "low", "outputSchema": {"type": "object"}, "serviceTierForTurn": "default"}

    def account(self, email=EMAIL):
        return "codex_subscription", hashlib.sha256(json.dumps(["chatgpt", email]).encode()).hexdigest()

    def key(self, start=None, turn=None, account=None, toolset=TOOLSET_VERSION):
        return cache_affinity.derive(cache_affinity.inputs(account or self.account(), start or self.START,
                                                           turn or self.TURN, toolset))

    def test_a_key_is_bounded_visible_ascii_and_stable(self):
        key = self.key()
        self.assertRegex(key, KEY)
        self.assertTrue(cache_affinity.valid(key))
        self.assertLessEqual(len(key), cache_affinity.MAX_LENGTH)
        self.assertEqual(key, self.key(dict(self.START), dict(self.TURN)))

    def test_paths_identities_time_and_effort_are_never_inputs(self):
        """What the chats sharing an opening need not share: folders, connection copy, tier, reasoning effort."""
        key = self.key()
        self.assertEqual(key, self.key(dict(self.START, cwd="/elsewhere/trial-07/state/workspace", threadId="x")))
        self.assertEqual(key, self.key(dict(self.START, serviceTier="priority"),
                                       dict(self.TURN, serviceTierForTurn="priority")))
        for effort in ("high", "medium", "xhigh", None):
            self.assertEqual(key, self.key(turn=dict(self.TURN, effort=effort)), "effort is a request parameter")
        values = cache_affinity.inputs(self.account(), self.START, self.TURN, TOOLSET_VERSION)
        self.assertEqual(set(values), {"account", "model", "prefix", "toolset"})
        self.assertEqual(set(values["prefix"]), {"instructions", "tools", "settings", "schema"})
        self.assertNotIn("/state/a", json.dumps(values))

    def test_everything_that_shapes_the_cached_prefix_or_its_owner_changes_the_key(self):
        key = self.key()
        for changed in (self.key(dict(self.START, baseInstructions="other")),
                        self.key(dict(self.START, dynamicTools=[])),
                        self.key(dict(self.START, model="gpt-6-astra"), dict(self.TURN, model="gpt-6-astra")),
                        self.key(dict(self.START, sandbox="workspace-write")),
                        self.key(turn=dict(self.TURN, outputSchema={"type": "object", "required": ["x"]})),
                        self.key(toolset=TOOLSET_VERSION + 1),
                        self.key(account=self.account("other@example.com")),
                        self.key(account=("codex_api", self.account()[1]))):
            self.assertNotEqual(key, changed)

    def test_a_key_and_its_trace_hold_nothing_that_can_be_read_back(self):
        account = self.account()
        values = cache_affinity.inputs(account, self.START, self.TURN, TOOLSET_VERSION)
        key = cache_affinity.derive(values)
        for secret in (self.EMAIL, "artist", "example", account[1][:12], "Build what", "angelo_scene_search"):
            self.assertNotIn(secret, key)
            self.assertNotIn(secret, json.dumps(values))
        stream = io.StringIO()
        with patch.object(sys, "stderr", stream):
            cache_affinity.trace(key, values, {})
            self.assertEqual(stream.getvalue(), "", "Silent unless asked")
            cache_affinity.trace(key, values, {"ANGELO_CACHE_KEY_TRACE": "1"})
        line = stream.getvalue()
        self.assertTrue(line.startswith("ANGELO_CACHE_KEY "))
        traced = json.loads(line.split(" ", 1)[1])
        self.assertEqual((traced["key"], traced["model"], traced["toolset"]), (key, "gpt-6.1-sol", TOOLSET_VERSION))
        self.assertNotIn("effort", traced)
        self.assertEqual(traced["prefix"]["instructions"], values["prefix"]["instructions"][:16])
        self.assertNotIn(self.EMAIL, line)

    def test_the_runtime_limits(self):
        for key in ("", " ", "a b", "x" * 65, "ключ", "tab\t"):
            self.assertFalse(cache_affinity.valid(key), key)
        for key in ("a", "x" * 64, "angelo-" + "0" * 40, "!~"):
            self.assertTrue(cache_affinity.valid(key), key)
        self.assertFalse(cache_affinity.valid(cache_affinity.INVALID_PROBE_KEY))

    def test_a_context_key_follows_its_prefix(self):
        runtime = codex_runtime.Runtime(("key",), "connection", app_server()[0], mode="codex_subscription")
        runtime.account = self.account()[1]
        provider = Provider(None)
        start = {"model": "gpt-6.1-sol", "baseInstructions": "a", "dynamicTools": [{"name": "t"}]}
        key = codex_connect.affinity_key(runtime, start, provider.controller)
        self.assertRegex(key, KEY)
        self.assertEqual(key, codex_connect.affinity_key(runtime, dict(start, cwd="/another/trial"), provider.controller))
        other = codex_runtime.Runtime(("other",), "another-connection", app_server()[0], mode="codex_subscription")
        other.account = runtime.account
        self.assertEqual(key, codex_connect.affinity_key(other, start, provider.controller),
                         "Another connection of the same account shares the key")
        for changed in (dict(start, baseInstructions="b"), dict(start, dynamicTools=[]), dict(start, model="x")):
            self.assertNotEqual(key, codex_connect.affinity_key(runtime, changed, provider.controller))
        effort = Provider(None, options={"model": "gpt-6.1-sol", "effort": "high", "fast": False,
                                         "approval": "AUTO_BOUNDED"})
        self.assertEqual(key, codex_connect.affinity_key(runtime, start, effort.controller), "Any effort, one key")
        fast = Provider(None, options={"model": "gpt-6.1-sol", "effort": "low", "fast": True, "approval": "AUTO_BOUNDED"})
        fast.controller.models = [{"id": "gpt-6.1-sol", "efforts": ["low"], "default_effort": "low", "fast_tier": None,
                                   "default": True}]
        self.assertEqual(key, codex_connect.affinity_key(runtime, start, fast.controller),
                         "An unoffered Fast tier (the turn reports it) still keys the thread")
        runtime.mode = "codex_api"
        self.assertIsNone(codex_connect.affinity_key(runtime, start, provider.controller), "Outside ELIGIBLE_MODES")


class ProbeTests(unittest.TestCase):
    def scripted(self, invalid, valid=STOCK):
        """A process answering the invalid key's probe with ``invalid`` and the valid key's with ``valid``."""
        def fork(params):
            answer = invalid if params["cacheAffinityKey"] == cache_affinity.INVALID_PROBE_KEY else valid
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return FakeProcess(None, None, {"thread/fork": fork})

    def test_two_steps_create_nothing_and_only_the_exact_refusal_then_no_rollout_is_support(self):
        process = self.scripted(PATCHED)
        self.assertEqual(cache_affinity.check(process), cache_affinity.SUPPORTED)
        (_, first), (_, second) = process.calls
        self.assertEqual(process.methods(), ["thread/fork", "thread/fork"])
        self.assertEqual((first["cacheAffinityKey"], second["cacheAffinityKey"]),
                         (cache_affinity.INVALID_PROBE_KEY, cache_affinity.VALID_PROBE_KEY))
        self.assertRegex(first["threadId"], r"^[0-9a-f-]{36}$")
        self.assertEqual(first["threadId"], second["threadId"], "A thread that does not exist: nothing is created")
        self.assertNotIn("experimentalPredictionMode", second)
        for invalid, valid in ((STOCK, STOCK), ({"thread": {"id": "x"}}, STOCK),
                               (PATCHED, RpcError("thread/fork.cacheAffinityKey requires experimentalApi capability")),
                               (PATCHED, {"thread": {"id": "x"}})):
            with self.subTest(invalid=invalid, valid=valid):
                self.assertEqual(cache_affinity.check(self.scripted(invalid, valid)), cache_affinity.NOT_PATCHED)

    def test_an_exit_or_a_silence_is_said_as_such_never_as_the_wrong_build(self):
        exited, gone, silent = (RpcError("Agent process exited"), RpcError("Agent process has disconnected"),
                                TimeoutError())
        for invalid, valid, verdict in ((exited, STOCK, cache_affinity.EXITED), (gone, STOCK, cache_affinity.EXITED),
                                        (BrokenPipeError(), STOCK, cache_affinity.EXITED),
                                        (silent, STOCK, cache_affinity.NO_ANSWER),
                                        (PATCHED, exited, cache_affinity.EXITED),
                                        (PATCHED, silent, cache_affinity.NO_ANSWER)):
            with self.subTest(verdict=verdict):
                self.assertEqual(cache_affinity.check(self.scripted(invalid, valid)), verdict)

    def test_each_failure_is_a_translatable_chat_error_naming_the_agent_runtime(self):
        from angelo_agent import ui_text
        for verdict in (cache_affinity.EXITED, cache_affinity.NO_ANSWER, cache_affinity.NOT_PATCHED):
            with self.subTest(verdict=verdict):
                error = cache_affinity.CheckFailed(verdict)
                self.assertIsInstance(error, RpcError)
                self.assertTrue(str(error).startswith("Angelo's agent runtime "))
                self.assertEqual(error.verdict, verdict)
                self.assertEqual(ui_text.stored_recipe({"role": "error", "text": str(error), "i18n": error.i18n}),
                                 error.i18n, "the chat translates it where it draws it")
        self.assertIn("within %d seconds" % cache_affinity.PROBE_TIMEOUT, str(cache_affinity.CheckFailed(
            cache_affinity.NO_ANSWER)))


class KeyedRuntimeTests(PoolCase):
    def setUp(self):
        super().setUp()
        self.answers = {"thread/start": threads_counter("chat"),
                        "thread/resume": lambda p: {"thread": {"id": p["threadId"]}},
                        "thread/fork": patched_fork, "turn/start": {"turn": {"id": "warm-turn"}}}
        spawn, processes = app_server(self.answers)
        self.runtime = Runtime(("key",), "connection", spawn, mode="codex_subscription", agent_runtime=True)
        self.process = processes[0]
        self.runtime.prepare(lambda: {"userAgent": PINNED_AGENT})

    def provider(self, native=None, **kwargs):
        return Provider(self.runtime.lease(Recorder().event, Recorder().request), native, **kwargs)

    def test_the_agent_runtime_is_checked_once_and_found_keyed(self):
        self.assertTrue(self.runtime.cache_affinity)
        self.assertEqual(self.process.methods(), ["thread/fork"] * 2, "Two local requests: no thread, no model")
        self.runtime.prepare(lambda: self.fail("initialized twice"))
        self.assertEqual(self.process.methods(), ["thread/fork"] * 2)

    def test_the_persons_own_codex_is_never_probed_or_keyed(self):
        spawn, processes = app_server(self.answers)
        stock = Runtime(("stock",), "connection", spawn, mode="codex_subscription")
        stock.prepare(lambda: {"userAgent": "angelo_blender/0.162.0 (Mac OS; arm64)"})
        self.assertFalse(stock.cache_affinity)
        self.assertEqual(processes[0].methods(), [])
        provider = Provider(stock.lease(Recorder().event, Recorder().request))
        with patch.object(codex_connect, "prepare_base"):
            codex_connect.open_thread(provider, {"model": "gpt-6.1-sol"}, lambda: [])
        (method, params), = processes[0].calls
        self.assertEqual(method, "thread/start")
        self.assertNotIn("cacheAffinityKey", params, "A key goes only to a runtime that accepts it")

    def test_an_agent_runtime_without_the_key_is_refused_and_retired(self):
        spawn, processes = app_server(dict(self.answers, **{"thread/fork": STOCK}))
        start = lambda rpc: {"userAgent": PINNED_AGENT}  # noqa: E731
        with self.assertRaises(cache_affinity.CheckFailed) as caught:
            codex_runtime.transport(spawn, Recorder().event, Recorder().request, start, lambda rpc: [], key=("pooled",),
                                    connection_id="connection", agent_runtime=True)
        self.assertEqual(caught.exception.verdict, cache_affinity.NOT_PATCHED)
        self.assertNotIn(("pooled",), codex_runtime._POOL)
        self.assertTrue(processes[0].closed.is_set())

    def test_new_chats_start_keyed_threads_and_never_touch_a_base(self):
        prepared = []
        with patch.object(codex_connect, "prepare_base", lambda *args, **kwargs: prepared.append(args)):
            first = self.provider()
            result = codex_connect.open_thread(first, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
            second = self.provider()
            codex_connect.open_thread(second, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
            codex_connect.after_turn(first, "completed")
        self.assertEqual(result["thread"]["id"], "chat-1")
        starts = [params for method, params in self.process.calls if method == "thread/start"]
        self.assertEqual(self.process.methods(), ["thread/fork"] * 2 + ["thread/start", "thread/start"])
        self.assertEqual(starts[0]["dynamicTools"], [{"name": "tool"}])
        expected = codex_connect.affinity_key(self.runtime, {"model": "gpt-6.1-sol", "dynamicTools": [{"name": "tool"}]},
                                              first.controller)
        self.assertEqual([params["cacheAffinityKey"] for params in starts], [expected, expected],
                         "Chats with the same startup context share one key")
        self.assertEqual(first._usage.total["input_tokens"], 0, "A started thread, measured from zero")
        self.assertEqual(prepared, [], "No base is resumed or warmed, before or after a turn")
        self.assertIsNone(getattr(first, "_warm_after_turn", None))
        self.assertIs(self.runtime.owners["chat-1"], first.rpc)

    def test_a_keyed_process_has_no_base_and_warms_none(self):
        provider = self.provider()
        start = {"model": "gpt-6.1-sol", "dynamicTools": []}
        variant = codex_connect.variant_key(start, codex_connect._turn_options(provider.controller))
        self.runtime.forks = True
        self.runtime.bases[variant] = {"state": "ready", "thread": "base", "total": None, "attempts": 1}
        self.assertFalse(self.runtime.has_base())
        codex_connect.open_thread(provider, {"model": "gpt-6.1-sol"}, lambda: [])
        self.assertEqual(self.process.methods(), ["thread/fork"] * 2 + ["thread/start"], "Never a fork of a base")
        del self.runtime.bases[variant]
        turn = codex_connect._turn_options(provider.controller)
        self.assertFalse(self.runtime.warm(variant, start, turn))
        self.assertFalse(self.runtime.warm(variant, start, turn, stored_only=True))
        self.assertEqual(self.runtime.bases, {})
        self.assertFalse(codex_connect.prepare_opening(self.provider(), {"model": "gpt-6.1-sol"}, lambda: []))
        time.sleep(.05)
        self.assertEqual(self.process.methods(), ["thread/fork"] * 2 + ["thread/start"], "No warm-up request")

    def test_a_resumed_thread_sends_no_key_and_keeps_its_own(self):
        codex_connect.open_thread(self.provider(), {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        resumed = self.provider(native="persisted")
        codex_connect.open_thread(resumed, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        (_, start), (_, resume) = [call for call in self.process.calls if call[0] in ("thread/start", "thread/resume")]
        self.assertEqual(resume["threadId"], "persisted")
        self.assertNotIn("dynamicTools", resume, "A resumed thread keeps its own tools")
        self.assertNotIn("cacheAffinityKey", resume, "...and its own key, persisted when it started")
        self.assertIn("cacheAffinityKey", start)

    def test_a_voice_thread_is_never_keyed(self):
        codex_connect.open_thread(self.provider(), {"model": "gpt-6.1-sol"}, lambda: [], fork=False)
        (_, params), = [call for call in self.process.calls if call[0] == "thread/start"]
        self.assertNotIn("cacheAffinityKey", params)


class KeyedProviderTests(PoolCase):
    """CodexProvider and the opening on Angelo's agent runtime, against a fake patched app-server."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settle_before_cleanup(self.temp.name)
        self.processes = []
        self.answers = {"initialize": {"userAgent": PINNED_AGENT},
                        "account/read": {"account": {"type": "chatgpt", "email": "artist@example.com"}},
                        "config/read": {"config": {}}, "model/list": {"data": []},
                        "thread/start": threads_counter("chat"), "thread/fork": patched_fork,
                        "thread/resume": lambda p: {"thread": {"id": p["threadId"]}},
                        "turn/start": {"turn": {"id": "warm-turn"}}}
        processes = self.processes
        files = agent_runtime.layout() or {"required": ["codex", "codex-code-mode-host"]}
        codex = executable_file(self.temp.name, files["required"][0])
        executable_file(self.temp.name, files["required"][1])  # Its code-mode host, as agent_runtime.verify needs.

        def process(command, *, env=None, cwd=None, spawn=None, on_event=None, on_request=None):
            processes.append(FakeProcess(on_event, on_request, self.answers))
            return processes[-1]
        self.agent_runtime = patch.object(providers.agent_runtime, "is_agent_runtime", return_value=True)
        for value in (patch.object(providers, "find_executable", return_value=codex),
                      patch.object(providers, "codex_command", return_value=([codex, "app-server"], {})),
                      patch.object(providers, "JsonRpcProcess", process), self.agent_runtime):
            value.start()
            self.addCleanup(value.stop)

    def controller(self, state=None, connection="connection"):
        controller = Controller(state or self.temp.name)
        self.addCleanup(controller.close)
        controller.set_document(("doc", 1))
        controller.state.update(provider="codex_subscription", native_id=None, toolset_version=TOOLSET_VERSION)
        controller.settings = {"connection_id": connection, "model": "gpt-6.1-sol"}
        return controller

    def chat(self, state=None, connection="connection", instructions=None):
        controller = self.controller(state, connection)
        controller._begin_metrics()
        context = controller.runtime_context()
        if instructions is not None:
            context.instructions = instructions
        provider = providers.CodexProvider(context, context.settings)
        self.addCleanup(provider.close)
        return provider

    def started(self):
        """The newest thread/start's parameters."""
        return [params for process in self.processes for method, params in process.calls if method == "thread/start"][-1]

    def test_launches_with_other_state_folders_and_connection_copies_of_one_account_share_the_key(self):
        """As the benchmark's trials: each a fresh process, its own state folder and its own connection copy."""
        keys, folders = [], []
        for trial in range(2):
            state = tempfile.TemporaryDirectory()
            self.addCleanup(state.cleanup)
            self.settle_before_cleanup(state.name)
            self.chat(Path(state.name) / ("trial-%02d" % trial) / "state", "copy-%d" % trial).connect()
            keys.append(self.started()["cacheAffinityKey"])
            folders.append(self.started()["cwd"])
            codex_runtime.close_all()  # The trial's Angelo quits; the next one starts a new app-server.
        self.assertEqual(len(self.processes), 2)
        self.assertNotEqual(folders[0], folders[1], "Each trial has its own working directory")
        self.assertEqual(keys[0], keys[1], "...and still the same key")
        self.chat(instructions="\nA project brief that changes the prefix.").connect()
        self.assertNotEqual(self.started()["cacheAffinityKey"], keys[0])
        self.answers["account/read"] = {"account": {"type": "chatgpt", "email": "someone-else@example.com"}}
        codex_runtime.close_all()
        self.chat().connect()
        self.assertNotEqual(self.started()["cacheAffinityKey"], keys[0], "Another account, another key")

    def test_new_chats_share_one_key_and_send_no_warm_up(self):
        first, second = self.chat(), self.chat()
        self.assertEqual(first.connect(), "chat-1")
        self.assertEqual(second.connect(), "chat-2")
        process = self.processes[0]
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(process.methods(), ["initialize", "thread/fork", "thread/fork", "account/read", "config/read",
                                             "model/list", "thread/start", "account/read", "config/read", "model/list",
                                             "thread/start"])
        keys = [params["cacheAffinityKey"] for method, params in process.calls if method == "thread/start"]
        self.assertEqual(len(set(keys)), 1)
        self.assertRegex(keys[0], KEY)
        self.assertNotIn("artist", keys[0])
        codex_connect.after_turn(first, "completed")
        time.sleep(.1)
        self.assertNotIn("turn/start", process.methods(), "No warm-up turn")
        self.assertEqual(process.methods().count("thread/fork"), 2, "Only the support check")
        runtime = first.rpc.runtime
        self.assertTrue(runtime.cache_affinity)
        self.assertEqual(runtime.bases, {})
        codex_runtime.close_all()
        TelemetryStore.shared(self.temp.name).flush()
        self.assertFalse([r for r in TelemetryStore.shared(self.temp.name).receipts if r.get("kind") == "warm_up"])

    def test_the_prepared_opening_starts_and_checks_the_process_and_warms_nothing(self):
        thread = codex_opening.prepare(self.controller())
        thread.join(30)
        self.assertFalse(thread.is_alive())
        process = self.processes[-1]
        self.assertEqual(process.methods(), ["initialize", "thread/fork", "thread/fork", "account/read", "config/read",
                                             "model/list"])
        time.sleep(.1)
        self.assertEqual(len(process.methods()), 6, "No base resumed, no warm-up")
        chat = self.chat()
        self.assertEqual(chat.connect(), "chat-1")
        self.assertEqual(len(self.processes), 1, "The first chat leases the prepared process")
        self.assertIn("cacheAffinityKey", process.calls[-1][1])

    def test_another_version_in_the_agent_runtimes_place_is_refused_before_any_request(self):
        self.answers["initialize"] = {"userAgent": "angelo_blender/0.162.0-alpha.2 (Mac OS; arm64)"}
        with self.assertRaises(RpcError) as caught:
            self.chat().connect()
        self.assertIn(agent_runtime.PIN["version"], str(caught.exception))
        self.assertEqual(self.processes[0].methods(), ["initialize"])
        self.assertTrue(self.processes[0].closed.is_set())

    def test_a_stock_build_in_the_agent_runtimes_place_is_refused(self):
        self.answers["thread/fork"] = STOCK
        with self.assertRaises(cache_affinity.CheckFailed) as caught:
            self.chat().connect()
        self.assertEqual(caught.exception.verdict, cache_affinity.NOT_PATCHED)
        self.assertTrue(self.processes[0].closed.is_set())
        self.assertNotIn("thread/start", self.processes[0].methods())

    def test_the_persons_own_codex_keeps_the_stock_path(self):
        self.agent_runtime.stop()
        self.answers["thread/fork"] = prediction_fork
        self.assertEqual(self.chat().connect(), "chat-1")
        process = self.processes[0]
        self.assertEqual(process.methods()[:5], ["initialize", "account/read", "config/read", "model/list",
                                                 "thread/start"])
        self.assertNotIn("cacheAffinityKey", process.calls[4][1])
        self.agent_runtime.start()


if __name__ == "__main__":
    unittest.main()
