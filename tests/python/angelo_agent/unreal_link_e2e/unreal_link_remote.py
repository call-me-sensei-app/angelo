# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""DRAFT, not registered in the add-on: Unreal Engine link phase 0, through the editor's Python remote execution.

doc/angelo/unreal_link.rst is the design; this module is its zero-install fallback and a test harness for the
durable layer. Standard library only, no Blender, no thread of its own: every call blocks the caller, so Angelo
would call it from a worker thread and marshal results to the main thread.

The wire format is the Python Editor Script Plugin's ``remote_execution`` protocol (version 1, magic ``ue_py``),
as Unreal 5.8 implements it in ``PythonScriptRemoteExecution.cpp``:

* **Discovery** (UDP multicast, default group 239.0.0.1:6766 on the 127.0.0.1 interface, TTL 0): Angelo sends
  ``ping``; each editor with remote execution on answers ``pong`` with its user, machine, engine version, engine
  root and, when a project is open, ``project_root`` and ``project_name``.
* **Command channel** (TCP, the editor connects *to us*): Angelo listens on 127.0.0.1, multicasts
  ``open_connection`` with that address, accepts the editor's connection, then sends ``command`` messages and reads
  one ``command_result`` each. The editor runs the command on its game thread from the plugin's tick.

There is no authentication on either channel: any process that can reach the group can run Python in the editor.
That is Unreal's design, not ours; it is why the recommended architecture is an installed Angelo Link plugin that
speaks Angelo's own authenticated protocol (doc/angelo/unreal_link.rst, "Architectures"). The safeguards here are
for Angelo's side only: loopback-only listener and bind address, a peer check on the accepted connection, bounded
messages, and parameters passed as JSON data, never spliced into code.

Messages carry no length prefix. The editor reads whatever bytes are pending on its socket and parses them as one
JSON object, so a command must arrive in one read: ``MAX_COMMAND`` keeps commands small, and file contents travel
as paths. Results are read until one complete JSON object has arrived (Epic's own client stops at the first short
read, which can split a large result).
"""

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
MAGIC = "ue_py"
DEFAULT_GROUP = ("239.0.0.1", 6766)    # UPythonScriptPluginSettings defaults (PythonScriptPluginSettings.cpp).
DEFAULT_BIND = "127.0.0.1"
LOOPBACK = "127.0.0.1"                  # The command channel always listens here, never on a network interface.
DEFAULT_TTL = 0                         # 0: this host only.
MAX_COMMAND = 32 * 1024                 # One read on the editor side; scripts stay well under it.
MAX_RESULT = 16 * 1024 * 1024
EXEC_FILE, EXEC_STATEMENT, EVAL_STATEMENT = "ExecuteFile", "ExecuteStatement", "EvaluateStatement"
RESULT_MARKER = "__ANGELO_UNREAL__"
NODE_FIELDS = ("user", "machine", "engine_version", "engine_root", "project_root", "project_name")


class RemoteError(RuntimeError):
    """The editor couldn't be reached, didn't answer, or the command failed; ``output`` holds its log lines."""

    def __init__(self, message, output=()):
        super().__init__(message)
        self.output = list(output)


def encode(type_, source, dest=None, data=None):
    """One protocol message as UTF-8 JSON bytes."""
    message = {"version": PROTOCOL_VERSION, "magic": MAGIC, "type": type_, "source": source}
    if dest:
        message["dest"] = dest
    if data is not None:
        message["data"] = data
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode(body):
    """The message dict of ``body`` when it is a valid protocol message, else None."""
    try:
        message = json.loads(body.rstrip(b"\0").decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if (not isinstance(message, dict) or message.get("version") != PROTOCOL_VERSION or message.get("magic") != MAGIC
            or not isinstance(message.get("type"), str) or not isinstance(message.get("source"), str)):
        return None
    return message


def for_us(message, node_id):
    """The editor's receive filter, mirrored: not our own echo, and addressed to us or to everyone."""
    return message["source"] != node_id and message.get("dest") in (None, "", node_id)


@dataclass
class Node:
    """One discovered editor."""
    id: str
    info: dict = field(default_factory=dict)
    seen: float = 0.0

    @property
    def project_root(self):
        return self.info.get("project_root", "")

    @property
    def project_name(self):
        return self.info.get("project_name", "")


