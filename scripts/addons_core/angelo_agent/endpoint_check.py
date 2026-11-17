# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test connection for a custom model endpoint: what the organization's server does with Angelo's requests.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints" (Test connection).
Run only when the person presses Test connection (it sends a few small requests
to the organization's server, billed there). For each format the endpoint
speaks, with that format's first model:

1. **Models**: the server's model list (``/models`` under the format's address),
   when it has one: whether it lists each configured model. A list that names
   none of them (empty, or a pass-through route listing the provider's own names)
   isn't the route's list: that is said once, in neutral words, and the tool check
   decides; only a list that names some configured models warns about the others.
2. **Tool call**: one short streamed request with one tool, in the shape the
   model gets it in a chat (``endpoint_check_tools``: a code-mode model's freeform
   custom tool, a Responses Lite model's tools in an additional_tools item, else a
   function tool), for each shape a format's models use. The reply must call the
   tool as a tool call. A reply that carries the call as text means the server
   (or the model behind it) doesn't parse tool calls; a code-mode model that
   can't call its custom tool can't call Angelo at all. The call and its result
   then go back in one more request, as after every tool a chat runs.
3. **Streaming**: a second streamed request whose reply is long enough to take
   measurable time. When all of it arrives at once after a wait, the server buffers
   streamed replies, which makes Angelo look frozen. A reply too fast to tell is
   reported as such, never as a pass.
4. **Prompt caching** (OpenAI format): whether the reply names the
   ``prompt_cache_key`` sent, when the server returns it.

A refused key is named as the key's problem whatever status carries it (some
servers answer a missing or wrong key with a 500, not a 401), and the server's
own message is shown with the key removed. Requests go straight to the
endpoint's host (no proxy, no redirects: a key is never forwarded elsewhere),
with the same TLS roots as Angelo's team requests (``team_http.tls_context``,
which adds the policy's ``ExtraCACertificates``).

Standard library only; no bpy. ``run`` is blocking: call it off the main thread.
"""

import json
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import quote

from . import endpoint_check_tools as tools

from .display_text import Text
from .ui_text import n_

OK, WARNING, ERROR = "ok", "warning", "error"
ANTHROPIC_VERSION = "2023-06-01"   # The Messages API version header Claude Code sends.
# Under each format's address: where its model list and its streamed request are. Claude Code adds /v1/messages to
# its base URL; Codex adds /responses (and Gemini models/<model>:streamGenerateContent) to its own.
MODELS_PATH = {"openai-responses": "/models", "gemini": "/models", "anthropic-messages": "/v1/models"}
TIMEOUT = 120.0           # Seconds for one request; some models think for a while before their first byte.
MESSAGE_LIMIT = 200       # Characters of the server's own message shown.
TOOL = tools.TOOL
STREAM_EVENTS = 8         # Fewer streamed pieces than this can't show whether the server buffers.
STREAM_SECONDS = 0.4      # A reply generated faster than this can't show it either.
BUFFERED_SHARE = 0.1      # All pieces within this share of the wait: delivered at once.

MODELS_LISTED = n_("{format}: the server lists every configured model")
MODELS_MISSING = n_("{format}: the server doesn't list {models}. Check the model names in the console.")
MODELS_UNLISTED = n_("{format}: the server has no model list; the model names weren't checked")
MODELS_ROUTE = n_("{format}: this route doesn't list its models; the tool check below is what counts")
TOOL_OK = n_("{format}: {model} answered with a tool call")
TOOL_AS_TEXT = n_("{format}: {model} wrote its tool call as plain text. The server needs tool-call parsing turned "
                  "on for this model, or the model doesn't support tools.")
TOOL_MISSING = n_("{format}: {model} answered without calling the tool it was asked to call. It may not support tools.")
CUSTOM_DROPPED = n_("{format}: {model} didn't call its tool. As {profile}, it reaches Angelo's tools only through a "
                    "custom (freeform) tool, so this server may not pass custom tools or their calls; ask your admin "
                    "to let them through this route.")
CUSTOM_REFUSED = n_("{format}: this server doesn't pass the custom tool calls {model} needs (HTTP {status}: {message}). "
                    "As {profile}, it reaches Angelo's tools only through a custom (freeform) tool; ask your admin to "
                    "let custom tools and their results through this route.")
RESULT_OK = n_("{format}: {model} received its tool's result and answered")
RESULT_REFUSED = n_("{format}: the server refused {model}'s tool result (HTTP {status}: {message}). A chat can't continue "
                    "after a tool runs; ask your admin to let tool results through this route.")
STREAMING_OK = n_("{format}: replies stream as they are written")
BUFFERED = n_("{format}: the server sent the whole reply at once after {seconds} s. It buffers streamed replies, which "
              "makes Angelo look frozen; turn response buffering off for this route.")
STREAMING_UNKNOWN = n_("{format}: the reply was too short or fast to tell whether the server streams it")
CACHE_KEY_KEPT = n_("{format}: the prompt cache key reached the model")
CACHE_KEY_CHANGED = n_("{format}: the server changed the prompt cache key, so prompt caching may not work")
KEY_REFUSED = n_("{format}: the server refused your key (HTTP {status}: {message}). Check the key in AI Providers, "
                 "or ask your admin whether it allows {model}.")
HTTP_ERROR = n_("{format}: the server answered HTTP {status}: {message}")
NOT_FOUND = n_("{format}: the server has no {path}. Check the endpoint's address and format in the console.")
TLS_FAILED = n_("{format}: couldn't verify the server's certificate ({message}). If your organization inspects TLS, "
                "its IT department adds its root certificate with the ExtraCACertificates policy.")
UNREACHABLE = n_("{format}: couldn't reach {host} ({message})")
BAD_REPLY = n_("{format}: the server's reply isn't one Angelo can read ({message})")
CHECK_FAILED = n_("The check stopped: {message}")
# What the model reads: English, never translated.
TOOL_PROMPT = "This is a connection check. Call the angelo_check tool with ok set to true."  # i18n: model-facing
CUSTOM_PROMPT = "This is a connection check. Call the angelo_check tool with the text ok."  # i18n: model-facing
STREAM_PROMPT = "This is a streaming check. Write the numbers from 1 to 80, one per line, and nothing else."  # i18n: model-facing
PROBE_TOOL = tools.PROBE_TOOL
# Words a server's message uses when the key is missing or wrong, whatever status it answers with.
_KEY_WORDS = ("api key", "api_key", "apikey", "api-key", "authenticat", "unauthori", "credential",  # i18n: matched
              "access token", "bearer", "forbidden", "permission", "virtual key")  # i18n: matched in servers' replies


@dataclass(frozen=True)
class Finding:
    level: str      # OK, WARNING or ERROR
    text: Text


@dataclass(frozen=True)
class Report:
    findings: tuple

    @property
    def ok(self):
        return all(f.level != ERROR for f in self.findings)

    @property
    def warnings(self):
        return any(f.level == WARNING for f in self.findings)


def failed(message):
    return Report((Finding(ERROR, Text(CHECK_FAILED, message=message[:MESSAGE_LIMIT])),))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirected", headers, fp)  # i18n: protocol reason


class _Failure(Exception):
    """One request's failure, already worded (a ``Finding``)."""

    def __init__(self, finding):
        super().__init__(finding.text.template)
        self.finding = finding


