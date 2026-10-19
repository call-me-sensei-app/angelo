# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Answer checks for the live-eval harness's questions: the answer against ground truth, and its evidence.

An unchanged scene says nothing about an answer, so a question is graded on the
answer itself. Its prompt asks for one last line in a fixed form
(``eval_answer_forms``, re-exported here), the harness holds the ground truth
(``survey_truth``, computed from the scene) and ``grade`` compares the two.
Free text is never matched. The value must state the answer: "ANSWER: no
idea" is no answer.

A form is not stricter than it needs to be: a reading that takes a liberty (a
count before the names, units among the numbers, the location on its own line)
names it, and so do words before the answer on its line and text after it.
``grade`` then records
``parsed: "leniently"`` with those notes and ``stated``, the line as written,
so reports can count how often a model needed it (``lenient_counts``).

Outcomes (``OUTCOMES``): ``right`` passes. ``empty`` (no reply text),
``no_answer`` (no such line, or one that cannot be parsed: a refusal or "I
cannot see it" lands here), ``wrong``, ``unsupported`` (the right answer to a
visual question with no image that could support it before the answer, one
whose reply says the model had no image, or one whose receipt shows no such
picture reached the model) and ``constant`` (both variants of a yes/no
question answered alike, ``constant_pair``) all fail. ``unverified`` (a right
answer whose picture's delivery the receipt cannot prove) is ungraded, never
passed.

A visual answer counts only when the model saw the picture, by two records
and its own account, never by its words alone:

* the rows: a view of the needed kind captured before the answer line;
* the receipt (``delivery``, the request receipt's ``path`` section,
  ``request_path``): a picture of that kind delivered in a provider turn the
  provider accepted (``image_delivery``). None delivered is ``unsupported``;
  delivery a runtime cannot prove (Codex code-mode results are sent without
  proof that the model received the pixels) or a receipt without the section
  is ``unverified``;
* the answering reply must not say the model had no image
  (``no_image_claim``, closed English phrases: "I can't see the image", "the
  render didn't load", "no picture was returned"); one that does is
  ``unsupported`` whatever its answer line, since the answer was a guess.

``visual_evidence`` reads the turn's chat rows: a completed image tool
(``IMAGE_TOOLS``, the add-on's own set) whose result lists views that hold
bytes. Only counts and closed camera sources are kept, never text, paths or
arguments. The rows record that an image was captured for the model's turn,
not that a provider request carried it: ``image_captured`` and
``images_before_answer`` say only that much (doc/angelo/native_coverage.rst,
"Grading contract"). A row's position is not when its text arrived (the Claude
runtime keeps one reply row per turn and rewrites it every round), so the
trial driver samples the rows while the turn runs (``Watch``) and credits an
image only when it was there before the answer line was last stated; rows
given without a watch (scripted ones, in tests) are read in their own order.

Dependency-free: no ``bpy``, no provider, importable by host tests.
"""

import re

from eval_answer_forms import (  # noqa: F401  (the forms are part of this module's interface)
    BOX_TOLERANCE, CONTINUATIONS, EXACTLY, FORMS, LENIENTLY, YES_NO_WORDS, answer_line, answer_lines,
    engine_name, matches, parse, prompt, readings, text_after_answer, text_before_answer)

# angelo_agent.claude_evidence.VISUAL_IMAGE_TOOLS (pinned equal by test_native_eval_answers).
IMAGE_TOOLS = frozenset({"angelo_view", "angelo_sculpt_view", "angelo_sculpt_review"})
OUTCOMES = ("right", "wrong", "empty", "no_answer", "unsupported", "unverified", "constant")
SCENE_CAMERA, ANY_IMAGE = "SCENE_CAMERA", "ANY"
# What a model writes when it had no picture to look at. A noun must follow the verb (one optional word between),
# so "I can't see any part cut off" or "the view doesn't show clipping" are no claims.
_NOUN = r"(?:images?|pictures?|screenshots?|renders?|rendered views?|views?|viewport images?)"
_WHICH = r"(?:(?:the|any|an|a|this|that|your|its|my)\s+)?(?:\w+\s+)?"
NO_IMAGE = re.compile(
    r"\b(?:can(?:no|')?t|can not|cannot|could(?: not|n't)|unable to|not able to|do(?: not|n't)|did(?: not|n't)|never)"
    r"\s+(?:actually\s+)?(?:see|view|access|load|open|receive|get|look at|have access to)\s+" + _WHICH + _NOUN + r"\b"
    r"|\bno\s+" + _NOUN + r"\s+(?:was|were|is|came|appeared|got|has been|is available|was returned)\b"
    r"|\b" + _NOUN + r"\s+(?:did(?: not|n't)|does(?: not|n't)|was(?: not|n't)|is(?: not|n't)|has(?: not|n't)|never)\s+"
    r"(?:load|loaded|come through|came through|appear|appeared|arrive|arrived|attached|returned|visible|available)\b"
    r"|\bwithout\s+(?:seeing|being able to see|viewing|access to)\s+" + _WHICH + _NOUN + r"\b", re.IGNORECASE)


def _tool_views(row):
    """``[{"source", "reused"}]`` for the images one completed image-tool row captured."""
    if (not isinstance(row, dict) or row.get("role") != "activity" or row.get("status") != "completed"
            or row.get("tool") not in IMAGE_TOOLS or not isinstance(row.get("result"), dict)):
        return []
    views = []
    for view in row["result"].get("views") or ():
        if not isinstance(view, dict) or type(view.get("bytes")) is not int or view["bytes"] <= 0:
            continue
        camera = view.get("camera") if isinstance(view.get("camera"), dict) else {}
        source = camera.get("source")
        if not (isinstance(source, str) and re.fullmatch(r"SCENE_CAMERA|FIT|AXIS:[A-Z]{1,8}", source)):
            source = "OTHER"   # Sculpt views frame a region, not a named camera.
        views.append({"source": source, "reused": bool(camera.get("reused_from"))})
    return views


def visual_evidence(rows, before=None):
    """Images captured in a turn's rows (only those before row ``before`` when given): counts and sources."""
    views = [view for index, row in enumerate(rows) if before is None or index < before for view in _tool_views(row)]
    sources = {}
    for view in views:
        sources[view["source"]] = sources.get(view["source"], 0) + 1
    return {"images": len(views), "sources": sources, "reused": sum(view["reused"] for view in views),
            "current_scene_camera": sum(view["source"] == SCENE_CAMERA and not view["reused"] for view in views)}


def reply_text(rows):
    """The assistant's text in a turn's rows, in row order."""
    return "\n".join(str(row.get("text") or "") for row in rows
                     if isinstance(row, dict) and row.get("role") == "assistant")


def stated_answers(rows, label):
    """``[(row index, value)]`` of every answer line in a turn's assistant rows, in row order."""
    return [(index, value) for index, row in enumerate(rows)
            if isinstance(row, dict) and row.get("role") == "assistant"
            for value in answer_lines(str(row.get("text") or ""), label)]


class Watch:
    """What a driver saw while a turn ran: the images already captured when the answer was last stated.

    ``observe(rows)`` is called on every driver tick. ``before`` is the
    evidence of the sample before the one in which the reply's answer lines
    last changed (a line appeared, or its value changed), so an image that
    appears in the same sample as the answer, or after it, is not what the
    answer rested on, whatever else the reply says later. In a runtime that
    rewrites one reply row, an answer stated before looking and repeated
    unchanged afterwards therefore stays unsupported.
    """

    def __init__(self, label):
        self.label, self.stated, self.samples = label, None, 0
        self.before = self.latest = visual_evidence([])

    def observe(self, rows):
        stated = [value for _, value in stated_answers(rows, self.label)]
        if stated != self.stated:
            self.stated, self.before = stated, self.latest
        self.latest, self.samples = visual_evidence(rows), self.samples + 1


def no_image_claim(text):
    """The phrase in which a reply says the model had no image to look at, or None."""
    found = NO_IMAGE.search(text or "")
    return found.group(0)[:120] if found else None


def _kinds(entries):
    """``[(kind, count)]`` of a receipt path's ``[[turn, edits, {kind: count}], ...]`` entries."""
    return [(kind, count) for entry in entries if isinstance(entry, list) and len(entry) == 3
            and isinstance(entry[2], dict) for kind, count in entry[2].items() if type(count) is int]


def _shows(kind, required):
    """Whether a delivered picture of ``kind`` (``request_path``) can support a ``required`` visual answer."""
    if required == SCENE_CAMERA:
        return kind.startswith("view:") and kind.endswith(":" + SCENE_CAMERA)
    return kind not in ("attachment", "media")


def image_delivery(path, required):
    """``confirmed``, ``none`` or ``unknown``: whether the receipt proves a picture the answer needs reached the model.

    ``path`` is the request receipt's ``path`` section. Pictures past its bounded per-turn list, and pictures a
    runtime sent without proof of delivery (``unconfirmed_images``), leave it ``unknown`` unless a listed
    delivered one already proves it; so does a receipt without the section or from before its delivery contract.
    """
    if not isinstance(path, dict) or path.get("image_delivery") not in ("confirmed", "unknown", "none"):
        return "unknown"
    delivered, unconfirmed = _kinds(path.get("images") or ()), _kinds(path.get("unconfirmed_images") or ())
    if any(_shows(kind, required) for kind, _ in delivered):
        return "confirmed"
    unlisted = ((path.get("images_delivered") or 0) > sum(count for _, count in delivered)
                or (path.get("images_unconfirmed") or 0) > sum(count for _, count in unconfirmed))
    return "unknown" if unlisted or any(_shows(kind, required) for kind, _ in unconfirmed) else "none"


def supports(evidence, required):
    """Whether captured images could support an answer: any image, or a current view through the scene camera."""
    if not required:
        return True
    return evidence["current_scene_camera"] > 0 if required == SCENE_CAMERA else evidence["images"] > 0


def grade(spec, accept, rows, watch=None, *, delivery=None):
    """One question's answer outcome from its turn's chat rows.

    ``spec`` is the task's ``answer`` (``form`` and, for a visual question,
    ``evidence``); ``accept`` lists the canonical right answers (the ground
    truth and its equivalent spellings). The answer is the last assistant
    message that carries the form's line. Evidence counts only before it:
    by the driver's samples when a ``watch`` observed the turn, else by row
    order. ``delivery`` is the turn's receipt ``path`` section: a visual
    answer needs it to prove the picture reached the model (``image_delivery``).
    """
    form, required = spec["form"], spec.get("evidence")
    label = FORMS[form][0]
    replies = [(index, str(row.get("text") or "")) for index, row in enumerate(rows)
               if isinstance(row, dict) and row.get("role") == "assistant"]
    result = {"form": form, "expected": accept, "answered": None, "evidence_required": required or None,
              "image_captured": visual_evidence(rows)["images"] > 0}
    lines = stated_answers(rows, label)
    if not any(text.strip() for _, text in replies):
        return dict(result, outcome="empty", supported=False)
    read = readings(form, lines[-1][1]) if lines else []
    if not read:
        return dict(result, outcome="no_answer", supported=False)
    # The first reading that is right; a wrong answer is reported as its most lenient reading (what was meant).
    parsed, notes = next(((answer, notes) for answer, notes in read if matches(form, answer, accept)), read[-1])
    row = dict(replies)[lines[-1][0]]
    if text_before_answer(row, label):
        notes = notes + ["other words before the answer on its line"]
    if text_after_answer(row, label) or any(text.strip() for index, text in replies if index > lines[-1][0]):
        notes = notes + ["text after the answer line"]
    sampled = watch is not None and watch.samples > 0
    evidence = watch.before if sampled else visual_evidence(rows, before=lines[-1][0])
    supported = supports(evidence, required)
    result.update(answered=parsed, supported=supported, images_before_answer=evidence["images"],
                  evidence_timing="sampled while the turn ran" if sampled else "row order",
                  stated=lines[-1][1][:200], parsed=LENIENTLY if notes else EXACTLY)
    if notes:
        result["lenient"] = notes
    if not matches(form, parsed, accept):
        return dict(result, outcome="wrong")
    if not required:
        return dict(result, outcome="right")
    # The answering reply and anything after it: an earlier "I can't see it" that a later look answered is gone.
    claim = no_image_claim("\n".join(text for index, text in replies if index >= lines[-1][0]))
    proof = image_delivery(delivery, required)
    result["delivery"] = proof
    if claim:
        result["no_image_claim"] = claim
    if claim or not supported or proof == "none":
        return dict(result, outcome="unsupported")
    return dict(result, outcome="right" if proof == "confirmed" else "unverified")


def answer_of(record):
    """A task record's answer result: the trial driver keeps it in ``host_check``, a benchmark record beside it."""
    host_check = record.get("host_check")
    answer = host_check.get("answer") if isinstance(host_check, dict) else None
    answer = record.get("answer") if answer is None else answer
    return answer if isinstance(answer, dict) else {}


def lenient_counts(records):
    """How many answers a check read over task records, and how many of those it had to read leniently."""
    read = [answer for answer in map(answer_of, records) if answer.get("parsed")]
    return {"read": len(read), "parsed_leniently": sum(answer["parsed"] == LENIENTLY for answer in read)}


def includes_outcome(names, rows):
    """The older, weaker check (``reply_includes``): every name appears in the reply; extra names still pass."""
    reply = reply_text(rows)
    missing = [name for name in names if name not in reply]
    return {"form": "includes", "outcome": "empty" if not reply.strip() else "wrong" if missing else "right",
            "missing": missing}


def constant_pair(first, second):
    """Whether two variants of one question (opposite ground truths) were given the same parsed answer."""
    return (first.get("answered") is not None and first.get("answered") == second.get("answered")
            and first.get("expected") != second.get("expected"))
