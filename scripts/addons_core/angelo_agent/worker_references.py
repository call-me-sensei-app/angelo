# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's attached files that go with a worker's brief (the worker capability contract, 2026-10-25).

They are taken when the lead starts the part, on the main thread (``taken``): the files of the request that started
it first, then earlier requests' images in the conversation, newest first, at most ``MAX_IMAGES`` (a message's own
limit): an earlier job's documents and models stay with that job, never copied again into every later worker.
Taken at the start, never when the worker is ready: by then the person may have sent a new request with other files
(break-it E2E-16 M-3). Each is copied into the worker chat's own references under the name the person gave it
(``attach``), and the brief says which came with the request that started the part and which are from earlier in the
conversation: a fact, never a claim that every file is this job's.

Every kind goes, as a single chat has it: images as pixels, and text, PDFs and 3D assets as the attached-file context
each turn gives (an excerpt and a path, ``references.prompt_context``), which ``angelo_read_reference`` reads further
in a worker's chat too (``worker_refusals.local_tools``). Until 2026-10-28 only images went, so a worker never saw a
text or PDF brief, or a model, the person attached (the Town miss analysis, 13d18cae0f9).
"""

from .references import MAX_IMAGES

THIS, EARLIER = "this", "earlier"


def taken(lead):
    """[(reference record, ``THIS`` or ``EARLIER``)] of ``lead``'s attached files now: this request's, then earlier
    ones'."""
    from .request_rows import own_words, person_request_start
    messages = lead.state.get("messages") or ()
    rows = [row for row in messages if own_words(row) and not row.get("steer")]   # Never Angelo's (EVENT M-3), nor a
    # steer: it belongs to its request (``steering``) and carries no files, so the request's own row stays its last.
    # The request a part continues is the newest that isn't Angelo's own: a worker-done event or an accepted preview goes
    # on with the person's request, while a connected app's task is another requester's, whose files these aren't.
    latest = next((row for row in reversed(messages) if person_request_start(row)), None)
    own = rows[-1] if rows and rows[-1] is latest else None
    current = list(getattr(lead, "turn_attachments", None) or (own.get("references") if own else None) or ())
    earlier = [reference for row in reversed(rows[:-1] if own else rows) for reference in (row.get("references") or ())
               if isinstance(reference, dict) and reference.get("kind") == "image"]   # Other files stay with their job.
    found, seen = [], set()
    for reference, origin in [(r, THIS) for r in current] + [(r, EARLIER) for r in earlier]:
        if (isinstance(reference, dict) and reference.get("kind") and reference.get("id") not in seen
                and len(found) < MAX_IMAGES):
            found.append((reference, origin))
            seen.add(reference.get("id"))
    return found


def attach(controller, lead, references):
    """``references`` (``taken``) copied into ``controller``'s own store and attached to its next message; the
    sentence its brief ends with: which came with what, and any that could not be copied, by name ("" when none
    was taken: a file that is gone is simply not sent)."""
    paths, kept = [], []
    for reference, origin in references:
        try:
            paths.append(str(lead.references.checked_path(reference)))
        except (ValueError, OSError):
            continue
        kept.append((reference.get("name"), origin))
    if not paths:
        return ""
    # One at a time: a file that can't be copied (a full disk, a file changed under it) leaves the others attached,
    # and the brief names it, so the worker never acts as if the person had sent nothing (release-1.0.9 review).
    copies, sent, failed = [], [], []
    for path, (name, origin) in zip(paths, kept):
        try:
            copy, = controller.references.import_images([path])
        except (ValueError, OSError) as error:
            print("Angelo: worker references:", error)
            failed.append((name or "a file", _why(error)))
            continue
        copies.append(copy)
        sent.append((name, origin))
    kept = sent
    controller.attachments = [dict(copy, name=name or copy["name"]) for copy, (name, _o) in zip(copies, kept)]
    return _attached(copies, kept) + _not_sent(failed)


def _why(error):
    """What the person and the worker may read of a copy's failure: never a path (an OSError's text has one)."""
    if isinstance(error, OSError):
        return (error.strerror or "it could not be copied").rstrip(".")
    return str(error).rstrip(".")


def _not_sent(failed):
    if not failed:
        return ""
    names = "; ".join('"%s" (%s)' % (name, why) for name, why in failed)
    return "\n\n%s the person attached could not be sent with this part: %s." % (
        "A file" if len(failed) == 1 else "%d files" % len(failed), names)


def _attached(copies, kept):
    if not copies:
        return ""
    kinds = [copy.get("kind") for copy in copies]
    # By count and order (this request's first), never by name: names can repeat (break-it E2E-17 L-3).
    this = sum(1 for _name, origin in kept if origin == THIS)
    files = "images" if all(kind == "image" for kind in kinds) else "files"
    if this == len(kept):
        return "\n\nThe %s attached are the person's, sent with the request that started this part." % files
    if not this:
        return "\n\nThe %s attached are the person's, from earlier in the conversation." % files
    return ("\n\nThe %s attached are the person's: the first %d sent with the request that started this part, the "
            "other %d from earlier in the conversation." % (files, this, len(kept) - this))
