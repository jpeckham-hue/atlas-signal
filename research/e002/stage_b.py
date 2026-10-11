"""Experiment 002 Stage B (relationship classification), research only.

Implemented so far: loading and integrity validation of the sealed Stage A
candidate artifact, candidate pair state, S5 steps 1-5 (`copy_of`,
template slot conflict, companion documents, advisory match, `follows_from`
cue), S6 same-event decisions, S7 constrained clustering, S8 container
attachment and S9 membership roles. S10-S11 are not implemented yet.

Specification: docs/experiments/002-event-relationship-baseline.md, sections
5-12 and the Stage B pre-implementation clarifications (C1-C15, R1-R5,
G1-G6, H1-H3, T1, C16-C25). Stage B is candidate-bounded (C1): only pairs in
the sealed Stage A candidate artifact receive decisions.
"""

import hashlib
import json
import re
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
    extract_numbers,
    jaccard,
    normalize_text,
    raw_tokens,
    split_sentences,
    token_kinds,
    tokenize,
    weighted_vector,
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
    s6: tuple[str, ...] | None = None  # S6 threshold branches that fired, if decided

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
    pending: dict[int, "PendingRelation"] = field(default_factory=dict)  # by cue article
    # S7 output, replaced on every apply_clustering call.
    clusters: tuple[tuple[int, ...], ...] = ()     # non-container articles, key-ordered
    cluster_of: dict[int, int] = field(default_factory=dict)  # article -> index in clusters
    edge_dispositions: tuple["EdgeDisposition", ...] = ()     # in processing order
    # S8 output, replaced on every apply_container_attachments call.
    events: tuple["S8Event", ...] = ()             # S7-derived first, by cluster index
    units: tuple["AttachmentUnit", ...] = ()       # in processing order
    heads: dict[int, "HeadEvent"] = field(default_factory=dict)  # by container cue article
    # S9 output, replaced on every apply_membership_roles call and cleared by S8.
    roles: tuple["MembershipRole", ...] = ()      # by event index, then event member order

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


# --- S5 step 5: follows_from cue -----------------------------------------------

FOLLOWS_FROM_CUES = ("after", "following", "in response to", "in the wake of", "prompted by")
_CUE_PATTERNS = [
    (cue, re.compile(r"(?<![^\W_])" + r"\s+".join(re.escape(w) for w in cue.split())
                     + r"(?![^\W_])"))
    for cue in FOLLOWS_FROM_CUES
]
_SPAN_END = re.compile(r";|\s-\s")
TIME_UNITS = frozenset(u + suffix for u in ("second", "minute", "hour", "day", "week", "month",
                                            "year", "decade") for suffix in ("", "s"))
TIME_ARTICLES = frozenset({"a", "an", "the"})
NUMBER_WORDS = frozenset({"one", "two", "three", "four", "five", "six", "seven", "eight",
                          "nine", "ten"})


@dataclass(frozen=True)
class Cue:
    """The first frozen cue in a normalized title and its antecedent span (C6)."""

    phrase: str
    position: int            # index of the cue's first token in token_kinds(title)
    start: int               # character offsets of the cue in the normalized title
    end: int
    antecedent: str          # normalized antecedent span (may be empty)


@dataclass(frozen=True)
class PendingRelation:
    """An article-level `follows_from` claim recorded at S5 (C2), mapped at S10."""

    cue_article: int
    cue: str
    cue_position: int
    cue_start: int
    cue_end: int
    antecedents: tuple[int, ...]   # sorted by (representative time, normalized_url)


def find_first_cue(title: str | None) -> Cue | None:
    """First frozen cue occurrence in the normalized title (R4) and its span (C6).

    Cues match whole tokens (no substring matches such as "afternoon"). The
    antecedent runs from after the cue to the first semicolon, spaced dash or
    end of title; later cues inside it do not end it.
    """
    text = normalize_text(title)
    found = [(m.start(), m.end(), cue) for cue, pattern in _CUE_PATTERNS
             for m in [pattern.search(text)] if m]
    if not found:
        return None
    start, end, cue = min(found)
    rest = text[end:]
    stop = _SPAN_END.search(rest)
    antecedent = (rest[:stop.start()] if stop else rest).strip()
    return Cue(cue, len(token_kinds(text[:start])), start, end, antecedent)


