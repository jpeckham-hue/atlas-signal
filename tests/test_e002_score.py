"""Experiment 002 Stage A scorer, on synthetic candidate artifacts and gold only.

No test here reads the repository gold or the sealed real candidate artifact.
"""

import contextlib
import copy
import io
import json
import random
import re
import tempfile
import unittest
from pathlib import Path

from research.e002 import rules
from research.e002 import score as sc
from research.e002.candidates import ROUTES, candidate_config

SHA = rules.CORPUS_SHA256
CONFIG_VALUES = json.loads(json.dumps(candidate_config()))
CONFIG_SHA = sc.committed_config_sha256()


def artifact(pairs, n=10, corpus_sha=SHA, config_values=None, config_sha=None):
    """Synthetic Stage A artifact. ``pairs`` maps (a, b) -> routes."""
    values = CONFIG_VALUES if config_values is None else config_values
    records = []
    for (a, b), routes in pairs.items():
        records.append({"a": a, "b": b, "routes": list(routes), "time_gap_seconds": 0,
                        "cosine": 0.5, "rank_a_to_b": 1, "rank_b_to_a": 1, "numbers": [],
                        "entities": [], "tags": [], "same_issuer_within_hours": False})
    return {
        "format": sc.CANDIDATE_FORMAT, "format_version": 1, "run": "run1", "stage": "A",
        "corpus": {"sha256": corpus_sha, "article_count": n},
        "config": {"design": "x", "design_commit": "y", "values": values,
                   "sha256": config_sha or sc._config_sha256(values)},
        "routes": list(ROUTES), "pair_count_total": n * (n - 1) // 2,
        "candidate_count": len(records), "candidates": records,
    }


def pair(a, b, label, **extra):
    return dict(a=a, b=b, label=label, **extra)


def gold(items, corpus_sha=SHA):
    """Synthetic gold. ``items`` are (id, pass, [pairs])."""
    return {
        "format": sc.GOLD_FORMAT, "format_version": 1,
        "corpus": {"sha256_at_encoding": corpus_sha},
        "items": [{"id": i, "pass": p, "articles": sorted({x for q in ps for x in (q["a"], q["b"])}, key=repr),
                   "memberships": [], "pairs": ps} for i, p, ps in items],
    }


def recall(result, key="unique_pairs"):
    return result[key]["positives"]["recall"]


LEX = ("lexical_cosine",)


class RecallTests(unittest.TestCase):
    def positives(self, k):
        return [pair(1, i, "same_event") for i in range(2, 2 + k)]

    def score(self, surfaced, k=10):
        g = gold([("P2-01", 2, self.positives(k))])
        cands = {(1, i): LEX for i in surfaced}
        return sc.score_stage_a(artifact(cands, n=20), g)

    def test_perfect_recall(self):
        r = self.score(range(2, 12))
        self.assertEqual(recall(r)["fraction"], "10/10")
        self.assertEqual(recall(r)["percent"], "100.0000")
        self.assertTrue(r["criteria"]["recall_met"])

    def test_partial_recall_at_and_below_criterion(self):
        r = self.score(range(2, 11))
        self.assertEqual(recall(r)["fraction"], "9/10")
        self.assertTrue(r["criteria"]["recall_met"])
        r = self.score(range(2, 10))
        self.assertEqual(recall(r)["percent"], "80.0000")
        self.assertFalse(r["criteria"]["recall_met"])
        missed = [(p["a"], p["b"]) for p in r["pairs"] if not p["in_pool"]]
        self.assertEqual(missed, [(1, 10), (1, 11)])
        self.assertTrue(all(p["routes"] == [] for p in r["pairs"] if not p["in_pool"]))

    def test_zero_recall(self):
        r = self.score([12, 13], k=10)
        self.assertEqual(recall(r)["fraction"], "0/10")
        self.assertFalse(r["criteria"]["recall_met"])

    def test_zero_candidate_pool(self):
        r = self.score([])
        self.assertEqual(r["pool"]["candidate_count"], 0)
        self.assertEqual(r["pool"]["share"]["percent"], "0.0000")
        self.assertTrue(r["criteria"]["pool_met"])
        self.assertEqual(recall(r)["numerator"], 0)

    def test_no_positives_gives_undefined_recall(self):
        r = sc.score_stage_a(artifact({}), gold([("P2-01", 2, [pair(1, 2, "distinct")])]))
        self.assertIsNone(recall(r)["fraction"])
        self.assertFalse(r["criteria"]["recall_met"])

    def test_per_label_recall(self):
        g = gold([("P2-01", 2, [pair(1, 2, "copy_of"), pair(1, 3, "same_event"),
                                pair(1, 4, "linked"), pair(1, 5, "linked")])])
        r = sc.score_stage_a(artifact({(1, 2): LEX, (1, 4): LEX}), g)
        by_label = r["unique_pairs"]["positives"]["by_label"]
        self.assertEqual({k: v["fraction"] for k, v in by_label.items()},
                         {"copy_of": "1/1", "same_event": "0/1", "linked": "1/2"})


