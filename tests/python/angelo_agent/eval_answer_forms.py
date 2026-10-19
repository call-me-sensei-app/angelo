# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The answer forms the live-eval harness asks for, and how a reply's answer line is read.

A question's prompt ends with one sentence that asks for a last line in a fixed
form (``FORMS``: a label and its value). ``answer_lines`` finds those lines in
a reply and ``readings`` reads a line's value: the form as asked first, then,
where a model plainly gave the answer and wrapped it in something, a reading
that takes that liberty and names it (``notes``): a count before the names is
dropped when it agrees with their number, units among six numbers are ignored,
a location on its own line is joined, a short clause after a yes or a no is
left unread ("no, it is cut off"), the same shortcut for the other platform is
read when it is named second.

What could hold a second answer is never dropped: a remark after the names
that is not their count ("... (and Suzanne)"), a second shortcut that is not
the other platform's, a second location, a clause that holds a yes or a no of
its own or refuses ("no, I cannot see it"). Without a separator the first word
is not the answer ("no idea", "no part of it is cut off"). Such a value is a
wrong answer or none. ``matches`` compares a reading with the ground truth.

Numeric leniency accepts a bounded vocabulary of labels, metre units and
wrappers, not arbitrary prose surrounding digits. Only explicit context notes
may contain extra numbers: an extrusion count, a prior step, or a render scale.
Other text, units and alternative values are not silently discarded.

The label and its tokens are fixed ASCII, so a line reads the same in every
language the app ships; a translated yes or no is accepted (``YES_NO_WORDS``).
The refusals recognised after a yes or a no are English.
Dependency-free: no ``bpy``, no provider, importable by host tests.
"""

import re
import unicodedata

# form -> (label of the answer line, the sentence a prompt ends with).
FORMS = {
    "yes_no": ("ANSWER", "End your reply with one line that reads exactly 'ANSWER: yes' or 'ANSWER: no'."),
    "counts": ("COUNTS", "End your reply with one line that reads exactly 'COUNTS: <vertices> <edges> <faces>', "
                         "three whole numbers."),
    "names": ("OBJECTS", "End your reply with one line that reads exactly 'OBJECTS: <names>', every object's exact "
                         "name, separated by commas."),
    "render": ("RENDER", "End your reply with one line that reads exactly 'RENDER: <engine> <width>x<height>', for "
                         "example 'RENDER: CYCLES 800x600'."),
    "shortcut": ("SHORTCUT", "End your reply with one line that reads exactly 'SHORTCUT: <keys>', that tool's "
                             "default keyboard shortcut written in English, for example 'SHORTCUT: Ctrl+A'."),
    # The gate sets' two questions (eval_gate): which meshes the scene holds, and where the cube is and how big.
    "mesh_names": ("MESHES", "End your reply with one line that reads exactly 'MESHES: <names>', the exact name of "
                             "every mesh object, separated by commas."),
    "box": ("DIMENSIONS", "End your reply with one line that reads exactly 'DIMENSIONS: <x> <y> <z>; LOCATION: <x> "
                          "<y> <z>', six numbers in metres, for example 'DIMENSIONS: 3 0.25 5; LOCATION: 6 -4 2.5'."),
}
# A second label whose own line continues the answer: "DIMENSIONS: 1 2 1" then "LOCATION: 0 0 1" on the next line.
CONTINUATIONS = {"DIMENSIONS": "LOCATION"}
# Metres: an answer rounded to two decimals of a metre still matches.
BOX_TOLERANCE = 0.005
EXACTLY, LENIENTLY = "exactly", "leniently"
# The line asks for the English tokens; a reply in the app's other languages may still translate them.
YES_NO_WORDS = {
    "en": ("yes", "no"), "ar": ("نعم", "لا"), "de": ("ja", "nein"), "es": ("sí", "no"), "fr": ("oui", "non"),
    "hi": ("हाँ", "नहीं"), "id": ("ya", "tidak"), "it": ("sì", "no"), "ja": ("はい", "いいえ"),
    "ko": ("예", "아니요"), "nl": ("ja", "nee"), "pl": ("tak", "nie"), "pt": ("sim", "não"),
    "pt-BR": ("sim", "não"), "ru": ("да", "нет"), "sv": ("ja", "nej"), "th": ("ใช่", "ไม่ใช่"),
    "tr": ("evet", "hayır"), "vi": ("có", "không"), "zh": ("是", "否"), "zh-TW": ("是", "否"),
}
_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "strg": "ctrl", "⌃": "ctrl", "^": "ctrl",
              "cmd": "cmd", "command": "cmd", "⌘": "cmd", "super": "cmd", "win": "cmd", "oskey": "cmd",
              "alt": "alt", "option": "alt", "opt": "alt", "⌥": "alt", "shift": "shift", "⇧": "shift"}
_MODIFIER_ORDER = ("ctrl", "cmd", "alt", "shift")
# Words a shortcut may be wrapped in: "press Ctrl+B", "the Ctrl+B key".
_FILLER = frozenset({"press", "hold", "the", "key", "keys", "shortcut", "is", "hotkey"})
# A clause after a yes or no: at most this long, and not one of these (English) ways of not answering.
CLAUSE_WORDS, CLAUSE_CHARACTERS = 20, 160
_REFUSALS = ("i cannot", "i can't", "i can not", "i could not", "i couldn't", "cannot see", "can't see", "cannot tell",
             "can't tell", "cannot determine", "can't determine", "cannot verify", "can't verify", "unable to",
             "not able to", "i do not have", "i don't have", "i have not", "i haven't", "i did not", "i didn't",
             "no access", "without looking", "guess", "not sure", "unsure", "uncertain", "not certain", "don't know",
             "do not know", "no idea", "unknown", "unclear", "hard to tell", "impossible to")
_DECORATION = " \t*_`\"'“”‘’«»"
# What may stand before the label on its line: Markdown (quote, list, emphasis, heading) and quotation marks.
_LINE_START = r"[\s>*_`#~\-•'\"\u201c\u201d\u2018\u2019\u00ab\u00bb]*"


def _fold(text):
    """Case and width folded text without combining marks, so 'Sí', 'si' and a full-width 'ＹＥＳ' compare equal
    (and the Turkish dotless i, which has no decomposition, equals a plain one)."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(text)).casefold())
    folded = "".join(c for c in decomposed if not unicodedata.combining(c)).replace("\u0131", "i")
    return unicodedata.normalize("NFKC", folded)


