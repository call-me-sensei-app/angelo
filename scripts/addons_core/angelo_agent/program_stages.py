# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The staged-job records program jobs keep: a live Stage and its SuspendedStage descriptor."""

from dataclasses import dataclass, field


@dataclass
class Stage:
    scene: object
    collection: object
    view_layer: object
    binding: dict
    context: object = None
    objects: dict = field(default_factory=dict)
    originals: dict = field(default_factory=dict)
    owned_ids: list = field(default_factory=list)
    changes: dict = field(default_factory=dict)
    logical_names: dict = field(default_factory=dict)
    bundle: str = ""
    extras: dict = field(default_factory=dict)
    # Live wrappers of the materials this resident stage borrows (stage_borrowing); never persisted.
    borrowing: object = None

    @property
    def created_ids(self):
        return self.context.created_ids if self.context else self.owned_ids


@dataclass
class SuspendedStage:
    bundle: str
    scene_name: str
    binding: dict
    object_names: dict
    aliases: dict
    changes: dict
    logical_names: dict
    topology_versions: dict = field(default_factory=dict)
    node_bounds: dict = field(default_factory=dict)
    node_metadata: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    # One active job may own a closed graph in a separate native Main. Retained
    # previews and inspection snapshots continue to own a file bundle instead.
    native_owner: object = None
    resident: object = None
