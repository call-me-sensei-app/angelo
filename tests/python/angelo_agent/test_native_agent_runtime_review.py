# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for the agent runtime and the cache-affinity key.

Each problem was first reproduced as a failing test; these are those reproductions, inverted to assert the
fixed behaviour, with the attacks that did not break anything ("holds") kept:

1. The key's account input is HMAC-SHA256 under a per-user secret (``cache_affinity_secret``): an outsider
   who guesses an email recomputes neither a trace line nor the key, and the trace carries no account.
2. Accounts without an email no longer share one key across users; an API-key (or any non-eligible) mode is
   never keyed.
3. ``thread/resume`` sends no key: a resumed thread keeps the key it was started with, whatever changed.
4. The support check is two steps with exact answers, and a silence or an exit is said as such, as a
   translatable error, never a bare ``TimeoutError`` or "the wrong build".
5. Selection reads only ``ANGELO_AGENT_RUNTIME``; empty is an error; a release build accepts only ``off``;
   receipts say which runtime ran and whether its thread was keyed.
6. A keyed runtime never starts a keyless text thread (an unoffered Fast tier keys it all the same).

Host-only: fake app-servers, one real ``JsonRpcProcess`` over a silent process, no model, no network.
"""

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (agent_runtime, cache_affinity, cache_affinity_secret, cli_runtime, codex_connect,  # noqa: E402
                          codex_runtime, product_env, providers, telemetry, ui_text)
from angelo_agent.codex_runtime import Runtime  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess, RpcError  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from test_native_cache_affinity import PATCHED, STOCK, TEST_SECRET, patched_fork  # noqa: E402
from test_native_codex_runtime import FakeProcess, PoolCase, Provider, Recorder, app_server, threads_counter  # noqa: E402

AGENT = {"userAgent": "angelo_blender/%s (Mac OS; arm64)" % agent_runtime.PIN["version"]}  # The pinned runtime.
TOOLS = [{"name": "angelo_scene_search"}]
START = {"model": "gpt-6.1-sol", "modelProvider": "openai", "sandbox": "read-only", "approvalPolicy": "on-request",
         "serviceTier": "default", "baseInstructions": "Angelo's public, open-source instructions."}
OTHER_SECRET = "a1" * 32
_environment = patch.dict(os.environ, {"ANGELO_CACHE_KEY_SECRET": TEST_SECRET})


def setUpModule():
    _environment.start()


def tearDownModule():
    _environment.stop()


def keyed_runtime(answers, mode="codex_subscription", account=None):
    """A prepared, keyed shared runtime (the check passed), its account noted as a connect's checks note it."""
    spawn, processes = app_server(answers)
    runtime = Runtime(("key", mode, id(answers)), "connection", spawn, mode=mode, agent_runtime=True)
    runtime.prepare(lambda: AGENT)
    runtime.note_account(account if account is not None else {"type": "chatgpt", "email": "artist@example.com",
                                                                "planType": "plus"})
    return runtime, processes[0]


def options(**changes):
    return dict({"model": "gpt-6.1-sol", "effort": "low", "fast": False, "approval": "AUTO_BOUNDED"}, **changes)


def key_of(runtime, controller=None):
    return codex_connect.affinity_key(runtime, dict(START, dynamicTools=TOOLS), controller or Provider(None).controller)


# 1. Privacy -------------------------------------------------------------------------------------------------

