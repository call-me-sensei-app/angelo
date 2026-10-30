# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
#
# The connection to Angelo: reads Angelo's endpoint record, connects over loopback, proves the record's token without
# sending it (HMAC-SHA256 both ways) and exchanges framed messages. Angelo's side is engine_link_protocol.py; the
# format is: a 4-byte big-endian length, a JSON header, then header.bytes bytes of payload (a .glb).
#
# Statically typed throughout, so a project that raises GDScript warnings to errors (including for addons) loads it.
@tool
extends RefCounted

signal message(header: Dictionary, payload: PackedByteArray)
signal state_changed(state: String)

const PROTOCOL: String = "angelo-link/1"
const MAX_HEADER: int = 64 * 1024
const MAX_PAYLOAD: int = 512 * 1024 * 1024
const PING_SECONDS: float = 10.0
## Nothing heard from Angelo for this long means it stopped answering (frozen or suspended): it answers every 10 s
## ping, and this leaves room for a long operation that holds up its answers.
const SILENT_SECONDS: float = 60.0
const AUTH_SECONDS: float = 10.0
const STATE_NAMES: Array[String] = ["off", "connecting", "authenticating", "ready"]

enum State { OFF, CONNECTING, AUTHENTICATING, READY }

var state: State = State.OFF
var app_path: String = ""        # Angelo's application, from the record: brought to the front on a request.
var last_error: String = ""      # Why the connection failed or was lost (English, for the person).
var _peer: StreamPeerTCP = null
var _buffer: PackedByteArray = PackedByteArray()
var _header: Dictionary = {}
var _token: PackedByteArray = PackedByteArray()
var _server_nonce: String = ""
var _client_nonce: String = ""
var _client_info: Dictionary = {}
var _since: float = 0.0
var _heard: float = 0.0          # When the last frame from Angelo arrived.
var _queue: Array[Dictionary] = []   # Requests made before the connection is ready.


static func record_path() -> String:
	var override: String = OS.get_environment("ANGELO_LINK_RECORD")
	if override != "":
		return override
	match OS.get_name():
		"macOS":
			return OS.get_environment("HOME").path_join("Library/Application Support/Angelo/angelo-link.json")
		"Windows":
			var local: String = OS.get_environment("LOCALAPPDATA")
			if local == "":
				local = OS.get_environment("USERPROFILE").path_join("AppData/Local")
			return local.path_join("Angelo/angelo-link.json")
		_:
			var base: String = OS.get_environment("XDG_STATE_HOME")
			if base == "":
				base = OS.get_environment("HOME").path_join(".local/state")
			return base.path_join("Angelo/angelo-link.json")


## Angelo's endpoint record, or {} when Angelo isn't listening or the record isn't one this plugin understands.
static func read_record() -> Dictionary:
	if not FileAccess.file_exists(record_path()):
		return {}
	var file: FileAccess = FileAccess.open(record_path(), FileAccess.READ)
	if file == null:
		return {}
	var parsed: Variant = JSON.parse_string(file.get_as_text())
	file.close()
	if not (parsed is Dictionary):
		return {}
	var record: Dictionary = parsed
	if text_of(record.get("protocol")) != PROTOCOL or text_of(record.get("token")).length() != 64 \
			or int_of(record.get("port")) <= 0:
		return {}
	return record


## A header value as text ("" when it is missing or not text): Angelo's frames are data, never trusted for type.
static func text_of(value: Variant) -> String:
	return value if value is String else ""


## A header value as a whole number (0 when it is missing or not a number; JSON numbers may arrive as floats).
static func int_of(value: Variant) -> int:
	if value is int:
		var whole: int = value
		return whole
	if value is float:
		var number: float = value
		return int(number)
	return 0


func _init(client_info: Dictionary) -> void:
	_client_info = client_info


func is_ready() -> bool:
	return state == State.READY


## Connect when not connected; false (with last_error) when Angelo isn't listening.
func ensure_connected() -> bool:
	if state != State.OFF:
		return true
	var record: Dictionary = read_record()
	if record.is_empty():
		last_error = ("Angelo isn't listening for Godot. In Angelo, turn on Godot Live Link from the + menu in the "
				+ "chat bar.")
		return false
	_token = text_of(record.get("token")).hex_decode()
	app_path = text_of(record.get("app"))
	_server_nonce = ""
	_client_nonce = ""
	_peer = StreamPeerTCP.new()
	var error: Error = _peer.connect_to_host("127.0.0.1", int_of(record.get("port")))
	if error != OK:
		last_error = "Couldn't reach Angelo. Is it still open?"
		_peer = null
		return false
	last_error = ""
	_set_state(State.CONNECTING)
	_since = _now()
	return true


## Send a request now, or once the connection is ready.
func request(header: Dictionary) -> void:
	if state == State.READY:
		_send(header)
	else:
		_queue.append(header)


func close() -> void:
	if _peer != null:
		_peer.disconnect_from_host()
	_peer = null
	_buffer = PackedByteArray()
	_header = {}
	_queue.clear()
	if state != State.OFF:
		_set_state(State.OFF)


