/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <cmath>
#include <limits>

#include "testing/testing.h"

#include "BKE_angelo_fcurve_digest.hh"
#include "BKE_fcurve.hh"
#include "BKE_gtest_base.hh"

#include "ANIM_fcurve.hh"

#include "DNA_anim_enums.h" /* SELECT */
#include "DNA_anim_types.h"
#include "DNA_curve_enums.h"
#include "DNA_curve_types.h"

namespace blender::bke::tests {
using namespace blender::animrig;

class AngeloFCurveDigestTest : public BlenderGTestBase {
 protected:
  FCurve *keyed(const int count)
  {
    FCurve *fcu = BKE_fcurve_create();
    const KeyframeSettings settings = get_keyframe_settings(false);
    for (int i = 0; i < count; i++) {
      insert_vert_fcurve(fcu, {float(i + 1), float(i) * 0.5f}, settings, INSERTKEY_NOFLAGS);
    }
    return fcu;
  }

  static std::string digest(const FCurve &fcu, const bool selection = false)
  {
    return BKE_angelo_fcurve_keys_digest(fcu, selection).value_or("non-finite");
  }
};

TEST_F(AngeloFCurveDigestTest, EveryKeyFieldChangesIt)
{
  FCurve *fcu = this->keyed(3);
  const std::string base = digest(*fcu);
  EXPECT_EQ(base.size(), 32);
  EXPECT_EQ(digest(*fcu), base); /* Equal states, equal digests within the process. */
  BezTriple &bezt = fcu->bezt[1];
  float *floats[] = {&bezt.vec[1][0],
                     &bezt.vec[1][1],
                     &bezt.vec[0][0],
                     &bezt.vec[0][1],
                     &bezt.vec[2][0],
                     &bezt.vec[2][1],
                     &bezt.back,
                     &bezt.amplitude,
                     &bezt.period};
  for (float *value : floats) {
    const float old = *value;
    *value += 0.25f;
    EXPECT_NE(digest(*fcu), base);
    *value = old;
  }
  char *chars[] = {reinterpret_cast<char *>(&bezt.ipo),
                   reinterpret_cast<char *>(&bezt.easing),
                   &bezt.hide,
                   reinterpret_cast<char *>(&bezt.h1),
                   reinterpret_cast<char *>(&bezt.h2)};
  for (char *value : chars) {
    const char old = *value;
    *value = char(old + 1);
    EXPECT_NE(digest(*fcu), base);
    *value = old;
  }
  EXPECT_EQ(digest(*fcu), base);
  BKE_fcurve_free(fcu);
}

TEST_F(AngeloFCurveDigestTest, SelectBitsOnlyAndOnlyWhenAsked)
{
  FCurve *fcu = this->keyed(2);
  const std::string base = digest(*fcu);
  const std::string with_selection = digest(*fcu, true);
  EXPECT_NE(base, with_selection);
  /* Editors' temporary tag bits share the bytes and must never change either digest. */
  fcu->bezt[0].f1 |= BEZT_FLAG_TEMP_TAG;
  fcu->bezt[0].f2 |= BEZT_FLAG_IGNORE_TAG;
  EXPECT_EQ(digest(*fcu), base);
  EXPECT_EQ(digest(*fcu, true), with_selection);
  for (eBezTriple_Flag *flag : {&fcu->bezt[1].f1, &fcu->bezt[1].f2, &fcu->bezt[1].f3}) {
    const eBezTriple_Flag old = *flag;
    *flag = eBezTriple_Flag(*flag ^ SELECT);
    EXPECT_EQ(digest(*fcu), base);
    EXPECT_NE(digest(*fcu, true), with_selection);
    *flag = old;
  }
  BKE_fcurve_free(fcu);
}

TEST_F(AngeloFCurveDigestTest, CountsSamplesAndNonFinite)
{
  FCurve *one = this->keyed(1);
  FCurve *two = this->keyed(2);
  EXPECT_NE(digest(*one), digest(*two));
  FCurve *sampled = this->keyed(4);
  fcurve_store_samples(sampled, nullptr, 1, 4, fcurve_samplingcb_evalcurve);
  ASSERT_NE(sampled->fpt, nullptr);
  const std::string samples = digest(*sampled);
  sampled->fpt[2].vec[1] += 1.0f;
  EXPECT_NE(digest(*sampled), samples);
  sampled->fpt[2].vec[1] = std::numeric_limits<float>::infinity();
  EXPECT_FALSE(BKE_angelo_fcurve_keys_digest(*sampled, false).has_value());
  two->bezt[1].period = std::nanf("");
  EXPECT_FALSE(BKE_angelo_fcurve_keys_digest(*two, false).has_value());
  BKE_fcurve_free(one);
  BKE_fcurve_free(two);
  BKE_fcurve_free(sampled);
}

}  // namespace blender::bke::tests
