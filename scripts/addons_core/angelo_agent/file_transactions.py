# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scoped file effect and its journal own one profile transaction."""

from contextlib import contextmanager
from .checkpoint_transactions import _transaction
from .checkpoint_timeline import version_key


class FileEffect:
    def __init__(self, store, identity, root, relative, before):
        self.store, self.identity = store, identity
        self.root, self.relative, self.before = str(root), relative, before
        self.entry = None
        self.applied = False
        self.uncertain = False

    def release_unused(self):
        current = self.store.load(self.identity)
        previous = {version_key(item): item for item in self.before}
        for item in reversed(current["files"]):
            if item["root"] == self.root and item["path"] == self.relative:
                prior = previous.get(version_key(item))
                if item != prior:
                    self.store.rollback_journal(self.identity, item, prior)


@contextmanager
def journaled_file_effect(store, identity, root, relative):
    """No automatic retry or live rollback after an applied effect."""
    with _transaction(store.root):
        effect = FileEffect(store, identity, root, relative, store.load(identity)["files"])
        try:
            effect.entry = store.journal(identity, root, relative, pending=True)
            yield effect
            if not effect.applied:
                raise ValueError("The prepared file effect did not complete")
            store.commit_journal(identity, effect.entry)
        except (OSError, ValueError) as error:
            try:
                if effect.applied:
                    # A publication can succeed before its acknowledgement fails.
                    store.commit_journal(identity, effect.entry)
                elif not effect.uncertain:
                    effect.release_unused()
            except (OSError, ValueError) as cleanup:
                error.add_note("File journal cleanup needs review: " + str(cleanup))
                error.partial_changes_possible = True
            else:
                error.partial_changes_possible = effect.applied or effect.uncertain
            raise