def time_expression_prefix(antecedent: str) -> bool:
    """T1: the span begins with NUMBER TIME_UNIT or ARTICLE TIME_UNIT."""
    kinds = token_kinds(antecedent)
    if len(kinds) < 2 or kinds[1].raw not in TIME_UNITS:
        return False
    first = kinds[0]
    return first.number or first.raw in NUMBER_WORDS or first.raw in TIME_ARTICLES


def clause_vector(text: str, source_id: str, model: CorpusModel) -> dict[str, float]:
    """C3/R3 clause vector: frozen tokenizer and IDF, title weight 1, and the
    given source's source-common tokens at zero weight."""
    return weighted_vector(tokenize(text), [], model.idf, title_weight=1,
                           zero_weight=model.common.get(source_id, frozenset()))


def clause_matches(
    clause_vec: dict[str, float],
    clause_amounts: frozenset[str],
    member_vec: dict[str, float],
    member_amounts: frozenset[str],
    distinctive: frozenset[str],
) -> bool:
    """Frozen clause-match condition (section 7, C3, H3).

    (a) a shared distinctive number plus a shared non-amount content token with
    positive weight in both vectors; or (b) cosine at least
    CONTAINER_CLAUSE_COSINE (compared as its float, the Stage A convention).
    """
    if cosine(clause_vec, member_vec) >= float(rules.CONTAINER_CLAUSE_COSINE):
        return True
    if not (clause_amounts & member_amounts & distinctive):
        return False
    shared = (set(clause_vec) & set(member_vec)) - clause_amounts - member_amounts
    return bool(shared)


def _order_key(article: Article) -> tuple:
    return (article.representative_time, article.normalized_url)


def apply_follows_from(state: StageBState) -> None:
    """S5 step 5 for every cue article, each evaluated independently (C19).

    Only the first cue is evaluated (R4); a time-expression antecedent abstains
    (T1). Eligible partners are non-container (R2) candidate partners whose
    pair is not terminal (C1, H1) and whose representative time is not later
    than the cue article's (G4). Each qualifying partner gets a symmetric
    `follows_from` cannot-link; one pending claim lists all of them. No edge
    is created and no pair becomes terminal.
    """
    partners: dict[int, list[PairState]] = {}
    for pair in state.pairs.values():
        if pair.terminal is None:
            partners.setdefault(pair.a, []).append(pair)
            partners.setdefault(pair.b, []).append(pair)
    features = state.features
    for x in sorted(partners, key=lambda i: _order_key(state.articles[i])):
        cue_article = state.articles[x]
        cue = find_first_cue(cue_article.title)
        if cue is None or not cue.antecedent or time_expression_prefix(cue.antecedent):
            continue
        vec = clause_vector(cue.antecedent, cue_article.source_id, state.model)
        amounts = frozenset(a.key for a in extract_numbers(cue.antecedent))
        matched = []
        for pair in partners[x]:
            y = state.articles[pair.b if pair.a == x else pair.a]
            if y.container_flag or y.representative_time > cue_article.representative_time:
                continue
            if clause_matches(vec, amounts, features[y.id].vector, state.model.numbers[y.id],
                              state.model.distinctive):
                matched.append((pair, y))
        if not matched:
            continue
        for pair, _ in matched:
            pair.cannot_link = "follows_from"
        state.pending[x] = PendingRelation(
            cue_article=x, cue=cue.phrase, cue_position=cue.position, cue_start=cue.start,
            cue_end=cue.end,
            antecedents=tuple(y.id for _, y in sorted(matched, key=lambda m: _order_key(m[1]))))


# --- S6: general same-event rule ---------------------------------------------------

# Threshold branch names, in the order of design section 6.
SAME_EVENT_BRANCHES = ("full_cosine", "number_cosine", "title_cosine")


