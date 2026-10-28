# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prospective common one-shot quality directive, before any parallel-arm sentence.

This requests iteration within the existing native turn; it neither verifies
semantic quality nor starts another turn. The original brief, reference images,
approval policy, grader and continuous task time/cost budgets remain unchanged.
Historical runs retain their frozen source and original prompt identities.
"""

import hashlib

VERSION = "within-turn-quality-v1"
DIRECTIVE = (
    "Work iteratively against the original brief and attached references. Identify the explicit requirements and "
    "the visual features that distinguish the requested result. Inspect the assembled scene using current overall "
    "and useful local images. Compare what is visible with those requirements, correct material mismatches, and "
    "inspect fresh images after changes. Continue until the requirements are met and no material mismatch remains, "
    "or the task's budget or a concrete blocker prevents further work. Report unmet or unverified requirements "
    "honestly. An object's presence, assigned material, completed worker or successful tool call alone does not "
    "establish visual completion."
)
DIRECTIVE_SHA256 = hashlib.sha256(DIRECTIVE.encode("utf-8")).hexdigest()


def metadata():
    """A detached protocol identity for task and at-send receipts."""
    return {"version": VERSION, "directive_sha256": DIRECTIVE_SHA256}


def apply(tasks):
    """Copy raw common tasks with the directive once, before arm-specific composition.

    Reference identifiers and all other task fields are preserved. Refusing a
    repeated or already split task prevents accidental duplicate/asymmetric text.
    """
    if any("quality_loop" in task or "parallel_request" in task for task in tasks):
        raise ValueError("The quality directive applies once to raw tasks before the parallel split")
    return [dict(task, prompt=task["prompt"] + "\n\n" + DIRECTIVE, quality_loop=metadata()) for task in tasks]
