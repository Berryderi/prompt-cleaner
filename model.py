"""Local qwen3 call through Ollama's /api/chat (loopback only).

Same safety checks as local-llm-mcp/src/ollama.js: a prompt that fills the
context window may have been silently truncated, and done_reason "length"
means the output was cut off. Either one is an error, never a result.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request

import jsonlog
from config import Config
from prompts import EXAMPLES, SYSTEM_PROMPT, USER_PREFIX

CHARS_PER_TOKEN = 3           # conservative: real English averages ~4
CTX_STEP = 4096               # ctx changes force an Ollama model reload, so use coarse steps
OUTPUT_TOKEN_FACTOR = 2       # num_predict = 2x the input tokens
MIN_PREDICT = 64
SYSTEM_OVERHEAD_TOKENS = 900  # system prompt + examples, generous
WARMUP_PREDICT = 16
MAX_RESPONSE_BYTES = 2_000_000  # a reply larger than this is a model failure (rules-only), never read whole


class ModelError(Exception):
    """kind: timeout | unreachable | gpu_busy | http | truncated | too_large | empty | bad_response"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


def num_predict_for(text: str) -> int:
    return max(MIN_PREDICT, (len(text) // CHARS_PER_TOKEN + 8) * OUTPUT_TOKEN_FACTOR)


def num_ctx_for(text: str, num_predict: int) -> int:
    need = len(text) // CHARS_PER_TOKEN + SYSTEM_OVERHEAD_TOKENS + num_predict
    return max(CTX_STEP, -(-need // CTX_STEP) * CTX_STEP)


def _same_model(loaded: str, wanted: str) -> bool:
    norm = lambda n: n if ":" in n else n + ":latest"  # noqa: E731
    return norm(loaded) == norm(wanted)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        base = cfg.ollama_url.rstrip("/")
        self.url = base + "/api/chat"
        self.ps_url = base + "/api/ps"
        # An empty ProxyHandler: urllib would otherwise honour HTTP_PROXY / the Windows proxy settings and
        # could route the dictated text through a proxy that does not exempt 127.0.0.1.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def build_payload(self, text: str, num_predict: int | None = None) -> dict:
        predict = num_predict or num_predict_for(text)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for src, dst in EXAMPLES:
            messages += [{"role": "user", "content": USER_PREFIX + src}, {"role": "assistant", "content": dst}]
        messages.append({"role": "user", "content": USER_PREFIX + text})
        return {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.cfg.keep_alive,
            "options": {"temperature": 0, "num_ctx": num_ctx_for(text, predict), "num_predict": predict},
        }

    def _post(self, payload: dict, timeout: float) -> dict:
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"}, method="POST",
        )
        return self._request(req, timeout)

    def loaded_models(self) -> list[str]:
        """Names of the models Ollama currently holds in memory (GET /api/ps)."""
        data = self._request(urllib.request.Request(self.ps_url, method="GET"), self.cfg.ps_timeout_s)
        names: list[str] = []
        for m in data.get("models") or []:
            names.append(str(m.get("name") or m.get("model") or ""))
        return names

    def check_gpu_free(self) -> None:
        """Raise ModelError("gpu_busy") if another holder's model is loaded and ours is not.

        Loading qwen3:8b next to a 22 GB model would push that model out of the shared GPU.
        We never evict or unload anyone else's model.
        """
        if self.cfg.evict_other_models:
            return
        loaded = [n for n in self.loaded_models() if n]
        if loaded and not any(_same_model(n, self.cfg.model) for n in loaded):
            raise ModelError("gpu_busy", f"{len(loaded)} other model(s) loaded")

    def _request(self, req: urllib.request.Request, timeout: float) -> dict:
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ModelError("too_large", f"over {MAX_RESPONSE_BYTES} bytes")
            return json.loads(body.decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            raise ModelError("timeout") from exc
        except urllib.error.HTTPError as exc:
            raise ModelError("http", str(exc.code)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ModelError("timeout") from exc
            raise ModelError("unreachable", type(exc.reason).__name__) from exc
        except (OSError, ValueError) as exc:
            raise ModelError("bad_response", type(exc).__name__) from exc

    def clean(self, text: str, timeout: float | None = None) -> str:
        self.check_gpu_free()
        payload = self.build_payload(text)
        opts = payload["options"]
        data = self._post(payload, timeout or self.cfg.timeout_s)
        prompt_tokens = data.get("prompt_eval_count", 0)
        if prompt_tokens >= opts["num_ctx"] - opts["num_predict"]:
            raise ModelError("truncated", "prompt may have been cut")
        if data.get("done_reason") == "length":
            raise ModelError("truncated", "output hit num_predict")
        content = (data.get("message") or {}).get("content") or ""
        content = _THINK_RE.sub("", content).strip()
        if not content:
            raise ModelError("empty")
        return content

    def warm_up(self) -> bool:
        """Load the model with the same context size as normal calls. Never raises."""
        try:
            self.check_gpu_free()
            self._post(self.build_payload("Hello there.", num_predict=WARMUP_PREDICT), self.cfg.warmup_timeout_s)
            jsonlog.event("warmup", ok=True)
            return True
        except ModelError as exc:
            jsonlog.event("warmup", ok=False, kind=exc.kind)
            return False