# Other common spellings of the same two answers.
_YES = frozenset(_fold(yes) for yes, _ in YES_NO_WORDS.values()) | frozenset(
    _fold(word) for word in ("y", "true", "네", "是的", "对", "對", "iya"))
_NO = frozenset(_fold(no) for _, no in YES_NO_WORDS.values()) | frozenset(
    _fold(word) for word in ("n", "false", "아니오", "不是", "不", "ไม่"))
# Engines Blender ships: an answer line may dress the name ("Blender EEVEE (Next)").
_ENGINES = ("EEVEE", "CYCLES", "WORKBENCH")


def prompt(question, form):
    """A question's full prompt: the question, then the sentence that asks for the answer line."""
    return "%s %s" % (question, FORMS[form][1])


def _line_pattern(label, keep_label=False, inline=False):
    """One ``LABEL: value`` line, Markdown decoration and quotes ignored; the last group is the value (with
    ``keep_label``, the label too). ``inline`` also finds the label after a sentence on the same line
    ("Two meshes. MESHES: ..."); the group ``lead`` is then that sentence.
    """
    lead = r"(?P<lead>.*?[.!?;:)]\s+)?" if inline else ""
    return re.compile(r"(?i)^%s%s%s%s[\s*_`]*:[ \t]*(.*?)%s[ \t]*$" % (
        lead, _LINE_START, "(" if keep_label else "", re.escape(label), ")" if keep_label else ""))


def _lines(text):
    return unicodedata.normalize("NFKC", str(text)).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def answer_lines(text, label):
    """The value of every ``LABEL: value`` line of ``text``, in order (Markdown decoration ignored).

    A label with a continuation (``CONTINUATIONS``) takes every line of that
    second label that follows it too, joined by line breaks: one such line
    completes the answer, and a second one makes it two answers, which is none.
    """
    pattern, more, found = _line_pattern(label, inline=True), CONTINUATIONS.get(label), []
    following = _line_pattern(more, keep_label=True) if more else None
    for line in _lines(text):
        match = pattern.match(line)
        if match:
            found.append(match.group(match.lastindex))
        elif following and found and following.match(line):
            found[-1] += "\n" + following.match(line).group(1)
    return [value.strip(_DECORATION).rstrip(".。 ").strip(_DECORATION) for value in found]


