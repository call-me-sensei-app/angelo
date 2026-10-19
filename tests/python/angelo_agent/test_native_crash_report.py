# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The crash report: what is read from the crash log, what is kept, what is sent and what is remembered.

The central property is negative: the report built from a log full of the person's
private data (file names, object names, a prompt, an API key, home-folder paths
in Info messages, in Python frames and in Windows source paths) contains none of
it (``crash_fixtures.CANARIES``). The text the dialog shows is byte for byte the text
the upload sends. Consent and "already dealt with" survive restarts and damage;
the upload is classified against a loopback server.
"""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import crash_fixtures as fx  # noqa: E402
from loopback_server import LoopbackServer  # noqa: E402
from angelo_agent import app_http, crash_log, crash_report, crash_state, crash_upload, managed_policy  # noqa: E402
from angelo_agent.app_http import NetworkError, Response  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
SYSTEM = crash_report.system_info(("NVIDIA", "NVIDIA GeForce RTX 4090/PCIe/SSE2", "4.6.0 NVIDIA 555.85", "OPENGL"),
                                  "Windows", "AMD64", "10.0.26100")
APP = crash_report.app_info("5.2.2 LTS", "311bbe4efa04", "2026-10-09")
ALLOWED_TOP = {"schema", "report_id", "created", "crashed_at", "app", "crashed_build", "system", "crash", "omitted_log_lines"}


def report_for(text, roots, mtime_ns=1_760_000_000_123_456_789):
    return crash_report.build_document(crash_log.parse(text, roots), SYSTEM, APP, mtime_ns, NOW, report_id="r-1")


def leaks(text):
    return [canary for canary in fx.CANARIES if canary.lower() in text.lower()]


class SanitizerTests(unittest.TestCase):
    def test_macos_log_keeps_only_frames_and_installation_python_files(self):
        log = crash_log.parse(fx.MAC, fx.MAC_ROOTS)
        self.assertEqual(log.build, {"name": "Blender", "version": "5.2.2", "date": "2026-10-09", "time": "07:39",
                                     "hash": "311bbe4efa04"})
        self.assertEqual(len(log.native), 9)
        self.assertEqual(log.native[0], {"image": "Angelo", "address": "0x00000001015a036c", "offset": 56,
                                         "symbol": "_ZN7blender33BLI_system_backtrace_with_os_infoEP7__sFILEPKv"})
        self.assertEqual([f["image"] for f in log.native][:5], ["Angelo", "Angelo", "libc++.1.dylib", "Angelo", "libsystem_platform.dylib"])
        self.assertEqual([p["file"] for p in log.python], [
            "<external>", "<external>", "scripts/addons_core/angelo_agent/chat_view.py",
            "python/lib/python3.13/json/__init__.py", "<internal>", "<external>"])
        self.assertEqual(log.python[2], {"file": "scripts/addons_core/angelo_agent/chat_view.py", "line": 612,
                                         "function": "draw_message"})
        self.assertTrue(log.owned_by_angelo())
        self.assertEqual(log.dropped, 6)   # The six messages before the real backtrace marker (one is a fake marker).

    def test_a_message_shaped_like_a_marker_or_a_frame_is_ignored(self):
        report = report_for(fx.MAC, fx.MAC_ROOTS)
        self.assertNotIn("leaked_symbol_from_a_message", crash_report.render(report))

    def test_linux_log(self):
        log = crash_log.parse(fx.LINUX, fx.LINUX_ROOTS)
        self.assertEqual([f["image"] for f in log.native], ["angelo", "angelo", "libc.so.6", "radeonsi_dri.so", "libc.so.6"])
        self.assertEqual(log.native[1], {"image": "angelo", "address": "0x55d1c7a3b8c4", "offset": 0x38,
                                         "symbol": "_ZN7blender3foo3barEv"})
        self.assertEqual(log.python[0]["file"], "scripts/startup/bl_ui/space_view3d.py")
        self.assertTrue(log.owned_by_angelo())

    def test_windows_log(self):
        log = crash_log.parse(fx.WINDOWS, fx.WINDOWS_ROOTS)
        self.assertEqual(log.exception, {"code_name": "EXCEPTION_ACCESS_VIOLATION", "code": "0xc0000005",
                                         "address": "0x00007ff6a1b2c3d4", "module": "Angelo.exe", "action": "read",
                                         "fault_address": "0x0000000000000008"})
        self.assertEqual(log.native[0], {"image": "Angelo.exe", "address": "0x00007ff6a1b2c3d4", "line": 120,
                                         "symbol": "blender::ed::draw::draw_something", "source": "draw.cc"})
        self.assertEqual(log.native[2]["symbol"], "Symbols not available")
        self.assertEqual([m["module"] for m in log.modules], ["Angelo.exe", "nvoglv64.dll", "KERNEL32.DLL"])
        self.assertEqual(log.modules[1]["version"], "31.0.15.3623")
        self.assertEqual([t["id"] for t in log.threads], ["000014b0", "00001a2c"])
        self.assertEqual(log.threads[0]["frames"][0]["source"], "task.cc")
        self.assertEqual([p["file"] for p in log.python],
                         ["scripts/addons_core/angelo_agent/chat_view.py", "<external>"])
        self.assertTrue(log.owned_by_angelo())

    def test_a_stock_blender_log_is_not_angelos(self):
        self.assertFalse(crash_log.parse(fx.STOCK_BLENDER).owned_by_angelo())
        self.assertFalse(crash_log.parse("").owned_by_angelo())
        self.assertFalse(crash_log.parse("not a crash log\nat all").owned_by_angelo())

    def test_symbols_with_paths_or_text_become_placeholders(self):
        text = fx.HEADER + "\n# backtrace\n0   Angelo   0x0000000100a8719c /Users/alice/x.cc + 1\n1   Angelo   0x0000000100a8719d fine_symbol + 2\n"
        self.assertEqual([f["symbol"] for f in crash_log.parse(text).native], ["?", "fine_symbol"])

    def test_oversized_lines_and_counts_are_bounded(self):
        frames = "".join("%d   Angelo   0x%016x symbol%d + 1\n" % (i % 1000, i + 4096, i) for i in range(400))
        log = crash_log.parse(fx.HEADER + "\n# backtrace\n" + frames + "x" * 1000 + "\n")
        self.assertEqual((len(log.native), log.dropped), (crash_log.MAX_FRAMES, 1 + 400 - crash_log.MAX_FRAMES))
        python = "\n# Python backtrace\n" + "".join('  File "<string>", line %d in f\n' % i for i in range(200))
        self.assertEqual(len(crash_log.parse(fx.HEADER + "\n# backtrace\n" + python).python), crash_log.MAX_PYTHON)

    def test_a_log_without_a_backtrace_has_no_stack(self):
        log = crash_log.parse(fx.HEADER + fx.REPORTS.replace("# backtrace", "# note"))
        self.assertEqual((log.native, log.python, log.exception), ([], [], {}))
        self.assertEqual(log.dropped, 6)

    def test_python_paths_only_resolve_inside_the_installation(self):
        roots = ("/Applications/Angelo.app/Contents/Resources/5.2",)
        for path, expected in (
                ("/Applications/Angelo.app/Contents/Resources/5.2/scripts/modules/x.py", "scripts/modules/x.py"),
                ("/applications/angelo.app/contents/resources/5.2/python/lib/a.py", "python/lib/a.py"),
                ("/Applications/Angelo.app/Contents/Resources/5.2/extensions/user/x.py", "<external>"),
                ("/Applications/Angelo.app/Contents/Resources/5.2/scripts/../../x.py", "<external>"),
                ("/Applications/Angelo.app/Contents/Resources/5.2/scripts/a b.py", "<external>"),
                ("/Applications/Angelo.app/Contents/Resources/5.20/scripts/x.py", "<external>"),
                ("/Users/alice/x.py", "<external>"), ("<frozen importlib._bootstrap>", "<internal>"), ("", "<external>")):
            with self.subTest(path=path):
                self.assertEqual(crash_log.python_file(path, roots), expected)
        self.assertEqual(crash_log.python_file("C:\\Program Files\\Angelo\\5.2\\scripts\\startup\\x.py",
                                               fx.WINDOWS_ROOTS), "scripts/startup/x.py")


class ReportTests(unittest.TestCase):
    def test_no_private_data_reaches_the_report_on_any_platform(self):
        for name, text, roots in (("macos", fx.MAC, fx.MAC_ROOTS), ("linux", fx.LINUX, fx.LINUX_ROOTS),
                                  ("windows", fx.WINDOWS, fx.WINDOWS_ROOTS)):
            with self.subTest(platform=name):
                rendered = crash_report.render(report_for(text, roots))
                self.assertEqual(leaks(rendered), [])
                self.assertNotIn("sk-ant", rendered)

    def test_the_shown_text_is_exactly_the_sent_text(self):
        document = report_for(fx.WINDOWS, fx.WINDOWS_ROOTS)
        shown, sent = crash_report.render(document), crash_report.body(document)
        self.assertEqual(sent, shown.encode("ascii"))
        self.assertEqual(json.loads(sent), json.loads(shown))
        self.assertTrue(shown.isascii() and shown == crash_report.render(json.loads(shown)))

    def test_the_report_has_exactly_the_documented_fields(self):
        document = report_for(fx.MAC, fx.MAC_ROOTS)
        self.assertEqual(set(document), ALLOWED_TOP)
        self.assertEqual(set(document["app"]), {"name", "version", "blender_base", "build_hash", "build_date"})
        self.assertEqual(set(document["system"]), {"os", "os_version", "arch", "gpu"})
        self.assertEqual(set(document["system"]["gpu"]), {"vendor", "renderer", "version", "backend"})
        self.assertEqual(set(document["crash"]), {"exception", "native_stack", "native_threads", "python_stack", "loaded_modules"})
        self.assertEqual(document["schema"], "angelo.crash-report.v1")
        self.assertEqual((document["created"], document["crashed_at"]), ("2026-10-25T12:00:00Z", "2025-10-09T08:53:00Z"))
        self.assertTrue(document["crashed_build"]["same_build"])
        self.assertEqual(document["omitted_log_lines"], 6)
        self.assertRegex(crash_report.render(document), r'"report_id": "r-1"')
        self.assertEqual(crash_report.default_file_name(document), "Angelo-crash-report-2025-10-09.json")

    def test_a_build_that_differs_from_the_crashed_one_is_flagged(self):
        other = crash_report.app_info("5.2.2 LTS", "ffffffffffff", "2026-10-20")
        document = crash_report.build_document(crash_log.parse(fx.MAC, fx.MAC_ROOTS), SYSTEM, other, 0, NOW)
        self.assertFalse(document["crashed_build"]["same_build"])
        self.assertRegex(document["report_id"], r"^[0-9a-f-]{36}$")

    def test_environment_text_is_printable_ascii(self):
        system = crash_report.system_info(("NV\u202eIDIA\n", "Rend\x00erer " + "x" * 300, None, "OPENGL"), "Linux", "x86_64", "6.8.0-45")
        self.assertEqual(system["gpu"]["vendor"], "NV?IDIA?")
        self.assertEqual(len(system["gpu"]["renderer"]), 120)
        self.assertEqual(system["gpu"]["version"], "")
        self.assertEqual(crash_report.system_info(None, "Darwin", "arm64", "26.3")["os"], "macOS")

    def test_a_huge_log_is_cut_down_to_the_size_limit(self):
        self.assertEqual(crash_report.MAX_BODY, 65536, "the server reads at most 65,536 bytes and answers 413 beyond")
        frames = "".join("%d   Angelo   0x%016x %s + 1\n" % (i % 1000, i + 4096, "s" * 280) for i in range(128))
        python = "".join('  File "<string>", line %d in %s\n' % (i, "f" * 90) for i in range(64))
        text = fx.HEADER + "\n# backtrace\n" + frames + "\n# Python backtrace\n" + python
        document = report_for(text, ())
        self.assertLessEqual(len(crash_report.body(document)), crash_report.MAX_BODY)
        self.assertTrue(document["crash"]["native_stack"])

    def test_the_size_limit_is_exact(self):
        """A report that fits is never cut; one byte over loses half of its lowest-priority list."""
        frames = "".join("%d   Angelo   0x%016x %s + 1\n" % (i % 1000, i + 4096, "s" * 120) for i in range(128))
        document = report_for(fx.HEADER + "\n# backtrace\n" + frames, ())
        size = len(crash_report.body(document))
        self.assertLess(size, crash_report.MAX_BODY)
        with mock.patch.object(crash_report, "MAX_BODY", size):
            self.assertEqual(len(report_for(fx.HEADER + "\n# backtrace\n" + frames, ())["crash"]["native_stack"]), 128)
        with mock.patch.object(crash_report, "MAX_BODY", size - 1):
            cut = report_for(fx.HEADER + "\n# backtrace\n" + frames, ())
            self.assertEqual(len(cut["crash"]["native_stack"]), 64)
            self.assertLessEqual(len(crash_report.body(cut)), size - 1)

    def test_the_app_version_is_cleaned_like_every_other_string(self):
        with mock.patch.object(crash_report.app_version, "current", return_value="1.0.0\u202e\n" + "9" * 60):
            app = crash_report.app_info("5.2.2 LTS", "311bbe4efa04", "2026-10-09")
        self.assertEqual(app["version"], ("1.0.0??" + "9" * 60)[:40])


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)

    def write(self, name, text, age=60, mode=None):
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def find(self, state=None):
        return crash_report.find_pending(self.base, state or crash_state.CrashState(), time.time(), fx.MAC_ROOTS)

    def test_the_newest_angelo_crash_is_pending_and_older_ones_are_superseded(self):
        self.write("angelo.crash.txt", fx.MAC, age=100)
        self.write("Scene.crash.txt", fx.MAC, age=7200)
        pending, records = self.find()
        self.assertEqual(pending.name, "angelo.crash.txt")
        self.assertEqual([status for _, status in records], [crash_state.SUPERSEDED])

    def test_a_stock_blenders_log_is_left_alone_and_never_recorded(self):
        self.write("blender.crash.txt", fx.STOCK_BLENDER)
        self.assertEqual(self.find(), (None, []))

    def test_a_log_that_does_not_name_angelos_executable_is_not_offered(self):
        self.write("angelo.crash.txt", fx.HEADER + "\n# backtrace\n0   Angelo   garbage\n")
        self.assertEqual(self.find(), (None, []))

    def test_handled_old_oversized_and_misnamed_files_are_skipped(self):
        path = self.write("angelo.crash.txt", fx.MAC)
        state = crash_state.CrashState()
        info = path.stat()
        state.mark(crash_state.fingerprint(path.name, info.st_mtime_ns, info.st_size), crash_state.DECLINED)
        self.assertEqual(self.find(state), (None, []))
        self.write("old.crash.txt", fx.MAC, age=15 * 86400)
        self.write("big.crash.txt", fx.MAC + "# " + "x" * crash_log.MAX_FILE)
        self.write("angelo.txt", fx.MAC)
        self.write("empty.crash.txt", "")
        (self.base / "link.crash.txt").symlink_to(path)
        self.assertEqual(self.find(state), (None, []))
        self.assertEqual(crash_log.crash_files(self.base / "missing", time.time()), [])

    def test_a_new_crash_over_the_same_file_name_is_a_new_question(self):
        path = self.write("angelo.crash.txt", fx.MAC, age=500)
        state = crash_state.CrashState()
        pending, _ = self.find(state)
        state.mark(pending.fingerprint, crash_state.SENT)
        self.assertEqual(self.find(state), (None, []))
        self.write("angelo.crash.txt", fx.MAC + "\n", age=5)
        self.assertEqual(self.find(state)[0].name, path.name)

    def test_a_failed_send_is_asked_again_until_the_attempts_run_out(self):
        self.write("angelo.crash.txt", fx.MAC)
        state = crash_state.CrashState()
        pending, _ = self.find(state)
        state.mark(pending.fingerprint, crash_state.FAILED)
        self.assertEqual(self.find(state)[0].fingerprint, pending.fingerprint)
        self.assertEqual(state.attempts(pending.fingerprint), 1)
        state.mark(pending.fingerprint, crash_state.GAVE_UP)
        self.assertEqual(self.find(state), (None, []))


class StateTests(unittest.TestCase):
    def test_consent_starts_as_ask_and_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(crash_state.load(folder).consent, crash_state.ASK)
            state = crash_state.CrashState(crash_state.ALWAYS)
            state.mark("a" * 16, crash_state.SENT, "r-1", now=5.0)
            self.assertTrue(crash_state.save(folder, state))
            again = crash_state.load(folder)
            self.assertEqual((again.consent, again.status("a" * 16), again.attempts("a" * 16)), ("always", "sent", 1))
            self.assertEqual(again.handled["a" * 16]["report_id"], "r-1")

    def test_a_damaged_file_reads_as_ask_with_nothing_handled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / crash_state.STATE_FILE
            for text in ("nope", "[]", json.dumps({"consent": "sometimes", "handled": {"short": {}, "b" * 16: {"status": "??"},
                                                                                    "c" * 16: 5}})):
                path.write_text(text)
                state = crash_state.load(folder)
                self.assertEqual((state.consent, state.handled), ("ask", {}))
            # A folder below a regular file cannot exist on any system (a root-level path is writable on Windows).
            self.assertFalse(crash_state.save(str(path / "below"), crash_state.CrashState()))

    def test_only_the_newest_fifty_dispositions_are_kept(self):
        state = crash_state.CrashState()
        for index in range(60):
            state.mark("%016d" % index, crash_state.DECLINED, now=float(index))
        self.assertEqual(len(state.handled), crash_state.MAX_HANDLED)
        self.assertNotIn("%016d" % 0, state.handled)
        self.assertIn("%016d" % 59, state.handled)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.document = report_for(fx.MAC, fx.MAC_ROOTS)
        self.body = crash_report.body(self.document)

    def test_the_endpoint_is_the_feeds_unless_overridden_or_forbidden(self):
        feed = "https://api.angelo.studio/v1/crash-reports"
        policy = managed_policy.Policy()
        self.assertEqual(crash_upload.endpoint(feed, policy, {}), feed)
        self.assertEqual(crash_upload.endpoint("", policy, {}), "")
        self.assertEqual(crash_upload.endpoint("http://api.angelo.studio/x", policy, {}), "")
        self.assertEqual(crash_upload.endpoint(feed, policy, {"ANGELO_CRASH_REPORT_URL": "http://127.0.0.1:9/c"}), "http://127.0.0.1:9/c")
        self.assertEqual(crash_upload.endpoint(feed, policy, {"ANGELO_CRASH_REPORT_URL": "http://example.com/c"}), "")
        forbidden = managed_policy.combine([("test", {"DisableCrashReporting": True})])
        self.assertEqual(crash_upload.endpoint(feed, forbidden, {"ANGELO_CRASH_REPORT_URL": "https://angelo.studio/c"}), "")

    def test_the_post_sends_exactly_the_shown_text_and_nothing_else(self):
        with LoopbackServer() as server:
            server.routes["/reports"] = lambda request: (201, {}, json.dumps({"id": "r-1"}).encode())
            outcome = crash_upload.send(server.base + "/reports", self.body, "r-1")
            self.assertEqual((outcome.kind, outcome.receipt), (crash_upload.SENT, "r-1"))
            sent = server.requests[0]
            self.assertEqual((sent.method, sent.body), ("POST", crash_report.render(self.document).encode("ascii")))
            self.assertEqual(sent.headers["content-type"], "application/json")
            self.assertFalse({"cookie", "authorization"} & set(sent.headers))

    def test_every_answer_is_classified(self):
        def answer(status, body=b"{}"):
            return lambda *a, **k: Response(status, {}, body)

        def failing(kind):
            def fail(*a, **k):
                raise NetworkError(kind)
            return fail

        url = "https://api.angelo.studio/v1/crash-reports"
        cases = ((answer(202, b"not json"), crash_upload.SENT), (answer(400), crash_upload.REFUSED),
                 (answer(413), crash_upload.REFUSED), (answer(404), crash_upload.REFUSED), (answer(429), crash_upload.RETRY),
                 (answer(408), crash_upload.RETRY), (answer(503), crash_upload.RETRY),
                 (failing(app_http.OFFLINE), crash_upload.OFFLINE), (failing(app_http.TIMEOUT), crash_upload.OFFLINE),
                 (failing(app_http.TLS), crash_upload.RETRY), (failing(app_http.PROTOCOL), crash_upload.RETRY))
        for request, kind in cases:
            with self.subTest(kind=kind):
                self.assertEqual(crash_upload.send(url, self.body, "r-1", request).kind, kind)
        self.assertEqual(crash_upload.send(url, self.body, "r-1", answer(200, b'{"id": "other"}')).receipt, "")

    def test_retry_after_is_kept_and_a_429_is_busy(self):
        def answer(status, wait):
            return lambda *a, **k: Response(status, {"retry-after": wait}, b"{}")

        url = "https://console.angelo.studio/api/v1/crash-reports"
        busy = crash_upload.send(url, self.body, "r-1", answer(429, "540"))
        self.assertEqual((busy.kind, busy.retry_after, busy.busy), (crash_upload.RETRY, 540.0, True))
        self.assertEqual(crash_upload.send(url, self.body, "r-1", answer(503, "99999999")).retry_after, 7 * 24 * 3600.0)
        for wait in ("", "soon", "-5", "Wed, 21 Oct 2026 07:28:00 GMT"):
            self.assertEqual(crash_upload.send(url, self.body, "r-1", answer(503, wait)).retry_after, 0.0, wait)
        self.assertFalse(crash_upload.send(url, self.body, "r-1", answer(503, "5")).busy)


class GrammarFuzzTests(unittest.TestCase):
    def test_arbitrary_text_in_any_part_of_the_log_never_adds_unlisted_output(self):
        rng_lines = ["/Users/alice/Secret Client/Project X.blend", 'File "/home/alice/x.py", line 3 in secret',
                     "12  Angelo  0x1000  /Users/alice/Documents/Project X.cc + 4", "ExceptionCode : /Users/alice/Secret Client",
                     "Thread : zzzz", "0xZZ 1.0 hunter2.dll", "\x00\x01 binary \u202e", "{" * 400]
        base = crash_log.parse(fx.MAC, fx.MAC_ROOTS)
        for line in rng_lines:
            for position in ("head", "reports", "native", "python"):
                lines = fx.MAC.split("\n")
                at = {"head": 1, "reports": 4, "native": lines.index("# backtrace") + 3,
                      "python": lines.index("# Python backtrace") + 2}[position]
                lines.insert(at, line)
                rendered = crash_report.render(report_for("\n".join(lines), fx.MAC_ROOTS))
                with self.subTest(line=line[:30], position=position):
                    self.assertEqual(leaks(rendered), [])
                    self.assertNotRegex(rendered, r"[A-Za-z]:\\\\|/Users/|hunter2|secret")
        self.assertEqual(len(base.native), 9)


if __name__ == "__main__":
    unittest.main()
