/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "infos/overlay_tension_infos.hh"

FRAGMENT_SHADER_CREATE_INFO(overlay_tension_mesh)

void main()
{
  /* Compression blue, neutral near white, stretch red. Unstrained surface keeps a light veil so the
   * map reads as on, while the strained areas carry the color. */
  constexpr float3 compress_color = float3(0.13f, 0.42f, 0.96f);
  constexpr float3 neutral_color = float3(0.93f, 0.93f, 0.93f);
  constexpr float3 stretch_color = float3(0.96f, 0.22f, 0.16f);
  constexpr float neutral_alpha = 0.2f;

  /* A NaN (a degenerate rest shape) reads as unstrained. */
  float value = isnan(tension_value) ? 0.0f : tension_value;
  float amount = (value < 0.0f) ? clamp(-value / compress_max, 0.0f, 1.0f) :
                                  clamp(value / stretch_max, 0.0f, 1.0f);
  float3 extreme = (value < 0.0f) ? compress_color : stretch_color;
  out_color.rgb = mix(neutral_color, extreme, amount);
  out_color.a = opacity * mix(neutral_alpha, 1.0f, amount);
  /* Writing to this second texture is necessary to avoid undefined behavior. */
  line_output = float4(0.0f);
}
