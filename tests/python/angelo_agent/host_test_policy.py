# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit environmental skips for the host runner's strict skip policy.

Use this only for unavailable platforms, filesystem capabilities, optional
external fixtures or runtimes. Missing host dependencies remain ordinary skips
and fail CI's --max-skips 0. The marker survives unittest's conversion of a
dynamic SkipTest exception to text, just as it does a skip decorator.

Every skip reason in the host suite says which it is, ``environment_skip`` or
``required_skip``, and test_native_host_skip_reasons.py reads the source for one
that doesn't: an unmarked reason was the author's undecided case, and a platform
or host-capability skip left unmarked failed only the shard where it fired.

``windows_fix_pending`` marks a test of a Windows product defect whose fix has
not landed yet: an expected failure on Windows, listed and counted by the
runner. The fix's own change removes the marker; a pass while it stands is an
unexpected success, which fails the run, so no marker outlives its fix.
"""

import os
import unittest

ENVIRONMENT_PREFIX = "[environment] "


def environment_skip(reason):
    return ENVIRONMENT_PREFIX + reason


def required_skip(reason):
    """A skip CI must never take: a dependency CI installs (NumPy, jsonschema, a tool on PATH) or code this branch
    must contain. The reason is returned unchanged, so it stays unclassified and fails --max-skips 0."""
    return reason


def is_environment_skip(reason):
    return reason.startswith(ENVIRONMENT_PREFIX)


def windows_fix_pending(reason):
    """Decorator: ``reason`` names the defect and its pending fix. Off Windows the test runs as usual."""
    def mark(test):
        if os.name != "nt":
            return test
        test.windows_fix_pending = reason
        return unittest.expectedFailure(test)
    return mark


def pending_reason(test):
    """The ``windows_fix_pending`` reason of a test case's method, or None."""
    return getattr(getattr(test, getattr(test, "_testMethodName", ""), None), "windows_fix_pending", None)
