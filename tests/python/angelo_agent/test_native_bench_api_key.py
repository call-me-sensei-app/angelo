# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A gate's API key for the Windows bench user (``bench_api_key.py``): read from Secret Manager, saved in the item
Angelo loads, removed at the request's end, loaded only for a selection that bills its account, refused with the
real cause when anything is missing, and never written anywhere (break-it review of 30b631d5742, 2026-11-13).

No network and no OS key store: the metadata server, Secret Manager and the store are fakes.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import base64
import contextlib
import functools
import http.client
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_api_key as gate_key  # noqa: E402
import final_runs as runs  # noqa: E402
import final_runs_plan as plan  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent.provider_registry import CODING  # noqa: E402
from angelo_agent.saved_keys import account_name, profile_id  # noqa: E402
from test_native_final_runs import config  # noqa: E402

KEY = "AIza-test-only-not-a-real-key-7f3c9e"   # Made up; the assertions look for it everywhere.
TOKEN = "ya29.test-only-token"
SECRET = gate_key.secret_name("gemini")
CLOUD = "https://www.googleapis.com/auth/cloud-platform"
STORAGE = "https://www.googleapis.com/auth/devstorage.read_write"


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def google_error(status, reason=None):
    detail = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}] if reason else []
    return json.dumps({"error": {"code": 0, "status": status, "message": "quoted request " + TOKEN,
                                 "details": detail}}).encode()


def server(status=200, payload=KEY, scopes=(STORAGE, CLOUD), body=b"", metadata=True, broken=None):
    """A fake opener for both the metadata server and Secret Manager."""
    def call(request, timeout):
        url = request.full_url
        if url.startswith(gate_key.METADATA):
            if not metadata:
                raise urllib.error.URLError("no route")
            assert request.headers.get("Metadata-flavor") == "Google"
            if url.endswith("project-id"):
                return Reply(b"proj-1")
            return Reply("\n".join(scopes).encode() if url.endswith("scopes")
                         else json.dumps({"access_token": TOKEN}).encode())
        assert url == gate_key.ACCESS % ("proj-1", SECRET), url
        assert request.headers.get("Authorization") == "Bearer " + TOKEN
        if broken:
            raise broken
        if status != 200:
            raise urllib.error.HTTPError(url, status, "refused", {}, io.BytesIO(body))
        return Reply(json.dumps({"payload": {"data": base64.b64encode(payload.encode()).decode()}}).encode())
    return call


def fetch(**fake):
    opener = server(**fake)
    return gate_key.fetch_secret(SECRET, opener=opener, metadata=opener)


class Store:
    """A fake key store following key_store's contract; ``fail`` makes a write raise with the key in its text."""

    def __init__(self, readback=True, fail=False):
        self.items, self.readback, self.fail = {}, readback, fail

    def write(self, account, secret, label, interactive=False):
        if self.fail:
            raise RuntimeError("store failed for " + secret)   # A careless error text: must never surface.
        self.items[account] = (secret, label)
        return self.readback

    def delete(self, account, interactive=False):
        return self.items.pop(account, None) is not None


