/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "infos/overlay_uv_checker_infos.hh"

FRAGMENT_SHADER_CREATE_INFO(overlay_uv_checker_missing)

/* No such UV map: magenta and dark grey diagonal stripes, as missing textures are drawn. */
void main()
{
  float stripe = step(0.5f, fract((gl_FragCoord.x + gl_FragCoord.y) / 16.0f));
  float3 color = mix(float3(0.85f, 0.15f, 0.75f), float3(0.12f), stripe);
  /* Pre-multiplied: the overlay frame-buffer is composited, not blended into. */
  frag_color = float4(color * opacity, opacity);
}
