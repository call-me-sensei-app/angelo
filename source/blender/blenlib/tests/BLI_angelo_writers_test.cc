/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* The writers' gate (BLI_angelo_writers.hh): the data decides. A write is judged when an input its path is built
 * from was set by a script (or its data-block came from a script region), or its job began inside a region. */

#include <atomic>
#include <cstring>
#include <set>
#include <string>
#include <thread>
#include <utility>

#include "testing/testing.h"

#include "BLI_angelo_writers.hh"
#include "BLI_task_c.hh"
#include "BLI_threads.hh"

namespace blender::angelo_writers::tests {

static std::atomic<int> fake_regions{0};
static thread_local bool fake_inside = false;
/* What a script set: (owner, "Type/prop"); a whole data-block a script made: (owner, ""). */
static std::set<std::pair<const void *, std::string>> fake_set;
static std::string fake_allowed;
static std::string fake_allowed_folder;
static int lookups = 0;
static const char *const REFUSED = "refused";

static const Gate fake_gate = {
    [] { return fake_regions.load(); },
    [] { return fake_inside; },
    [](const void *owner, const char *type, const char *prop) {
      lookups++;
      return fake_set.count({owner, ""}) > 0 ||
             (type && prop && fake_set.count({owner, std::string(type) + "/" + prop}) > 0);
    },
    [](const char *path, const Input & /*tag*/) { return fake_allowed == path; },
    [](const char *path, const Input & /*tag*/) { return fake_allowed_folder == path; },
    REFUSED,
};

/* Two data-blocks: the person's scene and an object. */
static const int scene_id = 0, object_id = 0;
static const Input render_inputs[] = {{&scene_id, nullptr, "RenderSettings", "filepath"},
                                     {&scene_id, nullptr, "SceneRenderView", "file_suffix"}};

class AngeloWritersTest : public testing::Test {
 protected:
  void SetUp() override
  {
    fake_regions = 0;
    fake_inside = false;
    fake_set.clear();
    fake_allowed = "/own/frame.png";
    fake_allowed_folder = "/own";
    lookups = 0;
    gate_set(&fake_gate);
  }
  void TearDown() override
  {
    gate_set(nullptr);
  }
};

TEST_F(AngeloWritersTest, NoGateNeverRefuses)
{
  gate_set(nullptr);
  fake_set.insert({&scene_id, "RenderSettings/filepath"});
  EXPECT_EQ(refusal_for("/person/taxes.png", render_inputs), nullptr);
}

TEST_F(AngeloWritersTest, PersonsDataWritesWhileScriptsRun)
{
  const OwnJob job;
  fake_regions = 1; /* An agent script runs while the person's render writes. */
  EXPECT_EQ(refusal_for("/person/renders/frame_0002.png", render_inputs), nullptr);
}

TEST_F(AngeloWritersTest, ScriptSetInputJudged)
{
  const OwnJob job;
  fake_set.insert({&scene_id, "SceneRenderView/file_suffix"}); /* A view suffix: no path property, still an input. */
  EXPECT_EQ(refusal_for("/person/thesis.docx", render_inputs), REFUSED);
  EXPECT_EQ(refusal_for("/own/frame.png", render_inputs), nullptr);
}

TEST_F(AngeloWritersTest, AnotherDataBlocksSetDoesNotJudge)
{
  const OwnJob job;
  fake_set.insert({&object_id, "Image/filepath"}); /* A relinked texture elsewhere: this render's data is untouched. */
  EXPECT_EQ(refusal_for("/person/renders/frame_0001.png", render_inputs), nullptr);
}

TEST_F(AngeloWritersTest, SameNameOnAnotherTypeDoesNotJudge)
{
  /* The re-read of 0a0d4e95743, MUST-5: a script relinks a movie strip (`filepath`, owner the scene) or renames a
   * view: the scene's own render output is untouched, so the person's render is not judged. */
  const OwnJob job;
  fake_set.insert({&scene_id, "MovieStrip/filepath"});
  fake_set.insert({&scene_id, "Node/name"});
  EXPECT_EQ(refusal_for("/person/renders/frame_0001.png", render_inputs), nullptr);
  fake_set.insert({&scene_id, "RenderSettings/filepath"});
  EXPECT_EQ(refusal_for("/person/renders/frame_0001.png", render_inputs), REFUSED);
}

TEST_F(AngeloWritersTest, WholeDataBlockInputJudgedWhenScriptMadeIt)
{
  const OwnJob job;
  const Input inputs[] = {{&object_id, nullptr, "MultiresModifier", "filepath"},
                          {&scene_id, nullptr, nullptr, nullptr}};
  EXPECT_EQ(refusal_for("/person/sculpt.btx", inputs), nullptr);
  fake_set.insert({&scene_id, "Mesh/anything"}); /* A property set on it: not what a whole-block input asks. */
  EXPECT_EQ(refusal_for("/person/sculpt.btx", inputs), nullptr);
  fake_set.insert({&scene_id, ""}); /* Made by a script. */
  EXPECT_EQ(refusal_for("/person/sculpt.btx", inputs), REFUSED);
}

TEST_F(AngeloWritersTest, JobBegunInsideJudged)
{
  fake_inside = true;
  const OwnJob job;
  fake_inside = false;
  EXPECT_EQ(refusal_for("/person/taxes.png", render_inputs), REFUSED);
  EXPECT_EQ(refusal_for("/own/frame.png", render_inputs), nullptr);
}

TEST_F(AngeloWritersTest, JobBegunWhileARegionRanJudged)
{
  fake_regions = 1;
  const OwnJob job;
  fake_regions = 0;
  EXPECT_EQ(refusal_for("/person/taxes.png", render_inputs), REFUSED);
}

TEST_F(AngeloWritersTest, ZeroedJobFailsClosed)
{
  const Job never_begun{};
  const JobScope scope(&never_begun);
  EXPECT_EQ(refusal_for("/person/taxes.png", render_inputs), REFUSED);
}

TEST_F(AngeloWritersTest, AgentCodeWithoutJobJudgedByItsInputs)
{
  fake_inside = true; /* A script's own write with no job: its own path argument is the file gate's. */
  EXPECT_EQ(refusal_for("/person/taxes.png", {}), nullptr);
  fake_set.insert({&scene_id, "RenderSettings/filepath"}); /* What it writes from data it set is judged (MUST-D). */
  EXPECT_EQ(refusal_for("/person/taxes.png", render_inputs), REFUSED);
}

TEST_F(AngeloWritersTest, InputScopeReachesDeeperWrites)
{
  fake_set.insert({&scene_id, "RenderSettings/filepath"});
  EXPECT_EQ(refusal("/person/taxes.png"), nullptr); /* No scope: no inputs, no job. */
  {
    const InputScope scope(render_inputs);
    EXPECT_EQ(refusal("/person/taxes.png"), REFUSED);
    {
      const Input other[] = {{&object_id, nullptr, "PointCache", "filepath"}};
      const InputScope inner(other); /* A nested writer's own inputs replace the outer ones. */
      EXPECT_EQ(refusal("/person/taxes.png"), nullptr);
    }
    EXPECT_EQ(refusal("/person/taxes.png"), REFUSED);
  }
  EXPECT_EQ(refusal("/person/taxes.png"), nullptr);
}

static void task_writes(TaskPool *__restrict /*pool*/, void *taskdata)
{
  *static_cast<const char **>(taskdata) = refusal_for("/person/cache_000002_00.bphys", render_inputs);
}

TEST_F(AngeloWritersTest, TaskOfAJobBegunInsideIsThatJobs)
{
  fake_inside = true;
  const OwnJob job; /* A script's own bake: the depsgraph's tasks write for it. */
  fake_inside = false;
  const char *seen = nullptr;
  BLI_threadapi_init();
  TaskPool *pool = BLI_task_pool_create(nullptr, TASK_PRIORITY_HIGH);
  BLI_task_pool_push(pool, task_writes, &seen, false, nullptr);
  BLI_task_pool_work_and_wait(pool);
  BLI_task_pool_free(pool);
  BLI_threadapi_exit();
  EXPECT_EQ(seen, REFUSED);
}

TEST_F(AngeloWritersTest, TaskOfThePersonsJobWritesWhileAnAgentJobRuns)
{
  fake_inside = true;
  const HeldJob agents; /* An agent's job, elsewhere. */
  fake_inside = false;
  const OwnJob persons; /* The person's own bake on this thread. */
  fake_regions = 1;
  const char *seen = REFUSED;
  BLI_threadapi_init();
  TaskPool *pool = BLI_task_pool_create(nullptr, TASK_PRIORITY_HIGH);
  BLI_task_pool_push(pool, task_writes, &seen, false, nullptr);
  BLI_task_pool_work_and_wait(pool);
  BLI_task_pool_free(pool);
  BLI_threadapi_exit();
  EXPECT_EQ(seen, nullptr);
}

TEST_F(AngeloWritersTest, TaskWithNoJobRunsWithNoneOnAThreadThatHasOne)
{
  /* The re-read of 0a0d4e95743, SHOULD-8: a task pushed by a thread with no job runs with none, even on a thread
   * that writes for a job begun inside a region (a waiting thread runs the pool's tasks: work stealing). */
  const char *seen[64];
  for (const char *&slot : seen) {
    slot = REFUSED;
  }
  BLI_threadapi_init();
  TaskPool *pool = BLI_task_pool_create_suspended(nullptr, TASK_PRIORITY_HIGH);
  std::thread pusher([&] {
    for (const char *&slot : seen) {
      BLI_task_pool_push(pool, task_writes, &slot, false, nullptr);
    }
  });
  pusher.join();
  {
    fake_inside = true;
    const OwnJob agents; /* The waiting thread writes for an agent's job. */
    fake_inside = false;
    BLI_task_pool_work_and_wait(pool); /* Runs the tasks, some (all without threads) on this thread. */
  }
  BLI_task_pool_free(pool);
  BLI_threadapi_exit();
  int refused = 0;
  for (const char *slot : seen) {
    refused += slot != nullptr;
  }
  EXPECT_EQ(refused, 0);
}

TEST_F(AngeloWritersTest, UnknownJobJudgedByInputsAlone)
{
  fake_inside = true;
  const HeldJob agents; /* An agent's job runs; this thread knows no job. */
  fake_inside = false;
  const char *untagged = REFUSED, *tagged = nullptr;
  std::thread worker([&] {
    untagged = refusal_for("/person/cache_000002_00.bphys", render_inputs);
    fake_set.insert({&scene_id, "RenderSettings/filepath"});
    tagged = refusal_for("/person/cache_000002_00.bphys", render_inputs);
  });
  worker.join();
  EXPECT_EQ(untagged, nullptr);
  EXPECT_EQ(tagged, REFUSED);
}

TEST_F(AngeloWritersTest, FoldersUseTheFolderJudge)
{
  const Input inputs[] = {{&object_id, nullptr, "FluidDomainSettings", "cache_directory"}};
  fake_set.insert({&object_id, "FluidDomainSettings/cache_directory"});
  EXPECT_EQ(refusal_folder("/own", inputs), nullptr);
  EXPECT_EQ(refusal_folder("/own/frame.png", inputs), REFUSED); /* The file judge is never asked for a folder. */
}

TEST_F(AngeloWritersTest, OneLookupPerInput)
{
  const OwnJob job;
  refusal_for("/person/renders/frame_0001.png", render_inputs);
  EXPECT_EQ(lookups, 2);
}

}  // namespace blender::angelo_writers::tests