class FetchTests(unittest.TestCase):
    def refused(self, phrase, **fake):
        with self.assertRaises(gate_key.Refusal) as raised:
            fetch(**fake)
        message = str(raised.exception)
        self.assertIn(phrase, message)
        self.assertNotIn(TOKEN, message)
        self.assertNotIn(KEY, message)
        return message

    def test_reads_the_latest_version_with_the_vms_own_token_and_strips_a_newline(self):
        self.assertEqual(fetch(payload=KEY + "\n"), KEY)
        self.assertEqual(fetch(payload=KEY + "\r\n"), KEY)

    def test_each_cause_is_named_for_what_it_is(self):
        cases = [
            ("the VM's access scopes lack cloud-platform", dict(scopes=(STORAGE,))),
            ("the VM's access scopes lack cloud-platform",
             dict(status=403, body=google_error("PERMISSION_DENIED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"))),
            ("API is turned off", dict(status=403, body=google_error("PERMISSION_DENIED", "SERVICE_DISABLED"))),
            ("roles/secretmanager.secretAccessor", dict(status=403, body=google_error("PERMISSION_DENIED",
                                                                                     "IAM_PERMISSION_DENIED"))),
            ("roles/secretmanager.secretAccessor", dict(status=403, body=b"not json")),
            ("does not exist", dict(status=404, body=google_error("NOT_FOUND"))),
            ("disabled or destroyed", dict(status=400, body=google_error("FAILED_PRECONDITION"))),
            ("HTTP 500", dict(status=500, body=google_error("INTERNAL"))),
            ("HTTP 401", dict(status=401, body=b'{"error": "invalid_token"}')),   # An OAuth-style body: no traceback.
            ("only on the gate VM", dict(metadata=False)),
            ("could not be read (IncompleteRead)", dict(broken=http.client.IncompleteRead(b""))),
        ]
        for phrase, fake in cases:
            with self.subTest(phrase=phrase, fake=fake):
                message = self.refused(phrase, **fake)
                if "metadata" not in fake:
                    self.assertIn(SECRET, message)
                    self.assertIn("proj-1", message)

    def test_a_value_that_is_not_one_key_is_refused_without_echoing_it(self):
        for value, phrase in (("﻿" + KEY, "non-ASCII"), ('"%s"' % KEY, "quotes"), ("GEMINI_API_KEY=" + KEY, "NAME=value"),
                              (KEY + " " + KEY, "spaces"), ("  \n", "empty")):
            with self.subTest(phrase=phrase):
                self.refused(phrase, payload=value)

    def test_padded_base64_is_one_key(self):
        self.assertEqual(fetch(payload="dGVzdA=="), "dGVzdA==")

    def test_a_name_outside_the_bench_prefix_is_refused_and_never_echoed(self):
        for name in ("a/b", "x?y", "", "a:b", "angelo-bench-" + "k" * 243, "gemini-key", KEY):
            with self.subTest(name=name), self.assertRaises(gate_key.Refusal) as raised:
                gate_key.fetch_secret(name, opener=server(), metadata=server())
            self.assertNotIn(name or "\0", str(raised.exception))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(gate_key.main(["check", "--secret", KEY]), 2)
        self.assertNotIn(KEY, out.getvalue() + err.getvalue())

    def test_the_metadata_server_is_never_reached_through_a_proxy(self):
        # An empty ProxyHandler adds no proxy method, and keeps build_opener from adding the environment's one.
        def proxies(opener):
            return [h.proxies for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
        self.assertEqual(proxies(gate_key.DIRECT.__self__), [])
        with mock.patch.dict("os.environ", {"http_proxy": "http://proxy.invalid:1"}):
            self.assertTrue(proxies(urllib.request.build_opener()), "a default opener would take the environment's")
            self.assertEqual(proxies(urllib.request.build_opener(urllib.request.ProxyHandler({}))), [])


class LoadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "gemini-connections"

    def test_load_saves_in_the_item_angelo_reads_and_unload_removes_it(self):
        store = Store()
        identity = gate_key.load(self.folder, "gemini", SECRET, fetch=lambda secret: KEY, backend=store)
        record = ConnectionStore(self.folder).records[identity]
        self.assertEqual((record["mode"], record["group"], record["method"]), ("gemini_api", CODING, "API_KEY"))
        self.assertEqual(store.items, {account_name(identity, profile_id(self.folder)): (KEY, "Gemini (bench)")})
        self.assertEqual(gate_key.load(self.folder, "gemini", SECRET, fetch=lambda s: KEY, backend=store), identity)
        self.assertTrue(gate_key.unload(self.folder, "gemini", backend=store))
        self.assertEqual(store.items, {})
        self.assertFalse(gate_key.unload(self.root / "never-made", "gemini", backend=store))

    def test_a_folder_holding_anything_else_or_unreadable_is_refused(self):
        store = ConnectionStore(self.folder)
        store.add("OPENAI", "SUBSCRIPTION", name="ChatGPT")
        with self.assertRaises(gate_key.Refusal):
            gate_key.load(self.folder, "gemini", SECRET, fetch=lambda secret: KEY, backend=Store())
        with mock.patch.object(ConnectionStore, "add", side_effect=ValueError("personal connections are off")), \
                self.assertRaises(gate_key.Refusal):
            gate_key.connection(self.root / "fresh", "gemini")

    def test_a_key_that_does_not_read_back_is_refused(self):
        with self.assertRaises(gate_key.Refusal):
            gate_key.load(self.folder, "gemini", SECRET, fetch=lambda secret: KEY, backend=Store(readback=False))

    def test_the_key_appears_in_no_output_and_no_file_on_any_path(self):
        """The real fetch path against fake servers, each action, success and every failure kind."""
        def run(argv, store=None, **fake):
            opener = server(**fake)
            real = functools.partial(gate_key.fetch_secret, opener=opener, metadata=opener)   # The real path.
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(gate_key, "fetch_secret", real), \
                    mock.patch.object(gate_key, "open_backend", lambda: store or Store()), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = gate_key.main(argv)
            return code, out.getvalue() + err.getvalue()
        load = ["load", "--connections-dir", str(self.folder)]
        results = [run(load), run(load, store=Store(fail=True)), run(["check"]), run(["check"], payload='"%s"' % KEY),
                   run(load, status=403, body=google_error("PERMISSION_DENIED", "IAM_PERMISSION_DENIED")),
                   run(["unload", "--connections-dir", str(self.folder)])]
        self.assertEqual([code for code, _ in results], [0, 2, 0, 2, 2, 0])
        self.assertIn("refused (RuntimeError", results[1][1])
        for _, text in results:
            self.assertNotIn(KEY, text)
            self.assertNotIn(TOKEN, text)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes(), path)


