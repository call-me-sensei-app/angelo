# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in ``codex app-server`` the hosted worker-chat suites play their model with (``hosted_codex_stand_in``).

It runs as a real child process on stdio, as Angelo's runtime service launches it. Pinned: a turn plays its chat's
oldest queued script (the chat named by the tag in its message), each tool call waits for its answer and is logged
with both clocks, a failed call stops the rest when asked, a hold waits for its release or an interrupt, a plan turn
ends with a typed plan, ``thread/fork`` answers Angelo's cache-affinity probe as the pinned build does, and the
``HostedBatchRuntime`` reads only the log off the main thread. No Blender.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import hosted_codex_stand_in as stand_in  # noqa: E402

PIN = {stand_in.ENV + "_VERSION": "9.9.9-pin"}
PATHEXT = os.pathsep.join((".COM", ".EXE", ".BAT", ".CMD"))   # Windows' default.


def windows_which(cmd, mode=os.F_OK | os.X_OK, path=None):
    """``shutil.which`` as Windows answers it, on any host: a POSIX ``which`` neither tries PATHEXT nor ignores the
    executable bit. Each PATH folder in order; ``cmd`` itself only when it already ends in a PATHEXT extension, else
    ``cmd`` plus each extension; names matched without case (NTFS); no executable bit; and, as for the lead's process,
    no current-folder search first."""
    extensions = [ext for ext in os.environ.get("PATHEXT", "").split(os.pathsep) if ext]
    lowered = cmd.lower()
    names = [cmd] if any(lowered.endswith(ext.lower()) for ext in extensions) else [cmd + ext for ext in extensions]
    for directory in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
        if not (directory and os.path.isdir(directory)):
            continue
        entries = {entry.lower(): entry for entry in os.listdir(directory)}
        for name in names:
            entry = entries.get(name.lower())
            if entry and os.path.isfile(os.path.join(directory, entry)):
                return os.path.join(directory, entry)
    return None