class PoolTests(unittest.TestCase):
    def test_percentage_and_five_percent_boundary(self):
        g = gold([("P2-01", 2, [pair(1, 2, "same_event")])])
        cands = {(1, i): LEX for i in range(2, 8)}  # 6 of C(16, 2) = 120 pairs = 5%
        r = sc.score_stage_a(artifact(cands, n=16), g)
        self.assertEqual(r["pool"]["share"]["fraction"], "6/120")
        self.assertEqual(r["pool"]["share"]["percent"], "5.0000")
        self.assertTrue(r["criteria"]["pool_met"])
        cands[(1, 8)] = LEX
        r = sc.score_stage_a(artifact(cands, n=16), g)
        self.assertEqual(r["pool"]["share"]["percent"], "5.8333")
        self.assertFalse(r["criteria"]["pool_met"])


class HardNegativeAndExclusionTests(unittest.TestCase):
    def setUp(self):
        self.g = gold([("P2-01", 2, [
            pair(1, 2, "same_event"),
            pair(1, 3, "distinct", reason="same_entity"),
            pair(1, 4, "distinct", reason="look_alike"),
            pair(1, 5, "unresolved", constraint="not_same_event"),
            pair(1, 6, "unresolved", alt_ok=["same_event"]),
            pair(1, 7, "not_scored"),
        ])])
        self.cands = {(1, 2): LEX, (1, 3): ("entity",), (1, 5): ("number",),
                      (1, 6): LEX, (1, 7): LEX}
        self.r = sc.score_stage_a(artifact(self.cands), self.g)

    def test_hard_negative_rate(self):
        hn = self.r["unique_pairs"]["hard_negatives"]
        self.assertEqual(hn["in_pool"]["fraction"], "2/3")
        self.assertEqual(hn["by_kind"]["distinct"]["fraction"], "1/2")
        self.assertEqual(hn["by_kind"]["unresolved_not_same_event"]["fraction"], "1/1")

    def test_unresolved_and_not_scored_are_neither_positive_nor_negative(self):
        self.assertEqual(recall(self.r)["fraction"], "1/1")
        self.assertEqual(self.r["unique_pairs"]["excluded"],
                         {"unresolved_unconstrained": 1, "not_scored": 1})
        self.assertEqual(self.r["gold_pairs"]["by_kind"],
                         {"positive": 1, "hard_negative": 3, "unresolved_unconstrained": 1,
                          "not_scored": 1})
        listed = {(p["a"], p["b"]) for p in self.r["pairs"]}
        self.assertEqual(listed, {(1, 2), (1, 3), (1, 4), (1, 5)})


