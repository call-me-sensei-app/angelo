# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of how an answer line is read: what each form accepts exactly, what it accepts leniently, what not.

A form is not stricter than it needs to be, and no looser: every liberty a
reading takes is named, so reports can count how often a model needed one, and
a value that does not plainly state the answer is no answer at all.
``test_native_eval_answers.py`` covers the forms' exact spellings, the
languages and the evidence.
"""

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_answer_forms as forms  # noqa: E402
import eval_answers as answers  # noqa: E402

COUNT, AND, COUNT_AFTER = "a count before the names", "names joined by 'and'", "a count after the names"
OWN_LINE, UNLABELLED, COMMAS, UNITS = ("the location on its own line", "the location not labelled",
                                       "decimal commas", "units or punctuation among the numbers")
CLAUSE, WORDS, AROUND = ("a clause after the answer", "words or punctuation among the numbers",
                         "words or punctuation around the engine and the size")
MORE, FILLER = "more after the shortcut", "words around the shortcut"
OTHER_PLATFORM, NAMED = "the shortcut for the other platform, named second", "numbers named, read by their names"
BRACKETS, PUNCTUATION = "brackets or backticks in the answer", "punctuation after the answer"
BOX = [1.0, 2.0, 1.0, -2.0, -1.0, 0.0]


def said(text):
    return [{"role": "assistant", "text": text}]


class ReadingTests(unittest.TestCase):
    """``readings(form, value)``: the exact reading first (no notes), then a lenient one that names its liberties."""

    def assertReads(self, form, cases):
        for value, expected in cases.items():
            with self.subTest(form=form, value=value):
                self.assertEqual(forms.readings(form, value), expected)

    def test_mesh_names_exactly(self):
        self.assertReads("mesh_names", {
            "Cube": [(["Cube"], [])], "Cube, Brass Marker": [(["Brass Marker", "Cube"], [])],
            "`Cube`; 'Brass Marker'": [(["Brass Marker", "Cube"], [])],
            "Cube.001, Cube": [(["Cube", "Cube.001"], [])], "": []})

    def test_mesh_names_leniently(self):
        both = ["Brass Marker", "Cube"]
        for value, notes in {"2: Cube, Brass Marker": [COUNT], "2 - Cube, Brass Marker": [COUNT],
                             "2 mesh objects: Cube, Brass Marker": [COUNT], "2 (Cube, Brass Marker)": [COUNT],
                             "Cube and Brass Marker": [AND], "Cube & Brass Marker": [AND],
                             "Cube, and Brass Marker": [AND], "Cube, Brass Marker (two meshes)": [COUNT_AFTER],
                             "Cube, Brass Marker (2)": [COUNT_AFTER], "Cube, Brass Marker (both)": [COUNT_AFTER],
                             "2, Cube, Brass Marker": [COUNT], "2; Cube, Brass Marker": [COUNT],
                             "Cube, Brass Marker \u2014 2 total": [COUNT_AFTER], "Cube, Brass Marker, 2": [COUNT_AFTER],
                             "There are 2 mesh objects: Cube, Brass Marker": [COUNT],
                             "all: Cube, Brass Marker": [COUNT], "<Cube>, <Brass Marker>": [BRACKETS],
                             "[Cube, Brass Marker]": [BRACKETS],
                             "Two meshes: Cube and Brass Marker (2 in total)": [COUNT, COUNT_AFTER, AND]}.items():
            with self.subTest(value=value):
                read = forms.readings("mesh_names", value)
                self.assertEqual(read[-1], (both, notes))
                self.assertEqual((len(read), read[0][1]), (2, []), "the value as written is read first")
                self.assertNotEqual(read[0][0], both)

    def test_a_liberty_never_changes_which_names_were_given(self):
        for value in ("Cube", "2", "none", "Cube, Light", "I cannot see any mesh"):
            with self.subTest(value=value):
                self.assertEqual(len(forms.readings("mesh_names", value)), 1, "nothing to take off")
        # Only a count is dropped: any other remark or sentence could hold one more name, so it stays in.
        both = ["Brass Marker", "Cube"]
        for value in ("Cube, Brass Marker (and Suzanne)", "Cube, Brass Marker. Suzanne too",
                      "Cube, Brass Marker (Suzanne)", "Cube, Brass Marker (the cube and a plate)",
                      "Cube, Brass Marker; Suzanne", "2 (Cube, Brass Marker) and Suzanne",
                      "Cube, Brass Marker (2, and Suzanne)", "Suzanne: Cube, Brass Marker",
                      "2 Suzanne: Cube, Brass Marker", "Cube, Brass Marker (2 plus Suzanne)",
                      "Cube, Brass Marker (plus Camera and Light)", "Cube, Brass Marker. Also Plane, Sphere and Torus.",
                      "Cube, Brass Marker. Actually only Cube is a mesh.",
                      # A count is dropped only when it agrees with the number of names given.
                      "5: Cube, Brass Marker", "Cube, Brass Marker (3)", "3, Cube, Brass Marker",
                      "Cube, Brass Marker, 3 in total", "1 mesh: Cube, Brass Marker"):
            with self.subTest(value=value):
                self.assertNotIn(both, [answer for answer, _ in forms.readings("mesh_names", value)])
        # A name that itself holds an "and" or a bracket is still right as written: the exact reading comes first.
        self.assertEqual(forms.readings("mesh_names", "Salt and Pepper, Cube (old)")[0],
                         (["Cube (old)", "Salt and Pepper"], []))

    def test_a_box_exactly(self):
        for value in ("1 2 1; LOCATION: -2 -1 0", "1 2 1 ; location : -2 -1 0", "1.0 2.00 1; LOCATION: -2.0 -1 0.0",
                      "+1 2 1; LOCATION: −2 −1 0", "1 2 1; LOCATION: -2 -1 .0"):
            with self.subTest(value=value):
                self.assertEqual(forms.readings("box", value), [(BOX, [])])

    def test_a_box_leniently(self):
        for value, notes in {"1 m x 2 m x 1 m; LOCATION: (-2, -1, 0)": [UNITS], "1, 2, 1; LOCATION: -2, -1, 0": [UNITS],
                             "X=1 Y=2 Z=1; LOCATION: X=-2 Y=-1 Z=0": [UNITS], "1 2 1, LOCATION -2 -1 0": [UNITS],
                             "1 2 1\nLOCATION: -2 -1 0": [OWN_LINE], "1 2 1 at -2 -1 0": [UNLABELLED],
                             "1,0 2,0 1,0; LOCATION: -2,0 -1,0 0,0": [COMMAS],
                             "1,0 m 2,0 m 1,0 m\nLOCATION: -2,0 -1,0 0,0": [OWN_LINE, COMMAS],
                             "1m x 2m x 1m; LOCATION: -2 -1 0": [UNITS], "1x2x1; LOCATION: -2 -1 0": [UNITS],
                             "1 2 1; LOCATION: -2 -1 0 in the 3D viewport": [UNITS],
                             "1 2 1; LOCATION: -2 -1 0 (unchanged since step 3)": ["a remark after the numbers"],
                             "<1> <2> <1>; LOCATION: <-2> <-1> <0>": [UNITS]}.items():
            with self.subTest(value=value):
                self.assertEqual(forms.readings("box", value)[0], (BOX, notes))
        self.assertEqual(forms.readings("box", "1 2 1; LOCATION: -2 -1 1e-7"), [(BOX[:5] + [1e-07], [])])

    def test_what_is_not_a_box(self):
        for value in ("1 2 1", "1 2; LOCATION: -2 -1 0", "1 2 1; LOCATION: -2 -1", "unknown", "",
                      "1 2; LOCATION: 1 -2 -1 0", "1 2 1 -2; LOCATION: -1 0",   # Six numbers, not three and three.
                      "1 2 1; LOCATION: -2 -1 0 or 2 2 2", "about 1 by 2; LOCATION: near the origin",
                      "1 2 1 2 1; LOCATION: -2 -1 0", "LOCATION: -2 -1 0",
                      "1 2 1\nLOCATION: -2 -1 0\nLOCATION: 0 0 0",   # Two locations are two answers.
                      "1 2 1; LOCATION: -2 -1 0\nLOCATION: 9 9 9", "1 2 1; LOCATION: -2 -1 0 (or 0 0 0)"):
            with self.subTest(value=value):
                self.assertEqual(forms.readings("box", value), [])
                self.assertIsNone(forms.parse("box", value))

    def test_a_box_matches_within_half_a_centimetre_and_names_match_exactly(self):
        self.assertEqual(forms.BOX_TOLERANCE, 0.005)
        near = [1.005, 1.995, 1.0, -2.005, -1.0, 0.004]
        self.assertTrue(forms.matches("box", near, [BOX]) and forms.matches("box", BOX, [[9.0] * 6, BOX]))
        for index in range(6):
            off = list(BOX)
            off[index] += 0.006
            self.assertFalse(forms.matches("box", off, [BOX]), index)
        self.assertFalse(forms.matches("box", BOX[:3] + BOX[:3], [BOX]) or forms.matches("box", BOX, []))
        self.assertTrue(forms.matches("mesh_names", ["Cube"], [["Cube"]]))
        self.assertFalse(forms.matches("mesh_names", ["cube"], [["Cube"]]) or forms.matches("yes_no", "yes", ["no"]))


class SurveyFormTests(unittest.TestCase):
    """The request survey's forms: the same rule. Exact as asked, lenient where the answer is plain, else nothing."""

    def assertReads(self, form, cases):
        for value, expected in cases.items():
            with self.subTest(form=form, value=value):
                self.assertEqual(forms.readings(form, value), expected)

    def test_numeric_forms_do_not_extract_a_right_answer_from_a_denial_or_an_alternative(self):
        cases = {
            "box": ([BOX], [
                "not 1 2 1; LOCATION: not -2 -1 0",
                "1 2 1; LOCATION: -2 -1 0, I cannot verify this",
                "1 2 1; LOCATION: -2 -1 0 (actually x is 9)",
                "1 cm x 2 cm x 1 cm; LOCATION: -2 -1 0",
                "1 2 1; LOCATION: -2 -1 0 (or 0 0 0)",
                "不是 1 2 1; LOCATION: -2 -1 0"]),
            "counts": ([[12, 20, 10]], [
                "not 12 20 10", "12 20 10, I do not know",
                "12 vertices, 20 edges, 10 faces or 99",
                "12 20 10 (or 14 22 12)", "12 20 10 (faces are 99)",
                "-12 -20 -10", "12 20 10 ではありません"]),
            "render": ([["EEVEE", 1600, 900]], [
                "EEVEE 1600x900, actually 1920x1080",
                "EEVEE 1600x900 (or 1920x1080)", "EEVEE 1600x900 or 1920",
                "EEVEE 1600x900, I cannot see the settings",
                "EEVEE 1600x900, width is actually 1920", "EEVEE 1600x900 ではありません",
                "EEVEE -1600x900", "EEVEE .1600x900", "EEVEE 1600x900.5", "EEVEE 1600x-900"]),
        }
        for form, (accept, values) in cases.items():
            for value in values:
                with self.subTest(form=form, value=value):
                    reply = forms.FORMS[form][0] + ": " + value
                    graded = answers.grade({"form": form}, accept, said(reply))
                    self.assertEqual(graded["outcome"], "no_answer")

    def test_numeric_forms_keep_exact_wrong_and_documented_lenient_answers_distinct(self):
        for form, accept, right, wrong, lenient in (
                ("box", [BOX], "1 2 1; LOCATION: -2 -1 0", "9 2 1; LOCATION: -2 -1 0",
                 "1 2 1; LOCATION: -2 -1 0 (unchanged since step 3)"),
                ("counts", [[12, 20, 10]], "12 20 10", "12 20 11", "12 20 10 (after 2 extrusions)"),
                ("render", [["EEVEE", 1600, 900]], "EEVEE 1600x900", "EEVEE 1600x901",
                 "engine: EEVEE; resolution: 1600x900 (100%)")):
            for value, outcome, parsed in ((right, "right", answers.EXACTLY),
                                           (wrong, "wrong", answers.EXACTLY),
                                           (lenient, "right", answers.LENIENTLY)):
                with self.subTest(form=form, value=value):
                    graded = answers.grade({"form": form}, accept, said(forms.FORMS[form][0] + ": " + value))
                    self.assertEqual((graded["outcome"], graded["parsed"]), (outcome, parsed))

    def test_bounded_numeric_wrappers_preserve_harmless_separator_punctuation(self):
        for value in ("12 vertices, 20 edges & 10 faces", "12 vertices — 20 edges — 10 faces"):
            self.assertEqual(forms.parse("counts", value), [12, 20, 10])
        for value in ('"EEVEE" 1600x900', "Eevee — 1600x900", "EEVEE 1600x900 at 100%"):
            self.assertEqual(forms.parse("render", value), ["EEVEE", 1600, 900])

    def test_yes_or_no_then_a_short_clause(self):
        self.assertReads("yes_no", {value: [(answer, [])] for value, answer in {
            "yes": "yes", "No.": "no", "YES!": "yes", "nein": "no"}.items()})
        self.assertReads("yes_no", {value: [(answer, [CLAUSE])] for value, answer in {
            "no, it is cut off": "no", "No \u2014 the top is cropped": "no", "Yes (fully in frame)": "yes",
            "no - cut off on the right": "no", "Yes. All of it is inside the frame.": "yes", "yes, probably": "yes",
            "no; part of it is outside": "no", "non, il est coup\u00e9": "no",
            "no, the right side cannot be seen": "no", "no, it is not fully in frame": "no",
            "\u041d\u0435\u0442, \u043e\u0431\u0440\u0435\u0437\u0430\u043d": "no",   # Russian: no, cut off.
            "\u306f\u3044\u3001\u30d5\u30ec\u30fc\u30e0\u5185\u3067\u3059": "yes",   # Japanese, an ideographic comma.
            "\u662f\u7684\uff0c\u5b8c\u5168\u5728\u753b\u9762\u5185": "yes"}.items()})   # Chinese, a full-width comma.
        self.assertReads("yes_no", {value: [(answer, [PUNCTUATION])] for value, answer in {
            "yes :)": "yes", "no :(": "no", "yes (!)": "yes", "yes \u2014 !": "yes"}.items()})
        self.assertReads("yes_no", {"<no>": [("no", [BRACKETS])], "[yes]": [("yes", [BRACKETS])]})

    def test_what_is_not_a_yes_or_a_no(self):
        for value in ("No idea", "No part of it is cut off", "no it is cut off", "yes it is", "no-idea", "nope",
                      "maybe, yes", "yes/no", "yes or no", "yes?", "yes, is it?", "n. a.", "y, sure",
                      # A clause that holds a yes or a no of its own, wherever it stands.
                      "no, yes", "yes, no doubt", "no, actually yes", "no (or yes, hard to tell)",
                      "no; the answer is yes", "yes, though it might be no", "yes. No part is cut off.",
                      "yes, no part of it is cropped",
                      # A clause that does not answer.
                      "no, I cannot see the scene", "no. I can\u2019t tell", "No, I could not look through the camera",
                      "no, unable to render a view", "yes, not sure though", "unknown, no",
                      "no, I do not have a camera view", "no - I don't have access to the viewport",
                      "no, I have not looked", "no, just a guess", "yes, but I did not look",
                      "no: " + "word " * 21, "no, " + "x" * 161):
            with self.subTest(value=value):
                self.assertEqual(forms.readings("yes_no", value), [])
        self.assertEqual((forms.CLAUSE_WORDS, forms.CLAUSE_CHARACTERS), (20, 160))
        self.assertEqual(forms.readings("yes_no", "no: " + "word " * 20), [("no", [CLAUSE])])

    def test_three_counts_among_words(self):
        self.assertReads("counts", {value: [([12, 20, 10], [])] for value in ("12 20 10", "12, 20, 10", "12/20/10")})
        self.assertReads("counts", {value: [([12, 20, 10], [WORDS])] for value in (
            "v=12 e=20 f=10", "12 20 10 (after 2 extrusions)", "about 12 20 10")})
        # Numbers that are named go by their names, whatever their order; unnamed ones by the form's order.
        self.assertReads("counts", {value: [([12, 20, 10], [NAMED])] for value in (
            "12 vertices, 20 edges, 10 faces", "vertices: 12; edges: 20; faces: 10",
            "20 edges, 12 vertices, 10 faces", "faces 10, verts 12, edges 20", "12 verts 20 edges 10 polygons")})
        self.assertReads("counts", {"12 faces, 20 edges, 10 vertices": [([10, 20, 12], [NAMED])],
                                    "1,234 2,468 1,234": [([1234, 2468, 1234], [])],
                                    "1,234 vertices, 2,468 edges and 1,234 faces": [([1234, 2468, 1234], [NAMED])]})
        for value in ("12 20", "12.5 20 10", "12 20 10 30", "unknown", "twelve, twenty and ten",
                      "12 20 10 or 14 22 12"):
            with self.subTest(value=value):
                self.assertEqual(forms.readings("counts", value), [])

    def test_object_names_read_as_mesh_names_do(self):
        three = ["Camera", "Cube", "Light"]
        self.assertEqual(forms.readings("names", "Cube, Camera, Light"), [(three, [])])
        self.assertEqual(forms.readings("names", "3: Cube, Camera and Light")[-1], (three, [COUNT, AND]))
        self.assertEqual(forms.readings("names", "Cube, Camera, Light (three objects)")[-1], (three, [COUNT_AFTER]))

    def test_an_engine_and_a_size_with_words_around_them(self):
        self.assertReads("render", {value: [(["EEVEE", 1600, 900], [])] for value in (
            "EEVEE 1600x900", "BLENDER_EEVEE_NEXT 1600x900", "Eevee Next 1600 x 900", "EEVEE 1,600X900")})
        self.assertReads("render", {value: [(["EEVEE", 1600, 900], [AROUND])] for value in (
            "EEVEE at 1600x900", "EEVEE 1600 by 900", "1600x900 EEVEE", "EEVEE 1600x900 px",
            "engine: EEVEE; resolution: 1600x900 (100%)", "Blender EEVEE, 1600 \u00d7 900")})
        for value in ("EEVEE or CYCLES 1600x900", "1600x900", "EEVEE", "EEVEE 1600", "1600 or 900 in EEVEE",
                      "not EEVEE 1600x900", "1600x900, no EEVEE"):
            with self.subTest(value=value):
                self.assertEqual(forms.readings("render", value), [])
        # An engine Blender does not ship is its words joined: an answer, and a wrong one for a scene on EEVEE.
        self.assertEqual(forms.readings("render", "Hydra Storm 1600x900"), [(["HYDRASTORM", 1600, 900], [AROUND])])

    def test_a_shortcut_and_what_may_surround_it(self):
        self.assertReads("shortcut", {value: [("ctrl+b", [])] for value in (
            "Ctrl+B", "ctrl + b", "Control-B", "\u2303B", "CTRL B")})
        self.assertReads("shortcut", {
            "Ctrl+B, then drag": [("ctrl+b", [MORE])], "Ctrl+B then drag": [("ctrl+b", [FILLER])],
            "press Ctrl+B": [("ctrl+b", [FILLER])], "the Ctrl+B key": [("ctrl+b", [FILLER])],
            "Ctrl+B on Windows and Linux": [("ctrl+b", [FILLER])],
            "<Ctrl+B>": [("ctrl+b", [BRACKETS])], "`Ctrl`+`B`": [("ctrl+b", [BRACKETS])],
            # The same keys for the other platform may be named second, and are then a reading too.
            "Ctrl+B (Cmd+B on a Mac)": [("ctrl+b", [MORE]), ("cmd+b", [OTHER_PLATFORM])],
            "Ctrl+B or Cmd+B": [("ctrl+b", [MORE]), ("cmd+b", [OTHER_PLATFORM])],
            "Cmd+B / Ctrl+B": [("cmd+b", [MORE]), ("ctrl+b", [OTHER_PLATFORM])],
            "Cmd+Shift+B (Ctrl+Shift+B on Windows)": [("cmd+shift+b", [MORE]), ("ctrl+shift+b", [OTHER_PLATFORM])]})
        # Any other second shortcut is a second guess: only the first is read.
        for value, first in {"Ctrl+A or Ctrl+B": "ctrl+a", "W or Ctrl+B": "w", "Ctrl+R / Ctrl+B": "ctrl+r",
                             "Ctrl+Shift+B / Ctrl+B": "ctrl+shift+b", "Tab, Ctrl+B": "tab",
                             "Ctrl+E or Ctrl+B or Ctrl+R": "ctrl+e", "Ctrl+B or Cmd+B or Alt+B": "ctrl+b"}.items():
            with self.subTest(value=value):
                self.assertEqual(forms.readings("shortcut", value), [(first, [MORE])])
        for value in ("Ctrl", "Ctrl+B then S", "Ctrl+B Ctrl+R", "", "I don't know", "the bevel tool", "B 2"):
            with self.subTest(value=value):
                self.assertEqual(forms.readings("shortcut", value), [])

    def test_the_other_platform_s_shortcut_named_second_may_be_the_right_one_and_says_so(self):
        graded = answers.grade({"form": "shortcut"}, ["ctrl+b"], said("SHORTCUT: Cmd+B (Ctrl+B on Windows)"))
        self.assertEqual((graded["outcome"], graded["answered"], graded["parsed"], graded["lenient"]),
                         ("right", "ctrl+b", answers.LENIENTLY, [OTHER_PLATFORM]))
        for guesses in ("SHORTCUT: Ctrl+E or Ctrl+B", "SHORTCUT: Ctrl+E or Ctrl+R", "SHORTCUT: Tab, Ctrl+B"):
            wrong = answers.grade({"form": "shortcut"}, ["cmd+b", "ctrl+b"], said(guesses))
            self.assertEqual((wrong["outcome"], wrong["parsed"]), ("wrong", answers.LENIENTLY), guesses)

    def test_a_refusal_after_a_no_is_not_read_as_no(self):
        spec, image = {"form": "yes_no", "evidence": answers.SCENE_CAMERA}, {
            "role": "activity", "tool": "angelo_view", "status": "completed",
            "result": {"views": [{"bytes": 9, "camera": {"source": answers.SCENE_CAMERA}}]}}
        refused = answers.grade(spec, ["no"], [image] + said("ANSWER: no, I cannot see the scene"))
        self.assertEqual((refused["outcome"], refused["answered"]), ("no_answer", None))
        delivered = {"edits": 0, "images_delivered": 1, "images": [[2, 0, {"view:SOLID:SCENE_CAMERA": 1}]],
                     "image_delivery": "confirmed"}
        clause = answers.grade(spec, ["no"], [image] + said("It is cropped.\nANSWER: no, it is cut off on the right"),
                               delivery=delivered)
        self.assertEqual((clause["outcome"], clause["parsed"], clause["lenient"], clause["stated"]),
                         ("right", answers.LENIENTLY, [CLAUSE], "no, it is cut off on the right"))
        blind = answers.grade(spec, ["no"], said("ANSWER: no, it is cut off"))
        self.assertEqual(blind["outcome"], "unsupported", "a clause is no observation")

    def test_every_form_reads_and_an_unknown_one_is_refused(self):
        for form in forms.FORMS:
            self.assertEqual(forms.readings(form, ""), [], form)
            self.assertIsNone(forms.parse(form, " "), form)
        with self.assertRaises(ValueError):
            forms.readings("essay", "anything")


