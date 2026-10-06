"""Experiment 002 Stage B (relationship classification), research only.

Implemented so far: loading and integrity validation of the sealed Stage A
candidate artifact, candidate pair state, and S5 step 1 (`copy_of`).
Later S5 steps and S6-S11 are not implemented yet.

Specification: docs/experiments/002-event-relationship-baseline.md, sections
5-12 and the Stage B pre-implementation clarifications (C1-C15, R1-R5,
G1-G6, H1-H3, T1). Stage B is candidate-bounded (C1): only pairs in the
sealed Stage A candidate artifact receive decisions.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from . import rules
from .candidates import ROUTES, CorpusModel, candidate_config
from .corpus import Article, Corpus, file_sha256
from .predict import DESIGN_COMMIT, FORMAT, FORMAT_VERSION
from .text import char_trigrams, clean_text, cosine, jaccard


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