def _last_answer_line(text, label):
    """``(index, match)`` of the last line of ``text`` that carries the label (its own or its continuation's)."""
    patterns = [_line_pattern(label, inline=True)] + (
        [_line_pattern(CONTINUATIONS[label])] if label in CONTINUATIONS else [])
    found = [(index, pattern.match(line)) for index, line in enumerate(_lines(text)) for pattern in patterns]
    return max((entry for entry in found if entry[1]), key=lambda entry: entry[0], default=(None, None))


def text_after_answer(text, label):
    """Whether anything but decoration follows the last answer line of ``text`` (the line was to end the reply)."""
    last, _ = _last_answer_line(text, label)
    return last is not None and any(line.strip(" \t>*_`#~-•'\"") for line in _lines(text)[last + 1:])


def text_before_answer(text, label):
    """Whether the last answer line of ``text`` starts with other words ("Two meshes. MESHES: ...")."""
    pattern = _line_pattern(label, inline=True)
    matches = [pattern.match(line) for line in _lines(text)]
    last = next((match for match in reversed(matches) if match), None)
    return bool(last and (last.group("lead") or "").strip())


def answer_line(text, label):
    """The value of the last ``LABEL: value`` line of ``text``, or None."""
    found = answer_lines(text, label)
    return found[-1] if found else None


def _integer(token):
    """A whole number, with thousands separators only where they separate groups of three digits."""
    if re.fullmatch(r"\d{1,3}(?:[,._'’ ]\d{3})+", token):
        token = re.sub(r"\D", "", token)
    return int(token) if token.isdecimal() else None


def parse(form, value):
    """The canonical (JSON) value of an answer line as its first reading gives it, or None when it has none."""
    return next((answer for answer, _ in readings(form, value)), None)


def _yes_no(word):
    return "yes" if word in _YES and word not in _NO else "no" if word in _NO and word not in _YES else None


def _yes_no_readings(value):
    """The whole value, else the answer followed by a separator and a short clause: "no, it is cut off".

    A clause that holds a yes or a no of its own ("no, actually yes", and so also "yes, no part is cut off"),
    refuses ("no, I cannot see it") or asks is ambiguous: no answer. Without a separator the first word is
    not the answer: "no idea", "no part of it is cut off".
    """
    folded = _fold(value).replace("\u2019", "'")
    whole = _yes_no(folded.strip(" .!"))
    if whole:
        return [(whole, [])]
    match = re.match(r"^([^\s,.;:!?()\u3002\u3001\u2013\u2014-]+)"
                     r"(?:\s*[,.;:!(\u3002\u3001\u2013\u2014]\s*|\s+-\s+)(\S.*)$", folded)
    answer = _yes_no(match.group(1)) if match else None
    if answer is None or "?" in folded or (len(match.group(1)) == 1 and match.group(1).isascii()):
        return []   # "n. a." is not the letter n and a clause.
    clause = match.group(2)
    words = [word for word in re.split(r"[^\w']+", clause) if word]
    if not words:   # "yes :)": punctuation is no clause.
        return [(answer, ["punctuation after the answer"])]
    if (len(words) > CLAUSE_WORDS or len(clause) > CLAUSE_CHARACTERS or any(word in _YES | _NO for word in words)
            or any(marker in clause for marker in _REFUSALS)):
        return []
    return [(answer, ["a clause after the answer"])]


def _counts_readings(value):
    """Three whole numbers; else exactly three numbers found among words ("12 vertices, 20 edges, 10 faces")."""
    tokens = [token.strip(",;") for token in re.split(r"[\s;/]+", value) if token.strip(",;")]
    numbers = [_integer(token) for token in tokens]
    if len(tokens) == 3 and None not in numbers:
        return [(numbers, [])]
    # A context note is not another count; arbitrary parenthetical answers must remain visible.
    bare = re.sub(r"(?i)\s*\(after \d+ extrusions?\)\s*$", "", value)
    number = r"\d{1,3}(?:[,._'\u2019]\d{3})+|\d+"
    words = {"v", "e", "f", "vert", "verts", "vertex", "vertices", "edge", "edges", "face", "faces",
             "poly", "polys", "polygon", "polygons", "and", "about"}
    if (re.search(r"[-\u2212]\s*\d", bare) or len(re.findall(number, bare)) != 3
            or not _numeric_wrapping(bare, number, words)):
        return []
    kinds = r"(vert|edge|face|poly)[a-z]*"
    for labelled in (re.findall(r"(?i)(%s)\s*%s" % (number, kinds), bare),
                     [(count, kind) for kind, count in re.findall(r"(?i)%s\s*[:=]?\s*(%s)" % (kinds, number), bare)]):
        named = {kind.lower().replace("poly", "face"): _integer(count) for count, kind in labelled}
        if len(labelled) == 3 and set(named) == {"vert", "edge", "face"}:   # "20 edges, 12 vertices, 10 faces".
            return [([named["vert"], named["edge"], named["face"]], ["numbers named, read by their names"])]
    for pattern in (r"\d+", number):
        found = re.findall(pattern, bare)
        if len(found) == 3:
            return [([_integer(found_number) for found_number in found], ["words or punctuation among the numbers"])]
    return []


