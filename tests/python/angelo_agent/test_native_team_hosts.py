# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Under a Teams gateway profile the app contacts only the published host list.

The profile: a managed policy (``ANGELO_POLICY_FILE``) that refuses personal connections and adds the test's root
as ``ExtraCACertificates``, and a signed-in team account whose organization uses the gateway. The published list
(doc/angelo/enterprise_policy.rst, "Network"): ``gateway.angelo.studio``, ``console.angelo.studio``,
``angelo.studio``, ``downloads.angelo.studio``, ``assets.toonlab.io``, ``cdn.toonlab.io`` and Angelo's R2 download
host (any ``*.r2.cloudflarestorage.com``). The one documented exception: with the organization's re-host setting
off, a generation's result downloads from the provider's own CDN, named by the gateway's poll answer.

``team_host_fence`` fences the whole process: a listed name resolves to a loopback HTTPS stand-in whose certificate
only the policy's root vouches for; any other name is refused and recorded, and so is any connect to an address no
listed name resolved to (a literal IP, a proxy). The real code paths run in-process against it:

* startup team traffic (restore and refresh, providers, usage, usage reports, grant key): the console only;
* the agent turn's team Claude Code and Codex children: fenced to the gateway (``team_network``); building the Codex
  launch reads the gateway's ``/health``. The real runtimes are checked by ``team_network_probe.py``;
* a team generation (submit, poll, result): the gateway, then the R2 host, or the provider CDN when not re-hosted;
* the team library's download (console, then its presigned R2 link) and the Angelo Library (ToonLab hosts);
* the update check, the installer download and the crash report: angelo.studio, downloads.angelo.studio, the console;
* saved personal keys at startup under the policy: nothing at all.

Every transport (``team_http``, ``app_http``, ``studio_http``, ``api_connections``, ``team_library_transfer``)
reaches the stand-in only through the policy's root, and the fence's own tests prove each one is seen when it
reaches for a host off the list. No real service is contacted.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import gzip
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import crash_fixtures  # noqa: E402
import update_fixtures  # noqa: E402
from fake_team_console import ORG_ID, USER_ID, FakeConsole, FakeVault  # noqa: E402
from fake_team_library import FakeLibrary  # noqa: E402
from team_host_fence import R2_SUFFIX, Fence, StandIn, certificate, forward, published  # noqa: E402
from team_library_fixtures import glb, png  # noqa: E402
from test_native_saved_keys import KEY, FakeBackend  # noqa: E402
from test_native_saved_keys_ui import FakeUI  # noqa: E402
from test_native_team_usage_reports import receipt  # noqa: E402
from angelo_agent import (api_connections, app_http, crash_log, crash_report, crash_upload,  # noqa: E402
                          gateway_capabilities, library_catalog, library_fetch, managed_policy, product_env,
                          saved_keys_ui, studio_http, team_connections, team_endpoints, team_http,
                          team_library_transfer, team_lock, team_network, update_check, update_download)
from angelo_agent.api_connections import ConnectionCheckError, probe_request  # noqa: E402
from angelo_agent.generative import bind_team_generation  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.provider_registry import HTTP_MODES, spec_for  # noqa: E402
from angelo_agent.runtime_launch import child_environment, codex_command  # noqa: E402
from angelo_agent.saved_keys import account_name, profile_id  # noqa: E402
from angelo_agent.studio_generation import GenerationError, generate  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_errors import NotSent  # noqa: E402
from angelo_agent.team_library_client import TeamLibraryClient  # noqa: E402
from angelo_agent.team_library_transfer import TransferError  # noqa: E402
from angelo_agent.team_usage_buffer import UsageBuffer  # noqa: E402