def _opener():
    from .team_http import tls_context
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                       urllib.request.HTTPSHandler(context=tls_context()))


def _headers(endpoint, format_id, key, stream):
    form = endpoint.format(format_id)
    headers = dict(endpoint.extra_headers)
    if key:   # A server that checks no key (credential "none") gets none.
        headers[form.header] = ("Bearer " + key) if form.scheme == "bearer" else key
    if format_id == "anthropic-messages":
        headers["anthropic-version"] = ANTHROPIC_VERSION
    headers.update({"Content-Type": "application/json", "User-Agent": "Angelo-EndpointCheck/1",
                    "Accept": "text/event-stream" if stream else "application/json"})
    return headers


def _clean(text, key):
    text = " ".join(str(text).split())
    return (text.replace(key, "[REDACTED]") if key else text)[:MESSAGE_LIMIT]


def _scrub(value, key):
    """``value`` (parsed JSON) with ``key`` taken out of every string, before any of it becomes text."""
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]")
    if isinstance(value, dict):
        return {_scrub(k, key): _scrub(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, key) for v in value]
    return value


def _server_message(body, key):
    if key:   # The raw reply first, the key as written and as JSON escapes it, before any parsing or conversion.
        body = body.replace(key, "[REDACTED]").replace(json.dumps(key)[1:-1], "[REDACTED]")
    try:
        value = json.loads(body)
    except ValueError:
        return _clean(body, key)
    value = _scrub(value, key) if key else value
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        error = error.get("message") or error
    return _clean(error if isinstance(error, str) else value, key)


