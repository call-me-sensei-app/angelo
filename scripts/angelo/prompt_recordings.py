# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a model actually received, read back from the recordings a trial keeps (``prompt_audit.py``).

A Claude Code transcript records each distinct request shape as an ``api-request-shape`` line (``shape.system`` and
``shape.tools``) and the messages it sent as ``api-request-blob`` lines. A Codex rollout records the thread's
``session_meta`` (``base_instructions`` and ``dynamic_tools``) and the developer and context messages the runtime adds
before the first model output. Nothing here reads Angelo's source: a fact counts as received only when it is in the
recording.

Each recording is split by author. Angelo's text starts at one of the registry's openings ("You are Angelo", Studio's
prompt editor) and runs to the end of the block that holds it: Claude Code puts Angelo's ``systemPrompt`` (or an
``appendSystemPrompt``) last, after its billing and identity blocks, and a
Codex thread's ``baseInstructions`` is Angelo's whole. Everything else the model received (the runtime's own system
prompt, its tools, the context it injects into the first message) is foreign text. Standard library only.
"""

import hashlib
import json
import re
from pathlib import Path

TRIAL = re.compile(r"(?:angelo|mcp)-trial-\d+")
# Personal and per-run values are masked before foreign text is compared or written down.
MASKS = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(?:/Users|/home|/private|/var/folders|/tmp)/[^\s\"'`)<>]*"), "<path>"),
    (re.compile(r"[A-Za-z]:\\\\?[^\s\"'`)<>]*"), "<path>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b20\d\d-\d\d-\d\d(?:[T ][\d:.]+Z?)?\b"), "<date>"),
    (re.compile(r"\buser-[A-Za-z0-9]{12,}\b"), "<user-id>"),
)


def mask(text):
    for pattern, replacement in MASKS:
        text = pattern.sub(replacement, text)
    return text


def digest(value):
    data = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def normalize(text):
    """Whitespace-insensitive form: the texts wrap the same sentence at different columns."""
    return " ".join(text.split())


def _lines(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def _text(value):
    """Every string inside a JSON value, joined (a block list, {"text": ...} or a plain string)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):   # a content block: its text, never its "type" tag
        if isinstance(value.get("text"), str):
            return value["text"]
        return "\n".join(_text(item) for key, item in value.items() if key != "type")
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    return ""


def split_author(blocks, openings):
    """``(angelo_text, opening, foreign_blocks)`` for a recorded system prompt given as a list of texts."""
    for index, block in enumerate(blocks):
        found = [(block.find(opening), opening) for opening in openings if opening in block]
        if found:
            at, opening = min(found)
            foreign = blocks[:index] + ([block[:at]] if block[:at].strip() else []) + blocks[index + 1:]
            return block[at:], opening, foreign
    return "", None, list(blocks)


def server_section(texts, openings):
    """``(angelo_text, opening)`` when Angelo's text arrives inside a message (an MCP client lists each server's
    instructions as a Markdown section in its context): from the opening to the next heading line, or the end."""
    for text in texts:
        found = [(text.find(opening), opening) for opening in openings if opening in text]
        if found:
            at, opening = min(found)
            heading = re.search(r"\n#{1,6} ", text[at:])
            return text[at:at + heading.start()] if heading else text[at:], opening
    return "", None


def _claude_tools(tools):
    """``(angelo_tools, foreign_tools)`` of a Claude Code request's tool list."""
    angelo, foreign = [], []
    for tool in tools or []:
        name = tool.get("name") or tool.get("type") or "?"
        if name.startswith("mcp__angelo__"):
            angelo.append(tool)
        elif name == "StructuredOutput":
            # Claude Code's tool around Angelo's plan schema: the schema is Angelo's, the description is not.
            angelo.append({"name": name, "input_schema": tool.get("input_schema")})
            foreign.append({key: value for key, value in tool.items() if key != "input_schema"})
        else:
            foreign.append(tool)
    return angelo, foreign


def _build(path):
    """The add-on commit the trial ran (its ``<trial>-launch.json``), or None when no launch record is beside it."""
    for part in [path] + list(path.parents)[:3]:   # trial-01-transcript.jsonl, or trial-01-rollouts/rollout-*.jsonl
        match = TRIAL.match(part.name)
        launch = part.parent / (match.group(0) + "-launch.json") if match else None
        if launch and launch.is_file():
            try:
                addon = json.loads(launch.read_text()).get("addon") or {}
            except ValueError:
                return None, str(launch)
            commit = (addon.get("commit") or "")[:11]
            if not commit:   # A Windows run copies the add-on without its Git checkout: name it by its digest.
                found = (addon.get("addon_digest") or "")[:12]
                return ("addon:" + found) if found else None, str(launch)
            return commit + ("+dirty" if addon.get("uncommitted_addon_changes") else ""), str(launch)
    return None, None


