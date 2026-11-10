# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local approval binding for the exact immutable Fab choice; never a transport credential.

Only public digest/placement appear in the tuple or JSON. This local-only
binding is intentionally refused by the worker's exact-type wire encoder; the
existing Library worker refusal remains unchanged.
The original call retains the private expected selection until its existing execution
owner releases it. Session.prepare independently rechecks that selection on its worker.
"""
from dataclasses import asdict
import hashlib
import json

from .fab_catalog import FabError


class Binding(tuple):
    def __new__(cls, session, selection, *, generation, controller, item_id, variant_id, scene, location):
        item, variant = selection
        # Dataclasses contain admitted strings, numbers and immutable records. JSON
        # rejects unexpected objects rather than reducing private identity to repr().
        private = json.dumps([asdict(item), asdict(variant)], ensure_ascii=True,
                             sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
        digest = hashlib.sha256(private).hexdigest()
        value = tuple.__new__(cls, ('fab', generation, item_id, variant_id, digest, scene, tuple(location)))
        object.__setattr__(value, '_session', session)
        object.__setattr__(value, '_selection', selection)
        object.__setattr__(value, '_controller', controller)
        return value

    def __setattr__(self, key, value):
        raise AttributeError('An approved Fab choice is immutable')

    def expected(self, *, session, generation, controller, item_id, variant_id, scene, location):
        if (session is not self._session or controller is not self._controller
                or self[1:4] != (generation, item_id, variant_id)
                or self[5:] != (scene, tuple(location)) or not session.active):
            raise FabError('EXPIRED')
        return self._selection


def executing(service, *, session, generation, args, scene, location):
    """Read the already-approved call, never reconstruct approval from current metadata."""
    call = getattr(service.controller, 'executing_call', None)
    binding = getattr(call, 'precondition', None)
    if (call is None or call.name != 'angelo_library_import' or type(binding) is not Binding
            or call.arguments != args):
        raise FabError('EXPIRED')
    return binding.expected(session=session, generation=generation, controller=service.controller,
                            item_id=args['item_id'], variant_id=args['variant_id'],
                            scene=scene, location=location)


def review(binding):
    """Only safe display facts from this call's original immutable approval snapshot."""
    if type(binding) is not Binding:
        return None
    item, variant = binding._selection
    from .team_library_files import MAX_BYTES
    choice = variant.browser_choice
    return dict(title=item.title, license=item.license, access=item.access,
                attribution=item.author if item.license == 'CC-BY-4.0' else None,
                version=choice.filename if choice is not None else variant.quality or 'GLB',
                size_bytes=variant.size, maximum_bytes=MAX_BYTES['model'])