class PassAndRepetitionTests(unittest.TestCase):
    def setUp(self):
        self.g = gold([
            ("P1-A", 1, [pair(1, 2, "same_event"), pair(3, 4, "distinct")]),
            ("P2-01", 2, [pair(2, 1, "same_event"), pair(5, 6, "copy_of")]),
            ("P2-02", 2, [pair(7, 8, "linked"), pair(5, 6, "copy_of")]),
        ])
        self.r = sc.score_stage_a(artifact({(1, 2): LEX, (5, 6): LEX, (3, 4): LEX}), self.g)

    def test_unique_pairs_and_occurrences(self):
        self.assertEqual(self.r["gold_pairs"]["unique"], 4)
        self.assertEqual(self.r["gold_pairs"]["occurrences"], 6)
        self.assertEqual(recall(self.r)["fraction"], "2/3")
        self.assertEqual(recall(self.r, "occurrence_weighted")["fraction"], "4/5")
        rows = {(p["a"], p["b"]): p for p in self.r["pairs"]}
        self.assertEqual(rows[(1, 2)]["passes"], [1, 2])
        self.assertEqual(rows[(1, 2)]["items"], ["P1-A", "P2-01"])
        self.assertEqual(rows[(5, 6)]["occurrences"], 2)

    def test_primary_unit_is_all_unique_pairs_across_passes(self):
        # Criteria use unique pairs from both passes, not Pass 2 alone.
        self.assertEqual(self.r["unique_pairs"]["positives"]["recall"]["fraction"], "2/3")
        self.assertEqual(self.r["by_pass"]["2"]["positives"]["recall"]["fraction"], "2/3")
        g = gold([("P1-A", 1, [pair(1, 2, "same_event")]),
                  ("P2-01", 2, [pair(3, 4, "same_event")])])
        r = sc.score_stage_a(artifact({(1, 2): LEX}), g)
        self.assertEqual(r["unique_pairs"]["positives"]["recall"]["fraction"], "1/2")
        self.assertEqual(r["by_pass"]["2"]["positives"]["recall"]["fraction"], "0/1")
        self.assertFalse(r["criteria"]["recall_met"])

    def test_by_pass(self):
        p1, p2 = self.r["by_pass"]["1"], self.r["by_pass"]["2"]
        self.assertEqual(p1["positives"]["recall"]["fraction"], "1/1")
        self.assertEqual(p1["hard_negatives"]["in_pool"]["fraction"], "1/1")
        self.assertEqual(p2["positives"]["recall"]["fraction"], "2/3")
        self.assertEqual(p2["hard_negatives"]["in_pool"]["denominator"], 0)

    def test_inconsistent_repeated_pair_rejected(self):
        for second in (pair(2, 1, "distinct"),
                       pair(2, 1, "same_event", alt_ok=["linked"])):
            g = gold([("P1-A", 1, [pair(1, 2, "same_event")]), ("P2-01", 2, [second])])
            with self.subTest(second=second), self.assertRaises(sc.ScoreInputError):
                sc.score_stage_a(artifact({}), g)
        g = gold([("P1-A", 1, [pair(1, 2, "unresolved", constraint="not_same_event")]),
                  ("P2-01", 2, [pair(1, 2, "unresolved")])])
        with self.assertRaises(sc.ScoreInputError):
            sc.score_stage_a(artifact({}), g)


class RouteDiagnosticTests(unittest.TestCase):
    def test_recorded_routes_only(self):
        g = gold([("P2-01", 2, [pair(1, i, "same_event") for i in range(2, 7)])])
        cands = {(1, 2): ("lexical_cosine", "lexical_top_k"), (1, 3): ("lexical_top_k", "number"),
                 (1, 4): ("number",), (1, 5): ("issuer",)}
        r = sc.score_stage_a(artifact(cands), g)
        routes = r["routes"]
        self.assertEqual(routes["per_route"]["number"],
                         {"surfaced_positives_with_route": 2, "surfaced_positives_only_by_route": 1})
        self.assertEqual(routes["per_route"]["lexical_top_k"]["surfaced_positives_only_by_route"], 0)
        self.assertEqual(routes["per_route"]["lexical_cosine"],
                         {"surfaced_positives_with_route": 1, "surfaced_positives_only_by_route": 0})
        self.assertEqual(routes["per_route"]["issuer"],
                         {"surfaced_positives_with_route": 1, "surfaced_positives_only_by_route": 1})
        self.assertEqual(set(routes), {"per_route"})  # descriptive only, no ablation
        rows = {(p["a"], p["b"]): p["routes"] for p in r["pairs"]}
        self.assertEqual(rows[(1, 3)], ["lexical_top_k", "number"])
        self.assertEqual(rows[(1, 6)], [])