class KeyIsNotAFunctionOfTheEmailAlone(PoolCase):
    def test_an_outsider_confirms_nothing_from_a_trace_line_or_the_key(self):
        runtime, _process = keyed_runtime({"thread/fork": patched_fork})
        provider = Provider(None, options=options())
        stream = io.StringIO()
        with patch.dict(os.environ, {"ANGELO_CACHE_KEY_TRACE": "1"}), patch.object(sys, "stderr", stream):
            key = codex_connect.affinity_key(runtime, dict(START, dynamicTools=TOOLS), provider.controller)
        traced = json.loads(stream.getvalue().split(" ", 1)[1])
        self.assertNotIn("account", traced, "The trace line names no account, digest or not")
        self.assertEqual(set(traced), {"key", "model", "prefix", "toolset"})

        def unsalted(email):  # What an outsider could compute from a guessed email and public values.
            identity = hashlib.sha256(json.dumps(["chatgpt", email]).encode()).hexdigest()
            return cache_affinity.derive({"account": hashlib.sha256(json.dumps(
                [cache_affinity.DOMAIN, "codex_subscription", identity], separators=(",", ":")).encode()).hexdigest(),
                "model": "gpt-6.1-sol", "prefix": cache_affinity.prefix_parts(
                    dict(START, dynamicTools=TOOLS), {"outputSchema": codex_connect._turn_options(
                        provider.controller)["outputSchema"]}), "toolset": TOOLSET_VERSION})
        self.assertNotIn(key, {unsalted(email) for email in ("artist@example.com", "someone@example.com")})

    def test_the_secret_keys_the_account_so_another_user_gets_another_key(self):
        runtime, _ = keyed_runtime({"thread/fork": patched_fork})
        mine = key_of(runtime)
        with patch.dict(os.environ, {"ANGELO_CACHE_KEY_SECRET": OTHER_SECRET}):
            theirs = key_of(runtime)
        self.assertNotEqual(mine, theirs)
        self.assertEqual(mine, key_of(runtime), "Stable for one user")


class PerUserSecretTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name, "Angelo")
        cache_affinity_secret.forget()
        self.addCleanup(cache_affinity_secret.forget)

    def test_created_once_owner_only_in_the_per_user_data_folder_and_shared_by_every_launch(self):
        first = cache_affinity_secret.secret({}, self.folder)
        path = self.folder / cache_affinity_secret.FILE
        self.assertEqual(len(first), 32)
        self.assertEqual(path.read_text().strip(), first.hex())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        cache_affinity_secret.forget()  # Another launch, another trial with its own state folder.
        trial = {"ANGELO_STATE_DIR": "/trial-02/state", "ANGELO_CONNECTIONS_DIR": "/trial-02/accounts"}
        self.assertEqual(cache_affinity_secret.secret(trial, self.folder), first)
        with patch.object(cache_affinity_secret.storage, "data_directory", return_value=self.folder) as folder:
            cache_affinity_secret.forget()
            self.assertEqual(cache_affinity_secret.secret(trial), first)
        folder.assert_called_once_with(trial)  # The data folder, never the state or account-store override.
        self.assertEqual([p.name for p in self.folder.iterdir()], [cache_affinity_secret.FILE], "No temporary left")

    def test_concurrent_launches_agree_and_a_damaged_file_is_replaced(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(cache_affinity_secret._stored(self.folder)))
                   for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1, "Every racer reads the winner's secret")
        (self.folder / cache_affinity_secret.FILE).write_text("not a secret")
        replaced = cache_affinity_secret._stored(self.folder)
        self.assertEqual((self.folder / cache_affinity_secret.FILE).read_text().strip(), replaced.hex())

    def test_a_campaign_passes_its_own_and_anything_else_is_refused(self):
        self.assertEqual(cache_affinity_secret.secret({"ANGELO_CACHE_KEY_SECRET": OTHER_SECRET}, self.folder),
                         bytes.fromhex(OTHER_SECRET))
        self.assertFalse(self.folder.exists(), "Nothing is written when the environment supplies it")
        for value in ("", "short", "zz" * 32, "a1" * 33):
            with self.subTest(value=value), self.assertRaises(RpcError):
                cache_affinity_secret.secret({"ANGELO_CACHE_KEY_SECRET": value}, self.folder)
        legacy = cache_affinity_secret.secret({"SENSEI_CACHE_KEY_SECRET": OTHER_SECRET}, self.folder)
        self.assertNotEqual(legacy, bytes.fromhex(OTHER_SECRET), "Only the ANGELO_ spelling")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "POSIX permissions")
    def test_an_unwritable_folder_keeps_this_launch_private_and_says_so(self):
        self.folder.mkdir()
        self.folder.chmod(0o500)
        self.addCleanup(self.folder.chmod, 0o700)
        stream = io.StringIO()
        with patch.object(sys, "stderr", stream):
            value = cache_affinity_secret.secret({}, self.folder)
        self.assertEqual(len(value), 32)
        self.assertIn("could not be stored", stream.getvalue())


# 2. Identity: no shared key across users; no key outside the eligible modes ---------------------------------

