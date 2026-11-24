# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which module writes a tool family's approval card (``approvals.approval_message``), and the chip a family names
for itself (``approval_kind``) when its tools change no scene. No text of its own: a family's ``CHIP`` is marked
for translation in its module and translated where the card is drawn (``chat_card_draw``)."""

from importlib import import_module

# Tool families whose cards are written by their own module (prefix -> module in this package).
FAMILY_APPROVALS = (("angelo_studio_", "studio_approvals"), ("angelo_flow_", "flow_approvals"),
                    ("angelo_team_", "team_library_approvals"), ("angelo_library_", "library_approvals"),
                    ("angelo_job_", "job_approvals"), ("angelo_app_feature", "app_feature_approvals"),
                    ("angelo_engine_link", "engine_link_tool_approvals"), ("angelo_download", "download_approvals"),
                    ("angelo_engine_fix", "engine_fix_approvals"),
                    ("angelo_context_folder_write", "context_folder_write"))   # A text file in the working folder.


def family_module(name):
    """The module that writes ``name``'s card, or None for a tool ``approvals`` describes itself."""
    for prefix, module in FAMILY_APPROVALS:
        if name.startswith(prefix):
            return import_module("." + module, __package__)
    return None


def approval_kind(name):
    """The untranslated chip a card shows for ``name`` when its family's module names one (``CHIP``), else None: the
    card then says what every other approval says ("Scene changes"). A tool that changes no scene names its own."""
    module = family_module(name)
    return getattr(module, "CHIP", None) if module is not None else None