class Child:
    def __init__(self, root, command=None):
        env = dict(os.environ, **{stand_in.ENV: str(root)}, **PIN)
        self.process = subprocess.Popen(command or [sys.executable, str(HERE / "hosted_codex_stand_in.py"),
                                                    "app-server"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        self.lines, self.lock = [], threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()
        self.ids = 0

    def _read(self):
        for line in self.process.stdout:
            with self.lock:
                self.lines.append(json.loads(line))
                self.lock.notify_all()

    def write(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, **params):
        self.ids += 1
        self.write({"id": self.ids, "method": method, "params": params})
        return self.wait(lambda m: m.get("id") == self.ids and "method" not in m)

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        with self.lock:
            while True:
                found = next((m for m in self.lines if predicate(m)), None)
                if found is not None:
                    return found
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError("no such message: %r" % self.lines[-5:])
                self.lock.wait(left)

    def close(self):
        self.process.stdin.close()
        self.process.wait(10)
        self.process.stdout.close()


class StandInTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.answered = set()
        self.child = Child(self.root)
        self.addCleanup(self.child.close)
        self.thread = self.child.request("thread/start", cwd=str(self.root))["result"]["thread"]["id"]

    def queue(self, chat, steps, sequence=1):
        folder = self.root / chat
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("queued-%d.json" % sequence)).write_text(json.dumps(steps))

    def turn(self, chat, plan=False):
        params = {"threadId": self.thread, "input": [{"type": "text", "text": "go " + stand_in.tag(chat)}]}
        if plan:
            params["outputSchema"] = {"type": "object"}
        return self.child.request("turn/start", **params)["result"]["turn"]["id"]

    def log(self, chat, kind):
        path = self.root / chat / "log.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row["kind"] == kind]

    def completed(self, turn):
        return self.child.wait(lambda m: m.get("method") == "turn/completed" and m["params"]["turn"]["id"] == turn)

    def answer(self, success=True):
        call = self.child.wait(lambda m: m.get("method") == "item/tool/call" and m["id"] not in self.answered)
        self.answered.add(call["id"])
        self.child.write({"id": call["id"], "result": {"success": success, "contentItems": [
            {"type": "inputText", "text": json.dumps({"seen": call["params"]["tool"]})}]}})
        return call

    def test_a_turn_plays_its_chats_queue_and_logs_each_answer(self):
        self.queue("chat-a", [{"call": {"id": "c1", "tool": "angelo_inspect_scene", "arguments": {}}},
                              {"reply": "Read it."}])
        turn = self.turn("chat-a", plan=True)
        call = self.answer()
        self.assertEqual((call["params"]["callId"], call["params"]["turnId"]), ("c1", turn))
        done = self.completed(turn)
        self.assertEqual(done["params"]["turn"]["status"], "completed")
        message = self.child.wait(lambda m: m.get("method") == "item/completed"
                                  and m["params"]["item"]["type"] == "agentMessage")
        self.assertEqual(json.loads(message["params"]["item"]["text"]), {"reply": "Read it.", "operations": []})
        row = self.log("chat-a", "tool_result")[0]
        self.assertEqual((row["id"], row["success"], json.loads(row["text"])), ("c1", True,
                                                                                 {"seen": "angelo_inspect_scene"}))
        self.assertLess(row["sent_ns"], row["answered_ns"])
        self.assertTrue((self.root / "chat-a" / "played-1.json").exists())
        self.assertEqual(self.child.request("initialize")["result"]["userAgent"].split()[0],
                         "codex_cli_rs/9.9.9-pin")

    def test_a_failed_call_stops_the_rest_when_asked(self):
        steps = [{"call": {"id": "b%d" % n, "tool": "angelo_execute_python", "arguments": {}}, "stop_if_failed": True}
                 for n in range(3)]
        self.queue("chat-b", steps)
        turn = self.turn("chat-b")
        self.answer(success=True)
        self.answer(success=False)
        self.completed(turn)
        self.assertEqual([row["id"] for row in self.log("chat-b", "tool_result")], ["b0", "b1"])

    def test_a_hold_waits_for_its_release_or_an_interrupt(self):
        self.queue("chat-c", [{"hold": "h"}, {"reply": "after"}], 1)
        self.queue("chat-c", [{"hold": "h2"}], 2)
        turn = self.turn("chat-c")
        time.sleep(0.2)
        self.assertEqual(self.log("chat-c", "turn_completed"), [])
        (self.root / "chat-c" / "release-h").write_text("go")
        self.assertEqual(self.completed(turn)["params"]["turn"]["status"], "completed")
        second = self.turn("chat-c")
        self.child.request("turn/interrupt", threadId=self.thread, turnId=second)
        self.assertEqual(self.completed(second)["params"]["turn"]["status"], "interrupted")

    def test_fork_answers_the_cache_affinity_probe_as_the_pinned_build(self):
        refused = self.child.request("thread/fork", threadId="missing", cacheAffinityKey=" ")
        self.assertEqual(refused["error"]["message"], "cacheAffinityKey must be 1-64 visible ASCII characters")
        unknown = self.child.request("thread/fork", threadId="missing", cacheAffinityKey="angelo-probe")
        self.assertTrue(unknown["error"]["message"].startswith("no rollout found"))
        forked = self.child.request("thread/fork", threadId=self.thread, cacheAffinityKey="angelo-probe")
        self.assertIn("thread", forked["result"])

    def test_an_unscripted_turn_replies_with_no_call(self):
        turn = self.turn("chat-d")
        self.assertEqual(self.completed(turn)["params"]["turn"]["status"], "completed")
        self.assertEqual(self.log("chat-d", "tool_result"), [])


class InstallTests(unittest.TestCase):
    """``install`` writes the kind of ``codex`` the lead's own launch path starts on each platform (Windows simulated
    here: the real exe bytes, the product's real resolution and launch functions; the run on Windows is the suites')."""

    COMMAND = ["app-server", "--listen", "stdio://", "-c", 'model_provider="openai"', "-c", "agents.enabled=false"]

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_posix_gets_a_shell_wrapper_that_runs_the_stand_in(self):
        folder = stand_in.install(self.folder / "bin", windows=False)
        wrapper = folder / "codex"
        self.assertTrue(wrapper.read_text().startswith("#!/bin/sh\nexec "))
        self.assertTrue(os.access(wrapper, os.X_OK))
        self.assertFalse((folder / "codex.exe").exists())
        if os.name != "nt":
            done = subprocess.run([str(wrapper), "--version"], capture_output=True, text=True, timeout=30,
                                  env=dict(os.environ, **PIN))
            self.assertEqual(done.stdout.strip(), "codex-cli 9.9.9-pin")

    def test_windows_gets_a_launcher_exe_naming_python_before_a_zip_that_runs_the_stand_in(self):
        python = "C:\\Program Files\\Angelo\\5.2\\python\\bin\\python.exe"
        exe = stand_in.install(self.folder / "bin", python=python, windows=True) / "codex.exe"
        self.assertFalse((self.folder / "bin" / "codex").exists())
        data, launcher = exe.read_bytes(), stand_in.launcher_exe()
        self.assertEqual(data[:len(launcher)], launcher)
        shebang, _, archive = data[len(launcher):].partition(b"\n")
        self.assertEqual(shebang.decode("utf-8"), '#!"%s"' % python)   # Quoted: the path has spaces.
        self.assertEqual(archive[:2], b"PK")
        self.assertNotEqual(stand_in.launcher_exe("ARM64"), stand_in.launcher_exe("AMD64"))
        # The launcher starts ``<shebang python> <exe> <arguments>``; any Python runs the exe's zip the same way.
        installed = stand_in.install(self.folder / "here", windows=True) / "codex.exe"
        done = subprocess.run([sys.executable, str(installed), "--version"], capture_output=True, text=True,
                              timeout=30, env=dict(os.environ, **PIN))
        self.assertEqual(done.stdout.strip(), "codex-cli 9.9.9-pin", done.stderr)
        child = Child(self.folder / "root", [sys.executable, str(installed), *self.COMMAND])
        self.addCleanup(child.close)
        self.assertEqual(child.request("initialize")["result"]["userAgent"].split()[0], "codex_cli_rs/9.9.9-pin")

    def test_the_leads_windows_launch_finds_the_exe_and_starts_it_with_its_arguments_unchanged(self):
        from angelo_agent import cli_runtime, cli_windows
        folder = stand_in.install(self.folder / "bin", windows=True)
        empty = self.folder / "empty"   # No ChatGPT app, npm or other well-known folder: PATH must find it.
        environ = {name: str(empty) for name in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "USERPROFILE")}
        with patch.object(cli_runtime.shutil, "which", windows_which), patch.object(Path, "home", return_value=empty), \
                patch.dict(os.environ, {"PATH": str(folder), "PATHEXT": PATHEXT}):
            found = cli_runtime._find_windows("codex", "", environ, registry=lambda: [])
        self.assertEqual((Path(found).parent, Path(found).name.lower()), (folder, "codex.exe"))   # Any case: NTFS.
        command = [found, *self.COMMAND]
        self.assertEqual(cli_windows.launch_command(command, lambda: self.fail("no node"), {}), (command, {}))
        # Why not a batch file: Angelo refuses to pass the app-server's quoted overrides through cmd.exe.
        batch = folder / "codex.cmd"
        batch.write_text("@echo off\r\n\"python\" \"stand_in.py\" %*\r\n")
        with self.assertRaises(cli_windows.UnsafeBatchArguments):
            cli_windows.launch_command([str(batch), *self.COMMAND], lambda: self.fail("no node"), {})


