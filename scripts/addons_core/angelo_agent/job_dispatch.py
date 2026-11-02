# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which runner each job kind uses (``job_runners.build`` calls ``runner``).

Kept apart from ``job_runners`` so each kind's module (renders, region renders, bakes, texture sets, fluids, exports)
is imported only when a job of that kind is built; every runner module imports ``job_runners``, so these imports are
made at call time.
"""


def runner(window, request, workspace, target, resolved):
    """The runner class for ``request["kind"]``, constructed (``job_runners.build`` binds it)."""
    kind = request["kind"]
    if kind in {"RENDER_STILL", "RENDER_ANIMATION"}:
        from .job_runners import RenderRunner
        return RenderRunner(window, request, workspace)
    if kind == "RENDER_REGION":
        from . import job_render_region
        return job_render_region.RegionRenderRunner(window, request, workspace)
    from . import job_bakes
    if kind == "BAKE_TEXTURE":
        return job_bakes.TextureBakeRunner(window, request, workspace, target, **(resolved or {}))
    if kind == "BAKE_MAPS":
        from .job_bake_maps import TextureSetRunner
        return TextureSetRunner(window, request, workspace, (resolved or {}).get("targets", ()),
                                (resolved or {}).get("sources", ()), (resolved or {}).get("cage"))
    if kind == "BAKE_PHYSICS":
        return job_bakes.PhysicsBakeRunner(window, request, workspace)
    if kind == "BAKE_SIMULATION":
        from . import job_fluid
        if job_fluid.is_domain(target):   # A Mantaflow domain: smoke, fire or liquid.
            return job_fluid.FluidBakeRunner(window, request, workspace, target)
        return job_bakes.SimulationBakeRunner(window, request, workspace, target)
    from .job_runners import ExportRunner
    return ExportRunner(window, request, workspace)