def _request(opener, endpoint, format_id, key, url, path, body=None, stream=False):
    """The open response of one request, or ``_Failure`` worded for the person."""
    label, model = _label(format_id), endpoint.models_in(format_id)[0].id
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=_headers(endpoint, format_id, key, stream),
                                     method="POST" if data is not None else "GET")
    try:
        return opener.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        message = _server_message(error.read(4096).decode("utf-8", "replace"), key)
        if error.code == 404:
            raise _Failure(Finding(ERROR, Text(NOT_FOUND, format=label, path=path))) from None
        if error.code in (401, 403) or (error.code >= 400 and any(w in message.lower() for w in _KEY_WORDS)):
            raise _Failure(Finding(ERROR, Text(KEY_REFUSED, format=label, status=error.code, message=message,
                                               model=model))) from None
        raise _Failure(Finding(ERROR, Text(HTTP_ERROR, format=label, status=error.code, message=message))) from None
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, ssl.SSLError):
            raise _Failure(Finding(ERROR, Text(TLS_FAILED, format=label, message=_clean(reason, key)))) from None
        raise _Failure(Finding(ERROR, Text(UNREACHABLE, format=label, host=endpoint.host,
                                           message=_clean(reason, key)))) from None
    except (OSError, ValueError) as error:
        raise _Failure(Finding(ERROR, Text(UNREACHABLE, format=label, host=endpoint.host,
                                           message=_clean(error, key)))) from None


def _label(format_id):
    from .custom_endpoints import FORMATS
    return FORMATS[format_id][2]


def _events(response, started, key, format_id):
    """``[(seconds since the request, JSON value)]`` of a server-sent event stream."""
    output = []
    try:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            output.append((time.monotonic() - started, json.loads(payload)))
    except ValueError as error:
        raise _Failure(Finding(ERROR, Text(BAD_REPLY, format=_label(format_id), message=_clean(error, key)))) from None
    finally:
        response.close()
    return output


def _models_finding(opener, endpoint, format_id, key):
    label = _label(format_id)
    try:
        path = MODELS_PATH[format_id]
        response = _request(opener, endpoint, format_id, key, endpoint.url(format_id) + path, path)
    except _Failure as failure:
        if failure.finding.text.template == NOT_FOUND:
            return Finding(OK, Text(MODELS_UNLISTED, format=label))
        return failure.finding
    try:
        with response:
            value = json.loads(response.read(4 * 1024 * 1024) or b"{}")
    except (OSError, ValueError) as error:
        return Finding(WARNING, Text(BAD_REPLY, format=label, message=_clean(error, key)))
    value = value if isinstance(value, dict) else {}
    listed = {m.get("id") for m in value.get("data") or () if isinstance(m, dict)}
    listed |= {str(m.get("name", "")).removeprefix("models/") for m in value.get("models") or () if isinstance(m, dict)}
    configured = [m.id for m in endpoint.models_in(format_id)]
    listed.discard("")
    if not listed & set(configured):   # Lists nothing, or lists in other words (a pass-through's own names):
        return Finding(OK, Text(MODELS_ROUTE, format=label))   # no model is judged by a list that isn't the route's.
    missing = [m for m in configured if m not in listed]
    if missing:   # The route lists its own names and some configured ones aren't among them.
        return Finding(WARNING, Text(MODELS_MISSING, format=label, models=", ".join(missing)))
    return Finding(OK, Text(MODELS_LISTED, format=label))


def _post(opener, endpoint, format_id, key, model, prompt, cache_key, use_tools, shape=tools.PLAIN, turns=()):
    """``(events, cache key echoed or None)`` of one streamed request to ``model``."""
    if format_id == "gemini":
        path = "/models/%s:streamGenerateContent" % quote(model, safe="")
        url, body = endpoint.url(format_id) + path + "?alt=sse", tools.gemini_body(prompt, use_tools, turns)  # i18n: a URL query
    elif format_id == "anthropic-messages":
        path = "/v1/messages"
        url, body = endpoint.url(format_id) + path, tools.messages_body(model, prompt, use_tools, turns)
    else:
        path = "/responses"
        url, body = endpoint.url(format_id) + path, tools.responses_body(model, prompt, cache_key, shape, use_tools,
                                                                          turns)
    started = time.monotonic()
    events = _events(_request(opener, endpoint, format_id, key, url, path, body, stream=True), started, key,
                     format_id)
    echoed = next((e.get("response", {}).get("prompt_cache_key") for _, e in events
                   if isinstance(e, dict) and isinstance(e.get("response"), dict)
                   and e["response"].get("prompt_cache_key")), None)
    return events, echoed


def _tool_finding(format_id, model, shape, made, text):
    label = _label(format_id)
    if made:
        return Finding(OK, Text(TOOL_OK, format=label, model=model.id))
    if TOOL in text:
        return Finding(ERROR, Text(TOOL_AS_TEXT, format=label, model=model.id))
    if shape[0] == tools.CUSTOM:
        return Finding(ERROR, Text(CUSTOM_DROPPED, format=label, model=model.id, profile=model.profile))
    return Finding(WARNING, Text(TOOL_MISSING, format=label, model=model.id))