class LineTests(unittest.TestCase):
    def test_a_location_on_its_own_line_continues_the_dimensions_line(self):
        self.assertEqual(forms.CONTINUATIONS, {"DIMENSIONS": "LOCATION"})
        lines = lambda text: forms.answer_lines(text, "DIMENSIONS")
        self.assertEqual(lines("Here.\nDIMENSIONS: 1 2 1\nLOCATION: -2 -1 0\n"), ["1 2 1\nLOCATION: -2 -1 0"])
        self.assertEqual(lines("**DIMENSIONS:** 1 2 1\n- **LOCATION:** -2 -1 0."), ["1 2 1\nLOCATION:** -2 -1 0"])
        self.assertEqual(lines("DIMENSIONS: 1 2 1; LOCATION: 0 0 0\nLOCATION: 9 9 9"),
                         ["1 2 1; LOCATION: 0 0 0\nLOCATION: 9 9 9"], "a second location is kept: two answers")
        self.assertEqual(lines("LOCATION: 9 9 9\nDIMENSIONS: 1 2 1"), ["1 2 1"], "only a line that follows")
        self.assertEqual(lines("DIMENSIONS: 3 3 3\nLOCATION: 1 1 1\nDIMENSIONS: 1 2 1\nLOCATION: -2 -1 0"),
                         ["3 3 3\nLOCATION: 1 1 1", "1 2 1\nLOCATION: -2 -1 0"])
        self.assertEqual(forms.answer_lines("MESHES: Cube\nLOCATION: 0 0 0", "MESHES"), ["Cube"])

    def test_the_line_is_found_in_quotes_and_after_a_sentence_on_its_line(self):
        for text in ("'MESHES: Cube, Brass Marker'", "\"MESHES: Cube, Brass Marker\"",
                     "\u201cMESHES: Cube, Brass Marker\u201d",   # Typographic quotes.
                     "> **MESHES:** Cube, Brass Marker", "Two meshes. MESHES: Cube, Brass Marker",
                     "There are two (see above): MESHES: Cube, Brass Marker"):
            with self.subTest(text=text):
                self.assertEqual(forms.answer_lines(text, "MESHES"), ["Cube, Brass Marker"])
        self.assertEqual(forms.answer_lines("The MESHES: are listed above", "MESHES"), [], "not after a sentence")
        self.assertEqual(forms.answer_lines("No MESHES here", "MESHES"), [])
        before = lambda text: forms.text_before_answer(text, "MESHES")
        self.assertTrue(before("Two meshes. MESHES: Cube, Brass Marker"))
        self.assertFalse(before("Two meshes.\nMESHES: Cube, Brass Marker") or before("'MESHES: Cube'")
                         or before("no line"))
        graded = answers.grade({"form": "mesh_names"}, [["Brass Marker", "Cube"]],
                               said("Two meshes. MESHES: Cube, Brass Marker"))
        self.assertEqual((graded["outcome"], graded["lenient"]),
                         ("right", ["other words before the answer on its line"]))

    def test_text_after_the_answer_line(self):
        after = lambda text, label="MESHES": forms.text_after_answer(text, label)
        self.assertFalse(after("Two meshes.\nMESHES: Cube, Brass Marker") or after("MESHES: Cube\n\n  \n")
                         or after("MESHES: Cube\n```\n---\n") or after("no line at all"))
        self.assertTrue(after("MESHES: Cube\nLet me know if you need more.") and after("MESHES: Cube\n\nDone"))
        self.assertFalse(after("DIMENSIONS: 1 2 1\nLOCATION: 0 0 0", "DIMENSIONS"))
        self.assertTrue(after("DIMENSIONS: 1 2 1\nLOCATION: 0 0 0\nMeasured in metres.", "DIMENSIONS"))


