# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the compile-ahead statistics may cost on the path every draw takes, pinned in the source (break-it C5 on 5.3).

Vulkan looks its graphics pipeline up in every draw (``VKShader::ensure_and_get_graphics_pipeline``, from
``VKContext::update_pipeline_data``). The statistics time a pipeline's creation, which is rare; reading the clock
around the lookup paid for it in every draw. The clock is read where a pipeline is created, nowhere else on that
path. Without Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLOCK = "BLI_time_now_seconds"


def body(text, start):
    """The braces' content of the function whose definition contains ``start``."""
    opened = text.index("{", text.index(start))
    depth = 0
    for index in range(opened, len(text)):
        depth += (text[index] == "{") - (text[index] == "}")
        if depth == 0:
            return text[opened:index + 1]
    raise AssertionError("Unbalanced braces after %r" % start)


class VulkanDrawPathTests(unittest.TestCase):
    def test_a_draws_pipeline_lookup_reads_the_clock_only_where_it_creates_one(self):
        shader = (ROOT / "source/blender/gpu/vulkan/vk_shader.cc").read_text(encoding="utf-8")
        for function in ("VkPipeline VKShader::ensure_and_get_graphics_pipeline(",
                         "bool VKShader::ensure_graphics_pipelines("):
            self.assertNotIn(CLOCK, body(shader, function), "%s reads the clock in every call" % function)
        pool = (ROOT / "source/blender/gpu/vulkan/vk_pipeline_pool.cc").read_text(encoding="utf-8")
        self.assertNotIn(CLOCK, body(pool, "VkPipeline VKPipelinePool::get_or_create_graphics_pipeline("))
        lookup = body((ROOT / "source/blender/gpu/vulkan/vk_pipeline_pool.hh").read_text(encoding="utf-8"),
                      "VkPipeline get_or_create(const PipelineInfo &pipeline_info,")
        created = body(lookup, "if (do_compile_pipeline)")
        self.assertEqual(lookup.count(CLOCK), created.count(CLOCK), "read outside the branch that creates one")
        self.assertEqual(created.count(CLOCK), 2, "a creation is timed, start and end")

    def test_the_statistics_still_time_a_created_pipeline(self):
        shader = (ROOT / "source/blender/gpu/vulkan/vk_shader.cc").read_text(encoding="utf-8")
        for function, in_draw in (("VkPipeline VKShader::ensure_and_get_graphics_pipeline(", "true"),
                                  ("bool VKShader::ensure_graphics_pipelines(", "false")):
            self.assertIn("pipeline_stats_built(%s, created_seconds * 1000.0)" % in_draw, body(shader, function))


if __name__ == "__main__":
    unittest.main()
