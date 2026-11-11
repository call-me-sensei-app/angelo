# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every format row of Fab's Download dialog becomes a choice; the table, not the listing, decides which.

Rows are the shapes the dialog showed on 2026-11-11 (signed in, after the listing's EULA): one file per format
straight under "Included formats" (a chair pack: Blender with an icon-only badge, FBX, OBJ and GLTF, two of them
both named source.zip), per-format groups with qualities (a Megascans rug: FBX zips, Converted Files glb/gltf/usdz),
and a texture set without qualities. Synthetic titles and identifiers.
"""
import suite_isolation  # noqa: F401
import team_library_fixtures  # noqa: F401  Puts the add-on on the path.
import unittest

from angelo_agent import fab_browser_listing_page as page
from angelo_agent.fab_browser_listing_owner import listing_from_page
from angelo_agent.fab_browser_variants import BrowserChoice
from angelo_agent.fab_catalog import FabError
from angelo_agent import fab_host_state

ID = '12345678-1234-1234-1234-123456789abc'
QUALITY = {'low': ('Low quality', 'This product is of _low. quality'),
           'high': ('High quality', 'This product is of _high. quality')}


def row(ordinal, filename, group, badge, size='1.49 MB', quality=None, disabled=False):
    label, aria = QUALITY[quality] if quality else (None, None)
    return {'ordinal': ordinal, 'filename': filename, 'group': group, 'format': badge, 'quality_label': label,
            'quality_aria': aria, 'display_size': size, 'button_type': 'button',
            'button_aria': 'Download %s asset' % group, 'button_disabled': disabled}


def observed(*rows, library='Saved in My Library'):
    return {'state': 'observed', 'listing_id': ID, 'title': 'Synthetic Chair Pack', 'author': 'Synthetic Studio',
            'license_label': 'Standard License', 'library_state_label': library, 'rows': list(rows)}


CHAIR_PACK = (row(0, 'chairs_pack.blend', 'Blender', ''), row(1, 'chairs_pack.fbx', 'FBX', 'fbx'),
              row(2, 'source.zip', 'OBJ', 'obj', '7.14 MB'), row(3, 'source.zip', 'GLTF', 'gltf', '3.73 MB'))
RUG = (row(0, 'rug_low.zip', 'FBX', 'fbx', '8.41 MB', 'low'), row(1, 'rug_low.glb', 'Converted Files', 'glb', '3.89 MB', 'low'),
       row(2, 'rug_gltf_low.zip', 'Converted Files', 'gltf', '3.89 MB', 'low'),
       row(3, 'rug_low.usdz', 'Converted Files', 'usdz', '2.50 MB', 'low'))
SURFACE = (row(0, 'rock_wall_1k.zip', 'Texture Set', 'texture set', '6.58 MB'),
           row(1, 'rock_wall_1k_ue_low.zip', 'GLTF', 'gltf', '2.01 MB', 'low'))


class ObservationTests(unittest.TestCase):
    def test_each_format_row_is_a_choice_of_its_table_format(self):
        result = page.parse_listing_observation(observed(*CHAIR_PACK), expected_listing_id=ID)
        self.assertEqual([c.format for c in result.choices], ['blend', 'fbx', 'obj', 'gltf'])
        self.assertEqual([c.group for c in result.choices], ['Blender', 'FBX', 'OBJ', 'GLTF'])
        self.assertEqual(len({c.control_fingerprint for c in result.choices}), 4)   # Two source.zip, two rows.

    def test_converted_files_say_their_format_in_the_badge(self):
        result = page.parse_listing_observation(observed(*RUG), expected_listing_id=ID)
        self.assertEqual([c.format for c in result.choices], ['fbx', 'glb', 'gltf', 'usd'])
        self.assertEqual(result.choices[1].quality_label, 'Low quality')

    def test_a_button_that_names_another_group_or_a_repeated_row_is_refused(self):
        bad = dict(CHAIR_PACK[1], button_aria='Download Converted Files asset')
        with self.assertRaises(page.ListingObservationUnavailable):
            page.parse_listing_observation(observed(bad), expected_listing_id=ID)
        with self.assertRaises(page.ListingObservationUnavailable):
            page.parse_listing_observation(observed(CHAIR_PACK[1], dict(CHAIR_PACK[1], ordinal=5)),
                                           expected_listing_id=ID)
        with self.assertRaises(page.ListingObservationUnavailable):
            page.parse_listing_observation(observed(row(0, 'a.uasset', 'Unreal Engine', 'unreal engine')),
                                           expected_listing_id=ID)

    def test_the_page_script_lists_exactly_the_table_labels(self):
        self.assertIn('const SUPPORTED=[', page.OBSERVE_LISTING_JS)
        for label in ('fbx', 'blender', 'texture set', 'glb', 'usdz'):
            self.assertIn('"%s"' % label, page.OBSERVE_LISTING_JS)
        self.assertNotIn('SUPPORTED_LABELS', page.OBSERVE_LISTING_JS)


class ChoiceTests(unittest.TestCase):
    def test_choices_carry_format_and_kind(self):
        listing = listing_from_page(page.parse_listing_observation(observed(*SURFACE), expected_listing_id=ID))
        item = listing.item()
        self.assertEqual([v.format for v in item.variants], ['surface', 'gltf'])
        self.assertEqual([v.kind for v in item.variants], ['material', 'model'])
        self.assertEqual(item.kind, 'material')   # A texture set marks a surface or decal listing.
        compact = item.compact()
        self.assertEqual(compact['variants'][0]['format'], 'surface')
        self.assertEqual(compact['variants'][0]['kind'], 'material')

    def test_a_texture_set_only_listing_is_a_material_and_a_model_listing_a_model(self):
        listing = listing_from_page(page.parse_listing_observation(observed(SURFACE[0]), expected_listing_id=ID))
        self.assertEqual(listing.item().kind, 'material')
        listing = listing_from_page(page.parse_listing_observation(observed(*RUG), expected_listing_id=ID))
        self.assertEqual(listing.item().kind, 'model')

    def test_a_surface_offers_its_texture_set_first(self):
        listing = listing_from_page(page.parse_listing_observation(observed(*SURFACE), expected_listing_id=ID))
        compact = dict(listing.item().compact(), access='owned')
        for variant, row in zip(listing.item().variants, compact['variants']):
            row['label'] = variant.browser_choice.filename   # As Session.lookup labels an observed choice.
        _selected, choices, _reason = fab_host_state.lookup(
            {'item': compact, 'download_permission': 'available', 'entitlement': 'download_available'})
        self.assertEqual([c['format'] for c in choices], ['surface', 'gltf'])

    def test_unknown_formats_never_become_choices(self):
        for fmt in ('unreal', 'max', '', 'GLB'):
            with self.subTest(fmt=fmt), self.assertRaises(FabError):
                BrowserChoice(ID, 'local', 'local', fmt, 'file.zip')

    def test_presented_choices_put_the_cheapest_path_first(self):
        listing = listing_from_page(page.parse_listing_observation(observed(*RUG), expected_listing_id=ID))
        compact = listing.item().compact()
        result = {'item': compact, 'download_permission': 'available', 'entitlement': 'download_available',
                  'import_ready': True}
        compact['access'] = 'owned'
        _selected, choices, _reason = fab_host_state.lookup(result)
        self.assertEqual([c['format'] for c in choices], ['glb', 'fbx', 'gltf', 'usd'])
        self.assertEqual([c['kind'] for c in choices], ['model'] * 4)


if __name__ == '__main__':
    unittest.main()
