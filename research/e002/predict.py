"""Experiment 002 predictor entry point. Stage A (candidates) only.

    python -m research.e002.predict --db PATH --out PATH

Reads only the corpus file. Writes one deterministic JSON artifact containing
article IDs, hashed signal keys and numeric signal values; no titles,
summaries or other publisher text. Refuses to overwrite an existing file.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import rules
from .candidates import (
    DEFAULT_CATCH_ALL_CATEGORIES,
    ROUTES,
    PairSignals,
    build_features,
    candidate_config,
    generate_candidates,
    total_pairs,
)
from .corpus import Corpus, load_corpus

FORMAT = "atlas-signal/e002-stage-a-candidates"
FORMAT_VERSION = 1
DESIGN = "docs/experiments/002-event-relationship-baseline.md"
DESIGN_COMMIT = "1592ca8e6b6fbe810e928438d9d37ac5a999e200"


def key_id(kind: str, key: str) -> str:
    """Stable opaque identifier for a signal key, so no text is written."""
    return hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:16]


def _keyed(kind: str, items: tuple[tuple[str, int], ...]) -> list[dict]:
    return sorted(({"id": key_id(kind, k), "df": df} for k, df in items),
                  key=lambda d: d["id"])


def candidate_record(s: PairSignals) -> dict:
    return {
        "a": s.a,
        "b": s.b,
        "routes": list(s.routes),
        "time_gap_seconds": s.time_gap_seconds,
        "cosine": s.cosine,
        "rank_a_to_b": s.rank_a_to_b,
        "rank_b_to_a": s.rank_b_to_a,
        "numbers": _keyed("number", s.numbers),
        "entities": _keyed("entity", s.entities),
        "tags": _keyed("tag", s.tags),
        "same_issuer_within_hours": s.same_issuer_within_hours,
    }


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, indent=1) + "\n"


def stage_a_artifact(corpus: Corpus, catch_all_categories=DEFAULT_CATCH_ALL_CATEGORIES) -> dict:
    features = build_features(corpus.articles, catch_all_categories)
    candidates = generate_candidates(features)
    config = candidate_config(catch_all_categories)
    config_json = json.dumps(config, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "run": rules.RUN,
        "stage": "A",
        "corpus": {"sha256": corpus.sha256, "article_count": len(corpus.articles)},
        "config": {
            "design": DESIGN,
            "design_commit": DESIGN_COMMIT,
            "sha256": hashlib.sha256(config_json.encode("ascii")).hexdigest(),
            "values": config,
        },
        "routes": list(ROUTES),
        "pair_count_total": total_pairs(len(corpus.articles)),
        "candidate_count": len(candidates),
        "candidates": [candidate_record(s) for s in candidates],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.e002.predict",
                                     description="Experiment 002 Stage A candidate generation.")
    parser.add_argument("--db", required=True, help="corpus SQLite file (opened read-only)")
    parser.add_argument("--out", required=True, help="artifact path; must not already exist")
    parser.add_argument("--expected-sha256", default=rules.CORPUS_SHA256,
                        help="required corpus SHA-256 (default: the frozen Run 4 snapshot)")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        print(f"error: {out} already exists", file=sys.stderr)
        return 2
    corpus = load_corpus(args.db, expected_sha256=args.expected_sha256)
    artifact = stage_a_artifact(corpus)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(artifact))
    print(f"{artifact['candidate_count']} candidates of {artifact['pair_count_total']} pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
