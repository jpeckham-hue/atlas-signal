"""Experiment 002 Stage A candidate generation on synthetic data only.

Feature-level tests construct vectors directly so each threshold boundary is
hit exactly; database-level tests use made-up articles.
"""

import contextlib
import io
import json
import math
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.e002 import candidates as cand
from research.e002 import corpus, predict, rules
from research.e002 import text as text_module
from research.e002.candidates import ArticleFeatures
from tests.e002_support import article, build_db

BASE = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def feat(id, hours=0.0, vector=None, numbers=(), entities=(), tags=(), unit=None, source="s"):
    return ArticleFeatures(id=id, source_id=source, time=BASE + timedelta(hours=hours),
                           issuing_unit=unit, vector=dict(vector or {}),
                           numbers=frozenset(numbers), entities=frozenset(entities),
                           tags=frozenset(tags))


def pairs(features):
    return {(s.a, s.b) for s in cand.generate_candidates(features)}


def unit_vector(shared_dim, value, own_dim):
    """A unit vector whose cosine with {shared_dim: 1} is ``value``."""
    return {shared_dim: value, own_dim: math.sqrt(1 - value * value)}


class LexicalRouteTests(unittest.TestCase):
    # |(3, 17, 10, 1, 1)| = 20, so the cosine with {"x": 1} is exactly 3/20.
    AT_015 = {"x": 3.0, "a": 17.0, "b": 10.0, "c": 1.0, "d": 1.0}
    # |(2, 24, 6, 3)| = 25, so the cosine with {"x": 1} is exactly 2/25.
    AT_008 = {"x": 2.0, "a": 24.0, "b": 6.0, "c": 3.0}

    def test_cosine_exactly_015_fires(self):
        (s,) = cand.generate_candidates([feat(1, vector=self.AT_015), feat(2, vector={"x": 1.0})])
        self.assertEqual(s.cosine, 0.15)
        self.assertIn("lexical_cosine", s.routes)

    def test_cosine_below_015_does_not_fire_cosine_route(self):
        below = dict(self.AT_015, e=0.01)
        s = cand.pair_signals([feat(1, vector=below), feat(2, vector={"x": 1.0})], 1, 2)
        self.assertLess(s.cosine, 0.15)
        self.assertNotIn("lexical_cosine", s.routes)

    def test_top_k_at_exactly_008(self):
        (s,) = cand.generate_candidates([feat(1, vector=self.AT_008), feat(2, vector={"x": 1.0})])
        self.assertEqual(s.cosine, 0.08)
        self.assertEqual(s.routes, ("lexical_top_k",))
        self.assertEqual((s.rank_a_to_b, s.rank_b_to_a), (1, 1))

    def test_top_k_below_008(self):
        below = dict(self.AT_008, e=0.01)
        self.assertEqual(pairs([feat(1, vector=below), feat(2, vector={"x": 1.0})]), set())

    def hub(self, extra_neighbours):
        """Hub 1; neighbours 10.. at cosines 0.14..; article 2 at 0.09 with
        five closer neighbours of its own (20..24)."""
        features = [feat(1, vector={"h": 1.0})]
        for i, c in enumerate([0.14, 0.13, 0.12, 0.11, 0.10][:extra_neighbours]):
            features.append(feat(10 + i, vector=unit_vector("h", c, f"n{i}")))
        features.append(feat(2, vector={"h": 0.09, "g": math.sqrt(1 - 0.0081)}))
        for j in range(5):
            features.append(feat(20 + j, vector={"g": 1.0, f"w{j}": 0.1 * (j + 1)}))
        return features

    def test_sixth_neighbour_on_both_sides_is_not_a_candidate(self):
        s = cand.pair_signals(self.hub(5), 1, 2)
        self.assertEqual((s.rank_a_to_b, s.rank_b_to_a), (6, 6))
        self.assertAlmostEqual(s.cosine, 0.09)
        self.assertFalse(s.is_candidate)
        self.assertNotIn((1, 2), pairs(self.hub(5)))

    def test_fifth_neighbour_is_a_candidate(self):
        s = cand.pair_signals(self.hub(4), 1, 2)
        self.assertEqual(s.rank_a_to_b, 5)
        self.assertEqual(s.routes, ("lexical_top_k",))
        self.assertIn((1, 2), pairs(self.hub(4)))

    def test_ties_share_a_rank_independent_of_ids(self):
        def build(t1, t2):
            features = [feat(1, vector={"h": 1.0})]
            for i, c in enumerate([0.5, 0.4, 0.3, 0.2]):
                features.append(feat(10 + i, vector=unit_vector("h", c, f"n{i}")))
            features.append(feat(t1, vector=unit_vector("h", 0.1, "t1")))
            features.append(feat(t2, vector=unit_vector("h", 0.1, "t2")))
            return features
        for t1, t2 in ((50, 60), (60, 50), (3, 99)):
            with self.subTest(ids=(t1, t2)):
                features = build(t1, t2)
                ranks = {cand.pair_signals(features, 1, t).rank_a_to_b for t in (t1, t2)}
                self.assertEqual(ranks, {5})
                self.assertTrue({(1, t1), (1, t2)} <= pairs(features))


