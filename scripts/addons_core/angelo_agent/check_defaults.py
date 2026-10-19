# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a check means by a field it leaves out (pure Python, no bpy).

Every check kind takes ``tolerance`` optionally. Twenty-three kinds once
required it while ten did not, so a plan whose dimensions or transform check
left it out was refused before anything ran (by Claude Code's schema check, with
an error of over 20,000 characters whose cause the model never saw).

A check without one compares each number at its own float32 read-back
precision (``value_tolerance``), as ``rna_rules.same`` does for a write's
read-back, and so do the checks the host derives itself, which state no
tolerance either. A single tolerance read from a check's largest number once
loosened its small ones: a light check stating 1,000,000 W compared its spot
size within 0.4 rad. Two kinds compare one quantity and
keep a stated default: an ``aim`` (an angle) and an ``armature_binding`` (each
vertex's total weight). Kinds whose evaluator compares by ``rna_rules.same``
when the tolerance is left out (``property``, the generic settings checks,
``node_graph``) are per value already.
"""

# A material_values check states any of these, at least one.
MATERIAL_VALUES = ("base_color", "roughness", "metallic")
# Kinds whose evaluator reads an omitted tolerance per number by value_tolerance (validation_reader.values_near).
READ_BACK_KINDS = frozenset({"dimensions", "transform", "material_values", "modifier", "animation",
                             "camera_settings", "light_settings", "curve_counts"})


def value_tolerance(value):
    """Absolute float32 read-back tolerance for one expected number (exact for small ones)."""
    return max(0.00001, 4e-7 * abs(value)) if type(value) in (int, float) else 0.00001


def proves_per_value(check):
    """Whether a check's stated tolerance is within every number's own, so it proves its twin that states none.

    No number's own tolerance is below ``value_tolerance(0)``, so one no larger is at least as strict.
    """
    tolerance = check.get("tolerance")
    return check["kind"] in READ_BACK_KINDS and type(tolerance) in (int, float) and 0 <= tolerance <= value_tolerance(0)


def complete(check):
    """Fill in the defaults of one schema-validated check; refuse one that compares nothing."""
    kind = check["kind"]
    if kind == "material_values" and not any(key in check for key in MATERIAL_VALUES):
        raise ValueError("A material_values check states at least one of base_color, roughness and metallic")
    if kind == "armature_binding" and "tolerance" not in check:
        check["tolerance"] = value_tolerance(1.0)  # Each weighted vertex's total weight.
    if kind == "aim" and "tolerance" not in check:
        from .derived_camera_light import AIM_CHECK_TOLERANCE  # An angle: the host's own aim checks use it.
        check["tolerance"] = AIM_CHECK_TOLERANCE
    return check
