"""Stage A candidate generation for Experiment 002 (design sections 2-4).

Recall-oriented: a pair surfaces when its representative times are within
CANDIDATE_MAX_DAYS and at least one signal fires. Nothing here decides a
relationship. Article IDs identify records and order output; they never
influence whether a pair surfaces.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
from typing import Iterable, Sequence

from . import rules
from .corpus import Article
from .text import (
    aligned_tokens,
    cosine,
    detect_wrappers,
    distinctive_numbers,
    document_frequencies,
    entity_spans,
    extract_numbers,
    inverse_document_frequencies,
    repeated_sentences,
    source_common_tokens,
    split_sentences,
    tokenize,
    weighted_vector,
)

# Route names in output order. ``lexical_cosine`` and ``lexical_top_k`` are the
# two branches of the design's ``lexical`` signal.
ROUTES = ("lexical_cosine", "lexical_top_k", "number", "entity", "tag", "issuer")

# Design section 4: catch-all categories excluded from the tag signal.
DEFAULT_CATCH_ALL_CATEGORIES: frozenset[str] = frozenset(rules.CATCH_ALL_CATEGORIES)


def _at_least(value: float, threshold: Fraction) -> bool:
    """Float similarity meets a frozen threshold.

    Similarities are floats, so the threshold is compared as its nearest
    float: a similarity equal to the threshold's float value meets it.
    """
    return value >= float(threshold)


def normalize_category(category: str) -> str:
    return category.strip().casefold()


@dataclass(frozen=True)
class ArticleFeatures:
    id: int
    source_id: str
    time: datetime
    issuing_unit: str | None
    vector: dict[str, float]
    numbers: frozenset[str]   # Amount keys from title and summary
    entities: frozenset[str]  # casefolded title entity spans
    tags: frozenset[str]      # eligible normalized categories


def suppressed_summary_tokens(
    summary: str,
    wrappers,
    boilerplate: frozenset[tuple[str, ...]],
) -> list[str]:
    """Content tokens of a summary after wrapper and boilerplate removal."""
    sentences = [aligned_tokens(s) for s in split_sentences(summary)]
    raw = [r for sentence in sentences for r, _ in sentence]
    keep_from, keep_to = _wrapper_bounds(raw, wrappers)
    tokens, pos = [], 0
    for sentence in sentences:
        removed = tuple(r for r, _ in sentence) in boilerplate
        for _, content in sentence:
            if not removed and keep_from <= pos < keep_to and content is not None:
                tokens.append(content)
            pos += 1
    return tokens


def _wrapper_bounds(raw: Sequence[str], wrappers) -> tuple[int, int]:
    n = len(raw)
    p = max((len(x) for x in wrappers.prefixes if tuple(raw[:len(x)]) == x), default=0)
    s = max((len(x) for x in wrappers.suffixes
             if len(x) <= n and tuple(raw[n - len(x):]) == x), default=0)
    if p + s >= n:
        return 0, 0
    return p, n - s


def _summary_raw_tokens(summary: str) -> list[str]:
    return [r for s in split_sentences(summary) for r, _ in aligned_tokens(s)]


def build_features(
    articles: Sequence[Article],
    catch_all_categories: Iterable[str] = DEFAULT_CATCH_ALL_CATEGORIES,
) -> tuple[ArticleFeatures, ...]:
    """Corpus-level preprocessing (design sections 2 and 3) into features.

    Returns features sorted by article ID.
    """
    articles = sorted(articles, key=lambda a: a.id)
    excluded = {normalize_category(c) for c in catch_all_categories} | set(rules.DOCUMENT_TYPES)

    numbers = {a.id: frozenset(x.key for x in extract_numbers(a.title))
               | frozenset(x.key for x in extract_numbers(a.summary)) for a in articles}
    distinctive = distinctive_numbers(numbers.values())

    by_source: dict[str, list[Article]] = {}
    for a in articles:
        by_source.setdefault(a.source_id, []).append(a)
    wrappers = {s: detect_wrappers([_summary_raw_tokens(a.summary) for a in group])
                for s, group in by_source.items()}
    boilerplate = repeated_sentences(((a.source_id, a.id, a.summary) for a in articles),
                                     distinctive)

    title_tokens = {a.id: tokenize(a.title) for a in articles}
    summary_tokens = {
        a.id: suppressed_summary_tokens(a.summary, wrappers[a.source_id],
                                        boilerplate.get(a.source_id, frozenset()))
        for a in articles
    }
    all_tokens = {a.id: title_tokens[a.id] + summary_tokens[a.id] for a in articles}
    common = source_common_tokens((a.source_id, all_tokens[a.id]) for a in articles)
    idf = inverse_document_frequencies(document_frequencies(all_tokens.values()), len(articles))

    return tuple(
        ArticleFeatures(
            id=a.id,
            source_id=a.source_id,
            time=a.representative_time,
            issuing_unit=a.issuing_unit,
            vector=weighted_vector(title_tokens[a.id], summary_tokens[a.id], idf,
                                   zero_weight=common.get(a.source_id, frozenset())),
            numbers=numbers[a.id],
            entities=frozenset(e.casefold() for e in entity_spans(a.title)),
            tags=frozenset(normalize_category(c) for c in a.categories) - excluded,
        )
        for a in articles
    )


@dataclass(frozen=True)
class PairSignals:
    """Every candidate signal value for one pair (a.id < b.id)."""

    a: int
    b: int
    time_gap_seconds: int
    within_gate: bool
    cosine: float
    rank_a_to_b: int | None   # b's rank among a's gated neighbours
    rank_b_to_a: int | None
    numbers: tuple[tuple[str, int], ...]   # shared distinctive amounts and their DF
    entities: tuple[tuple[str, int], ...]  # shared entity spans with DF <= limit
    tags: tuple[tuple[str, int], ...]      # shared eligible tags with DF <= limit
    same_issuer_within_hours: bool
    routes: tuple[str, ...]

    @property
    def is_candidate(self) -> bool:
        return self.within_gate and bool(self.routes)


class _Context:
    def __init__(self, features: Sequence[ArticleFeatures]):
        self.features = sorted(features, key=lambda f: f.id)
        ids = [f.id for f in self.features]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate article ids")
        self.number_df = document_frequencies(f.numbers for f in self.features)
        self.entity_df = document_frequencies(f.entities for f in self.features)
        self.tag_df = document_frequencies(f.tags for f in self.features)
        self.gate = timedelta(days=rules.CANDIDATE_MAX_DAYS)
        self.cosines: dict[tuple[int, int], float] = {}
        neighbours: dict[int, list[tuple[float, int]]] = {f.id: [] for f in self.features}
        for fa, fb in combinations(self.features, 2):
            c = cosine(fa.vector, fb.vector)
            self.cosines[(fa.id, fb.id)] = c
            if abs(fa.time - fb.time) <= self.gate:
                neighbours[fa.id].append((c, fb.id))
                neighbours[fb.id].append((c, fa.id))
        # Rank = 1 + number of gated neighbours with a strictly higher cosine,
        # so tied neighbours share a rank and no ID tie-break is needed.
        self.ranks: dict[tuple[int, int], int] = {}
        for owner, items in neighbours.items():
            values = [c for c, _ in items]
            for c, other in items:
                self.ranks[(owner, other)] = 1 + sum(1 for v in values if v > c)

    def signals(self, fa: ArticleFeatures, fb: ArticleFeatures) -> PairSignals:
        if fa.id > fb.id:
            fa, fb = fb, fa
        gap = abs(fa.time - fb.time)
        within_gate = gap <= self.gate
        c = self.cosines.get((fa.id, fb.id))
        if c is None:
            c = cosine(fa.vector, fb.vector)
        rank_ab = self.ranks.get((fa.id, fb.id))
        rank_ba = self.ranks.get((fb.id, fa.id))
        numbers = tuple(sorted((k, self.number_df[k]) for k in fa.numbers & fb.numbers
                               if self.number_df[k] <= rules.NUM_MAX_DF))
        entities = tuple(sorted((k, self.entity_df[k]) for k in fa.entities & fb.entities
                                if self.entity_df[k] <= rules.CAND_ENTITY_MAX_DF))
        tags = tuple(sorted((k, self.tag_df[k]) for k in fa.tags & fb.tags
                            if self.tag_df[k] <= rules.CAND_TAG_MAX_DF))
        issuer = fa.issuing_unit is not None and fa.issuing_unit == fb.issuing_unit \
            and gap <= timedelta(hours=rules.CAND_ISSUER_HOURS)
        top_k = _at_least(c, rules.CAND_TOP_K_MIN) and any(
            r is not None and r <= rules.CAND_TOP_K for r in (rank_ab, rank_ba))
        fired = {
            "lexical_cosine": _at_least(c, rules.CAND_COSINE),
            "lexical_top_k": top_k,
            "number": bool(numbers),
            "entity": bool(entities),
            "tag": bool(tags),
            "issuer": issuer,
        }
        return PairSignals(
            a=fa.id, b=fb.id,
            time_gap_seconds=int(gap.total_seconds()),
            within_gate=within_gate,
            cosine=c,
            rank_a_to_b=rank_ab, rank_b_to_a=rank_ba,
            numbers=numbers, entities=entities, tags=tags,
            same_issuer_within_hours=issuer,
            routes=tuple(r for r in ROUTES if fired[r]),
        )


def generate_candidates(features: Sequence[ArticleFeatures]) -> tuple[PairSignals, ...]:
    """All candidate pairs, sorted by (a, b). No relationship is assigned."""
    ctx = _Context(features)
    result = []
    for fa, fb in combinations(ctx.features, 2):
        signals = ctx.signals(fa, fb)
        if signals.is_candidate:
            result.append(signals)
    return tuple(result)


def pair_signals(features: Sequence[ArticleFeatures], a: int, b: int) -> PairSignals:
    """Signal values for any one pair, candidate or not (for diagnostics)."""
    ctx = _Context(features)
    by_id = {f.id: f for f in ctx.features}
    return ctx.signals(by_id[a], by_id[b])


def total_pairs(n: int) -> int:
    return n * (n - 1) // 2


def _decimal(value: Fraction) -> str:
    return str(Decimal(value.numerator) / Decimal(value.denominator))


def candidate_config(catch_all_categories: Iterable[str] = DEFAULT_CATCH_ALL_CATEGORIES) -> dict:
    """The frozen values that determine Stage A output."""
    return {
        "TITLE_WEIGHT": rules.TITLE_WEIGHT,
        "NUM_MAX_DF": rules.NUM_MAX_DF,
        "WRAPPER_MIN_SHARE": _decimal(rules.WRAPPER_MIN_SHARE),
        "BOILERPLATE_MIN_TOKENS": rules.BOILERPLATE_MIN_TOKENS,
        "BOILERPLATE_MIN_ARTICLES": rules.BOILERPLATE_MIN_ARTICLES,
        "SOURCE_COMMON_SHARE": _decimal(rules.SOURCE_COMMON_SHARE),
        "CANDIDATE_MAX_DAYS": rules.CANDIDATE_MAX_DAYS,
        "CAND_COSINE": _decimal(rules.CAND_COSINE),
        "CAND_TOP_K": rules.CAND_TOP_K,
        "CAND_TOP_K_MIN": _decimal(rules.CAND_TOP_K_MIN),
        "CAND_ENTITY_MAX_DF": rules.CAND_ENTITY_MAX_DF,
        "CAND_TAG_MAX_DF": rules.CAND_TAG_MAX_DF,
        "CAND_ISSUER_HOURS": rules.CAND_ISSUER_HOURS,
        "DOCUMENT_TYPES": list(rules.DOCUMENT_TYPES),
        "CATCH_ALL_CATEGORIES": sorted(normalize_category(c) for c in catch_all_categories),
        "STOPWORDS": sorted(rules.STOPWORDS),
    }
