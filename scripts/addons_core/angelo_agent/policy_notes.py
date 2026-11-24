# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the policy left out or applied restrictively, said as the source that set it (doc/angelo/enterprise_policy.rst).

The organization's managed policy (MDM, Group Policy, ``/etc/angelo``) and the person's own policy file
(``ANGELO_POLICY_FILE``) each have their own sentence: a key the person's file named is never reported as the
organization's, and is shown whether or not any managed policy exists (break-it of the policy-file fix, S2). Shown on
AI Providers (its attention notices) and in Preferences' Angelo team section. Strings are translated where shown."""

from .display_text import Text
from .ui_text import n_

MANAGED_PROBLEM = n_("Your organization's Angelo policy has unusable values ({keys}); they were applied in the "
                     "safest way. Contact your admin.")
FILE_IGNORED = n_("Your own policy file (ANGELO_POLICY_FILE) can only turn access off, so Angelo didn't use {keys}. "
                  "Only a managed policy can set them.")
FILE_PROBLEM = n_("Your own policy file (ANGELO_POLICY_FILE) has unusable values ({keys}); they were applied in the "
                  "safest way.")


def organization(policy):
    """The organization's managed policy's note (``Text``), or none."""
    return [Text(MANAGED_PROBLEM, keys=", ".join(policy.problems))] if policy.problems else []


def own_file(policy):
    """The person's own policy file's notes (``Text``): what it couldn't set, then what was unusable in it."""
    return [Text(template, keys=", ".join(keys)) for template, keys in (
        (FILE_IGNORED, policy.ignored), (FILE_PROBLEM, policy.file_problems)) if keys]


def lines(policy):
    """Every note for ``policy``: the organization's, then the person's own file's."""
    return organization(policy) + own_file(policy)