class Discovery:
    """The UDP side: ping, collect pongs, ask an editor to open or close a command connection."""

    def __init__(self, group=DEFAULT_GROUP, bind=DEFAULT_BIND, ttl=DEFAULT_TTL, node_id=None, receive_any=True):
        """``bind`` is the interface the group is joined and sent on. ``receive_any`` binds the receiving port on every
        address: on macOS (and Linux) a UDP socket bound to 127.0.0.1 never receives datagrams addressed to the
        group, so Epic's client, which binds to ``bind``, hears no pong there (measured on macOS 26, see
        doc/angelo/unreal_link.rst). TTL 0 keeps what is sent on this host either way."""
        self.node_id = node_id or str(uuid.uuid4())
        self.group, self.bind = (str(group[0]), int(group[1])), str(bind)
        self.nodes = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_REUSEPORT", socket.SO_REUSEADDR), 1)
            sock.bind(("" if receive_any else self.bind, self.group[1]))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(ttl))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.bind))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton(self.group[0]) + socket.inet_aton(self.bind))
        except OSError:
            sock.close()
            raise
        self._sock = sock

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send(self, type_, dest=None, data=None):
        self._sock.sendto(encode(type_, self.node_id, dest, data), self.group)

    def poll(self, seconds):
        """Read discovery traffic for ``seconds``; returns the nodes heard from so far, by id."""
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return dict(self.nodes)
            self._sock.settimeout(min(left, 0.1))
            try:
                body = self._sock.recv(65535)
            except socket.timeout:
                continue
            message = decode(body)
            if message is None or message["type"] != "pong" or not for_us(message, self.node_id):
                continue
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            info = {key: str(data[key])[:1024] for key in NODE_FIELDS if key in data}
            self.nodes[message["source"]] = Node(message["source"], info, time.monotonic())

    def discover(self, seconds=1.5, pings=3):
        """Ping ``pings`` times over ``seconds`` and return the editors that answered."""
        nodes = {}
        for _ in range(max(1, pings)):
            self.send("ping")
            nodes = self.poll(seconds / max(1, pings))
        return list(nodes.values())


SETTINGS_SECTION = "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]"


def project_settings(project_root):
    """{"enabled", "group", "bind", "ttl"} from the project's Config/DefaultEngine.ini, where Project Settings >
    Plugins > Python writes them (UPythonScriptPluginSettings is ``config=Engine, defaultconfig``). Discovery must
    join and send on the same interface as the editor: an editor set to 0.0.0.0 joins on the default interface, and
    Angelo's pings sent on 127.0.0.1 never reach it. Unset keys keep the plugin's defaults; use them as
    ``Discovery(settings["group"], settings["bind"], settings["ttl"])``."""
    settings = {"enabled": False, "group": DEFAULT_GROUP, "bind": DEFAULT_BIND, "ttl": DEFAULT_TTL}
    section = None
    try:
        with open(os.path.join(project_root, "Config", "DefaultEngine.ini"), encoding="utf-8-sig") as handle:
            lines = handle.read(1 << 20).splitlines()
    except (OSError, UnicodeError):
        return settings
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        if section != SETTINGS_SECTION or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        try:
            if key == "bRemoteExecution":
                settings["enabled"] = value.lower() == "true"
            elif key == "RemoteExecutionMulticastGroupEndpoint":
                host, port = value.rsplit(":", 1)
                socket.inet_aton(host)
                settings["group"] = (host, int(port))
            elif key == "RemoteExecutionMulticastBindAddress":
                socket.inet_aton(value)
                settings["bind"] = value
            elif key == "RemoteExecutionMulticastTtl":
                settings["ttl"] = max(0, min(255, int(value)))
        except (OSError, ValueError):
            continue   # Malformed: the editor disables remote execution then; discovery simply finds nothing.
    return settings


def find_editor(discovery, project_root=None, seconds=1.5):
    """The editor of the project at ``project_root`` (any editor with a project when None), or None."""
    wanted = os.path.normcase(os.path.realpath(project_root)) if project_root else None
    for node in discovery.discover(seconds):
        if not node.project_root:
            continue
        if wanted is None or os.path.normcase(os.path.realpath(node.project_root)) == wanted:
            return node
    return None


