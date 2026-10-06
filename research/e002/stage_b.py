"""Experiment 002 Stage B (relationship classification), research only.

Implemented so far: loading and integrity validation of the sealed Stage A
candidate artifact, candidate pair state, and S5 steps 1-4 (`copy_of`,
template slot conflict, companion documents, advisory match). S5 step 5
(`follows_from` cue) and S6-S11 are not implemented yet.

Specification: docs/experiments/002-event-relationship-baseline.md, sections
5-12 and the Stage B pre-implementation clarifications (C1-C15, R1-R5,
G1-G6, H1-H3, T1, C16-C18). Stage B is candidate-bounded (C1): only pairs in the
sealed Stage A candidate artifact receive decisions.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from . import rules
from .candidates import ROUTES, CorpusModel, candidate_config
from .corpus import Article, Corpus, file_sha256, template_scope
from .predict import DESIGN_COMMIT, FORMAT, FORMAT_VERSION
from .text import (
    char_trigrams,
    clean_text,
    compare_title_templates,
    cosine,
    document_frequencies,
    jaccard,
    token_kinds,
)


class StageBInputError(ValueError):
    """The Stage A candidate artifact cannot be used as Stage B input."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StageBInputError(message)


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def stage_a_config_sha256() -> str:
    """Configuration hash of the committed Stage A candidate configuration,
    computed exactly as the Stage A artifact computes it."""
    text = json.dumps(candidate_config(), sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


# --- Pair state ---------------------------------------------------------------


@dataclass
class PairState:
    """Stage B state of one sealed candidate pair, keyed (a, b) with a < b.

    Fields are filled by later phases; ``None`` means "not decided".
    """

    a: int
    b: int
    cosine: float
    routes: tuple[str, ...]
    terminal: str | None = None      # terminal S5 decision, e.g. "copy_of"
    edge: str | None = None          # structural edge type, if one was created
    cannot_link: str | None = None   # cannot-link source, if any
    s6: str | None = None            # S6 branch, if any

    @property
    def key(self) -> tuple[int, int]:
        return (self.a, self.b)


@dataclass(frozen=True)
class Edge:
    """A structural or same-event edge for later clustering."""

    type: str
    a: int
    b: int
    cosine: float


@dataclass
class StageBState:
    corpus: Corpus
    model: CorpusModel
    pairs: dict[tuple[int, int], PairState]   # sorted by (a, b)
    edges: list[Edge] = field(default_factory=list)

    def __post_init__(self):
        self.articles: dict[int, Article] = {a.id: a for a in self.corpus.articles}
        self.features = {f.id: f for f in self.model.features}


# --- Sealed Stage A candidate artifact ----------------------------------------

_RECORD_KEYS = {"a", "b", "routes", "time_gap_seconds", "cosine", "rank_a_to_b",
                "rank_b_to_a", "numbers", "entities", "tags", "same_issuer_within_hours"}


def validate_candidate_artifact(
    artifact: dict,
    corpus: Corpus,
    model: CorpusModel,
) -> dict[tuple[int, int], PairState]:
    """Validate a parsed Stage A artifact against the corpus and model.

    Returns pair states sorted by (a, b). Raises StageBInputError on any
    mismatch.
    """
    _require(isinstance(artifact, dict), "candidate artifact is not an object")
    _require(artifact.get("format") == FORMAT, "unexpected candidate artifact format")
    _require(artifact.get("format_version") == FORMAT_VERSION,
             "unexpected candidate artifact version")
    _require(artifact.get("run") == rules.RUN, "candidate artifact is not this run")
    _require(artifact.get("stage") == "A", "candidate artifact is not Stage A")

    identity = artifact.get("corpus")
    _require(isinstance(identity, dict), "candidate artifact has no corpus identity")
    _require(identity.get("sha256") == corpus.sha256,
             "candidate artifact corpus SHA-256 does not match the corpus")
    n = len(corpus.articles)
    _require(identity.get("article_count") == n, "candidate artifact article count mismatch")
    _require(artifact.get("pair_count_total") == n * (n - 1) // 2,
             "candidate artifact pair count mismatch")

    config = artifact.get("config")
    _require(isinstance(config, dict), "candidate artifact has no configuration")
    _require(config.get("design_commit") == DESIGN_COMMIT,
             "candidate artifact design commit mismatch")
    _require(config.get("values") == json.loads(json.dumps(candidate_config())),
             "candidate artifact configuration differs from the committed configuration")
    _require(config.get("sha256") == stage_a_config_sha256(),
             "candidate artifact configuration hash mismatch")
    _require(artifact.get("routes") == list(ROUTES), "candidate artifact route list mismatch")

    records = artifact.get("candidates")
    _require(isinstance(records, list), "candidate artifact candidates is not a list")
    _require(artifact.get("candidate_count") == len(records),
             "candidate artifact candidate count mismatch")

    features = {f.id: f for f in model.features}
    _require(set(features) == {a.id for a in corpus.articles},
             "corpus model does not match the corpus")
    pairs: dict[tuple[int, int], PairState] = {}
    for record in records:
        _require(isinstance(record, dict) and set(record) == _RECORD_KEYS,
                 "candidate record has unexpected fields")
        a, b = record["a"], record["b"]
        _require(_is_id(a) and _is_id(b) and a < b, f"invalid candidate pair ({a!r}, {b!r})")
        _require(a in features and b in features, f"candidate ({a}, {b}) has an unknown article")
        _require((a, b) not in pairs, f"duplicate candidate pair ({a}, {b})")
        routes = record["routes"]
        _require(isinstance(routes, list) and routes and len(set(routes)) == len(routes)
                 and set(routes) <= set(ROUTES), f"candidate ({a}, {b}) has invalid routes")
        stored = record["cosine"]
        _require(isinstance(stored, float) and stored == cosine(features[a].vector,
                                                                features[b].vector),
                 f"candidate ({a}, {b}) cosine differs from the recomputed value")
        pairs[(a, b)] = PairState(a=a, b=b, cosine=stored, routes=tuple(routes))
    return dict(sorted(pairs.items()))


def load_candidate_artifact(
    path,
    expected_sha256: str,
    corpus: Corpus,
    model: CorpusModel,
) -> dict[tuple[int, int], PairState]:
    """Verify the artifact file's SHA-256, then parse and validate it."""
    actual = file_sha256(path)
    _require(actual == expected_sha256,
             f"candidate artifact SHA-256 {actual} does not match {expected_sha256}")
    with open(Path(path), "rb") as fh:
        raw = fh.read()
    try:
        artifact = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageBInputError(f"candidate artifact is not valid JSON ({exc})") from exc
    return validate_candidate_artifact(artifact, corpus, model)


# --- S5 step 1: copy_of ---------------------------------------------------------


def _type_key(article: Article) -> tuple[str, str]:
    """Document type, or format class where no document type exists (section 5)."""
    if article.document_type is not None:
        return ("document_type", article.document_type)
    return ("format_class", article.format_class)


def copy_of_qualifies(a: Article, b: Article, model: CorpusModel) -> bool:
    """Frozen `copy_of` rule (design section 5); every condition is required."""
    if a.publisher_domain != b.publisher_domain:
        return False
    if _type_key(a) != _type_key(b):
        return False
    if abs(a.representative_time - b.representative_time) > timedelta(hours=rules.COPY_MAX_HOURS):
        return False
    if min(len(clean_text(a.summary)), len(clean_text(b.summary))) < rules.COPY_MIN_SUMMARY_CHARS:
        return False
    if (model.numbers[a.id] & model.distinctive) != (model.numbers[b.id] & model.distinctive):
        return False
    if jaccard(char_trigrams(a.title), char_trigrams(b.title)) < rules.COPY_TITLE_JACCARD:
        return False
    return jaccard(char_trigrams(a.summary), char_trigrams(b.summary)) >= rules.COPY_SUMMARY_JACCARD


def apply_copy_of(state: StageBState) -> None:
    """S5 step 1. A qualifying pair becomes terminal `copy_of`.

    A non-container pair also gets a `copy_of` structural edge. A pair
    involving a container gets the terminal decision but no edge (C9).
    """
    for pair in state.pairs.values():
        if pair.terminal is not None:
            continue
        a, b = state.articles[pair.a], state.articles[pair.b]
        if not copy_of_qualifies(a, b, state.model):
            continue
        pair.terminal = "copy_of"
        if not (a.container_flag or b.container_flag):
            pair.edge = "copy_of"
            state.edges.append(Edge("copy_of", pair.a, pair.b, pair.cosine))


# --- S5 step 2: template slot conflict -------------------------------------------

SLOT_CONFLICT = rules.DISTINCT_RULES[0]  # "template_slot_conflict"


def slot_conflict(a: Article, b: Article) -> bool:
    """Frozen template slot conflict (design section 3) for any two articles.

    Same template scope (source, and document type or else format class) and
    ``compare_title_templates`` reports a slot conflict. Creates nothing; it
    only states whether the condition holds.
    """
    return template_scope(a) == template_scope(b) and \
        compare_title_templates(a.title, b.title).slot_conflict


def apply_slot_conflict(state: StageBState) -> None:
    """S5 step 2. A non-terminal candidate pair with a slot conflict becomes
    terminal ``template_slot_conflict`` with a cannot-link and no edge."""
    for pair in state.pairs.values():
        if pair.terminal is not None:
            continue
        if slot_conflict(state.articles[pair.a], state.articles[pair.b]):
            pair.terminal = SLOT_CONFLICT
            pair.cannot_link = SLOT_CONFLICT


def slot_conflict_cannot_link(state: StageBState, x: int, y: int) -> bool:
    """Whether articles x and y have a slot-conflict cannot-link (after S5).

    For a sealed candidate pair this is the S5 decision, so `copy_of`
    precedence holds. Any other pair is checked directly (C1: non-candidate
    pairs may only block); no pair state or prediction is created for it.
    """
    key = (min(x, y), max(x, y))
    if key in state.pairs:
        return state.pairs[key].cannot_link == SLOT_CONFLICT
    return slot_conflict(state.articles[x], state.articles[y])


# --- S5 step 3: companion documents --------------------------------------------

COMPANION_TYPES = frozenset({"news releases", "backgrounders"})


def companion_qualifies(a: Article, b: Article, pair_cosine: float, model: CorpusModel) -> bool:
    """Frozen companion rule (design section 6, S5 step 3).

    Same (non-empty) issuing unit; one news release and one backgrounder;
    representative times at most COMPANION_MAX_MINUTES apart; and cosine at
    least COMPANION_MIN_COSINE (compared as its float, the Stage A convention)
    or a shared distinctive number.
    """
    if a.issuing_unit is None or a.issuing_unit != b.issuing_unit:
        return False
    if {a.document_type, b.document_type} != COMPANION_TYPES:
        return False
    if abs(a.representative_time - b.representative_time) > \
            timedelta(minutes=rules.COMPANION_MAX_MINUTES):
        return False
    if pair_cosine >= float(rules.COMPANION_MIN_COSINE):
        return True
    return bool(model.numbers[a.id] & model.numbers[b.id] & model.distinctive)


def apply_companion(state: StageBState) -> None:
    """S5 step 3, sealed candidate pairs only (C1). A non-terminal qualifying
    pair becomes terminal ``companion``. A pair of two non-containers also gets
    one companion edge; a pair involving a container gets none (C9, C16)."""
    for pair in state.pairs.values():
        if pair.terminal is not None:
            continue
        a, b = state.articles[pair.a], state.articles[pair.b]
        if not companion_qualifies(a, b, pair.cosine, state.model):
            continue
        pair.terminal = "companion"
        if not (a.container_flag or b.container_flag):
            pair.edge = "companion"
            state.edges.append(Edge("companion", pair.a, pair.b, pair.cosine))


# --- S5 step 4: advisory match ------------------------------------------------

ADVISORY_TYPE = "media advisories"


def _eligible_trigrams(text: str | None) -> set[tuple[str, str, str]]:
    """Three consecutive raw tokens of one text, none a stopword or a number
    expression (C12). One text is one sequence domain."""
    kinds = token_kinds(text)
    trigrams = set()
    for i in range(len(kinds) - 2):
        window = kinds[i:i + 3]
        if all(not k.number and k.raw not in rules.STOPWORDS for k in window):
            trigrams.add(tuple(k.raw for k in window))
    return trigrams


def rare_sequences(articles) -> dict[int, frozenset[tuple[str, str, str]]]:
    """Per article, its eligible trigrams with article DF <= CAND_ENTITY_MAX_DF.

    Title and unsuppressed summary are separate domains (no sequence spans the
    title-summary boundary); an article's sequences are the union of both. DF
    counts articles containing the complete trigram, over all given articles.
    The threshold is CAND_ENTITY_MAX_DF by design: section 6 and the frozen
    configuration table assign that constant to this rule (C12 restates 5).
    """
    per_article = {a.id: _eligible_trigrams(a.title) | _eligible_trigrams(a.summary)
                   for a in articles}
    df = document_frequencies(per_article.values())
    return {i: frozenset(t for t in seqs if df[t] <= rules.CAND_ENTITY_MAX_DF)
            for i, seqs in per_article.items()}


def advisory_roles(a: Article, b: Article) -> tuple[Article, Article] | None:
    """(advisory, document) if the pair meets the non-anchor advisory conditions.

    Exactly one media advisory; same non-empty issuing unit; the document is
    strictly later than the advisory and at most ADVISORY_MAX_DAYS after it.
    """
    if (a.document_type == ADVISORY_TYPE) == (b.document_type == ADVISORY_TYPE):
        return None
    adv, doc = (a, b) if a.document_type == ADVISORY_TYPE else (b, a)
    if adv.issuing_unit is None or adv.issuing_unit != doc.issuing_unit:
        return None
    gap = doc.representative_time - adv.representative_time
    if gap <= timedelta(0) or gap > timedelta(days=rules.ADVISORY_MAX_DAYS):
        return None
    return adv, doc


def advisory_anchor_types(
    a: Article,
    b: Article,
    pair_cosine: float,
    model: CorpusModel,
    sequences: dict[int, frozenset],
) -> frozenset[str]:
    """Anchor types present for the pair (C17: each type counts at most once)."""
    features = {f.id: f for f in model.features}
    present = set()
    if model.numbers[a.id] & model.numbers[b.id] & model.distinctive:
        present.add("number")
    if sequences[a.id] & sequences[b.id]:
        present.add("sequence")
    if pair_cosine >= float(rules.ADVISORY_MIN_COSINE):
        present.add("cosine")
    if features[a.id].entities & features[b.id].entities:
        present.add("entity")
    return frozenset(present)


def advisory_qualifies(
    a: Article,
    b: Article,
    pair_cosine: float,
    model: CorpusModel,
    sequences: dict[int, frozenset],
) -> bool:
    """Non-anchor conditions plus at least ADVISORY_MIN_ANCHORS distinct anchor
    types. The entity type contributes at most ADVISORY_MAX_ENTITY_ANCHORS."""
    if advisory_roles(a, b) is None:
        return False
    types = advisory_anchor_types(a, b, pair_cosine, model, sequences)
    count = len(types - {"entity"}) + min(len(types & {"entity"}),
                                          rules.ADVISORY_MAX_ENTITY_ANCHORS)
    return count >= rules.ADVISORY_MIN_ANCHORS


def apply_advisory(state: StageBState) -> None:
    """S5 step 4 over non-terminal sealed candidate pairs (C1, H1).

    All qualifying pairs are collected first. A qualifying pair whose advisory
    has more than one qualifying document, or whose document has more than one
    qualifying advisory, becomes terminal ``advisory_abstain`` (unresolved, no
    edge, no cannot-link). Every other qualifying pair becomes terminal
    ``advisory``, with one advisory edge unless it involves a container (C18).
    """
    sequences = rare_sequences(state.corpus.articles)
    qualifying = []
    for pair in state.pairs.values():
        if pair.terminal is not None:
            continue
        a, b = state.articles[pair.a], state.articles[pair.b]
        if advisory_qualifies(a, b, pair.cosine, state.model, sequences):
            adv, doc = advisory_roles(a, b)
            qualifying.append((pair, adv.id, doc.id))
    per_advisory = Counter(adv for _, adv, _ in qualifying)
    per_document = Counter(doc for _, _, doc in qualifying)
    for pair, adv, doc in qualifying:
        if per_advisory[adv] > 1 or per_document[doc] > 1:
            pair.terminal = "advisory_abstain"
            continue
        pair.terminal = "advisory"
        a, b = state.articles[pair.a], state.articles[pair.b]
        if not (a.container_flag or b.container_flag):
            pair.edge = "advisory"
            state.edges.append(Edge("advisory", pair.a, pair.b, pair.cosine))
