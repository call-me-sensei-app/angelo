/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bli
 * \brief The confinement's gate at Blender's writers (Angelo, doc/angelo/script_confinement.rst, "Writers that read
 * their path from data").
 *
 * Blender's writers build the path they write from data: the render output and its view suffixes, extension and
 * template values; a File Output node's folder, file name and items; a cache's folder and name; a bake's folder and
 * the names of what it bakes; an image's or a text's file. A confined agent script can set that data (a timer it
 * left, a handler, before or while a job runs), and the writer, unconfined, would then write where the script chose.
 *
 * The data decides, not the place (the re-read of f9f24cc6ffd): every such writer declares the inputs its path is
 * built from (`Input`: a property of data in a data-block). A write is judged when any input was set by a confined
 * script or its data-block was made, appended or linked inside a script region (`Gate::script_set`), or when its job
 * began inside a script region (`job_begin`); then the path must be one Angelo's output may take for the property
 * it was read from (`Gate::output_allowed`). Otherwise nothing changes: a person's render or bake of their own data
 * writes everything while the agent works. The Python layer installs the gate; without it nothing is refused.
 */

#pragma once

#include <cstddef>
#include <cstdint>

#include "BLI_function_ref.hh"
#include "BLI_span.hh"
#include "BLI_vector.hh"

namespace blender::angelo_writers {

/** One input of an output path: the property `prop` (its RNA identifier) of `data`, of the RNA type `type` (the most
 * specific type that defines it: `RenderSettings`, `NodeCompositorFileOutputItem`, `ID` for a data-block's name), in
 * the data-block `owner` (an `ID *`). The type keeps a set elsewhere in the same data-block (a strip's `filepath`,
 * any node's `name`) from judging this writer (the re-read of 0a0d4e95743, MUST-5). A null `type` and `prop`: the
 * data-block as a whole (judged only when a script made it). The first input a writer gives is its tag: the property
 * the path itself is read from. Four values always: no input can leave its type out by accident. */
struct Input {
  const void *owner;
  const void *data;
  const char *type;
  const char *prop;

  constexpr Input(const void *owner, const void *data, const char *type, const char *prop)
      : owner(owner), data(data), type(type), prop(prop)
  {
  }
};

struct Gate {
  /** How many script regions run now (any thread). */
  int (*regions)();
  /** Whether this thread runs agent code now. */
  bool (*inside)();
  /** Whether a confined script set `prop` of data of the RNA type `type` in `owner`, or `owner` was made, appended
   * or linked inside a script region. One lookup, no file system call. An unknown `type`/`prop` pair (no RNA type
   * defines it) answers true: a wrong declaration over-judges, loudly, never under-judges. */
  bool (*script_set)(const void *owner, const char *type, const char *prop);
  /** Whether Angelo's output may take the file `path` (expanded, absolute), read from the property `tag`. It may make
   * that file (a new one where it accepts it), so it is asked only right before the file is written. */
  bool (*output_allowed)(const char *path, const Input &tag);
  /** The same for a folder a writer makes, fills or removes without asking for each file (it may make the folder). */
  bool (*folder_allowed)(const char *path, const Input &tag);
  /** The refusal's English text (the person's interface translates it). */
  const char *refusal;
};

void gate_set(const Gate *gate);
const Gate *gate_get();

/** What the confinement knew when a writer job began. Trivially copyable (kept in C structs). */
struct Job {
  /** Began on a thread that ran agent code, or while a script region ran. */
  bool inside;
  /** Recorded by `job_begin`; a zeroed job (never begun) counts as begun inside: fail closed. */
  bool begun;
};

/** The job a writer starts now (on the thread that starts it). Without a gate: never judged. */
Job job_begin();

/** This thread writes for `job` while the scope lives (a WM job's thread, a modal writer's step). A task a thread
 * pushes to a task pool runs under the pusher's job (task_pool.cc), so the depsgraph's evaluation during a bake and
 * a render's write pool write for the job that made them; a write whose job is unknown is judged by its inputs. */
class JobScope {
  const Job *previous_;

 public:
  explicit JobScope(const Job *job);
  ~JobScope();
  JobScope(const JobScope &) = delete;
  JobScope &operator=(const JobScope &) = delete;
};

/** A writer's own synchronous run on a thread with no job yet (a render or bake called from Python, the command
 * line): begins one for its lifetime; on a thread that has one already (a WM job's), uses that. */
class OwnJob {
  Job job_;
  bool active_;
  const Job *previous_;

 public:
  OwnJob();
  ~OwnJob();
  OwnJob(const OwnJob &) = delete;
  OwnJob &operator=(const OwnJob &) = delete;
};

/** A writer that runs across several steps (a modal operator's): begun when the object is made (a member of the
 * writer's own state); its steps put it on their thread with a `JobScope`. */
class HeldJob {
  Job job_;

 public:
  HeldJob();
  HeldJob(const HeldJob &) = delete;
  HeldJob &operator=(const HeldJob &) = delete;
  const Job *get() const
  {
    return &job_;
  }
};

/** The job this thread writes for, or null. */
const Job *current_job();

/** The inputs this thread's next writes are built from, while the scope lives: for a writer that calls deeper code
 * which writes (the render pipeline around a frame's files or a movie's open, an image's save). A nested scope
 * replaces the outer one. */
class InputScope {
  Vector<Input, 16> inputs_;
  const InputScope *previous_;

 public:
  explicit InputScope(Span<Input> inputs);
  template<size_t N> explicit InputScope(const Input (&inputs)[N]) : InputScope(Span<Input>(inputs, N)) {}
  ~InputScope();
  InputScope(const InputScope &) = delete;
  InputScope &operator=(const InputScope &) = delete;
  Span<Input> inputs() const
  {
    return inputs_;
  }
};

/** The refusal (English) for writing, replacing or removing the file at `path` (absolute, expanded) now, built from
 * `inputs` (the first is the tag), or null. `path` null or empty: null. `original_data`, for a writer that runs on an
 * evaluated copy: the tag's data as the person edits it (where their typed values are kept), asked only for a judged
 * write. */
const char *refusal_for(const char *path,
                        Span<Input> inputs,
                        FunctionRef<const void *()> original_data = nullptr);
template<size_t N> const char *refusal_for(const char *path, const Input (&inputs)[N])
{
  return refusal_for(path, Span<Input>(inputs, N));
}
/** As `refusal_for`, with this thread's `InputScope` (none: only the job decides). */
const char *refusal(const char *path);
/** As `refusal_for`, for a folder the writer makes, writes files in without asking for each, or removes. */
const char *refusal_folder(const char *path,
                           Span<Input> inputs,
                           FunctionRef<const void *()> original_data = nullptr);
template<size_t N> const char *refusal_folder(const char *path, const Input (&inputs)[N])
{
  return refusal_folder(path, Span<Input>(inputs, N));
}
/** As `refusal_folder`, with this thread's `InputScope`. */
const char *refusal_folder(const char *path);

}  // namespace blender::angelo_writers
