"""Experiment 002 scorer. Stage A (candidate generation) only.

    python -m research.e002.score stage-a --candidates PATH --gold PATH --out PATH

Reads exactly two inputs, both named explicitly: a sealed Stage A candidate
artifact and a gold file. It never reads the corpus, never regenerates
candidates and never modifies its inputs. Identity (artifact format, corpus
hash, configuration hash, optional expected artifact hash) is verified before
any metric is computed. Output contains article IDs, gold item IDs, labels and
recorded route names only; no publisher text.

Metric definitions follow docs/experiments/002-event-relationship-baseline.md
section 13 and the Run 1 success criteria. The primary evaluation unit is all
unique gold pairs across Pass 1 and Pass 2 (``unique_pairs`` and
``criteria``); ``by_pass`` and ``occurrence_weighted`` are secondary
diagnostics.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

from . import rules
from .candidates import ROUTES, candidate_config

SCORE_FORMAT = "atlas-signal/e002-stage-a-score"
SCORE_FORMAT_VERSION = 1
CANDIDATE_FORMAT = "atlas-signal/e002-stage-a-candidates"
GOLD_FORMAT = "atlas-signal/event-relationship-gold"

LABELS = ("copy_of", "same_event", "linked", "distinct", "unresolved", "not_scored")
POSITIVE_LABELS = ("copy_of", "same_event", "linked")
CONSTRAINTS = ("not_same_event",)

# Run 1 Stage A success criteria (design "Success criteria for Run 1").
MIN_RECALL = Fraction(9, 10)
MAX_POOL_SHARE = Fraction(5, 100)


class ScoreInputError(ValueError):
    """An input is malformed or does not match the expected identity."""


def file_sha256(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _config_sha256(values: dict) -> str:
    text = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def committed_config_sha256() -> str:
    """Configuration hash of the committed Run 1 candidate configuration."""
    return _config_sha256(json.loads(json.dumps(candidate_config())))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScoreInputError(message)


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


# --- Inputs ----------------------------------------------------------------------


def parse_candidates(
    artifact: dict,
    expected_corpus_sha256: str,
    expected_config_sha256: str,
) -> tuple[dict[tuple[int, int], tuple[str, ...]], dict]:
    """Validate a Stage A artifact. Returns ({pair: routes}, identity)."""
    _require(isinstance(artifact, dict), "candidate artifact is not an object")
    _require(artifact.get("format") == CANDIDATE_FORMAT, "unexpected candidate artifact format")
    _require(artifact.get("format_version") == 1, "unexpected candidate artifact version")
    _require(artifact.get("stage") == "A", "candidate artifact is not Stage A")
    corpus = artifact.get("corpus")
    _require(isinstance(corpus, dict), "candidate artifact has no corpus identity")
    _require(corpus.get("sha256") == expected_corpus_sha256,
             "candidate artifact corpus SHA-256 does not match the expected corpus")
    n = corpus.get("article_count")
    _require(_is_id(n), "candidate artifact article_count is invalid")
    config = artifact.get("config")
    _require(isinstance(config, dict) and isinstance(config.get("values"), dict),
             "candidate artifact has no configuration")
    _require(config.get("sha256") == _config_sha256(config["values"]),
             "candidate artifact configuration hash does not match its values")
    _require(config["sha256"] == expected_config_sha256,
             "candidate artifact configuration is not the expected configuration")
    _require(artifact.get("pair_count_total") == n * (n - 1) // 2,
             "pair_count_total does not match article_count")
    routes_known = artifact.get("routes")
    _require(routes_known == list(ROUTES), "candidate artifact route list is not the expected one")
    records = artifact.get("candidates")
    _require(isinstance(records, list), "candidates is not a list")
    _require(artifact.get("candidate_count") == len(records),
             "candidate_count does not match the number of candidates")

    pairs: dict[tuple[int, int], tuple[str, ...]] = {}
    for record in records:
        _require(isinstance(record, dict), "candidate record is not an object")
        a, b = record.get("a"), record.get("b")
        _require(_is_id(a) and _is_id(b) and a != b, f"invalid candidate pair {a!r}, {b!r}")
        _require(a <= n and b <= n, f"candidate pair ({a}, {b}) has an unknown article id")
        routes = record.get("routes")
        _require(isinstance(routes, list) and routes and set(routes) <= set(ROUTES)
                 and len(set(routes)) == len(routes),
                 f"candidate ({a}, {b}) has invalid routes")
        key = (min(a, b), max(a, b))
        _require(key not in pairs, f"duplicate candidate pair {key}")
        pairs[key] = tuple(r for r in ROUTES if r in routes)
    identity = {
        "corpus_sha256": corpus["sha256"],
        "config_sha256": config["sha256"],
        "article_count": n,
        "pair_count_total": artifact["pair_count_total"],
        "candidate_count": len(records),
    }
    return pairs, identity


def parse_gold(gold: dict, article_count: int) -> dict[tuple[int, int], dict]:
    """Validate the gold and collapse it to unique pairs.

    Each unique pair records its label, constraint, alt_ok, the passes and
    item IDs it appears in, and its occurrence count. Repeated pairs must agree
    on label, constraint and alt_ok.
    """
    _require(isinstance(gold, dict), "gold is not an object")
    _require(gold.get("format") == GOLD_FORMAT, "unexpected gold format")
    _require(gold.get("format_version") == 1, "unexpected gold version")
    items = gold.get("items")
    _require(isinstance(items, list), "gold items is not a list")
    pairs: dict[tuple[int, int], dict] = {}
    for item in items:
        _require(isinstance(item, dict) and isinstance(item.get("id"), str),
                 "gold item without a string id")
        _require(item.get("pass") in (1, 2), f"gold item {item.get('id')} has an invalid pass")
        _require(isinstance(item.get("pairs"), list), f"gold item {item['id']} has no pairs list")
        for p in item["pairs"]:
            _require(isinstance(p, dict), f"gold item {item['id']}: pair is not an object")
            a, b, label = p.get("a"), p.get("b"), p.get("label")
            _require(_is_id(a) and _is_id(b) and a != b,
                     f"gold item {item['id']}: invalid pair {a!r}, {b!r}")
            _require(a <= article_count and b <= article_count,
                     f"gold item {item['id']}: pair ({a}, {b}) has an unknown article id")
            _require(label in LABELS, f"gold item {item['id']}: unknown label {label!r}")
            constraint = p.get("constraint")
            _require(constraint is None or (constraint in CONSTRAINTS and label == "unresolved"),
                     f"gold item {item['id']}: invalid constraint {constraint!r}")
            alt_ok = p.get("alt_ok", [])
            _require(isinstance(alt_ok, list) and all(x in LABELS for x in alt_ok),
                     f"gold item {item['id']}: invalid alt_ok")
            key = (min(a, b), max(a, b))
            entry = {"label": label, "constraint": constraint, "alt_ok": tuple(sorted(alt_ok))}
            if key in pairs:
                existing = pairs[key]
                _require(all(existing[k] == entry[k] for k in entry),
                         f"gold pair {key} is repeated with inconsistent labels")
            else:
                pairs[key] = dict(entry, passes=set(), items=set(), occurrences=0)
            pairs[key]["passes"].add(item["pass"])
            pairs[key]["items"].add(item["id"])
            pairs[key]["occurrences"] += 1
    return pairs


def kind_of(pair: dict) -> str:
    """Stage A role of a gold pair (design section 13, "Gold as scored")."""
    if pair["label"] in POSITIVE_LABELS:
        return "positive"
    if pair["label"] == "distinct" or (
            pair["label"] == "unresolved" and pair["constraint"] == "not_same_event"):
        return "hard_negative"
    if pair["label"] == "unresolved":
        return "unresolved_unconstrained"
    return "not_scored"


# --- Metrics ---------------------------------------------------------------------


def ratio(numerator: int, denominator: int) -> dict:
    """Exact ratio plus a 4-decimal percentage string; null when undefined."""
    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "fraction": None, "percent": None}
    value = Fraction(numerator, denominator) * 100
    percent = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {"numerator": numerator, "denominator": denominator,
            "fraction": f"{numerator}/{denominator}", "percent": str(percent)}


def _subset_metrics(pairs: dict, candidates: dict, weight) -> dict:
    """Recall, per-label recall and hard-negative exposure for a pair subset.

    ``weight(key, pair)`` gives each pair's count (1 for unique pairs,
    occurrences for occurrence-weighted results).
    """
    positives = {k: p for k, p in pairs.items() if kind_of(p) == "positive"}
    negatives = {k: p for k, p in pairs.items() if kind_of(p) == "hard_negative"}

    def total(subset):
        return sum(weight(k, p) for k, p in subset.items())

    def surfaced(subset):
        return sum(weight(k, p) for k, p in subset.items() if k in candidates)

    by_label = {
        label: ratio(surfaced(s), total(s)) for label in POSITIVE_LABELS
        for s in [{k: p for k, p in positives.items() if p["label"] == label}]
    }
    unresolved_neg = {k: p for k, p in negatives.items() if p["label"] == "unresolved"}
    distinct = {k: p for k, p in negatives.items() if p["label"] == "distinct"}
    return {
        "positives": {"recall": ratio(surfaced(positives), total(positives)),
                      "by_label": by_label},
        "hard_negatives": {
            "in_pool": ratio(surfaced(negatives), total(negatives)),
            "by_kind": {"distinct": ratio(surfaced(distinct), total(distinct)),
                        "unresolved_not_same_event": ratio(surfaced(unresolved_neg),
                                                           total(unresolved_neg))},
        },
        "excluded": {
            "unresolved_unconstrained": total(
                {k: p for k, p in pairs.items() if kind_of(p) == "unresolved_unconstrained"}),
            "not_scored": total({k: p for k, p in pairs.items() if kind_of(p) == "not_scored"}),
        },
    }


def _route_metrics(pairs: dict, candidates: dict) -> dict:
    """Descriptive per-route counts over surfaced positive pairs.

    Uses only the routes recorded in the candidate artifact. These are
    observations, not ablations: no counterfactual without a route is
    computed, because removing a route would change candidate generation
    itself (for example top-k rankings).
    """
    surfaced = [k for k, p in pairs.items() if kind_of(p) == "positive" and k in candidates]
    per_route = {}
    for route in ROUTES:
        with_route = [k for k in surfaced if route in candidates[k]]
        only = [k for k in with_route if candidates[k] == (route,)]
        per_route[route] = {"surfaced_positives_with_route": len(with_route),
                            "surfaced_positives_only_by_route": len(only)}
    return {"per_route": per_route}


def score_stage_a(
    artifact: dict,
    gold: dict,
    expected_corpus_sha256: str = rules.CORPUS_SHA256,
    expected_config_sha256: str | None = None,
    artifact_sha256: str | None = None,
    gold_sha256: str | None = None,
) -> dict:
    """Score a Stage A candidate artifact against a gold. Pure function."""
    if expected_config_sha256 is None:
        expected_config_sha256 = committed_config_sha256()
    _require(isinstance(gold, dict) and isinstance(gold.get("corpus"), dict),
             "gold has no corpus identity")
    _require(gold["corpus"].get("sha256_at_encoding") == expected_corpus_sha256,
             "gold corpus SHA-256 does not match the expected corpus")
    candidates, identity = parse_candidates(artifact, expected_corpus_sha256,
                                            expected_config_sha256)
    pairs = parse_gold(gold, identity["article_count"])

    unique = _subset_metrics(pairs, candidates, lambda k, p: 1)
    weighted = _subset_metrics(pairs, candidates, lambda k, p: p["occurrences"])
    by_pass = {}
    for ps in (1, 2):
        subset = {k: p for k, p in pairs.items() if ps in p["passes"]}
        by_pass[str(ps)] = _subset_metrics(subset, candidates, lambda k, p: 1)

    pool = ratio(identity["candidate_count"], identity["pair_count_total"])
    recall = unique["positives"]["recall"]
    criteria = {
        "min_recall": "0.90",
        "max_pool_share": "0.05",
        "recall_met": recall["denominator"] > 0
        and Fraction(recall["numerator"], recall["denominator"]) >= MIN_RECALL,
        "pool_met": Fraction(identity["candidate_count"], identity["pair_count_total"])
        <= MAX_POOL_SHARE if identity["pair_count_total"] else False,
    }

    pair_rows = []
    for (a, b), p in sorted(pairs.items()):
        kind = kind_of(p)
        if kind not in ("positive", "hard_negative"):
            continue
        in_pool = (a, b) in candidates
        pair_rows.append({
            "a": a, "b": b, "label": p["label"], "constraint": p["constraint"],
            "kind": kind, "passes": sorted(p["passes"]), "items": sorted(p["items"]),
            "occurrences": p["occurrences"], "in_pool": in_pool,
            "routes": list(candidates[(a, b)]) if in_pool else [],
        })

    return {
        "format": SCORE_FORMAT,
        "format_version": SCORE_FORMAT_VERSION,
        "run": rules.RUN,
        "stage": "A",
        "inputs": {
            "candidate_artifact_sha256": artifact_sha256,
            "gold_sha256": gold_sha256,
            "corpus_sha256": identity["corpus_sha256"],
            "config_sha256": identity["config_sha256"],
        },
        "pool": {"candidate_count": identity["candidate_count"],
                 "pair_count_total": identity["pair_count_total"], "share": pool},
        "gold_pairs": {
            "unique": len(pairs),
            "occurrences": sum(p["occurrences"] for p in pairs.values()),
            "by_kind": {k: sum(1 for p in pairs.values() if kind_of(p) == k)
                        for k in ("positive", "hard_negative", "unresolved_unconstrained",
                                  "not_scored")},
        },
        "unique_pairs": unique,
        "occurrence_weighted": weighted,
        "by_pass": by_pass,
        "routes": _route_metrics(pairs, candidates),
        "criteria": criteria,
        "pairs": pair_rows,
    }


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, indent=1) + "\n"


def _load_json(path) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScoreInputError(f"{path}: not valid JSON ({exc})") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.e002.score",
                                     description="Experiment 002 scorer (Stage A only).")
    sub = parser.add_subparsers(dest="stage", required=True)
    a = sub.add_parser("stage-a", help="score a Stage A candidate artifact")
    a.add_argument("--candidates", required=True, help="sealed Stage A candidate artifact")
    a.add_argument("--gold", required=True, help="gold file")
    a.add_argument("--out", required=True, help="score artifact path; must not already exist")
    a.add_argument("--expected-artifact-sha256", help="required candidate artifact SHA-256")
    a.add_argument("--expected-corpus-sha256", default=rules.CORPUS_SHA256)
    a.add_argument("--expected-config-sha256", default=None,
                   help="default: the committed Run 1 candidate configuration")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        print(f"error: {out} already exists", file=sys.stderr)
        return 2
    artifact_sha = file_sha256(args.candidates)
    if args.expected_artifact_sha256 and artifact_sha != args.expected_artifact_sha256:
        raise ScoreInputError("candidate artifact SHA-256 does not match the expected value")
    gold_sha = file_sha256(args.gold)
    result = score_stage_a(_load_json(args.candidates), _load_json(args.gold),
                           expected_corpus_sha256=args.expected_corpus_sha256,
                           expected_config_sha256=args.expected_config_sha256,
                           artifact_sha256=artifact_sha, gold_sha256=gold_sha)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