class NeverRaisesTests(unittest.TestCase):
    def test_no_value_makes_a_reading_raise(self):
        # Every form on every mix of the pieces answers are made of: a reading may be empty, never an exception.
        pieces = ("yes", "no", "No idea", ",", ".", ";", ":", "(", ")", "-", "\u2014", "?", "!", ":)", "<", ">", "[",
                  "]", "`", "'", "12", "1,234", "-2", "1e-7", ".5", "3D", "x", "m", "LOCATION", "and", "or", "/",
                  "Ctrl", "+", "B", "\u2318", "Cube", "Brass Marker", "2", "both", "EEVEE", "1600x900", "by", "not",
                  "vertices", "edges", "faces", "\n", " ", "\u662f", "\u4e0d", "press", "on a Mac", "")
        state = 12345
        for _ in range(4000):
            words = []
            for _ in range(1 + state % 7):
                state = (state * 1103515245 + 12345) % 2 ** 31
                words.append(pieces[state % len(pieces)])
            value = (" " if state % 3 else "").join(words)
            for form in forms.FORMS:
                read = forms.readings(form, value)   # Raises nothing.
                self.assertTrue(all(isinstance(notes, list) for _, notes in read), (form, value))
                label = forms.FORMS[form][0]
                answers.grade({"form": form}, [["x"]], said("%s: %s" % (label, value)))


