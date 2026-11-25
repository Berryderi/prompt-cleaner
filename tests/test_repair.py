"""Align-and-repair guard. The three realistic cases use synthetic dictation and model outputs that
reproduce the failure patterns seen with qwen3:8b: it drops a content phrase, it inserts a word to
"complete" a false start, and it drops a clause-final "like"."""
import unittest

import support  # noqa: F401
import guard
import rules

# --- case A: the model drops a content phrase ("like broken links") --------------------------------
RAW_A = (
    "hey so I just saw that the new release notes are published and I have the link to the page and I'll "
    "send you the link and I'll send you the link of the docs too I want you to go through make sure "
    "there's only typos like broken links or anything like wrong like version numbers that type of stuff "
    "else and once you're sure it's fine I'll go and check it out see what it is see what it's like "
    "And see what's new https://example.com/notes https://example.com/docs"
)
MODEL_A = (
    "Hey, so I just saw that the new release notes are published, and I have the link to the page. "
    "And I'll send you the link and I'll send you the link of the docs too. I want you to go through, "
    "make sure there's only typos or anything wrong, like version numbers that type of stuff else. "
    "And once you're sure it's fine, I'll go and check it out, see what it is, see what it's like. "
    "And see what's new. https://example.com/notes https://example.com/docs"
)

# --- case B: the model drops a clause and a word ("see or just we'll look at it and", "ask") --------
RAW_B = (
    "yo there's a public repo called Sample Tools and I want you to take it or run it through like a lint "
    "check and then if it's fine and you know also test it to see like or just we'll look at it and see "
    "what you can reuse from it and if you find anything useful and fine and whatnot then add it into my "
    "build scripts and docs and everything carefully it's pretty simple ask so you should be able to do "
    "this pretty well"
)
MODEL_B = (
    "Yo, there's a public repo called Sample Tools, and I want you to take it or run it through a lint "
    "check, and then if it's fine, also test it to see what you can reuse from it and if you find anything "
    "useful and fine and whatnot, then add it into my build scripts, docs, and everything carefully. "
    "It's pretty simple, so you should be able to do this pretty well."
)

# --- case C: the model inserts "it," to complete the false start "if you have" ---------------------
RAW_C = (
    "make a note and I want you to send this to the team also if you have if I don't have it already make "
    "like a status page kind of where it shows like a build summary and any other release related or "
    "finding gaps within a release so that it can flag problems and also don't forget the changelog"
)
MODEL_C = (
    "Make a note, and I want you to send this to the team. Also, if you have it, if I don't have it "
    "already, make a status page where it shows a build summary and any other release related or finding "
    "gaps within a release, so that it can flag problems. And also, don't forget the changelog."
)


def prepare(raw: str, model_output: str):
    masked, spans = rules.mask(raw)
    source = rules.apply_rules(masked)
    candidate, _ = rules.mask(model_output)
    return source, candidate, spans


def flat(text: str) -> str:
    return " ".join(guard.words(text))


class RealisticOutputTests(unittest.TestCase):
    def test_dropped_content_phrase_is_reinserted(self):
        source, candidate, _ = prepare(RAW_A, MODEL_A)
        self.assertNotIn("broken links", flat(candidate))  # the model really dropped it
        r = guard.verify(source, candidate)
        self.assertTrue(r.ok, r.reason)
        self.assertIn("like broken links", flat(r.text))
        self.assertIn("[[0]] [[1]]", r.text)
        self.assertGreater(r.reinserted, 0)

    def test_dropped_clause_and_word_are_restored_without_the_filler(self):
        source, candidate, _ = prepare(RAW_B, MODEL_B)
        self.assertNotIn("look at it and", flat(candidate))
        r = guard.verify(source, candidate)
        self.assertTrue(r.ok, r.reason)
        self.assertIn("see or just well look at it and see what", flat(r.text))
        self.assertIn("simple ask so", flat(r.text))
        self.assertNotIn("see like or just", flat(r.text))  # the filler "like" inside the restored run stays out

    def test_inserted_word_is_dropped_and_filler_removal_kept(self):
        source, candidate, _ = prepare(RAW_C, MODEL_C)
        self.assertEqual(guard.check(source, candidate).reason, "inserted_or_reordered")
        r = guard.verify(source, candidate)
        self.assertTrue(r.ok, r.reason)
        self.assertIn("if you have if i dont have it already", flat(r.text))
        self.assertNotIn("have it if", flat(r.text))
        self.assertEqual(r.inserted, 1)
        for filler in ["like", "kind of"]:
            self.assertNotIn(filler, flat(r.text))

    def test_clause_final_like_is_not_treated_as_filler(self):
        model = MODEL_A.replace("see what it's like. And see what's new.", "see what it's And see what's new.")
        self.assertNotEqual(model, MODEL_A)
        source, candidate, _ = prepare(RAW_A, model)
        r = guard.verify(source, candidate)
        self.assertTrue(r.ok, r.reason)
        self.assertIn("see what its like", flat(r.text))

    def test_repaired_text_passes_the_strict_guard(self):
        for raw, model in [(RAW_A, MODEL_A), (RAW_B, MODEL_B), (RAW_C, MODEL_C)]:
            source, candidate, _ = prepare(raw, model)
            r = guard.verify(source, candidate)
            self.assertTrue(guard.check(source, r.text).ok)


