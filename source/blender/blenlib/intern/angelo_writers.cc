/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bli
 * The confinement's gate at Blender's writers (BLI_angelo_writers.hh, doc/angelo/script_confinement.rst).
 */

#include <atomic>

#include "BLI_angelo_writers.hh"

namespace blender::angelo_writers {

static std::atomic<const Gate *> installed{nullptr};

static thread_local const Job *thread_job = nullptr;
static thread_local const InputScope *thread_inputs = nullptr;

void gate_set(const Gate *gate)
{
  installed.store(gate);
}

const Gate *gate_get()
{
  return installed.load();
}

Job job_begin()
{
  Job job{false, true};
  if (const Gate *gate = gate_get()) {
    job.inside = gate->inside() || gate->regions() > 0;
  }
  return job;
}

JobScope::JobScope(const Job *job) : previous_(thread_job)
{
  thread_job = job;
}

JobScope::~JobScope()
{
  thread_job = previous_;
}

OwnJob::OwnJob() : job_(job_begin()), active_(thread_job == nullptr), previous_(thread_job)
{
  if (active_) {
    thread_job = &job_;
  }
}

OwnJob::~OwnJob()
{
  if (active_) {
    thread_job = previous_;
  }
}

HeldJob::HeldJob() : job_(job_begin()) {}

const Job *current_job()
{
  return thread_job;
}

InputScope::InputScope(Span<Input> inputs) : inputs_(inputs), previous_(thread_inputs)
{
  thread_inputs = this;
}

InputScope::~InputScope()
{
  thread_inputs = previous_;
}

/** Whether the job this write belongs to began inside a script region. A write whose job is unknown (no job on its
 * thread: agent code writing for itself, a thread no job made) is judged by its inputs alone: agent code's own path
 * argument was judged where it gave it, and the file gate judges that write. */
static bool job_began_inside()
{
  const Job *job = thread_job;
  return job != nullptr && (!job->begun || job->inside);
}

/** Whether a write built from `inputs` is judged: one of them was set by a confined script (or its data-block came
 * from a script region), or its job began inside a region. One lookup per input. */
static bool judged(const Gate &gate, Span<Input> inputs)
{
  for (const Input &input : inputs) {
    if (input.owner != nullptr && gate.script_set(input.owner, input.type, input.prop)) {
      return true;
    }
  }
  return job_began_inside();
}

static Input tag_of(Span<Input> inputs, FunctionRef<const void *()> original_data)
{
  Input tag = inputs.is_empty() ? Input{nullptr, nullptr, nullptr, nullptr} : inputs.first();
  if (original_data) {
    tag.data = original_data();
  }
  return tag;
}

const char *refusal_for(const char *path, Span<Input> inputs, FunctionRef<const void *()> original_data)
{
  const Gate *gate = gate_get();
  if (gate == nullptr || path == nullptr || path[0] == '\0' || !judged(*gate, inputs)) {
    return nullptr; /* No script set what this path is built from: exactly as before. */
  }
  /* Asked only now, right before the write: it may make the file (a new one where it accepts it). */
  return gate->output_allowed(path, tag_of(inputs, original_data)) ? nullptr : gate->refusal;
}

const char *refusal(const char *path)
{
  return refusal_for(path, thread_inputs ? thread_inputs->inputs() : Span<Input>());
}

const char *refusal_folder(const char *path, Span<Input> inputs, FunctionRef<const void *()> original_data)
{
  const Gate *gate = gate_get();
  if (gate == nullptr || path == nullptr || path[0] == '\0' || !judged(*gate, inputs)) {
    return nullptr;
  }
  return gate->folder_allowed(path, tag_of(inputs, original_data)) ? nullptr : gate->refusal;
}

const char *refusal_folder(const char *path)
{
  return refusal_folder(path, thread_inputs ? thread_inputs->inputs() : Span<Input>());
}

}  // namespace blender::angelo_writers
