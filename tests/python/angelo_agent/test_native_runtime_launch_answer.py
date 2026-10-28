# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's Codex checks use the lead's launch answer, never a runtime the worker resolves itself (FE D1).

Angelo launches a worker chat's Codex (``runtime_service``) and says in its answer which Codex that was, as Angelo
resolved it: ``{kind, version}`` (``agent_runtime.launched``; the agent runtime's files are checked there). The
worker's version check (``codex_connect.initialize``), its cache-affinity probe (``codex_runtime.Runtime.prepare``)
and its receipt (``codex_connect.stamp_route``) follow that answer; an answer without one refuses the launch. The
answer is the only way across: the worker's environment never carries ``ANGELO_AGENT_RUNTIME``.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (agent_runtime, codex_connect, codex_runtime, runtime_relay, runtime_service,  # noqa: E402
                          worker_process)
from angelo_agent.headless_pipe import PipeClosed  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

PIN = agent_runtime.PIN["version"]


class Ended:
    def receive(self):
        raise PipeClosed()

    def close(self):
        pass


def relayed_rpc(runtime, agent="angelo_blender/9.9.9 (x)"):
    """A ``JsonRpcProcess`` over a relayed process that reports ``agent`` from its ``initialize``."""
    process = runtime_relay.RelayedProcess(Ended(), 11, runtime=runtime)
    requests = []

    def request(method, params):
        requests.append(method)
        return {"userAgent": agent} if method == "initialize" else {}
    return SimpleNamespace(process=process, request=request, notify=lambda method: None, requests=requests)


class TheLeadSaysWhichCodexItLaunched(unittest.TestCase):
    def test_the_answer_is_as_angelo_resolved_it_and_its_files_are_checked_where_it_runs(self):
        with mock.patch.object(agent_runtime, "kind", lambda path, environ=None: "own"):
            self.assertEqual(agent_runtime.launched("/x/codex"), {"kind": "own", "version": None})
        with mock.patch.object(agent_runtime, "kind", lambda path, environ=None: "override"), \
                mock.patch.object(agent_runtime, "check_files") as checked:
            self.assertEqual(agent_runtime.launched("/x/codex"), {"kind": "override", "version": PIN})
        checked.assert_called_once_with("/x/codex")
        with mock.patch.object(agent_runtime, "kind", lambda path, environ=None: "bundled"), \
                mock.patch.object(agent_runtime, "check_files", side_effect=RpcError("incomplete")):
            with self.assertRaisesRegex(RpcError, "incomplete"):
                agent_runtime.launched("/x/codex")   # Refused before anything is launched.

    def test_the_launch_answer_carries_it(self):
        chat = SimpleNamespace(mode="codex", connection_id="", api_key="")   # A hosted chat: a launch is for one.
        service = runtime_service.RuntimeService(chats=lambda session: chat)
        planned = {"kind": "codex", "key": ("s",), "runtime": {"kind": "bundled", "version": PIN}}
        with mock.patch.object(service, "plan", lambda request, chat: planned), \
                mock.patch.object(service, "_adopt", lambda key: SimpleNamespace(pid=5)), \
                mock.patch.object(service, "_relay", lambda child: ("fd:passed", None)):
            answer = service.launch({"session": "s"}, chat=chat)
        self.assertEqual(answer["runtime"], {"kind": "bundled", "version": PIN})