def _refused(failure, template, format_id, model):
    """A request's HTTP refusal reworded as ``template``; any other failure as it was."""
    text = failure.finding.text
    if text.template != HTTP_ERROR:
        return failure.finding
    return Finding(ERROR, Text(template, format=_label(format_id), model=model.id, profile=model.profile,
                               status=text.fields["status"], message=text.fields["message"]))


def _tool_findings(opener, endpoint, format_id, key, model, shape, cache_key):
    """The tool call in ``shape`` and its result sent back: ``(findings, cache key echoed)``."""
    custom = shape[0] == tools.CUSTOM
    try:
        events, echoed = _post(opener, endpoint, format_id, key, model.id, CUSTOM_PROMPT if custom else TOOL_PROMPT,
                               cache_key, True, shape)
    except _Failure as failure:
        return [_refused(failure, CUSTOM_REFUSED, format_id, model) if custom else failure.finding], None
    made, text = tools.call(events)
    findings = [_tool_finding(format_id, model, shape, made, text)]
    if made:
        try:
            _post(opener, endpoint, format_id, key, model.id, CUSTOM_PROMPT if custom else TOOL_PROMPT, cache_key,
                  True, shape, tools.answer_turns(made))
            findings.append(Finding(OK, Text(RESULT_OK, format=_label(format_id), model=model.id)))
        except _Failure as failure:
            findings.append(_refused(failure, RESULT_REFUSED, format_id, model))
    return findings, echoed


def streaming_finding(format_id, times):
    """Whether replies stream, from the arrival times (seconds since the request) of one reply's pieces."""
    label = _label(format_id)
    if len(times) < STREAM_EVENTS:
        return Finding(WARNING, Text(STREAMING_UNKNOWN, format=label))
    spread, waited = times[-1] - times[0], times[-1]
    if spread >= BUFFERED_SHARE * waited and spread >= STREAM_SECONDS:
        return Finding(OK, Text(STREAMING_OK, format=label))
    if waited >= STREAM_SECONDS and spread < BUFFERED_SHARE * waited:
        return Finding(ERROR, Text(BUFFERED, format=label, seconds="%.1f" % waited))
    return Finding(WARNING, Text(STREAMING_UNKNOWN, format=label))


def _tool_models(endpoint, format_id, shapes):
    """``[(model, shape)]``: the first model of each tool shape a format's models use."""
    chosen = {}
    for model in endpoint.models_in(format_id):
        shape = (shapes or {}).get(model.id, tools.PLAIN) if format_id == "openai-responses" else tools.PLAIN
        chosen.setdefault(shape, model)
    return [(model, shape) for shape, model in chosen.items()]


def check_format(opener, endpoint, format_id, key, shapes=None):
    """The findings for one format; a failed request ends that format's checks with its reason."""
    label = _label(format_id)
    findings = [_models_finding(opener, endpoint, format_id, key)]
    if findings[0].level == ERROR:
        return findings
    cache_key = "angelo-check-" + uuid.uuid4().hex
    echoes = []
    for model, shape in _tool_models(endpoint, format_id, shapes):
        found, echoed = _tool_findings(opener, endpoint, format_id, key, model, shape, cache_key)
        findings += found
        echoes.append(echoed)
        if found[0].level == ERROR and found[0].text.template in (KEY_REFUSED, UNREACHABLE, TLS_FAILED, NOT_FOUND):
            return findings   # Nothing else can work for this format.
    echoed = next((e for e in echoes if e), None)
    if echoed:
        findings.append(Finding(OK if echoed == cache_key else WARNING,
                                Text(CACHE_KEY_KEPT if echoed == cache_key else CACHE_KEY_CHANGED, format=label)))
    try:
        events, _ = _post(opener, endpoint, format_id, key, endpoint.models_in(format_id)[0].id, STREAM_PROMPT,
                          cache_key, False)
        findings.append(streaming_finding(format_id, [seconds for seconds, _ in events]))
    except _Failure as failure:
        findings.append(failure.finding)
    return findings


def run(endpoint, key, opener=None, shapes=None):
    """The ``Report`` of every format of ``endpoint``, with the person's ``key``. ``shapes``: each Responses model's
    tool shape (``endpoint_check_tools.shapes``); none known means function tools."""
    opener = opener or _opener()
    findings = []
    for format_id, _form in endpoint.formats:
        if endpoint.models_in(format_id):
            findings += check_format(opener, endpoint, format_id, key, shapes)
    return Report(tuple(findings))