def title_vector(article_id: int, source_id: str, model: CorpusModel) -> dict[str, float]:
    """C11 title-only vector: the article's committed title tokens, corpus IDF
    and the article's own source-common zero weights. No summary tokens."""
    return weighted_vector(model.title_tokens[article_id], [], model.idf,
                           zero_weight=model.common.get(source_id, frozenset()))


def same_event_branches(
    pair_cosine: float,
    shared_distinctive_number: bool,
    title_cosine: float,
) -> tuple[str, ...]:
    """The S6 threshold branches that fire, in SAME_EVENT_BRANCHES order.

    Thresholds are inclusive and compared as their floats (the Stage A
    convention).
    """
    fired = {
        "full_cosine": pair_cosine >= float(rules.SAME_EVENT_COSINE),
        "number_cosine": shared_distinctive_number
        and pair_cosine >= float(rules.SAME_EVENT_NUMBER_COSINE),
        "title_cosine": title_cosine >= float(rules.SAME_EVENT_TITLE_COSINE),
    }
    return tuple(b for b in SAME_EVENT_BRANCHES if fired[b])


def entity_token_types(entities) -> frozenset[str]:
    """C21: token types of an article's frozen title entity spans, tokenized
    with the committed tokenizer."""
    return frozenset(t for span in entities for t in tokenize(span))


def amount_keys(article: Article, model: CorpusModel) -> frozenset[str]:
    """Amount keys of an article from the committed number machinery.

    The article's extracted amounts (``model.numbers``) plus every amount key
    the number-expression matcher yields on the title and on each summary
    sentence, i.e. on exactly the text units its vector tokens come from.
    """
    units = [article.title, *split_sentences(article.summary)]
    return model.numbers[article.id] | frozenset(
        k.content for text in units for k in token_kinds(text) if k.number and k.content)


def shared_evidence_tokens(
    vec_a: dict[str, float],
    vec_b: dict[str, float],
    excluded: frozenset[str],
) -> tuple[str, ...]:
    """C10/C20: shared token types with positive weight in both full article
    vectors and not in ``excluded`` (amount keys and C21 entity token types of
    either article). Sorted."""
    return tuple(sorted(t for t in vec_a.keys() & vec_b.keys()
                        if vec_a[t] > 0 and vec_b[t] > 0 and t not in excluded))


def same_event_eligible(pair: PairState, a: Article, b: Article) -> bool:
    """S6 eligibility: not terminal after S5, no cannot-link, no container, and
    representative times at most SAME_EVENT_MAX_HOURS apart (inclusive)."""
    if pair.terminal is not None or pair.cannot_link is not None:
        return False
    if a.container_flag or b.container_flag:
        return False
    gap = abs(a.representative_time - b.representative_time)
    return gap <= timedelta(hours=rules.SAME_EVENT_MAX_HOURS)


def apply_same_event(state: StageBState) -> None:
    """S6 over sealed candidate pairs (C1) left eligible after S5.

    A pair decides same-event when at least one threshold branch fires and the
    branch-independent shared-evidence guard holds (C10, C20, C21). It gets
    ``edge = "same_event"``, the fired branches in ``s6`` and exactly one
    same-event edge; it is not made terminal and no cannot-link is created.
    A failing pair is left undecided. A pair that already has an edge is
    skipped, so a rerun changes nothing.
    """
    model = state.model
    for pair in state.pairs.values():
        if pair.edge is not None:
            continue
        a, b = state.articles[pair.a], state.articles[pair.b]
        if not same_event_eligible(pair, a, b):
            continue
        fa, fb = state.features[a.id], state.features[b.id]
        number = bool(model.numbers[a.id] & model.numbers[b.id] & model.distinctive)
        title_cos = cosine(title_vector(a.id, a.source_id, model),
                           title_vector(b.id, b.source_id, model))
        branches = same_event_branches(pair.cosine, number, title_cos)
        if not branches:
            continue
        excluded = (amount_keys(a, model) | amount_keys(b, model)
                    | entity_token_types(fa.entities) | entity_token_types(fb.entities))
        if not shared_evidence_tokens(fa.vector, fb.vector, excluded):
            continue
        pair.edge = "same_event"
        pair.s6 = branches
        state.edges.append(Edge("same_event", pair.a, pair.b, pair.cosine))


