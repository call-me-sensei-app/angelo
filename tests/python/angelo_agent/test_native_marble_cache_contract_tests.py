# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mutation checks for the additive cache oracle; real cache logic, fake GPU, no native launch."""

import ast
import contextlib
import copy
from pathlib import Path
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path[:0] = [str(HERE), str(ADDON.parent)]
import angelo_agent
import test_native_marble_cache_contract as contract
import test_native_marble_cache_probe as probe


class CacheMutation(ast.NodeTransformer):
    def __init__(self, mutation):
        self.mutation = mutation

    def visit_Compare(self, node):
        text = ast.unparse(node)
        if self.mutation == "ignore_size" and text.startswith("entry['size'] !="):
            return ast.copy_location(ast.Constant(False), node)
        if self.mutation == "ignore_expiry" and text == "now - entry['at'] > REFRESH":
            return ast.copy_location(ast.Constant(False), node)
        return self.generic_visit(node)

    def visit_IfExp(self, node):
        if self.mutation == "ignore_draft" and ast.unparse(node) == "history[-1].body if history else ''":
            return ast.copy_location(ast.Constant("original"), node)
        return self.generic_visit(node)


class HostCache:
    """Execute actual key/reuse/keep from the source with only native/GPU interfaces replaced."""
    def __init__(self, mutation=None):
        self.patches = contextlib.ExitStack()
        self.now, self.cpu = 10.1, 0.0
        self.line = NS(body="original", current_character=8)
        self.area = NS(as_pointer=lambda: 7, spaces=NS(active=NS(history=[self.line], font_size=14)))
        self.region = NS(width=16, height=16, as_pointer=lambda: 8)
        self.controller = NS(state={"id": "fixture"}, version=1, status="Thinking", attachments=[], pending={})
        self.wait = NS(hero_size=lambda now: 112)
        self.host = NS(enabled=True)
        self.host.current = lambda controller: self.wait if self.host.enabled else None
        self.chat = NS(_views={7: {"input_serial": 0}}, _scroll={}, _font=0,
                       ui_scale=lambda: 1, last_error=None, draw_count=0)
        self.frames = types.ModuleType("angelo_agent._cache_contract_host")
        self.frames.__package__ = "angelo_agent"
        self.frames.time = NS(monotonic=lambda: self.now, perf_counter=self.perf_counter)
        self.frames.stats = {key: 0 for key in contract.FIELDS}
        self.frames._kept, self.frames._requested = {}, set()
        self.frames.request = lambda area: self.frames._requested.add(area.as_pointer())
        self.frames.REFRESH, self.frames.frame_cost = 2.0, 0.0
        self.frames.bypass, self.frames.trace = False, None
        self.frames.marble_draw = NS(placing=[], replay=lambda *args: None)
        self.frames._blit = lambda *args: None
        self.frames._created_in_region = lambda gpu, size: NS(bind=contextlib.nullcontext)
        modules = {
            "ui": NS(_controller=self.controller), "chat_view": self.chat, "marble_host": self.host,
            "chat_scrollbar": NS(LINGER=.4), "job_ui": NS(_shown={}),
            "live_progress": NS(lead=lambda controller, now: int(now)), "ui_text": NS(is_rtl=lambda: False),
        }
        self.patches.enter_context(mock.patch.dict(sys.modules, {
            **{"angelo_agent." + name: module for name, module in modules.items()}, "gpu": NS()}))
        for name, module in modules.items():
            self.patches.enter_context(mock.patch.object(angelo_agent, name, module, create=True))
        nodes = [node for node in ast.parse((ADDON / "marble_frames.py").read_text()).body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in {"key", "_wait", "reuse", "keep"}]
        for index, node in enumerate(nodes):
            if node.name == "reuse" and mutation in {"ignore_size", "ignore_expiry"}:
                nodes[index] = CacheMutation(mutation).visit(node)
            if node.name == "key" and mutation == "ignore_draft":
                nodes[index] = CacheMutation(mutation).visit(node)
        tree = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(tree, str(ADDON / "marble_frames.py"), "exec"), self.frames.__dict__)
        self.product_reuse = self.frames.reuse
        if mutation == "always_miss":
            self.frames.reuse = lambda area, region: False
        elif mutation == "always_reuse":
            self.frames.reuse = lambda area, region: True

    def perf_counter(self):
        self.cpu += .000001
        return self.cpu

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.patches.close()

    def observer(self):
        return probe.Observer(self.frames, self.host, self.chat, self.area, self.region)

    def draw(self, observer, requested=True, intent="fixture", posts=1, extra_draw=False):
        observer.begin(intent, requested)
        self.frames._requested.discard(7)
        if requested:
            self.frames._requested.add(7)
        if not self.frames.reuse(self.area, self.region):
            with self.frames.keep(self.area, self.region):
                self.chat.draw_count += 1
        if extra_draw:
            self.chat.draw_count += 1
        for _ in range(posts):
            observer.post()
        return observer.end()


