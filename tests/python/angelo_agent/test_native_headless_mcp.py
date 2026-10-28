# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless MCP: an app's calls on a .blend with no Angelo window, end to end without Blender.

The real launcher (``external_mcp.Server``) and ``HeadlessSession`` talk over real anonymous pipes
(``headless_pipe``) to a real ``HeadlessHost`` running in a thread instead of a background Angelo (a stand-in
``Popen``), with real ``RequestService``, ``ExternalWork``, ``DirectRequest`` and ``Controller`` in a fresh /tmp
profile, a fake executor, and a stand-in ``bpy`` whose ``wm`` operators write files. The test thread is the child's
main-thread loop. Contract: doc/angelo/external_mcp.rst, "Headless".
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import headless_files, managed_policy
from angelo_agent.controller import ToolDispatchBudget
from angelo_agent.external_clients import (BOUNDED_MUTATIONS, EDIT, HEADLESS_TOOLS, PYTHON, READ, READ_TOOLS, TASKS,
                                           AppRegistry, read_access, tools_for, write_access)
from angelo_agent.external_mcp import Server
from angelo_agent.headless_host import HeadlessHost, apply_window_approval
from angelo_agent.headless_pipe import ChildProcess, PipeServer
from angelo_agent.headless_session import (PRIVATE_PREFIX, HeadlessSession, child_command, child_environment,
                                           sweep_leftovers)
from angelo_agent.external_requests import ClientRegistry
from unittest.mock import patch
from angelo_agent.managed_policy import Policy
from test_native_external_cli_host import Environment, Executor

CUBE = {"name": "Box", "size": 1, "location": [0, 0, 0]}
SOURCE_BYTES = b"BLENDER-v502 source scene"


def blend(path, data=SOURCE_BYTES):
    path.write_bytes(data)
    return path


class FakeBpy:
    """``bpy.ops.wm`` save/open as Blender does them: a save writes a temporary file and renames it over the target."""

    def __init__(self, source):
        self.data = SimpleNamespace(filepath=str(source))
        self.saves, self.loads = [], []
        self.ops = SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=self._save_as, save_mainfile=self._save,
                                                      open_mainfile=self._open))

    def _write(self, path):
        temporary = Path(str(path) + "@")
        temporary.write_bytes(b"BLENDER-v502 saved %d" % len(self.saves))
        os.replace(temporary, path)
        self.saves.append(str(path))
        return {"FINISHED"}

    def _save_as(self, filepath, copy, relative_remap, check_existing):
        assert copy is True and relative_remap is False, "saved as is, the session's own file path unchanged"
        assert Path(filepath).parent.name.startswith("angelo-save-"), "Blender writes only in a private folder"
        from angelo_agent.private_roots import scratch_root   # Angelo's private scratch folder (break-it r2m4c R9-M-2).
        assert Path(filepath).parent.parent == scratch_root(), "never in the source's shared folder"
        return self._write(filepath)

    def _save(self, filepath, check_existing):
        assert filepath == self.data.filepath, "saving anything but the session's own file"
        return self._write(filepath)

    def _open(self, filepath, load_ui, use_scripts):
        assert load_ui is False and use_scripts is False
        self.data.filepath = filepath
        self.loads.append(filepath)
        return {"FINISHED"}


class FileRuleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="shl-files-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_only_an_absolute_readable_blend_opens(self):
        good = blend(self.root / "scene.blend")
        self.assertEqual(headless_files.check_source(str(good)), good.resolve())
        blend(self.root / "zstd.blend", b"\x28\xb5\x2f\xfd....")
        headless_files.check_source(str(self.root / "zstd.blend"))
        for bad in ("scene.blend", str(self.root / "missing.blend"), str(blend(self.root / "a.txt")),
                    str(blend(self.root / "fake.blend", b"#!/bin/sh\n")), "", None, "x" * 5000):
            with self.assertRaises(headless_files.FileProblem, msg=repr(bad)[:60]):
                headless_files.check_source(bad)

    def test_an_output_never_replaces_an_existing_file(self):
        source = blend(self.root / "scene.blend")
        now = time.time()
        first = next(headless_files.output_candidates(source, now))
        first.write_bytes(b"someone else's file")
        claimed = headless_files.claim_output(source, now)
        self.assertNotEqual(claimed, first)
        self.assertTrue(claimed.name.startswith("scene.angelo-") and claimed.name.endswith("-2.blend"), claimed)
        self.assertEqual(first.read_bytes(), b"someone else's file")
        self.assertEqual(claimed.parent, source.parent, "next to the source, so relative paths stay valid")
        self.assertEqual(source.read_bytes(), SOURCE_BYTES)

    def test_an_unwritable_folder_says_so(self):
        folder = self.root / "locked"
        folder.mkdir()
        source = blend(folder / "scene.blend")
        os.chmod(folder, 0o555)
        self.addCleanup(os.chmod, folder, 0o755)
        if os.access(folder, os.W_OK):
            self.skipTest("running as a user who can write anywhere")
        with self.assertRaises(headless_files.FileProblem) as raised:
            headless_files.claim_output(source)
        self.assertEqual(raised.exception.code, "output_unwritable")

    def test_resume_accepts_only_this_files_own_outputs(self):
        source = blend(self.root / "scene.blend")
        output = blend(self.root / "scene.angelo-20261020-120000.blend")
        self.assertEqual(headless_files.check_resume(source, str(output)), output.resolve())
        for other in (blend(self.root / "other.blend"), blend(self.root / "sub.blend")):
            with self.assertRaises(headless_files.FileProblem):
                headless_files.check_resume(source, str(other))

    def test_the_open_file_digest_follows_the_real_path(self):
        source = blend(self.root / "scene.blend")
        link = self.root / "link.blend"
        link.symlink_to(source)
        self.assertEqual(headless_files.path_digest(link), headless_files.path_digest(source))
        self.assertIsNone(headless_files.path_digest(self.root / "missing.blend"))


