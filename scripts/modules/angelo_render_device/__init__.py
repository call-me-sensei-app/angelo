# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles on the GPU by default (Angelo): the device setup Cycles' add-on runs before a session reads its device.

Contract: doc/angelo/long_jobs.rst, "Render device". ``device`` decides and lists, ``facts`` says what a render
uses and why, ``session`` is the gate Cycles calls. Standard library and ``bpy`` only: it never imports the
agent add-on, which reads these modules as ``angelo_agent.render_device``, ``render_device_facts`` and
``render_device_session``.
"""