CONSOLE_HOST, GATEWAY_HOST = "console.angelo.studio", "gateway.angelo.studio"
CONSOLE, GATEWAY = "https://" + CONSOLE_HOST, "https://" + GATEWAY_HOST
CRASH_PATH = "/api/v1/crash-reports"
INSTALLER_URL = "https://downloads.angelo.studio/1.1.0/Angelo-1.1.0-macos-arm64.dmg"
R2_HOST = "0f1e2d3c4b5a69788796a5b4c3d2e1f0" + R2_SUFFIX   # An R2 account endpoint (path-style bucket links).
# A provider's own output host, as a Tripo poll answer names it when the organization does not re-host results. It is
# not on the published list, so the fence refuses it like any other.
PROVIDER_CDN = "tripo-data.rg1.data.tripo3d.com"
TASK = "task-7f3a90c2"
GRANT = "grant-team-hosts"
GRANT_KEY = "agk_" + "k" * 43
JSON = {"Content-Type": "application/json"}
URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"',;]+")
# The variables that point Angelo at another console, gateway, feed, crash endpoint, library or policy: the test
# clears them so every flow uses its production address (product_env's spellings; the library reads its own name).
ENDPOINT_VARIABLES = frozenset(name for suffix in (team_endpoints.CONSOLE_VARIABLE, team_endpoints.GATEWAY_VARIABLE,
                                                   update_check.FEED_VARIABLE, crash_upload.URL_VARIABLE,
                                                   managed_policy.TEST_FILE_VARIABLE)
                               for name in product_env.names(suffix)) | {library_fetch.URL_VARIABLE}


