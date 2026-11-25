"""Deterministic guard: aligns the model output with the rules output, repairs it, then checks it.

`verify()` is what the pipeline uses:
  1. align rules-output tokens with model-output tokens (difflib opcodes on normalised keys);
  2. a model INSERTION is dropped; a model DELETION is allowed only if every deleted token is filler,
     a function word or an adjacent repeat, otherwise the deleted run is re-inserted (minus filler);
     a REPLACEMENT is kept only if it is a typo fix (same first letter, edit distance <= 2);
  3. more than REPAIR_LIMIT of the tokens repaired -> reject (rules-only);
  4. the repaired text must still pass `check()`, the strict final guard.
"""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass

from rules import MASK_RE

MIN_KEEP_RATIO = 0.70  # fewer words than this share of the rules output = dropped content
FUZZY_MIN_LETTERS = 4
FUZZY_MAX_DISTANCE = 2
REPAIR_LIMIT = 0.30    # share of tokens inserted+re-inserted above which the model output is not trusted
# A spelling fix keeps its first letter ("wierd" -> "weird"); without this, "also" passes as a
# "typo" of "else" (distance 2) and the model can slip a new word in.

FILLER_WORDS = frozenset("um uh umm er hmm like basically well actually".split())
FILLER_PHRASES = (("you", "know"), ("kind", "of"), ("sort", "of"), ("i", "mean"))
# Reviewed list: articles, conjunctions, pronouns, auxiliaries, prepositions. Not "not"/"no" (they flip meaning).
FUNCTION_WORDS = frozenset("""
a an the
and or but so because if than then while though although as nor yet
i me my mine myself you your yours he him his she her hers it its we us our ours they them their theirs
this that these those there here
am is are was were be been being do does did done have has had having will would can could shall should may might must
of in on at to for with from by about into onto through over under up down out off
i'm i'll i've i'd you're you'll you've you'd he's she's it's we're we'll we've we'd they're they'll they've they'd
that's there's here's let's
""".split())

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_TOKEN_RE = re.compile(r"(\S+)(\s*)")


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class RepairResult:
    ok: bool
    text: str = ""
    reason: str = ""
    inserted: int = 0      # model tokens dropped
    reinserted: int = 0    # source tokens put back


@dataclass
class Tok:
    text: str
    key: str            # alignment key: lowercase letters/digits only ("it's" == "its")
    ws: str = " "
    low: str = ""       # classification form: apostrophes kept, so "we'll" is not the filler "well"


def words(text: str) -> list[str]:
    """Lowercase words, apostrophes removed, protected [[n]] tokens ignored."""
    text = MASK_RE.sub(" ", text)
    return [w.lower().replace("'", "").replace("’", "") for w in _WORD_RE.findall(text)]


