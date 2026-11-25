"""Model-free checks of the rules stage and guard against the sample set."""
import re
import unittest

import support
import guard
import rules

MISHEARINGS = ["kithub", "scithub", "clod", "super base", "tail scale",
               "SWAT analysis", "cloud code", "revo", "card reels", "why see"]
URL_RE = re.compile(r"https?://\S+")


class SampleRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = support.load_samples()

    def test_samples_present(self):
        self.assertGreaterEqual(len(self.samples), 6)

    def test_rules_output_has_no_mishearing_and_keeps_urls(self):
        for s in self.samples:
            with self.subTest(id=s["id"]):
                out = rules.clean(s["raw"])
                for bad in MISHEARINGS:
                    self.assertIsNone(re.search(rf"(?<!\w){re.escape(bad)}(?!\w)", out, re.I), bad)
                self.assertEqual(URL_RE.findall(out), URL_RE.findall(s["raw"]))

    def test_expected_outputs_pass_the_guard(self):
        for s in self.samples:
            with self.subTest(id=s["id"]):
                src, _ = rules.mask(s["raw"])
                cand, _ = rules.mask(s["expected"])
                r = guard.check(rules.apply_rules(src), cand)
                self.assertTrue(r.ok, r.reason)

    def test_typed_samples_only_change_case_and_punctuation(self):
        strip = lambda t: re.sub(r"[^\w\s]", "", t).lower().split()  # noqa: E731
        for s in self.samples:
            if s["kind"] == "typed":
                with self.subTest(id=s["id"]):
                    self.assertEqual(strip(rules.clean(s["raw"])), strip(s["expected"]))

    def test_rules_never_add_words(self):
        for s in self.samples:
            with self.subTest(id=s["id"]):
                self.assertLessEqual(len(guard.words(rules.clean(s["raw"]))), len(guard.words(s["raw"])))


if __name__ == "__main__":
    unittest.main()
