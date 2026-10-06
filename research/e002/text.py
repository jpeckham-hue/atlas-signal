"""Text preprocessing primitives for Experiment 002 (design sections 2 and 3).

Pure functions over strings and token sequences. Nothing here reads files or
databases. Thresholds come from ``rules``.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

from . import rules

# --- Normalization -----------------------------------------------------------

_CHAR_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
})
_POSSESSIVE = re.compile(r"(?<=[^\W_])'s\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    """NFKC, ASCII quotes and dashes, possessive 's removed, whitespace collapsed.

    Case is preserved.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    text = _POSSESSIVE.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_text(text: str | None) -> str:
    """``clean_text`` followed by casefolding."""
    return clean_text(text).casefold()


# --- Dates and numbers -------------------------------------------------------

_MONTH = (
    "(?:" + "|".join(sorted(rules.MONTH_NAMES + rules.MONTH_ABBREVIATIONS,
                            key=len, reverse=True)) + r")\.?"
)
_DAY = r"(?:3[01]|[12]\d|0?[1-9])(?:st|nd|rd|th)?"
_DATE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        rf"(?<!\w){_DAY}\s+(?:of\s+)?{_MONTH}(?:,?\s+\d{{4}})?(?!\w)",
        rf"(?<!\w){_MONTH}\s+{_DAY}(?:,?\s+\d{{4}})?(?!\w)",
        rf"(?<!\w){_MONTH}\s+\d{{4}}(?!\w)",
        r"(?<!\w)\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}(?!\w)",
        r"(?<!\w)\d{4}-\d{2}-\d{2}(?!\w)",
    )
]

_CURRENCY = "[" + re.escape("".join(rules.CURRENCY_SYMBOLS)) + "]"
_NUMBER = re.compile(
    rf"(?:(?P<cur>{_CURRENCY})\s?|(?<![\w.,]))"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<magnitude>thousand|million|billion|bn|k|m)(?!\w))?"
    r"(?P<pct>\s?%|\s(?:per\s?cent|percent)(?!\w))?"
    r"(?!\w)",
    re.IGNORECASE,
)


