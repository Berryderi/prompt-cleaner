"""Deterministic text rules: custom words, safe filler, stutters, spacing.

Everything here is a pure function. Protected spans (URLs, paths, `code`, #n)
are swapped for `[[n]]` tokens by `mask()` before any stage runs and restored
by `unmask()`, so they come out byte-identical.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CUSTOM_WORDS_PATH = Path(__file__).with_name("custom_words.json")

MASK_RE = re.compile(r"\[\[(\d+)\]\]")

_PROTECTED_RE = re.compile(
    "|".join(
        [
            r"```.*?```",  # fenced code
            r"`[^`\n]+`",  # inline code
            r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+",  # URLs
            r"(?<![\w.])www\.[^\s<>\"']+",
            r"(?<![\w.])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",  # e-mail
            r"(?<![\w])[A-Za-z]:[\\/][^\s<>\"']*",  # Windows path
            r"(?<![\w/])(?:~|\.{1,2})?/[\w.~-]+(?:/[\w.~-]*)*",  # /posix/path
            r"\b[\w-]+(?:/[\w.-]+)+",  # relative/path
            r"\b[\w-]+\.(?:py|js|ts|json|jsonl|md|txt|cmd|html|css|yml|yaml|toml|exe)\b",
            r"#\d+\b",  # issue / item refs
        ]
    ),
    re.DOTALL,
)
_TRAILING_PUNCT = ".,;:!?"

# Words that may legitimately repeat in English; leave them to the model.
_LEGIT_REPEATS = frozenset({"had", "that"})

# "you know" is real speech, not filler, after/before these words.
_YK_KEEP_PREV = frozenset(
    "if do did does don't didn't doesn't as can could would will should whether unless how what that".split()
)
_YK_KEEP_NEXT = frozenset("what how where who whom why which whether if that".split())

_FILLER_RE = re.compile(
    r"(?<![\w'’-])(?:(?i:um+|uh+|hmm+)|[Ee]r)(?![\w'’-])[ \t]*,?"
)
_YOU_KNOW_RE = re.compile(r"(?<![\w'’-])you\s+know(?![\w'’-])[ \t]*,?", re.IGNORECASE)
_PREV_WORD_RE = re.compile(r"([\w'’]+)\W*$")
_NEXT_WORD_RE = re.compile(r"\W*([\w'’]+)")

_BIGRAM_RE = re.compile(r"\b(\w+(?:['’]\w+)?)\s+(\w+(?:['’]\w+)?)\s+\1\s+\2\b(?!['’]\w)", re.IGNORECASE)
_UNIGRAM_RE = re.compile(r"\b(\w+(?:['’]\w+)?)\s+\1\b(?!['’]\w)", re.IGNORECASE)


def mask(text: str) -> tuple[str, list[str]]:
    """Replace protected spans with [[n]] tokens; return (masked, spans)."""
    spans: list[str] = []

    def repl(m: re.Match) -> str:
        span = m.group(0)
        tail = ""
        if not span.startswith("`"):
            stripped = span.rstrip(_TRAILING_PUNCT)
            tail = span[len(stripped):]
            span = stripped
        if not span:
            return m.group(0)
        spans.append(span)
        return f"[[{len(spans) - 1}]]{tail}"

    return _PROTECTED_RE.sub(repl, text), spans


def unmask(text: str, spans: list[str]) -> str:
    def repl(m: re.Match) -> str:
        i = int(m.group(1))
        return spans[i] if i < len(spans) else m.group(0)

    return MASK_RE.sub(repl, text)


# ---- custom words -----------------------------------------------------------

def _norm_phrase(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'")).strip().lower()


def _phrase_pattern(phrase: str) -> str:
    parts = [re.escape(tok).replace("'", "['’]") for tok in phrase.split()]
    return r"\s+".join(parts)


_EDGE_BEFORE = r"(?<![\w'’-])"
_EDGE_AFTER = r"(?![\w-])"


@dataclass(frozen=True)
class _Entry:
    to: str
    except_res: tuple[re.Pattern, ...]


@dataclass(frozen=True)
class CustomWords:
    pattern: re.Pattern | None
    entries: dict[str, _Entry]


def load_custom_words(path: Path = CUSTOM_WORDS_PATH) -> CustomWords:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries: dict[str, _Entry] = {}
    phrases: list[str] = []
    for item in data["replacements"]:
        excepts = tuple(
            re.compile(_EDGE_BEFORE + _phrase_pattern(p) + _EDGE_AFTER, re.IGNORECASE)
            for p in item.get("except_phrases", [])
        )
        entry = _Entry(to=item["to"], except_res=excepts)
        for phrase in item["from"]:
            entries[_norm_phrase(phrase)] = entry
            phrases.append(phrase)
    if not phrases:
        return CustomWords(None, entries)
    phrases.sort(key=lambda p: len(_norm_phrase(p)), reverse=True)  # longest match first
    alternation = "|".join(_phrase_pattern(p) for p in phrases)
    pattern = re.compile(_EDGE_BEFORE + "(?:" + alternation + ")" + _EDGE_AFTER, re.IGNORECASE)
    return CustomWords(pattern, entries)


_DEFAULT_WORDS: CustomWords | None = None


def default_words() -> CustomWords:
    global _DEFAULT_WORDS
    if _DEFAULT_WORDS is None:
        _DEFAULT_WORDS = load_custom_words()
    return _DEFAULT_WORDS


def apply_custom_words(text: str, words: CustomWords) -> str:
    if words.pattern is None:
        return text
    excluded: dict[int, list[tuple[int, int]]] = {}

    def repl(m: re.Match) -> str:
        entry = words.entries[_norm_phrase(m.group(0))]
        if entry.except_res:
            spans = excluded.get(id(entry))
            if spans is None:
                spans = [x.span() for rx in entry.except_res for x in rx.finditer(text)]
                excluded[id(entry)] = spans
            if any(a <= m.start() and m.end() <= b for a, b in spans):
                return m.group(0)
        return entry.to

    return words.pattern.sub(repl, text)


# ---- filler, stutters, spacing ---------------------------------------------

def remove_filler(text: str) -> str:
    text = _FILLER_RE.sub("", text)

    def you_know(m: re.Match) -> str:
        prev = _PREV_WORD_RE.search(text[: m.start()])
        nxt = _NEXT_WORD_RE.match(text, m.end())
        if prev and prev.group(1).lower().replace("’", "'") in _YK_KEEP_PREV:
            return m.group(0)
        if nxt and nxt.group(1).lower() in _YK_KEEP_NEXT:
            return m.group(0)
        return ""

    return _YOU_KNOW_RE.sub(you_know, text)


def collapse_repeats(text: str) -> str:
    def bigram(m: re.Match) -> str:
        if any(ch.isdigit() for ch in m.group(0)):
            return m.group(0)
        return f"{m.group(1)} {m.group(2)}"

    def unigram(m: re.Match) -> str:
        w = m.group(1)
        if any(ch.isdigit() for ch in w) or w.lower() in _LEGIT_REPEATS:
            return m.group(0)
        return w

    prev = None
    while prev != text:
        prev = text
        text = _BIGRAM_RE.sub(bigram, text)
        text = _UNIGRAM_RE.sub(unigram, text)
    return text


def fix_spacing(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r",[ \t]*([.?!])(?!\.)", r"\1", text)  # ", ." left by a removed filler
    text = re.sub(r"[ \t]+([,.?!])", r"\1", text)
    return text


def apply_rules(masked: str, words: CustomWords | None = None) -> str:
    """All rule stages on already-masked text. Leading/trailing whitespace is kept."""
    words = words or default_words()
    core = masked.strip()
    lead = masked[: len(masked) - len(masked.lstrip())]
    trail = masked[len(masked.rstrip()):]
    core = apply_custom_words(core, words)
    core = remove_filler(core)
    core = collapse_repeats(core)
    core = fix_spacing(core).strip()
    return lead + core + trail


def clean(text: str, words: CustomWords | None = None) -> str:
    """Rules-only cleaning of raw text (mask, rules, unmask)."""
    masked, spans = mask(text)
    return unmask(apply_rules(masked, words), spans)