class AllowListTests(unittest.TestCase):
    def test_headless_runs_reads_and_bounded_edits_only(self):
        everything = tools_for((READ, EDIT, PYTHON, TASKS), headless=True)
        for never in ("angelo_execute_python", "angelo_job_start", "angelo_job_cancel", "angelo_accept_preview",
                      "angelo_write_file", "angelo_delegate", "angelo_memory_update"):
            self.assertNotIn(never, everything)
        self.assertLessEqual(everything, READ_TOOLS | BOUNDED_MUTATIONS)
        self.assertEqual(everything, HEADLESS_TOOLS)
        self.assertIn("angelo_view", tools_for((READ,), headless=True))
        self.assertNotIn("angelo_create_cube", tools_for((READ,), headless=True))
        self.assertIn("angelo_create_cube", tools_for((READ, EDIT), headless=True))

    def test_the_child_starts_with_no_scripts_add_ons_or_network(self):
        command = child_command("/A/Angelo", "/s/angelo_headless.py", "/f/a.blend", "codex", "codex", ("read",))
        for flag in ("--background", "--factory-startup", "--disable-autoexec", "--offline-mode"):
            self.assertIn(flag, command)
        environment = child_environment({"PATH": "/bin", "HOME": "/h", "BLENDER_USER_SCRIPTS": "/evil",
                                         "PYTHONPATH": "/evil", "ANGELO_STATE_DIR": "/real", "LC_ALL": "C"},
                                        Path("/tmp/p"))
        self.assertNotIn("BLENDER_USER_SCRIPTS", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["ANGELO_STATE_DIR"], "/tmp/p/state", "never the person's own state")
        self.assertEqual(environment["LC_ALL"], "C")

    def test_a_file_name_holding_the_pipe_placeholders_reaches_the_child_unchanged(self):
        """Break-it L1 (2026-10-23): only the arguments that are exactly {in}/{out} become pipe numbers."""
        seen = {}

        def popen(argv, **options):
            seen["argv"] = argv
            raise OSError("not started")
        with self.assertRaises(OSError):
            ChildProcess(["/A/Angelo", "--in", "{in}", "--out", "{out}", "--file", "/p/x{in}y{out}.blend"],
                         {}, popen=popen)
        self.assertEqual(seen["argv"][-1], "/p/x{in}y{out}.blend")
        self.assertTrue(seen["argv"][2].isdigit() and seen["argv"][4].isdigit())

    def test_a_test_policy_file_reaches_the_child(self):
        """Break-it L3: the lowest-priority test policy is the one the child enforces too."""
        environment = child_environment({"PATH": "/bin", "ANGELO_POLICY_FILE": "/t/policy.json"}, Path("/tmp/p"))
        self.assertEqual(environment.get("ANGELO_POLICY_FILE"), "/t/policy.json")

    def test_only_a_bounded_qa_owner_reaches_the_private_child(self):
        for owner in ("a" * 32, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32, None, 3):
            with self.subTest(owner=owner):
                env = child_environment({"ANGELO_QA_PROCESS_OWNER": owner}, Path("/tmp/p"))
                self.assertEqual(env.get("ANGELO_QA_PROCESS_OWNER"), owner if owner == "a" * 32 else None)

    def test_a_folder_gone_mid_sweep_never_stops_the_sweep(self):
        """Break-it L2: a leftover folder that disappears while the sweep looks at it is skipped."""
        root = Path(tempfile.mkdtemp(prefix="shl-sweep-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, root, True)
        gone, dead = root / (PRIVATE_PREFIX + "gone"), root / (PRIVATE_PREFIX + "dead")
        dead.mkdir()
        (dead / "child.pid").write_text("999999999")
        real = os.lstat

        def vanishing(path, *arguments, **options):
            if Path(path) == gone:
                raise FileNotFoundError(path)
            return real(path, *arguments, **options)
        with patch.object(Path, "iterdir", lambda folder: iter([gone, dead])), \
                patch("angelo_agent.headless_session.os.lstat", vanishing):
            sweep_leftovers(root)
        self.assertFalse(dead.exists())

    def test_no_headless_module_opens_a_listener(self):
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        for name in ("headless_pipe.py", "headless_session.py", "headless_files.py", "headless_host.py",
                     "headless_main.py"):
            text = (root / name).read_text(encoding="utf-8")
            for word in ("AF_INET", "AF_UNIX", ".bind(", ".listen(", "http.server", "socket.socket"):
                self.assertNotIn(word, text, name)


class PipeTests(unittest.TestCase):
    """Real pipes and a real child process speaking the frames."""

    CHILD = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from angelo_agent.headless_pipe import PipeServer
from angelo_agent import external_cli_protocol as protocol
done = []
def handle(request, client):
    if request.get("command") == "slow":
        time.sleep(0.3)
    return protocol.envelope("ok", "echo", result={"echo": request})
server = PipeServer(int(sys.argv[2]), int(sys.argv[3]), handle, None, on_closed=lambda: done.append(1))
server.ready({"ok": True})
server.start()
while not done:
    time.sleep(0.01)
'''

    def child(self):
        addons = str(Path(__file__).resolve().parents[3] / "scripts/addons_core")
        return ChildProcess([sys.executable, "-I", "-c", self.CHILD, addons, "{in}", "{out}"], dict(os.environ))

    def test_requests_share_the_pipes_and_the_child_ends_with_them(self):
        child = self.child()
        self.assertEqual(child.wait_ready(20), {"ok": True})
        answers = {}
        threads = [threading.Thread(target=lambda n=n: answers.__setitem__(n, child.request(
            {"command": "slow" if n % 2 else "fast", "n": n}, 10))) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertEqual({n: answers[n]["result"]["echo"]["n"] for n in range(6)}, {n: n for n in range(6)})
        child.close()
        self.assertIsNotNone(child.process.poll(), "the child exits when its pipes close")
        self.assertEqual(child.request({"command": "fast"}, 1)["code"], "unavailable")

    def test_a_dead_child_answers_unavailable(self):
        child = self.child()
        child.wait_ready(20)
        child.process.kill()
        child.process.wait(5)
        deadline = time.monotonic() + 5
        while child.alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(child.request({"command": "fast"}, 1)["code"], "unavailable")
        child.close()

    def test_a_child_that_never_says_ready_is_not_used(self):
        child = ChildProcess([sys.executable, "-I", "-c", "import time; time.sleep(30)"], dict(os.environ))
        self.assertIsNone(child.wait_ready(0.3))
        child.close(grace=0.2)
        self.assertIsNotNone(child.process.poll())


class InThreadPopen:
    """A stand-in ``Popen``: the child's PipeServer and HeadlessHost run in this process; the test pumps them."""

    def __init__(self, harness):
        self.harness = harness

    def __call__(self, argv, env, pass_fds, **_ignored):
        read_fd, write_fd = os.dup(pass_fds[0]), os.dup(pass_fds[1])
        options = dict(zip(argv[argv.index("--") + 1::2], argv[argv.index("--") + 2::2]))
        process = SimpleNamespace(returncode=None, argv=argv, env=env)
        process.poll = lambda: process.returncode

        def wait(timeout=None):
            deadline = time.monotonic() + (timeout or 5)
            while process.returncode is None and time.monotonic() < deadline:
                time.sleep(0.005)
            return process.returncode
        process.wait = wait
        process.kill = lambda: setattr(process, "returncode", -9)
        self.harness.start_child(options, read_fd, write_fd, process)
        return process


@unittest.skipIf(sys.platform == "win32", "Headless is macOS (and Linux) first")
class HeadlessTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.root = Path(tempfile.mkdtemp(prefix="shl-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = str(self.root / "state")
        self.name = AppRegistry(self.state).connect("claude_code")
        write_access(self.state, True)   # What Angelo's window records while its "Allow apps" switch is on.
        self.folder = self.root / "project"
        self.folder.mkdir()
        self.source = blend(self.folder / "scene.blend")
        self.host = self.child_env = None
        self.executor = Executor(str(self.root / "child-state"))
        self.approve_mode = None
        session = HeadlessSession(self.name, "claude_code", self.state, binary=Path("/A/Angelo"), script="/s/x.py",
                                  child_type=lambda command, environ, stderr=None: ChildProcess(
                                      command, environ, popen=InThreadPopen(self), stderr=stderr),
                                  config_directory=self.root / "no-preferences")   # Never this machine's own.
        self.out = io.StringIO()
        self.server = Server(self.name, self.state, stdout=self.out, headless=session)
        self.addCleanup(session.close)

    def start_child(self, options, read_fd, write_fd, process):
        self.child_options = options
        if options.get("--organization"):   # What headless_main does with the launcher's --organization.
            from angelo_agent.headless_main import organization_policy
            organization = organization_policy(options["--organization"])
            managed_policy.set_organization_source(lambda: organization)
        self.child_env = Environment(str(self.root / "child-state"))
        approval = options.get("--approval")
        made = self.child_env.make_controller
        self.child_env.make_controller = lambda identity: apply_window_approval(made(identity), approval)
        working = options.get("--working")   # The copy the launcher made; Blender opens it, never the source.
        self.bpy = FakeBpy(working or options["--file"])
        host = HeadlessHost(self.child_env, source=options["--file"], label=options["--client"],
                            app=options["--app"], levels=options["--levels"].split(","), bpy=self.bpy,
                            working=working)
        host.staging = lambda: False
        if "--resume" in options:
            host.output, host.output_identity = Path(options["--resume"]), headless_files.identity(options["--resume"])
            self.bpy.data.filepath = options["--resume"]

        def closed():   # What the real child's exit does: its loop ends, its pipe ends close, its folder goes.
            host.end()
            host.discard_working()
            channel.writer.stream.close()
            shutil.rmtree(options["--private"], ignore_errors=True)
            process.returncode = 0
        channel = PipeServer(read_fd, write_fd, host.handle_request, host.client, on_closed=closed)
        host.channel = channel
        host.start()
        channel.start()
        channel.ready({"ok": True, "file": options["--file"], "writable": True, "working": working,
                       "working_identity": list(host.working_identity) if working else None})
        self.host = host
        # What the real child's HeadlessEnvironment adds: what the app named stays as named (named_roots).
        from angelo_agent.headless_working_copy import named_roots
        roots = self.child_env.private_roots
        self.child_env.private_roots = lambda: list(roots()) + named_roots(host.source,
                                                                           getattr(host, "named_scripts", ()))

    def tick(self):
        host = self.host
        if host is None or host.ended:
            return
        host.pump()
        controllers = list(self.child_env.controllers().values())
        for controller in controllers:
            if self.approve_mode:
                controller.state["options"]["approval"] = self.approve_mode
            controller.pump_events()
            controller.pump_tools(self.executor, self.child_env.document, budget=ToolDispatchBudget(seconds=60),
                                  apply_mutation=lambda call, c=controller: c.execute_tool(call, self.executor,
                                                                                            c.document))
        host.refuse_waiting(controllers)
        host.finish_answers()

    def test_the_windows_access_switch_governs_headless_sessions_too(self):
        """Break-it M1 (2026-10-23): "Turning this off closes the connection" holds without the window as well;
        a switch never recorded counts as off."""
        def opened():
            return self.call("angelo_open_file", {"file": str(self.source)})
        write_access(self.state, False)
        self.assertIn("Allow apps on this computer", json.dumps(opened()))
        os.remove(Path(self.state) / "mcp" / "access.json")
        self.assertIsNone(read_access(self.state))
        self.assertIn("Allow apps on this computer", json.dumps(opened()), "never recorded: off")
        write_access(self.state, True)
        self.assertFalse(opened()["isError"])
        write_access(self.state, False)
        result = self.call("angelo_inspect_scene")
        self.assertTrue(result["isError"])
        self.assertIn("Allow apps on this computer", json.dumps(result), "an open session ends at the next call")

    def test_the_window_records_its_switch_where_headless_reads_it(self):
        from angelo_agent.external_host import ExternalHost
        environment = Environment(self.state)
        host = ExternalHost(environment, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(host.stop)
        environment.requested = False
        host.record_access()
        self.assertIs(read_access(self.state), False)
        environment.requested = True
        self.assertEqual(host.wanted(), (True, ""))
        self.assertIs(read_access(self.state), False, "wanted() is a read: it records nothing")
        recorded = []
        with patch.object(host, "record_access", lambda: recorded.append(True)), \
                patch.object(host, "wanted", lambda: (False, "Off")):
            host.pump()
        self.assertEqual(recorded, [True], "recording is pump's own step")
        host.record_access()
        self.assertIs(read_access(self.state), True)
        self.assertEqual(stat.S_IMODE((Path(self.state) / "mcp" / "access.json").stat().st_mode), 0o600)

    def test_a_host_whose_environment_has_no_state_folder_records_nothing_and_still_answers(self):
        """A stand-in environment with only access_requested (test_native_managed_policy's) never breaks wanted()
        or pump's recording step."""
        from angelo_agent.external_host import ExternalHost
        host = ExternalHost.__new__(ExternalHost)
        host.environ, host.env = {}, SimpleNamespace(access_requested=lambda: True)
        host.record_access()
        self.assertEqual(host.wanted(), (True, ""))

    def test_the_signed_in_organizations_policy_reaches_the_launcher_and_the_child(self):
        """Break-it review M1 (2026-10-23, script files + headless): the window applies the team's app policy;
        headless sessions used to see only the machine's. The launcher reads the saved policy at every call."""
        organization = {}
        self.server.organization = lambda: dict(organization) or None
        organization.update(allowLocalCliAccess=False)
        result = self.call("angelo_open_file", {"file": str(self.source)})
        self.assertTrue(result["isError"])
        self.assertIn("organization turned off access", json.dumps(result))
        organization.clear()
        organization.update(requireAskForEdits=True)
        self.open()
        self.assertEqual(self.child_options.get("--approval"), "ASK", "edits ask, as a new conversation would")
        self.assertEqual(json.loads(self.child_options["--organization"]), {"requireAskForEdits": True})
        edit = self.call("angelo_create_cube", CUBE)
        self.assertTrue(edit["isError"])
        self.assertIn("needs_gui", json.dumps(edit))
        from angelo_agent import managed_policy as policy
        self.assertTrue(policy.current().require_ask_for_edits, "the child applies it as its organization source")
        organization.update(allowAutoApproval=False)
        self.call("angelo_inspect_scene")
        self.assertEqual(json.loads(self.child_options["--organization"]),
                         {"requireAskForEdits": True, "allowAutoApproval": False}, "a change restarts the child")

    def test_the_saved_policy_is_read_as_the_window_saved_it(self):
        from angelo_agent import team_org_policy
        folder = self.root / "connections"
        folder.mkdir()
        self.assertIsNone(team_org_policy.read_saved(folder), "none saved: no organization")
        (folder / team_org_policy.FILE).write_text(json.dumps({"version": 1, "console": "https://c",
                                                               "policy": {"allowAutoApproval": False}}))
        self.assertFalse(team_org_policy.read_saved(folder)["allowAutoApproval"])
        (folder / team_org_policy.FILE).write_text("{not json")
        strictest = team_org_policy.read_saved(folder)
        self.assertFalse(strictest["allowLocalCliAccess"] or strictest["allowAutoApproval"])
        self.assertTrue(strictest["requireAskForEdits"], "an unreadable saved policy fails closed")
        (folder / team_org_policy.FILE).unlink()
        os.mkfifo(folder / team_org_policy.FILE)   # Review N1: a FIFO there never blocks a reader.
        self.assertTrue(team_org_policy.read_saved(folder)["requireAskForEdits"], "unreadable: the strictest")
        account = SimpleNamespace(console_url="https://c")
        self.assertIsNone(team_org_policy.OrganizationPolicy(account, str(folder)).known, "the window's reader too")
        from angelo_agent.headless_main import organization_policy
        self.assertTrue(organization_policy("not json")["requireAskForEdits"])
        self.assertIsNone(organization_policy(None))

    def call(self, tool, arguments=None, timeout=20):
        out = []
        thread = threading.Thread(target=lambda: out.append(self.server.call(1, {"name": tool,
                                                                                  "arguments": arguments or {}})))
        thread.start()
        deadline = time.monotonic() + timeout
        while thread.is_alive() and time.monotonic() < deadline:
            self.tick()
            time.sleep(0.003)
        thread.join(1)
        self.assertTrue(out, "no answer for " + tool)
        return out[0]

    def text(self, result):
        return json.dumps(result)

    def open(self):
        result = self.call("angelo_open_file", {"file": str(self.source)})
        self.assertFalse(result["isError"], result)
        return result

    def test_no_window_and_no_file_points_at_open_file(self):
        result = self.call("angelo_inspect_scene")
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "no_file")
        self.assertIn("angelo_open_file", self.text(result))

    def test_open_reads_and_an_edit_saved_to_a_new_file_never_the_source(self):
        opened = self.open()["structuredContent"]
        self.assertEqual(opened["levels"], [READ, EDIT])
        self.assertIn("angelo_inspect_scene", opened["summary"])
        read = self.call("angelo_inspect_scene")
        self.assertFalse(read["isError"], read)
        self.assertEqual(self.bpy.saves, [], "a read saves nothing")
        edit = self.call("angelo_create_cube", CUBE)
        self.assertFalse(edit["isError"], edit)
        output = self.host.output
        self.assertIn("Saved as %s" % output, edit["content"][0]["text"])
        self.assertTrue(output.name.startswith("scene.angelo-") and output.parent == self.folder.resolve(), output)
        self.assertEqual(output.read_bytes(), b"BLENDER-v502 saved 0")
        again = self.call("angelo_set_transform", {"object": "Box", "location": [1, 0, 0]})
        self.assertFalse(again["isError"], again)
        self.assertEqual((len(self.bpy.saves), self.host.output), (2, output), "later edits save the same output")
        self.assertEqual(output.read_bytes(), b"BLENDER-v502 saved 1")
        self.assertEqual(self.source.read_bytes(), SOURCE_BYTES, "the source never changes")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), sorted(["scene.blend", output.name]))
        status = self.call("angelo_file_status")["structuredContent"]
        self.assertEqual(status["output"], str(output))
        audit = (Path(self.state) / "mcp" / "headless-audit.jsonl").read_text()
        self.assertIn("angelo_create_cube", audit)
        self.assertNotIn("Box", audit, "the audit never holds arguments")
        self.assertEqual(stat.S_IMODE(os.stat(Path(self.state) / "mcp" / "headless-audit.jsonl").st_mode), 0o600)

    def test_anything_that_needs_a_person_answers_needs_gui_at_once(self):
        self.open()
        for tool, arguments in (("angelo_execute_python", {"description": "x", "code": "result = 1"}),
                                ("angelo_job_start", {"kind": "RENDER"}), ("angelo_accept_preview", {"preview_id": "p"})):
            result = self.call(tool, arguments, timeout=10)
            self.assertTrue(result["isError"], tool)
            self.assertEqual(result["structuredContent"]["code"], "needs_gui", (tool, result))
        task = self.call("angelo_task", {"task": "model a bridge"})
        self.assertEqual(task["structuredContent"]["code"], "needs_gui")
        self.approve_mode = "ASK"      # An organization's RequireAskForEdits: every edit would show a card.
        started = time.monotonic()
        result = self.call("angelo_create_cube", CUBE, timeout=10)
        self.assertLess(time.monotonic() - started, 5, "nothing waits for a person")
        self.assertTrue(result["isError"])
        self.assertIn("needs_gui", self.text(result))
        self.assertNotIn("angelo_create_cube", self.executor.calls)
        self.assertEqual(self.bpy.saves, [])

    def test_a_preview_needs_the_window(self):
        self.open()
        result = self.call("angelo_scene_program", {"mode": "PREVIEW", "operations": []}, timeout=10)
        self.assertEqual(result["structuredContent"]["code"], "needs_gui", result)

    def test_a_change_on_disk_is_stale_until_opened_again(self):
        self.open()
        self.source.write_bytes(SOURCE_BYTES + b" saved by the person")
        result = self.call("angelo_inspect_scene")
        self.assertIn("stale", self.text(result))
        self.open()
        self.assertFalse(self.call("angelo_inspect_scene")["isError"])

    def test_an_output_someone_changed_is_never_saved_over(self):
        self.open()
        self.call("angelo_create_cube", CUBE)
        first = self.host.output
        first.write_bytes(b"BLENDER the person saved here")
        result = self.call("angelo_inspect_scene")
        self.assertIn("stale", self.text(result))
        self.assertEqual(first.read_bytes(), b"BLENDER the person saved here")

    def test_a_failed_edit_that_ended_the_request_reloads_the_last_saved_state(self):
        self.open()
        self.call("angelo_create_cube", CUBE)
        output = str(self.host.output)
        original = self.executor.execute

        def failing(name, arguments):
            if name == "angelo_set_transform":
                raise RuntimeError("partial failure")
            return original(name, arguments)
        self.executor.execute = failing
        result = self.call("angelo_set_transform", {"object": "Box", "location": [3, 0, 0]})
        self.assertTrue(result["isError"])
        self.assertEqual(self.bpy.loads, [output], "the request ended: the last saved state is loaded again")
        self.assertIn("reloaded", result["content"][0]["text"])
        self.assertEqual(len(self.bpy.saves), 1, "nothing of the failed edit is saved")
        self.executor.execute = original
        again = self.call("angelo_inspect_scene")
        self.assertFalse(again["isError"], "the session continues after the reload")

    def test_disconnect_and_policy_turn_headless_off(self):
        self.open()
        AppRegistry(self.state).disconnect(self.name)
        result = self.call("angelo_inspect_scene")
        self.assertIn("not connected", self.text(result))
        self.assertFalse(self.server.headless.opened)
        AppRegistry(self.state).connect("claude_code")
        managed_policy.reset(Policy(allow_local_cli_access=False))
        result = self.call("angelo_open_file", {"file": str(self.source)})
        self.assertEqual(result["structuredContent"]["code"], "refused")

    def test_a_read_only_app_cannot_edit_headless(self):
        AppRegistry(self.state).set_levels(self.name, (READ,))
        self.open()
        result = self.call("angelo_create_cube", CUBE)
        self.assertTrue(result["isError"])
        self.assertEqual(self.bpy.saves, [])

    def test_an_idle_child_restarts_on_the_saved_output(self):
        self.open()
        self.call("angelo_create_cube", CUBE)
        output = str(self.host.output)
        self.server.headless.child.close()       # The child ended (idle exit).
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertIn("Observe the scene again", result["content"][0]["text"])
        self.assertEqual(str(self.host.output), output, "continued in the session's own output")
        self.call("angelo_set_transform", {"object": "Box", "location": [2, 0, 0]})
        self.assertEqual(str(self.host.output), output)
        self.assertEqual(len(self.bpy.saves), 1, "the restarted child saved its edit")
        self.assertEqual(Path(output).read_bytes(), b"BLENDER-v502 saved 0")

    def test_closing_returns_to_the_window(self):
        self.open()
        self.call("angelo_close_file")
        self.assertEqual(self.call("angelo_inspect_scene")["structuredContent"]["code"], "no_file")

    # Regressions from the independent break-it review (2026-10-20) ------------------------------------------------

    def test_a_link_planted_at_blenders_temporary_name_never_reaches_the_source(self):
        """Blender writes ``<target>@`` following links; it only ever writes in the session's private folder."""
        self.open()
        self.call("angelo_create_cube", CUBE)
        output = self.host.output
        (output.parent / (output.name + "@")).symlink_to(self.source)
        hard = output.parent / "hard"
        os.link(self.source, hard)
        self.call("angelo_set_transform", {"object": "Box", "location": [4, 0, 0]})
        output.unlink()
        output.symlink_to(self.source)     # The output itself swapped for a link: a new name is claimed instead.
        result = self.call("angelo_inspect_scene")
        self.assertIn("stale", self.text(result))
        self.assertEqual(self.source.read_bytes(), SOURCE_BYTES)
        self.assertEqual(hard.read_bytes(), SOURCE_BYTES)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir() if path.name.startswith(".")), [],
                         "no temporary file is left next to the source")

    def test_a_revoked_level_holds_from_the_next_call(self):
        self.open()
        AppRegistry(self.state).set_levels(self.name, (READ,))
        result = self.call("angelo_create_cube", CUBE)
        self.assertTrue(result["isError"])
        self.assertEqual(self.bpy.saves, [])
        self.server.headless.child.close()   # And after a restart.
        self.assertTrue(self.call("angelo_set_transform", {"object": "Cube", "location": [1, 0, 0]})["isError"])
        self.assertEqual(self.bpy.saves, [])

    def test_a_restart_never_saves_over_an_output_someone_changed(self):
        self.open()
        self.call("angelo_create_cube", CUBE)
        output = self.host.output
        self.server.headless.child.close()
        output.write_bytes(b"BLENDER the person's own save")
        result = self.call("angelo_set_transform", {"object": "Box", "location": [1, 0, 0]})
        self.assertIn("stale", self.text(result))
        self.assertEqual(output.read_bytes(), b"BLENDER the person's own save")

    def test_the_persons_ask_or_read_only_choice_holds_headless(self):
        identity = "11111111-2222-3333-4444-555555555555"
        ClientRegistry(self.state).save({self.name: identity})
        folder = Path(self.state) / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (identity + ".json")).write_text(json.dumps({
            "version": 1, "options": {"approval": "ASK"}, "approval_version": 2,
            "messages": [{"role": "user", "text": "External: " + self.name, "request_kind": "external_session"}]}))
        self.open()
        result = self.call("angelo_create_cube", CUBE, timeout=10)
        self.assertIn("needs_gui", self.text(result))
        self.assertEqual(self.bpy.saves, [])

    def test_the_kill_switch_turns_headless_off(self):
        with patch.dict(os.environ, {"ANGELO_DISABLE_LOCAL_CLI": "1"}):
            result = self.call("angelo_open_file", {"file": str(self.source)})
        self.assertEqual(result["structuredContent"]["code"], "refused")

    def test_the_childs_private_folder_goes_with_it(self):
        self.open()
        private = self.server.headless._private
        self.assertTrue(private.name.startswith(PRIVATE_PREFIX) and private.is_dir())
        self.server.headless.close()
        self.assertFalse(private.exists())

    def test_leftovers_of_dead_children_are_swept(self):
        dead = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX, dir=self.root))
        (dead / "child.pid").write_text("999999")
        live = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX, dir=self.root))
        (live / "child.pid").write_text(str(os.getpid()))
        sweep_leftovers(self.root)
        self.assertFalse(dead.exists())
        self.assertTrue(live.exists())

    def test_a_planted_fifo_or_link_never_stalls_or_steers_the_sweep(self):
        stuck = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX, dir=self.root))
        os.mkfifo(stuck / "child.pid")
        linked = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX, dir=self.root))
        (linked / "child.pid").symlink_to(self.source)
        done = threading.Event()
        threading.Thread(target=lambda: (sweep_leftovers(self.root), done.set()), daemon=True).start()
        self.assertTrue(done.wait(5), "the sweep blocked on a FIFO")
        self.assertEqual(self.source.read_bytes(), SOURCE_BYTES)

    def test_placing_the_saved_bytes_never_writes_through_a_link(self):
        staged = Path(tempfile.mkdtemp(dir=self.root)) / "x.blend"
        staged.write_bytes(b"BLENDER new")
        target = self.folder / "scene.angelo-20990101-000000.blend"
        target.symlink_to(self.source)
        headless_files.place(staged, target)
        self.assertEqual(self.source.read_bytes(), SOURCE_BYTES)
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), b"BLENDER new")

    def test_the_child_refuses_a_folder_the_launcher_did_not_make(self):
        from angelo_agent.headless_main import _isolated
        other = self.root / "not-ours"
        other.mkdir(mode=0o700)
        for environ, private in (({}, None), ({"ANGELO_HEADLESS": "1", "ANGELO_STATE_DIR": str(other / "state")}, other)):
            with self.assertRaises(RuntimeError):
                _isolated(environ, private)
        good = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX, dir=self.root))
        self.assertEqual(_isolated({"ANGELO_HEADLESS": "1", "ANGELO_STATE_DIR": str(good / "state")}, good), good)


if __name__ == "__main__":
    unittest.main()