# --- S7: constrained clustering ------------------------------------------------------

# C8 edge-type priority.
EDGE_TYPES = ("copy_of", "companion", "advisory", "same_event")


@dataclass(frozen=True)
class Blocker:
    """One failed cross-pair constraint of a rejected merge.

    ``x`` and ``y`` are ordered by article key. ``detail`` is the cannot-link
    source for ``cannot_link`` and the cross-pair cosine for ``cosine_floor``.
    """

    x: int
    y: int
    kind: str                # "cannot_link" or "cosine_floor"
    detail: str | float
    candidate: bool          # whether (x, y) is a sealed candidate pair


@dataclass(frozen=True)
class EdgeDisposition:
    """What S7 did with one driving edge."""

    edge: Edge
    status: str                          # "merged", "already_joined" or "rejected"
    blockers: tuple[Blocker, ...] = ()   # only for "rejected"


def edge_order(edge: Edge, articles: dict[int, Article]) -> tuple:
    """C8/C22 processing key: type priority, cosine descending, then the
    edge's two article keys sorted ascending. No article IDs."""
    keys = tuple(sorted((_order_key(articles[edge.a]), _order_key(articles[edge.b]))))
    return (EDGE_TYPES.index(edge.type), -edge.cosine, keys)


def ordered_edges(state: StageBState) -> list[Edge]:
    """``state.edges`` in C8/C22 order, independent of their incoming order.

    Raises ValueError on malformed state: an unknown edge type, an edge
    involving a container (C9), or more than one edge for a pair.
    """
    seen: set[tuple[int, int]] = set()
    for edge in state.edges:
        if edge.type not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {edge.type!r}")
        if state.articles[edge.a].container_flag or state.articles[edge.b].container_flag:
            raise ValueError(f"edge ({edge.a}, {edge.b}) involves a container")
        key = (min(edge.a, edge.b), max(edge.a, edge.b))
        if key in seen:
            raise ValueError(f"more than one edge for pair {key}")
        seen.add(key)
    return sorted(state.edges, key=lambda e: edge_order(e, state.articles))


def cross_pair_blockers(state: StageBState, x: int, y: int, driving: bool) -> list[Blocker]:
    """Constraint failures of one cross pair (C1, C7, R1).

    Cannot-link applies to every cross pair, the driving pair included: a
    candidate pair's recorded cannot-link, or for a non-candidate pair a
    template slot conflict. Every cross pair except the driving pair must have
    full-vector cosine at least CLUSTER_MIN_CROSS_COSINE (compared as its
    float, the Stage A convention).
    """
    if _order_key(state.articles[y]) < _order_key(state.articles[x]):
        x, y = y, x
    pair = state.pairs.get((min(x, y), max(x, y)))
    candidate = pair is not None
    if candidate:
        source = pair.cannot_link
    else:
        source = SLOT_CONFLICT if slot_conflict(state.articles[x], state.articles[y]) else None
    blockers = []
    if source is not None:
        blockers.append(Blocker(x, y, "cannot_link", source, candidate))
    if not driving:
        value = pair.cosine if candidate else cosine(state.features[x].vector,
                                                     state.features[y].vector)
        if value < float(rules.CLUSTER_MIN_CROSS_COSINE):
            blockers.append(Blocker(x, y, "cosine_floor", value, candidate))
    return blockers


def _blocker_order(state: StageBState, blocker: Blocker) -> tuple:
    return (_order_key(state.articles[blocker.x]), _order_key(state.articles[blocker.y]),
            blocker.kind)


