# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's own words that go with a worker's brief, as their images do (``worker_references``). No bpy.

Evidence: in the 1.0.6 Town benchmark of record (13d18cae0f9, 2026-10-28) every worker read only its lead's paraphrase
of the person's request, never the request itself, while a single chat reads the request whole. One lead's brief asked
for "elegant slender legs" and an "upholstered muted oatmeal seat" the person never asked for and the reference
contradicts; the grade marked that worker's dining chairs down for exactly those. A worker given the person's words
can tell the person's request from its lead's design.

The text is taken when the lead starts the part (``taken``, main thread), never when the worker is ready: by then
the person may have sent more (as the images, break-it E2E-16 M-3). It is every message of the person's own in the
lead's chat so far, oldest first: a request is often several messages (a brief, then an answer to the lead's question,
"Oak"), and the newest alone can be a reply that says nothing of the job. The chat holds no boundary between jobs, so
the header says the earlier ones may be about earlier work. Only rows with no request kind are the person's
(``request_rows.own_words``): never a row Angelo made (the worker-done event, an accepted preview, a memory rebind) or a
connected app's task; an image-only send's stand-in text is not their words either. Up to ``MAX_CHARS`` in all, the
newest are quoted whole first, the oldest that does not fit is cut to its start, and older ones are left out; each is
stated. Each is quoted as mail is, every line behind "> ". The whole first message stays within
``Controller.send``'s limit (``joined``): the messages come before the looked-up schemas, which get the room left
(``worker_schemas.block``'s limit) and are only a saving, since the worker can look them up itself.
"""

SEND_LIMIT = 32000   # ``Controller.send``'s longest message; a brief past it is never sent and its worker stops.
MAX_CHARS = 24000   # Town's whole request is 11.5k characters; past this the oldest messages are cut, then left out.
MIN_CHARS = 200   # A message cut to less than this says too little to quote: it is counted as left out instead.
# Model-visible (never translated): facts about where the brief came from, never an instruction. Each message is
# quoted as a person reads quoted mail, every line behind "> ", so the person's own text can never read as Angelo's
# (no line of it stands unquoted) and the worker's chat shows it plainly, with no markup.
HEADER = ("\n\nThe person's own messages in this chat up to the start of this part, oldest first; the earlier ones may "
          "be about earlier work, and your part is the brief above:")
QUOTE = "> "
LEFT_OUT = "\n\nThe person's %d earlier messages are left out for length."
LEFT_OUT_ONE = "\n\nThe person's one earlier message is left out for length."
CUT = "\n\nThe oldest message shown is cut for length: %d of its %d characters are shown."


def taken(lead):
    """The texts of the person's own messages in ``lead``'s chat now (``request_rows.own_words``), oldest first."""
    from .request_rows import IMAGE_ONLY_TEXT, own_words
    texts = []
    for row in lead.state.get("messages") or ():
        text = row.get("text") if own_words(row) else None
        if isinstance(text, str) and text.strip() and not (text == IMAGE_ONLY_TEXT and row.get("references")):
            texts.append(text.strip())
    return texts


def quoted(text):
    """One message as the brief quotes it: a blank line, then every line of it behind ``QUOTE``."""
    return "\n\n" + "\n".join(QUOTE + line if line else QUOTE.rstrip() for line in text.splitlines())


def _left_out(count):
    return "" if not count else LEFT_OUT_ONE if count == 1 else LEFT_OUT % count


def _start(text, room):
    """``text``'s longest start whose quote fits in ``room`` characters."""
    low, high = 0, min(len(text), max(0, room))
    while low < high:   # Each line's prefix counts too, so the room isn't a number of the text's characters.
        middle = (low + high + 1) // 2
        if len(quoted(text[:middle])) <= room:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def block(messages, room=SEND_LIMIT):
    """The text a worker's brief carries for the person's ``messages`` (``taken``), at most ``room`` characters
    long; "" when there are none or no room for any. The newest are kept whole first; the oldest that does not fit
    is cut to its start (a long first brief keeps its opening), and any older are left out, as stated."""
    room = min(room - len(HEADER), MAX_CHARS)
    kept, cut, left = [], "", 0   # ``kept``: quoted, newest first; ``left``: how many earlier ones are left out.
    for index in range(len(messages) - 1, -1, -1):
        text = messages[index]
        quote, notice = quoted(text), len(_left_out(index))
        if len(quote) + notice <= room:
            kept.append(quote)
            room -= len(quote)
            continue
        total = len(text)
        shown = _start(text, room - notice - len(CUT % (total, total)))
        if len(shown) >= MIN_CHARS:
            kept.append(quoted(shown))
            cut, left = CUT % (len(shown), total), index
        else:
            left = index + 1
        break
    if not kept:
        return ""
    return HEADER + _left_out(left) + "".join(reversed(kept)) + cut


def joined(head, messages, schemas):
    """A worker's first message: ``head`` (its part and image sentence), the person's ``messages`` in what room is
    left, then ``schemas(room)`` (``worker_schemas.block`` within ``room`` bytes) in what room is left after them."""
    head += block(messages, SEND_LIMIT - len(head))
    return head + schemas(SEND_LIMIT - len(head))   # Bytes bound characters: never past the limit.
