# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Person-owned Fab discovery preference. Main-thread RNA; providers receive a thread-safe policy copy.

No purchase, acquisition, notification or license-acceptance action exists here. Session construction is reserved
for the future verified account owner; registering this preference does not enable an unverified connector.
"""

import weakref
from bpy.props import BoolProperty
from .ui_text import iface_

_sessions = weakref.WeakSet()
from .fab_settings import NAME, LABEL, DESCRIPTION


def _changed(preferences, _context):
    for session in tuple(_sessions):
        session.set_suggest_paid(getattr(preferences, NAME) is True)


class FabPreferences:
    fab_suggest_paid_assets: BoolProperty(name=LABEL, description=DESCRIPTION, default=False, update=_changed)


def create_session(root, account_scope, transport, preferences):
    """Called on the main thread by the account owner after login is independently verified."""
    from .fab_provider import Session
    session = Session(root, account_scope, transport, suggest_paid=getattr(preferences, NAME, False) is True)
    _sessions.add(session)
    return session


def draw(layout, preferences):
    box = layout.box()
    box.label(text=iface_('Fab asset suggestions'), translate=False)
    box.prop(preferences, NAME)
    box.label(text=iface_('No automatic purchases or purchase reminders.'), translate=False)