def wait_for(predicate, timeout=30.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(what + " not reached")


class TeamHostsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory(prefix="angelo-team-hosts-")
        cls.addClassCleanup(folder.cleanup)
        cls.cert, key = certificate(folder.name)
        cls.policy_file = Path(folder.name, "policy.json")
        cls.policy_file.write_text(json.dumps({"AllowPersonalConnections": False, "ExtraCACertificates": cls.cert}))
        cls.stand_in = StandIn(cls.cert, key)
        cls.addClassCleanup(cls.stand_in.close)

    def setUp(self):
        environ = {name: value for name, value in os.environ.items()
                   if not name.lower().endswith("_proxy") and name.upper() not in ENDPOINT_VARIABLES}
        environ[product_env.PREFIX + managed_policy.TEST_FILE_VARIABLE] = str(self.policy_file)
        environment = patch.dict(os.environ, environ, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        managed_policy.reset()
        self.addCleanup(managed_policy.reset)
        policy = managed_policy.current()
        self.assertEqual((policy.allow_personal_connections, policy.extra_ca_certificates, policy.problems),
                         (False, self.cert, ()), "the Teams gateway profile's machine policy")
        gateway_capabilities.reset()
        self.addCleanup(gateway_capabilities.reset)
        folder = tempfile.TemporaryDirectory(prefix="angelo-team-hosts-")
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.console = FakeConsole()
        self.addCleanup(self.console.close)
        # The team library's presigned links are on Angelo's R2 host (fake_team_library signs them on this origin).
        self.console.library = FakeLibrary("https://" + R2_HOST, user_id=USER_ID, org_id=ORG_ID)
        self.files = {}   # (host, path) -> (content type, bytes): static answers of the listed hosts.
        self.crash_reports, self.submits = [], []
        self.result_url = self.preview_url = ""
        self.stand_in.reset()
        self.stand_in.answer = self.answer
        self.fence = Fence(self.stand_in.port)
        self.fence.install(self)

    # The stand-in's answers ----------------------------------------------------------------------------------------

    def answer(self, request):
        console = self.console.server.server_address[1]
        if request.host == CONSOLE_HOST:
            if request.method == "POST" and request.path == CRASH_PATH:
                report = json.loads(request.body)
                self.crash_reports.append(report)
                return 202, JSON, json.dumps({"id": report["report_id"]}).encode()
            return forward(console, request)
        if request.host == GATEWAY_HOST:
            return self.gateway(request)
        if request.host.endswith(R2_SUFFIX) and request.path.startswith("/api/dev-storage/"):
            return forward(console, request)
        stored = self.files.get((request.host, request.path))
        if stored is None:
            return 404, {}, b""
        return 200, {"Content-Type": stored[0]}, stored[1]

    def gateway(self, request):
        if request.method == "GET" and request.path == "/health":
            return 200, JSON, json.dumps({"requestEncodings": ["gzip", "zstd"]}).encode()
        token = {k.lower(): v for k, v in request.headers.items()}.get("authorization", "").removeprefix("Bearer ")
        with self.console.lock:
            signed_in = token in self.console.access
        if not signed_in:
            return 401, JSON, json.dumps({"error": {"code": "UNAUTHENTICATED", "message": "Sign in again."}}).encode()
        if request.method == "POST" and request.path.startswith("/v1/tripo/"):
            self.submits.append(request.path)
            return 200, JSON, json.dumps({"code": 0, "data": {"task_id": TASK}}).encode()
        if request.method == "GET" and request.path == "/v1/tripo/v3/tasks/" + TASK:
            output = {"model_url": self.result_url, "rendered_image_url": self.preview_url}
            return 200, JSON, json.dumps({"code": 0, "data": {"status": "success", "progress": 100,
                                                               "output": output}}).encode()
        return 404, JSON, json.dumps({"code": 404, "message": "Not found."}).encode()

    # Helpers -------------------------------------------------------------------------------------------------------

    def mark(self):
        """What follows is the flow under test: forget what was contacted before."""
        self.fence.reset()
        self.stand_in.reset()

    def assert_contacted(self, expected, refused=()):
        """The flow looked up exactly ``expected`` (all listed) and the ``refused`` names; nothing sidestepped."""
        expected, refused = set(expected), set(refused)
        names = self.fence.names()
        self.assertEqual(self.fence.blocked_connects(), [], "connects to addresses no listed name resolved to")
        self.assertEqual(set(self.fence.refusals()), refused, "names off the published list")
        self.assertEqual({name for name in names - refused if not published(name)}, set())
        self.assertEqual(names, expected | refused)
        self.assertEqual(self.stand_in.errors, [])
        self.assertEqual(self.stand_in.hosts(), expected, "each listed host answered over TLS")

    def console_paths(self):
        with self.console.lock:
            return [(method, path) for method, path, *_rest in self.console.requests]

    def console_saw(self, method, pattern):
        return sum(1 for m, path in self.console_paths() if m == method and re.fullmatch(pattern, path))

    def stop(self, account):
        """Close ``account`` and wait for every thread it started, which open files in ``self.work``."""
        account.close()
        self.assertTrue(account.join(10), "a team account thread outlived its test")

    def restored_account(self):
        """The team account as Angelo starts it: a stored sign-in restored, its token refreshed, providers read."""
        with self.console.lock:
            self.console.grants[GRANT] = {"org": ORG_ID, "device": "Test computer",
                                          "scopes": {"api", "generate", "offline_access"}}
            refresh = self.console._issue(GRANT)["refresh_token"]
        account = TeamAccount(CONSOLE, vault=FakeVault(refresh), process_lock=team_lock.factory(str(self.work), 5),
                              usage_buffer=UsageBuffer(str(self.work)))
        self.addCleanup(self.stop, account)
        self.assertTrue(account.restore())
        wait_for(lambda: account.view().signed_in and account.view().providers is not None, what="the restored sign-in")
        return account


class StartupAndAgentTests(TeamHostsCase):
    def test_startup_team_traffic_reaches_only_the_console(self):
        account = self.restored_account()
        self.assertTrue(account.count_agent_task(receipt()))
        account.tick(idle=True)   # Due at start: usage, the usage report and the coding agents' grant key.
        wait_for(lambda: self.console_saw("GET", r"/api/orgs/[^/]+/usage/people/[^/]+")
                 and self.console_saw("POST", r"/api/orgs/[^/]+/usage/app-reports")
                 and self.console_saw("POST", "/api/oauth/grant-key"), what="startup's console reads")
        wait_for(account.grant_key, what="the grant key")   # Its task runs after the report's: both have landed.
        self.assertTrue(account.count_agent_task(receipt()))
        account.close()   # Quit: the exit report goes with the access token in hand.
        wait_for(lambda: self.console_saw("POST", r"/api/orgs/[^/]+/usage/app-reports") >= 2, what="the exit report")
        self.assertEqual(self.console_saw("POST", "/api/oauth/token"), 1, "one refresh at restore")
        self.assertEqual(self.console_saw("GET", r"/api/orgs/[^/]+/providers/available"), 1)
        self.assert_contacted({CONSOLE_HOST})

    def test_team_runtimes_reach_only_the_gateway(self):
        self.mark()
        claude = child_environment("claude_team", GRANT_KEY)
        identity = team_connections.connection_id(CONSOLE, ORG_ID, USER_ID, "codex_team")
        command, codex = codex_command("codex", "codex_team", str(self.work), reuse_login=False,
                                       connection_id=identity, api_key=GRANT_KEY)
        spellings = {spelling for name in team_network.PROXY_VARIABLES for spelling in (name, name.lower())}
        for runtime, env in (("claude", claude), ("codex", codex)):
            with self.subTest(runtime=runtime):
                proxies = {k: v for k, v in env.items() if k.upper() in team_network.PROXY_VARIABLES}
                self.assertEqual(set(proxies), spellings)
                self.assertEqual(set(proxies.values()), {team_network.REFUSING_PROXY})
                self.assertEqual({k: v for k, v in env.items() if k.upper() == "NO_PROXY"},
                                 {"NO_PROXY": GATEWAY_HOST, "no_proxy": GATEWAY_HOST})
                # Every address the app gives the child is the gateway or this computer (the refusing proxy).
                added = [v for k, v in env.items() if os.environ.get(k) != v]
                self.assert_gateway_or_loopback(added)
        self.assertEqual(claude["ANTHROPIC_BASE_URL"], GATEWAY + spec_for("claude_team")["gateway_path"])
        self.assertEqual(claude["NODE_EXTRA_CA_CERTS"], self.cert, "Claude Code trusts the policy's roots")
        pairs = dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])
        self.assertEqual(json.loads(pairs["model_providers.codex_team.base_url"]),
                         GATEWAY + spec_for("codex_team")["gateway_path"])
        # The gateway's /health was read over TLS through the policy's root: it named zstd.
        self.assertEqual(json.loads(pairs["model_providers.codex_team.capabilities.request_compression"]), "zstd")
        self.assert_gateway_or_loopback(list(pairs.values()))
        self.assert_contacted({GATEWAY_HOST})

    def assert_gateway_or_loopback(self, values):
        for value in values:
            for url in URL.findall(value):
                host = urlsplit(url).hostname or ""
                with self.subTest(url=url):
                    self.assertTrue(host == GATEWAY_HOST or team_endpoints.is_loopback(host), url)


