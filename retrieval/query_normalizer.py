"""
retrieval/query_normalizer.py
──────────────────────────────
Correct misspellings in a query before it is used for retrieval.

Why this is part of routing, not a cosmetic nicety
──────────────────────────────────────────────────
The router decides between documents and general knowledge by reading the top
cross-encoder rerank score. A misspelled document question retrieves worse,
scores lower, and drops out of the document band — so the user is told the
documents do not cover something the documents plainly do cover. Measured on
the live corpus, three of four typo'd in-corpus questions fell through this way.

Nothing here is specific to any document
────────────────────────────────────────
Corrections are drawn from `ingestion.bm25_index.get_vocabulary()` — every word
in the documents currently ingested — which is rebuilt after every ingestion.
Upload a new corpus and the vocabulary follows it with no code change and no
list to maintain. There is deliberately no hand-written dictionary anywhere in
this module: that is what would break the moment real documents arrive.

Two consequences follow from correcting only toward the corpus, and both are
wanted. A correction can only ever move a query toward text the index actually
contains, so it cannot invent a match. And a typo in a word that appears in no
document stays uncorrected — which is correct, because such a question was
never going to be answered from the documents anyway.

Layers, cheapest first
──────────────────────
  1. Edit distance (Damerau-Levenshtein). Ordinary slips: transposed, dropped,
     doubled or mistyped letters. "agnets" → "agents".
  2. Consonant skeleton. Sound-alike misspellings too far off for layer 1,
     where the consonants survive but the vowels do not:
     "diprisiation" → "depreciation".

Both run in memory against a cached index of the vocabulary; there is no model
call and no GPU work. A third, genuinely semantic tier (asking the LLM to
rewrite the query) lives in the RAG agent and is off by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import structlog

from config.settings import get_settings
from ingestion.bm25_index import get_vocabulary, get_vocabulary_epoch

logger = structlog.get_logger(__name__)
settings = get_settings()

# rapidfuzz ships a C implementation of Damerau-Levenshtein. It is present in
# the image transitively, but this module must not break if that ever stops
# being true, so a pure-Python equivalent stands behind it.
try:  # pragma: no cover - exercised by whichever branch is installed
    from rapidfuzz.distance import DamerauLevenshtein as _rapid_damerau
except ImportError:  # pragma: no cover
    _rapid_damerau = None


# Only alphabetic runs are candidates for correction. Everything else in the
# query — digits, punctuation, spacing — is copied through untouched.
_WORD_RE = re.compile(r"[A-Za-z]+")

_VOWELS = frozenset("aeiouy")

# A candidate that differs from the token only by one of these endings is
# rejected. Without this, "write a pyhton function" had "write" corrected to
# "writes" purely because the corpus happened to contain the plural.
_INFLECTION_SUFFIXES = ("s", "es", "ed", "d", "ing")

# Shortest corpus word that may be offered as a correction. Below this, a word
# is within one edit of far too much to be a safe target.
_MIN_CANDIDATE_LENGTH = 3


@dataclass(frozen=True)
class Correction:
    """One substitution made in the query."""
    original: str
    corrected: str
    method: str  # "edit_distance" | "phonetic"


@dataclass(frozen=True)
class NormalizationResult:
    text: str
    corrections: tuple[Correction, ...] = ()
    # Words that are not in the corpus and that no layer could resolve. The RAG
    # agent uses this to decide whether the optional semantic tier is worth
    # trying: a query with no unresolved words has nothing left to fix.
    unresolved: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.corrections)

    def as_log_value(self) -> list[str]:
        """Compact 'before→after' list for the structlog event."""
        return [f"{c.original}→{c.corrected}" for c in self.corrections]


# ── Distance ─────────────────────────────────────────────────────────────────

def _osa_distance(a: str, b: str) -> int:
    """
    Optimal string alignment distance — Damerau-Levenshtein restricted to
    non-overlapping transpositions.

    Only used when rapidfuzz is unavailable. Transposition must count as a
    single edit: "waht" → "what" is one slip a user made once, but two edits
    under plain Levenshtein, which would push it outside the budget.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la

    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + cost)
        prev2, prev = prev, cur
    return prev[lb]


def _distance(a: str, b: str, cutoff: int) -> Optional[int]:
    """Damerau-Levenshtein distance, or None if it exceeds `cutoff`."""
    if _rapid_damerau is not None:
        d = _rapid_damerau.distance(a, b, score_cutoff=cutoff)
        # rapidfuzz returns cutoff + 1 to signal "above the cutoff".
        return None if d > cutoff else int(d)
    d = _osa_distance(a, b)
    return None if d > cutoff else d


# ── Phonetic key ─────────────────────────────────────────────────────────────

