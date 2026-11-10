/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <thread>

#include "testing/testing.h"

#include "BKE_gtest_base.hh"
#include "angelo_recovery_observation.hh"

namespace blender::ed::undo::recovery::tests {

class AngeloRecoveryObservationTest : public bke::BlenderGTestBase {};

static Binding binding()
{
  return {BLI_session_uid_generate(), BLI_session_uid_generate(), BLI_session_uid_generate()};
}

TEST_F(AngeloRecoveryObservationTest, ScopeCloseAndRequestHandleLossDoNotEraseUncertainty)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  ASSERT_NE(handle, nullptr);
  ASSERT_TRUE(registry.mark_effects_possible(handle));
  EXPECT_TRUE(registry.note_capture_closed(handle));
  handle.reset();
  const std::optional<Status> observed = registry.status_for_document(input.document);
  ASSERT_TRUE(observed.has_value());
  EXPECT_EQ(observed->effect, EffectState::RecoveryRequired);
  EXPECT_FALSE(observed->capture_open);
  EXPECT_FALSE(observed->effect_certified);
  EXPECT_FALSE(observed->recovery_proven);
  EXPECT_EQ(registry.size(), 1);
}

TEST_F(AngeloRecoveryObservationTest, SixteenOwnersAreAdmittedAndSeventeenthCannotEvict)
{
  Registry registry;
  const Binding first = binding();
  Handle first_handle = registry.prepare(first);
  ASSERT_TRUE(registry.mark_effects_possible(first_handle));
  for (int i = 1; i < Registry::capacity; i++) {
    Handle handle = registry.prepare(binding());
    ASSERT_NE(handle, nullptr);
    ASSERT_TRUE(registry.mark_effects_possible(handle));
  }
  EXPECT_EQ(registry.size(), 16);
  PrepareFailure failure = PrepareFailure::None;
  EXPECT_EQ(registry.prepare(binding(), &failure), nullptr);
  EXPECT_EQ(failure, PrepareFailure::Capacity);
  EXPECT_STRNE(prepare_failure_message(failure), "");
  EXPECT_EQ(registry.size(), 16);
  ASSERT_TRUE(registry.status(first_handle).has_value());
  EXPECT_EQ(registry.status(first_handle)->effect, EffectState::RecoveryRequired);
}

TEST_F(AngeloRecoveryObservationTest, DuplicateDocumentCannotAcquireAnotherOwnerAfterDetach)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  ASSERT_TRUE(registry.mark_effects_possible(handle));
  registry.note_document_detached(input.document);
  const Binding another{input.document, BLI_session_uid_generate(), BLI_session_uid_generate()};
  EXPECT_EQ(registry.prepare(another), nullptr);
  ASSERT_TRUE(registry.status(handle).has_value());
  EXPECT_EQ(registry.status(handle)->attachment, Attachment::Detached);
  EXPECT_EQ(registry.status(handle)->effect, EffectState::RecoveryRequired);
  EXPECT_FALSE(registry.cancel_before_effects(handle));
}

TEST_F(AngeloRecoveryObservationTest, DetachedPreparationCannotArmOrBindHistory)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  ASSERT_NE(handle, nullptr);
  registry.note_document_detached(input.document);
  EXPECT_FALSE(registry.mark_effects_possible(handle));
  EXPECT_FALSE(registry.bind_history(handle, input.preimage, BLI_session_uid_generate()));
  ASSERT_TRUE(registry.status(handle).has_value());
  EXPECT_EQ(registry.status(handle)->effect, EffectState::Prepared);
  EXPECT_EQ(registry.status(handle)->attachment, Attachment::Detached);
  EXPECT_FALSE(registry.status(handle)->effect_certified);
}

TEST_F(AngeloRecoveryObservationTest, PruningCannotCertifyRecoveryOrReleaseAnArmedOwner)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  const SessionUID afterimage = BLI_session_uid_generate();
  ASSERT_TRUE(registry.bind_history(handle, input.preimage, afterimage));
  ASSERT_TRUE(registry.mark_effects_possible(handle));
  registry.note_step_freed(input.preimage);
  registry.note_step_freed(afterimage);
  ASSERT_TRUE(registry.status(handle).has_value());
  EXPECT_EQ(registry.status(handle)->effect, EffectState::TargetUnavailable);
  EXPECT_FALSE(registry.status(handle)->preimage_history_alive);
  EXPECT_FALSE(registry.status(handle)->afterimage_history_alive);
  EXPECT_FALSE(registry.status(handle)->effect_certified);
  EXPECT_FALSE(registry.status(handle)->recovery_proven);
  EXPECT_FALSE(registry.cancel_before_effects(handle));
  EXPECT_EQ(registry.size(), 1);
}

