Darwin PDF resource policy
==========================

Linux uses a hard 1 GiB address-space ceiling before PDF access, and Windows a
hard 1 GiB committed-memory Job ceiling. macOS allocator reservations can greatly
exceed useful physical footprint, making that same small address-space ceiling
unusable for the bundled interpreter. Raising the virtual ceiling or describing
RSS sampling as a hard cap would not preserve the same guarantee.

macOS instead uses a sampled physical-
footprint termination threshold of the same 1 GiB. It is a material tradeoff:
scheduler delay and allocation between observations permit overshoot, and an entire
peak can be missed. Neither the threshold nor the 50 ms requested pipe wait bounds
peak allocation or establishes a real-time scheduling guarantee. Linux hard AS and
Windows suspended Job admission remain unchanged. No allocator security setting,
provider process default, machine policy, entitlement, root access or external
runtime is changed. Actual OS and release acceptance remain separate gates.

Owner and admission
-------------------

``pdf_process_output.run`` owns the actual unreaped Popen child. On Darwin its
stdin is a private pipe, rather than DEVNULL. ``pdf_darwin_watchdog`` obtains the
public ``proc_pid_rusage`` V0 start timestamp and physical footprint for this PID.
The process must have a nonzero start timestamp, positive footprint below the
threshold, and no exit timestamp. The parent records PID plus start timestamp;
the executable UUID alone is not process identity. It rechecks before sending one
bounded token, and closes stdin afterward. The worker loads only its sibling
helper and validates that token against its own PID, start timestamp and supplied
threshold before any PDF stat/open, parser import or page-tree enumeration.
EOF, malformed/stale admission or failed accounting refuses with ``resource``.
This is an ownership handshake, not cryptographic proof that a parent continues
monitoring. A child blocked at the barrier is still covered by the parent deadline.

The same parent loop samples before bounded pipe reads and continues after stdout
EOF until process exit. It introduces no detached reader/monitor thread or queue.
Live accounting failure, identity mismatch or threshold crossing aborts extraction;
existing cleanup kills/reaps the owned process and closes both pipes. A disappearing
accounting record is treated as normal completion only when the actual Popen owner
confirms that its child exited. The direct child remains unreaped until that point,
so its PID cannot be reused during ordinary accounting checks. A changed sample
never supplies a different PID to signal. Failure to reap is reported by the
existing cleanup contract, not mislabeled as successful drain.

Other limits and observations
-----------------------------

The 20 MiB input, first 64 pages, 1,000,000 extracted characters, 20-second page
budget, 30-second child deadline, bounded stdout/discarded stderr, isolated bundled
reader, parser stream/work limits and no-external-decoder rule remain. Memory
sampling and cleanup do not turn atomic OS calls into a strict walltime bound.
The parent refuses sampled crossings as ``limit`` and missing accounting as
``resource``; file-level errors retain the existing locked/empty/unreadable
classification. No file is parsed in Blender's interpreter or on its UI thread.

Required evidence before support acceptance
------------------------------------------

The new mocked fixtures are intended to verify branch ordering, owner checks, finite handshake,
accounting failure, threshold rejection, post-EOF monitoring and timeout/output
cleanup. They make no peak-memory or actual-OS claim. Existing mocked output tests
explicitly select Linux so they continue to verify the unchanged hard-admission
route, while new Darwin facade tests cover its distinct policy. Existing Windows
Job and Linux hard-AS fixtures must remain green, without softened counts.

A separately reviewed REV-only run must use the exact production interpreter,
command, helper hashes and arguments. It must extract a plain text PDF, classify
locked/image-only inputs, exercise a bounded allocation threshold refusal, preserve
timeout/output cleanup, and independently prove owned-child drain. Retain the old
before-parser refusal receipt separately. All runtime, build, package, model-facing
wording and release acceptance gates remain open until their actual evidence exists.

Source basis: installed public SDK libproc.h and sys/resource.h ``rusage_info_v0``;
Apple XNU xnu-12377.1.9 vm_map.c ``vm_map_set_size_limit`` explains the old refusal.
No private memorystatus or footprint-limit setter is used.