def _skeleton(word: str) -> str:
    """
    Reduce a word to the consonants that carry its sound.

    This is not a full Metaphone. It is a deliberately small set of English
    spelling-to-sound rules aimed at the error classes that actually show up
    when people type quickly: wrong vowels, doubled or undoubled consonants,
    and the handful of letters with two spellings for one sound.

        depreciation → dprstn
        diprisiation → dprstn

    Kept narrow on purpose. A looser key collides unrelated words, and a
    confident correction to the wrong word is worse than no correction — which
    is why a skeleton match alone never wins: the caller also requires the two
    words to be within a bounded edit distance of each other.
    """
    w = word.lower()
    out: list[str] = []
    i = 0
    while i < len(w):
        ch = w[i]
        nxt = w[i + 1] if i + 1 < len(w) else ""

        # Digraphs first — they must not be broken up by the single-letter rules.
        if ch == "p" and nxt == "h":
            out.append("f")
            i += 2
            continue
        if ch == "c" and nxt == "k":
            out.append("k")
            i += 2
            continue

        if ch == "c":
            # Soft c before e/i/y ("depreciation"), hard otherwise ("credit").
            out.append("s" if nxt in "eiy" else "k")
        elif ch == "q":
            out.append("k")
        elif ch == "z":
            out.append("s")
        elif ch == "x":
            out.append("ks")
        elif ch == "h":
            pass  # silent often enough that keeping it costs more than it gains
        elif ch in _VOWELS:
            # The first letter is kept even when it is a vowel, so that words
            # are at least anchored on their opening sound.
            if not out and i == 0:
                out.append(ch)
        else:
            out.append(ch)
        i += 1

    # Collapse runs: "committee" and "comittee" must reduce to the same key.
    collapsed: list[str] = []
    for ch in "".join(out):
        if not collapsed or collapsed[-1] != ch:
            collapsed.append(ch)
    return "".join(collapsed)


# ── Vocabulary index ─────────────────────────────────────────────────────────

_index_epoch: int = -1
_by_length: dict[int, list[str]] = {}
_by_skeleton: dict[str, list[str]] = {}

# Ordinary English words, used to tell "the user misspelled something" apart
# from "the documents simply do not discuss this". Populated on first use from
# the reranker's tokenizer and cached for the process lifetime. Tests replace it
# directly.
_known_words_cache: frozenset[str] | None = None


def _known_words() -> frozenset[str]:
    """
    A general English vocabulary, independent of the ingested corpus.

    Correcting only toward the corpus is what makes a correction safe, but it is
    not enough on its own to decide *whether* to correct. On a small corpus most
    ordinary words are absent, and treating every absent word as a misspelling
    turned "tell me a joke" into "well me a joke" — both are real words one edit
    apart, and the corpus contained only the wrong one.

    The reranker's tokenizer is used as the word list because it is already
    loaded, costs nothing extra, and is not derived from our documents. An empty
    set disables the guard, which is the behaviour if the model cannot be
    reached — degrading to the previous behaviour rather than failing a query.
    """
    global _known_words_cache

    if _known_words_cache is not None:
        return _known_words_cache

    if not settings.typo_protect_dictionary_words:
        _known_words_cache = frozenset()
        return _known_words_cache

    try:
        from retrieval.reranker import _get_reranker

        vocabulary = _get_reranker().tokenizer.get_vocab()
        # Whole words only — WordPiece continuations ("##ing") are not words.
        _known_words_cache = frozenset(
            token for token in vocabulary if token.isalpha() and len(token) >= 3
        )
        logger.info("query_normalizer.dictionary_loaded", size=len(_known_words_cache))
    except Exception as e:
        logger.warning("query_normalizer.dictionary_unavailable", error=str(e))
        _known_words_cache = frozenset()

    return _known_words_cache


def _ensure_index() -> None:
    """
    Build the lookup structures for the current vocabulary, once per rebuild.

    Keyed on the BM25 vocabulary epoch, so an ingestion silently invalidates
    this and the next query rebuilds against the new document set. Without that
    link, corrections would keep pointing at the corpus as it was at startup.
    """
    global _index_epoch, _by_length, _by_skeleton

    epoch = get_vocabulary_epoch()
    if epoch == _index_epoch:
        return

    by_length: dict[int, list[str]] = {}
    by_skeleton: dict[str, list[str]] = {}

    for word in get_vocabulary():
        # Candidates are filtered only for being real words, never by the
        # minimum token length: that setting governs which *query* words are
        # risky to correct, not which corpus words are legitimate targets.
        # Filtering both dropped "own" as a candidate and left "owsn" uncorrected.
        if not word.isalpha() or len(word) < _MIN_CANDIDATE_LENGTH:
            continue
        by_length.setdefault(len(word), []).append(word)
        by_skeleton.setdefault(_skeleton(word), []).append(word)

    # Deterministic ordering: two candidates that tie must always resolve the
    # same way, or the same typo gives different answers on different runs.
    for bucket in by_length.values():
        bucket.sort()
    for bucket in by_skeleton.values():
        bucket.sort()

    _by_length, _by_skeleton, _index_epoch = by_length, by_skeleton, epoch
    logger.info(
        "query_normalizer.index_built",
        epoch=epoch,
        vocabulary_size=len(get_vocabulary()),
        skeletons=len(by_skeleton),
    )