class SelectionTests(unittest.TestCase):
    """The key is loaded for the selection's own API-key accounts only, never for the metadata alone."""

    def needs(self, *only):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            plan.main(["needs", "--platform", "windows", *[a for g in only for a in ("--only", g)]])
        return json.loads(out.getvalue())

    def test_needs_names_the_api_key_accounts_a_selection_bills(self):
        self.assertEqual(self.needs("gate-carbon-*", "smoke-carbon-*")["api_key_accounts"], ["gemini"])
        for only in (("gate-sol-*", "smoke-sol-*"), ("gate-astra-*",), ("gate-opus-*",)):
            self.assertEqual(self.needs(*only)["api_key_accounts"], [], only)

    def test_init_refuses_an_empty_folder_instead_of_the_working_directory(self):
        with tempfile.TemporaryDirectory() as root:
            candidate = Path(config(root)["candidate"])
            argv = ["init", "--output", str(Path(root) / "camp"), "--candidate", str(candidate), "--platform", "windows",
                    "--gemini-connection", "g", "--only", "gate-carbon-*"]
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                runs.main(argv)
            self.assertIn("--gemini-connections-dir", str(raised.exception))


class SessionTests(unittest.TestCase):
    """windows_final_runs.ps1 and its tools: load before any trial for the selection's accounts, unload at the end
    with a failure recorded, the helper's reasons in the log, the old bench user removed, the metadata always sent."""

    def test_the_session_loads_for_its_selection_and_always_unloads(self):
        session = (ROOT / "scripts/angelo/windows_final_runs.ps1").read_text(encoding="utf-8")
        tools = (ROOT / "scripts/angelo/windows_bench_tools.ps1").read_text(encoding="utf-8")
        load = session.index('Use-GateKey $py $SourceDir $bench @($facts.api_key_accounts) "load"')
        self.assertLess(load, session.index('$initArgs = @($finalRuns, "init"'))
        self.assertLess(load, session.index("$finalRuns run --output"))
        self.assertIn("api_key_accounts = @($needs.api_key_accounts)", session)
        finally_block = session[session.index("    } finally {", load):].split("exit 0")[0]
        self.assertIn('Use-GateKey $py $SourceDir $bench @($facts.api_key_accounts) "unload"', finally_block)
        self.assertIn('"the gate key was not removed: $_"', finally_block)
        self.assertLess(finally_block.index("the gate key was not removed"), finally_block.index("DONE-$Nonce"))
        self.assertEqual(session.count("Remove-BenchUser $benchUser"), 2)
        self.assertIn("$result = \"$result; $($cleanup -join '; ')\"", session, "what stays fails the request")
        function = tools[tools.index("function Use-GateKey"):tools.index("function Remove-BenchUser")]
        self.assertIn('throw "metadata $meta is required', function)
        self.assertIn('if ($Action -ne "unload")', function, "unload needs no metadata: it always runs")
        self.assertIn("-cnotmatch '^angelo-bench-[A-Za-z0-9_-]{1,242}$'", function)
        self.assertNotIn("$secret)", function.split("cnotmatch")[1].split("\n")[0], "a rejected value is never shown")
        cleanup = tools[tools.index("function Remove-BenchUser"):]
        self.assertIn("Start-Sleep", cleanup, "it waits for Windows to unload the profile")
        self.assertIn("Microsoft\\Credentials", cleanup)
        self.assertIn('@("unload", "load")', function, "a key an earlier request left goes first")
        self.assertIn("2>&1", function)
        self.assertNotRegex(function, re.compile(r"versions access|--data|ConvertTo-SecureString|Set-Content|Out-File"))
        self.assertLessEqual(len(session.splitlines()), 500)

    def test_the_launcher_writes_the_secret_name_on_every_send(self):
        launcher = (ROOT / "scripts/angelo/gcp_windows_validate.sh").read_text(encoding="utf-8")
        self.assertIn(',angelo-bench-gemini-secret=$gemini_secret"', launcher)
        self.assertIn('local gemini_secret="${ANGELO_BENCH_GEMINI_SECRET_NAME:-}"', launcher)
        self.assertIn("^angelo-bench-[A-Za-z0-9_-]{1,242}$", launcher)
        refusal = re.search(r'echo "(ANGELO_BENCH_GEMINI_SECRET_NAME is not[^"]*)"', launcher).group(1)
        self.assertNotIn("$", refusal, "a rejected value is never echoed")


if __name__ == "__main__":
    unittest.main()