class GenerationTests(TeamHostsCase):
    def run_generation(self, result_host):
        account = self.restored_account()
        view = account.view()
        record = next(r for r in team_connections.records(view, CONSOLE).values() if r["mode"] == "tripo_team")
        gateway = team_endpoints.gateway_origin().url
        binding = bind_team_generation(record, team_connections.health(view, record), gateway, account,
                                       "|".join((CONSOLE, view.org_id, view.user_id)))
        self.model, self.preview = glb(), png()
        folder = "/results/%s/" % TASK
        self.result_url = "https://%s%smodel.glb?signature=fixture" % (result_host, folder)
        self.preview_url = "https://%s%spreview.png?signature=fixture" % (result_host, folder)
        self.files[(result_host, folder + "model.glb")] = ("model/gltf-binary", self.model)
        self.files[(result_host, folder + "preview.png")] = ("image/png", self.preview)
        directory = self.work / "generation"
        directory.mkdir()
        self.mark()
        return directory, lambda: generate(binding, binding.model_ids[0], "A weathered wooden crate",
                                           {"texture": True, "polycount": 10000}, [], directory, [].append,
                                           threading.Event(), poll_interval=0)

    def test_a_result_re_hosted_on_r2_needs_no_other_host(self):
        directory, run = self.run_generation(R2_HOST)
        result = run()
        self.assertEqual((result["status"], result["files"]), ("ready", ["model.glb"]))
        self.assertEqual((directory / "model.glb").read_bytes(), self.model)
        self.assertEqual((directory / result["thumbnail"]).read_bytes(), self.preview)
        self.assertEqual(len(self.submits), 1)
        self.assert_contacted({GATEWAY_HOST, R2_HOST})

    def test_without_re_hosting_only_the_providers_cdn_is_off_the_list(self):
        directory, run = self.run_generation(PROVIDER_CDN)
        with self.assertRaises(GenerationError):   # The fence refuses the CDN; a real network would download it.
            run()
        self.assertEqual(len(self.submits), 1, "submitted and polled through the gateway")
        self.assertFalse((directory / "model.glb").exists())
        self.assert_contacted({GATEWAY_HOST}, refused={PROVIDER_CDN})   # The documented exception, and only it.


