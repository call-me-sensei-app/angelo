# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a plan being written will make, read from its text as it streams.

A typed plan is one structured message. Until it was complete the chat showed
"Preparing the next steps" and nothing else: on the recorded bridge session
six of nine typed turns showed nothing of their content before the commit,
for up to half a minute. The plan's text is already streaming through the
host, so the host can say what is in it at no cost to the model: nothing
here is requested from, or sent to, a model.

``PlanProgress.feed`` takes the plan's JSON text as it arrives (a Claude
plan's ``input_json_delta``, a Codex plan's message deltas, where the
parameters of some kinds are JSON text inside the JSON) and keeps three facts:
how many operations have been written, the names of the objects the creating
ones make, or a reviewed script's own description (its code follows it: recorded
arched-footbridge one-shots wrote one script for
9-10 s and named nothing until it ended), and the plan's steps in order
(``steps``: each operation's kind with its first name, a script with its
description). ``detail`` puts the names in one short line, the first names and
how many more operations follow; ``plan_steps`` groups the steps into the
live row's provisional "Planned" rows. Names are the model's own and are shown
as data, never translated; both are carried on the status (``status``/``split``,
``plan_steps.rows``) and drawn under the translated "Preparing the next steps".

A value is adopted only once its closing quote has arrived, so a fragment
boundary never shows part of one. A long value is cut at a space
(``MAX_LABEL``), never inside a word, so a credential (which has no spaces)
is either whole, and redacted by the chat (``Controller.redact``), or absent.

Display only, bounded, and tolerant by construction: text that is not a plan
simply has no operations, a partial token is counted when its end arrives,
and whatever the scan shows, the plan the host runs is the complete message
its validator accepts.
"""

import json
import re

from .action_schemas import OPERATION_SCHEMAS
from .plan_ops import ClosedOps
from .scene_binding import TARGETLESS_KINDS

SEPARATOR = "\x1f"
SHOWN_NAMES, MAX_NAMES, MAX_OPERATIONS, MAX_TAIL = 3, 16, 4096, 512
MAX_STEPS = 64  # Steps kept with their kind and name; later ones are only counted (``more_steps``).
MAX_LABEL = 160  # Longer than the live row draws; a longer value is cut at a space (``label``).
# A key and its string value, with plain quotes or the escaped quotes of JSON text inside a JSON string.
_TOKEN = re.compile(r'\\?"(kind|name|operations|expected_checks|checks|tool|description|code|edits)\\?"\s*:\s*'
                    r'(?:\\?"((?:[^"\\]|\\[^"])*?)\\?")?')
SCRIPT_TOOL = "angelo_execute_python"
# A plan step's own tool: a program's operations are its steps; a verification is a check, not a step.
PROGRAM_TOOL, VERIFY_TOOL = "angelo_scene_program", "angelo_scene_verify"


def _has_name(schema, depth=0):
    """Whether a parameters schema (or one of its alternatives) has a ``name``: a step of it waits for one."""
    if not isinstance(schema, dict) or depth > 2:
        return False
    return "name" in (schema.get("properties") or {}) or any(
        _has_name(variant, depth + 1) for variant in schema.get("oneOf", []) + schema.get("anyOf", []))


# Kinds whose step is named by their own ``name`` (an object, material, modifier...); any other kind's step is
# shown by its kind as soon as it starts, and a "name" deeper in it (a setting's) never names it.
NAMED_KINDS = frozenset(kind for kind, schema in OPERATION_SCHEMAS.items()
                        if _has_name(schema["properties"].get("parameters", {})))


def label(raw):
    """A string value's text for the live row (escapes decoded, spaces collapsed), or None.

    Cut at a space before ``MAX_LABEL`` characters and ended with "…"; a longer run without a space is
    dropped whole, so no cut ever splits a word (or a credential) in two.
    """
    try:
        text = json.loads('"' + raw + '"')
    except ValueError:
        text = raw.replace("\\n", " ")
    text = " ".join(str(text).split())
    if len(text) <= MAX_LABEL:
        return text or None
    cut = text.rfind(" ", 0, MAX_LABEL)
    return text[:cut].rstrip(" ,;:") + "…" if cut > 0 else None


def edits(tool):
    """Whether a plan's tool changes something (a lookup or a view is no step of what the plan makes)."""
    from .tool_specs import TOOL_MAP  # Here, not at import: the plan stream loads before the tool catalogue.
    spec = TOOL_MAP.get(tool)
    return spec is not None and not spec["annotations"]["readOnlyHint"]


def named_tool(tool):
    """Whether a plan's own tool step waits for a name: a script's description, or a ``name`` argument."""
    from .tool_specs import TOOL_MAP
    return tool == SCRIPT_TOOL or _has_name(TOOL_MAP[tool]["inputSchema"])


