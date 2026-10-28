# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's images that go with a worker's brief (the worker capability contract, 2026-10-25).

They are taken when the lead starts the part, on the main thread (``taken``): the images of the request that started
it first, then earlier requests' in the conversation, newest first, at most ``MAX_IMAGES``. Taken at the start, never
when the worker is ready: by then the person may have sent a new request with other images (break-it E2E-16 M-3). Each
is copied into the worker chat's own references under the name the person gave it (``attach``), and the brief says
which came with the request that started the part and which are from earlier in the conversation: a fact, never a
claim that every image is this job's.
"""

from .references import MAX_IMAGES

THIS, EARLIER = "this", "earlier"


def taken(lead):
    """[(reference record, ``THIS`` or ``EARLIER``)] of ``lead``'s images now: this request's, then earlier ones'."""
    from .request_rows import person_request
    rows = [row for row in (lead.state.get("messages") or ()) if person_request(row)]   # Never Angelo's (EVENT M-3).
    current = list(getattr(lead, "turn_attachments", None) or (rows[-1].get("references") if rows else None) or ())
    earlier = [reference for row in reversed(rows[:-1]) for reference in (row.get("references") or ())]
    found, seen = [], set()
    for reference, origin in [(r, THIS) for r in current] + [(r, EARLIER) for r in earlier]:
        if (isinstance(reference, dict) and reference.get("kind") == "image" and reference.get("id") not in seen
                and len(found) < MAX_IMAGES):
            found.append((reference, origin))
            seen.add(reference.get("id"))
    return found


def attach(controller, lead, references):
    """``references`` (``taken``) copied into ``controller``'s own store and attached to its next message; the
    sentence its brief ends with ("" when none could be sent: a file that is gone is simply not sent)."""
    paths, kept = [], []
    for reference, origin in references:
        try:
            paths.append(str(lead.references.checked_path(reference)))
        except (ValueError, OSError):
            continue
        kept.append((reference.get("name"), origin))
    if not paths:
        return ""
    copies = controller.references.import_images(paths)
    controller.attachments = [dict(copy, name=name or copy["name"]) for copy, (name, _o) in zip(copies, kept)]
    # By count and order (this request's first), never by name: names can repeat (break-it E2E-17 L-3).
    this = sum(1 for _name, origin in kept if origin == THIS)
    if this == len(kept):
        return "\n\nThe images attached are the person's, sent with the request that started this part."
    if not this:
        return "\n\nThe images attached are the person's, from earlier in the conversation."
    return ("\n\nThe images attached are the person's: the first %d sent with the request that started this part, the "
            "other %d from earlier in the conversation." % (this, len(kept) - this))