class RepairPrimitiveTests(unittest.TestCase):
    def test_insertion_is_dropped(self):
        r = guard.verify("so check the repo and tell me", "So check the repo please and tell me.")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "So check the repo and tell me.")
        self.assertEqual(r.inserted, 1)

    def test_filler_and_function_word_deletions_are_allowed(self):
        r = guard.verify("we think the new server is the best option for us and um you know it works well",
                         "We think the new server is the best option for us and it works well.")
        self.assertTrue(r.ok, r.reason)
        self.assertEqual((r.inserted, r.reinserted), (0, 0))

    def test_content_deletion_is_restored(self):
        src = "please check the broken server logs and then restart the nightly job"
        r = guard.verify(src, "Please check the broken logs and then restart the nightly job.")
        self.assertTrue(r.ok, r.reason)
        self.assertIn("server", r.text)
        self.assertEqual(r.reinserted, 1)

    def test_adjacent_repeat_deletion_is_allowed(self):
        r = guard.verify("we should go more as like more as how it works for our team now",
                         "We should go more as how it works for our team now.")
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.reinserted, 0)

    def test_typo_fix_replacement_kept_other_replacement_undone(self):
        self.assertEqual(guard.verify("it was wierd and old", "It was weird and old.").text, "It was weird and old.")
        src = "this is a really strange and old server that we keep for the legacy jobs"
        r = guard.verify(src, "This is a really bizarre and old server that we keep for the legacy jobs.")
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.text, "This is a really strange and old server that we keep for the legacy jobs.")
        self.assertEqual((r.inserted, r.reinserted), (1, 1))

    def test_mask_tokens_are_restored_if_deleted_or_mangled(self):
        r = guard.verify("see [[0]] now and tell me about it later", "See now and tell me about it later.")
        self.assertTrue(r.ok, r.reason)
        self.assertIn("[[0]]", r.text)
        r = guard.verify("see [[0]] now and tell me about it later", "See [[7]] now and tell me about it later.")
        self.assertTrue(r.ok, r.reason)
        self.assertIn("[[0]]", r.text)
        self.assertNotIn("[[7]]", r.text)

    def test_wholesale_rewrite_is_rejected_as_too_large(self):
        r = guard.verify("please check the broken server logs and restart the nightly job today",
                         "Kindly examine the faulty machine journals then reboot the scheduled task.")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "repair_too_large")

    def test_paragraph_breaks_survive(self):
        r = guard.verify("first thing here\n\nsecond thing there", "First thing here.\n\nSecond thing there.")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "First thing here.\n\nSecond thing there.")

    def test_empty_model_output_rejected(self):
        self.assertFalse(guard.verify("hello there", "   ").ok)


class ClauseFinalLikeTests(unittest.TestCase):
    def test_mid_clause_filler_like_still_deletable(self):
        r = guard.verify("we need like a place where they can prep for the exams",
                         "We need a place where they can prep for the exams.")
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.reinserted, 0)

    def test_like_before_capitalised_sentence_start_is_restored(self):
        r = guard.verify("i want to see what it is like And then we can decide what to do",
                         "I want to see what it is. And then we can decide what to do.")
        self.assertTrue(r.ok, r.reason)
        self.assertIn("like", r.text)


class ContentKeysTests(unittest.TestCase):
    def test_filler_function_and_repeats_excluded(self):
        keys = guard.content_keys("so um i think you know the server like really failed more as like more as")
        self.assertEqual(keys, ["think", "server", "really", "failed"])


if __name__ == "__main__":
    unittest.main()
