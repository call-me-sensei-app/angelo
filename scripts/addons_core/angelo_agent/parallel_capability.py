# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether this machine can run parallel edit, and how many workers at once: the one capability check.

Parallel edit is for a capable machine (the maintainer, 2026-10-24): a lead Angelo window plus worker Blenders that
each evaluate, save and draw a part. Below the bar the feature does not exist on that machine. The lead's tools never
reach a model and there is no rail; Settings says what it needs (``providers_page_model``). There is no degraded mode.

The bar, from the footprints measured on Mac 1 and Mac 2 (the parallel-agents design, "Memory measurements";
Windows from phase 0's build4 run):

- **A hardware GPU in a windowed Angelo.** The live view and the rail draw on it, and a worker's first offscreen draw
  needs one: on a VM with no display adapter it ended the worker. A software renderer (llvmpipe, SwiftShader,
  Microsoft Basic Render Driver) does not count, and a background Angelo never qualifies.
- **Memory.** ``LEAD_GB`` for the lead window: a medium scene in Material Preview measured 4.1 GB, an F12 EEVEE still
  4.8 GB, with the system beside it. Plus ``WORKER_GB`` per worker: 1.2 GB peak, a worker Blender with its model
  process while it draws (0.75 GB steady). At least ``MIN_WORKERS`` must fit, or the machine is below the bar.
- **Cores.** ``MIN_CORES`` logical cores: each worker evaluates its part on a process of its own while the lead window
  keeps drawing.

How many run at once follows the machine unless the person chose a number (maintainer, 2026-10-25: Workers at once,
``parallel_setting``; Suggested is this count): the workers its memory holds beside the lead, and its logical cores
less ``LEAD_CORES`` for the lead window and the system (at the bar, 8 cores, that is the 4 every earlier build
allowed), never more than ``MAX_WORKERS``, the most parts one lead request starts.

``workers()`` is how many may run at once on this machine: the suggestion. Worker admission
(``worker_sessions.precondition``) and the start tool's limit use ``parallel_access.worker_limit``, which is the
person's number or this, so the bar and the admission are one rule. Measured once per launch. No bpy at import.
"""

import os
import sys
from dataclasses import dataclass, field

LEAD_GB = 8.0
WORKER_GB = 1.2
MIN_WORKERS = 2
MAX_WORKERS = 16   # The most at once on any machine: the most parts one lead request starts.
MIN_CORES = 8
LEAD_CORES = 4     # Logical cores kept for the lead window and the system; each worker beyond them has one.
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swiftshader", "microsoft basic render", "gdi generic", "lavapipe")

_measured = []   # The one measurement of this launch: [Capability].


@dataclass(frozen=True)
class Capability:
    gpu: bool
    gpu_name: str
    memory_gb: float
    cores: int
    workers: int = 0
    missing: tuple = field(default_factory=tuple)   # What is below the bar: "gpu", "memory", "cores".

    @property
    def capable(self):
        return not self.missing


def fit(memory_gb, cores):
    """Workers at once on a machine with this memory and these logical cores, its GPU aside: the workers its memory
    holds beside the lead, a core each beyond ``LEAD_CORES``, never more than ``MAX_WORKERS`` (pure)."""
    memory_fit = int((memory_gb - LEAD_GB) // WORKER_GB) if memory_gb > LEAD_GB else 0
    return max(0, min(MAX_WORKERS, memory_fit, cores - LEAD_CORES))


def assess(gpu, gpu_name, memory_gb, cores):
    """The capability of a machine with these facts (pure: host-tested)."""
    memory_fit = int((memory_gb - LEAD_GB) // WORKER_GB) if memory_gb > LEAD_GB else 0
    missing = tuple(name for name, short in (("gpu", not gpu), ("memory", memory_fit < MIN_WORKERS),
                                             ("cores", cores < MIN_CORES)) if short)
    return Capability(gpu, gpu_name, round(memory_gb, 1), cores, 0 if missing else fit(memory_gb, cores), missing)


def measured_fit():
    """This machine's own count from its measured memory and cores, whatever its GPU: a forced run (a live trial, a
    benchmark arm, a suite) follows the machine as a person's Angelo does (break-it P1-20 H2)."""
    machine = current()
    return fit(machine.memory_gb, machine.cores)


def required_memory_gb():
    """The memory the bar asks for, as Settings says it: the lead plus ``MIN_WORKERS`` workers, whole gigabytes."""
    return int(-(-(LEAD_GB + MIN_WORKERS * WORKER_GB) // 1))


def describe(machine):
    """What the check measured, one line for the app log (gate records, support): the renderer, memory, cores, the
    worker count and what is below the bar. Hardware only: no path, account or name."""
    line = "GPU %s, %.1f GB, %d cores, %d workers" % (machine.gpu_name or "none", machine.memory_gb, machine.cores,
                                                     machine.workers)
    return line + ("; below the bar: " + ", ".join(machine.missing) if machine.missing else "")


def current():
    """This machine's capability, measured once per launch."""
    if not _measured:
        _measured.append(assess(*_gpu(), _memory_gb(), os.cpu_count() or 1))
    return _measured[0]


def _gpu():
    """``(hardware GPU, its name)`` as Blender's GPU module reports it; never in a background Angelo."""
    try:
        import bpy
        if bpy.app.background:
            return False, ""
        import gpu
        backend = gpu.platform.backend_type_get()
        name = gpu.platform.renderer_get() or ""
    except Exception:   # noqa: BLE001 - no GPU module or no context: not a capable machine.
        return False, ""
    hardware = backend in {"METAL", "VULKAN", "OPENGL"} and not any(
        soft in name.lower() for soft in SOFTWARE_RENDERERS)
    return hardware, name


def _memory_gb():
    """Physical memory in GB (10^9 bytes, as machines are sold): sysconf on macOS and Linux, the Win32 API on Windows."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0.0
            return status.total / 1e9
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
    except (AttributeError, OSError, ValueError):
        return 0.0