def batch_count(parameters):
    """How many instances a batch's parameters make (``batch_spec.instance_count``), or None when they do not say."""
    from .batch_spec import instance_count
    if isinstance(parameters, str):  # A Codex plan may write a kind's parameters as JSON text.
        try:
            parameters = json.loads(parameters)
        except ValueError:
            return None
    if not isinstance(parameters, dict):
        return None
    placement = {key: parameters.get(key) for key in ("array", "at", "along", "scatter", "mirror")}
    if isinstance(placement["along"], dict):
        placement["along"] = dict({"spacing": None, "count": None, "align": False}, **placement["along"])
    try:
        count = instance_count(placement)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    return count if type(count) is int and count > 0 else None


class PlanProgress:
    def __init__(self):
        self.operations, self.names, self.steps, self.more_steps = 0, [], [], 0
        self._buffer, self._checks, self._naming, self._script, self._code = "", False, False, False, False
        # Complete program operations (plan_ops), matched in order to their steps; created objects by alias.
        self._closed, self._program_steps, self._matched, self._aliases = ClosedOps(), [], 0, {}

    def _step(self, kind, waits, program=False):
        """A new step: ``[kind, name or None, waits, count, folded]``.

        One that ``waits`` is named by its own name or description (a batch by its unit, once complete);
        ``count`` is how many objects it makes, and a batch ``folded`` into its unit's step shows there.
        """
        step = [kind, None, waits or kind == "batch", 1, False]
        if program:
            self._program_steps.append(step if len(self.steps) < MAX_STEPS else None)
        if len(self.steps) < MAX_STEPS:
            self.steps.append(step)
        else:
            self.more_steps += 1

    def _name_step(self, text):
        step = self.steps[-1] if self.steps and not self.more_steps else None
        if step is not None and step[2] and step[1] is None and step[0] != "batch" and text:
            step[1] = text

    def _complete(self, operation):
        """A program operation is complete: remember its alias, or count a batch into its unit's step."""
        kind = operation.get("kind")
        if kind not in OPERATION_SCHEMAS:
            return
        index = next((i for i in range(self._matched, len(self._program_steps))
                      if self._program_steps[i] is None or self._program_steps[i][0] == kind), None)
        if index is None:
            return
        self._matched, step = index + 1, self._program_steps[index]
        parameters = operation.get("parameters")
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except ValueError:
                parameters = None
        parameters = parameters if isinstance(parameters, dict) else {}
        if step is None:
            return
        if kind in TARGETLESS_KINDS and isinstance(parameters.get("alias"), str):
            self._aliases[parameters["alias"]] = step
        if kind != "batch":
            return
        sources = parameters.get("sources") if isinstance(parameters.get("sources"), list) else []
        first = sources[0].get("object") if sources and isinstance(sources[0], dict) else (sources or [None])[0]
        unit = self._aliases.get(first[1:]) if isinstance(first, str) and first.startswith("$") else None
        count = batch_count(parameters)
        if unit is not None and len(sources) == 1 and count:
            unit[3], step[4] = count, True  # "Post ×4" on the unit's own row.
        else:
            step[1] = unit[1] if unit is not None else None
            step[3] = count or 1
        step[2] = False

    def feed(self, text):
        """Scan more of the plan's text; returns whether what there is to show changed."""
        if not isinstance(text, str) or not text or self.operations >= MAX_OPERATIONS:
            return False
        self._buffer += text
        before, end = (self.operations, len(self.names), self.more_steps, self._named()), 0
        for match in _TOKEN.finditer(self._buffer):
            key, value = match.group(1), match.group(2)
            if match.group(0)[0] == "\\" and (self._code or key in ("tool", "description", "code")):
                end = match.end()
                continue  # Quoted text inside a string (a script's code): never a step's own field.
            if key in ("code", "edits"):  # A script's code or patch: until the next step, escaped keys are its text.
                self._code, end = True, match.end()
                continue
            if key in ("kind", "name", "tool", "description") and value is None:
                if match.end() >= len(self._buffer) - 1:
                    break  # Its value has not arrived yet.
                continue
            end = match.end()
            if key in ("operations", "tool"):  # A plan step's own tool ends any checks list (and code) before it.
                self._checks, self._script = False, (value == SCRIPT_TOOL if key == "tool" else self._script)
                self._code = False
                if key == "tool" and value not in (PROGRAM_TOOL, VERIFY_TOOL) and edits(value):
                    self._step(value, named_tool(value))  # An edit tool is a step; a program's operations are its steps.
            elif key in ("expected_checks", "checks"):
                self._checks = True
            elif self._checks:
                continue
            elif key == "kind" and value in OPERATION_SCHEMAS:
                self.operations += 1
                self._naming = value in TARGETLESS_KINDS
                self._step(value, value in NAMED_KINDS, program=True)
            elif key == "name" and (shown := label(value)):
                if self._naming and len(self.names) < MAX_NAMES:
                    self.names.append(shown)
                self._naming = False
                self._name_step(shown)  # A modifier's or a material's own name names its step, not an object.
            elif key == "description" and self._script and (shown := label(value)):
                if len(self.names) < MAX_NAMES:
                    self.names.append(shown)  # A script's own summary.
                self._script = False
                self._name_step(shown)
        self._buffer = self._buffer[max(end, len(self._buffer) - MAX_TAIL):]
        for operation in self._closed.feed(text):
            self._complete(operation)
        return (self.operations, len(self.names), self.more_steps, self._named()) != before

    def _named(self):
        """What the steps show, compared before and after a fragment."""
        return tuple((step[1] is not None or not step[2], step[3], step[4]) for step in self.steps)

    def detail(self):
        """The line to show: the first names and how many more operations there are, or "" before the first name."""
        if not self.names:
            return ""
        shown = self.names[:SHOWN_NAMES]
        more = self.operations - len(shown)
        return ", ".join(shown) + (" +%d" % more if more > 0 else "")