class AccountsWithoutAnEmail(PoolCase):
    def test_two_people_without_an_email_on_two_machines_get_two_keys(self):
        first, _ = keyed_runtime({"thread/fork": patched_fork},
                                 account={"type": "chatgpt", "email": None, "planType": "business"})
        second, _ = keyed_runtime({"thread/fork": patched_fork},
                                  account={"type": "chatgpt", "email": None, "planType": "enterprise"})
        self.assertEqual(first.account, second.account, "Codex gives both the same identity")
        mine = key_of(first)
        with patch.dict(os.environ, {"ANGELO_CACHE_KEY_SECRET": OTHER_SECRET}):
            theirs = key_of(second)
        self.assertIsNotNone(mine)
        self.assertNotEqual(mine, theirs, "Each user's secret separates them")

    def test_an_api_key_account_is_never_keyed(self):
        runtime, _ = keyed_runtime({"thread/fork": patched_fork}, mode="codex_api", account={"type": "apiKey"})
        self.assertIsNone(key_of(runtime))
        provider = Provider(runtime.lease(Recorder().event, Recorder().request))
        with self.assertRaises(RpcError):  # Never a keyless text thread on a keyed runtime (6).
            codex_connect.open_thread(provider, START, lambda: TOOLS)


# 3. Resume keeps the thread's own key -------------------------------------------------------------------------

class ResumeKeepsALiveThreadsRoute(PoolCase):
    def setUp(self):
        super().setUp()
        self.persisted, self.session_keys = {}, {}
        counter = threads_counter("chat")

        def start(params):
            thread = counter(params)
            self.persisted[thread["thread"]["id"]] = params.get("cacheAffinityKey")
            self.session_keys[thread["thread"]["id"]] = params.get("cacheAffinityKey")
            return thread

        def resume(params):  # An explicit key wins for the session; omitted keeps the stored one.
            thread = params["threadId"]
            self.session_keys[thread] = params.get("cacheAffinityKey") or self.persisted[thread]
            return {"thread": {"id": thread}}
        self.answers = {"thread/fork": patched_fork, "thread/start": start, "thread/resume": resume}

    def restart_and_resume(self, native, provider_options, tools):
        codex_runtime.close_all()
        runtime, _ = keyed_runtime(self.answers)
        provider = Provider(runtime.lease(Recorder().event, Recorder().request), native, options=provider_options)
        codex_connect.open_thread(provider, dict(START, model=provider_options["model"]), lambda: tools)

    def started(self, provider_options, tools):
        runtime, _ = keyed_runtime(self.answers)
        provider = Provider(runtime.lease(Recorder().event, Recorder().request), options=provider_options)
        return codex_connect.open_thread(provider, dict(START, model=provider_options["model"]),
                                         lambda: tools)["thread"]["id"]

    def test_an_effort_model_or_tool_change_before_a_resume_keeps_the_threads_route(self):
        for changed, tools in ((options(effort="high"), TOOLS), (options(model="gpt-6-astra"), TOOLS),
                               (options(), TOOLS + [{"name": "angelo_team_library_search"}])):
            with self.subTest(changed=changed, tools=len(tools)):
                thread = self.started(options(), TOOLS)
                self.assertTrue(self.persisted[thread])
                self.restart_and_resume(thread, changed, tools)
                self.assertEqual(self.session_keys[thread], self.persisted[thread])


# 4. The support check ------------------------------------------------------------------------------------------

class _SilentProcess:
    """A started app-server that never answers (a stalled first request on a loaded machine)."""

    def __init__(self, *args, **kwargs):
        out, self._out = os.pipe()
        err, self._err = os.pipe()
        self.stdout, self.stderr = os.fdopen(out, "r", encoding="utf-8"), os.fdopen(err, "r", encoding="utf-8")
        self.stdin, self.returncode, self._lock = io.StringIO(), None, threading.Lock()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        with self._lock:
            if self.returncode is None:
                os.close(self._out)
                os.close(self._err)
                self.returncode = 0
        return 0

    terminate = kill = wait