def _render_readings(value):
    """An engine and a width x height; exact when the value is nothing but those two."""
    number = r"(\d{1,3}(?:[,.']\d{3})+|\d+)"
    sizes = list(re.finditer(r"(?<![\w.+\-\u2212])" + number + r"\s*(?:[xX\u00d7\u2715*]|\bby\b)\s*"
                            + number + r"(?!\d|\.\d)", value))
    if len(sizes) != 1:
        return []   # Two resolutions are competing answers, never a suffix to discard.
    size = sizes[0]
    rest = value[:size.start()] + " " + value[size.end():]
    engine = engine_name(rest)
    if not engine or re.search(r"(?i)\b(?:not|no|isn't|neither)\b", rest):   # "not EEVEE" names no engine.
        return []
    wrapper = re.sub(r"(?i)\s*(?:\(\d+(?:\.\d+)?%\)|at \d+(?:\.\d+)?%)\s*$", "", rest)
    words = {"blender", "eevee", "next", "cycles", "workbench", "render", "engine", "the", "at",
             "resolution", "size", "px", "pixel", "pixels"}
    if re.search(r"\d", wrapper) or (engine in _ENGINES and not _numeric_wrapping(wrapper, r"(?!)", words)):
        return []
    exact = (not value[size.end():].strip() and "by" not in size.group(0)
             and re.fullmatch(r"[\sA-Za-z0-9_()]*", rest)
             and set(re.findall(r"[A-Z0-9]+", rest.upper().replace("_", " "))) <= {engine, "BLENDER", "NEXT"})
    return [([engine, _integer(size.group(1)), _integer(size.group(2))],
             [] if exact else ["words or punctuation around the engine and the size"])]


def _words_after_a_key(tokens):
    """Whether what follows a shortcut's key is prose ("on a Mac", "then drag") and not another key ("then S")."""
    for index, token in enumerate(tokens):
        article = token == "a" and index and tokens[index - 1] in {"on", "in", "for", "with"}
        if token in _MODIFIERS or not (article or (len(token) > 1 and token.isalpha())):
            return False
    return True


def _shortcut(named):
    """``(canonical shortcut, whether words around it were dropped)`` of one named shortcut, or ``(None, False)``."""
    named = re.sub("([\u2303\u2318\u2325\u21e7^])", r"\1+", named)   # Glyphs are written without a separator: \u2318B.
    tokens = [token for token in re.split(r"[\s+\-\u2013]+", named) if token]
    kept = [token for token in tokens if token not in _FILLER]
    keys = [token for token in kept if token not in _MODIFIERS]
    if len(keys) > 1 and len(keys[0]) == 1 and _words_after_a_key(kept[kept.index(keys[0]) + 1:]):
        kept = kept[:kept.index(keys[0]) + 1]   # "Cmd+B on a Mac": the shortcut, then where it applies.
        keys = keys[:1]
    if len(keys) != 1:
        return None, False
    modifiers = {_MODIFIERS[token] for token in kept if token in _MODIFIERS}
    return "+".join([name for name in _MODIFIER_ORDER if name in modifiers] + [keys[0]]), len(kept) != len(tokens)


def _other_platform(shortcut):
    """The same keys with Ctrl and Cmd exchanged: what a shortcut is called on the other platform."""
    swap = {"ctrl": "cmd", "cmd": "ctrl"}
    return "+".join(sorted((swap.get(key, key) for key in shortcut.split("+")[:-1]), key=_MODIFIER_ORDER.index)
                    + shortcut.split("+")[-1:])