TEST_F(AngeloRecoveryObservationTest, StaleHistoryIdentityNeverMatchesAReplacement)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  const SessionUID afterimage = BLI_session_uid_generate();
  ASSERT_TRUE(registry.bind_history(handle, input.preimage, afterimage));
  ASSERT_TRUE(registry.mark_effects_possible(handle));
  registry.note_step_freed(BLI_session_uid_generate());
  ASSERT_TRUE(registry.status(handle).has_value());
  EXPECT_TRUE(registry.status(handle)->preimage_history_alive);
  registry.note_step_freed(input.preimage);
  EXPECT_FALSE(registry.status(handle)->preimage_history_alive);
  EXPECT_FALSE(
      registry.bind_history(handle, BLI_session_uid_generate(), BLI_session_uid_generate()));
  EXPECT_EQ(registry.status(handle)->effect, EffectState::TargetUnavailable);
}

TEST_F(AngeloRecoveryObservationTest, OnlyPreparedOwnerMayBeCancelledBeforeEffects)
{
  Registry registry;
  Handle handle = registry.prepare(binding());
  ASSERT_NE(handle, nullptr);
  EXPECT_TRUE(registry.cancel_before_effects(handle));
  EXPECT_FALSE(registry.status(handle).has_value());
  EXPECT_FALSE(registry.mark_effects_possible(handle));
  EXPECT_EQ(registry.size(), 0);
}

TEST_F(AngeloRecoveryObservationTest, ClosedCaptureCannotArmOrChangeHistory)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  ASSERT_TRUE(registry.note_capture_closed(handle));
  EXPECT_FALSE(registry.mark_effects_possible(handle));
  EXPECT_FALSE(registry.bind_history(handle, input.preimage, BLI_session_uid_generate()));
  EXPECT_TRUE(registry.cancel_before_effects(handle));
}

TEST_F(AngeloRecoveryObservationTest, MissingIdentitiesForeignHandlesAndWorkerCallsRefuse)
{
  Registry registry, foreign;
  const Binding input = binding();
  EXPECT_EQ(registry.prepare({{}, input.invocation, input.preimage}), nullptr);
  Handle handle = registry.prepare(input);
  EXPECT_FALSE(foreign.status(handle).has_value());
  EXPECT_FALSE(foreign.mark_effects_possible(handle));
  EXPECT_FALSE(foreign.cancel_before_effects(handle));
  bool worker_prepared = true, worker_read = true, worker_armed = true;
  std::thread worker([&]() {
    worker_prepared = bool(registry.prepare(binding()));
    worker_read = registry.status(handle).has_value();
    worker_armed = registry.mark_effects_possible(handle);
  });
  worker.join();
  EXPECT_FALSE(worker_prepared);
  EXPECT_FALSE(worker_read);
  EXPECT_FALSE(worker_armed);
  EXPECT_EQ(registry.size(), 1);
}

TEST_F(AngeloRecoveryObservationTest, HistoryBindingIsNotAProofCapability)
{
  Registry registry;
  const Binding input = binding();
  Handle handle = registry.prepare(input);
  EXPECT_FALSE(
      registry.bind_history(handle, BLI_session_uid_generate(), BLI_session_uid_generate()));
  EXPECT_FALSE(registry.bind_history(handle, input.preimage, input.preimage));
  EXPECT_TRUE(registry.bind_history(handle, input.preimage, BLI_session_uid_generate()));
  EXPECT_FALSE(registry.bind_history(handle, input.preimage, BLI_session_uid_generate()));
  EXPECT_FALSE(Capabilities::identity_binding_ready);
  EXPECT_FALSE(Capabilities::exact_preimage_ready);
  EXPECT_FALSE(Capabilities::checked_restore_ready);
  EXPECT_FALSE(Capabilities::terminal_effect_seal_ready);
  ASSERT_TRUE(registry.status(handle).has_value());
  EXPECT_FALSE(registry.status(handle)->effect_certified);
  EXPECT_FALSE(registry.status(handle)->recovery_proven);
}

TEST_F(AngeloRecoveryObservationTest, ObservationOwnsTheOriginalImmutableBinding)
{
  Registry registry;
  Binding input = binding();
  const SessionUID original_document = input.document;
  Handle handle = registry.prepare(input);
  input.document = BLI_session_uid_generate();
  input.invocation = BLI_session_uid_generate();
  input.preimage = BLI_session_uid_generate();
  EXPECT_TRUE(registry.status_for_document(original_document).has_value());
  EXPECT_FALSE(registry.status_for_document(input.document).has_value());
  EXPECT_TRUE(registry.mark_effects_possible(handle));
  EXPECT_FALSE(registry.status(handle)->effect_certified);
}

}  // namespace blender::ed::undo::recovery::tests