def _budget(token: str) -> int:
    """
    Edit-distance allowance for a token of this length.

    Short words are close to everything, so they get one edit at most; longer
    words can afford the configured maximum without matching something unrelated.
    """
    return 1 if len(token) <= 6 else max(1, settings.typo_max_edit_distance)


def _is_inflection(token: str, candidate: str) -> bool:
    """True if the two differ only by a trailing grammatical ending."""
    shorter, longer = sorted((token, candidate), key=len)
    if not longer.startswith(shorter):
        return False
    return longer[len(shorter):] in _INFLECTION_SUFFIXES


def _best_by_edit_distance(token: str) -> Optional[str]:
    budget = _budget(token)
    best: Optional[str] = None
    best_distance = budget + 1

    # Only lengths within the budget can possibly be within the budget.
    for length in range(len(token) - budget, len(token) + budget + 1):
        for candidate in _by_length.get(length, ()):
            if _is_inflection(token, candidate):
                continue
            distance = _distance(token, candidate, best_distance - 1)
            if distance is not None and distance < best_distance:
                best, best_distance = candidate, distance
                if distance == 1:
                    # Buckets are sorted, so the first distance-1 hit is stable.
                    return best
    return best


def _best_by_phonetic(token: str) -> Optional[str]:
    """
    Match on consonant skeleton, then confirm with edit distance.

    The skeleton alone is too blunt to trust — it is designed to ignore vowels,
    so it will happily equate words that merely share a consonant run. Requiring
    the candidate to also be within roughly a third of the token's length in
    edits keeps this to genuine misspellings.
    """
    candidates = _by_skeleton.get(_skeleton(token))
    if not candidates:
        return None

    budget = max(2, len(token) // 3)
    best: Optional[str] = None
    best_distance = budget + 1

    for candidate in candidates:
        if candidate == token or _is_inflection(token, candidate):
            continue
        distance = _distance(token, candidate, best_distance - 1)
        if distance is not None and distance < best_distance:
            best, best_distance = candidate, distance
    return best


# ── Public entry point ───────────────────────────────────────────────────────

def correct_typos(text: str) -> NormalizationResult:
    """
    Correct misspelled words in `text` against the ingested corpus.

    Substitutions are spliced into the original string by character span. The
    query is never rebuilt by joining tokens: doing that dropped the "+" from
    "what is 2 + 2", and the resulting "what is 2 2" scored well enough to be
    routed into the documents — a general question answered from a document set
    that says nothing about it.
    """
    if not settings.typo_correction_enabled or not text:
        return NormalizationResult(text=text)

    vocabulary = get_vocabulary()
    if not vocabulary:
        # No corpus ingested yet, or the index has not finished building.
        return NormalizationResult(text=text)

    _ensure_index()

    corrections: list[Correction] = []
    unresolved: list[str] = []
    pieces: list[str] = []
    cursor = 0

    for match in _WORD_RE.finditer(text):
        token = match.group(0)
        lowered = token.lower()

        if lowered in vocabulary:
            continue  # a word the documents actually use
        if len(lowered) < settings.typo_min_token_length:
            continue  # too short to correct safely
        if lowered in _known_words():
            # A real word the documents happen not to use. Leave it alone —
            # this is a general question, not a misspelling.
            unresolved.append(lowered)
            continue

        replacement = _best_by_edit_distance(lowered)
        method = "edit_distance"

        if replacement is None and settings.typo_phonetic_enabled:
            replacement = _best_by_phonetic(lowered)
            method = "phonetic"

        if replacement is None:
            unresolved.append(lowered)
            continue

        # Carry the original capitalisation across so the query still reads as
        # the user wrote it.
        if token[0].isupper():
            replacement = replacement.capitalize()

        pieces.append(text[cursor:match.start()])
        pieces.append(replacement)
        cursor = match.end()
        corrections.append(
            Correction(original=token, corrected=replacement, method=method)
        )

    if not corrections:
        return NormalizationResult(text=text, unresolved=tuple(unresolved))

    pieces.append(text[cursor:])
    return NormalizationResult(
        text="".join(pieces),
        corrections=tuple(corrections),
        unresolved=tuple(unresolved),
    )
