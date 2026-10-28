# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed native cache cases after the original measurements: the marble's reuse gate (the aggregate ratio is
reported only, test_native_marble_cache_contract)."""

import time
import traceback

import test_native_marble_cache_contract as contract

ACTIVE = None
MAX_DRAWS = 18  # Includes the disabled setup, direct pixel draw and one reserved cleanup draw.
MAX_SECONDS = 2.0
# Every result the fixture must see, the per-case ones first; ``outer_deadline`` is the fixture's own.
CASES = ('cold', 'key', 'size', 'expiry', 'unrequested', 'pixels')
CHECKS = CASES + ('cleanup', 'deadline', 'accounting', 'outer_deadline')
KEY_FIELDS = ('width', 'height', 'scale', 'font', 'rtl', 'session', 'version', 'status', 'attachments',
              'scroll', 'draft', 'font_size', 'input_serial', 'hover', 'pressed', 'focus', 'focused',
              'selection', 'scrollbar', 'jobs', 'hero_size', 'clock')


def assert_inactive():
    assert ACTIVE is None, 'Cache observer/capture must be absent during original measurements'


class Observer:
    """Original callbacks once each; one reuse decision and POST_PIXEL notification per explicit draw."""
    def __init__(self, frames, host, chat, area, region, target=lambda: True):
        self.frames, self.host, self.chat = frames, host, chat
        self.area, self.region, self.target = area, region, target
        self.record, self.inside, self.patches, self.records = None, None, [], []
        self.outside_errors = []

    def __enter__(self):
        global ACTIVE
        assert_inactive()
        self.original_reuse, self.original_key = self.frames.reuse, self.frames.key
        self.original_wait, self.original_time = self.frames._wait, self.frames.time
        ACTIVE = self
        try:
            self.install()
        except BaseException as primary:
            try:
                self.__exit__(None, None, None)
            except Exception as cleanup_error:
                raise primary from cleanup_error
            raise
        return self

    def install(self):
        for name, value in (('reuse', self.reuse), ('key', self.key), ('_wait', self.wait), ('time', self)):
            self.patches.append((name, getattr(self.frames, name)))
            setattr(self.frames, name, value)

    def __exit__(self, *args):
        global ACTIVE
        errors = []
        try:
            for name, original in reversed(self.patches):
                try:
                    setattr(self.frames, name, original)
                except Exception as error:
                    errors.append(str(error))
        finally:
            ACTIVE = None
        assert not errors, "Callback restoration failed: " + repr(errors)

    def __getattr__(self, name):
        original = vars(self).get("original_time")
        if original is None:
            raise AttributeError(name)
        return getattr(original, name)

    def monotonic(self):
        now = self.original_time.monotonic()
        if self.inside is not None:
            self.inside.setdefault('now', now)
        return now

    def key(self, *args, **kwargs):
        value = self.original_key(*args, **kwargs)
        if self.inside is not None:
            self.inside.update(current=value, key_called=True)
            self.inside['key_calls'] = self.inside.get('key_calls', 0) + 1
        if self.record is not None and args[0] == self.area and args[1] == self.region:
            self.record['draw_key'] = value
        return value

    def wait(self, *args, **kwargs):
        value = self.original_wait(*args, **kwargs)
        if self.record is not None:
            self.record['live_wait'] = value is not None
        if self.inside is not None:
            self.inside['wait_called'] = True
            self.inside['wait_calls'] = self.inside.get('wait_calls', 0) + 1
            self.inside['wait_exists'] = value is not None
        return value

    def reuse(self, area, region):
        if self.record is None or area != self.area or region != self.region or not self.target():
            if self.record is None and area == self.area and region == self.region and self.target():
                self.outside_errors.append("reuse outside explicit draw")
            return self.original_reuse(area, region)
        r = self.record
        r['reuse_calls'] += 1
        if self.inside is not None:
            r['errors'].append('nested reuse')
        entry = self.frames._kept.get(area.as_pointer())
        kept = entry.get('key') if entry is not None else None
        e = dict(asked=area.as_pointer() in self.frames._requested, entry=entry is not None,
                 size=entry is not None and entry['size'] == (region.width, region.height),
                 at=entry.get('at') if entry is not None else None, key_called=False, wait_exists=False, wait_called=False)
        previous, self.inside = self.inside, e
        try:
            used = self.original_reuse(area, region)
        finally:
            self.inside = previous
        if e.get('wait_calls', 0) > 1 or e.get('key_calls', 0) > 1:
            r['errors'].append('duplicate original callback')
        current = e.get('current')
        reason = ('unrequested' if not e['asked'] else 'cold' if not e['entry'] else
                  'unobserved' if not e['wait_called'] else 'no_wait' if not e['wait_exists'] else 'unobserved' if 'now' not in e else
                  'expired' if e['now'] - e['at'] > self.frames.REFRESH else 'size' if not e['size'] else
                  'unobserved' if not e['key_called'] else 'uninitialized_key' if kept is None else
                  'ineligible_key' if current is None else
                  ('clock' if current[:-1] == kept[:-1] else 'key_changed') if current != kept else 'stable')
        changed = [KEY_FIELDS[i] if i < len(KEY_FIELDS) else str(i)
                   for i, (a, b) in enumerate(zip(kept or (), current or ())) if a != b]
        r.update(reason=reason, eligible=reason == 'stable', reused=bool(used), changed=changed)
        return used

    def begin(self, intent, requested, capture=False):
        assert self.record is None, 'Nested explicit draw'
        self.before, self.before_draws = dict(self.frames.stats), self.chat.draw_count
        self.record = dict(sequence=len(self.records) + 1, intent=intent, requested=requested,
            enabled=self.host.enabled, bypass=self.frames.bypass, reuse_calls=0, post_calls=0,
            errors=[], capture=capture, live_wait=None)

    def post(self):
        if self.record is None and self.target():
            self.outside_errors.append('POST_PIXEL outside explicit draw')
        if self.record is not None and self.target():
            self.record['post_calls'] += 1

    def end(self, validate=True):
        r, self.record = self.record, None
        r['delta'] = contract.delta(self.before, self.frames.stats)
        r['chat_draws'] = self.chat.draw_count - self.before_draws
        entry = self.frames._kept.get(self.area.as_pointer())
        r['cache_empty'] = entry is None
        r['entry_valid'] = entry is not None and entry['size'] == (self.region.width, self.region.height) and entry['key'] is not None
        r['entry_size'] = None if entry is None else entry['size']
        r['entry_at'] = None if entry is None else entry['at']
        self.records.append(r)
        if validate:
            contract.frame(r)
        return r


class Driver:
    """Dependency-injected fixed draw driver; native paint and fixture draft access are supplied by run()."""
    def __init__(self, observer, paint, quiet, read_draft, write_draft, deadline, clock=time.monotonic, alive=lambda: True):
        self.observer, self.paint, self.quiet = observer, paint, quiet
        self.read_draft, self.write_draft, self.deadline, self.clock = read_draft, write_draft, deadline, clock
        self.frames, self.host, self.region = observer.frames, observer.host, observer.region
        self.pointer, self.draws, self.seeds = observer.area.as_pointer(), 0, []
        self.alive = alive
        self.case_results = dict.fromkeys(CASES, False)
        self.secondary_errors = []

    def guard(self):
        assert self.clock() < self.deadline, 'Cache probe exceeded its fixed quiet deadline'
        assert self.quiet(), 'Original controller/quiet turn no longer applies'

    def draw(self, intent, requested, capture=False, cleanup=False):
        assert self.alive(), 'Original draw target no longer exists'
        if not cleanup:
            self.guard()
        assert self.draws < MAX_DRAWS - (0 if cleanup else 1), 'Fixed draw budget exceeded'
        self.draws += 1
        if requested:
            self.frames.request(self.observer.area)
        else:
            self.frames._requested.discard(self.pointer)
        self.observer.begin(intent, requested, capture)
        error = None
        try:
            assert self.paint() == {'FINISHED'}, 'Native redraw did not finish'
        except Exception as failure:
            error = failure
        try:
            record = self.observer.end(validate=False)
        except Exception as failure:
            if error is not None:
                self.secondary_errors.append(str(failure))
                raise error
            raise
        if error is not None:
            raise error
        contract.frame(record)
        if not cleanup:
            self.guard()
        return record

    def following(self, name, capture_last=False):
        return [self.draw(name + '_post_1', True), self.draw(name + '_post_2', True, capture_last)]

    def seed(self, field, value):
        entry = self.frames._kept[self.pointer]
        self.seeds.append((entry, field, entry[field], value))
        entry[field] = value

    def restore_seeds(self):
        for entry, field, original, injected in reversed(self.seeds):
            # Do not resurrect an offscreen freed/replaced by cold, resize or bypass drawing.
            if self.frames._kept.get(self.pointer) is entry and entry[field] == injected:
                entry[field] = original


def cases(driver):
    """Seventeen explicit draws; run() reserves draw eighteen for restoration and verification."""
    d = driver
    d.host.enabled = False
    setup = d.draw('cold_setup', False)
    assert setup['cache_empty'] and not setup['live_wait'] and setup['chat_draws'] == 1
    d.host.enabled = True
    cold = d.draw('cold', True)
    contract.refreshed(cold, d.following('cold'), 'cold')
    d.case_results['cold'] = True
    original = d.read_draft()
    changed = original[0] + ' cache probe'
    d.write_draft((changed, len(changed)))
    key = d.draw('key', True)
    contract.refreshed(key, d.following('key'), 'key_changed', changed='draft')
    d.case_results['key'] = True
    d.seed('size', (d.region.width + 1, d.region.height))
    size = d.draw('size', True)
    contract.refreshed(size, d.following('size'), 'size')
    d.case_results['size'] = True
    d.seed('at', d.clock() - d.frames.REFRESH - 1)
    expiry = d.draw('expiry', True)
    contract.refreshed(expiry, d.following('expiry'), 'expired')
    d.case_results['expiry'] = True
    ordinary = d.draw('unrequested', False)
    assert ordinary['reason'] == 'unrequested' and ordinary['delta']['full'] == 1
    following = d.following('unrequested', capture_last=True)
    assert any(item['eligible'] and item['reused'] for item in following)
    d.case_results['unrequested'] = True
    first = following[-1]  # Fixed capture, never select a passing frame from retries.
    d.frames.bypass = True
    direct = d.draw('direct_pixels', False, capture=True)
    parity = contract.pixel_difference(first, direct)
    d.case_results["pixels"] = True
    return first, direct, parity


def execute(driver, install_capture, remove_capture, read_selection, restore_selection, save_pixels, hard_deadline):
    """Run/restore once. Cleanup is inside accounting; failures are data and never cause a retry."""
    d, obs, frames = driver, driver.observer, driver.frames
    started, before = d.clock(), dict(frames.stats)
    flags = d.host.enabled, frames.bypass, frames.trace, frames.frame_cost
    requested = d.pointer in frames._requested
    saved_draft = saved_selection = None
    entered, handle = False, None
    report = {'planned_draws': MAX_DRAWS, 'checks': {}, 'cleanup_errors': []}
    d.case_results = dict.fromkeys(CASES, False)
    try:
        d.guard()
        saved_draft, saved_selection = d.read_draft(), read_selection()
        assert flags[0] and not flags[1], 'Probe needs the original live wait, not a bypassed canvas'
        frames.trace = None
        obs.__enter__()
        entered = True
        handle = install_capture()
        first, direct, parity = cases(d)
        report['pixel_parity'] = parity
        report['captures'] = save_pixels(first, direct)
        d.guard()
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        def cleanup(action):
            try:
                action()
            except Exception:
                report['cleanup_errors'].append(traceback.format_exc())
        cleanup(lambda: setattr(d.host, 'enabled', flags[0]))
        cleanup(lambda: setattr(frames, 'bypass', flags[1]))
        cleanup(d.restore_seeds)
        if saved_draft is not None:
            cleanup(lambda: d.write_draft(saved_draft))
        if saved_selection is not None:
            cleanup(lambda: restore_selection(saved_selection))
        if entered and d.draws:
            cleanup(lambda: d.draw('cleanup', False, cleanup=True))
        # Restore feedback/pending-request state, not monotonic counters. Never restore a freed GPU object.
        cleanup(lambda: setattr(frames, 'frame_cost', flags[3]))
        cleanup(lambda: setattr(frames, 'trace', flags[2]))
        cleanup(lambda: frames._requested.add(d.pointer) if requested else frames._requested.discard(d.pointer))
        if handle is not None:
            cleanup(lambda: remove_capture(handle))
        if entered:
            cleanup(lambda: obs.__exit__(None, None, None))
        if saved_draft is not None:
            cleanup(lambda: require(d.read_draft() == saved_draft, 'Draft/caret restoration failed'))
        if saved_selection is not None:
            cleanup(lambda: require(read_selection() == saved_selection, 'Selection restoration failed'))
    report['raw_before'], report['raw_after'] = before, dict(frames.stats)
    report['draws'] = d.draws
    report['checks'].update(d.case_results)
    report['checks']['cleanup'] = not report['cleanup_errors'] and not d.secondary_errors and ACTIVE is None
    report['checks']['deadline'] = d.clock() < hard_deadline and d.quiet()
    try:
        report['stats_delta'] = contract.delta(before, frames.stats)
        sums = {key: sum(r['delta'][key] for r in obs.records) for key in contract.FIELDS}
        contract.adjusted(report['stats_delta'], sums)
        require(all(abs(report['stats_delta'][key] - sums[key]) <= 1e-12 for key in sums), 'Unpaired helper work')
        require(not obs.outside_errors, 'Draw outside explicit case: ' + repr(obs.outside_errors))
        report['checks']['accounting'] = True
    except Exception:
        report['checks']['accounting'] = False
        report['accounting_error'] = traceback.format_exc()
    report['frames'] = [{k: v for k, v in r.items() if k not in {'pixels', 'content_key', 'draw_key'}} for r in obs.records]
    report['elapsed'] = d.clock() - started
    report['checks']['deadline'] = d.clock() < hard_deadline and d.quiet()
    report['complete'] = not report.get('error') and all(report['checks'].values()) and d.draws == MAX_DRAWS
    return report


def require(condition, message):
    assert condition, message