class LibraryTests(TeamHostsCase):
    def test_a_team_library_download_uses_the_console_and_its_presigned_r2_link(self):
        account = self.restored_account()
        data = glb()
        digest = hashlib.sha256(data).hexdigest()
        item = self.console.library.add_item("Crate", data, "model/gltf-binary", sha256=digest)
        self.mark()
        url = TeamLibraryClient(account).download_link(item)
        self.assertEqual(urlsplit(url).hostname, R2_HOST)
        target = self.work / "crate.glb"
        self.assertEqual(team_library_transfer.download(url, target, limit=1 << 20, expected=len(data), sha256=digest),
                         len(data))
        self.assertEqual(target.read_bytes(), data)
        self.assert_contacted({CONSOLE_HOST, R2_HOST})

    def test_the_angelo_library_uses_only_toonlab_hosts(self):
        latest = urlsplit(library_fetch.LATEST_URL)
        packed = gzip.compress(json.dumps({"schema": library_catalog.SCHEMA, "version": "v1", "items": []}).encode())
        index = "/catalog/v1/index-v1.json.gz"
        self.files[(latest.hostname, index)] = ("application/gzip", packed)
        self.files[(latest.hostname, latest.path)] = ("application/json", json.dumps({
            "schema": library_catalog.SCHEMA, "version": "v1", "url": "https://%s%s" % (latest.hostname, index),
            "size": len(packed), "sha256": hashlib.sha256(packed).hexdigest()}).encode())
        model, thumb = glb(), png()
        self.files[("cdn.toonlab.io", "/items/crate/crate.glb")] = ("model/gltf-binary", model)
        self.files[("cdn.toonlab.io", "/items/crate/thumb.png")] = ("image/png", thumb)
        file = {"role": "model", "name": "crate.glb", "url": "https://cdn.toonlab.io/items/crate/crate.glb",
                "size": len(model), "sha256": hashlib.sha256(model).hexdigest()}
        root = self.work / "library"
        self.mark()
        self.assertEqual(library_fetch.refresh(root / "index")["version"], "v1")
        self.assertEqual(library_fetch.fetch_files(root, {"id": "crate"}, [file])["crate.glb"].read_bytes(), model)
        self.assertEqual(library_fetch.thumbnail(root, "https://cdn.toonlab.io/items/crate/thumb.png").read_bytes(),
                         thumb)
        self.assert_contacted({latest.hostname, "cdn.toonlab.io"})