class HostedBatchRuntimeTests(unittest.TestCase):
    """The batch runtime reads only the stand-in's log off the main thread; results come from the chat's journal."""

    class Chat:
        def __init__(self):
            self.rows, self.results, self.said = [], {}, None

        def say(self, text, steps):
            self.said = steps

        def model_log(self, kind=None):
            return [row for row in list(self.rows) if kind is None or row["kind"] == kind]

        def pump(self):
            pass

        def result_of(self, call_id):
            return self.results.get(call_id)

    def test_it_finishes_once_every_batch_is_answered_and_stops_at_an_error(self):
        from concurrent_edit_runtime import HostedBatchRuntime
        chat = self.Chat()
        jobs = [{"job_id": "j%d" % n} for n in range(2)]
        runtime = HostedBatchRuntime(chat, jobs, lambda job: "result = 1", time.monotonic() + 10).start()
        self.assertEqual([step["call"]["tool"] for step in chat.said], ["angelo_execute_python"] * 2)
        self.assertTrue(all(step["stop_if_failed"] for step in chat.said))
        ids = [receipt["call_id"] for receipt in runtime.receipts]
        for n, identity in enumerate(ids):
            chat.rows.append({"kind": "tool_result", "id": identity, "success": True, "sent_ns": 10 + n,
                              "answered_ns": 20 + n})
            chat.results[identity] = {"success": True, "result": {"job_id": "j%d" % n, "start_ns": 12 + n}}
        self.assertTrue(runtime.finished.wait(5))
        self.assertTrue(runtime.join(5))
        self.assertEqual(runtime.errors, [])
        events = runtime.events(timeout=1)
        self.assertEqual([(e["job_id"], e["admitted_ns"], e["answered_ns"]) for e in events],
                         [("j0", 10, 20), ("j1", 11, 21)])
        failing = HostedBatchRuntime(self.Chat(), jobs, lambda job: "x", time.monotonic() + 10)
        failing.chat.rows.append({"kind": "tool_result", "id": None, "success": False})
        failing.start()
        failing.chat.rows[0]["id"] = failing.receipts[0]["call_id"]
        self.assertTrue(failing.finished.wait(5))
        self.assertIn("Native batch failed", failing.errors[0])


if __name__ == "__main__":
    unittest.main()