def edit_distance(a: str, b: str, limit: int = FUZZY_MAX_DISTANCE) -> int:
    """Levenshtein distance, returning limit+1 as soon as it must exceed limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _same_word(w: str, s: str) -> bool:
    """Equal, or a small spelling fix: 4+ letters, same first letter, edit distance <= 2."""
    if w == s:
        return True
    return (len(w) >= FUZZY_MIN_LETTERS and s[:1] == w[:1]
            and edit_distance(w, s) <= FUZZY_MAX_DISTANCE)


def _is_subsequence(out: list[str], src: list[str]) -> bool:
    """True if every output word can be matched to a source word, in order (deletions allowed).

    The vocabulary check alone passes an inserted "it and" when both words appear elsewhere in
    the text; the model may delete filler but must not add or move words.
    """
    i = 0
    for w in out:
        while i < len(src) and not _same_word(w, src[i]):
            i += 1
        if i == len(src):
            return False
        i += 1
    return True


def check(source: str, candidate: str) -> GuardResult:
    """Strict final guard. `source` is the (masked) rules output; `candidate` the (masked) text to vet."""
    leftovers = MASK_RE.sub("", candidate)
    if "[[" in leftovers or "]]" in leftovers or MASK_RE.findall(source) != MASK_RE.findall(candidate):
        return GuardResult(False, "mask_changed")

    src, out = words(source), words(candidate)
    known = set(src)
    for w in out:
        if w not in known and not any(_same_word(w, s) for s in known):
            return GuardResult(False, "new_word")
    if len(out) > len(src):
        return GuardResult(False, "more_words")
    if len(out) < math.ceil(MIN_KEEP_RATIO * len(src)):
        return GuardResult(False, "dropped_content")
    if not _is_subsequence(out, src):
        return GuardResult(False, "inserted_or_reordered")
    return GuardResult(True)


# ---- align and repair -------------------------------------------------------

def tokenize(text: str) -> list[Tok]:
    return [Tok(m.group(1), _key(m.group(1)), m.group(2) or " ", _low(m.group(1)))
            for m in _TOKEN_RE.finditer(text.strip())]


def _key(token: str) -> str:
    m = MASK_RE.search(token)
    if m:
        return m.group(0)
    core = re.sub(r"[\W_]+", "", token.lower())
    return core or token


def _low(token: str) -> str:
    m = MASK_RE.search(token)
    if m:
        return m.group(0)
    core = re.sub(r"[^\w']+", "", token.lower().replace("’", "'")).strip("_")
    return core or token


def _lows(toks: list[Tok]) -> list[str]:
    """Classification forms, with a clause-final "like" marked as content ("like!").

    "see what it's like." ends a clause, so it is not filler; deleting it leaves "see what it's".
    A "like" counts as clause-final when punctuation is attached, the text ends, or the next word starts
    a sentence (capitalised, as Windows dictation writes it).
    """
    lows = [t.low for t in toks]
    for i, t in enumerate(toks):
        if t.low != "like":
            continue
        nxt = toks[i + 1].text if i + 1 < len(toks) else ""
        sentence_start = nxt[:1].isupper() and nxt != "I" and not nxt.startswith("I'")
        if not nxt or sentence_start or t.text[-1] in ".,;:?!":
            lows[i] = "like!"
    return lows


def _is_mask(key: str) -> bool:
    return bool(MASK_RE.fullmatch(key))


def _is_punct(key: str) -> bool:
    return not any(ch.isalnum() for ch in key) and not _is_mask(key)


def repeat_flags(keys: list[str]) -> list[bool]:
    """Mark tokens that belong to an adjacent repeat of 1-3 tokens ("the the", "more as like more as").

    Fillers between the copies are ignored, so "more as like more as" counts as a repeat.
    """
    idx = [i for i, k in enumerate(keys) if k not in FILLER_WORDS]
    seq = [keys[i] for i in idx]
    flags = [False] * len(keys)
    for n in (3, 2, 1):
        for p in range(n, len(seq) - n + 1):
            if seq[p:p + n] == seq[p - n:p] and not any(_is_mask(k) or _is_punct(k) for k in seq[p:p + n]):
                for q in range(p - n, p + n):
                    flags[idx[q]] = True
    return flags


def _filler_phrase_at(keys: list[str], i: int) -> int:
    for phrase in FILLER_PHRASES:
        if tuple(keys[i:i + len(phrase)]) == phrase:
            return len(phrase)
    return 0


def _droppable(keys: list[str], flags: list[bool], i: int) -> bool:
    """Token i may be deleted without losing content: filler, function word, punctuation or repeat."""
    k = keys[i]
    return k in FILLER_WORDS or k in FUNCTION_WORDS or flags[i] or _is_punct(k)


def _run_is_deletable(keys: list[str], flags: list[bool], i1: int, i2: int) -> bool:
    i = i1
    while i < i2:
        step = _filler_phrase_at(keys, i)
        if step and i + step <= i2:
            i += step
            continue
        if not _droppable(keys, flags, i):
            return False
        i += 1
    return True


def _reinsert(src: list[Tok], flags: list[bool], i1: int, i2: int) -> list[Tok]:
    """Source tokens of a deleted run, minus the filler the model was right to remove.

    A lone "like" is kept only when a content word follows it ("like malicious code");
    before a function word ("like a ...") or another filler it is filler.
    """
    keys = _lows(src)
    kept: list[Tok] = []
    i = i1
    while i < i2:
        step = _filler_phrase_at(keys, i)
        if step:
            i += step
            continue
        k = keys[i]
        nxt = keys[i + 1] if i + 1 < len(keys) else ""
        if k in FILLER_WORDS:
            if k == "like" and nxt and nxt not in FUNCTION_WORDS and nxt not in FILLER_WORDS and not _is_punct(nxt):
                kept.append(Tok(src[i].text, k))
            i += 1
            continue
        if flags[i]:
            i += 1
            continue
        kept.append(Tok(src[i].text, k))
        i += 1
    return kept


def repair(source: str, candidate: str) -> RepairResult:
    """Align `candidate` with `source`; drop insertions, restore deleted content. See module docstring."""
    src, out = tokenize(source), tokenize(candidate)
    if not src or not out:
        return RepairResult(False, reason="empty")
    skeys, okeys = [t.key for t in src], [t.key for t in out]
    slows = _lows(src)
    flags = repeat_flags(slows)
    result: list[Tok] = []
    inserted = reinserted = 0

    def put_back(i1: int, i2: int) -> None:
        nonlocal reinserted
        back = _reinsert(src, flags, i1, i2)
        reinserted += len(back)
        result.extend(back)

    def drop_model(tokens: list[Tok]) -> None:
        nonlocal inserted
        inserted += len(tokens)
        if tokens and "\n" in tokens[-1].ws and result:
            result[-1].ws = tokens[-1].ws  # keep a paragraph break the model put after a dropped word

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, skeys, okeys, autojunk=False).get_opcodes():
        if op == "equal":
            result.extend(out[j1:j2])
        elif op == "insert":
            drop_model(out[j1:j2])
        elif op == "delete":
            if not _run_is_deletable(slows, flags, i1, i2):
                put_back(i1, i2)
        else:  # replace
            pairs_ok = (i2 - i1 == j2 - j1) and all(
                not _is_mask(skeys[i1 + n]) and not _is_mask(okeys[j1 + n]) and _same_word(okeys[j1 + n], skeys[i1 + n])
                for n in range(i2 - i1))
            if pairs_ok:
                result.extend(out[j1:j2])
            else:
                drop_model(out[j1:j2])
                if not _run_is_deletable(slows, flags, i1, i2):
                    put_back(i1, i2)

    fraction = (inserted + reinserted) / max(1, len(src))
    text = "".join(t.text + t.ws for t in result).strip()
    if fraction > REPAIR_LIMIT:
        return RepairResult(False, text, "repair_too_large", inserted, reinserted)
    return RepairResult(True, text, "", inserted, reinserted)


def verify(source: str, candidate: str) -> RepairResult:
    """Repair the model output, then run the strict guard on the repaired text."""
    if not candidate.strip():
        return RepairResult(False, reason="empty")
    repaired = repair(source, candidate)
    if not repaired.ok:
        return repaired
    final = check(source, repaired.text)
    if not final.ok:
        return RepairResult(False, repaired.text, final.reason, repaired.inserted, repaired.reinserted)
    return repaired


def content_keys(text: str) -> list[str]:
    """Content words of a text: not filler, not a function word, not part of an adjacent repeat."""
    toks = tokenize(text)
    keys = _lows(toks)
    flags = repeat_flags(keys)
    out: list[str] = []
    i = 0
    while i < len(keys):
        step = _filler_phrase_at(keys, i)
        if step:
            i += step
            continue
        if not _droppable(keys, flags, i) and not _is_mask(keys[i]):
            out.append(toks[i].key)
        i += 1
    return out