class RealCacheMutations(unittest.TestCase):
    def test_actual_cold_and_stable_cache_paths_are_observed_and_restored(self):
        with HostCache() as cache:
            before = {name: getattr(cache.frames, name) for name in ("reuse", "key", "_wait", "time")}
            with cache.observer() as observer:
                first = cache.draw(observer)
                later = [cache.draw(observer) for _ in range(2)]
                contract.refreshed(first, later, "cold")
                self.assertEqual([item["reason"] for item in later], ["stable", "stable"])
                with self.assertRaises(AssertionError):
                    probe.assert_inactive()
            probe.assert_inactive()
            for name, value in before.items():
                self.assertIs(getattr(cache.frames, name), value)

    def test_disabling_requires_a_draw_before_the_retained_picture_is_cleared(self):
        with HostCache() as cache, cache.observer() as observer:
            cache.draw(observer)
            cache.host.enabled = False
            self.assertIn(7, cache.frames._kept, "A flag change alone does not clear the GPU cache")
            disabled = cache.draw(observer, requested=False)
            self.assertTrue(disabled["cache_empty"])
            self.assertEqual(disabled["delta"]["full"], 0, "Disabled drawing is not a kept full draw")
            cache.host.enabled = True
            first = cache.draw(observer)
            contract.refreshed(first, [cache.draw(observer) for _ in range(2)], "cold")

    def test_always_miss_cannot_pass_per_case_nonvacuity(self):
        with HostCache("always_miss") as cache, cache.observer() as observer:
            with self.assertRaises(AssertionError):
                first = cache.draw(observer)
                later = [cache.draw(observer) for _ in range(2)]
                contract.refreshed(first, later, "cold")

    def test_always_reuse_is_rejected_on_a_cold_cache(self):
        with HostCache("always_reuse") as cache, cache.observer() as observer:
            with self.assertRaises(AssertionError):
                cache.draw(observer)

    def test_a_single_early_miss_among_successes_is_not_misclassified_as_no_wait(self):
        with HostCache() as cache:
            original = cache.frames.reuse
            calls = [0]

            def one_miss(area, region):
                calls[0] += 1
                return False if calls[0] == 2 else original(area, region)
            cache.frames.reuse = one_miss
            with cache.observer() as observer:
                cache.draw(observer)
                with self.assertRaises(AssertionError):
                    cache.draw(observer)

    def test_draft_invalidation_is_real_and_omitting_draft_from_key_is_rejected(self):
        for mutation in (None, "ignore_draft"):
            with self.subTest(mutation=mutation), HostCache(mutation) as cache, cache.observer() as observer:
                cache.draw(observer)
                cache.line.body = "A different draft"
                result = cache.draw(observer)
                later = [cache.draw(observer) for _ in range(2)]
                if mutation:
                    with self.assertRaises(AssertionError):
                        contract.refreshed(result, later, "key_changed", changed="draft")
                else:
                    contract.refreshed(result, later, "key_changed", changed="draft")

    def test_stale_size_and_age_refresh_and_ignored_predicates_fail(self):
        for reason, mutation in (("size", "ignore_size"), ("expired", "ignore_expiry")):
            for broken in (False, True):
                with self.subTest(reason=reason, broken=broken), HostCache(mutation if broken else None) as cache, cache.observer() as observer:
                    cache.draw(observer)
                    if reason == "size":
                        cache.frames._kept[7]["size"] = (17, 16)
                    else:
                        cache.frames._kept[7]["at"] = cache.now - 3
                    if broken:
                        with self.assertRaises(AssertionError):
                            cache.draw(observer)
                    else:
                        refreshed = cache.draw(observer)
                        contract.refreshed(refreshed, [cache.draw(observer) for _ in range(2)], reason)

    def test_expiry_precedes_size_and_clock_refresh_is_counted(self):
        with HostCache() as cache, cache.observer() as observer:
            cache.draw(observer)
            cache.frames._kept[7].update(at=cache.now - 3, size=(17, 16))
            self.assertEqual(cache.draw(observer)["reason"], "expired")
            cache.now += 1
            clock = cache.draw(observer)
            self.assertEqual(clock["reason"], "clock")
            self.assertEqual(clock["delta"]["clock"], 1)

    def test_unrequested_redraw_is_full_and_missing_post_or_extra_draw_fails(self):
        with HostCache() as cache, cache.observer() as observer:
            cache.draw(observer)
            normal = cache.draw(observer, requested=False)
            self.assertEqual((normal["reason"], normal["delta"]["full"]), ("unrequested", 1))
            for options in ({"posts": 0}, {"posts": 2}, {"extra_draw": True}):
                with self.subTest(options=options), self.assertRaises(AssertionError):
                    cache.draw(observer, **options)

    def test_counter_drift_and_nested_explicit_draw_are_rejected(self):
        with HostCache() as cache, cache.observer() as observer:
            cache.draw(observer)
            observer.begin("first", True)
            with self.assertRaises(AssertionError):
                observer.begin("nested", True)
            cache.frames._requested.add(7)
            cache.frames.reuse(cache.area, cache.region)
            cache.frames.stats["reused"] += 1
            observer.post()
            with self.assertRaises(AssertionError):
                observer.end()

    def test_a_nested_actual_reuse_callback_is_rejected(self):
        with HostCache() as cache, cache.observer() as observer:
            cache.draw(observer)
            cache.frames._blit = lambda *args: cache.frames.reuse(cache.area, cache.region)
            with self.assertRaises(AssertionError):
                cache.draw(observer)

    def test_followup_opportunities_are_required_for_each_case(self):
        with HostCache() as cache, cache.observer() as observer:
            first = cache.draw(observer)
            contract.refreshed(first, [cache.draw(observer) for _ in range(2)], "cold")
            cache.line.body = "another draft"
            changed = cache.draw(observer)
            follows = []
            for _ in range(2):
                cache.now += 1
                follows.append(cache.draw(observer))
            self.assertTrue(all(item["reason"] == "clock" for item in follows))
            with self.assertRaisesRegex(AssertionError, "No stable reuse"):
                contract.refreshed(changed, follows, "key_changed", changed="draft")
            with self.assertRaisesRegex(AssertionError, "fixed"):
                contract.refreshed(changed, follows[:1], "key_changed", changed="draft")

    def test_exception_restores_all_patched_callbacks_and_active_marker(self):
        with HostCache() as cache:
            before = {name: getattr(cache.frames, name) for name in ("reuse", "key", "_wait", "time")}
            with self.assertRaisesRegex(RuntimeError, "injected draw failure"):
                with cache.observer() as observer:
                    cache.draw(observer)
                    cache.frames._blit = mock.Mock(side_effect=RuntimeError("injected draw failure"))
                    cache.draw(observer)
            probe.assert_inactive()
            for name, value in before.items():
                self.assertIs(getattr(cache.frames, name), value)

    def test_install_and_restoration_failure_preserve_primary_and_cleanup_chain(self):
        with HostCache() as cache:
            observer = cache.observer()
            before = {name: getattr(cache.frames, name) for name in ('reuse', 'key', '_wait', 'time')}
            primary = RuntimeError('injected installation failure')

            def failing_setattr(target, name, value):
                if target is cache.frames and name == 'time' and value is observer:
                    raise primary
                if target is cache.frames and name == '_wait' and value is before['_wait']:
                    raise RuntimeError('injected restoration failure')
                setattr(target, name, value)

            with mock.patch.object(probe, 'setattr', failing_setattr, create=True):
                with self.assertRaises(RuntimeError) as caught:
                    observer.__enter__()
            self.assertIs(caught.exception, primary)
            self.assertIsInstance(caught.exception.__cause__, AssertionError)
            self.assertIn('injected restoration failure', str(caught.exception.__cause__))
            probe.assert_inactive()
            for name in ('reuse', 'key', 'time'):
                self.assertIs(getattr(cache.frames, name), before[name])
            self.assertIsNot(cache.frames._wait, before['_wait'])
            cache.frames._wait = before['_wait']  # Fault removed; leave this disposable harness restored.


