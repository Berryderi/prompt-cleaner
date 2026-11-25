"""Text must never leave this PC: no proxies, no cloud models, bounded responses."""
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import support  # noqa: F401
import config
import model
from cleaner import Cleaner
from model import ModelError, OllamaClient
from stub_ollama import StubOllama

RAW = "um so check the revo and like tell me"
GOOD = lambda text: "So check the repo and tell me."  # noqa: E731
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens on port 9
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


class NoProxyTests(unittest.TestCase):
    def test_environment_proxies_are_ignored(self):
        env = {v: DEAD_PROXY for v in PROXY_VARS}
        env["NO_PROXY"] = env["no_proxy"] = ""  # nothing exempts 127.0.0.1
        with StubOllama(loaded=["qwen3:8b"], reply=GOOD) as stub, mock.patch.dict(os.environ, env):
            r = Cleaner(OllamaClient(config.Config(ollama_url=stub.url))).clean(RAW)
            chat_calls = stub.chat_calls()
        self.assertEqual((r.path, r.text), ("model", "So check the repo and tell me."))
        self.assertEqual(chat_calls, 1)  # the request reached the stub directly, not the dead proxy

    def test_opener_has_no_active_proxy_handler(self):
        # build_opener(ProxyHandler({})) replaces urllib's default env/registry-reading ProxyHandler; an
        # empty one registers no open methods, so no ProxyHandler is left on the opener.
        handlers = OllamaClient(config.Config())._opener.handlers
        self.assertFalse(any(isinstance(h, urllib.request.ProxyHandler) for h in handlers))


class CloudModelTests(unittest.TestCase):
    def test_cloud_model_names_are_refused(self):
        for name in ["gpt-oss:120b-cloud", "x:cloud", "deepseek-v3.1:671b-cloud", "Qwen3:Cloud"]:
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.Config(model=name)
                self.assertIn("off this PC", str(ctx.exception))

    def test_refused_through_config_json_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text('{"model": "gpt-oss:120b-cloud"}', encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_local_models_are_accepted(self):
        for name in ["qwen3:8b", "qwen3.6:35b", "llama3.2:3b", "cloudy-model:7b"]:
            with self.subTest(name=name):
                self.assertEqual(config.Config(model=name).model, name)


class ResponseCapTests(unittest.TestCase):
    def test_cap_is_a_named_constant(self):
        self.assertEqual(model.MAX_RESPONSE_BYTES, 2_000_000)

    def test_oversized_response_is_a_model_failure_and_falls_back(self):
        with StubOllama(loaded=["qwen3:8b"], reply=lambda text: "x" * 5000) as stub, \
                mock.patch.object(model, "MAX_RESPONSE_BYTES", 1000):
            client = OllamaClient(config.Config(ollama_url=stub.url))
            with self.assertRaises(ModelError) as ctx:
                client.clean("hello there")
            self.assertEqual(ctx.exception.kind, "too_large")
            r = Cleaner(client).clean(RAW)
        self.assertEqual((r.path, r.fallback), ("rules-only", "too_large"))
        self.assertEqual(r.text, "so check the repo and like tell me")

    def test_response_just_under_the_cap_is_accepted(self):
        with StubOllama(loaded=["qwen3:8b"], reply=GOOD) as stub:
            self.assertEqual(OllamaClient(config.Config(ollama_url=stub.url)).clean("hello there"),
                             "So check the repo and tell me.")


if __name__ == "__main__":
    unittest.main()