def apply_clustering(state: StageBState) -> None:
    """S7, recomputed from scratch on every call.

    Every non-container article starts as a singleton; containers take no
    part (C9). Edges are processed once each in C8/C22 order. A driving edge
    whose ends are already together is ``already_joined``. Otherwise every
    cross pair of the two components is checked, and the merge is rejected,
    with all blockers recorded, if any fails. Replaces ``clusters``,
    ``cluster_of`` and ``edge_dispositions``; pair states and edges are not
    modified.
    """
    component = {i: frozenset((i,)) for i, a in state.articles.items() if not a.container_flag}
    dispositions = []
    for edge in ordered_edges(state):
        left, right = component[edge.a], component[edge.b]
        if left is right:
            dispositions.append(EdgeDisposition(edge, "already_joined"))
            continue
        blockers = [b for x in left for y in right
                    for b in cross_pair_blockers(state, x, y,
                                                 {x, y} == {edge.a, edge.b})]
        if blockers:
            blockers.sort(key=lambda b: _blocker_order(state, b))
            dispositions.append(EdgeDisposition(edge, "rejected", tuple(blockers)))
            continue
        merged = left | right
        for member in merged:
            component[member] = merged
        dispositions.append(EdgeDisposition(edge, "merged"))

    def member_key(i: int) -> tuple:
        return _order_key(state.articles[i])

    clusters = sorted((tuple(sorted(members, key=member_key))
                       for members in set(component.values())),
                      key=lambda members: [member_key(i) for i in members])
    state.clusters = tuple(clusters)
    state.cluster_of = {i: n for n, members in enumerate(clusters) for i in members}
    state.edge_dispositions = tuple(dispositions)


# --- S8: container attachment ------------------------------------------------------

# C4 split points: semicolon, spaced dash (after normalization) and every frozen
# follows_from cue as a whole token. Colons and unspaced dashes do not split.
_CLAUSE_DELIMITERS = (re.compile(r";"), re.compile(r"\s-\s"),
                      *(pattern for _, pattern in _CUE_PATTERNS))


@dataclass(frozen=True)
class TitlePiece:
    """One piece of a normalized container title between split points (C4)."""

    position: int    # index among all pieces of the title, in title order
    text: str        # normalized, stripped
    end: int         # offset in the normalized title where the next split point starts


def title_pieces(title: str | None) -> list[TitlePiece]:
    """The normalized title cut at every C4 split point; split points removed."""
    text = normalize_text(title)
    spans: list[tuple[int, int]] = []
    for start, end in sorted({m.span() for p in _CLAUSE_DELIMITERS for m in p.finditer(text)}):
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    pieces, pos = [], 0
    for k, (start, end) in enumerate(spans):
        pieces.append(TitlePiece(k, text[pos:start].strip(), start))
        pos = end
    pieces.append(TitlePiece(len(spans), text[pos:].strip(), len(text)))
    return pieces


def title_clauses(title: str | None) -> list[TitlePiece]:
    """Retained C4 title clauses: pieces with at least one content token."""
    return [p for p in title_pieces(title) if tokenize(p.text)]


def head_clause_position(title: str | None) -> int | None:
    """C23: position of the retained clause immediately preceding the first
    cue (R4/C6), or None if there is no cue or that piece is not retained."""
    cue = find_first_cue(title)
    if cue is None:
        return None
    pieces = title_pieces(title)
    before = [p for p in pieces if p.end == cue.start]
    if len(before) != 1:
        raise ValueError("first cue is not a C4 split point")
    return before[0].position if tokenize(before[0].text) else None


@dataclass(frozen=True)
class S8Event:
    """An event after S8. S7-derived events keep their S7 cluster index;
    container-only events are created by an unmatched title clause."""

    members: tuple[int, ...]       # key-ordered, attached containers included
    container_only: bool
    origin: tuple                  # ("s7", cluster index) or ("clause", container, position)


@dataclass(frozen=True)
class QualifiedEvent:
    """An S7 event meeting the clause-match evidence condition for one unit."""

    event: int                     # index into state.events
    member: int                    # qualifying member supplying the similarity
    similarity: float              # highest qualifying member cosine
    blockers: tuple[Blocker, ...]  # cannot-links against current members (R1, G2)


@dataclass(frozen=True)
class AttachmentUnit:
    """S8 outcome of one container title clause or suppressed summary sentence."""

    container: int
    kind: str                      # "title" or "summary"
    position: int                  # title piece position or summary sentence index
    status: str                    # "attached", "blocked", "unmatched_container_only"
                                   # or "unmatched_summary"
    event: int | None              # attached or created event
    member: int | None             # qualifying member of the attached event
    similarity: float | None
    qualified: tuple[QualifiedEvent, ...]   # evidence-qualified events, by event index