class AccountingAndPixels(unittest.TestCase):
    def test_accounting_removes_only_helper_work_without_changing_counters(self):
        before = dict(zip(contract.FIELDS, (10, 20, 3, .4, .1)))
        after = dict(zip(contract.FIELDS, (16, 30, 4, .6, .2)))
        saved = copy.deepcopy((before, after))
        helper = contract.delta(before, after)
        raw = dict(zip(contract.FIELDS, (48, 160, 14, 1.2, .8)))
        net = contract.adjusted(raw, helper)
        for key in contract.FIELDS:
            self.assertAlmostEqual(net[key] + helper[key], raw[key])
        self.assertEqual((before, after), saved)
        for key in contract.FIELDS:
            bad = dict(after, **{key: before[key] - 1})
            with self.subTest(key=key), self.assertRaises(AssertionError):
                contract.delta(before, bad)
        with self.assertRaises(AssertionError):
            contract.adjusted(helper, raw)

    def pixels(self):
        import numpy as np
        values = np.arange(16 * 16 * 3, dtype=np.uint16).reshape(16, 16, 3) % 128
        first = dict(reused=True, delta={"reused": 1, "full": 0}, capture_sequence=1, sequence=1,
                     content_key=("draft", 10), size=(16, 16), pixels=values.astype(np.uint8), holes=[])
        direct = dict(first, reused=False, bypass=True, chat_draws=1,
                      capture_sequence=2, sequence=2, pixels=first["pixels"].copy())
        return first, direct

    def test_pixel_threshold_and_corruption_detection_are_unchanged(self):
        first, direct = self.pixels()
        self.assertEqual(contract.pixel_difference(first, direct)["changed_share"], 0)
        direct["pixels"][0, 0, 0] += 2
        contract.pixel_difference(first, direct)
        direct["pixels"][0, 0, 0] += 1
        with self.assertRaises(AssertionError):
            contract.pixel_difference(first, direct)

    def test_bad_capture_pair_static_key_or_dimensions_cannot_pass(self):
        for field, value in (("capture_sequence", 999), ("content_key", ("changed", 10)),
                             ("size", (15, 16)), ("bypass", False)):
            first, direct = self.pixels()
            direct[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                contract.pixel_difference(first, direct)

    def test_missing_static_key_or_sequence_is_not_vacuous_pixel_parity(self):
        for field in ("content_key", "sequence"):
            first, direct = self.pixels()
            first[field] = direct[field] = None
            if field == "sequence":
                first["capture_sequence"] = direct["capture_sequence"] = None
            with self.subTest(field=field), self.assertRaises(AssertionError):
                contract.pixel_difference(first, direct)

    def test_blank_or_fully_masked_buffers_cannot_pass(self):
        for color in ((0, 0, 0), (255, 255, 255), (255, 0, 0)):
            first, direct = self.pixels()
            first["pixels"][:] = direct["pixels"][:] = color
            with self.subTest(color=color), self.assertRaises(AssertionError):
                contract.pixel_difference(first, direct)
        first, direct = self.pixels()
        first["holes"] = [(0, 0, 16, 16)]
        with self.assertRaises(AssertionError):
            contract.pixel_difference(first, direct)

    def test_only_the_existing_four_pixel_mark_padding_is_masked(self):
        first, direct = self.pixels()
        first["holes"] = [(5, 5, 1, 1)]
        direct["pixels"][1:10, 1:10] = 255
        self.assertEqual(contract.pixel_difference(first, direct)["unmasked_pixels"], 175)
        direct["pixels"][0, 0] = 255
        with self.assertRaises(AssertionError):
            contract.pixel_difference(first, direct)


class ProtocolHarness:
    """Fixed protocol with actual product reuse/keep and a fake POST_PIXEL readback."""
    def __init__(self, cache):
        self.cache = cache
        self.observer = cache.observer()
        self.selection = (3, 7)
        self.paint_count = 0
        self.hooks = {}
        self.installed, self.removed = [], []
        self.quiet = True
        self.driver = probe.Driver(self.observer, self.paint, lambda: self.quiet,
            self.read_draft, self.write_draft, cache.now + 2,
            clock=lambda: cache.now)

    def read_draft(self):
        return self.cache.line.body, self.cache.line.current_character

    def write_draft(self, value):
        self.cache.line.body, self.cache.line.current_character = value

    def paint(self):
        cache, obs = self.cache, self.observer
        self.paint_count += 1
        hook = self.hooks.get(obs.record['intent'])
        if hook:
            hook()
        if not cache.frames.reuse(cache.area, cache.region):
            with cache.frames.keep(cache.area, cache.region):
                cache.chat.draw_count += 1
        obs.post()
        if obs.record['capture']:
            import numpy as np
            obs.record.update(capture_sequence=obs.record['sequence'],
                content_key=obs.original_key(cache.area, cache.region, cache.controller,
                    cache.chat._views[7], cache.wait, cache.now),
                size=(16, 16), holes=[], pixels=(np.arange(16 * 16 * 3).reshape(16, 16, 3) % 128).astype(np.uint8))
        cache.now += .01
        return {'FINISHED'}

    def install(self):
        handle = object()
        self.installed.append(handle)
        return handle

    def remove(self, handle):
        self.removed.append(handle)

    def run(self, **kwargs):
        args = dict(install_capture=self.install, remove_capture=self.remove,
            read_selection=lambda: self.selection,
            restore_selection=lambda value: setattr(self, 'selection', value),
            save_pixels=lambda first, direct: ('fake-reused-buffer', 'fake-direct-buffer'),
            hard_deadline=self.driver.deadline + 1)
        args.update(kwargs)
        return probe.execute(self.driver, **args)


class FixedProtocolMutations(unittest.TestCase):
    def test_eighteen_draw_protocol_is_complete_and_accounts_cleanup(self):
        with HostCache() as cache:
            h = ProtocolHarness(cache)
            before = h.read_draft(), h.selection
            cache.frames.trace = sentinel = object()
            cache.frames.frame_cost = .123
            cache.frames._requested.add(7)
            result = h.run()
            self.assertTrue(result['complete'], {key: value for key, value in result.items() if key != 'frames'})
            self.assertEqual(result['draws'], 18)
            self.assertEqual(result['frames'][-1]['intent'], 'cleanup')
            self.assertEqual((h.read_draft(), h.selection), before)
            self.assertIs(cache.frames.trace, sentinel)
            self.assertEqual(cache.frames.frame_cost, .123)
            self.assertIn(7, cache.frames._requested)
            self.assertEqual(h.installed, h.removed)
            for field in contract.FIELDS:
                self.assertAlmostEqual(result['stats_delta'][field], sum(frame['delta'][field] for frame in result['frames']))
            self.assertAlmostEqual(result['elapsed'], .18)
            probe.assert_inactive()

    def test_actual_cache_mutants_cannot_complete_the_protocol(self):
        for mutation in ('always_miss', 'always_reuse', 'ignore_draft', 'ignore_size', 'ignore_expiry'):
            with self.subTest(mutation=mutation), HostCache(mutation) as cache:
                h = ProtocolHarness(cache)
                result = h.run()
                self.assertFalse(result['complete'])
                self.assertIn('error', result)
                self.assertLessEqual(result['draws'], 18)
                probe.assert_inactive()

    def test_expired_deadline_never_starts_or_extends_work(self):
        with HostCache() as cache:
            h = ProtocolHarness(cache)
            h.driver.deadline = cache.now
            result = h.run()
            self.assertFalse(result['complete'])
            self.assertEqual(result['draws'], 0)
            self.assertEqual(h.installed, [])
            self.assertIn('deadline', result['error'])
            self.assertEqual(h.driver.deadline, cache.now)
            probe.assert_inactive()

    def test_mid_protocol_deadline_or_turn_change_stops_with_only_cleanup(self):
        for mode in ('deadline', 'turn'):
            with self.subTest(mode=mode), HostCache() as cache:
                h = ProtocolHarness(cache)
                def stop():
                    if mode == 'deadline':
                        cache.now = h.driver.deadline
                    else:
                        h.quiet = False
                h.hooks['key'] = stop
                result = h.run()
                self.assertFalse(result['complete'])
                self.assertEqual(result['draws'], 6)
                self.assertEqual(result['frames'][-1]['intent'], 'cleanup')
                self.assertEqual(h.installed, h.removed)
                self.assertEqual(h.read_draft(), ('original', 8))
                probe.assert_inactive()

    def test_primary_and_cleanup_failures_are_both_preserved(self):
        with HostCache() as cache:
            h = ProtocolHarness(cache)
            def primary():
                raise RuntimeError('primary draw failure')
            def cleanup():
                raise RuntimeError('cleanup draw failure')
            h.hooks.update(key=primary, cleanup=cleanup)
            result = h.run()
            self.assertFalse(result['complete'])
            self.assertIn('primary draw failure', result['error'])
            self.assertTrue(any('cleanup draw failure' in error for error in result['cleanup_errors']))
            self.assertEqual(h.installed, h.removed)
            self.assertEqual(h.read_draft(), ('original', 8))
            probe.assert_inactive()

    def test_handler_removal_or_selection_restoration_failure_fails_closed(self):
        for stage in ('handler', 'selection'):
            with self.subTest(stage=stage), HostCache() as cache:
                h = ProtocolHarness(cache)
                def broken(*args):
                    raise RuntimeError('injected cleanup failure')
                overrides = {'remove_capture': broken} if stage == 'handler' else {'restore_selection': broken}
                result = h.run(**overrides)
                self.assertFalse(result['complete'])
                self.assertFalse(result['checks']['cleanup'])
                self.assertTrue(any('injected cleanup failure' in error for error in result['cleanup_errors']))
                probe.assert_inactive()

    def test_cleanup_restores_metadata_only_on_the_same_live_entry(self):
        with HostCache() as cache:
            h = ProtocolHarness(cache)
            old = dict(size=(16, 16), at=1)
            cache.frames._kept[7] = old
            h.driver.seed('at', 99)
            h.driver.restore_seeds()
            self.assertEqual(old['at'], 1)
            h.driver.seed('at', 99)
            replacement = dict(size=(16, 16), at=2)
            cache.frames._kept[7] = replacement
            h.driver.restore_seeds()
            self.assertIs(cache.frames._kept[7], replacement)
            self.assertEqual(replacement['at'], 2)
            del cache.frames._kept[7]
            h.driver.restore_seeds()
            self.assertNotIn(7, cache.frames._kept)

    def test_unpaired_work_and_counter_drift_cannot_pass_accounting(self):
        for mode in ('outside', 'counter'):
            with self.subTest(mode=mode), HostCache() as cache:
                h = ProtocolHarness(cache)
                def save(first, direct):
                    if mode == 'outside':
                        h.observer.post()
                    else:
                        cache.frames.stats['full'] += 1
                    return ('fake-reused', 'fake-direct')
                result = h.run(save_pixels=save)
                self.assertFalse(result['complete'])
                self.assertFalse(result['checks']['accounting'])
                probe.assert_inactive()

    def test_later_case_cannot_borrow_cold_cases_stable_opportunities(self):
        with HostCache() as cache:
            h = ProtocolHarness(cache)
            h.driver.deadline = cache.now + 10
            def cross_clock():
                cache.now += 1
            h.hooks.update(key_post_1=cross_clock, key_post_2=cross_clock)
            result = h.run()
            self.assertFalse(result['complete'])
            self.assertTrue(result['checks']['cold'])
            self.assertFalse(result['checks']['key'])
            self.assertIn('No stable reuse', result['error'])
            self.assertEqual(result['draws'], 8)
            self.assertEqual(result['frames'][-1]['intent'], 'cleanup')
            probe.assert_inactive()

    def test_pixel_pair_corruption_or_clock_change_fails_without_retry(self):
        for mode in ('pixels', 'sequence', 'key'):
            with self.subTest(mode=mode), HostCache() as cache:
                h = ProtocolHarness(cache)
                original = h.paint
                def paint():
                    result = original()
                    record = h.observer.record
                    if record['intent'] == 'direct_pixels':
                        if mode == 'pixels':
                            record['pixels'][0, 0] = 255
                        elif mode == 'sequence':
                            record['capture_sequence'] += 1
                        else:
                            record['content_key'] = record['content_key'][:-1] + ('changed-clock',)
                    return result
                h.driver.paint = paint
                result = h.run()
                self.assertFalse(result['complete'])
                self.assertFalse(result['checks']['pixels'])
                self.assertEqual(result['draws'], 18)
                self.assertEqual(sum(frame['intent'] == 'direct_pixels' for frame in result['frames']), 1)
                self.assertEqual(h.installed, h.removed)
                probe.assert_inactive()

    def test_draft_restoration_is_checked_and_cleanup_is_inside_hard_deadline(self):
        for mode in ('draft', 'hard_deadline'):
            with self.subTest(mode=mode), HostCache() as cache:
                h = ProtocolHarness(cache)
                if mode == 'draft':
                    write = h.write_draft
                    h.driver.write_draft = lambda value: write(value) if value[0] != 'original' else None
                else:
                    h.hooks['cleanup'] = lambda: setattr(cache, 'now', h.driver.deadline + 1)
                result = h.run()
                self.assertFalse(result['complete'])
                if mode == 'draft':
                    self.assertTrue(any('Draft/caret restoration failed' in item for item in result['cleanup_errors']))
                else:
                    self.assertFalse(result['checks']['deadline'])
                    self.assertGreaterEqual(result['elapsed'], 3)
                self.assertEqual(h.installed, h.removed)
                probe.assert_inactive()

    def test_probe_cannot_begin_inside_an_existing_observer(self):
        with HostCache() as cache:
            h = ProtocolHarness(cache)
            with cache.observer() as outer:
                result = h.run()
                self.assertFalse(result['complete'])
                self.assertEqual(result['draws'], 0)
                self.assertEqual(h.installed, [])
                self.assertIs(probe.ACTIVE, outer)
            probe.assert_inactive()


class NativeAdapterCallbacks(unittest.TestCase):
    """Compile only the nested callbacks; never import bpy/gpu or execute the native run function."""
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((HERE / 'test_native_marble_cache_native.py').read_text())
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        cls.callbacks = {node.name: node for node in run.body if isinstance(node, ast.FunctionDef)}

    def callback(self, name, namespace):
        node = copy.deepcopy(self.callbacks[name])
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<isolated native adapter callback>', 'exec'), namespace)
        return namespace[name]

    def context(self):
        import hashlib
        import numpy as np
        class Readback(bytearray):
            pass
        buffer = Readback(range(16))
        framebuffer = NS(viewport_get=lambda: (0, 0, 2, 2), read_color=mock.Mock(return_value=buffer))
        record = dict(reuse_calls=1, capture=True, post_calls=0, draw_key=('static', 1), bypass=False,
                      sequence=4, errors=[])
        obs = NS(record=record, original_key=mock.Mock(return_value=('static', 1)))
        obs.post = lambda: record.update(post_calls=record['post_calls'] + 1)
        handler = object()
        namespace = dict(closed=[False], target=lambda: True, observer=obs,
            chat_view=NS(_handler=handler), original_handler=handler, gpu=NS(state=NS(active_framebuffer_get=lambda: framebuffer)),
            region=NS(width=2, height=2), np=np, copy=copy, hashlib=hashlib,
            area=object(), controller=object(), state={'marble_drawn': {'hero': {'rect': (1, 2, 3, 4)}}},
            original_wait=object(), time=NS(monotonic=lambda: 10.0))
        return namespace, framebuffer, buffer

    def test_capture_is_after_product_and_copies_exact_region_bytes(self):
        namespace, framebuffer, buffer = self.context()
        self.callback('post', namespace)()
        record = namespace['observer'].record
        self.assertFalse(record['errors'])
        self.assertEqual(record['capture_sequence'], record['sequence'])
        self.assertEqual(record['pixels'].tolist(), [[[0, 1, 2], [4, 5, 6]], [[8, 9, 10], [12, 13, 14]]])
        self.assertEqual(record['holes'], [(1, 2, 3, 4)])
        self.assertEqual(record['content_key'], ('static', 1))
        framebuffer.read_color.assert_called_once_with(0, 0, 2, 2, 4, 0, 'UBYTE')
        buffer[0] = 255
        self.assertEqual(record['pixels'][0, 0, 0], 0, 'The next framebuffer read must not mutate prior evidence')
        namespace['observer'].original_key.assert_not_called()

    def test_capture_rejects_wrong_handler_order_viewport_and_duplicate_callback(self):
        for failure in ('handler', 'order', 'viewport', 'origin', 'duplicate'):
            with self.subTest(failure=failure):
                namespace, framebuffer, buffer = self.context()
                record = namespace['observer'].record
                if failure == 'handler':
                    namespace['chat_view']._handler = object()
                elif failure == 'order':
                    record['reuse_calls'] = 0
                elif failure == 'viewport':
                    framebuffer.viewport_get = lambda: (0, 0, 3, 2)
                elif failure == 'origin':
                    framebuffer.viewport_get = lambda: (1, 0, 2, 2)
                else:
                    record['post_calls'] = 1
                self.callback('post', namespace)()
                self.assertTrue(record['errors'])
                self.assertNotIn('pixels', record)
                framebuffer.read_color.assert_not_called()

    def test_capture_is_filtered_and_closed_callback_is_inert(self):
        for state in ('closed', 'other_target', 'non_capture'):
            with self.subTest(state=state):
                namespace, framebuffer, buffer = self.context()
                if state == 'closed':
                    namespace['closed'][0] = True
                elif state == 'other_target':
                    namespace['target'] = lambda: False
                else:
                    namespace['observer'].record['capture'] = False
                self.callback('post', namespace)()
                self.assertNotIn('pixels', namespace['observer'].record)
                framebuffer.read_color.assert_not_called()
                self.assertEqual(namespace['observer'].record['post_calls'], int(state == 'non_capture'))

    def test_direct_key_is_observed_after_bypass_draw_without_extra_draw(self):
        namespace, framebuffer, buffer = self.context()
        record = namespace['observer'].record
        record.update(bypass=True, draw_key=None)
        self.callback('post', namespace)()
        self.assertFalse(record['errors'])
        self.assertEqual(record['content_key'], ('static', 1))
        namespace['observer'].original_key.assert_called_once_with(namespace['area'], namespace['region'],
            namespace['controller'], namespace['state'], namespace['original_wait'], 10.0)

    def test_failed_handler_removal_still_closes_callback_before_throwing(self):
        remove = mock.Mock(side_effect=RuntimeError('injected removal failure'))
        namespace = dict(closed=[False], bpy=NS(types=NS(SpaceConsole=NS(draw_handler_remove=remove))))
        with self.assertRaisesRegex(RuntimeError, 'injected removal failure'):
            self.callback('remove', namespace)('owned-handle')
        self.assertTrue(namespace['closed'][0])
        remove.assert_called_once_with('owned-handle', 'WINDOW')


class MeasurementIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((HERE / 'test_blender_chat_waiting.py').read_text())
        cls.functions = {node.name: node for node in cls.tree.body if isinstance(node, ast.FunctionDef)}

    def compile_function(self, name, namespace):
        node = copy.deepcopy(self.functions[name])
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(HERE / 'test_blender_chat_waiting.py'), 'exec'), namespace)
        return namespace[name]

    def checks(self):
        return {target.slice.value: node.value for node in ast.walk(self.functions['measure'])
                if isinstance(node, ast.Assign) for target in node.targets
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                and target.value.id == 'checks' and isinstance(target.slice, ast.Constant)}

    def test_original_threshold_expressions_are_preserved(self):
        expected = {
            '5_the_wait_takes_at_most_2_percent_of_the_main_thread': 'composed <= 0.02',
            '5_and_the_loop_rate_agrees_within_its_noise': 'share is not None and share <= 0.03',
            '5_a_marble_frame_draws_in_under_half_a_millisecond': '0 < own <= 0.5',
            '5_and_less_than_the_chats_full_draw': "(marks['frame']['marble']['cpu_ms'] or 9) < (marks['frame']['stock_chat']['cpu_ms'] or 0)",
            '5_the_kept_picture_is_the_chats_own': "max(marks['picture'].values()) <= 0.002",
            '5_every_kept_draw_lands_in_the_regions_own_framebuffer': "bool(traced) and all(t['before'][2:] == t['size'] and t['after'][2:] == t['size'] for t in traced)",
            '5_orbiting_pauses_the_animation': "marks['rotated'] and orbit_frames_on <= 1 and marks['paused'] > 0 and marks['pause_reason'] == 'modal'",
            '5_and_orbit_frames_stay_within_noise_of_off': "bool(orbit[True] and orbit[False]) and medians['on']['median'] <= medians['off']['median'] * 1.05 + 0.05",
        }
        found = self.checks()
        self.assertEqual(set(found), set(expected) | {'5_and_the_clock_refreshes_at_most_once_a_second'})
        for name, expression in expected.items():
            with self.subTest(name=name):
                self.assertEqual(ast.dump(found[name]), ast.dump(ast.parse(expression, mode='eval').body))

    def test_the_reuse_ratio_is_reported_not_gated(self):
        """Retired as a gate for every candidate (maintainer, 2026-10-26); the direct checks gate reuse instead."""
        self.assertNotIn('5_marble_frames_reuse_the_kept_picture', self.checks())
        source = ast.unparse(self.functions['measure'])
        self.assertIn("observed['marble_reuse_ratio'] = cache_contract.reuse_report(stats, clock)", source)
        report = contract.reuse_report(dict(zip(contract.FIELDS, (46, 160, 12, .06, .04))), 12)
        self.assertEqual(report, {'reused': 160, 'full_excluding_clock': 34, 'reused_per_full': 4.706,
                                  'meets_former_5_to_1': False, 'gate': False})

    def test_helper_counters_cannot_inflate_the_reported_ratio(self):
        original = dict(zip(contract.FIELDS, (48, 158, 13, .3, .1)))
        helper = dict(zip(contract.FIELDS, (6, 100, 0, .01, .1)))
        raw = {field: original[field] + helper[field] for field in contract.FIELDS}
        self.assertTrue(contract.reuse_report(raw, raw['clock'])['meets_former_5_to_1'])
        adjusted = contract.adjusted(raw, helper)
        self.assertFalse(contract.reuse_report(adjusted, adjusted['clock'])['meets_former_5_to_1'])
        self.assertEqual(adjusted['reused'], original['reused'])

    def test_helper_elapsed_is_excluded_from_original_clock_allowance(self):
        value = self.checks()['5_and_the_clock_refreshes_at_most_once_a_second']
        expected = "clock <= (since() - marks['stats_at'] - helper['elapsed']) + 2"
        self.assertEqual(ast.dump(value), ast.dump(ast.parse(expected, mode='eval').body))
        expression = compile(ast.Expression(value), '<actual clock allowance>', 'eval')
        namespace = dict(clock=12, since=lambda: 11, marks={'stats_at': 0}, helper={'elapsed': 2})
        self.assertFalse(eval(expression, namespace), 'Helper elapsed must not excuse extra refreshes')
        namespace['helper']['elapsed'] = 0
        self.assertTrue(eval(expression, namespace))

    def integration_environment(self, cache):
        loop = NS(running=False, _step=object())
        orbit = NS(running=False, handler=None, _move=object())
        ticks = [100.0]
        namespace = dict(cache_probe=probe, cache_contract=contract,
            cache_native=NS(run=None), marks=dict(frame={}, picture={}, rotated=True, loop=loop, orbit=orbit),
            state={'sent': 90, 'observed': {}}, time=NS(monotonic=lambda: ticks[0]),
            rounds=NS(MEASURE_THINKING=22), OUT=Path('/unused-no-io'), chat=lambda: (cache.area, cache.region),
            window=lambda: 'original-window', bpy=NS(app=NS(timers=NS(is_registered=lambda callback: False))))
        run = self.compile_function('additive_cache_checks', namespace)
        return namespace, run, ticks

    def test_outer_accounting_includes_whole_helper_return_and_cleanup(self):
        with HostCache() as cache, mock.patch.object(angelo_agent, 'marble_frames', cache.frames, create=True):
            namespace, additive, ticks = self.integration_environment(cache)
            calls = []
            helper = dict(zip(contract.FIELDS, (6, 10, 0, .006, .01)))
            def native(*args):
                calls.append(args)
                self.assertEqual(namespace['marks']['cache_contract'], {'complete': False})
                cache.frames.stats.update(helper)
                ticks[0] += .5
                return dict(stats_delta=dict(helper), elapsed=.4, complete=True,
                            checks=dict.fromkeys(probe.CHECKS[:-1], True))  # outer_deadline is the fixture's own.
            namespace['cache_native'].run = native
            checks = {}
            additive(cache.controller, checks)
            result = namespace['marks']['cache_contract']
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-2], 112)
            self.assertEqual((result['inner_elapsed'], result['elapsed']), (.4, .5))
            self.assertEqual(result['stats_delta'], helper)
            self.assertTrue(checks['8_cache_contract_complete'])
            self.assertIs(namespace['state']['observed']['additive_cache_contract'], result)

    def test_a_missing_direct_check_cannot_complete_the_gate(self):
        """The direct checks are the reuse gate: one the probe stops reporting fails instead of passing unseen."""
        with HostCache() as cache, mock.patch.object(angelo_agent, 'marble_frames', cache.frames, create=True):
            namespace, additive, ticks = self.integration_environment(cache)
            reported = dict.fromkeys(probe.CHECKS[:-1], True)
            del reported['pixels']
            namespace['cache_native'].run = lambda *args: dict(stats_delta=dict.fromkeys(contract.FIELDS, 0),
                                                               elapsed=0, checks=reported, complete=True)
            checks = {}
            additive(cache.controller, checks)
            self.assertFalse(checks['8_cache_contract_complete'])

    def test_additive_preconditions_reject_active_measurement_or_missing_original_stage(self):
        for failure in ('frame', 'picture', 'rotated', 'loop', 'orbit', 'handler', 'loop_timer', 'orbit_timer'):
            with self.subTest(failure=failure), HostCache() as cache, mock.patch.object(angelo_agent, 'marble_frames', cache.frames, create=True):
                namespace, additive, ticks = self.integration_environment(cache)
                native = mock.Mock()
                namespace['cache_native'].run = native
                marks = namespace['marks']
                if failure in ('frame', 'picture', 'rotated'):
                    del marks[failure]
                elif failure in ('loop', 'orbit'):
                    marks[failure].running = True
                elif failure == 'handler':
                    marks['orbit'].handler = object()
                else:
                    callback = marks['loop']._step if failure == 'loop_timer' else marks['orbit']._move
                    namespace['bpy'].app.timers.is_registered = lambda value: value is callback
                with self.assertRaises(AssertionError):
                    additive(cache.controller, {})
                native.assert_not_called()

    def test_native_exception_keeps_attempt_marker_and_counter_drift_is_fatal(self):
        for failure in ('exception', 'counter'):
            with self.subTest(failure=failure), HostCache() as cache, mock.patch.object(angelo_agent, 'marble_frames', cache.frames, create=True):
                namespace, additive, ticks = self.integration_environment(cache)
                def native(*args):
                    if failure == 'exception':
                        raise RuntimeError('injected native failure')
                    cache.frames.stats['full'] += 1
                    return dict(stats_delta=dict.fromkeys(contract.FIELDS, 0), elapsed=0,
                                checks={'cleanup': True}, complete=True)
                namespace['cache_native'].run = native
                with self.assertRaises((RuntimeError, AssertionError)):
                    additive(cache.controller, {})
                self.assertEqual(namespace['marks']['cache_contract'], {'complete': False})

    def test_actual_measure_calls_additive_once_after_original_stages(self):
        with HostCache() as cache:
            gauge = NS(LoopRate=NS(_step=object()), Orbit=NS(_drawn=object(), _move=object()),
                       frame_cost=object(), same_picture=object(), alternate=lambda *args: True)
            marks = dict(loop=NS(count=0), orbit=object(), rotated=True, frame={}, picture={}, orbit_from=0)
            additive = mock.Mock(side_effect=lambda *args: marks.update(cache_contract={'complete': False}))
            namespace = dict(cache_probe=probe, marks=marks, since=lambda: 17, state={'observed': {}},
                             ended=lambda controller: None, additive_cache_checks=additive)
            measure = self.compile_function('measure', namespace)
            with mock.patch.dict(sys.modules, {'chat_waiting_measure': gauge}), mock.patch.object(angelo_agent, 'marble_frames', cache.frames, create=True):
                measure(cache.controller, {})
                measure(cache.controller, {})
            additive.assert_called_once()
            calls = {ast.unparse(node.func): node.lineno for node in ast.walk(self.functions['measure'])
                     if isinstance(node, ast.Call)}
            for name in ('gauge.frame_cost', 'gauge.same_picture', "marks['loop'].stop", "marks['orbit'].stop"):
                self.assertLess(calls[name], calls['additive_cache_checks'])


if __name__ == "__main__":
    unittest.main()
