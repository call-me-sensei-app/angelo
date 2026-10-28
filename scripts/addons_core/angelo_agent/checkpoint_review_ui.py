# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Honest restore labels for an actual recorded snapshot, never guessed timing.

Capture policy alone is not a receipt that a particular point preceded a user
message. Prefer explicit snapshot scope or an exact-UUID host transcript receipt.
Legacy/missing/ambiguous scope is labelled as recorded state, not before-request.
"""

from .ui_text import n_, notice

# Question and note are shown in the Restore dialog, which translates them. The
# third entry completes the incomplete-recovery notice, which stays in English
# (it carries exception text). The fourth is the stored success notice: a whole
# sentence per scope, so the chat can translate it as it draws (``notice``).
_SCOPES = {
    'before_request': (n_('Restore the document to its recorded state before this request?'),
        n_('Manual edits made after that pre-request capture will be reverted.'),
        'the recorded state before that request',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the recorded state before that request and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
    'before_first_approved_edit': (n_('Restore the document to before its first approved edit?'),
        n_('This deferred snapshot includes manual edits made after sending the request but before the approved edit.'),
        'the recorded state before the first approved edit',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the recorded state before the first approved edit and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
    'before_native_staging': (n_('Restore the document to before Angelo started working on this change?'),
        n_('It includes edits you made before Angelo started, so it may not match the moment you sent the message.'),
        'the state before Angelo started working on that change',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the state before Angelo started working on that change and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
    'before_preview_acceptance': (n_('Restore the document to before you applied this preview?'),
        n_('This snapshot was taken when you accepted the preview, not when you sent the original message.'),
        'the state before you applied that preview',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the state before you applied that preview and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
    'before_preview_acceptance_staging': (n_('Restore the document to before Angelo prepared that preview for applying?'),
        n_('Edits you made before then are kept; this is not the moment you sent the original message.'),
        'the state before Angelo prepared that preview for applying',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the state before Angelo prepared that preview for applying and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
    'before_restore': (n_('Restore the protected snapshot from before the previous restore?'),
        n_('This safety point records the state before that restore operation.'),
        'the protected state before the previous restore',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the protected state before the previous restore and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
    'recorded': (n_('Restore the document to this recorded snapshot?'),
        n_('The exact request/edit boundary was not recorded. No before-message boundary is promised.'),
        'the recorded snapshot state',  # i18n: stored in the English incomplete-recovery notice
        n_('Restored the document to the recorded snapshot state and reverted {count} file changes. '
           'Chat history was kept. The original project is unchanged; use Save As to save the recovered state.')),
}


def checkpoint_review(record, messages=()):
    identity = record.get('id')
    scope = record.get('capture_scope')
    receipts = [message for message in messages if message.get('checkpoint') == identity]
    receipt = receipts[-1] if receipts else {}
    if scope not in _SCOPES:
        scope = receipt.get('checkpoint_scope')
    if record.get('purpose') == 'safety' and any(message.get('restore_checkpoint') == identity for message in messages):
        scope = 'before_restore'
    elif receipt.get('request_kind') == 'native_preview_accept':
        if scope in {'before_native_staging', 'before_first_approved_edit'}:
            scope = 'before_preview_acceptance_staging'
        elif scope in {'before_request', 'before_preview_acceptance'}:
            scope = 'before_preview_acceptance'
    elif scope == 'before_request' and (receipt and receipt.get('role') != 'user'):
        scope = 'recorded'
    # A deferred policy can never be displayed as a before-message guarantee.
    if record.get('capture_mode') == 'BEFORE_FIRST_EDIT' and scope == 'before_request':
        scope = 'recorded'
    if scope not in _SCOPES:
        scope = 'recorded'
    question, note, restored, done = _SCOPES[scope]
    return {'scope': scope, 'question': question, 'note': note, 'restored': restored, 'done': done}


def restore_outcome(review, restored_files):
    """The stored success notice (English ``text``) with the template the chat translates as it draws."""
    return notice(review['done'], count=restored_files)


def restore_notice(review, restored_files):
    return restore_outcome(review, restored_files)['text']


def mark_scene_history(target, record, file_count):
    """Badge each chat row a restore reverted (``reverted``), or left uncertain (``restore_incomplete``).

    Under the chat's lock, which every save holds (rows change only under it, ``session_writer``); the
    badges reach rows of any age, so the chat's next save rewrites its journal whole. A chat hosted in its worker
    (design A′) is left as it is: its file is its worker's, whose part this Restore did not change.
    """
    if getattr(target, "hosted_in_worker", False) is True:
        return
    with target.lock:
        reverted = False
        target.store.invalidate()
        for message in target.state['messages']:
            if message.get('checkpoint'):
                try:
                    reverted = target.checkpoints.load(message['checkpoint'])['created'] >= record['created']
                except (OSError, ValueError, KeyError):
                    reverted = False
            result = message.get('result', {})
            if result.get('changes'):
                message['reverted'] = reverted
            if result.get('file_changes') and file_count is None:
                # A failed file phase may have published only some targets. Do not
                # badge every old file result as reverted based on request timing.
                message.pop('reverted', None)
                message['restore_incomplete'] = True
            elif result.get('file_changes') and file_count is not None:
                message['reverted'] = reverted
