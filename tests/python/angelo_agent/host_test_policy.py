# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit environmental skips for the host runner's strict skip policy.

Use this only for unavailable platforms, filesystem capabilities, optional
external fixtures or runtimes. Missing host dependencies remain ordinary skips
and fail CI's --max-skips 0. The marker survives unittest's conversion of a
dynamic SkipTest exception to text, just as it does a skip decorator.
"""

ENVIRONMENT_PREFIX = "[environment] "


def environment_skip(reason):
    return ENVIRONMENT_PREFIX + reason


def is_environment_skip(reason):
    return reason.startswith(ENVIRONMENT_PREFIX)