def _scripts_from_input(value):
    """Python a tool call carried: every ``code`` string in its arguments, at any depth."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "code" and isinstance(item, str):
                found += [item] if item.strip() else []
            else:
                found += _scripts_from_input(item)
    elif isinstance(value, list):
        for item in value:
            found += _scripts_from_input(item)
    elif isinstance(value, str) and value[:1] in "{[":
        try:
            found += _scripts_from_input(json.loads(value))
        except ValueError:
            pass
    return found


def claude_recordings(path, openings):
    """One recording per distinct request shape in a Claude Code transcript."""
    shapes, blobs, scripts, model, first_answer = [], [], [], None, False
    for record in _lines(path):
        kind = record.get("type")
        if kind == "api-request-shape":
            shapes.append(record)
        elif kind == "api-request" and not model:
            model = (record.get("params") or {}).get("model")
        elif kind == "api-request-blob" and not first_answer:
            blobs.append(record.get("message") or {})
        elif kind == "assistant":
            first_answer = True
            message = record.get("message") or {}
            model = model or message.get("model")
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    scripts += _scripts_from_input(block.get("input"))
    injected, texts, image = [], [], False
    for message in blobs:
        if message.get("role") not in ("user", "system"):
            continue
        for block in message.get("content") or []:
            image = image or (isinstance(block, dict) and block.get("type") == "image")
            text = block.get("text") if isinstance(block, dict) else block if isinstance(block, str) else None
            if isinstance(text, str):
                texts.append(text)
                if text.lstrip().startswith("<system-reminder>") or message.get("role") == "system":
                    injected.append(text)
    out, seen = [], set()
    for shape_record in shapes:
        shape = shape_record.get("shape") or {}
        key = shape_record.get("shapeHash") or digest(shape)
        if key in seen:
            continue
        seen.add(key)
        out.append(_claude_recording(shape.get("system"), shape.get("tools"), texts, injected, openings,
                                     runtime_version=shape_record.get("version"), model=model,
                                     session=shape_record.get("sessionId"), timestamp=shape_record.get("timestamp"),
                                     scripts=scripts, turn_texts=texts, turn_image=image))
    return out


def _claude_recording(system, tools, texts, injected, openings, **fields):
    system = [{"text": system}] if isinstance(system, str) else system or []
    angelo, opening, foreign = split_author([_text(block) for block in system], openings)
    angelo_tools, foreign_tools = _claude_tools(tools)
    if not opening:   # An MCP client: Angelo's server instructions are a section of a context message.
        angelo, opening = server_section(texts, openings)
        if opening:
            injected = [text.replace(angelo, "<Angelo's server instructions>") for text in injected]
    return dict(runtime="claude", angelo_text=angelo, opening=opening, foreign_blocks=foreign,
                angelo_tools=angelo_tools, foreign_tools=foreign_tools, injected=list(injected), extra_text="",
                **fields)


def codex_recordings(path, openings):
    """The one thread a Codex rollout records."""
    meta, model, developer, injected, scripts, answered, realtime, mcp_calls = None, None, [], [], [], False, False, False
    turn, image = [], False
    for record in _lines(path):
        kind, payload = record.get("type"), record.get("payload") or {}
        if kind == "session_meta" and meta is None:
            meta = payload
        elif kind == "turn_context":
            model = model or payload.get("model")
            realtime = realtime or bool(payload.get("realtime_active"))
            instructions = ((payload.get("collaboration_mode") or {}).get("settings") or {}).get("developer_instructions")
            if instructions and not answered:
                developer.append(_text(instructions))
        item = payload.get("type")
        if item == "message" and not answered:
            role, text = payload.get("role"), _text(payload.get("content"))
            if role == "developer":
                developer.append(text)
            elif role == "user" and text.lstrip().startswith("<") and re.match(r"\s*<[a-z_]+[ >]", text):
                injected.append(text)
            elif role == "user":
                turn.append(text)
                image = image or "input_image" in json.dumps(payload.get("content"))
            elif role == "assistant":
                answered = True
        elif item in ("custom_tool_call", "function_call", "reasoning"):
            answered = True
            if item != "reasoning":
                call = payload.get("input") or payload.get("arguments") or ""
                mcp_calls = mcp_calls or "mcp__angelo" in json.dumps(payload) or (payload.get("namespace") == "angelo")
                if payload.get("name") == "angelo_execute_python" or "angelo_execute_python" in call:
                    scripts.append(call)
    if meta is None:
        return []
    base = _text(meta.get("base_instructions") or meta.get("instructions") or "")
    angelo, opening, foreign = split_author([base], openings)
    own = [text for text in developer if any(o in text for o in openings)]
    foreign += [text for text in developer if text not in own]
    tools = meta.get("dynamic_tools") or []
    return [dict(runtime="codex", runtime_version=meta.get("cli_version"), model=model, session=meta.get("id"),
                 timestamp=meta.get("timestamp"), angelo_text=angelo + "".join("\n" + text for text in own),
                 opening=opening, foreign_blocks=foreign, angelo_tools=tools if angelo else [],
                 foreign_tools=[] if angelo else tools, injected=injected, scripts=scripts,
                 extra_text="voice" if realtime else "", mcp_client=mcp_calls, turn_texts=turn, turn_image=image)]


def request_recordings(path, openings):
    """A probe's ``requests.jsonl`` (a header, then each request body as sent): its first request."""
    lines = list(_lines(path))
    if len(lines) < 2:
        return []
    header, body = lines[0], lines[1].get("body") or {}
    fields = dict(runtime_version=header.get("runtime_version"), model=body.get("model"), session=header.get("probe"),
                  timestamp=None, scripts=[], declared_route=header.get("probe"), probe_build=header.get("build"))
    if header.get("api") == "anthropic-messages":
        texts, injected = [], []
        for message in body.get("messages") or []:
            if message.get("role") == "assistant":
                break
            content = message.get("content")
            fields["turn_image"] = fields.get("turn_image") or any(
                isinstance(block, dict) and block.get("type") == "image" for block in content or [] if not isinstance(content, str))
            for text in [content] if isinstance(content, str) else [_text(block) for block in content or []]:
                texts.append(text)
                if text.lstrip().startswith("<system-reminder>") or message.get("role") == "system":
                    injected.append(text)
        return [_claude_recording(body.get("system"), body.get("tools"), texts, injected, openings, turn_texts=texts,
                                  **fields)]
    blocks, injected, foreign_tools, angelo_tools, turn = [], [], [], [], []
    if body.get("instructions"):
        blocks.append(_text(body["instructions"]))
    for item in body.get("input") or []:
        if item.get("type") == "additional_tools":   # the runtime's own tools, declared in the input
            foreign_tools += [{**tool, "name": "%s.%s" % (group.get("name"), tool.get("name"))}
                              for group in item.get("tools") or [] for tool in group.get("tools") or [group]]
        elif item.get("role") == "developer":
            blocks.append(_text(item.get("content")))
        elif item.get("role") == "user" and re.match(r"\s*<[a-z_]+[ >]", _text(item.get("content"))):
            injected.append(_text(item.get("content")))
        elif item.get("role") == "user":
            turn.append(_text(item.get("content")))
            fields["turn_image"] = fields.get("turn_image") or "input_image" in json.dumps(item.get("content"))
        elif item.get("role") in ("assistant",) or item.get("type") in ("function_call", "custom_tool_call"):
            break
    for tool in body.get("tools") or []:
        name = tool.get("name") or tool.get("type") or "?"
        (angelo_tools if name.startswith(("angelo_", "mcp__angelo__")) else foreign_tools).append(tool)
    angelo, opening, foreign = split_author(blocks, openings)
    return [dict(runtime="codex", angelo_text=angelo, opening=opening, foreign_blocks=foreign,
                 angelo_tools=angelo_tools, foreign_tools=foreign_tools, injected=injected,
                 extra_text="voice" if header.get("probe") == "voice" else "", turn_texts=turn, **fields)]


def recordings(roots, openings):
    """Every recording under ``roots``: Claude Code transcripts and Codex rollouts, with their file and build."""
    files = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            files.append(root)
            continue
        found = sorted(p for p in root.rglob("*.jsonl")
                       if "transcript" in p.name or p.name.startswith("rollout-") or p.name == "requests.jsonl")
        # A probe keeps the request body as sent beside the runtime's own log of it: read the body only.
        probes = {p.parent for p in found if p.name == "requests.jsonl"}
        files += [p for p in found if p.name == "requests.jsonl" or not probes & set(p.parents)]
    for path in files:
        reader = (request_recordings if path.name == "requests.jsonl" else
                  codex_recordings if path.name.startswith("rollout-") else claude_recordings)
        try:
            found = reader(path, openings)
        except OSError:
            continue
        build, launch = _build(path) if found else (None, None)
        build = build or ((found[0].get("probe_build") or "")[:11] or None if found else None)
        trial = launch or str(path)
        for index, recording in enumerate(found):
            recording.update(path=str(path), build=build, trial=trial, shape_index=index,
                             kind="why" if "why" in path.name else "warm-up" if "warm" in str(path) else "trial")
            yield recording