@dataclass(frozen=True)
class HeadEvent:
    """C23 head-clause provenance for a container with a pending claim."""

    position: int | None           # head clause position, None if there is none
    event: int | None              # event the head clause resolved to, if any


def _units(article: Article, model: CorpusModel):
    """(kind, position, vector, amount keys) for a container's S8 units.

    Retained title clauses in title order (C4), then suppressed summary
    sentences with content tokens in sentence order (C3), all vectorized with
    title weight 1 and the container's own source-common zero weights.
    """
    zero = model.common.get(article.source_id, frozenset())
    for piece in title_clauses(article.title):
        yield ("title", piece.position, clause_vector(piece.text, article.source_id, model),
               frozenset(a.key for a in extract_numbers(piece.text)))
    sentences = split_sentences(article.summary)
    for k, tokens in enumerate(model.sentence_tokens[article.id]):
        if not tokens:
            continue
        numbers = {t.content for t in token_kinds(sentences[k]) if t.number and t.content}
        yield ("summary", k, weighted_vector(tokens, [], model.idf, title_weight=1,
                                             zero_weight=zero),
               frozenset(numbers & set(tokens)))


def apply_container_attachments(state: StageBState) -> None:
    """S8, recomputed from the S7 clusters on every call.

    Containers are processed in (representative time, normalized_url) order
    (G2), each unit in title-clause then summary-sentence order. An S7 event is
    evidence-qualified when a non-container member that is a sealed candidate
    partner of the container (G6) meets ``clause_matches`` (C3, H3); its similarity
    is the highest such member cosine. It is eligible unless the container has
    a cannot-link with a current member, earlier-attached containers included
    (R1, G2). The best eligible event wins, exact ties going to the smallest
    sorted current-member key (H2, C24). If qualified events exist but all are
    blocked, nothing happens (G3). If none qualifies, a title clause creates its
    own container-only event; a summary sentence creates nothing. Containers
    never merge events. Replaces ``events``, ``units`` and ``heads`` and clears
    the S9 ``roles``, which depend on the events; the S7 output, pair states and
    edges are not modified.
    """
    non_containers = {i for i, a in state.articles.items() if not a.container_flag}
    if set(state.cluster_of) != non_containers:
        raise ValueError("S7 clustering must be applied before S8")
    model, articles = state.model, state.articles
    working = [set(members) for members in state.clusters]
    origins: list[tuple] = [("s7", n) for n in range(len(state.clusters))]
    units: list[AttachmentUnit] = []
    heads: dict[int, HeadEvent] = {}

    def key_of(i: int) -> tuple:
        return _order_key(articles[i])

    containers = sorted((i for i, a in articles.items() if a.container_flag), key=key_of)
    for c in containers:
        resolved: dict[int, int | None] = {}   # title clause position -> event
        for kind, position, vec, amounts in _units(articles[c], model):
            qualified = []
            for n, members in enumerate(state.clusters):
                matches = [(cosine(vec, state.features[m].vector), m) for m in members
                          if (min(c, m), max(c, m)) in state.pairs
                          and clause_matches(vec, amounts, state.features[m].vector,
                                             model.numbers[m], model.distinctive)]
                if not matches:
                    continue
                similarity, member = min(matches, key=lambda s: (-s[0], key_of(s[1])))
                blockers = sorted((b for x in working[n] if x != c
                                   for b in cross_pair_blockers(state, c, x, driving=True)),
                                  key=lambda b: _blocker_order(state, b))
                qualified.append(QualifiedEvent(n, member, similarity, tuple(blockers)))
            eligible = [q for q in qualified if not q.blockers]
            if eligible:
                best = min(eligible, key=lambda q: (-q.similarity,
                                                    sorted(key_of(i) for i in working[q.event])))
                working[best.event].add(c)
                unit = AttachmentUnit(c, kind, position, "attached", best.event, best.member,
                                      best.similarity, tuple(qualified))
            elif qualified:
                unit = AttachmentUnit(c, kind, position, "blocked", None, None, None,
                                      tuple(qualified))
            elif kind == "title":
                working.append({c})
                origins.append(("clause", c, position))
                unit = AttachmentUnit(c, kind, position, "unmatched_container_only",
                                      len(working) - 1, None, None, ())
            else:
                unit = AttachmentUnit(c, kind, position, "unmatched_summary", None, None, None,
                                      ())
            units.append(unit)
            if kind == "title":
                resolved[position] = unit.event
        if c in state.pending:
            head = head_clause_position(articles[c].title)
            heads[c] = HeadEvent(head, None if head is None else resolved[head])

    state.events = tuple(S8Event(tuple(sorted(members, key=key_of)), origin[0] == "clause",
                                 origin)
                         for members, origin in zip(working, origins))
    state.units = tuple(units)
    state.heads = heads
    state.roles = ()


