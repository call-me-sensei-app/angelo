# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The machine factor for absolute timing limits (qa_waits.machine_limit), measured by run_native_suites.py.

The factor is qa_reference_workload.py's median time on this machine, in the app under test, divided by its time on
the reference Mac the limits were calibrated on (REFERENCE_SECONDS, an Apple M-series Mac, recorded in
doc/angelo/agent_execution.rst). Never below 1 and never padded: a slower machine gets exactly its measured ratio.
Standard library only (host side).
"""

import json
import re
import subprocess
from pathlib import Path

# The reference Mac (Apple M-series, the release Mac): qa_reference_workload.py's median in Angelo 1.0.0 (096b2bd3714),
# three launches 0.1806 / 0.1808 / 0.1795 s at load ~5, 2026-10-20 (doc/angelo/agent_execution.rst).
REFERENCE_SECONDS = 0.1806
WORKLOAD = Path(__file__).resolve().with_name("qa_reference_workload.py")
_LINE = re.compile(r"^ANGELO_QA_REFERENCE (\{.*\})\s*$", re.M)


def measure(binary, run=subprocess.run, timeout=300):
    """The reference workload's median seconds in ``binary`` (background, factory settings)."""
    done = run([str(binary), "--background", "--factory-startup", "--python", str(WORKLOAD)],
               capture_output=True, text=True, timeout=timeout)
    found = _LINE.search(done.stdout or "")
    if not found:
        raise RuntimeError("the reference workload printed no result (exit %s)" % done.returncode)
    return float(json.loads(found.group(1))["seconds"])


def factor(seconds, reference=None):
    """This machine's factor: measured over reference seconds, never below 1."""
    reference = REFERENCE_SECONDS if reference is None else reference
    if not reference or reference <= 0 or seconds <= 0:
        raise ValueError("a factor needs positive measured and reference seconds")
    return max(1.0, round(seconds / reference, 3))


def resolve(option, binary, run=subprocess.run):
    """``--machine-factor``: "auto" measures (1 until a reference is recorded), "off" is 1, a number as given."""
    if option == "off":
        return {"factor": 1.0, "source": "off"}
    if option != "auto":
        value = float(option)
        if value <= 0:
            raise ValueError("--machine-factor must be positive")
        return {"factor": max(1.0, value), "source": "given"}
    if not REFERENCE_SECONDS:
        return {"factor": 1.0, "source": "no reference recorded"}
    try:
        seconds = measure(binary, run=run)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        # Fail strict: the calibrated limits stand when this machine could not be measured.
        return {"factor": 1.0, "source": "measurement failed: %s" % error}
    return {"factor": factor(seconds), "source": "measured", "seconds": seconds, "reference": REFERENCE_SECONDS}
