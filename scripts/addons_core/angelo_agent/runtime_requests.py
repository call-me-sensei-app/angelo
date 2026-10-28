# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The model runtime's own requests to its client in a worker chat: who answers each, and what an answer may be.

A worker chat's model runtime child runs in Angelo with Angelo's credentials (``runtime_service``), so an answer
to one of its requests acts with them. Angelo answers every request that grants or asks for anything itself and never
relays it (``runtime_filter``); the worker answers only the requests that carry its chat's own tools:

* **Codex** (the app-server's server requests, ``ServerRequest`` in its protocol schema): command, file-change and
  MCP-elicitation requests are declined here, as Angelo's providers decline them; permission, user-input, sign-in
  token, attestation and clock requests, the legacy ``execCommandApproval`` / ``applyPatchApproval`` and any request
  this list does not know are answered with the providers' ``-32601`` error. Only ``item/tool/call`` (Angelo's own
  dynamic tools) is relayed, and its answer must be ``{success, contentItems}`` of text, image or audio items, or an
  error; a picture or sound is inline (a ``data:image/`` or ``data:audio/`` URL, as ``codex_result_images`` makes),
  never a ``file://`` or web URL the child would read.
* **Claude** (Claude Code's control requests to its SDK client): ``can_use_tool`` (a permission prompt; Angelo's
  sessions run in ``dontAsk`` with their tools allowed up front, so none needs one) is denied and ``elicitation``
  declined here; hook callbacks, token refreshes, device hooks, dialogs and anything unknown are answered with a
  control error. Only ``mcp_message`` (Angelo's direct tools, ``claude_direct``) is relayed, and its answer must be a
  success carrying exactly one JSON-RPC ``mcp_response`` for the same message id, or a control error.

A relayed request is remembered with what it asked; the worker's answer is taken once, and only when it fits. An
answer that does not fit is refused and Angelo answers the child with the method's refusal instead, so no request
waits on an answer that will never pass. Standard library only and no ``bpy``.
"""

import json

DENIED = "Angelo does not relay this request to the model runtime"
NOT_ENABLED = "This runtime request isn't enabled in the native Blender integration"
NO_PERMISSION = "Use native web search or the scoped host tools"

CODEX_DECLINED = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
}
CODEX_RELAYED = frozenset({"item/tool/call"})
CLAUDE_DECLINED = {
    "can_use_tool": {"behavior": "deny", "message": NO_PERMISSION},
    "elicitation": {"action": "decline"},
}
CLAUDE_RELAYED = frozenset({"mcp_message"})
_CONTENT = {"inputText": "text", "inputImage": "imageUrl", "inputAudio": "audioUrl"}
_INLINE = {"inputImage": "data:image/", "inputAudio": "data:audio/"}   # Inline only: never a file or web URL to fetch.


def _line(message):
    return json.dumps(message, ensure_ascii=False)


# Codex --------------------------------------------------------------------------------------------------------------

def codex_answer(message):
    """The line Angelo answers the child's request ``message`` with itself, or None when the worker answers it."""
    method, identity = message.get("method"), message.get("id")
    if method in CODEX_RELAYED:
        return None
    if method in CODEX_DECLINED:
        return _line({"id": identity, "result": dict(CODEX_DECLINED[method])})
    return _line({"id": identity, "error": {"code": -32601, "message": NOT_ENABLED}})


def codex_refusal(method, identity):
    """Angelo's answer to a relayed request whose worker answer did not fit (``method``'s own refusal)."""
    if method == "item/tool/call":
        return _line({"id": identity, "result": {"success": False,
                                                 "contentItems": [{"type": "inputText", "text": DENIED}]}})
    return _line({"id": identity, "error": {"code": -32601, "message": NOT_ENABLED}})


def _error(value):
    return (isinstance(value, dict) and type(value.get("code")) is int and isinstance(value.get("message"), str)
            and set(value) <= {"code", "message", "data"})


def codex_fits(method, answer):
    """Whether the worker's JSON-RPC ``answer`` has the shape of ``method``'s response (or is an error)."""
    if not set(answer) <= {"id", "result", "error", "jsonrpc"} or answer.get("jsonrpc", "2.0") != "2.0":
        return False
    if "error" in answer:
        return _error(answer["error"])
    result = answer.get("result")
    if method != "item/tool/call" or not isinstance(result, dict) or set(result) != {"success", "contentItems"}:
        return False
    items = result["contentItems"]
    return type(result["success"]) is bool and isinstance(items, list) and all(
        isinstance(item, dict) and set(item) == {"type", _CONTENT.get(item.get("type"))}
        and isinstance(item[_CONTENT[item["type"]]], str)
        and item[_CONTENT[item["type"]]].startswith(_INLINE.get(item["type"], "")) for item in items)


# Claude -------------------------------------------------------------------------------------------------------------

def _control(request_id, response=None, error=None):
    body = ({"subtype": "error", "request_id": request_id, "error": error} if error is not None else
            {"subtype": "success", "request_id": request_id, "response": response})
    return _line({"type": "control_response", "response": body})


def claude_answer(message):
    """The line Angelo answers Claude Code's control request ``message`` with itself, or None when relayed."""
    request = message.get("request") if isinstance(message.get("request"), dict) else {}
    subtype, request_id = request.get("subtype"), message.get("request_id")
    if subtype in CLAUDE_RELAYED:
        return None
    if subtype in CLAUDE_DECLINED:
        return _control(request_id, dict(CLAUDE_DECLINED[subtype]))
    return _control(request_id, error=DENIED)


def claude_expected(message):
    """What a relayed control request's answer must name: the MCP message id it answers."""
    request = message.get("request") if isinstance(message.get("request"), dict) else {}
    inner = request.get("message") if isinstance(request.get("message"), dict) else {}
    return {"subtype": request.get("subtype"), "id": inner.get("id")}


def claude_refusal(expected, request_id):
    if expected.get("subtype") == "mcp_message":
        return _control(request_id, {"mcp_response": {"jsonrpc": "2.0", "id": expected.get("id"),
                                                      "error": {"code": -32601, "message": DENIED}}})
    return _control(request_id, error=DENIED)


def claude_fits(expected, answer):
    """Whether the worker's ``control_response`` ``answer`` fits the relayed request ``expected`` describes."""
    response = answer.get("response")
    if set(answer) != {"type", "response"} or not isinstance(response, dict):
        return False
    if response.get("subtype") == "error":
        return set(response) == {"subtype", "request_id", "error"} and isinstance(response["error"], str)
    payload = response.get("response")
    if (response.get("subtype") != "success" or set(response) != {"subtype", "request_id", "response"}
            or not isinstance(payload, dict) or set(payload) != {"mcp_response"}):
        return False
    reply = payload["mcp_response"]
    return (expected.get("subtype") == "mcp_message" and isinstance(reply, dict)
            and reply.get("jsonrpc") == "2.0" and "id" in reply and reply["id"] == expected.get("id")
            and type(reply["id"]) is type(expected.get("id"))
            and ((set(reply) == {"jsonrpc", "id", "result"} and isinstance(reply["result"], dict))
                 or (set(reply) == {"jsonrpc", "id", "error"} and _error(reply["error"]))))