class UpdateAndCrashTests(TeamHostsCase):
    def publish_feed(self):
        seed, keys = update_fixtures.new_key()
        self.installer = os.urandom(96 * 1024)
        document = update_fixtures.payload("1.1.0", services={"crash_reports": CONSOLE + CRASH_PATH},
                                           expires="2036-01-01T00:00:00Z")
        document["latest"]["downloads"] = {"macos-arm64": {"url": INSTALLER_URL, "size": len(self.installer),
                                                           "sha256": hashlib.sha256(self.installer).hexdigest()}}
        feed = urlsplit(update_check.FEED_URL)
        self.files[(feed.hostname, feed.path)] = ("application/json", update_fixtures.envelope(document, seed))
        self.files[(urlsplit(INSTALLER_URL).hostname, urlsplit(INSTALLER_URL).path)] = ("application/octet-stream",
                                                                                       self.installer)
        return update_check.Checker(str(self.work), keys=keys, current="1.0.0", platform="macos-arm64")

    def test_the_update_check_and_installer_use_angelo_studio_hosts(self):
        checker = self.publish_feed()
        self.mark()
        result = checker.check(manual=True)
        self.assertEqual(result.kind, update_check.UPDATE, result.detail)
        download = result.offer.download
        self.assertEqual(download.url, INSTALLER_URL)
        target = update_download.fetch(update_download.Expected(download.url, download.sha256, download.size),
                                       self.work / "update" / "Angelo.dmg")
        self.assertEqual(Path(target).read_bytes(), self.installer)
        self.assert_contacted({urlsplit(update_check.FEED_URL).hostname, urlsplit(INSTALLER_URL).hostname})

    def test_a_crash_report_goes_to_the_console(self):
        checker = self.publish_feed()
        self.assertEqual(checker.check(manual=True).kind, update_check.UPDATE)
        self.mark()
        url = crash_upload.endpoint(checker.crash_report_url())
        self.assertEqual(url, CONSOLE + CRASH_PATH)
        document = crash_report.build_document(
            crash_log.parse(crash_fixtures.MAC, crash_fixtures.MAC_ROOTS),
            crash_report.system_info(None, "Darwin", "arm64", "25.3.0"),
            crash_report.app_info("5.2.2", "311bbe4efa04", "2026-10-09"), 1_760_000_000_123_456_789,
            datetime.now(timezone.utc), report_id="r-team-hosts")
        outcome = crash_upload.send(url, crash_report.body(document), "r-team-hosts")
        self.assertEqual((outcome.kind, outcome.receipt), (crash_upload.SENT, "r-team-hosts"))
        self.assertEqual([r["report_id"] for r in self.crash_reports], ["r-team-hosts"])
        self.assert_contacted({CONSOLE_HOST})


class SavedKeyTests(TeamHostsCase):
    def saved_keys(self):
        """Every HTTP personal key connection, stored before the organization's policy arrived, with its key saved."""
        managed_policy.reset(Policy())
        ui = FakeUI(self.work / "accounts")
        ui._api_connections = api_connections.ApiConnections()   # The real checks, as ui.py owns them.
        records = {}
        for mode in sorted(HTTP_MODES):
            spec = spec_for(mode)
            record = ui.store.add(spec["provider"], "API_KEY", spec["name"], group=spec["group"])
            if record["mode"] == mode:
                records[record["id"]] = record
        managed_policy.reset()   # The machine policy again, as ANGELO_POLICY_FILE says.
        self.addCleanup(saved_keys_ui.close)
        backend = FakeBackend()
        profile = profile_id(ui.store.directory)
        for identity in records:
            backend.items[account_name(identity, profile)] = KEY
        return ui, records, backend

    def start(self, ui, records, backend):
        saved_keys_ui.start(ui, background=False, platform="darwin", backend=backend)

        def settled():
            saved_keys_ui.pump(ui)
            ui._api_connections.pump()
            results = ui._api_connections.results
            return not saved_keys_ui._vault._pending and all(
                identity in results and results[identity].state != "checking" for identity in records)
        wait_for(settled, what="the saved keys' startup checks")
        self.assertEqual(sorted(ui.replaced), sorted(records), "each saved key loaded through the typed-key path")
        return ui._api_connections.results

    def test_saved_personal_keys_contact_no_provider_under_the_policy(self):
        ui, records, backend = self.saved_keys()
        self.assertGreater(len(records), 1)
        self.mark()
        results = self.start(ui, records, backend)
        refusal = str(managed_policy.personal_refusal(managed_policy.current(), False))
        for identity, record in records.items():
            with self.subTest(mode=record["mode"]):
                self.assertEqual((results[identity].state, str(results[identity].message)), ("failed", refusal))
        self.assert_contacted(set())

    def test_the_fence_sees_the_checks_a_permissive_policy_would_send(self):
        ui, records, backend = self.saved_keys()
        managed_policy.reset(replace(managed_policy.current(), allow_personal_connections=True))
        probes = {r["mode"]: probe_request(r["mode"], KEY) for r in records.values()}
        expected = {urlsplit(request.full_url).hostname for request in probes.values() if request is not None}
        self.assertTrue(expected)
        self.mark()
        results = self.start(ui, records, backend)
        for identity, record in records.items():
            with self.subTest(mode=record["mode"]):
                self.assertEqual(results[identity].state, "failed" if probes[record["mode"]] else "configured")
        self.assert_contacted(set(), refused=expected)


