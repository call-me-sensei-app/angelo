# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Claude typed plan's requested views: bounded, deduplicated and refreshed after edits.

``ClaudeProvider.run_request`` uses it as ``providers._ClaudeSculptEvidence``.

Every view a plan asked for is kept and captured again after each later edit,
so the model always verifies against the current scene. Three bounds keep
that from costing more than it gives (doc/angelo/agent_execution.rst, "Visual
observation"):

* a number of kept views per request (``VIEW_IMAGE_LIMIT``); a plan over it is
  returned with the number left (``problem``, read by ``plan_validation.screen_plan``);
* a time one edit may spend capturing them again (``REFRESH_SECONDS``). The
  captures draw on Blender's main thread, and on a heavy scene one view takes
  seconds: the views asked for most recently are captured first, and the rest
  are reported as stale instead of being captured (``refresh``);
* a capture that fails does not end the turn: the view is reported as stale
  with its error.

A stale view is never attached. The model reads which views are stale and
asks for one again, with the same arguments, when it needs it.
"""

import copy
import json
import time

from .protocol import RpcError

# Shared by sculpt views/reviews and angelo_view: all of them are refreshed
# after a later mutation and count toward one bounded per-request image budget
# (VIEW_IMAGE_LIMIT distinct images). Sculpt views and reviews also hold a
# native camera each, and a review its baseline, in the 12-camera store of
# ``references.remember_observation``; they keep their own bound within it.
VIEW_IMAGE_LIMIT = 12
CLAUDE_SCULPT_IMAGE_LIMIT = 6
SCULPT_IMAGE_TOOLS = frozenset({'angelo_sculpt_view', 'angelo_sculpt_review'})
VISUAL_IMAGE_TOOLS = SCULPT_IMAGE_TOOLS | {'angelo_view'}
# A RENDERED look is a render, not a viewport capture: seconds to minutes each, bounded by its own time
# budget (view_render). It is delivered once, never repeated after an edit and not one of the retained views.
STALE_LOOK = ' · before a later edit of this plan'
# What one edit may spend capturing kept views again, in seconds on Blender's main thread. The most
# recently asked view is always captured; further ones only while their measured cost fits. Measured
# (performance.rst, "Rendered looks"): a view takes 0.01-0.1 s on a light scene, so all twelve fit, and
# 2.4-3.8 s on the heaviest stage, where twelve took 30-45 s per edit.
REFRESH_SECONDS = 5.0
REFRESH_BUDGET, CAPTURE_FAILED = 'refresh_budget', 'capture_failed'
STALE_NOTE = ('stale_views: kept views not captured again after this edit (the host spends at most %d s per edit on '
              'that, most recently asked first; or the capture failed, see error). None is attached; an earlier image '
              'of one shows the scene before the edit. Ask for one again with the same arguments to see it now; it is '
              'not a new view. Do not repeat edits because a view is stale.' % REFRESH_SECONDS)


def one_shot(operation):
    return operation['tool'] == 'angelo_view' and operation['arguments'].get('shading') == 'RENDERED'


class ClaudeVisualEvidence:
    """Bound visual cost without silently dropping a requested camera or crop.

    Requests are split by angle so overlapping one-/three-view calls deduplicate.
    Fixed-camera UUIDs are compared by their actual framing when still admitted.
    Review requests keep their baseline and metrics targets, not just their PNG.
    Six current sculpt images plus six baselines fit the 12-camera native store. Touch
    retained fixed cameras before captures so ordinary superseded images, rather
    than still-needed baselines, are evicted by that store's LRU.
    """

    def __init__(self, references):
        self.references = references
        self.requests = {}
        self.dirty = set()
        self.feedback = {}
        self.looks = set()  # Feedback keys of RENDERED looks: delivered once, never refreshed.
        self.asked = {}     # Request key -> when the model last asked for it (a counter): the refresh order.
        self.cost = {}      # Request key -> seconds its last capture took, per view, when the result said.
        self.edited = False  # An edit since the last refresh: kept views are captured again only after one.

    def _frame(self, identity, *, comparison=False):
        if not identity:
            return None
        try:
            camera = self.references.observation_camera(identity)
        except ValueError:
            # A missing authority will fail through the normal native tool; do
            # not invent a camera from a result or from model-supplied metadata.
            return {'reference': identity}
        keys = ('object_uid', 'label', 'center', 'radius', 'direction', 'clip_depth', 'camera_distance')
        frame = {key: camera[key] for key in keys if key in camera}
        if comparison:
            # Equal framing alone does not make different before-states equal.
            frame['baseline_revision'] = camera.get('mesh_revision', identity)
        return frame

    def _parts(self, operation):
        name, arguments = operation['tool'], operation['arguments']
        if name not in VISUAL_IMAGE_TOOLS:
            return []
        arguments = copy.deepcopy(arguments)
        if name == 'angelo_sculpt_review':
            signature = dict(arguments, baseline=self._frame(arguments['baseline'], comparison=True))
            return [(json.dumps([name, signature], sort_keys=True, separators=(',', ':')), (name, arguments))]
        parts = []
        for angle in arguments['views']:
            request = dict(arguments, views=[angle])
            signature = dict(request, region=arguments.get('region', ''),
                             framing_reference=self._frame(arguments.get('framing_reference')))
            parts.append((json.dumps([name, signature], sort_keys=True, separators=(',', ':')), (name, request)))
        return parts

    def problem(self, operations):
        """Why this plan's views exceed the request's budget, with what is left; None when they fit."""
        wanted = dict(self.requests)
        for operation in operations:
            if not one_shot(operation):
                wanted.update(self._parts(operation))

        def sculpt(requests):
            return sum(1 for name, _arguments in requests.values() if name in SCULPT_IMAGE_TOOLS)
        if len(wanted) <= VIEW_IMAGE_LIMIT and sculpt(wanted) <= CLAUDE_SCULPT_IMAGE_LIMIT:
            return None
        left = VIEW_IMAGE_LIMIT - len(self.requests)
        sculpt_left = max(0, min(left, CLAUDE_SCULPT_IMAGE_LIMIT - sculpt(self.requests)))
        return (
            f"View budget: this request keeps {len(self.requests)} distinct view images, each captured again and "
            f"attached after every edit, and has {left} of {VIEW_IMAGE_LIMIT} left ({sculpt_left} of them for sculpt "
            f"views or reviews, which are limited to {CLAUDE_SCULPT_IMAGE_LIMIT}); this plan asks for "
            f"{len(wanted) - len(self.requests)} new ones (another angle, target, shading, resolution, framing, region "
            "or comparison is a new one; a view asked for before is not). None of this plan's operations were run "
            f"and earlier views are kept. Ask for at most {left} new views. RENDERED looks and finished pictures "
            "are not counted.")

    def check_plan(self, operations):
        problem = self.problem(operations)
        if problem:
            raise RpcError(problem)

    def pin_cameras(self, operations=()):
        # A newer UUID for equivalent framing replaces its old authority in the
        # retained slot. Pinning both would double the baseline working set and
        # could evict another still-needed camera during a six-view refresh.
        requests = dict(self.requests)
        for operation in operations:
            requests.update(self._parts(operation))
        for name, args in requests.values():
            identity = args.get('baseline' if name == 'angelo_sculpt_review' else 'framing_reference')
            if identity:
                self._frame(identity)

    def admit(self, operation, result, *, refreshed=False):
        parts = self._parts(operation)
        records = self.references.tool_images(result)
        if one_shot(operation):
            # A look that ended without an image (its budget, a cancel) says so in its result.
            for record in records:
                self.feedback['look/' + record['id']] = record
                self.looks.add('look/' + record['id'])
            return
        if len(records) != len(parts):
            raise RpcError("Native visual observation did not return every requested image. "
                           "Further operations were stopped; inspect before continuing.")
        stamp = max(self.asked.values(), default=0) + 1
        timing = result.get('timing') if isinstance(result.get('timing'), dict) else {}
        seconds = timing.get('capture_wall_ms')
        for (key, request), record in zip(parts, records):
            self.requests[key] = request
            self.feedback[key] = record
            self.dirty.discard(key)
            if not refreshed:  # The model asked: these views are now the most recent ones.
                self.asked[key] = stamp
            if type(seconds) in (int, float) and seconds >= 0:
                self.cost[key] = seconds / 1000.0 / len(parts)

    def invalidate(self):
        # A look taken before this edit is kept, since nothing renders it again, and labelled as history.
        looks = {key: record if record.get('name', '').endswith(STALE_LOOK)
                 else dict(record, name=record.get('name', '') + STALE_LOOK)
                 for key, record in self.feedback.items() if key in self.looks}
        self.feedback.clear()
        self.feedback.update(looks)
        self.dirty.update(self.requests)
        self.edited = True

    def take_images(self):
        records = list(self.feedback.values())
        self.feedback.clear()
        self.looks.clear()
        return records

    def refresh_operations(self):
        # Keep the native tool's maximum of three images per capture, while
        # avoiding three round trips for matching front/right/three-quarter views.
        operations, groups = [], {}
        recent_first = sorted(self.requests, key=lambda key: -self.asked.get(key, 0))
        for key in recent_first:
            name, args = self.requests[key]
            if key not in self.dirty:
                continue
            if name == 'angelo_sculpt_review':
                operations.append({'tool': name, 'arguments': copy.deepcopy(args)})
                continue
            common = {field: value for field, value in args.items() if field != 'views'}
            group = json.dumps([name, common], sort_keys=True, separators=(',', ':'))
            operation = groups.get(group)
            if operation is None or len(operation['arguments']['views']) == 3:
                operation = {'tool': name, 'arguments': dict(common, views=[])}
                operations.append(operation)
                groups[group] = operation
            operation['arguments']['views'].extend(args['views'])
        return operations

    clock = staticmethod(time.monotonic)

    def refresh(self, call, current):
        """Capture kept views again after an edit, most recently asked first, within ``REFRESH_SECONDS``.

        ``call(operation)`` dispatches one capture and ``current()`` says whether
        the request still owns the turn. Returns what the next round reads about
        it (``refreshed_views``, and ``stale_views`` with ``stale_views_note``
        when some view was not captured), None when the request stopped owning
        the turn, or the error of a capture that the person or the host decided
        (a stop, a document change), which ends the turn as before.

        The view asked for most recently is always captured. Each further one
        is captured only if the time left covers what a view costs: the most
        of what this refresh has measured and what the view's own last capture
        reported. Views whose cost is unknown and unmeasured are captured in
        the grouped calls of ``refresh_operations``.
        """
        from .claude_outcomes import native_result_problem
        from .plan_recovery import decided
        self.edited = False
        report = {'stale_visual_evidence_discarded': True, 'refreshed_views': []}
        stale, started, measured, attempted = [], self.clock(), 0.0, False
        for operation in self.refresh_operations():
            costs = [self.cost.get(key, 0.0) for key, _request in self._parts(operation)]
            pending = list(zip(operation['arguments'].get('views') or [None], costs))
            while pending:
                estimate = max(measured, max(cost for _angle, cost in pending))
                left = REFRESH_SECONDS - (self.clock() - started)
                count = len(pending) if estimate <= 0 else min(len(pending), int(left / estimate))
                if not attempted:
                    count = max(1, count)
                elif left <= 0:
                    count = 0
                if count < 1:
                    stale.extend(self._stale(operation, angle, REFRESH_BUDGET) for angle, _cost in pending)
                    break
                batch, pending = [angle for angle, _cost in pending[:count]], pending[count:]
                part = operation if batch == [None] else {
                    'tool': operation['tool'], 'arguments': dict(operation['arguments'], views=batch)}
                if not current():
                    return None
                self.pin_cameras()
                before, attempted = self.clock(), True
                result = call(part)
                if not current():
                    return None
                measured = max(measured, (self.clock() - before) / len(batch))
                problem = native_result_problem(result)
                if problem is None:
                    try:
                        self.admit(part, result, refreshed=True)
                    except (RpcError, ValueError) as error:
                        problem = str(error)
                if problem is None:
                    report['refreshed_views'].append(result)
                elif decided(result):
                    return problem
                else:
                    stale.extend(self._stale(part, angle, CAPTURE_FAILED, problem) for angle in batch)
        # Captured most recent first, attached in the order the views were first asked for, as before.
        order = {key: index for index, key in enumerate(self.requests)}
        kept = iter(sorted((key for key in self.feedback if key in order), key=order.get))
        self.feedback = {key: self.feedback[key] for key in [next(kept) if key in order else key for key in self.feedback]}
        if stale:
            report.update(stale_views=stale, stale_views_note=STALE_NOTE)
        return report

    @staticmethod
    def _stale(operation, angle, reason, error=None):
        """One kept view that was not captured again, as the model can ask for it."""
        arguments = dict(operation['arguments'])
        if angle is not None:
            arguments['views'] = [angle]
        entry = {'tool': operation['tool'], 'arguments': arguments, 'reason': reason}
        if error:
            entry['error'] = ' '.join(str(error).split())[:300]
        return entry