class Connection:
    """A command connection to one editor. ``run`` sends a command and returns its ``command_result`` data."""

    def __init__(self, discovery, node, accept_seconds=6.0):
        self.discovery, self.node = discovery, node
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._channel, self._pending = None, b""
        try:
            self._listener.bind((LOOPBACK, 0))   # Loopback whatever the multicast interface; a free port (Epic's
            #                                     fixed 6776 collides between clients).
            self._listener.listen(1)
            self._accept(accept_seconds)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _accept(self, seconds):
        ip, port = self._listener.getsockname()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.discovery.send("open_connection", self.node.id, {"command_ip": ip, "command_port": port})
            self._listener.settimeout(min(1.0, max(0.05, deadline - time.monotonic())))
            try:
                channel, address = self._listener.accept()
            except socket.timeout:
                continue
            if address[0] != LOOPBACK:   # Only a local editor.
                channel.close()
                continue
            channel.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._channel = channel
            return
        raise RemoteError("The Unreal Editor didn't open a command connection (is it busy or throttled?)")

    def run(self, command, exec_mode=EXEC_FILE, unattended=True, timeout=120.0):
        """Run ``command`` in the editor; returns {"success", "command", "result", "output": [{"type", "output"}]}."""
        body = encode("command", self.discovery.node_id, self.node.id,
                      {"command": command, "unattended": bool(unattended), "exec_mode": exec_mode})
        if len(body) > MAX_COMMAND:
            raise RemoteError("The command is %d bytes; the editor reads at most %d at once" % (len(body), MAX_COMMAND))
        self._channel.settimeout(timeout)
        self._channel.sendall(body)
        message = self._receive(time.monotonic() + timeout)
        data = message.get("data")
        if message["type"] != "command_result" or not isinstance(data, dict):
            raise RemoteError("The Unreal Editor sent an unexpected reply")
        return data

    def _receive(self, deadline):
        """The next complete message addressed to us; bytes after it (another message) are kept for the next one."""
        decoder = json.JSONDecoder()
        while True:
            text = self._pending.lstrip(b" \t\r\n\0")
            try:
                obj, end = decoder.raw_decode(text.decode("utf-8"))
            except (UnicodeError, ValueError):
                obj = None   # Not complete yet (a split UTF-8 sequence or a partial object).
            if obj is not None:
                self._pending = text.decode("utf-8")[end:].encode("utf-8")
                message = decode(json.dumps(obj).encode("utf-8"))
                if message is not None and for_us(message, self.discovery.node_id):
                    return message
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                raise RemoteError("The Unreal Editor didn't answer in time")
            self._channel.settimeout(left)
            try:
                part = self._channel.recv(65536)
            except socket.timeout:
                continue
            if not part:
                raise RemoteError("The Unreal Editor closed the command connection")
            self._pending += part
            if len(self._pending) > MAX_RESULT:
                raise RemoteError("The Unreal Editor's reply exceeds %d bytes" % MAX_RESULT)

    def close(self):
        if self._channel is not None:
            try:
                self.discovery.send("close_connection", self.node.id)
            except OSError:
                pass
            self._channel.close()
            self._channel = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None


# The Unreal side ---------------------------------------------------------------------------------------------------
# Each script reads its parameters from JSON (never from formatted code) and prints one RESULT_MARKER line.

_PRELUDE = '''import json, unreal
P = json.loads(%r)
def _out(value):
    print("%s" + json.dumps(value))
def _sources(asset):
    try:
        data = asset.get_editor_property("asset_import_data")
    except Exception:
        data = None
    return data, (list(data.extract_filenames()) if data is not None else [])
def _describe(asset):
    data, files = _sources(asset)
    return {"path": asset.get_path_name(), "package": asset.get_outermost().get_name(), "name": asset.get_name(),
            "class": asset.get_class().get_name(), "sources": files,
            "interchange": data is not None and isinstance(data, getattr(unreal, "InterchangeAssetImportData", ()))}
'''

