# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host authority adapter for task notes; no bpy, network or billing calls.

Only an exact live, manually approved memory ToolCall may write notes. Evidence
comes from real user attachments and the controller's completed invocation
journal, never assistant text, saved memory, or model-supplied approval flags.
"""

import hashlib
import ipaddress
import json
import re
import time
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit

from .chat_settings import clean_options
from .files import MAX_FILE
from .legacy_names import current_tool_name
from .memory_spec import MAX_POINTER, pointer, text, validate_memory
from .request_rows import person_request
from .task_memory import admit_evidence, read_memory, scope_token, update_memory

MAX_MESSAGES = 4096
MAX_RECEIPTS = 256
_URL = re.compile(r"https://[^\s<>\"'`\[\]{}]+", re.IGNORECASE)
_ASSET_TOOLS = frozenset({"angelo_studio_generate", "angelo_studio_import_image", "angelo_studio_import_3d"})
_FILE_TOOLS = frozenset({"angelo_write_file", "angelo_studio_export"})


def _canonical(name, args):
    payload = json.dumps({"tool": name, "arguments": args}, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _profile(controller):
    return str(controller.store.directory.resolve())


def _bound_document(controller):
    if controller is None or getattr(controller, "document", None) is None or getattr(controller, "document_invalid", False):
        raise ValueError("Memory tools require this chat's valid bound document")
    return controller.document


def _public_arguments(controller, args):
    key = getattr(controller, "api_key", "")
    if not isinstance(key, str) or not key:
        return

    def contains(value):
        if isinstance(value, str):
            return key in value
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return False
    if contains(args):
        raise ValueError("Task memory cannot contain this account's runtime credential")


def _public_memory(controller, memory):
    key = getattr(controller, "api_key", "")
    if not isinstance(key, str) or not key:
        return memory

    def redact(value):
        if isinstance(value, str):
            return value.replace(key, "[REDACTED]")
        if isinstance(value, dict):
            return {name: redact(item) for name, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value
    return redact(memory)


def _live_call(controller, args, executing):
    # Import lazily: the controller itself imports task-memory domain helpers.
    from .controller import ToolCall
    if (not controller.busy or controller.cancelled or controller._closing
            or controller.document_invalid or clean_options(controller.state.get("options"))["approval"] == "READ_ONLY"):
        raise ValueError("No active admitted memory operation belongs to this document")
    digest = _canonical("angelo_memory_update", args)
    matches = []
    for key, call in islice(controller.pending.items(), 32):
        if not isinstance(call, ToolCall) or key != call.id or call.name != "angelo_memory_update":
            continue
        try:
            same_args = validate_memory(call.name, call.arguments) == args and _canonical(call.name, call.arguments) == digest
        except (ValueError, TypeError, UnicodeError):
            continue
        if (not same_args or call.invocation_digest != digest or call.document != controller.document
                or call.epoch != controller._epoch or call.generation != controller._request_generation
                or time.monotonic() >= call.expires):
            continue
        marker = controller.state.get("results", {}).get(call.id)
        if (not isinstance(marker, dict) or marker.get("invocation_digest") != digest
                or marker.get("document") != repr(controller.document)
                or marker.get("phase") not in ({"execution_started"} if executing else {"queued", "execution_started"})):
            continue
        if executing and (call.executing is not True or call.approval is not True or call.automatic is not False):
            continue
        matches.append(call)
    if not matches or executing and len(matches) != 1:
        raise ValueError("Saving memory requires the exact currently executing, individually approved tool call")
    return matches[0]


def _fingerprint(path, maximum=MAX_FILE):
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > maximum:
            raise ValueError("Memory evidence requires a regular scoped file within its existing host size limit")
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except OSError:
        raise ValueError("Memory evidence is no longer available; attach or approve it again") from None


def _file_hash(path):
    digest, remaining = hashlib.sha256(), MAX_FILE + 1
    try:
        with path.open("rb") as handle:
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError:
        raise ValueError("Memory evidence is no longer available") from None
    raise ValueError("Memory evidence exceeds the 20 MiB limit")


def _snapshot(controller, args):
    document = _bound_document(controller)
    memory = read_memory(controller.state, document, profile_id=_profile(controller))
    stored_scope = (controller.state.get("task_memory", {}).get("scope") or {}).get("document")
    if memory["scope_mismatch"] and stored_scope is not None:
        raise ValueError("This brief belongs to another document; start a new chat")
    if args["expected_revision"] != memory["revision"]:
        raise ValueError("Task memory changed; read its current revision and review a new exact patch")
    return memory


class _Evidence:
    """One bounded, current host-evidence lookup per preflight/execution."""

    def __init__(self, controller):
        self.controller = controller
        self.profile, self.document = _profile(controller), _bound_document(controller)
        self.messages = controller.state.get("messages", [])[-MAX_MESSAGES:]
        self.first_index = max(0, len(controller.state.get("messages", [])) - MAX_MESSAGES)
        self.cache, self.signatures, self.hashes = {}, {}, {}
        self.library = None

    def _receipt(self, source, identity, origin):
        return admit_evidence(source, identity, self.profile, self.document, origin)

    def attachments(self):
        # Only actual user metadata, not assistant observations or preview PNGs.
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if not person_request(message):   # The person's own rows: never a request Angelo made (EVENT M-3).
                continue
            records = message.get("references", [])
            if isinstance(records, list):
                for record in records[:4]:
                    if isinstance(record, dict):
                        yield record, {"kind": "user_message", "id": "message:" + str(self.first_index + index)}
        for record in list(getattr(self.controller, "turn_attachments", ()))[:4]:
            if isinstance(record, dict):
                yield record, {"kind": "user_message", "id": "turn:attachment:" + str(record.get("id", ""))}

    def attachment_path(self, record):
        identity = pointer("ATTACHMENT", record.get("id"))
        path = self.controller.references.checked_path(record)
        if path.stem != identity:
            raise ValueError("The attached snapshot does not match its real reference ID")
        _fingerprint(path)
        return path

    def receipts(self, tools):
        # Chats saved before the tool rename record the pre-rename names; their receipts still count.
        activities = {message.get("call_id"): message for message in self.messages if isinstance(message, dict)
                      and message.get("role") == "activity" and current_tool_name(message.get("tool")) in tools}
        journal = self.controller.state.get("results", {})
        if not isinstance(journal, dict):
            return
        for identity, entry in islice(reversed(journal.items()), MAX_RECEIPTS):
            message = activities.get(identity)
            if (not isinstance(entry, dict) or message is None or entry.get("phase") != "completed"
                    or entry.get("document") != repr(self.document) or message.get("status") != "completed"
                    or message.get("reverted") or entry.get("reverted")
                    or entry.get("approved") is False or message.get("approved") is False
                    or entry.get("automatic") or message.get("automatic")):
                continue
            recorded, args, result = message["tool"], message.get("arguments"), entry.get("result")
            tool = current_tool_name(recorded)
            if (not isinstance(args, dict) or not isinstance(result, dict) or result.get("error")
                    or result.get("success") is False or result.get("partial_changes_possible")
                    or result.get("serialization_failed") or result.get("uncertain")):
                continue
            try:
                text(identity, MAX_POINTER, 1)
                from .tool_specs import validate_call
                checked = validate_call(tool, args)
                # The journal digest was computed with the name the call actually used.
                if entry.get("invocation_digest") != _canonical(recorded, checked):
                    continue
            except (ValueError, TypeError, KeyError, UnicodeError):
                continue
            # These exact tools are individually reviewed (not Auto bounded).
            # A completed journal + matching invocation/activity is the actual
            # host approval/execution receipt, not a model's 'approved' boolean.
            yield tool, checked, result, {"kind": "approved_update", "id": identity}

    def _asset(self, identity):
        origin, approved_args, approved_result, approved_tool = None, None, None, None
        for tool, args, result, candidate in self.receipts(_ASSET_TOOLS):
            if tool == "angelo_studio_import_3d":
                matches = args.get("asset_id") == identity and result.get("success") is True
            else:
                matches = result.get("id", result.get("asset_id")) == identity and result.get("status") == "ready"
                if tool == "angelo_studio_generate":
                    matches &= (result.get("connection") == args["connection_id"]
                                and result.get("model_id", args["model"]) == args["model"])
            if matches:
                origin, approved_args, approved_result, approved_tool = candidate, args, result, tool
                break
        if origin is None:
            raise ValueError("This library asset has no completed individually approved generation/import in this chat")
        if self.library is None:
            from .studio_state import StudioLibrary
            service = getattr(self.controller, "media_service", None)
            self.library = getattr(service, "library", None)
            if not isinstance(self.library, StudioLibrary):
                self.library = StudioLibrary()
            else:
                self.library.reload()  # Local metadata only; never starts/polls a provider job.
        asset = self.library.assets.get(identity)
        if (not isinstance(asset, dict) or asset.get("id") != identity or asset.get("status") != "ready"
                or asset.get("kind") not in {"IMAGE", "3D"} or not isinstance(asset.get("files"), list)
                or not 1 <= len(asset["files"]) <= 4):
            raise ValueError("The approved library asset is no longer ready or available")
        if (approved_tool == "angelo_studio_import_3d" and asset["kind"] != "3D"
                or approved_tool == "angelo_studio_generate" and asset.get("connection") != approved_args["connection_id"]
                or any(key in approved_result and asset.get(key) != approved_result[key]
                       for key in ("kind", "connection", "model_id"))):
            raise ValueError("The current library asset no longer matches its approved host receipt")
        signatures = []
        from .studio_generation import MAX_IMAGE, MAX_MODEL
        maximum = MAX_IMAGE if asset["kind"] == "IMAGE" else MAX_MODEL
        for relative in asset["files"]:
            if not isinstance(relative, str) or relative.startswith("."):
                raise ValueError("The approved library output is invalid")
            signatures.append((relative, _fingerprint(self.library.checked_file(asset, relative), maximum)))
        self.signatures[("ASSET", identity)] = (origin["id"], tuple(signatures))
        return self._receipt("ASSET", identity, origin)

    def _workspace(self, identity):
        path = self.controller.files.path(identity)
        signature = _fingerprint(path)
        for tool, args, result, origin in self.receipts(_FILE_TOOLS):
            if args.get("path") != identity or result.get("path") != identity or result.get("bytes") != signature[1]:
                continue
            if tool == "angelo_write_file":
                if result.get("success") is not True or result.get("journaled") is not True:
                    continue
                expected = hashlib.sha256(args["content"].encode("utf-8")).hexdigest()
                if self.hash(path) != expected:
                    continue
            elif result.get("asset_id") != args.get("asset_id"):
                continue
            self.signatures[("WORKSPACE", identity)] = (origin["id"], signature)
            return self._receipt("WORKSPACE", identity, origin)
        # A selected attachment may have originated from the workspace; its
        # private snapshot and this current file must actually match, not just
        # share a basename. Only hashes are compared; file text is never exposed.
        for record, origin in self.attachments():
            if record.get("name") != path.name:
                continue
            try:
                attached = self.attachment_path(record)
                if _fingerprint(attached)[1] != signature[1] or self.hash(attached) != self.hash(path):
                    continue
            except (ValueError, OSError):
                continue
            self.signatures[("WORKSPACE", identity)] = (origin["id"], signature, self.hash(path))
            return self._receipt("WORKSPACE", identity, origin)
        raise ValueError("This workspace file lacks a real matching user attachment or completed approved file-write receipt")

    def hash(self, path):
        if path not in self.hashes:
            self.hashes[path] = _file_hash(path)
        return self.hashes[path]

    def _url(self, identity):
        host = urlsplit(identity).hostname
        try:
            private = not ipaddress.ip_address(host).is_global
        except ValueError:
            private = host == "localhost" or host.endswith(".localhost")
        if private:
            raise ValueError("Use a public literal HTTPS reference, not a local/private address")
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            value = message.get("text") if person_request(message) else None
            if not isinstance(value, str) or identity not in value[:32000]:
                continue
            tokens = (match.group().rstrip(").,;!") for match in _URL.finditer(value[:32000]))
            if identity not in tokens:
                continue
            origin = {"kind": "user_message", "id": "message:" + str(self.first_index + index)}
            self.signatures[("URL", identity)] = (origin["id"], identity)
            return self._receipt("URL", identity, origin)
        raise ValueError("This URL is not a literal reference in an actual user message; memory never fetches URLs")

    def resolve(self, source, identity):
        key = (source, pointer(source, identity))
        if key in self.cache:
            return self.cache[key]
        if source == "ATTACHMENT":
            for record, origin in self.attachments():
                if record.get("id") != key[1]:
                    continue
                path = self.attachment_path(record)
                self.signatures[key] = (origin["id"], _fingerprint(path))
                self.cache[key] = self._receipt(source, key[1], origin)
                return self.cache[key]
            raise ValueError("This reference was not actually attached by the user in this chat")
        receipt = {"ASSET": self._asset, "WORKSPACE": self._workspace, "URL": self._url}[source](key[1])
        self.cache[key] = receipt
        return receipt


def _prepare(controller, args):
    memory = _snapshot(controller, args)
    evidence = _Evidence(controller)
    for field in ("references", "assets"):
        for entry in args["patch"].get(field, []):
            evidence.resolve(entry["source"], entry["id"])
    signature = ("memory-v1", scope_token(_profile(controller)), scope_token(controller.document),
                 memory["revision"], memory["generation"], tuple(sorted(evidence.signatures.items())))
    return evidence, signature


def precondition(controller, name, arguments):
    """Capture exact revision/source availability before the manual review."""
    args = validate_memory(name, arguments)
    _bound_document(controller)
    with controller.lock:
        _bound_document(controller)
        if name == "angelo_memory_read":
            return None
        _public_arguments(controller, args)
        _live_call(controller, args, executing=False)
        _, signature = _prepare(controller, args)
        return signature


def execute(controller, name, arguments):
    """Read notes, or save an exact executing manual approval atomically.

    Return only public memory + success. Notes are not scene changes and have
    no Blender Undo claim. Failed persistence restores the in-memory state.
    """
    args = validate_memory(name, arguments)
    _bound_document(controller)
    with controller.lock:
        document = _bound_document(controller)
        if name == "angelo_memory_read":
            return _public_memory(controller, read_memory(controller.state, document, profile_id=_profile(controller)))
        _public_arguments(controller, args)
        call = _live_call(controller, args, executing=True)
        evidence, signature = _prepare(controller, args)
        if call.precondition is not None and call.precondition != signature:
            raise ValueError("Memory/source state changed after review; inspect and approve a new exact patch")
        previous = controller.state
        updated = update_memory(previous, args, document,
            provenance={"kind": "approved_update", "id": call.id}, evidence=evidence.resolve, profile_id=_profile(controller))
        controller.state = updated
        try:
            controller.save()
        except (OSError, ValueError, TypeError, UnicodeError):
            controller.state = previous
            raise ValueError("Task memory could not be saved; no memory update was retained") from None
        controller.version += 1
        return {"success": True, "memory": _public_memory(controller, read_memory(updated, document, profile_id=_profile(controller)))}