def date_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of recognised dates in ``text``, sorted and merged."""
    spans = sorted(m.span() for p in _DATE_PATTERNS for m in p.finditer(text))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


@dataclass(frozen=True, order=True)
class Amount:
    """A normalized number: currency symbol (or None), exact value, unit."""

    currency: str | None
    value: Decimal
    unit: str | None  # "%" or None

    @property
    def key(self) -> str:
        value = format(self.value.normalize(), "f")
        return f"{self.currency or ''}{value}{self.unit or ''}"


def _number_matches(text: str):
    """Yield (match, Amount or None); None marks an excluded number."""
    dates = date_spans(text)
    for m in _NUMBER.finditer(text):
        start, end = m.span("num")
        if any(ds < end and start < de for ds, de in dates):
            yield m, None
            continue
        digits = m.group("num")
        value = Decimal(digits.replace(",", ""))
        currency = m.group("cur")
        magnitude = (m.group("magnitude") or "").casefold()
        percent = m.group("pct") is not None
        if magnitude:
            value *= rules.MAGNITUDES[magnitude]
        plain = not (currency or magnitude or percent)
        is_integer = "." not in digits
        if plain and is_integer and "," not in digits and len(digits) == 4 \
                and rules.YEAR_MIN <= value <= rules.YEAR_MAX:
            yield m, None
        elif plain and is_integer and value < rules.SMALL_INTEGER_LIMIT:
            yield m, None
        else:
            yield m, Amount(currency, value, "%" if percent else None)


def extract_numbers(text: str | None) -> tuple[Amount, ...]:
    """Distinct amounts in ``text`` in order of first appearance.

    Dates, years 1900-2100 without a currency, and bare integers below 100
    without a currency, magnitude or percent are excluded.
    """
    seen: dict[str, Amount] = {}
    for _, amount in _number_matches(normalize_text(text)):
        if amount is not None and amount.key not in seen:
            seen[amount.key] = amount
    return tuple(seen.values())


def distinctive_numbers(per_document: Iterable[Iterable[str]]) -> frozenset[str]:
    """Amount keys occurring in at most NUM_MAX_DF documents."""
    df = document_frequencies(per_document)
    return frozenset(k for k, n in df.items() if n <= rules.NUM_MAX_DF)


# --- Tokenization -------------------------------------------------------------

_WORD = re.compile(r"[^\W_]+")


def raw_tokens(text: str | None, keep_case: bool = False) -> list[str]:
    """Every word and number expression in order; no stopwords removed.

    Number expressions (including excluded ones such as years) are kept whole,
    without internal whitespace. Used for wrappers, sentence keys and title
    templates.
    """
    text = clean_text(text) if keep_case else normalize_text(text)
    tokens: list[str] = []
    pos = 0
    for m, _ in _number_matches(text):
        tokens.extend(_WORD.findall(text[pos:m.start()]))
        tokens.append(re.sub(r"\s+", "", m.group(0)))
        pos = m.end()
    tokens.extend(_WORD.findall(text[pos:]))
    return tokens


def strip_plural(word: str) -> str:
    """Minimal plural stripping (``-ies``→``-y``, ``-es``→``-e``, ``-s``→``""``).

    Applied only when the result keeps at least PLURAL_MIN_STEM characters.
    Words ending in ``-ss`` or ``-us`` are left alone.
    """
    if word.endswith("ies") and not word.endswith(("eies", "aies")):
        result = word[:-3] + "y"
    elif word.endswith("es") and not word.endswith(("aes", "ees", "oes")):
        result = word[:-1]
    elif word.endswith("s") and not word.endswith(("ss", "us")):
        result = word[:-1]
    else:
        return word
    return result if len(result) >= rules.PLURAL_MIN_STEM else word


def aligned_tokens(text: str | None) -> list[tuple[str, str | None]]:
    """``(raw token, content token or None)`` pairs in order.

    The raw tokens equal ``raw_tokens(text)`` and the non-None content tokens
    equal ``tokenize(text)``. Lets token-level suppression found on raw tokens
    be applied to content tokens.
    """
    text = normalize_text(text)
    pairs: list[tuple[str, str | None]] = []
    pos = 0

    def words(segment: str) -> None:
        for w in _WORD.findall(segment):
            content = None if w in rules.STOPWORDS or w.isdigit() else strip_plural(w)
            pairs.append((w, content))

    for m, amount in _number_matches(text):
        words(text[pos:m.start()])
        pairs.append((re.sub(r"\s+", "", m.group(0)), amount.key if amount else None))
        pos = m.end()
    words(text[pos:])
    return pairs


@dataclass(frozen=True)
class TokenKind:
    """One raw token with its content token and number-expression provenance."""

    raw: str
    content: str | None
    number: bool  # produced by the number-expression matcher


def token_kinds(text: str | None) -> list[TokenKind]:
    """Raw tokens with content tokens and number-expression provenance, in order.

    ``raw`` and ``content`` equal those of ``aligned_tokens(text)``. ``number``
    is true exactly for tokens produced by the existing number-expression
    matcher, including numbers that are not extracted as amounts (dates, years,
    small integers such as ``10``). Number words such as ``ten`` are ordinary
    words here.
    """
    text = normalize_text(text)
    kinds: list[TokenKind] = []
    pos = 0

    def words(segment: str) -> None:
        for w in _WORD.findall(segment):
            content = None if w in rules.STOPWORDS or w.isdigit() else strip_plural(w)
            kinds.append(TokenKind(w, content, False))

    for m, amount in _number_matches(text):
        words(text[pos:m.start()])
        kinds.append(TokenKind(re.sub(r"\s+", "", m.group(0)),
                               amount.key if amount else None, True))
        pos = m.end()
    words(text[pos:])
    return kinds


def tokenize(text: str | None) -> list[str]:
    """Content tokens in order: words and amount keys.

    Words are casefolded, stopwords removed, digit-only fragments dropped and
    plurals stripped. Extracted amounts appear as ``Amount.key``; excluded
    numbers are dropped.
    """
    text = normalize_text(text)
    tokens: list[str] = []
    pos = 0

    def words(segment: str) -> None:
        for w in _WORD.findall(segment):
            if w in rules.STOPWORDS or w.isdigit():
                continue
            tokens.append(strip_plural(w))

    for m, amount in _number_matches(text):
        words(text[pos:m.start()])
        if amount is not None:
            tokens.append(amount.key)
        pos = m.end()
    words(text[pos:])
    return tokens


# --- Entity spans -------------------------------------------------------------

_EXCLUDED_NAMES = frozenset(rules.MONTH_NAMES + rules.WEEKDAY_NAMES)
_RUN_JOINER = re.compile(r"[\s\-'&]*")
_SEGMENT_BREAK = re.compile(r"[:;?!|.]|\s-\s")


def _is_capitalized(token: str) -> bool:
    return token[0].isalpha() and token[0].isupper() \
        and token.casefold() not in _EXCLUDED_NAMES


def entity_spans(title: str | None) -> tuple[str, ...]:
    """Capitalized-token runs in a title (case preserved), first occurrence order.

    A run of two or more capitalized tokens is a span. A single capitalized
    token is a span unless it starts the title or a title segment (after
    ``: ; ? ! | .`` or a spaced dash). A stopword that starts a segment is
    capitalized only by position and is dropped from the front of its run.
    Month and weekday names are never part of a span.
    """
    text = clean_text(title)
    matches = list(_WORD.finditer(text))
    runs: list[list[tuple[str, bool]]] = []
    current: list[tuple[str, bool]] = []
    prev_end = 0
    for i, m in enumerate(matches):
        gap = text[prev_end:m.start()]
        initial = i == 0 or bool(_SEGMENT_BREAK.search(gap))
        joined = not initial and _RUN_JOINER.fullmatch(gap) is not None
        if current and not joined:
            runs.append(current)
            current = []
        if _is_capitalized(m.group()):
            current.append((m.group(), initial))
        elif current:
            runs.append(current)
            current = []
        prev_end = m.end()
    if current:
        runs.append(current)

    spans: dict[str, str] = {}
    for run in runs:
        if run[0][1] and run[0][0].casefold() in rules.STOPWORDS:
            run = run[1:]
        if not run:
            continue
        if len(run) == 1 and run[0][1]:
            continue
        span = " ".join(token for token, _ in run)
        spans.setdefault(span.casefold(), span)
    return tuple(spans.values())


# --- Sentences ------------------------------------------------------------------

_SENTENCE_END = re.compile(r"([.!?]+)([\"')\]]*)(\s+)(?=[\"'(\[]?[A-Z0-9])")


def split_sentences(text: str | None) -> list[str]:
    """Split cleaned text into sentences; case preserved.

    A boundary is ``.``, ``!`` or ``?`` (plus closing quotes or brackets) and
    whitespace before an uppercase letter, digit or opening quote. A single
    ``.`` after a listed abbreviation or a single uppercase initial is not a
    boundary.
    """
    text = clean_text(text)
    sentences: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        if m.group(1) == ".":
            before = re.search(r"([^\W_]+)$", text[start:m.start()])
            word = before.group(1) if before else ""
            if word.casefold() in rules.ABBREVIATIONS or \
                    (len(word) == 1 and word.isupper()):
                continue
        sentences.append(text[start:m.end(2)].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return [s for s in sentences if s]


def sentence_key(sentence: str) -> tuple[str, ...]:
    """Normalized identity of a sentence: its raw casefolded tokens."""
    return tuple(raw_tokens(sentence))


# --- Character trigrams -------------------------------------------------------


def char_trigrams(text: str | None) -> frozenset[str]:
    """Character trigrams of the normalized text (whitespace collapsed).

    Text shorter than three characters yields itself as the only element;
    empty text yields an empty set.
    """
    text = normalize_text(text)
    if len(text) < 3:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[i:i + 3] for i in range(len(text) - 2))


def jaccard(a: frozenset | set, b: frozenset | set) -> Fraction:
    """Exact Jaccard similarity; 0 when both sets are empty."""
    union = len(a | b)
    return Fraction(len(a & b), union) if union else Fraction(0)


# --- TF-IDF and cosine ----------------------------------------------------------


def document_frequencies(documents: Iterable[Iterable[str]]) -> Counter:
    """Number of documents containing each token."""
    df: Counter = Counter()
    for doc in documents:
        df.update(set(doc))
    return df


def inverse_document_frequencies(df: Mapping[str, int], n_documents: int) -> dict[str, float]:
    """``ln(N / df)`` for every token, sorted by token."""
    return {t: math.log(n_documents / df[t]) for t in sorted(df)}


def weighted_vector(
    title_tokens: Sequence[str],
    summary_tokens: Sequence[str],
    idf: Mapping[str, float],
    title_weight: int = rules.TITLE_WEIGHT,
    zero_weight: Iterable[str] = (),
) -> dict[str, float]:
    """TF-IDF vector with title occurrences weighted ``title_weight``.

    Tokens without an IDF, with IDF 0, or listed in ``zero_weight`` (for
    example source-common tokens) are omitted. Keys are sorted.
    """
    zero = set(zero_weight)
    tf: Counter = Counter()
    for t in title_tokens:
        tf[t] += title_weight
    for t in summary_tokens:
        tf[t] += 1
    vector = {}
    for t in sorted(tf):
        w = idf.get(t, 0.0)
        if w > 0 and t not in zero:
            vector[t] = tf[t] * w
    return vector


def cosine(u: Mapping[str, float], v: Mapping[str, float]) -> float:
    """Cosine similarity; 0.0 if either vector is empty.

    Uses ``math.fsum`` so the result does not depend on iteration order.
    """
    if not u or not v:
        return 0.0
    dot = math.fsum(u[t] * v[t] for t in u.keys() & v.keys())
    if dot == 0:
        return 0.0
    norm_u = math.sqrt(math.fsum(x * x for x in u.values()))
    norm_v = math.sqrt(math.fsum(x * x for x in v.values()))
    return dot / (norm_u * norm_v)


# --- Corpus-derived suppression -------------------------------------------------


@dataclass(frozen=True)
class Wrappers:
    """Leading and trailing token sequences shared by enough summaries."""

    prefixes: frozenset[tuple[str, ...]]
    suffixes: frozenset[tuple[str, ...]]


def _shared_prefixes(token_lists: Sequence[Sequence[str]]) -> frozenset[tuple[str, ...]]:
    n = len(token_lists)
    found: set[tuple[str, ...]] = set()
    if not n:
        return frozenset()
    groups: dict[tuple[str, ...], list[int]] = {(): list(range(n))}
    length = 0
    while groups:
        extended: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for prefix, members in groups.items():
            for i in members:
                if len(token_lists[i]) > length:
                    extended[prefix + (token_lists[i][length],)].append(i)
        groups = {p: m for p, m in extended.items()
                  if Fraction(len(m), n) >= rules.WRAPPER_MIN_SHARE}
        found.update(groups)
        length += 1
    return frozenset(found)


def detect_wrappers(token_lists: Sequence[Sequence[str]]) -> Wrappers:
    """Feed wrappers for one source's summaries (as ``raw_tokens`` lists).

    A leading (trailing) sequence qualifies when at least WRAPPER_MIN_SHARE of
    the source's summaries start (end) with it.
    """
    reversed_lists = [list(reversed(t)) for t in token_lists]
    suffixes = frozenset(tuple(reversed(s)) for s in _shared_prefixes(reversed_lists))
    return Wrappers(_shared_prefixes(token_lists), suffixes)


def strip_wrappers(tokens: Sequence[str], wrappers: Wrappers) -> list[str]:
    """Remove the longest qualifying prefix and the longest qualifying suffix."""
    tokens = list(tokens)
    p = max((len(x) for x in wrappers.prefixes if tuple(tokens[:len(x)]) == x), default=0)
    s = max((len(x) for x in wrappers.suffixes
             if len(x) <= len(tokens) and tuple(tokens[len(tokens) - len(x):]) == x),
            default=0)
    if p + s >= len(tokens):
        return []
    return tokens[p:len(tokens) - s]


def repeated_sentences(
    documents: Iterable[tuple[str, object, str | None]],
    distinctive: frozenset[str],
) -> dict[str, frozenset[tuple[str, ...]]]:
    """Boilerplate sentence keys per source.

    ``documents`` are ``(source_id, document_key, summary)``. A sentence
    qualifies when it has at least BOILERPLATE_MIN_TOKENS raw tokens, contains
    no distinctive amount, and occurs in at least BOILERPLATE_MIN_ARTICLES
    distinct documents of the same source.
    """
    seen: dict[tuple[str, tuple[str, ...]], set] = defaultdict(set)
    for source_id, doc_key, summary in documents:
        for sentence in split_sentences(summary):
            key = sentence_key(sentence)
            if len(key) < rules.BOILERPLATE_MIN_TOKENS:
                continue
            if {a.key for a in extract_numbers(sentence)} & distinctive:
                continue
            seen[(source_id, key)].add(doc_key)
    result: dict[str, set] = defaultdict(set)
    for (source_id, key), docs in seen.items():
        if len(docs) >= rules.BOILERPLATE_MIN_ARTICLES:
            result[source_id].add(key)
    return {s: frozenset(k) for s, k in sorted(result.items())}


def remove_sentences(text: str | None, keys: frozenset[tuple[str, ...]]) -> str:
    """``text`` without the sentences whose key is in ``keys``."""
    return " ".join(s for s in split_sentences(text) if sentence_key(s) not in keys)


def source_common_tokens(
    documents: Iterable[tuple[str, Iterable[str]]],
) -> dict[str, frozenset[str]]:
    """Tokens in more than SOURCE_COMMON_SHARE of one source's documents.

    ``documents`` are ``(source_id, tokens)``.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    sizes: Counter = Counter()
    for source_id, tokens in documents:
        sizes[source_id] += 1
        counts[source_id].update(set(tokens))
    return {
        s: frozenset(t for t, n in counts[s].items()
                     if Fraction(n, sizes[s]) > rules.SOURCE_COMMON_SHARE)
        for s in sorted(sizes)
    }