def _shortcut_readings(value):
    """The shortcut named. A second one is a reading too only when it is the first for the other platform
    ("Cmd+B / Ctrl+B", "Ctrl+B (Cmd+B on a Mac)"): any other second shortcut is a second guess and unread.
    """
    parts = [part for part in re.split(r"[(),;/]|\bor\b", _fold(value)) if part.strip()]
    named = [_shortcut(part) for part in parts]
    named = [entry for entry in named if entry[0] is not None]
    if not named:
        return []
    (first, filler), more = named[0], ["more after the shortcut"] if len(parts) > 1 else []
    output = [(first, more + (["words around the shortcut"] if filler else []))]
    if len(named) == 2 and named[1][0] == _other_platform(first) != first:
        output.append((named[1][0], ["the shortcut for the other platform, named second"]))
    return output


def _names(value):
    names = [name.strip(_DECORATION + ".") for name in re.split(r"[,;、،]", value)]
    return sorted(name for name in names if name)


def _count(word):
    """The number a count word states: 3, "three", "both"; None for "all" (any number)."""
    word = word.casefold()
    return int(word) if word.isdecimal() else {"both": 2, "all": None}.get(word, _NUMBER_WORDS.index(word) + 1
                                                                        if word in _NUMBER_WORDS else None)


def _names_readings(value):
    """Names as written, then with a count taken off the front or the back and an "and" read as a comma.

    Only a count may be dropped ("2: ...", "... (2 meshes)", "... - 2 in total"), and only one that agrees with
    the number of names: any other remark could hold a further name ("... (and Suzanne)"), so it stays part
    of the last name and the answer is wrong.
    """
    exact, loose, notes, counts = _names(value), value, [], []
    before = re.match(_COUNT_BEFORE, loose)
    if before and loose[before.end():].strip():
        loose, notes, counts = loose[before.end():], notes + ["a count before the names"], [before.group("count")]
        if before.group(0).rstrip().endswith("("):   # "2 (Cube, Plate)": the bracket closes after the names.
            loose = re.sub(r"\)\s*$", "", loose)
    after = re.search(_COUNT_AFTER, loose)
    if after and loose[:after.start()].strip():
        loose, notes = loose[:after.start()], notes + ["a count after the names"]
        counts.append(after.group("count") or after.group("count2"))
    joined = re.sub(r"(?i)(?:,\s*|\s+)(?:and|&)\s+", ", ", loose)
    if joined != loose:
        loose, notes = joined, notes + ["names joined by 'and'"]
    lenient = _names(loose)
    agrees = all(_count(count) in (None, len(lenient)) for count in counts)
    return ([(exact, [])] if exact else []) + (
        [(lenient, notes)] if notes and lenient and lenient != exact and agrees else [])


_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_COUNT_WORD = r"(?P<count>\d+|%s|both|all)" % "|".join(_NUMBER_WORDS)
# What a count may be followed by: "2 mesh objects: ...". Any other word could be a name.
_COUNTED = r"(?:\s+(?:mesh objects?|meshes|mesh|objects?|names?|items?))?(?:\s+(?:in total|total))?"
_COUNT_BEFORE = (r"(?i)^\s*(?:(?:there (?:are|is)|i (?:see|count|found)|total(?: of)?|count)\s+)?\(?"
                 + _COUNT_WORD + r"\)?" + _COUNTED + r"\s*[:;,\-\u2013\u2014(]\s*")
_COUNT_AFTER = (r"(?i)\s*(?:\(\s*" + _COUNT_WORD + _COUNTED + r"\s*\)|[;,\u2013\u2014-]\s*"
                + _COUNT_WORD.replace("count", "count2") + _COUNTED + r")\s*$")
# A number of an answer: a sign, digits, a decimal point, an exponent. Not a digit of a word ("3D", "v2");
# a unit or the "x" between dimensions may touch it ("2m", "2x2x2").
_NUMBER = r"(?<![A-WYZa-wyz\d.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?(?![A-LN-WYZa-ln-wyz\d])"
_BOX = re.compile(r"\s*(%s)\s+(%s)\s+(%s)\s*;\s*LOCATION\s*:\s*(%s)\s+(%s)\s+(%s)\s*" % ((_NUMBER,) * 6), re.I)


