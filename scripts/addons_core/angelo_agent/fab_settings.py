# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared labels and canvas model for the person-owned Fab discovery setting; no bpy."""

from .ui_text import iface_, n_

NAME = 'fab_suggest_paid_assets'
LABEL = n_('Suggest paid assets')
DESCRIPTION = n_('Include Fab assets you do not own that require a purchase. Off shows only free or owned assets. '
               'No automatic purchases or purchase reminders')


def section(preferences):
    return {'title': iface_('Fab asset suggestions'),
            'description': iface_('Choose whether unpurchased paid assets appear in suggestions.'),
            'rows': [{'kind': 'switch', 'name': iface_(LABEL), 'lines': [iface_(DESCRIPTION)],
                      'on': getattr(preferences, NAME, False) is True, 'action': 'toggle', 'value': NAME}]}
