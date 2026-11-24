# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which chat rows a model may read: the test the readers that build a model's text from rows apply.

* ``person_only`` rows (``document_offer.person_only``: an offer, or any row marked ``person_only`` such as a share a
  script was refused, ``share_refusal_notice``) reach no model: not this chat's, not a lead's, not a connected app's.
* ``notice`` rows (``"notice": True``: Angelo's own cards for the person, a live page, a folder, a snapshot) are not
  the chat's own model context either; a connected app reads them (``external_transcript``), as the person would.

``model_hidden`` is the test ``session_rail.turn_end`` applies for a lead model; ``handoff_compose`` applies the same
two checks through its budgeted reader, and ``external_transcript`` applies ``person_only`` alone.
``test_native_row_audience.py`` runs the readers known to build a model's text from rows (handoff, the lead's worker
status, a connected app's rows and results, a rebased context) over a chat holding such rows, and lists the modules
that read a row's ``model_text``. A reader that builds model text from ``text`` alone is not found by that list: add
it to the test's readers when it is written (review of ba222097e18 S4).
"""


def person_only(message):
    from .document_offer import person_only as only
    return only(message)


def model_hidden(message):
    """Whether no model's context may include ``message``: a person-only row or one of Angelo's notices."""
    return isinstance(message, dict) and (message.get("notice") is True or person_only(message))
