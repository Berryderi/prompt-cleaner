"""The cleaning pipeline: mask, rules, model, guard, unmask. Never raises for model trouble."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import guard
import jsonlog
import rules
from model import ModelError


class ModelClient(Protocol):
    def clean(self, text: str, timeout: float | None = None) -> str: ...


@dataclass
class CleanResult:
    text: str
    path: str                      # "model" | "rules-only" | "empty"
    fallback: str = ""             # e.g. "timeout", "guard:new_word"
    words_in: int = 0
    words_out: int = 0
    inserted: int = 0              # model tokens the guard dropped
    reinserted: int = 0            # source tokens the guard put back
    timings_ms: dict[str, int] = field(default_factory=dict)


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


class Cleaner:
    def __init__(self, model: ModelClient | None):
        self.model = model

    def clean(self, raw: str) -> CleanResult:
        started = time.perf_counter()
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        core = raw.strip()
        words_in = len(guard.words(core))
        if not core or words_in == 0:
            return self._done(CleanResult(raw, "empty", words_in=words_in), started, {})

        t = time.perf_counter()
        masked, spans = rules.mask(core)
        rules_masked = rules.apply_rules(masked)
        timings = {"rules": _ms(t)}
        rules_text = lead + rules.unmask(rules_masked, spans) + trail

        if self.model is None:
            return self._done(self._fallback(rules_text, words_in, "no_model"), started, timings)

        t = time.perf_counter()
        try:
            model_out = self.model.clean(rules_masked)
        except ModelError as exc:
            timings["model"] = _ms(t)
            return self._done(self._fallback(rules_text, words_in, exc.kind), started, timings)
        timings["model"] = _ms(t)

        t = time.perf_counter()
        verdict = guard.verify(rules_masked, model_out)
        timings["guard"] = _ms(t)
        if not verdict.ok:
            reason = verdict.reason if verdict.reason == "repair_too_large" else f"guard:{verdict.reason}"
            fb = self._fallback(rules_text, words_in, reason)
            fb.inserted, fb.reinserted = verdict.inserted, verdict.reinserted
            return self._done(fb, started, timings)

        final = lead + rules.unmask(verdict.text, spans) + trail
        result = CleanResult(final, "model", words_in=words_in, words_out=len(guard.words(final)),
                             inserted=verdict.inserted, reinserted=verdict.reinserted)
        return self._done(result, started, timings)

    @staticmethod
    def _fallback(rules_text: str, words_in: int, reason: str) -> CleanResult:
        return CleanResult(rules_text, "rules-only", fallback=reason, words_in=words_in,
                           words_out=len(guard.words(rules_text)))

    @staticmethod
    def _done(result: CleanResult, started: float, timings: dict[str, int]) -> CleanResult:
        result.timings_ms = {**timings, "total": _ms(started)}
        fields = {"path": result.path, "words_in": result.words_in, "words_out": result.words_out,
                  "timings_ms": result.timings_ms}
        if result.path == "model" or result.inserted or result.reinserted:
            fields["inserted"], fields["reinserted"] = result.inserted, result.reinserted
        if result.fallback:
            fields["fallback"] = result.fallback
        jsonlog.event("clean", **fields)
        return result