class DiscreteRouteTests(unittest.TestCase):
    def check_df_boundary(self, field, key, limit, route):
        at = [feat(i, **{field: [key]}) for i in range(1, limit + 1)]
        result = cand.generate_candidates(at)
        self.assertEqual(len(result), limit * (limit - 1) // 2)
        for s in result:
            self.assertEqual(s.routes, (route,))
            self.assertEqual(getattr(s, field), ((key, limit),))
        over = [feat(i, **{field: [key]}) for i in range(1, limit + 2)]
        self.assertEqual(cand.generate_candidates(over), ())

    def test_number_df_5_vs_6(self):
        self.assertEqual(rules.NUM_MAX_DF, 5)
        self.check_df_boundary("numbers", "£7", 5, "number")

    def test_entity_df_5_vs_6(self):
        self.assertEqual(rules.CAND_ENTITY_MAX_DF, 5)
        self.check_df_boundary("entities", "northwind bank", 5, "entity")

    def test_tag_df_8_vs_9(self):
        self.assertEqual(rules.CAND_TAG_MAX_DF, 8)
        self.check_df_boundary("tags", "widgets", 8, "tag")

    def test_issuer_24_hour_boundary(self):
        self.assertEqual(rules.CAND_ISSUER_HOURS, 24)
        (s,) = cand.generate_candidates([feat(1, unit="u"), feat(2, hours=24, unit="u")])
        self.assertEqual(s.routes, ("issuer",))
        self.assertEqual(s.time_gap_seconds, 24 * 3600)
        late = [feat(1, unit="u"), feat(2, hours=24 + 1 / 3600, unit="u")]
        self.assertEqual(pairs(late), set())
        self.assertEqual(pairs([feat(1, unit="u"), feat(2, unit="v")]), set())
        self.assertEqual(pairs([feat(1), feat(2)]), set())


class GateTests(unittest.TestCase):
    def test_14_day_boundary(self):
        self.assertEqual(rules.CANDIDATE_MAX_DAYS, 14)
        at = [feat(1, numbers=["£7"]), feat(2, hours=14 * 24, numbers=["£7"])]
        self.assertEqual(pairs(at), {(1, 2)})
        over = [feat(1, numbers=["£7"]), feat(2, hours=14 * 24 + 1 / 3600, numbers=["£7"])]
        self.assertEqual(pairs(over), set())

    def test_outside_gate_never_surfaces_even_with_every_signal(self):
        shared = dict(vector={"x": 1.0}, numbers=["£7"], entities=["acme"], tags=["widgets"],
                      unit="u")
        features = [feat(1, **shared), feat(2, hours=15 * 24, **shared)]
        s = cand.pair_signals(features, 1, 2)
        self.assertFalse(s.within_gate)
        self.assertFalse(s.is_candidate)
        self.assertEqual(s.routes, ("lexical_cosine", "number", "entity", "tag"))
        self.assertEqual(cand.generate_candidates(features), ())

    def test_out_of_gate_articles_do_not_take_neighbour_slots(self):
        features = [feat(1, vector={"h": 1.0})]
        for i, c in enumerate([0.9, 0.8, 0.7, 0.6, 0.5]):
            features.append(feat(10 + i, hours=30 * 24, vector=unit_vector("h", c, f"n{i}")))
        features.append(feat(2, vector={"h": 0.09, "g": math.sqrt(1 - 0.0081)}))
        for j in range(5):
            features.append(feat(20 + j, vector={"g": 1.0, f"w{j}": 0.1 * (j + 1)}))
        self.assertEqual(cand.pair_signals(features, 1, 2).rank_a_to_b, 1)
        self.assertIn((1, 2), pairs(features))


class CombinationAndInvarianceTests(unittest.TestCase):
    def test_multiple_signals_recorded_in_route_order(self):
        shared = dict(vector={"x": 1.0}, numbers=["£7", "300"], entities=["acme"],
                      tags=["widgets"], unit="u")
        (s,) = cand.generate_candidates([feat(2, **shared), feat(1, hours=5, **shared)])
        self.assertEqual((s.a, s.b), (1, 2))
        self.assertEqual(s.routes, cand.ROUTES)
        self.assertEqual(s.numbers, (("300", 2), ("£7", 2)))

    def random_features(self, seed, ids):
        rng = random.Random(seed)
        dims = [f"d{i}" for i in range(12)]
        features = []
        for i in ids:
            vector = {d: rng.choice([0.0, 0.5, 1.0, 2.0]) for d in rng.sample(dims, 3)}
            features.append(feat(
                i, hours=rng.choice([0, 6, 30, 200, 400]),
                vector={d: v for d, v in vector.items() if v},
                numbers=rng.sample(["£1m", "300", "4.5%"], rng.randint(0, 1)),
                entities=rng.sample(["acme", "fooland"], rng.randint(0, 1)),
                tags=rng.sample(["widgets", "ports"], rng.randint(0, 1)),
                unit=rng.choice([None, "u", "v"])))
        return features

    def test_invariant_under_article_renumbering(self):
        old_ids = list(range(1, 31))
        new_ids = random.Random(7).sample(range(100, 1000), 30)
        mapping = dict(zip(old_ids, new_ids))
        first = cand.generate_candidates(self.random_features(1, old_ids))
        second = cand.generate_candidates(self.random_features(1, new_ids))
        self.assertGreater(len(first), 0)

        def canon(signals, to_new):
            out = set()
            for s in signals:
                a, b = to_new(s.a), to_new(s.b)
                ranks = {(a, b): s.rank_a_to_b, (b, a): s.rank_b_to_a}
                lo, hi = sorted((a, b))
                out.add((lo, hi, s.routes, s.cosine, ranks[(lo, hi)], ranks[(hi, lo)],
                         s.numbers, s.entities, s.tags, s.time_gap_seconds))
            return out
        self.assertEqual(canon(first, mapping.get), canon(second, lambda x: x))

    def test_invariant_under_input_order(self):
        features = self.random_features(2, range(1, 31))
        shuffled = list(features)
        random.Random(3).shuffle(shuffled)
        self.assertEqual(cand.generate_candidates(features), cand.generate_candidates(shuffled))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            cand.generate_candidates([feat(1), feat(1)])


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def load(self, rows, name="c.sqlite3"):
        path = build_db(self.dir / name, rows)
        return corpus.load_corpus(path, expected_sha256=corpus.file_sha256(path)), path


class FeatureTests(DatabaseTestCase):
    def test_numbers_entities_tags_from_articles(self):
        c, _ = self.load([
            article(1, title="Talks between Northwind Bank and Acme stall",
                    summary="A £7m plan.",
                    sightings=((None, ["Widgets", "news releases", "Catch All"]),)),
        ])
        (f,) = cand.build_features(c.articles)
        self.assertEqual(f.numbers, {"£7000000"})
        self.assertEqual(f.entities, {"northwind bank", "acme"})
        self.assertEqual(f.tags, {"widgets", "catch all"})
        (g,) = cand.build_features(c.articles, catch_all_categories=["CATCH ALL "])
        self.assertEqual(g.tags, {"widgets"})

    def test_suppression_feeds_vectors(self):
        boiler = "Our agency helps every small firm in the whole region grow faster today."
        rows = [article(i, title=f"Item {w}", summary=f"Agency notice board. {boiler} {w} detail.")
                for i, w in enumerate(["alpha", "bravo", "charlie", "delta"], start=1)]
        rows += [article(i, title=f"Other {w}", summary=f"{w} report.")
                 for i, w in enumerate(["echo", "foxtrot", "golf", "hotel", "india", "juliet"],
                                       start=5)]
        c, _ = self.load(rows)
        f = {x.id: x for x in cand.build_features(c.articles)}
        # The wrapper "agency notice board", the repeated boilerplate sentence
        # and the title word "item" (in 4 of 10 articles, over 20%) are gone.
        # The shared trailing "detail" is a wrapper suffix as well.
        self.assertEqual(set(f[1].vector), {"alpha"})
        self.assertEqual(set(f[5].vector), {"echo"})  # "report" is a wrapper suffix

    def test_containers_get_no_special_treatment(self):
        def rows(live_url):
            return [
                article(1, url="https://example.test/a", title="Acme opens widget plant",
                        summary="Acme widget plant."),
                article(2, url=live_url, title="Markets update",
                        summary="Acme widget plant opens. Fooland port strike starts."),
                article(3, url="https://example.test/c", title="Fooland port strike",
                        summary="Fooland port strike."),
            ] + [article(i, url=f"https://example.test/f{i}", title=f"Filler {i} topic",
                         summary=f"Unrelated filler {i}.") for i in range(4, 12)]
        live, _ = self.load(rows("https://example.test/business/live/b"), "live.sqlite3")
        plain, _ = self.load(rows("https://example.test/business/b"), "plain.sqlite3")
        self.assertTrue(live.articles[1].container_flag)
        self.assertFalse(plain.articles[1].container_flag)
        p_live = pairs(cand.build_features(live.articles))
        p_plain = pairs(cand.build_features(plain.articles))
        self.assertEqual(p_live, p_plain)
        self.assertTrue({(1, 2), (2, 3)} <= p_live)
        self.assertNotIn((1, 3), p_live)


def reference_build_features(articles, catch_all_categories=cand.DEFAULT_CATCH_ALL_CATEGORIES):
    """The pre-refactor build_features body, kept verbatim as an oracle."""
    from research.e002.text import (
        detect_wrappers, distinctive_numbers, document_frequencies, entity_spans,
        extract_numbers, inverse_document_frequencies, repeated_sentences,
        source_common_tokens, tokenize, weighted_vector)
    articles = sorted(articles, key=lambda a: a.id)
    excluded = {cand.normalize_category(c) for c in catch_all_categories} | set(rules.DOCUMENT_TYPES)
    numbers = {a.id: frozenset(x.key for x in extract_numbers(a.title))
               | frozenset(x.key for x in extract_numbers(a.summary)) for a in articles}
    distinctive = distinctive_numbers(numbers.values())
    by_source = {}
    for a in articles:
        by_source.setdefault(a.source_id, []).append(a)
    wrappers = {s: detect_wrappers([cand._summary_raw_tokens(a.summary) for a in group])
                for s, group in by_source.items()}
    boilerplate = repeated_sentences(((a.source_id, a.id, a.summary) for a in articles),
                                     distinctive)
    title_tokens = {a.id: tokenize(a.title) for a in articles}
    summary_tokens = {
        a.id: cand.suppressed_summary_tokens(a.summary, wrappers[a.source_id],
                                             boilerplate.get(a.source_id, frozenset()))
        for a in articles}
    all_tokens = {a.id: title_tokens[a.id] + summary_tokens[a.id] for a in articles}
    common = source_common_tokens((a.source_id, all_tokens[a.id]) for a in articles)
    idf = inverse_document_frequencies(document_frequencies(all_tokens.values()), len(articles))
    return tuple(
        ArticleFeatures(
            id=a.id, source_id=a.source_id, time=a.representative_time,
            issuing_unit=a.issuing_unit,
            vector=weighted_vector(title_tokens[a.id], summary_tokens[a.id], idf,
                                   zero_weight=common.get(a.source_id, frozenset())),
            numbers=numbers[a.id],
            entities=frozenset(e.casefold() for e in entity_spans(a.title)),
            tags=frozenset(cand.normalize_category(c) for c in a.categories) - excluded)
        for a in articles)


class CorpusModelTests(DatabaseTestCase):
    BOILER = "Our agency helps every small firm in the whole region grow faster today."

    def rows(self):
        rows = []
        for i, w in enumerate(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"], start=1):
            rows.append(article(
                i, title=f"Acme Widget {w} plan costs \u00a3{i}m",
                summary=f"Agency notice board. {self.BOILER} The {w} widget plant opens. Continue reading",
                published_at=f"2026-01-1{i}T12:00:00Z", first_seen_at=f"2026-01-1{i}T12:00:00Z",
                sightings=((None, ["Widgets", "Ports" if i % 2 else "Rail"]),)))
        rows += [
            article(7, source="gov-b", url="https://www.canada.ca/en/widget-agency/news/a.html",
                    title="Minister Doe opens Zeta port",
                    summary="The Zeta port cost $20 million and employs 300 staff.",
                    date_status="updated_only", published_at=None,
                    updated_at="2026-01-12T10:00:00Z", sightings=((None, ["news releases"]),)),
            article(8, source="gov-b", url="https://www.canada.ca/en/widget-agency/news/b.html",
                    title="Zeta port backgrounder",
                    summary="More than $20 million supports the Zeta port and 300 staff.",
                    date_status="updated_only", published_at=None,
                    updated_at="2026-01-12T10:30:00Z", sightings=((None, ["backgrounders"]),)),
            article(9, url="https://example.test/business/live/x",
                    title="Markets day; Zeta port opens - as it happened",
                    summary="Zeta port opens. Acme widget plan revealed. Shares rise 4.2%."),
        ]
        return rows

    def setUp(self):
        super().setUp()
        self.corpus, _ = self.load(self.rows())
        self.model = cand.corpus_model(self.corpus.articles)

    def test_exposes_intermediates(self):
        m = self.model
        self.assertEqual(m.numbers[7], {"$20000000", "300"})
        self.assertIn("$20000000", m.distinctive)
        self.assertIn(("agency", "notice", "board"), m.wrappers["pub-a"].prefixes)
        self.assertEqual(len(m.boilerplate["pub-a"]), 1)
        self.assertEqual(m.title_tokens[7], ("minister", "doe", "open", "zeta", "port"))
        sentences = text_module.split_sentences(self.corpus.articles[0].summary)
        self.assertEqual(len(m.sentence_tokens[1]), len(sentences))
        self.assertEqual(m.sentence_tokens[1][0], ())   # wrapper prefix sentence
        self.assertEqual(m.sentence_tokens[1][1], ())   # boilerplate sentence
        self.assertIn("alpha", m.sentence_tokens[1][2])
        self.assertIn("widget", m.common["pub-a"])
        vocabulary = set()
        for i in m.title_tokens:
            vocabulary |= set(m.title_tokens[i]) | {t for s in m.sentence_tokens[i] for t in s}
        self.assertEqual(set(m.idf), vocabulary)
        self.assertEqual([f.id for f in m.features], [1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_sentence_tokens_flatten_to_suppressed_summary(self):
        m = self.model
        for a in self.corpus.articles:
            with self.subTest(article=a.id):
                flat = [t for s in m.sentence_tokens[a.id] for t in s]
                self.assertEqual(flat, cand.suppressed_summary_tokens(
                    a.summary, m.wrappers[a.source_id], m.boilerplate.get(a.source_id, frozenset())))

    def test_build_features_unchanged(self):
        reference = reference_build_features(self.corpus.articles)
        self.assertEqual(cand.build_features(self.corpus.articles), reference)
        self.assertEqual(self.model.features, reference)
        self.assertEqual(cand.generate_candidates(cand.build_features(self.corpus.articles)),
                         cand.generate_candidates(reference))
        self.assertGreater(len(cand.generate_candidates(reference)), 0)

    def test_independent_of_input_order(self):
        shuffled = list(self.corpus.articles)
        random.Random(5).shuffle(shuffled)
        self.assertEqual(cand.corpus_model(shuffled), self.model)


class CatchAllCategoryTests(DatabaseTestCase):
    def two(self, cats, summary_b="Dockers walk out."):
        self.count = getattr(self, "count", 0) + 1
        c, _ = self.load([
            article(1, title="Alpha", summary="A widget plan.", sightings=((None, cats),)),
            article(2, title="Bravo", summary=summary_b, sightings=((None, cats),)),
        ], f"two{self.count}.sqlite3")
        return cand.build_features(c.articles)

    def test_frozen_value(self):
        self.assertEqual(rules.CATCH_ALL_CATEGORIES, ("POLICY_AREA=GENINFO",))
        self.assertEqual(cand.DEFAULT_CATCH_ALL_CATEGORIES, {"POLICY_AREA=GENINFO"})

    def test_shared_geninfo_alone_does_not_surface(self):
        features = self.two(["POLICY_AREA=GENINFO"])
        self.assertEqual([f.tags for f in features], [frozenset(), frozenset()])
        self.assertEqual(cand.generate_candidates(features), ())

    def test_geninfo_adds_no_tag_route_when_pair_surfaces_otherwise(self):
        c, _ = self.load([
            article(1, title="Alpha", summary="A £7m plan.",
                    sightings=((None, ["POLICY_AREA=GENINFO"]),)),
            article(2, title="Bravo", summary="A £7m deal.",
                    sightings=((None, ["POLICY_AREA=GENINFO"]),)),
        ], "n.sqlite3")
        (s,) = cand.generate_candidates(cand.build_features(c.articles))
        self.assertEqual(s.routes, ("number",))
        self.assertEqual(s.tags, ())

    def test_other_eligible_tag_still_works(self):
        (s,) = cand.generate_candidates(self.two(["POLICY_AREA=GENINFO", "Widgets"]))
        self.assertEqual(s.routes, ("tag",))
        self.assertEqual(s.tags, (("widgets", 2),))

    def test_case_insensitive_and_composite_not_excluded(self):
        self.assertEqual(cand.generate_candidates(self.two([" policy_area=geninfo "])), ())
        (s,) = cand.generate_candidates(self.two(["POLICY_AREA=GENINFO,ENERGY"]))
        self.assertEqual(s.tags, (("policy_area=geninfo,energy", 2),))

    def test_in_configuration_identity(self):
        self.assertEqual(cand.candidate_config()["CATCH_ALL_CATEGORIES"], ["policy_area=geninfo"])
        self.assertNotEqual(cand.candidate_config(), cand.candidate_config(()))


class ArtifactTests(DatabaseTestCase):
    ROWS = [
        article(1, title="Acme opens Zeta widget plant", summary="The plant cost £7m."),
        article(2, title="Zeta widget plant opened by Acme", summary="A £7m plant opens."),
        article(3, title="Fooland port strike", summary="Dockers walk out."),
    ]

    def run_cli(self, db, out, sha):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return predict.main(["--db", str(db), "--out", str(out), "--expected-sha256", sha])

    def test_schema_determinism_and_no_text(self):
        _, db = self.load(self.ROWS)
        sha = corpus.file_sha256(db)
        out1, out2 = self.dir / "run" / "a.json", self.dir / "run" / "b.json"
        self.assertEqual(self.run_cli(db, out1, sha), 0)
        self.assertEqual(self.run_cli(db, out2, sha), 0)
        raw = out1.read_bytes()
        self.assertEqual(raw, out2.read_bytes())
        data = json.loads(raw)
        self.assertEqual(set(data), {"format", "format_version", "run", "stage", "corpus", "config",
                                     "routes", "pair_count_total", "candidate_count",
                                     "candidates"})
        self.assertEqual(data["format"], "atlas-signal/e002-stage-a-candidates")
        self.assertEqual(data["corpus"], {"sha256": sha, "article_count": 3})
        self.assertEqual(data["pair_count_total"], 3)
        self.assertEqual(data["candidate_count"], len(data["candidates"]))
        self.assertEqual(data["config"]["design_commit"], predict.DESIGN_COMMIT)
        self.assertEqual(data["config"]["values"]["CAND_COSINE"], "0.15")
        self.assertEqual(data["config"]["values"]["CATCH_ALL_CATEGORIES"],
                         ["policy_area=geninfo"])
        first = data["candidates"][0]
        self.assertEqual((first["a"], first["b"]), (1, 2))
        self.assertEqual(set(first), {"a", "b", "routes", "time_gap_seconds", "cosine",
                                      "rank_a_to_b", "rank_b_to_a", "numbers", "entities",
                                      "tags", "same_issuer_within_hours"})
        self.assertIn("number", first["routes"])
        self.assertEqual(first["numbers"], [{"id": predict.key_id("number", "£7000000"),
                                             "df": 2}])
        text = raw.decode("ascii").casefold()
        for word in ("acme", "zeta", "widget", "fooland", "dockers", "plant", "£7m", "7000000"):
            self.assertNotIn(word, text)
        forbidden = {"label", "event", "copy_of", "distinct", "unresolved", "role", "relation"}
        self.assertFalse(forbidden & set(json.dumps(data["candidates"]).split('"')))

    def test_refuses_to_overwrite(self):
        _, db = self.load(self.ROWS)
        out = self.dir / "a.json"
        out.write_text("existing", encoding="utf-8")
        self.assertEqual(self.run_cli(db, out, corpus.file_sha256(db)), 2)
        self.assertEqual(out.read_text(encoding="utf-8"), "existing")

    def test_wrong_hash_rejected(self):
        _, db = self.load(self.ROWS)
        with self.assertRaises(corpus.CorpusHashMismatch):
            self.run_cli(db, self.dir / "a.json", "0" * 64)
        self.assertFalse((self.dir / "a.json").exists())


if __name__ == "__main__":
    unittest.main()