def status(base, detail, rows=""):
    """A status that carries a detail line for the live row (``split`` reads it back).

    ``rows``: the plan's provisional steps, encoded by ``plan_steps.encode`` (``plan_steps.rows`` reads them).
    """
    return base + SEPARATOR + detail + rows if detail or rows else base


# The generic status of a running turn (claude_stream.STATUS_THINKING), which a summary title may refine.
THINKING = "Thinking"
# A title of a public reasoning summary part: **Planning the bridge**, as GPT models write them, which ends its
# line; inline bold in a part's body is followed by more of the line (otherwise the
# last bold anywhere picked "two" from "needs **two** posts"). A title the stream has just ended counts too.
_TITLE = re.compile(r"\*\*([^*\n]{1,200}?)\*\*(?=\n|\Z)")
MAX_TITLE = 64


def summary_title(text):
    """The latest title of a public reasoning summary, for the live row's detail, or "".

    A GPT reasoning summary streams as titled parts. While the model reasons, before a typed plan's reply or
    operations stream, the live row said only "Thinking": 5.7-11.8 s of reasoning in a recorded GPT-6.1-Sol
    one-shot footbridge. The title is the model's own text from the runtime's public summary,
    already shown in the thinking row, and is drawn as data like the names a plan will make; nothing here is
    requested from or sent to a model.
    """
    titles = _TITLE.findall(text[-4096:]) if isinstance(text, str) else []
    title = " ".join(titles[-1].split()) if titles else ""
    return title if len(title) <= MAX_TITLE else title[:MAX_TITLE - 1].rstrip() + "…"


# A finished sentence of a summary written as prose: its end mark is followed by more text, so "1." of "1.5" and a
# sentence still streaming never count.
_SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?!\s))+[.!?](?=\s)")
_MARKUP = re.compile(r"[*_`#>]+")


def summary_line(text):
    """The latest finished sentence of a reasoning summary written as prose, for the live row's detail, or "".

    The hook for runtimes whose summaries have no titles (Claude's summarized thinking is short paragraphs):
    the live row then names what the model is working on, a sentence at a time, never word by word. A sentence
    longer than ``MAX_TITLE`` is cut at a space and ended with "…"; one without a space before that is not
    shown, so a cut never splits a word or a credential. Display only, like ``summary_title``.
    """
    found = _SENTENCE.findall(text[-4096:]) if isinstance(text, str) else []
    sentences = [sentence for sentence in found if sentence.strip(" .!?")]
    line = " ".join(_MARKUP.sub("", sentences[-1]).split()) if sentences else ""
    if len(line) <= MAX_TITLE:
        return line
    cut = line.rfind(" ", 0, MAX_TITLE)
    return line[:cut].rstrip(" ,;:") + "…" if cut > 0 else ""


def thinking_status(current, text, prose=False):
    """``current`` carrying the latest title of the summary ``text`` while it is the generic thinking status.

    ``prose``: a summary without titles (``summary_line``) also names the row, by its latest finished sentence;
    a title, when there is one, still wins. Every other status (connecting, a plan being written, approvals) is
    left as it is.
    """
    title = summary_title(text) or (summary_line(text) if prose else "")
    return status(THINKING, title) if title and split(current)[0] == THINKING else current


def split(text):
    """``(status, detail)`` of a status text; a status without a detail is itself.

    The detail never includes the provisional steps a plan's status carries after it (``plan_steps``).
    """
    base, _, detail = str(text).partition(SEPARATOR)
    return base, detail.partition(ROWS)[0]


ROWS = "\x1e"  # Starts the provisional steps after a detail (plan_steps.encode).