SCRIPTS = {
    # The assets selected in the Content Browser, and the mesh assets of the actors selected in the level.
    "selection": '''
assets = [_describe(a) for a in unreal.EditorUtilityLibrary.get_selected_assets()]
actors = []
for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors():
    meshes = [c.static_mesh for c in actor.get_components_by_class(unreal.StaticMeshComponent) if c.static_mesh]
    meshes += [c.get_skeletal_mesh_asset() for c in actor.get_components_by_class(unreal.SkeletalMeshComponent)
               if c.get_skeletal_mesh_asset()]
    actors.append({"actor": actor.get_actor_label(), "path": actor.get_path_name(),
                   "assets": [_describe(m) for m in meshes]})
_out({"ok": True, "assets": assets, "actors": actors})
''',
    # One asset by object path (/Game/Props/SM_Crate.SM_Crate) or package path.
    "describe": '''
asset = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).load_asset(P["asset"])
_out({"ok": True, "asset": _describe(asset)} if asset else {"ok": False, "code": "missing"})
''',
    # Re-import an asset from its source file after Angelo wrote it; keeps the asset's own import settings.
    "reimport": '''
subsystem = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
asset = subsystem.load_asset(P["asset"])
if not asset:
    _out({"ok": False, "code": "missing"})
else:
    data, files = _sources(asset)
    if not files:
        _out({"ok": False, "code": "no_source"})
    elif P.get("source") and unreal.Paths.convert_relative_path_to_full(files[0]).lower() != \\
            unreal.Paths.convert_relative_path_to_full(P["source"]).lower():
        _out({"ok": False, "code": "other_source", "sources": files})
    else:
        if isinstance(data, getattr(unreal, "InterchangeAssetImportData", ())):
            parameters = unreal.ImportAssetParameters()
            parameters.is_automated = True
            done = unreal.InterchangeManager.get_interchange_manager_scripted().reimport_asset(asset, parameters)
            done = done[0] if isinstance(done, tuple) else done
            route = "interchange"
        else:
            task = unreal.AssetImportTask()
            task.set_editor_property("filename", files[0])
            task.set_editor_property("destination_path", asset.get_outermost().get_name().rsplit("/", 1)[0])
            task.set_editor_property("destination_name", asset.get_name())
            for key, value in (("replace_existing", True), ("replace_existing_settings", False),
                               ("automated", True), ("save", False)):
                task.set_editor_property(key, value)
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            done = bool(task.get_editor_property("imported_object_paths"))
            route = "import_task"
        saved = bool(done and P.get("save") and subsystem.save_loaded_asset(asset, True))
        _out({"ok": bool(done), "code": "" if done else "reimport_failed", "route": route, "saved": saved,
              "asset": _describe(asset)})
''',
    # Import a new source file (Angelo's first write of a new asset) into a /Game folder.
    "import": '''
task = unreal.AssetImportTask()
for key, value in (("filename", P["source"]), ("destination_path", P["folder"]), ("destination_name", P.get("name", "")),
                   ("replace_existing", False), ("automated", True), ("save", bool(P.get("save")))):
    task.set_editor_property(key, value)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
paths = list(task.get_editor_property("imported_object_paths"))
_out({"ok": bool(paths), "code": "" if paths else "import_failed", "paths": paths})
''',
}


def script(name, params):
    """The Python the editor runs for ``name`` with ``params`` (JSON data embedded as a string literal)."""
    return _PRELUDE % (json.dumps(params, ensure_ascii=True), RESULT_MARKER) + SCRIPTS[name]


def call(connection, name, timeout=300.0, **params):
    """Run the named Unreal-side script; returns its result dict. Raises RemoteError with the editor's output."""
    data = connection.run(script(name, params), EXEC_FILE, True, timeout)
    output = data.get("output") if isinstance(data.get("output"), list) else []
    lines = [str(entry.get("output", "")) for entry in output if isinstance(entry, dict)]
    for line in reversed(lines):
        marker = line.find(RESULT_MARKER)
        if marker >= 0:
            try:
                result = json.loads(line[marker + len(RESULT_MARKER):])
            except ValueError:
                break
            if isinstance(result, dict):
                return result
    raise RemoteError("The Unreal Editor ran %s without a result (success=%s)" % (name, data.get("success")), lines)