class GradeTests(unittest.TestCase):
    def grade(self, text, form="mesh_names", accept=(["Brass Marker", "Cube"],), rows=None):
        return answers.grade({"form": form}, list(accept), said(text) if rows is None else rows)

    def test_the_result_says_how_the_line_was_read_and_keeps_it_as_written(self):
        exact = self.grade("Two.\nMESHES: Cube, Brass Marker")
        self.assertEqual((exact["outcome"], exact["parsed"], exact["stated"], exact.get("lenient")),
                         ("right", answers.EXACTLY, "Cube, Brass Marker", None))
        loose = self.grade("MESHES: 2 (Cube and Brass Marker)")
        self.assertEqual((loose["outcome"], loose["parsed"], loose["lenient"], loose["answered"]),
                         ("right", answers.LENIENTLY, [COUNT, AND], ["Brass Marker", "Cube"]))
        long = self.grade("MESHES: " + ", ".join("Mesh %03d" % index for index in range(60)))
        self.assertEqual(len(long["stated"]), 200, "the kept line is bounded")

    def test_a_wrong_answer_is_reported_as_what_was_meant(self):
        wrong = self.grade("MESHES: 1: Cube")
        self.assertEqual((wrong["outcome"], wrong["answered"], wrong["parsed"], wrong["stated"]),
                         ("wrong", ["Cube"], answers.LENIENTLY, "1: Cube"))
        plain = self.grade("MESHES: Cube")
        self.assertEqual((plain["outcome"], plain["answered"], plain["parsed"]), ("wrong", ["Cube"], answers.EXACTLY))
        self.assertEqual(self.grade("I see two.")["outcome"], "no_answer")
        self.assertNotIn("parsed", self.grade("I see two."), "nothing was read")

    def test_text_after_the_line_is_a_liberty_and_still_the_answer(self):
        trailing = self.grade("MESHES: Cube, Brass Marker\nAnything else?")
        self.assertEqual((trailing["outcome"], trailing["parsed"], trailing["lenient"]),
                         ("right", answers.LENIENTLY, ["text after the answer line"]))
        tool = {"role": "activity", "tool": "angelo_scene_object"}
        rows = said("MESHES: Cube, Brass Marker") + [tool] + said("Done.")
        self.assertEqual(self.grade("", rows=rows)["lenient"], ["text after the answer line"])
        box = self.grade("DIMENSIONS: 1 2 1\nLOCATION: -2 -1 0", "box", [BOX])
        self.assertEqual((box["outcome"], box["lenient"]), ("right", [OWN_LINE]))

    def test_leniently_read_answers_are_counted(self):
        right, loose = self.grade("MESHES: Cube, Brass Marker"), self.grade("MESHES: Cube and Brass Marker")
        records = [{"host_check": {"scene_unchanged": True, "answer": right}}, {"host_check": {"answer": loose}},
                   {"answer": loose}, {"host_check": {"answer": self.grade("")}}, {"host_check": {"success": True}},
                   {"host_check": None}, {}]
        self.assertEqual(answers.lenient_counts(records), {"read": 3, "parsed_leniently": 2})
        self.assertEqual(answers.answer_of({"host_check": {"answer": right}, "answer": loose}), right,
                         "the driver's own record wins")
        self.assertEqual([answers.answer_of(record) for record in ({}, {"answer": "yes"}, {"host_check": "x"})],
                         [{}] * 3)


if __name__ == "__main__":
    unittest.main()