def _numeric_wrapping(value, number, words):
    """Numbers may carry only declared labels/units and separator punctuation.

    This is a grammar boundary, not a translation or sentiment check: an
    unknown word may deny or qualify a number in any language, so it stays
    unread. The fixed requested form itself remains language-independent.
    """
    rest = re.sub(number, " ", value)
    tokens = re.sub(r"[\s,;:=()/\u00d7\u2715*+_\[\]<>`.'\"\u2018\u2019\u201c\u201d\u2013\u2014&-]+",
                    " ", rest).split()
    return all(token.casefold() in words for token in tokens)


def _box_readings(value):
    """Three dimensions and three coordinates: as the form writes them, else found among units and punctuation."""
    text = value.replace("\u2212", "-")   # A typographic minus sign.
    exact = _BOX.fullmatch(text)
    if exact:
        return [([float(number) for number in exact.groups()], [])]
    notes = ["the location on its own line"] if "\n" in text else []
    # As written; then with a closing remark taken off ("... (unchanged since step 3)"), then with decimal commas.
    closing = re.search(r"\s*\(([^()]*)\)\s*$", text)
    # Only this context note can contain a number that is not one of the six coordinates.
    droppable = bool(closing and re.fullmatch(r"(?i)unchanged(?: since (?:step|turn) \d+)?", closing.group(1)))
    words = {"x", "y", "z", "m", "metre", "metres", "meter", "meters", "location", "at", "in", "the",
             "3d", "viewport"}
    candidate = text[:closing.start()] if droppable else text
    if not _numeric_wrapping(candidate, _NUMBER, words):
        return []
    for remark, decimal_commas in ((False, False), (True, False), (False, True), (True, True)):
        if remark and not droppable:
            continue
        parts = re.split(r"(?i)\blocation\b", text[:closing.start()] if remark else text)
        found = [re.findall(_NUMBER, re.sub(r"(?<=\d),(?=\d)", ".", part) if decimal_commas else part)
                 for part in parts]
        if len(parts) == 1 and len(found[0]) == 6:
            found, labelled = [found[0][:3], found[0][3:]], ["the location not labelled"]
        else:
            labelled = []
        if [len(part) for part in found] == [3, 3]:   # Two location labels are two answers: none.
            notes = (notes + labelled + (["a remark after the numbers"] if remark else [])
                     + (["decimal commas"] if decimal_commas else []))
            return [([float(number) for part in found for number in part],
                     notes or ["units or punctuation among the numbers"])]
    return []


def readings(form, value):
    """Every way an answer line's value can be read, the form as asked first: ``[(answer, notes)]``.

    ``notes`` is empty for a value written as the form asks; otherwise each
    note names one liberty the reading took. No reading: the value is not an
    answer. ``eval_answers.grade`` takes the first reading that is right; a
    wrong answer is reported as its last, most lenient reading. Never raises.
    """
    if form not in _READINGS:
        raise ValueError("Unknown answer form %r" % form)
    found = _READINGS[form](value)
    bare = re.sub(r"[<>\[\]`]", "", value)   # The prompt writes <names>; a reply may keep the brackets.
    if bare != value:
        seen = [answer for answer, _ in found]
        found += [(answer, notes + ["brackets or backticks in the answer"])
                  for answer, notes in _READINGS[form](bare) if answer not in seen]
    return found


_READINGS = {"yes_no": _yes_no_readings, "counts": _counts_readings, "names": _names_readings,
             "mesh_names": _names_readings, "render": _render_readings, "shortcut": _shortcut_readings,
             "box": _box_readings}


def matches(form, answer, accept):
    """Whether a read answer is one of the accepted ones (a box within ``BOX_TOLERANCE`` of one of them)."""
    if form == "box":
        return any(len(answer) == len(right) and all(abs(a - b) <= BOX_TOLERANCE + 1e-9 for a, b in zip(answer, right))
                   for right in accept)
    return answer in accept


def engine_name(text):
    """A render engine's bare name: ``BLENDER_EEVEE_NEXT``, ``Eevee`` and ``EEVEE Next`` are all ``EEVEE``.

    One of Blender's own engines is recognised among other words; two of them
    in one answer name none. Any other engine is its words joined.
    """
    words = [word for word in re.findall(r"[A-Z0-9]+", str(text).upper().replace("_", " "))
             if word not in {"BLENDER", "NEXT", "RENDER", "ENGINE", "THE"}]
    known = [name for name in _ENGINES if name in words]
    return known[0] if len(known) == 1 else "" if known else "".join(words)
