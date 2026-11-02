/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/**
 * Angelo: the viewport UV checker (doc/angelo/uv_checker.rst).
 *
 * Texel density, stretch and flipped UVs come from screen-space derivatives. Within a triangle
 * both the world position and the UV are affine, so the map from the surface to UV space is the
 * same for every pixel of it, and the screen's own Jacobian cancels out of every ratio below: the
 * values are exact per triangle, need no CPU pass and follow edits live.
 */

#include "infos/overlay_uv_checker_infos.hh"

FRAGMENT_SHADER_CREATE_INFO(overlay_uv_checker_0)

#include "draw_view_lib.glsl"

/* #eView3DUVCheckerPattern. */
#define PATTERN_COLOR_GRID 0
#define PATTERN_CHECKER 1
#define PATTERN_TEXEL_DENSITY 2
#define PATTERN_STRETCH 3

/** Blue (0) through cyan, green and yellow to red (1). */
float3 heat_color(float t)
{
  float hue = (2.0f / 3.0f) * (1.0f - clamp(t, 0.0f, 1.0f));
  float3 rgb = abs(fract(hue + float3(1.0f, 2.0f / 3.0f, 1.0f / 3.0f)) * 6.0f - 3.0f) - 1.0f;
  return clamp(rgb, 0.0f, 1.0f);
}

void main()
{
  float2 uv = uv_interp * pattern_scale;
  float3 color;
  if (pattern == PATTERN_TEXEL_DENSITY || pattern == PATTERN_STRETCH) {
    /* Only these patterns pay for the metric (most of the shader). */
    float3 dpdx = gpu_dfdx(world_pos);
    float3 dpdy = gpu_dfdy(world_pos);
    float2 duvdx = gpu_dfdx(uv_interp);
    float2 duvdy = gpu_dfdy(uv_interp);
    /* Metric of the surface (G) and of its UVs (H) over the same two screen directions. */
    float g11 = dot(dpdx, dpdx), g12 = dot(dpdx, dpdy), g22 = dot(dpdy, dpdy);
    float h11 = dot(duvdx, duvdx), h12 = dot(duvdx, duvdy), h22 = dot(duvdy, duvdy);
    float det_g = max(g11 * g22 - g12 * g12, 1e-30f);
    float det = max((h11 * h22 - h12 * h12) / det_g, 0.0f);
    float t;
    if (pattern == PATTERN_TEXEL_DENSITY) {
      /* Texels per unit length is the texture size times sqrt(UV area / surface area); each step of 2x below or
       * above the target is a quarter of the ramp, green on target. */
      float ratio = density_factor * sqrt(sqrt(det));
      t = (log2(max(ratio, 1e-6f)) + 2.0f) * 0.25f;
    }
    else {
      /* Eigenvalues of G^-1 H are the squared singular values of the surface-to-UV map. Angle distortion: 1 for a
       * conformal map; collapsed UVs (no area) show as the most stretched. */
      float trace = (g22 * h11 - 2.0f * g12 * h12 + g11 * h22) / det_g;
      float disc = sqrt(max(trace * trace * 0.25f - det, 0.0f));
      float sigma_max_sq = trace * 0.5f + disc;
      float sigma_min_sq = max(trace * 0.5f - disc, 0.0f);
      float aspect = sigma_min_sq > 0.0f ? sqrt(sigma_max_sq / sigma_min_sq) : 1e6f;
      t = log2(aspect) * 0.5f;
    }
    color = heat_color(t);
    /* A faint checker keeps the UV layout readable under the colors. */
    float2 cell = floor(uv * 8.0f);
    color *= (mod(cell.x + cell.y, 2.0f) < 0.5f) ? 1.0f : 0.85f;
  }
  else {
    color = texture(pattern_tx, uv).rgb;
  }

  if (show_flipped) {
    /* Flipped: the UV winding disagrees with the face's. cross(dpdx, dpdy) is det(J) times
     * cross(dP/du, dP/dv), so the product's sign is that of the UV tangent frame against the
     * normal, whatever the screen's y direction is. */
    float2 duvdx = gpu_dfdx(uv_interp);
    float2 duvdy = gpu_dfdy(uv_interp);
    float uv_det = duvdx.x * duvdy.y - duvdx.y * duvdy.x;
    if (dot(cross(gpu_dfdx(world_pos), gpu_dfdy(world_pos)), world_nor) * uv_det < 0.0f) {
      float stripe = step(0.5f, fract((gl_FragCoord.x - gl_FragCoord.y) / 12.0f));
      color = mix(color, float3(1.0f, 0.1f, 0.6f), 0.55f * stripe);
    }
  }

  /* Light it from the eye so the form stays readable. */
  float3 N = normalize(world_nor);
  float3 I = drw_world_incident_vector(world_pos);
  color *= mix(0.55f, 1.0f, abs(dot(N, I)));

  /* Pre-multiplied: the overlay frame-buffer is composited, not blended into. */
  frag_color = float4(color * opacity, opacity);
}