class ValidationTests(unittest.TestCase):
    G = gold([("P2-01", 2, [pair(1, 2, "same_event")])])

    def rejects(self, art=None, g=None, **kwargs):
        with self.assertRaises(sc.ScoreInputError):
            sc.score_stage_a(art if art is not None else artifact({}),
                             g if g is not None else self.G, **kwargs)

    def test_unknown_or_invalid_article_ids(self):
        self.rejects(g=gold([("P2-01", 2, [pair(1, 11, "same_event")])]))
        self.rejects(art=artifact({(1, 11): LEX}))
        for a, b in ((0, 2), (-1, 2), (2, 2), (True, 2), ("1", 2)):
            with self.subTest(a=a, b=b):
                self.rejects(g=gold([("P2-01", 2, [pair(a, b, "same_event")])]))
                self.rejects(art=artifact({(a, b): LEX}))

    def test_candidate_pairs_normalized(self):
        r = sc.score_stage_a(artifact({(2, 1): LEX}), self.G)
        self.assertEqual(recall(r)["fraction"], "1/1")

    def test_duplicate_candidates_rejected(self):
        art = artifact({(1, 2): LEX})
        art["candidates"].append(dict(art["candidates"][0], a=2, b=1))
        art["candidate_count"] = 2
        self.rejects(art=art)

    def test_identity_mismatches_rejected(self):
        self.rejects(art=artifact({}, corpus_sha="0" * 64))
        self.rejects(g=gold([("P2-01", 2, [pair(1, 2, "same_event")])], corpus_sha="0" * 64))
        tampered = dict(CONFIG_VALUES, CAND_COSINE="0.10")
        self.rejects(art=artifact({}, config_values=tampered, config_sha=CONFIG_SHA))
        self.rejects(art=artifact({}, config_values=tampered))
        self.rejects(expected_corpus_sha256="0" * 64)
        self.rejects(expected_config_sha256="0" * 64)

    def test_malformed_candidate_artifacts_rejected(self):
        base = artifact({(1, 2): LEX})
        mutations = [
            ("format", "other"), ("format_version", 2), ("stage", "B"),
            ("pair_count_total", 44), ("candidate_count", 5), ("routes", ["lexical"]),
            ("candidates", {}), ("corpus", None), ("config", None),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                art = copy.deepcopy(base)
                art[key] = value
                self.rejects(art=art)
        for routes in ([], ["bogus"], ["number", "number"], "number"):
            with self.subTest(routes=routes):
                art = copy.deepcopy(base)
                art["candidates"][0]["routes"] = routes
                self.rejects(art=art)
        self.rejects(art=[])

    def test_malformed_gold_rejected(self):
        bad = [
            gold([("P2-01", 2, [pair(1, 2, "maybe")])]),
            gold([("P2-01", 3, [pair(1, 2, "same_event")])]),
            gold([("P2-01", 2, [pair(1, 2, "distinct", constraint="not_same_event")])]),
            gold([("P2-01", 2, [pair(1, 2, "unresolved", constraint="other")])]),
            gold([("P2-01", 2, [pair(1, 2, "unresolved", alt_ok="same_event")])]),
            dict(self.G, format="other"),
            dict(self.G, items={}),
            dict(self.G, corpus={}),
        ]
        for g in bad:
            with self.subTest(g=str(g)[:80]):
                self.rejects(g=g)
        g = copy.deepcopy(self.G)
        del g["items"][0]["id"]
        self.rejects(g=g)


class DeterminismAndOutputTests(unittest.TestCase):
    def inputs(self, seed):
        rng = random.Random(seed)
        labels = ["same_event", "linked", "copy_of", "distinct", "unresolved", "not_scored"]
        items = []
        for i in range(6):
            pairs = []
            for _ in range(5):
                a, b = rng.sample(range(1, 21), 2)
                pairs.append(pair(a, b, rng.choice(labels)))
            items.append((f"P{1 + i % 2}-{i:02d}", 1 + i % 2, pairs))
        # make repeated pairs consistent by keeping the first label
        seen, cleaned = {}, []
        for iid, ps, pairs in items:
            kept = []
            for p in pairs:
                key = tuple(sorted((p["a"], p["b"])))
                seen.setdefault(key, p["label"])
                kept.append(dict(p, label=seen[key]))
            cleaned.append((iid, ps, kept))
        cands = {tuple(sorted(rng.sample(range(1, 21), 2))): (rng.choice(ROUTES),)
                 for _ in range(25)}
        return artifact(cands, n=20), gold(cleaned)

    def test_independent_of_input_order(self):
        art, g = self.inputs(1)
        first = sc.canonical_json(sc.score_stage_a(art, g))
        art2, g2 = copy.deepcopy(art), copy.deepcopy(g)
        rng = random.Random(9)
        rng.shuffle(art2["candidates"])
        rng.shuffle(g2["items"])
        for item in g2["items"]:
            rng.shuffle(item["pairs"])
            for p in item["pairs"]:
                p["a"], p["b"] = p["b"], p["a"]
        self.assertEqual(first, sc.canonical_json(sc.score_stage_a(art2, g2)))

    def test_output_contains_no_free_text(self):
        art, g = self.inputs(2)
        result = sc.score_stage_a(art, g, artifact_sha256="a" * 64, gold_sha256="b" * 64)
        allowed = set(sc.LABELS) | set(ROUTES) | set(sc.CONSTRAINTS) | {
            "positive", "hard_negative", "unresolved_unconstrained", "run1", "A", "0.90", "0.05",
            sc.SCORE_FORMAT}

        def strings(o, key=None):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from strings(v, k)
            elif isinstance(o, list):
                for v in o:
                    yield from strings(v, key)
            elif isinstance(o, str):
                yield key, o
        for key, value in strings(result):
            with self.subTest(key=key, value=value):
                self.assertTrue(value in allowed or re.fullmatch(r"[0-9a-f]{64}", value)
                                or re.fullmatch(r"\d+/\d+|\d+\.\d{4}", value)
                                or re.fullmatch(r"P[12]-\d\d", value))


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cand = self.dir / "candidates.json"
        self.gold = self.dir / "gold.json"
        self.cand.write_text(json.dumps(artifact({(1, 2): LEX})), encoding="utf-8")
        self.gold.write_text(json.dumps(gold([("P2-01", 2, [pair(1, 2, "same_event")])])),
                             encoding="utf-8")

    def run_cli(self, out, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return sc.main(["stage-a", "--candidates", str(self.cand), "--gold", str(self.gold),
                            "--out", str(out), *extra])

    def test_writes_deterministic_artifact_with_input_hashes(self):
        a, b = self.dir / "s1.json", self.dir / "s2.json"
        self.assertEqual(self.run_cli(a), 0)
        self.assertEqual(self.run_cli(b), 0)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        data = json.loads(a.read_bytes())
        self.assertEqual(data["inputs"]["candidate_artifact_sha256"], sc.file_sha256(self.cand))
        self.assertEqual(data["inputs"]["gold_sha256"], sc.file_sha256(self.gold))

    def test_expected_artifact_hash_enforced(self):
        out = self.dir / "s.json"
        with self.assertRaises(sc.ScoreInputError):
            self.run_cli(out, "--expected-artifact-sha256", "0" * 64)
        self.assertFalse(out.exists())
        self.assertEqual(self.run_cli(out, "--expected-artifact-sha256",
                                      sc.file_sha256(self.cand)), 0)

    def test_refuses_overwrite_and_rejects_non_json(self):
        out = self.dir / "s.json"
        out.write_text("existing", encoding="utf-8")
        self.assertEqual(self.run_cli(out), 2)
        self.assertEqual(out.read_text(encoding="utf-8"), "existing")
        self.gold.write_text("not json", encoding="utf-8")
        with self.assertRaises(sc.ScoreInputError):
            self.run_cli(self.dir / "t.json")

    def test_inputs_not_modified(self):
        before = (self.cand.read_bytes(), self.gold.read_bytes())
        self.run_cli(self.dir / "s.json")
        self.assertEqual(before, (self.cand.read_bytes(), self.gold.read_bytes()))


if __name__ == "__main__":
    unittest.main()
