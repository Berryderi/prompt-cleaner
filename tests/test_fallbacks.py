"""Fallback paths against a local http.server stub: no real Ollama or GPU is touched."""
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401
import config
import jsonlog
from cleaner import Cleaner
from model import OllamaClient
from stub_ollama import StubOllama

RAW = "um so check the revo and like tell me"
RULES_ONLY = "so check the repo and like tell me"
GOOD = lambda text: "So check the repo and tell me."  # noqa: E731


def cfg_for(stub, **kw):
    return config.Config(ollama_url=stub.url, **kw)


class LogCapture:
    """Point the JSONL logger at a temp dir and read the rows back."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logger = logging.getLogger(jsonlog.LOGGER_NAME)
        self.saved = list(self.logger.handlers)
        for h in self.saved:
            self.logger.removeHandler(h)
        jsonlog.setup(Path(self.tmp.name))
        return self

    def rows(self) -> list[dict]:
        for h in self.logger.handlers:
            h.flush()
        text = (Path(self.tmp.name) / "cleaner.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def __exit__(self, *exc):
        for h in list(self.logger.handlers):
            h.close()
            self.logger.removeHandler(h)
        for h in self.saved:
            self.logger.addHandler(h)
        self.tmp.cleanup()


class TimeoutFallbackTests(unittest.TestCase):
    def test_slow_model_returns_rules_only_within_the_timeout_and_logs_it(self):
        timeout = 0.5
        with StubOllama(chat_delay_s=3.0) as stub, LogCapture() as logs:
            cleaner = Cleaner(OllamaClient(cfg_for(stub, timeout_s=timeout)))
            t = time.perf_counter()
            r = cleaner.clean(RAW)
            elapsed = time.perf_counter() - t
            row = logs.rows()[-1]
        self.assertEqual((r.path, r.fallback, r.text), ("rules-only", "timeout", RULES_ONLY))
        self.assertLess(elapsed, timeout + 0.8)
        self.assertEqual(row["fallback"], "timeout")
        self.assertEqual(row["path"], "rules-only")


class GpuBusyTests(unittest.TestCase):
    def test_other_model_loaded_skips_chat_and_returns_at_once(self):
        with StubOllama(loaded=["research-original:latest"], chat_delay_s=3.0) as stub, LogCapture() as logs:
            cleaner = Cleaner(OllamaClient(cfg_for(stub)))
            t = time.perf_counter()
            r = cleaner.clean(RAW)
            elapsed = time.perf_counter() - t
            row = logs.rows()[-1]
            chat_calls, requests = stub.chat_calls(), list(stub.requests)
        self.assertEqual((r.path, r.fallback, r.text), ("rules-only", "gpu_busy", RULES_ONLY))
        self.assertEqual(chat_calls, 0, requests)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(row["fallback"], "gpu_busy")

    def test_our_model_loaded_is_used(self):
        with StubOllama(loaded=["qwen3:8b"], reply=GOOD) as stub:
            r = Cleaner(OllamaClient(cfg_for(stub))).clean(RAW)
            self.assertEqual(stub.chat_calls(), 1)
        self.assertEqual((r.path, r.text), ("model", "So check the repo and tell me."))

    def test_our_model_next_to_another_is_used(self):
        with StubOllama(loaded=["other:7b", "qwen3:8b"], reply=GOOD) as stub:
            r = Cleaner(OllamaClient(cfg_for(stub))).clean(RAW)
        self.assertEqual(r.path, "model")

    def test_nothing_loaded_is_used(self):
        with StubOllama(loaded=[], reply=GOOD) as stub:
            r = Cleaner(OllamaClient(cfg_for(stub))).clean(RAW)
        self.assertEqual(r.path, "model")

    def test_ps_is_asked_before_every_call(self):
        with StubOllama(loaded=["qwen3:8b"], reply=GOOD) as stub:
            cleaner = Cleaner(OllamaClient(cfg_for(stub)))
            cleaner.clean(RAW)
            cleaner.clean(RAW)
            self.assertEqual(stub.requests, ["GET /api/ps", "POST /api/chat"] * 2)

    def test_warm_up_skipped_when_another_model_is_loaded(self):
        with StubOllama(loaded=["research-original:latest"]) as stub, LogCapture() as logs:
            self.assertFalse(OllamaClient(cfg_for(stub)).warm_up())
            self.assertEqual(stub.chat_calls(), 0)
            self.assertEqual(logs.rows()[-1]["ok"], False)

    def test_warm_up_runs_when_gpu_is_free(self):
        with StubOllama(loaded=[], reply=GOOD) as stub:
            self.assertTrue(OllamaClient(cfg_for(stub)).warm_up())
            self.assertEqual(stub.chat_calls(), 1)

    def test_no_request_ever_asks_ollama_to_unload_anything(self):
        with StubOllama(loaded=["research-original:latest"]) as stub:
            Cleaner(OllamaClient(cfg_for(stub))).clean(RAW)
            self.assertEqual(stub.requests, ["GET /api/ps"])  # no chat, no keep_alive:0 request

    def test_evict_flag_defaults_off(self):
        self.assertFalse(config.load().evict_other_models)


if __name__ == "__main__":
    unittest.main()