# --- S9: membership roles ----------------------------------------------------------

# Section 8 role cues, matched as frozen (C13, C25).
ANNOUNCEMENT_CUES = ("to make an announcement", "to announce")
_ANNOUNCEMENT_PATTERNS = [
    re.compile(r"(?<![^\W_])" + r"\s+".join(re.escape(w) for w in cue.split()) + r"(?![^\W_])")
    for cue in ANNOUNCEMENT_CUES
]
EXPLAINER_WORDS = frozenset({"why", "how", "what"})
DISCUSSES_FORMATS = frozenset({"audio", "video", "opinion", "programme"})
DISCUSSES_DOCUMENT_TYPES = frozenset({"backgrounders"})


@dataclass(frozen=True)
class MembershipRole:
    """S9 role of one event membership and the role rule that assigned it."""

    event: int                     # index into state.events
    article: int
    role: str                      # "anticipates", "discusses" or "reports"
    rule: str                      # "container", "advisory_document_type",
                                   # "announcement_title", "discusses_format",
                                   # "backgrounder_document_type", "explainer_title"
                                   # or "default"


def membership_role(article: Article) -> tuple[str, str]:
    """(role, rule) for any membership of ``article`` (section 8).

    Every container membership is ``reports`` (R5). Otherwise the C13
    precedence applies: ``anticipates`` (media advisory document type, or a
    whole-token announcement phrase anywhere in the normalized title, C25), then
    ``discusses`` (audio, video, opinion or programme format class, backgrounder
    document type, or a first title word token of why/how/what, C25), then
    ``reports``. Depends only on the article itself.
    """
    if article.container_flag:
        return ("reports", "container")
    if article.document_type == ADVISORY_TYPE:
        return ("anticipates", "advisory_document_type")
    title = normalize_text(article.title)
    if any(p.search(title) for p in _ANNOUNCEMENT_PATTERNS):
        return ("anticipates", "announcement_title")
    if article.format_class in DISCUSSES_FORMATS:
        return ("discusses", "discusses_format")
    if article.document_type in DISCUSSES_DOCUMENT_TYPES:
        return ("discusses", "backgrounder_document_type")
    words = raw_tokens(article.title)
    if words and words[0] in EXPLAINER_WORDS:
        return ("discusses", "explainer_title")
    return ("reports", "default")


def apply_membership_roles(state: StageBState) -> None:
    """S9, recomputed from the S8 events on every call.

    One role per event membership, in event index order and then each event's
    member order; a container gets one ``reports`` record per event it belongs
    to and none if it belongs to no event. Replaces ``roles``; events, units,
    heads, the S7 output, pair states and edges are not modified.
    """
    if (set(state.cluster_of) != {i for i, a in state.articles.items() if not a.container_flag}
            or len(state.events) < len(state.clusters)
            or any(state.events[n].origin != ("s7", n)
                   or not set(members) <= set(state.events[n].members)
                   for n, members in enumerate(state.clusters))):
        raise ValueError("S8 container attachment must be applied before S9")
    state.roles = tuple(MembershipRole(n, i, *membership_role(state.articles[i]))
                        for n, event in enumerate(state.events) for i in event.members)
