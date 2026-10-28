# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a worker's hosted chat (``worker_chat_host``) meets the other parts of design A′, and the one switch.

A worker Blender hosts its chat's real ``Controller`` (``doc/angelo/agent_integration.rst``, "Worker chat host").
What it needs from the other modules is reached only through the names below:

* **Consent** (``worker_consent``): the standing-consent lease (``apply_consent``, ``LeasedConsent`` mixed into the
  hosted class), the approval digest of each card (``pending_digests``) and the person's Approve (``approve``).
* **Journal ownership** (``worker_journal_owner``): the hosted class's store is an ``OwnedSessionStore``
  (``HostedJournal``): its load is the claim (``JournalTaken`` when another writer changed the file) and
  ``end_hosting`` closes the chat and releases the file. ``journal_owner`` keeps the claim/release seam for tests.
* **Usage** (``worker_usage``): every usage and telemetry record a hosted chat makes is forwarded, never written here.
* **Model runtime transport** (``runtime_relay``): the launcher ``runtime_relay.install`` put in ``cli_runtime``;
  without it no child can start (``unavailable_transport``), so a provider in a worker never spawns a process or
  reads a credential of its own.

``enabled`` is the one reader of the rollout switch ``ANGELO_WORKER_CHAT_HOST`` for the lead and the worker: unset or
anything but ``0`` hosts worker chats in their workers; ``0`` keeps the earlier lanes and shadow path exactly as
they were. Standard library only and no ``bpy``.
"""

SWITCH = "WORKER_CHAT_HOST"   # ``ANGELO_WORKER_CHAT_HOST`` (``product_env``)
NO_RUNTIME = "This worker cannot start a model runtime: the lead's runtime service is not available. Nothing ran."


def enabled(environ=None):
    """Whether worker chats run in their workers (design A′): the default; ``ANGELO_WORKER_CHAT_HOST=0`` turns it off."""
    from . import product_env
    return str(product_env.get(SWITCH, environ, "1")).strip() != "0"


def consent():
    from . import worker_consent
    return worker_consent


class OwnedFile:
    """The file's single writer is the hosted class's own store (``worker_journal_owner.OwnedSessionStore``): its
    load refuses a file another writer changed, and ``end_hosting`` releases it. Nothing to claim beside that."""

    @staticmethod
    def claim(session, path, resume):
        del session, path, resume
        return True

    @staticmethod
    def release(session, path):
        del session, path


def journal_owner():
    return OwnedFile


def hosted_base(controller_type):
    """The hosted chat's base class: consent from the lead's lease, the journal owned here, then the window's
    ``Controller``."""
    from .worker_consent import LeasedConsent
    from .worker_journal_owner import HostedJournal
    return type("HostedBase", (LeasedConsent, HostedJournal, controller_type), {})


def unavailable_transport(*_args, **_kwargs):
    """The transport when the relay is missing: no child starts, so nothing runs."""
    error = RuntimeError(NO_RUNTIME)
    error.partial_changes_possible = False
    raise error


def runtime_transport():
    """The relay's launcher (``runtime_relay.install``), or ``unavailable_transport`` when it is not installed."""
    from . import runtime_relay
    return runtime_relay._installed if runtime_relay.relayed() else unavailable_transport