## Call every frame: connects, authenticates, reads frames and pings.
func poll() -> void:
	if _peer == null:
		return
	var polled: Error = _peer.poll()
	var status: StreamPeerTCP.Status = _peer.get_status()
	var now: float = _now()
	if polled != OK or status == StreamPeerTCP.STATUS_ERROR or status == StreamPeerTCP.STATUS_NONE:
		_lost()
		return
	if status == StreamPeerTCP.STATUS_CONNECTING:
		if now - _since > AUTH_SECONDS:
			_fail("Angelo didn't answer.")
		return
	if state == State.CONNECTING:
		_set_state(State.AUTHENTICATING)
	var available: int = _peer.get_available_bytes()
	if available > 0:
		var got: Array = _peer.get_partial_data(available)
		var code: int = int_of(got[0])
		if code == OK:
			var chunk: PackedByteArray = got[1]
			_buffer.append_array(chunk)
	while _peer != null and _take_frame():
		pass
	if _peer == null:
		return
	if state == State.AUTHENTICATING and now - _since > AUTH_SECONDS:
		_fail("Angelo didn't answer.")
	elif state == State.READY and now - _heard > SILENT_SECONDS:
		_fail("Angelo stopped answering.")   # A frozen or suspended Angelo: the socket alone would never say so.
	elif state == State.READY and now - _since > PING_SECONDS:
		_since = now
		_send({"type": "ping"})


## The socket closed or failed: Angelo quit, crashed or turned the link off.
func _lost() -> void:
	last_error = ("Lost the connection to Angelo." if state == State.READY
			else "Couldn't reach Angelo. Is it still open?")
	close()


func _take_frame() -> bool:
	if _header.is_empty():
		if _buffer.size() < 4:
			return false
		var size: int = (_buffer[0] << 24) | (_buffer[1] << 16) | (_buffer[2] << 8) | _buffer[3]
		if size <= 0 or size > MAX_HEADER:
			_fail("Angelo sent a frame this plugin can't read.")
			return false
		if _buffer.size() < 4 + size:
			return false
		var parsed: Variant = JSON.parse_string(_buffer.slice(4, 4 + size).get_string_from_utf8())
		if not (parsed is Dictionary):
			_fail("Angelo sent a frame this plugin can't read.")
			return false
		var header: Dictionary = parsed
		var length: int = int_of(header.get("bytes", 0))
		if text_of(header.get("type")) == "" or length < 0 or length > MAX_PAYLOAD:
			_fail("Angelo sent a frame this plugin can't read.")
			return false
		_header = header
		_buffer = _buffer.slice(4 + size)
	var bytes: int = int_of(_header.get("bytes", 0))
	if _buffer.size() < bytes:
		return false
	var payload: PackedByteArray = _buffer.slice(0, bytes)
	_buffer = _buffer.slice(bytes)
	var complete: Dictionary = _header
	_header = {}
	_receive(complete, payload)
	return true


func _fail(reason: String) -> void:
	last_error = reason
	close()


func _receive(header: Dictionary, payload: PackedByteArray) -> void:
	_heard = _now()
	var kind: String = text_of(header.get("type"))
	if state == State.AUTHENTICATING:
		if kind == "hello" and text_of(header.get("protocol")) == PROTOCOL and _valid_nonce(header.get("nonce")):
			_server_nonce = text_of(header.get("nonce"))
			_client_nonce = Crypto.new().generate_random_bytes(32).hex_encode()
			_send({"type": "auth", "nonce": _client_nonce, "proof": _proof(PROTOCOL + "/client"),
					"client": _client_info})
		elif kind == "welcome" and _server_nonce != "" and text_of(header.get("proof")) == _proof(PROTOCOL + "/server"):
			_since = _now()
			_set_state(State.READY)
			var queued: Array[Dictionary] = _queue.duplicate()
			_queue.clear()
			for item: Dictionary in queued:
				_send(item)
		else:
			_fail("Angelo didn't accept this connection. Turn Godot Live Link off and on in Angelo."
					if kind == "error" else "Something else answered on Angelo's port.")
		return
	if kind == "pong":
		return
	message.emit(header, payload)


func _valid_nonce(value: Variant) -> bool:
	var text: String = text_of(value)
	return text.length() == 64 and text.is_valid_hex_number()


func _proof(context: String) -> String:
	var text: String = "%s\n%s\n%s" % [context, _server_nonce, _client_nonce]
	return Crypto.new().hmac_digest(HashingContext.HASH_SHA256, _token, text.to_ascii_buffer()).hex_encode()


func _send(header: Dictionary) -> void:
	if _peer == null:
		return
	var body: PackedByteArray = JSON.stringify(header).to_utf8_buffer()
	var frame: PackedByteArray = PackedByteArray([(body.size() >> 24) & 255, (body.size() >> 16) & 255,
			(body.size() >> 8) & 255, body.size() & 255])
	frame.append_array(body)
	if _peer.put_data(frame) != OK:
		_lost()


func _set_state(value: State) -> void:
	state = value
	state_changed.emit(STATE_NAMES[value])


static func _now() -> float:
	return Time.get_ticks_msec() / 1000.0
