# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal link drafts, without Unreal (doc/angelo/unreal_link.rst).

Covered:

* ``unreal_link_remote`` against a stand-in editor that follows Unreal 5.8's PythonScriptRemoteExecution.cpp over
  real loopback multicast and TCP: discovery returns the project, a command connection opens only to the editor that
  was asked, a result split across reads is reassembled, an oversized command is refused before sending, a script's
  result line is parsed and a script without one raises with the editor's output. Skipped where the host has no
  loopback multicast.
* The Unreal-side scripts parse as Python 3.11 (Unreal 5.8's interpreter) and carry their parameters as data.
* The plugin's link client (``AngeloLink/Content/Python/angelo_link``) against Angelo's real link server, with the
  engine-neutral protocol name: the mutual proof, the ``open`` request, a scene frame, and a wrong token refused.
* The plugin is content-only, depends on the Python plugin, is MIT, parses as Python 3.11 and refuses the assets it
  can't edit.
"""

import ast
import json
import os
import random
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core"
PLUGIN = ADDON / "angelo_agent/unreal_plugin/AngeloLink"
sys.path.insert(0, str(ADDON))
sys.path.insert(0, str(PLUGIN / "Content/Python"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "unreal_link_e2e"))
import unreal_link_remote as remote  # noqa: E402
from angelo_agent import engine_link_protocol as protocol, engine_link_server, unreal_link_assets  # noqa: E402
from angelo_link import assets, link_client  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_test_policy import environment_skip  # noqa: E402

TOKEN = bytes(range(32))


class FakeEditor:
    """A remote-execution node as PythonScriptRemoteExecution.cpp implements it: answers ping with pong, connects to
    the command endpoint an ``open_connection`` names, parses each read as one message and answers ``command``.
    Its receiving socket binds every address, as an editor with Multicast Bind Address 0.0.0.0 does."""

    def __init__(self, group, handler, info=None, split=False, interface="127.0.0.1"):
        self.id, self.group, self.handler, self.split = "fake-" + str(random.random()), group, handler, split
        self.info = info or {"user": "u", "machine": "m", "engine_version": "5.8.2", "engine_root": "/E",
                             "project_root": "/P/", "project_name": "P"}
        self.commands, self.closed = [], False
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.udp.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_REUSEPORT", socket.SO_REUSEADDR), 1)
        self.udp.bind(("", group[1]))   # An editor whose Multicast Bind Address is 0.0.0.0 (see Discovery).
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton(group[0]) + socket.inet_aton(interface))
        self.udp.settimeout(0.05)
        self.tcp = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def close(self):
        self.closed = True
        self.thread.join(2)
        self.udp.close()
        if self.tcp:
            self.tcp.close()

    def _run(self):
        while not self.closed:
            try:
                message = remote.decode(self.udp.recv(65535))
            except socket.timeout:
                message = None
            if message and remote.for_us(message, self.id):
                if message["type"] == "ping":
                    self.udp.sendto(remote.encode("pong", self.id, message["source"], self.info), self.group)
                elif message["type"] == "open_connection" and self.tcp is None:
                    data = message["data"]
                    self.tcp = socket.create_connection((data["command_ip"], data["command_port"]), 2)
                    self.peer = message["source"]
            if self.tcp is not None:
                self._serve_once()

    def _serve_once(self):
        self.tcp.settimeout(0.05)
        try:
            body = self.tcp.recv(1 << 22)   # One read is one message, as in the editor.
        except socket.timeout:
            return
        except OSError:
            self.tcp = None
            return
        message = remote.decode(body) if body else None
        if not message or message["type"] != "command" or not remote.for_us(message, self.id):
            return
        self.commands.append(message["data"])
        success, output = self.handler(message["data"]["command"])
        reply = remote.encode("command_result", self.id, self.peer, {
            "success": success, "command": message["data"]["command"], "result": "None",
            "output": [{"type": "Info", "output": line} for line in output]})
        if self.split:
            half = len(reply) // 2
            self.tcp.sendall(reply[:half])
            time.sleep(0.15)
            self.tcp.sendall(reply[half:])
        else:
            self.tcp.sendall(reply)


class RemoteExecutionTests(unittest.TestCase):
    def setUp(self):
        self.group = ("239.0.0.1", random.randint(42000, 62000))   # Never Unreal's own 6766.
        self.editors = []
        self.discovery = remote.Discovery(self.group)
        self.addCleanup(self.discovery.close)
        self.addCleanup(lambda: [editor.close() for editor in self.editors])

    def editor(self, handler=lambda command: (True, []), **kwargs):
        editor = FakeEditor(self.group, handler, **kwargs)
        self.editors.append(editor)
        return editor

    def found(self):
        nodes = self.discovery.discover(1.0)
        if not nodes:
            self.skipTest(environment_skip("this host doesn't deliver loopback multicast"))
        return nodes

    def test_discovery_reports_the_project(self):
        editor = self.editor()
        nodes = self.found()
        self.assertEqual([node.id for node in nodes], [editor.id])
        self.assertEqual((nodes[0].project_name, nodes[0].project_root), ("P", "/P/"))
        self.assertEqual(remote.find_editor(self.discovery, "/elsewhere", 0.5), None)

    def test_command_round_trip_and_split_result(self):
        result = {"ok": True, "asset": {"package": "/Game/Ä", "name": "名" * 30000}}   # Split mid-character.
        editor = self.editor(lambda command: (True, ["noise", remote.RESULT_MARKER + json.dumps(result)]), split=True)
        node = self.found()[0]
        with remote.Connection(self.discovery, node) as connection:
            self.assertEqual(remote.call(connection, "describe", asset="/Game/A"), result)
            self.assertEqual(editor.commands[0]["exec_mode"], remote.EXEC_FILE)
            self.assertIs(editor.commands[0]["unattended"], True)
            with self.assertRaises(remote.RemoteError):
                connection.run("x" * remote.MAX_COMMAND)
            self.assertEqual(len(editor.commands), 1)   # Refused before sending.

    def test_missing_result_raises_with_output(self):
        self.editor(lambda command: (False, ["Traceback: boom"]))
        node = self.found()[0]
        with remote.Connection(self.discovery, node) as connection:
            with self.assertRaises(remote.RemoteError) as caught:
                remote.call(connection, "selection")
        self.assertEqual(caught.exception.output, ["Traceback: boom"])

    def test_unasked_editor_never_connects(self):
        asked, other = self.editor(), self.editor()
        nodes = {node.id: node for node in self.found()}
        if len(nodes) < 2:
            self.skipTest(environment_skip("this host delivers multicast to one socket per port"))
        with remote.Connection(self.discovery, nodes[asked.id]):
            time.sleep(0.2)
            self.assertIsNotNone(asked.tcp)
            self.assertIsNone(other.tcp)


class UnrealScriptTests(unittest.TestCase):
    def test_scripts_parse_as_python_311_and_keep_parameters_as_data(self):
        tricky = {"asset": "/Game/A'\"\n__import__('os')", "source": "C:\\x\\y.fbx", "save": True}
        for name in remote.SCRIPTS:
            with self.subTest(name=name):
                code = remote.script(name, tricky)
                ast.parse(code, feature_version=(3, 11))
                self.assertLess(len(remote.encode("command", "a" * 36, "b" * 36,
                                                  {"command": code, "unattended": True,
                                                   "exec_mode": remote.EXEC_FILE})), remote.MAX_COMMAND)
        namespace = {}
        sys.modules["unreal"] = types.ModuleType("unreal")
        try:
            prelude = remote.script("describe", tricky).split("asset = ", 1)[0]
            exec(compile(prelude, "prelude", "exec"), namespace)
        finally:
            del sys.modules["unreal"]
        self.assertEqual(namespace["P"], tricky)


class ProjectSettingsTests(unittest.TestCase):
    def test_settings_come_from_default_engine_ini(self):
        project = tempfile.mkdtemp()
        self.assertEqual(remote.project_settings(project)["bind"], remote.DEFAULT_BIND)
        os.makedirs(os.path.join(project, "Config"))
        with open(os.path.join(project, "Config", "DefaultEngine.ini"), "w", encoding="utf-8") as handle:
            handle.write("[/Script/Other]\nRemoteExecutionMulticastBindAddress=10.0.0.1\n"
                         "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]\nbRemoteExecution=True\n"
                         "RemoteExecutionMulticastBindAddress=0.0.0.0\n"
                         "RemoteExecutionMulticastGroupEndpoint=239.0.0.2:7000\nRemoteExecutionMulticastTtl=bad\n")
        self.assertEqual(remote.project_settings(project),
                         {"enabled": True, "group": ("239.0.0.2", 7000), "bind": "0.0.0.0", "ttl": 0})


class PluginClientTests(unittest.TestCase):
    """The plugin's client against Angelo's real link server."""

    def setUp(self):
        self.assertEqual(link_client.PROTOCOL, protocol.PROTOCOL)
        self.server = engine_link_server.LinkServer(TOKEN, {"name": "Angelo"})
        self.addCleanup(self.server.close)
        folder = tempfile.mkdtemp()
        self.record = os.path.join(folder, link_client.RECORD_FILE)
        self.received = []

    def client(self, token=TOKEN):
        with open(self.record, "w", encoding="utf-8") as handle:
            json.dump({"protocol": link_client.PROTOCOL, "port": self.server.port, "pid": os.getpid(),
                       "token": token.hex(), "app": ""}, handle)
        return link_client.LinkClient({"unreal": "5.8.2", "project": "/P", "project_name": "P",
                                       "capabilities": ["durable"]},
                                      lambda header, payload: self.received.append((header, payload)), self.record)

    def until(self, client, condition, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            client.poll()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_handshake_request_and_scene(self):
        client = self.client()
        self.assertTrue(client.ensure_connected())
        client.request({"type": "open", "id": "2", "engine": "unreal", "project": "/P", "asset": "/Game/A"})
        inbox = []
        opened = lambda: [item for item in inbox if item[1]["type"] == "open"]
        self.assertTrue(self.until(client, lambda: inbox.extend(self.server.drain()) or opened()))
        self.assertTrue(client.is_ready())
        self.assertEqual([header["type"] for _client, header in inbox], ["connected", "open"])
        self.assertEqual(opened()[0][1]["asset"], "/Game/A")
        self.assertEqual(list(self.server.clients().values())[0],
                         {"engine": "unreal", "version": "5.8.2", "project": "/P", "project_name": "P",
                          "capabilities": ["durable"]})
        self.server.send(opened()[0][0], {"type": "scene", "asset": "/Game/A", "version": 1}, b"glTF" + bytes(100000))
        self.assertTrue(self.until(client, lambda: self.received))
        header, payload = self.received[0]
        self.assertEqual((header["type"], len(payload), payload[:4]), ("scene", 100004, b"glTF"))

    def test_wrong_token_is_refused(self):
        client = self.client(token=bytes(32))
        self.assertTrue(client.ensure_connected())
        self.assertTrue(self.until(client, lambda: client.state == link_client.OFF))
        self.assertFalse(self.server.clients())
        self.assertIn("accept", client.last_error)

    def test_no_record_says_how_to_turn_it_on(self):
        client = link_client.LinkClient({}, lambda *_: None, os.path.join(tempfile.mkdtemp(), "none.json"))
        self.assertFalse(client.ensure_connected())
        self.assertIn("turn on", client.last_error)


class PluginTests(unittest.TestCase):
    def test_descriptor_is_content_only_with_the_python_plugin(self):
        descriptor = json.loads((PLUGIN / "AngeloLink.uplugin").read_text(encoding="utf-8"))
        self.assertNotIn("Modules", descriptor)
        self.assertTrue(descriptor["CanContainContent"])
        self.assertIn({"Name": "PythonScriptPlugin", "Enabled": True}, descriptor["Plugins"])
        self.assertTrue((PLUGIN / "Content/Python/init_unreal.py").is_file())

    def test_sources_are_mit_and_python_311(self):
        files = sorted((PLUGIN / "Content/Python").rglob("*.py"))
        self.assertGreaterEqual(len(files), 5)
        for path in files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("SPDX-License-Identifier: MIT", text)
                ast.parse(text, feature_version=(3, 11))

    def test_record_location_matches_angelo(self):
        self.assertEqual(link_client.record_path({"ANGELO_LINK_RECORD": "/x.json"}, "darwin"), "/x.json")
        self.assertEqual(link_client.record_path({"HOME": "/h"}, "darwin"),
                         "/h/Library/Application Support/Angelo/angelo-link.json")
        self.assertEqual(link_client.record_path({"HOME": "/h", "XDG_STATE_HOME": "/s"}, "linux"),
                         "/s/Angelo/angelo-link.json")

    def test_refusals_and_request(self):
        project = tempfile.mkdtemp()
        inside = os.path.join(project, "SourceArt", "crate.glb")
        mesh = {"name": "SM_Crate", "class": "StaticMesh", "package": "/Game/Props/SM_Crate",
                "path": "/Game/Props/SM_Crate.SM_Crate", "sources": [inside]}
        self.assertIsNone(assets.refusal(mesh, project))
        cases = {"class": dict(mesh, **{"class": "Texture2D"}), "no_source": dict(mesh, sources=[]),
                 "several": dict(mesh, sources=[inside, inside]), "kind": dict(mesh, sources=[inside[:-4] + ".obj"]),
                 "outside": dict(mesh, sources=[os.path.join(tempfile.mkdtemp(), "crate.glb")]),
                 "split": dict(mesh, assets_from_source=3)}
        for code, description in cases.items():
            with self.subTest(code=code):
                self.assertEqual(assets.refusal(description, project)[0], code)
        request = assets.open_request(3, mesh, project, "P", "Crate_2")
        self.assertEqual((request["engine"], request["asset"], request["kind"], request["node"]),
                         ("unreal", "/Game/Props/SM_Crate", ".glb", "Crate_2"))



class AngeloAdapterTests(unittest.TestCase):
    """Angelo's Unreal adapter (``unreal_link_assets``) takes the plugin's own ``open`` request."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp()).resolve()
        (self.project / "Game.uproject").write_text("{}")
        (self.project / "SourceArt").mkdir()
        for name in ("crate.glb", "crate.fbx"):
            (self.project / "SourceArt" / name).write_bytes(b"x")

    def request(self, source, package="/Game/Props/SM_Crate"):
        mesh = {"name": "SM_Crate", "class": "StaticMesh", "package": package, "path": package + ".SM_Crate",
                "sources": [str(source)]}
        return assets.open_request(3, mesh, str(self.project), "Game", "Crate_2")

    def code(self, request):
        with self.assertRaises(unreal_link_assets.AssetError) as caught:
            unreal_link_assets.resolve(request)
        caught.exception.english()
        return caught.exception.code

    def test_the_plugins_request_resolves(self):
        asset = unreal_link_assets.resolve(self.request(self.project / "SourceArt/crate.glb"))
        self.assertEqual((asset.engine, asset.ident, asset.kind), ("unreal", "/Game/Props/SM_Crate", ".glb"))
        self.assertEqual(asset.editable, str(self.project / ".angelo/SourceArt/crate.glb.blend"))
        self.assertEqual(unreal_link_assets.durable(asset, {})[0], "gltf")
        self.assertEqual(unreal_link_assets.CAPABILITIES, ("durable",), "the Python plugin shows no live scenes")

    def test_refusals(self):
        self.assertEqual(self.code(self.request(self.project / "SourceArt/crate.fbx")), "kind",
                         "until the FBX profile is recorded")
        self.assertEqual(self.code(self.request(Path(tempfile.mkdtemp()) / "crate.glb")), "outside")
        self.assertEqual(self.code(self.request(self.project / "SourceArt/none.glb")), "missing")
        self.assertEqual(self.code(self.request(self.project / "SourceArt/crate.glb", package="Game/x")), "package")
        self.assertEqual(self.code(self.request(self.project / "SourceArt/crate.glb", package="/Game/../x")),
                         "package")
        (self.project / "Game.uproject").unlink()
        self.assertEqual(self.code(self.request(self.project / "SourceArt/crate.glb")), "not_project")

    def test_the_plugin_admits_what_angelo_admits(self):
        self.assertEqual(assets.KINDS, unreal_link_assets.KINDS)


if __name__ == "__main__":
    unittest.main()
