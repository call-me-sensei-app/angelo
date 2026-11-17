/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The confined USD asset resolver (doc/angelo/script_confinement_pipeline.rst): USD's primary resolver in Angelo.
 * Every asset path USD resolves, opens or writes, for the Python `pxr` modules and Blender's own USD import and export
 * alike (they share one USD library), is judged by the agent-script confinement while a confined script runs. A
 * layer that composition reaches through a sublayer, reference, payload or a `.mtlx` file is judged like the file a
 * script names.
 */

namespace blender::io::usd {

/** The confinement's verdict on a path: null when allowed, else the refusal text (English). Called from any thread,
 * without the GIL (USD opens layers on its worker threads). */
using ConfinedPathJudge = const char *(*)(const char *path, bool write);

/** While a confined script runs and USD's resolver is not the confined one: the refusal (English) for every USD
 * entry point, else null. Called from any thread, without the GIL. */
using ConfinedUnavailable = const char *(*)();

/** Set once by the confinement (`install_pipeline_entries`); null judges nothing. */
void confined_resolver_set_judge(ConfinedPathJudge judge, ConfinedUnavailable unavailable);

/** The refusal for Blender's own USD entry points (import, export, a USD cache file, Hydra) while a confined
 * script runs and USD's resolver is not the confined one, else null: nothing USD does is judged then, so none of
 * it is a confined script's (fail closed). */
const char *confined_resolver_refusal();

/** Whether USD's primary asset resolver is the confined one. Creates USD's resolver when none exists yet, preferring
 * the confined one by name (another primary resolver plugin in the environment would otherwise win when its type
 * name sorts first): called once at the confinement's install, before any script, so that no environment a script
 * changes later (for example `PXR_AR_DISABLE_PLUGIN_RESOLVER`) decides which resolver USD uses. */
bool confined_resolver_active();

}  // namespace blender::io::usd
