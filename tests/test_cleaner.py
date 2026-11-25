import json
import logging
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import config
import jsonlog
from cleaner import Cleaner
from model import ModelError, OllamaClient

RAW = "um so i want you to like check the revo at https://example.com/a.b and tell me"


class FakeModel:
    def __init__(self, output=None, error=None):
        self.output, self.error, self.calls = output, error, []

    def clean(self, text, timeout=None):
        self.calls.append(text)
        if self.error:
            raise self.error
        return self.output(text) if callable(self.output) else self.output


class CleanerTests(unittest.TestCase):
    def test_model_path_uses_model_text_and_restores_urls(self):
        model = FakeModel("So I want you to check the repo at [[0]] and tell me.")
        r = Cleaner(model).clean(RAW)
        self.assertEqual(r.path, "model")
        self.assertEqual(r.text, "So I want you to check the repo at https://example.com/a.b and tell me.")
        # the model only ever sees masked text, with rules already applied
        self.assertNotIn("https", model.calls[0])
        self.assertIn("repo", model.calls[0])
        self.assertNotIn(" um ", " " + model.calls[0])

    def test_timeout_falls_back_to_rules_only(self):
        r = Cleaner(FakeModel(error=ModelError("timeout"))).clean(RAW)
        self.assertEqual((r.path, r.fallback), ("rules-only", "timeout"))
        self.assertEqual(r.text, "so i want you to like check the repo at https://example.com/a.b and tell me")

    def test_inserted_word_is_dropped_not_the_whole_output(self):
        r = Cleaner(FakeModel("So I want you to please check the repo at [[0]] and tell me.")).clean(RAW)
        self.assertEqual(r.path, "model")
        self.assertEqual(r.text, "So I want you to check the repo at https://example.com/a.b and tell me.")
        self.assertEqual((r.inserted, r.reinserted), (1, 0))

    def test_changed_url_is_put_back(self):
        r = Cleaner(FakeModel("So I want you to check the repo at https://example.org and tell me.")).clean(RAW)
        self.assertEqual(r.path, "model")
        self.assertIn("https://example.com/a.b", r.text)
        self.assertNotIn("example.org", r.text)

    def test_wholesale_rewrite_falls_back_with_repair_too_large(self):
        r = Cleaner(FakeModel("Kindly examine the software archive located online and inform me about it.")).clean(RAW)
        self.assertEqual((r.path, r.fallback), ("rules-only", "repair_too_large"))
        self.assertIn("https://example.com/a.b", r.text)

    def test_deleted_content_is_restored(self):
        r = Cleaner(FakeModel("So I want you to check the at [[0]] and tell me.")).clean(RAW)
        self.assertEqual(r.path, "model")
        self.assertIn("repo", r.text)
        self.assertEqual(r.reinserted, 1)

    def test_no_model_is_rules_only(self):
        r = Cleaner(None).clean("um hello")
        self.assertEqual((r.text, r.path, r.fallback), ("hello", "rules-only", "no_model"))

    def test_empty_and_wordless_input_untouched(self):
        for raw in ["", "   \n", "?!"]:
            r = Cleaner(FakeModel("x")).clean(raw)
            self.assertEqual((r.text, r.path), (raw, "empty"))

    def test_surrounding_whitespace_preserved(self):
        r = Cleaner(FakeModel("Hello there.")).clean("  hello there \n")
        self.assertEqual(r.text, "  Hello there. \n")

    def test_logging_has_no_dictated_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = logging.getLogger(jsonlog.LOGGER_NAME)
            old = list(logger.handlers)
            for h in old:
                logger.removeHandler(h)
            try:
                jsonlog.setup(Path(tmp))
                Cleaner(FakeModel(error=ModelError("timeout"))).clean(RAW)
                for h in logger.handlers:
                    h.flush()
                content = (Path(tmp) / "cleaner.jsonl").read_text(encoding="utf-8")
                row = json.loads(content.splitlines()[-1])
                self.assertEqual(row["fallback"], "timeout")
                self.assertEqual(row["path"], "rules-only")
                self.assertIn("timings_ms", row)
                for secret in ["revo", "example.com", "check"]:
                    self.assertNotIn(secret, content)
            finally:
                for h in list(logger.handlers):
                    h.close()
                    logger.removeHandler(h)
                for h in old:
                    logger.addHandler(h)


class ModelClientTests(unittest.TestCase):
    def test_payload_follows_the_brief(self):
        p = OllamaClient(config.Config()).build_payload("hello world")
        self.assertEqual(p["model"], "qwen3:8b")
        self.assertIs(p["think"], False)
        self.assertIs(p["stream"], False)
        self.assertEqual(p["keep_alive"], "30m")
        self.assertEqual(p["options"]["temperature"], 0)
        self.assertGreaterEqual(p["options"]["num_predict"], 64)

    def test_ctx_is_bucketed_so_normal_inputs_do_not_reload_the_model(self):
        from model import num_ctx_for, num_predict_for
        small, big = "word " * 50, "word " * 1200
        self.assertEqual(num_ctx_for(small, num_predict_for(small)), 4096)
        self.assertEqual(num_ctx_for(big, num_predict_for(big)) % 4096, 0)
        self.assertGreater(num_ctx_for(big, num_predict_for(big)), 4096)

    def test_non_loopback_url_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"ollama_url": "http://example.com:11434"}), encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_shipped_config_is_valid(self):
        self.assertEqual(config.load().hotkey, "ctrl+shift+space")

    def test_unreachable_server_raises_modelerror_not_a_crash(self):
        cfg = config.Config(ollama_url="http://127.0.0.1:9", timeout_s=1.0)  # port 9: nothing listens
        with self.assertRaises(ModelError) as ctx:
            OllamaClient(cfg).clean("hello")
        self.assertIn(ctx.exception.kind, {"unreachable", "timeout"})


if __name__ == "__main__":
    unittest.main()
