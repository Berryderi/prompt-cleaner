import unittest

import support  # noqa: F401  (sets sys.path)
import rules


class MaskTests(unittest.TestCase):
    def test_urls_roundtrip_byte_identical(self):
        url1 = "https://example.com/projects/notes/blob/main/docs/release-5.2.md"
        url2 = "https://example.com/projects/notes/tree/main"
        text = f"see {url1} and  {url2}"
        masked, spans = rules.mask(text)
        self.assertNotIn("http", masked)
        self.assertEqual(spans, [url1, url2])
        self.assertEqual(rules.unmask(masked, spans), text)

    def test_url_trailing_punctuation_stays_outside_the_span(self):
        masked, spans = rules.mask("go to https://example.com/a.")
        self.assertEqual(spans, ["https://example.com/a"])
        self.assertTrue(masked.endswith("]]."))

    def test_refs_paths_and_code_are_protected(self):
        text = r"fix #12 in C:\Users\me\x.py and `um uh code` and src/app.py"
        masked, spans = rules.mask(text)
        self.assertEqual(rules.unmask(masked, spans), text)
        self.assertEqual(len(spans), 4)

    def test_rules_never_touch_protected_spans(self):
        text = "um check `the the code` and https://x.io/the-the-cloud now"
        self.assertEqual(rules.clean(text), "check `the the code` and https://x.io/the-the-cloud now")


class CustomWordTests(unittest.TestCase):
    def check(self, raw, expected):
        self.assertEqual(rules.clean(raw), expected)

    def test_seed_list(self):
        pairs = {
            "why see you": "YC", "why see": "YC", "cloud code": "Claude Code", "cloud": "Claude",
            "clod": "Claude",
            "kithub": "GitHub", "scithub": "GitHub", "github": "GitHub", "revo": "repo",
            "card reels": "guardrails",
            "super base": "Supabase", "tail scale": "Tailscale", "SWAT analysis": "SWOT analysis",
            "macbook": "MacBook",
        }
        for raw, expected in pairs.items():
            with self.subTest(raw=raw):
                self.check(f"please ask {raw} now", f"please ask {expected} now")

    def test_case_insensitive_and_possessive(self):
        self.check("his Macbook's revo", "his MacBook's repo")

    def test_cloud_exceptions_unchanged(self):
        for phrase in ["Google Cloud", "Ollama Cloud", "cloud API", "API cloud", "the cloud",
                       "cloud run", "cloud storage", "cloud computing"]:
            with self.subTest(phrase=phrase):
                self.check(f"we use {phrase} here", f"we use {phrase} here")

    def test_cloud_code_beats_the_cloud_exception(self):
        self.check("in the cloud code session", "in the Claude Code session")

    def test_club_alone_unchanged(self):
        self.check("the club meeting and our team", "the club meeting and our team")

    def test_whole_phrase_only(self):
        self.check("a revolution in cloud-based clouds", "a revolution in cloud-based clouds")


class FillerTests(unittest.TestCase):
    def test_simple_fillers_removed(self):
        self.assertEqual(rules.clean("so um I uh think umm it er works hmm fine"), "so I think it works fine")

    def test_filler_with_commas(self):
        self.assertEqual(rules.clean("well, uh, it works"), "well, it works")
        self.assertEqual(rules.clean("it works, um."), "it works.")

    def test_er_is_not_matched_inside_words_or_as_ER(self):
        self.assertEqual(rules.clean("the ER visit was fine"), "the ER visit was fine")

    def test_standalone_you_know_removed(self):
        self.assertEqual(rules.clean("I want you to you know take it"), "I want you to take it")
        self.assertEqual(rules.clean("stuff you know else"), "stuff else")

    def test_real_you_know_kept(self):
        for text in ["can I run it elsewhere if you know what I mean", "do you know the way",
                     "you know what, no", "as you know it works"]:
            with self.subTest(text=text):
                self.assertEqual(rules.clean(text), text)

    def test_like_and_kind_of_are_left_to_the_model(self):
        for text in ["just like them", "like the * asterisks", "this kind of concept", "basically fine"]:
            with self.subTest(text=text):
                self.assertEqual(rules.clean(text), text)


class RepeatTests(unittest.TestCase):
    def test_unigram_and_bigram(self):
        self.assertEqual(rules.clean("the the cat"), "the cat")
        self.assertEqual(rules.clean("like like prompt"), "like prompt")
        self.assertEqual(rules.clean("more as more as how"), "more as how")

    def test_keeps_numbers_and_legit_repeats(self):
        self.assertEqual(rules.clean("add 20 20 items"), "add 20 20 items")
        self.assertEqual(rules.clean("I know that that works"), "I know that that works")

    def test_not_adjacent_not_collapsed(self):
        text = "I'll send you the link and I'll send you the link"
        self.assertEqual(rules.clean(text), text)

    def test_contractions_are_not_split(self):
        self.assertEqual(rules.clean("I I'll go"), "I I'll go")


class SpacingTests(unittest.TestCase):
    def test_spaces_and_punctuation(self):
        self.assertEqual(rules.clean("hello   world , ok ?  yes !"), "hello world, ok? yes!")

    def test_surrounding_whitespace_and_newlines_kept(self):
        self.assertEqual(rules.clean("  um hi  \n"), "  hi  \n")
        self.assertEqual(rules.clean("a  b\nc   d"), "a b\nc d")


if __name__ == "__main__":
    unittest.main()
