import unittest

import support  # noqa: F401
import guard

SRC = "Can you make an interface for my local model. It could look better. Thanks a lot for this."


class GuardTests(unittest.TestCase):
    def test_accepts_punctuation_and_case_changes(self):
        self.assertTrue(guard.check("i think its fine", "I think it's fine.").ok)

    def test_accepts_filler_removed(self):
        self.assertTrue(guard.check("it was like really big", "It was really big.").ok)

    def test_rejects_added_word(self):
        r = guard.check(SRC, "Can you please make an interface for my local model. It could look better. Thanks a lot for this.")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "new_word")

    def test_rejects_dropped_sentence(self):
        r = guard.check(SRC, "Can you make an interface for my local model.")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "dropped_content")

    def test_rejects_more_words_than_source(self):
        r = guard.check("go now", "go now now")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "more_words")

    def test_small_spelling_fix_allowed_for_4_plus_letters(self):
        self.assertTrue(guard.check("this is blorpfully fine", "This is blorpfully fine.").ok)
        self.assertTrue(guard.check("it was wierd", "It was weird.").ok)

    def test_fuzzy_match_needs_same_first_letter(self):
        # "also" is edit distance 2 from "else" but is a new word, not a typo fix
        self.assertEqual(guard.check("stuff else now", "stuff also now").reason, "new_word")

    def test_rejects_inserted_known_words(self):
        # "it" and "and" both occur in the source, but the model moved/added them
        # trailing words are dropped so the word count alone does not catch the insertion
        r = guard.check("go through make sure it works and then check the other stuff too",
                        "go through it and make sure it works and then check")
        self.assertEqual((r.ok, r.reason), (False, "inserted_or_reordered"))

    def test_rejects_reordered_words(self):
        self.assertFalse(guard.check("tell him and run it now", "run it and tell him now").ok)

    def test_deletions_in_order_are_fine(self):
        self.assertTrue(guard.check("so um run it and then tell him", "So run it and tell him.").ok)

    def test_short_new_word_rejected(self):
        self.assertFalse(guard.check("go to it", "go to is").ok)

    def test_apostrophes_and_case_ignored(self):
        self.assertTrue(guard.check("alices laptop said", "Alice's laptop said.").ok)

    def test_changed_mask_rejected(self):
        self.assertEqual(guard.check("see [[0]] now", "see [[1]] now").reason, "mask_changed")
        self.assertEqual(guard.check("see [[0]] now", "see now").reason, "mask_changed")
        self.assertEqual(guard.check("see [[0]] now", "see [[0] now").reason, "mask_changed")

    def test_masked_tokens_do_not_count_as_words(self):
        self.assertTrue(guard.check("see [[0]] now", "See [[0]] now.").ok)

    def test_edit_distance(self):
        self.assertEqual(guard.edit_distance("kitten", "sitting", 5), 3)
        self.assertGreater(guard.edit_distance("abcdef", "abc"), 2)


if __name__ == "__main__":
    unittest.main()