class TheWorkerChecksAgainstIt(unittest.TestCase):
    def launcher(self, response):
        killed = []
        caller = SimpleNamespace(request=lambda request, timeout: (dict(response), []))
        launcher = runtime_relay.Launcher(caller, sessions=lambda: ["s"])
        launcher._kill = lambda session, pid: killed.append(pid)
        launcher.open = lambda argument, handles: Ended()
        return launcher, killed

    def test_a_codex_launch_without_a_well_formed_answer_is_refused_and_its_child_ended(self):
        command = ["codex", "app-server"]
        for runtime in (None, {}, {"kind": "stock"}, {"kind": "own", "version": PIN}, {"kind": "bundled"},
                        {"kind": "bundled", "version": ""}, {"kind": "override", "version": 3}, "own"):
            with self.subTest(runtime=runtime):
                launcher, killed = self.launcher({"ok": True, "relay": "fd:passed", "pid": 8, "runtime": runtime})
                with self.assertRaisesRegex(RpcError, "did not say which Codex"):
                    launcher(command, env={}, cwd="/w", text=True)
                self.assertEqual(killed, [8])
        launcher, killed = self.launcher({"ok": True, "relay": "fd:passed", "pid": 8,
                                          "runtime": {"kind": "override", "version": PIN}})
        process = launcher(command, env={}, cwd="/w", text=True)
        self.assertEqual(process.runtime, {"kind": "override", "version": PIN})
        self.assertEqual(killed, [])

    def test_the_version_check_follows_the_answer_not_the_runtime_this_process_resolves(self):
        controller = SimpleNamespace()
        with mock.patch.object(agent_runtime, "is_agent_runtime", lambda path, environ=None: True), \
                mock.patch("angelo_agent.cli_runtime.codex_version", lambda initialized: (9, 9, 9)), \
                mock.patch("angelo_agent.cli_runtime.note_newer", lambda name, version: None):
            # The lead launched the person's own Codex: never held to the pin, whatever this process would resolve.
            codex_connect.initialize(relayed_rpc({"kind": "own", "version": None}), controller, "/worker/codex")
            # The lead launched its agent runtime: held to the version the lead resolved, files checked there.
            with mock.patch.object(agent_runtime, "check_files", side_effect=AssertionError("checked here")):
                codex_connect.initialize(relayed_rpc({"kind": "bundled", "version": "9.9.9"}), controller, None)
                with self.assertRaisesRegex(RpcError, "reports Codex 9.9.9"):
                    codex_connect.initialize(relayed_rpc({"kind": "bundled", "version": PIN}), controller, None)
        with mock.patch.object(agent_runtime, "is_agent_runtime", lambda path, environ=None: False), \
                mock.patch("angelo_agent.cli_runtime.codex_version", lambda initialized: (9, 9, 9)):
            # This process would resolve no agent runtime; the lead's answer says it is one: the pin holds.
            with self.assertRaisesRegex(RpcError, "reports Codex 9.9.9"):
                codex_connect.initialize(relayed_rpc({"kind": "override", "version": PIN}), controller, "/w/codex")

    def test_the_cache_affinity_probe_follows_the_answer(self):
        for flag, lead, probed in ((True, {"kind": "own", "version": None}, False),
                                   (False, {"kind": "bundled", "version": PIN}, True), (True, None, True)):
            with self.subTest(flag=flag, lead=lead):
                rpc = relayed_rpc(lead) if lead is not None else SimpleNamespace(process=None)
                runtime = codex_runtime.Runtime(("k",), "c", lambda on_event, on_request: rpc, agent_runtime=flag)
                checks = []
                with mock.patch.object(codex_runtime.cache_affinity, "check",
                                       lambda process: checks.append(process) or codex_runtime.cache_affinity.SUPPORTED):
                    runtime.prepare(lambda: {"userAgent": "angelo_blender/" + PIN})
                self.assertEqual(bool(checks), probed)
                self.assertEqual(runtime.cache_affinity, probed)

    def test_the_receipt_names_the_codex_the_lead_launched(self):
        routes = []
        metrics = SimpleNamespace(set_route=lambda **route: routes.append(route))
        host = SimpleNamespace(_request_metrics=metrics)
        fence = SimpleNamespace(matches=lambda owner: True)
        provider = SimpleNamespace(controller=SimpleNamespace(host=host, request_fence=fence), _runtime_kind="bundled",
                                   rpc=relayed_rpc({"kind": "own", "version": None}))
        codex_connect.stamp_route(provider)
        self.assertEqual(routes[-1]["agent_runtime"], "own")
        provider.rpc = SimpleNamespace(process=None)
        codex_connect.stamp_route(provider)
        self.assertEqual(routes[-1]["agent_runtime"], "bundled")   # Not a worker chat: its own resolution.

    def test_the_lead_runtime_never_reaches_the_worker_by_its_environment(self):
        environ = {"PATH": "/bin", "ANGELO_AGENT_RUNTIME": "/lead/codex", "ANGELO_CACHE_KEY_SECRET": "s" * 32}
        kept = worker_process.worker_environment(environ, "/p", platform="darwin")
        self.assertFalse({"ANGELO_AGENT_RUNTIME", "ANGELO_CACHE_KEY_SECRET"} & set(kept))


if __name__ == "__main__":
    unittest.main()