# --- Title templates ------------------------------------------------------------


def longest_common_subsequence(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]]:
    """Index pairs of one longest common subsequence, compared casefolded.

    Ties are resolved deterministically (prefer skipping in ``a``).
    """
    fa = [t.casefold() for t in a]
    fb = [t.casefold() for t in b]
    n, m = len(fa), len(fb)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if fa[i] == fb[j] \
                else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if fa[i] == fb[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


@dataclass(frozen=True)
class TemplateComparison:
    shared_share: Fraction
    residual_a: tuple[str, ...]
    residual_b: tuple[str, ...]
    slot_conflict: bool


def _slot_tokens(tokens: Iterable[str]) -> set[str]:
    return {t.casefold() for t in tokens
            if (t[0].isalpha() and t[0].isupper()) or any(c.isdigit() for c in t)}


def compare_title_templates(title_a: str | None, title_b: str | None) -> TemplateComparison:
    """Template skeleton comparison of two titles (design section 3).

    ``slot_conflict`` is true when the longest common token subsequence covers
    at least TEMPLATE_MIN_SHARED of the shorter title and the non-shared parts
    of both titles contain capitalized or numeric tokens that do not overlap.
    Whether the two titles are in the same template scope (same source and
    document type or format class) is the caller's concern.
    """
    a = raw_tokens(title_a, keep_case=True)
    b = raw_tokens(title_b, keep_case=True)
    if not a or not b:
        return TemplateComparison(Fraction(0), tuple(a), tuple(b), False)
    pairs = longest_common_subsequence(a, b)
    in_a = {i for i, _ in pairs}
    in_b = {j for _, j in pairs}
    residual_a = tuple(t for i, t in enumerate(a) if i not in in_a)
    residual_b = tuple(t for j, t in enumerate(b) if j not in in_b)
    share = Fraction(len(pairs), min(len(a), len(b)))
    slots_a, slots_b = _slot_tokens(residual_a), _slot_tokens(residual_b)
    conflict = share >= rules.TEMPLATE_MIN_SHARED and bool(slots_a) and bool(slots_b) \
        and not (slots_a & slots_b)
    return TemplateComparison(share, residual_a, residual_b, conflict)
