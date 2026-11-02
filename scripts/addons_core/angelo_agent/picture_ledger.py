# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which pictures of a request reached the model: the picture half of ``request_path.PathFacts``.

A picture is in exactly one of four places once the request ends:

* delivered (``images``): proven in the model's input. The Claude runtime proves
  it when the provider acknowledges the message or round that carries it
  (``pictures(staged=True)`` then ``accepted``). A Codex runtime that reports its
  model input (``image_proof``) proves it by content: a picture is ``expect``-ed
  with the SHA-256 of the exact bytes Angelo handed over, and ``confirm`` matches
  the runtime's report of the decoded bytes it sent (or, for a picture it
  re-encoded, of the bytes as it received them) to it. Nothing else matches: no
  tool name, question or kind.
* not delivered (``missing``): expected by content but never reported by the end
  of the request (``settle``); a negative, provable only on such a runtime.
* unconfirmed: written to a Codex runtime that cannot say whether the pixels
  reached the model; never a negative.
* pending: expected and not reported yet, while the request runs. A receipt
  taken before the request ends leaves them out.

Each group is ``[turn, capture edits, {kind: count}]``; the request's
``PathFacts`` owns the lock, the turn count and the known pictures' kinds.
"""

from collections import deque

MAX_IMAGE_TURNS, MAX_KINDS = 12, 6
MAX_PENDING = 128  # Expected pictures and early reports kept per request; the oldest is settled first.


class Jobs(list):
    """The IDs of the jobs whose pictures one call delivered; ``files`` is the output each picture showed, or None."""

    def __init__(self, identities=(), files=()):
        super().__init__(identities)
        self.files = list(files)


class PictureLedger:
    """Mixed into ``PathFacts``: needs its ``_lock``, ``turns`` and ``_known``."""

    def _init_pictures(self):
        self.images = self.images_unconfirmed = self.images_not_delivered = 0
        self.image_turns = deque(maxlen=MAX_IMAGE_TURNS)
        self.unconfirmed_turns = deque(maxlen=MAX_IMAGE_TURNS)
        self.missing_turns = deque(maxlen=MAX_IMAGE_TURNS)
        self._staged = []  # (kind, output, capture edits), not yet accepted by the provider.
        self.proof = False  # The runtime of this request's latest turn reports its model input (image_proof).
        self._pending = []  # [digests, (kind, output, capture edits)] expected by content, not reported yet.
        self._early = []  # Digest pairs reported before Angelo recorded the picture they show (a write's race).
        self._responses = []  # The model requests already reported: a repeated report delivers nothing more.

    def _kinds(self, records):
        return [self._known.get(record.get("id")) or ("attachment", None, None) for record in records
                if isinstance(record, dict) and str(record.get("mime", "")).startswith("image/")]

    def pictures(self, records, *, staged):
        """Pictures staged for a provider, or acknowledged tool-result pictures (``staged=False``).

        Returns the IDs of the jobs whose pictures were delivered by this call.
        """
        with self._lock:
            kinds = self._kinds(records)
            if staged:
                self._staged = kinds  # A message that was never accepted is replaced, not counted.
                return Jobs()
            return self._deliver(kinds)

    def accepted(self, turn=None):
        """A provider input was acknowledged; ``turn`` identifies its submitted turn when supplied."""
        with self._lock:
            self.turns = turn if turn is not None else self.turns + 1
            staged, self._staged = self._staged, []
            return self._deliver(staged)

    def unconfirmed(self, records):
        """A successful transport write whose model image delivery is not observable."""
        with self._lock:
            kinds = self._kinds(records)
            self.images_unconfirmed += len(kinds)
            self._group_images(self.unconfirmed_turns, kinds)

    def prove(self, enabled):
        """Whether this request's runtime reports the pictures in its model input (``image_proof.begin``)."""
        with self._lock:
            self.proof = bool(enabled)

    def expect(self, records, digests):
        """Pictures handed to a runtime that reports its model input, with the SHA-256 of each one's bytes.

        ``digests`` pairs with the image records of ``records`` in order. A picture its runtime already reported
        (``confirm`` ran first) is delivered at once. Returns the jobs whose pictures were delivered.
        """
        with self._lock:
            delivered = []
            for kind, digest in zip(self._kinds(records), digests):
                early = next((pair for pair in self._early if digest in pair), None)
                if early is not None:
                    self._early.remove(early)
                    delivered.append(kind)
                    continue
                if len(self._pending) >= MAX_PENDING:
                    self._miss([self._pending.pop(0)[1]])
                self._pending.append([digest, kind])
            return self._deliver(delivered)

    def confirm(self, images, response=None):
        """The runtime sent these pictures to the model in request ``response``: ``[(sha256, source or None)]``.

        Each reported picture delivers at most one expected picture with the same bytes, oldest first, and is
        then spent, so no picture is delivered twice. One that matches nothing yet is kept for a picture recorded
        just after it (the runtime may forward a result before its write returns here). Returns the jobs whose
        pictures were delivered.
        """
        with self._lock:
            if response is not None:
                if response in self._responses:
                    return Jobs()
                self._responses = (self._responses + [response])[-MAX_PENDING:]
            delivered = []
            for pair in images:
                digests = {digest for digest in pair if digest}
                found = next((entry for entry in self._pending if entry[0] in digests), None)
                if found is not None:
                    self._pending.remove(found)
                    delivered.append(found[1])
                elif digests:
                    if len(self._early) >= MAX_PENDING:
                        self._early.pop(0)
                    self._early.append(tuple(digests))
            return self._deliver(delivered)

    def settle(self):
        """The request ended: every picture still expected was not delivered."""
        with self._lock:
            self._settle()

    def _settle(self):
        pending, self._pending, self._early = self._pending, [], []
        self._miss([kind for _digest, kind in pending])

    def _miss(self, kinds):
        self.images_not_delivered += len(kinds)
        self._group_images(self.missing_turns, kinds)

    def _deliver(self, kinds):
        if not kinds:
            return Jobs()
        self.images += len(kinds)
        self._group_images(self.image_turns, kinds)
        shown = [(kind[4:], output) for kind, output, _captured in kinds if kind.startswith("job:")]
        return Jobs((job for job, _ in shown), (output for _, output in shown))

    def _group_images(self, entries, kinds):
        for kind, _output_shown, captured in kinds:
            entry = next((entry for entry in reversed(entries)
                          if entry[:2] == [max(self.turns, 1), captured]), None)
            if entry is None:
                entry = [max(self.turns, 1), captured, {}]
                entries.append(entry)
            if kind in entry[2] or len(entry[2]) < MAX_KINDS:
                entry[2][kind] = entry[2].get(kind, 0) + 1

    def picture_section(self, settled):
        """The receipt's picture fields, under the caller's lock; ``settled`` (the request ended) counts what is
        still expected as not delivered."""
        if settled:
            self._settle()
        groups = lambda entries: [[turn, edits, dict(kinds)] for turn, edits, kinds in entries]  # noqa: E731
        path = {}
        if self.images_unconfirmed:
            path.update(images_unconfirmed=self.images_unconfirmed, unconfirmed_images=groups(self.unconfirmed_turns))
        if self.images_not_delivered:
            path.update(images_not_delivered=self.images_not_delivered, not_delivered_images=groups(self.missing_turns))
        if self.images:
            path.update(images_delivered=self.images, images=groups(self.image_turns))
        return path
