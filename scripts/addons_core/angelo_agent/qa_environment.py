# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The test-only switches a worker is started with (``worker_process.QA_ENVIRONMENT``), as this process started:
read once, when the add-on registers, before any agent script runs. A later change to ``os.environ`` (a script's, or
anything else's) never reaches a worker. Absent outside tests (the review of the GPU gate, 5fa49e5f324, N3)."""

import os

NAMES = ("ANGELO_QA_GPU_PROBE_CRASH", "ANGELO_QA_REFUSE_GPU_CONTEXT", "ANGELO_TEST_ISOLATED")
# The test slot's non-secret owner too: a worker's probe child keeps it for the owner-based QA cleanup.
CARRIED = NAMES + ("ANGELO_QA_PROCESS_OWNER",)
# (name, value) pairs: a tuple under an UPPER_CASE name is a constant the confinement puts back, so a script cannot
# change what a later worker or probe is started with (a dict could be changed in place).
STARTED = tuple((name, os.environ[name]) for name in CARRIED if name in os.environ)