class TransportTests(TeamHostsCase):
    """The five transports trust the stand-in only through the policy's root, and none sidesteps the fence."""

    def attempts(self, url):
        def stream():
            status, _headers, body = app_http.stream(url)
            body.close()
            return status
        return {
            "team_http": (lambda: team_http.http_transport("GET", url)[0], NotSent),
            "app_http.request": (lambda: app_http.request("GET", url).status, app_http.NetworkError),
            "app_http.stream": (stream, app_http.NetworkError),
            "studio_http": (lambda: json.loads(studio_http.request_bytes(url)), GenerationError),
            "team_library_transfer": (lambda: team_library_transfer.fetch(url, limit=4096), TransferError),
            "api_connections": (lambda: api_connections.read_probe(urllib.request.Request(url)), ConnectionCheckError),
        }

    def test_every_transport_honours_the_policys_extra_root_and_only_it(self):
        for name, (attempt, _error) in self.attempts(GATEWAY + "/health").items():
            with self.subTest(name, root="policy"):
                self.mark()
                self.assertTrue(attempt(), name)
                self.assert_contacted({GATEWAY_HOST})
        managed_policy.reset(replace(managed_policy.current(), extra_ca_certificates=""))
        for name, (attempt, error) in self.attempts(GATEWAY + "/health").items():
            with self.subTest(name, root="none"):
                self.mark()
                with self.assertRaises(error) as caught:
                    attempt()
                if isinstance(caught.exception, app_http.NetworkError):
                    self.assertEqual(caught.exception.kind, app_http.TLS)
                self.assertEqual(self.fence.names(), {GATEWAY_HOST})
                self.assertEqual(self.stand_in.hosts(), set(), "no request got past the handshake")

    def test_a_host_off_the_list_is_refused_and_recorded_on_every_way_out(self):
        off = "telemetry.example.com"

        def raw_connect():
            with socket.socket() as sock:
                sock.settimeout(5)
                sock.connect((off, 443))
        attempts = dict(self.attempts("https://%s/x" % off),
                        create_connection=(lambda: socket.create_connection((off, 443), 5), OSError),
                        connect_by_name=(raw_connect, OSError))
        for name, (attempt, error) in attempts.items():
            with self.subTest(name):
                self.mark()
                with self.assertRaises(error):
                    attempt()
                self.assert_contacted(set(), refused={off})

    def test_a_literal_address_or_an_environment_proxy_is_fenced_too(self):
        address = "192.0.2.10"   # TEST-NET-1: never routed, and the fence refuses the connect before it is tried.
        self.mark()
        with self.assertRaises(app_http.NetworkError):
            app_http.request("GET", "https://%s/" % address)
        with self.assertRaises(NotSent):
            team_http.http_transport("GET", "https://%s/" % address)
        self.assertEqual(self.fence.blocked_connects(), [address + ":443"] * 2)
        self.assertEqual(self.fence.names(), set())
        self.mark()
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.example.net:3128"}):
            with self.assertRaises(app_http.NetworkError):   # app_http follows the proxy (no credential is sent).
                app_http.request("GET", CONSOLE + "/api/anything")
        self.assert_contacted(set(), refused={"proxy.example.net"})


if __name__ == "__main__":
    unittest.main()