class CheckErrors(PoolCase):
    def test_a_silent_runtime_is_a_clear_translatable_error_not_a_bare_timeout(self):
        spawn = lambda on_event, on_request: JsonRpcProcess(  # noqa: E731
            ["codex", "app-server"], spawn=_SilentProcess, on_event=on_event, on_request=on_request)
        with patch.object(cache_affinity, "PROBE_TIMEOUT", 0.05):
            with self.assertRaises(cache_affinity.CheckFailed) as caught:
                codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: AGENT,
                                        lambda rpc: [], key=("silent",), connection_id="c", agent_runtime=True)
        self.assertEqual(caught.exception.verdict, cache_affinity.NO_ANSWER)
        self.assertIn("did not answer", str(caught.exception))
        self.assertIsNotNone(caught.exception.i18n)

    def test_a_crash_during_the_check_is_said_as_a_stop_not_as_the_wrong_build(self):
        spawn, _ = app_server({"thread/fork": RpcError("Agent process exited")})
        with self.assertRaises(cache_affinity.CheckFailed) as caught:
            codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: AGENT, lambda rpc: [],
                                    key=("crashed",), connection_id="c", agent_runtime=True)
        self.assertEqual(caught.exception.verdict, cache_affinity.EXITED)
        self.assertNotIn("not the Codex build", str(caught.exception))

    def test_only_the_exact_validation_refusal_then_no_rollout_means_support(self):
        for refusal in ("unknown field `cacheAffinityKey`, expected one of `threadId`, `path`",
                        "thread/fork.cacheAffinityKey requires experimentalApi capability",
                        "cacheAffinityKey is not supported by this server"):
            with self.subTest(refusal=refusal):
                spawn, _processes = app_server({"thread/fork": RpcError(refusal)})
                self.assertFalse(cache_affinity.supported(spawn(None, None)))

    def test_the_chat_shows_the_failure_in_the_persons_language(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(folder)
            try:
                controller.set_document(("doc", 1))
                controller.state["native_id"] = None
                controller._begin_metrics()
                controller.busy = True
                with patch.object(controller, "_connect_runtime",
                                  side_effect=cache_affinity.CheckFailed(cache_affinity.NOT_PATCHED)):
                    controller._run("Hello", {})
                controller.pump_events()
                error = [m for m in controller.state["messages"] if m.get("role") == "error"][-1]
                self.assertEqual(ui_text.stored_recipe(error), [cache_affinity.FAILURES[cache_affinity.NOT_PATCHED],
                                                                {}])
            finally:
                controller.close()


# 5. Selection and receipts --------------------------------------------------------------------------------------

class Selection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.own = Path(self.temp.name, "own-codex")
        self.own.write_text("#!/bin/sh\n")
        self.own.chmod(self.own.stat().st_mode | stat.S_IXUSR)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for prefix in ("ANGELO_", "SENSEI_", "TOONLAB_"):
            os.environ.pop(prefix + agent_runtime.OVERRIDE, None)
        agent_runtime._present.cache_clear()
        self.addCleanup(agent_runtime._present.cache_clear)

    def test_a_set_but_empty_override_is_an_error_never_the_persons_codex(self):
        for value in ("", "  "):
            os.environ["ANGELO_AGENT_RUNTIME"] = value
            with self.subTest(value=value), patch.object(cli_runtime.shutil, "which", return_value=str(self.own)):
                with self.assertRaises(RpcError) as caught:
                    cli_runtime.find_executable("codex")
                self.assertIn("set but empty", str(caught.exception))

    def test_only_the_angelo_spelling_selects_the_binary_that_receives_the_login(self):
        for legacy in ("TOONLAB_AGENT_RUNTIME", "SENSEI_AGENT_RUNTIME"):
            os.environ[legacy] = str(self.own)
            self.assertIsNone(agent_runtime.override())
            with patch.object(agent_runtime, "bundled", return_value=None):
                self.assertIsNone(agent_runtime.executable(), legacy)

    def test_a_release_build_accepts_only_off(self):
        with patch.object(agent_runtime, "release_build", return_value=True), \
                patch.object(agent_runtime, "bundled", return_value=str(self.own)):
            os.environ["ANGELO_AGENT_RUNTIME"] = str(self.own)
            with self.assertRaises(RpcError) as caught:
                agent_runtime.executable()
            self.assertIn("runs only its own agent runtime", str(caught.exception))
            os.environ["ANGELO_AGENT_RUNTIME"] = "off"
            self.assertIsNone(agent_runtime.executable())
            del os.environ["ANGELO_AGENT_RUNTIME"]
            self.assertEqual(agent_runtime.executable(), str(self.own))
        with patch.object(agent_runtime, "release_build", return_value=False):
            os.environ["ANGELO_AGENT_RUNTIME"] = str(self.own)
            self.assertEqual(agent_runtime.executable(), str(self.own), "A development build keeps overrides")

    def test_a_release_build_is_one_with_the_marker_beside_its_bundled_runtime(self):
        app = Path(self.temp.name, "Angelo.app/Contents")
        for name, relative in (("Angelo", "MacOS"), ("codex", "Helpers"), ("codex-code-mode-host", "Helpers")):
            (app / relative).mkdir(parents=True, exist_ok=True)
            (app / relative / name).write_text("#!/bin/sh\n")
            (app / relative / name).chmod(0o755)
        binary = str(app / "MacOS/Angelo")
        agent_runtime._bundled.cache_clear()
        agent_runtime._release_build.cache_clear()
        self.addCleanup(agent_runtime._release_build.cache_clear)
        self.addCleanup(agent_runtime._bundled.cache_clear)
        self.assertFalse(agent_runtime.release_build("darwin", binary), "A development build that bundles one")
        marker = Path(self.temp.name, "Angelo.app", agent_runtime.PIN["platforms"]["macos"]["release_marker"])
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
        agent_runtime._release_build.cache_clear()
        self.assertTrue(agent_runtime.release_build("darwin", binary))

    def test_receipts_say_which_runtime_ran_and_whether_it_was_keyed(self):
        route = telemetry.clean_route({"agent_runtime": "bundled", "cache_affinity": True, "fast": False, "auto": True,
                                       "single_model": False})
        self.assertEqual(route, {"agent_runtime": "bundled", "cache_affinity": True, "fast": False, "auto": True,
                                 "single_model": False})
        self.assertIsNone(telemetry.clean_route({"agent_runtime": "/Users/x/codex", "cache_affinity": "yes"}))
        os.environ["ANGELO_AGENT_RUNTIME"] = str(self.own)
        self.assertEqual(agent_runtime.kind(str(self.own)), "override")
        os.environ["ANGELO_AGENT_RUNTIME"] = "off"
        self.assertEqual(agent_runtime.kind(str(self.own)), "own")
        del os.environ["ANGELO_AGENT_RUNTIME"]
        with patch.object(agent_runtime, "bundled", return_value=str(self.own)):
            self.assertEqual(agent_runtime.kind(str(self.own)), "bundled")

    def test_ai_providers_shows_an_active_override(self):
        source = (Path(agent_runtime.__file__).parent / "ui.py").read_text(encoding="utf-8")
        self.assertIn('if agent_runtime.override() is not None:', source)
        self.assertIn('iface_("Agent runtime override: ANGELO_AGENT_RUNTIME={value}", value=agent_runtime.override())',
                      source)


class KeyedConnectReceipt(PoolCase):
    def test_the_connect_stamps_the_requests_route(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settle_before_cleanup(temp.name)
        folder = temp.name
        answers = {"initialize": AGENT, "account/read": {"account": {"type": "chatgpt", "email": "a@example.com"}},
                   "config/read": {"config": {}}, "model/list": {"data": []}, "thread/fork": patched_fork,
                   "thread/start": threads_counter("chat")}
        codex = Path(folder, "codex")
        codex.write_text("#!/bin/sh\n")
        codex.chmod(0o755)
        Path(folder, "codex-code-mode-host").write_text("")  # Beside codex, as agent_runtime.verify requires.
        processes = []

        def process(command, *, env=None, cwd=None, spawn=None, on_event=None, on_request=None):
            processes.append(FakeProcess(on_event, on_request, answers))
            return processes[-1]
        controller = Controller(folder)
        self.addCleanup(controller.close)
        controller.set_document(("doc", 1))
        controller.state.update(provider="codex_subscription", native_id=None, toolset_version=TOOLSET_VERSION)
        controller.settings = {"connection_id": "connection", "model": "gpt-6.1-sol"}
        controller._begin_metrics()
        context = controller.runtime_context()
        provider = providers.CodexProvider(context, context.settings)
        self.addCleanup(provider.close)
        with patch.object(providers, "find_executable", return_value=str(codex)), \
                patch.object(providers, "codex_command", return_value=([str(codex), "app-server"], {})), \
                patch.object(providers, "JsonRpcProcess", process), \
                patch.object(agent_runtime, "bundled", return_value=str(codex)), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANGELO_AGENT_RUNTIME", None)
            provider.connect()
        self.assertEqual({key: controller._request_metrics.route.get(key) for key in
                          ("agent_runtime", "cache_affinity")}, {"agent_runtime": "bundled", "cache_affinity": True})


# 6. Never a keyless text thread on a keyed runtime ----------------------------------------------------------------

class NoKeylessThreadOnAKeyedRuntime(PoolCase):
    def test_an_unoffered_fast_tier_or_unavailable_model_still_starts_a_keyed_thread(self):
        for provider_options in (options(fast=True), options(model="gpt-retired")):
            with self.subTest(options=provider_options):
                runtime, process = keyed_runtime({"thread/fork": patched_fork,
                                                  "thread/start": threads_counter("chat")})
                provider = Provider(runtime.lease(Recorder().event, Recorder().request), options=provider_options)
                provider.controller.models = [{"id": "gpt-6.1-sol", "efforts": ["low"], "default_effort": "low",
                                               "fast_tier": None, "default": True}]
                codex_connect.open_thread(provider, dict(START, model=provider_options["model"]), lambda: TOOLS)
                (_, params), = [call for call in process.calls if call[0] == "thread/start"]
                self.assertIn("cacheAffinityKey", params)


# Attacks that did not break anything ("holds") --------------------------------------------------------------------

class HoldsTeamAndCollaboratorThreadsAreNeverKeyed(unittest.TestCase):
    def test_only_a_personal_chatgpt_plan_chat_can_reach_a_keyed_runtime(self):
        from angelo_agent.provider_registry import CUSTOM_PROVIDER_MODES, SPECS, TEAM_MODES
        chat = SimpleNamespace(voice=None)
        for mode in sorted(TEAM_MODES | CUSTOM_PROVIDER_MODES | {"codex_api"}):
            self.assertFalse(codex_runtime.eligible(chat, mode, mode in CUSTOM_PROVIDER_MODES, True), mode)
        self.assertLessEqual({mode for mode in TEAM_MODES if SPECS[mode]["runtime"] == "codex"}, CUSTOM_PROVIDER_MODES)
        self.assertFalse(codex_runtime.eligible(chat, "codex_subscription", False, False), "Non-plan chats")
        self.assertFalse(codex_runtime.eligible(SimpleNamespace(voice=object()), "codex_subscription", False, True))

    def test_a_collaborator_never_plans_so_never_leases_a_shared_runtime(self):
        from angelo_agent.codex_plan import plan_mode
        from angelo_agent.tool_specs import COLLABORATOR_TOOLS
        self.assertFalse(plan_mode(SimpleNamespace(voice=None, base_instructions=None,
                                                   allowed_tools=COLLABORATOR_TOOLS), False))


class HoldsDistinctEmailsDistinctKeys(PoolCase):
    def test_two_accounts_with_emails_never_share_a_key(self):
        keys = set()
        for email in ("a@example.com", "b@example.com", "A@example.com"):
            runtime, _ = keyed_runtime({"thread/fork": patched_fork},
                                       account={"type": "chatgpt", "email": email, "planType": "plus"})
            keys.add(key_of(runtime))
        self.assertEqual(len(keys), 3)


class HoldsTheCheckOnThePatchedBuild(unittest.TestCase):
    def test_the_patched_answers_are_support_and_stocks_are_not(self):
        spawn, _processes = app_server({"thread/fork": patched_fork})
        self.assertTrue(cache_affinity.supported(spawn(None, None)))
        spawn, _processes = app_server({"thread/fork": STOCK})
        self.assertFalse(cache_affinity.supported(spawn(None, None)))
        spawn, _processes = app_server({"thread/fork": PATCHED})  # Refuses every key: not the patched build.
        self.assertFalse(cache_affinity.supported(spawn(None, None)))


if __name__ == "__main__":
    unittest.main()
