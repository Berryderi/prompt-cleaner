"""Run the full pipeline (real model) over tests/samples.jsonl (or, if absent,
tests/samples.example.jsonl) and check the done criteria.

Usage: python eval.py            (model must be warm; a warm-up call is made first)
       python eval.py --rules-only   (no model; rules stage only, for a quick look)
Exit code 1 if any sample fails.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

import config
import guard
import jsonlog
import rules
from cleaner import Cleaner
from model import OllamaClient

_TESTS = Path(__file__).parent / "tests"
SAMPLES = _TESTS / "samples.jsonl" if (_TESTS / "samples.jsonl").exists() else _TESTS / "samples.example.jsonl"

FILLER_TERMS = ["um", "uh", "umm", "you know", "like", "kind of", "basically"]
MISHEARINGS = ["kithub", "scithub", "clod", "super base", "tail scale",
               "SWAT analysis", "cloud code", "revo", "card reels", "why see"]
URL_RE = re.compile(r"https?://[^\s]+")

MIN_SIMILARITY = 0.90
TYPED_SIMILARITY = 1.00
# Latency rule: <= 2.0 s up to 100 words, then +1.5 s per extra 100 words
# (decode speed is ~72 tok/s on an 8 GB RTX 2080 SUPER, so a flat 2.0 s at 150 words is not reachable).
LATENCY_BASE_S = 2.0
LATENCY_BASE_WORDS = 100
LATENCY_EXTRA_S_PER_100_WORDS = 1.5


def keys(text: str) -> list[str]:
    """Lowercased words with punctuation stripped (apostrophes dropped)."""
    cleaned = re.sub(r"[^\w\s]", "", text.replace("'", "").replace("’", "")).lower()
    return cleaned.split()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, keys(a), keys(b), autojunk=False).ratio()


def count_term(term: str, text: str) -> int:
    return len(re.findall(rf"(?<![\w'])" + r"\s+".join(map(re.escape, term.split())) + r"(?![\w'])", text, re.I))


def word_diff(output: str, expected: str) -> str:
    """Inline diff on words: [-in output only-] {+in expected only+}."""
    a, b = output.split(), expected.split()
    ka, kb = ["".join(keys(w)) for w in a], ["".join(keys(w)) for w in b]
    parts: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if op == "equal":
            parts.append(" ".join(a[i1:i2]))
        else:
            if i2 > i1:
                parts.append("[-" + " ".join(a[i1:i2]) + "-]")
            if j2 > j1:
                parts.append("{+" + " ".join(b[j1:j2]) + "+}")
    return " ".join(parts)


def latency_limit(sample: dict) -> float:
    extra_words = max(0, len(sample["raw"].split()) - LATENCY_BASE_WORDS)
    return LATENCY_BASE_S + LATENCY_EXTRA_S_PER_100_WORDS * extra_words / 100


def missing_content(sample: dict, out: str) -> list[str]:
    """Content words of the rules output (not filler, function word or repeat) absent from `out`.

    Words listed in the sample's "allow_dropped" may be missing (abandoned fragments).
    """
    allowed = {w.lower() for w in sample.get("allow_dropped", [])}
    available = [t.key for t in guard.tokenize(out)]
    missing: list[str] = []
    for word in guard.content_keys(rules.clean(sample["raw"])):
        hit = next((i for i, a in enumerate(available) if guard._same_word(a, word)), None)
        if hit is not None:
            available.pop(hit)
        elif word not in allowed:
            missing.append(word)
    return missing


def check_sample(sample: dict, out: str, result, elapsed_s: float) -> tuple[list[str], float]:
    fails: list[str] = []
    expected = sample["expected"]
    for term in FILLER_TERMS:
        if count_term(term, out) > count_term(term, expected):
            fails.append(f"filler:{term}")
    for bad in MISHEARINGS:
        if count_term(bad, out):
            fails.append(f"mishearing:{bad}")
    if URL_RE.findall(out) != URL_RE.findall(sample["raw"]):
        fails.append("urls")
    lost = missing_content(sample, out)
    if lost:
        fails.append("content:" + "/".join(lost[:4]) + ("+" if len(lost) > 4 else ""))
    sim = similarity(out, expected)
    if sim < MIN_SIMILARITY:
        fails.append(f"similarity<{MIN_SIMILARITY:.2f}")
    if sample["kind"] == "typed" and sim < TYPED_SIMILARITY:
        fails.append("typed!=1.00")
    if elapsed_s > latency_limit(sample):
        fails.append(f"latency>{latency_limit(sample):.1f}s")
    return fails, sim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules-only", action="store_true")
    args = ap.parse_args()

    jsonlog.setup(config.LOG_DIR)
    cfg = config.load()
    client = None if args.rules_only else OllamaClient(cfg)
    if client is not None and not client.warm_up():
        print("model warm-up failed: is Ollama running with qwen3:8b?", file=sys.stderr)
        return 2
    cleaner = Cleaner(client)

    samples = [json.loads(line) for line in SAMPLES.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows, diffs, failed = [], [], 0
    for s in samples:
        t = time.perf_counter()
        result = cleaner.clean(s["raw"])
        elapsed = time.perf_counter() - t
        fails, sim = check_sample(s, result.text, result, elapsed)
        path = result.path if not result.fallback else f"rules-only ({result.fallback})"
        verdict = "PASS" if not fails else "FAIL " + ",".join(fails)
        failed += bool(fails)
        rows.append((s["id"], s["kind"], len(s["raw"].split()), path, f"{sim:.3f}", f"{elapsed:.2f}s", verdict))
        diffs.append((s["id"], word_diff(result.text, s["expected"])))

    headers = ("id", "kind", "words", "path", "sim", "latency", "result")
    widths = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    for row in [headers] + rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    print("\nWord diffs vs expected ([-output only-] {+expected only+}):")
    for sid, d in diffs:
        print(f"\n{sid}: {d}")
    print(f"\n{len(samples) - failed}/{len(samples)} samples pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
