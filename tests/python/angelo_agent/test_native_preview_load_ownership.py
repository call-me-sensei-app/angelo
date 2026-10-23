# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pin who may touch a deferred PreviewImage while the preview-loading job runs.

The main thread replaces and frees a pending request's PreviewImage when an asset library
reloads and the UI asks for the same file again (``PreviewLoadJob::push_load_request``). It
does so only while holding ``requested_previews_mutex_``. The job thread therefore must read
the preview, and write the loaded pixels into it, only under that mutex, and must keep its own
copy of the file path for the slow thumbnail load in between. Reading the path from the
preview outside the mutex was a use-after-free: the thumbnail path lock was released with
freed text ("Code marked as unreachable" in IMB_thumb_path_unlock), then the job read the
freed preview and crashed (1.0.4, Object-mode asset shelf with a library added at startup).
"""

from pathlib import Path
import re
import unittest

SOURCE = Path(__file__).resolve().parents[3] / 'source/blender/editors/render/render_preview.cc'
LOCK = 'std::lock_guard lock(job_data->requested_previews_mutex_);'
OWN_LOCK = 'std::lock_guard lock(requested_previews_mutex_);'


def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}\n', start) + 2
    return source[start:end]


def locked_ranges(body, lock_statement):
    """Return (start, end) offsets of every brace block that begins by taking the lock."""
    ranges = []
    for match in re.finditer(re.escape(lock_statement), body):
        open_brace = body.rindex('{', 0, match.start())
        depth = 0
        for index in range(open_brace, len(body)):
            if body[index] == '{':
                depth += 1
            elif body[index] == '}':
                depth -= 1
                if depth == 0:
                    ranges.append((match.start(), index))
                    break
    return ranges


def code_only(text):
    """Blank out comments so that names mentioned in them are not counted as uses."""
    text = re.sub(r'/\*.*?\*/', lambda m: ' ' * len(m.group(0)), text, flags=re.S)
    return re.sub(r'//[^\n]*', lambda m: ' ' * len(m.group(0)), text)


class PreviewLoadJobOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE.read_text()
        self.run_fn = code_only(function(self.source, 'void PreviewLoadJob::run_fn('))
        self.ranges = locked_ranges(self.run_fn, LOCK)

    def inside_lock(self, offset):
        return any(start < offset < end for start, end in self.ranges)

    def uses(self, pattern):
        return [m.start() for m in re.finditer(pattern, self.run_fn)]

    def test_job_reads_and_writes_the_preview_only_under_the_requests_mutex(self):
        patterns = (r'request->preview\b', r'\bBKE_previewimg_\w+\(', r'\bpreview->',
                    r'\bED_preview_use_image_size\(', r'\bicon_copy_rect\(')
        for pattern in patterns:
            offsets = self.uses(pattern)
            self.assertTrue(offsets, f'{pattern} is expected in run_fn')
            for offset in offsets:
                line = self.run_fn[:offset].count('\n') + 1
                self.assertTrue(self.inside_lock(offset),
                                f'run_fn line {line}: {pattern} outside requested_previews_mutex_')

    def test_slow_thumbnail_load_uses_a_job_owned_path_outside_the_mutex(self):
        for call in ('IMB_thumb_path_lock(', 'IMB_thumb_manage(', 'IMB_thumb_path_unlock('):
            offsets = self.uses(re.escape(call))
            self.assertEqual(len(offsets), 1, call)
            self.assertFalse(self.inside_lock(offsets[0]),
                             f'{call} must not block the main thread on the requests mutex')
            argument = self.run_fn[offsets[0] + len(call):].lstrip()
            self.assertTrue(argument.startswith('filepath.c_str()'), call)
        self.assertIn('std::string filepath;', self.run_fn)
        self.assertNotIn('StringRefNull> filepath', self.run_fn)

    def test_final_state_is_published_with_the_pixels_and_request_is_not_used_after(self):
        state = self.uses(r'request->state = thumb \?')
        self.assertEqual(len(state), 1)
        self.assertTrue(self.inside_lock(state[0]))
        block_end = next(end for start, end in self.ranges if start < state[0] < end)
        loop_rest = self.run_fn[block_end:self.run_fn.index('IMB_thumb_locks_release();')]
        self.assertNotIn('request->', loop_rest)

    def test_every_preview_alias_is_read_afresh_in_the_locked_block_that_uses_it(self):
        """A pointer read under the first lock and reused under the second is the 1.0.4 crash."""
        assignment = re.compile(r'\b(\w+)\s*=\s*([^;=][^;]*);')
        cast = re.compile(r'^(?:const_cast|static_cast|reinterpret_cast)<[^>]*>\((.*)\)$')
        aliases = set()

        def is_alias(rhs):
            rhs = rhs.strip()
            rhs = cast.sub(r'\1', rhs).strip()
            return rhs == 'request->preview' or rhs in aliases

        changed = True
        while changed:
            changed = False
            for match in assignment.finditer(self.run_fn):
                if is_alias(match.group(2)) and match.group(1) not in aliases:
                    aliases.add(match.group(1))
                    changed = True
        self.assertIn('preview', aliases)
        for name in sorted(aliases):
            reads = [m.start() for m in assignment.finditer(self.run_fn)
                     if m.group(1) == name and is_alias(m.group(2))]
            for use in re.finditer(rf'\b{name}\b', self.run_fn):
                block = next(((s, e) for s, e in self.ranges if s < use.start() < e), None)
                line = self.run_fn[:use.start()].count('\n') + 1
                self.assertTrue(block and any(block[0] < read <= use.start() for read in reads),
                                f'run_fn line {line}: {name} is not read afresh in its locked block')

    def test_a_request_finished_into_the_old_preview_is_finished_and_requested_again(self):
        push = code_only(function(self.source, 'void PreviewLoadJob::push_load_request('))
        finished = ('ELEM(existing_request->state.load(), PreviewState::Ready, '
                    'PreviewState::Failed)')
        self.assertIn(finished, push, 'a finished request must not be swapped to a new preview')
        guard = push.index(finished)
        branch = push[guard:push.index('else', guard)]
        self.assertLess(branch.index('finish_request(*existing_request);'),
                        branch.index('requested_previews_.remove(key);'))
        self.assertNotIn('return', branch)
        self.assertLess(push.index('if (existing_request->preview == preview) {'), guard)
        self.assertLess(guard, push.index('requested_previews_.add(key, std::move(new_request));'))

    def test_main_thread_frees_a_replaced_preview_only_under_the_same_mutex(self):
        push = code_only(function(self.source, 'void PreviewLoadJob::push_load_request('))
        ranges = locked_ranges(push, OWN_LOCK)
        for pattern in (r'BKE_previewimg_render_end\(existing_request->preview',
                        r'existing_request->preview = preview;'):
            offsets = [m.start() for m in re.finditer(pattern, push)]
            self.assertEqual(len(offsets), 1, pattern)
            self.assertTrue(any(s < offsets[0] < e for s, e in ranges), pattern)
        for name in ('void PreviewLoadJob::update_fn(', 'void PreviewLoadJob::end_fn('):
            body = code_only(function(self.source, name))
            lock = body.index(LOCK)
            self.assertLess(lock, body.index('finish_request('), name)


if __name__ == '__main__':
    unittest.main()
