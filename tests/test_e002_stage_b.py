"""Experiment 002 Stage B: candidate-artifact loading, S5 steps 1-5, S6 and S7.

Synthetic fixtures only; no repository gold and no sealed real artifact.
"""

import copy
import dataclasses
import json
import math
import random
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from research.e002 import candidates as cand
from research.e002 import corpus, predict, rules
from research.e002 import stage_b as sb
from research.e002.text import char_trigrams, clean_text, compare_title_templates, jaccard
from tests.e002_support import article, build_db

# Characters outside the ASCII fixtures; each appended one adds one new trigram.
UNIQUE = "αβγδεζηθικλμνξοπρτυφχψωабвгдежзийклмнопрстуфхцчшщ"

BASE_SUMMARY = ("Acme Corporation has opened a new widget plant in Fooland, creating jobs "
                "for the local region and expanding production capacity there.")
FILLER_WORDS = ["harbour", "orchard", "glacier", "lantern", "meadow", "quarry", "saddle",
                "thistle", "vineyard", "walrus", "yonder", "zephyr"]


def fillers(start, n=12, hours=0):
    return [article(start + i, url=f"https://example.test/filler/{w}",
                    title=f"{w.capitalize()} story number {w}",
                    summary=f"A separate {w} report about the {w} season and {w} prices.",
                    published_at=f"2026-01-10T{12 + hours:02d}:00:00Z")
            for i, w in enumerate(FILLER_WORDS[:n])]


def exact_variant(base: str, p: int, q: int):
    """(a, at, below): Jaccard(a, at) == p/q exactly and Jaccard(a, below) < p/q."""
    chars = iter(UNIQUE)
    a = base
    while len(char_trigrams(a)) % p:
        a += next(chars)
    m = len(char_trigrams(a)) * (q - p) // p
    extra = "".join(next(chars) for _ in range(m + 1))
    return a, a + extra[:m], a + extra


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.n = 0

    def load(self, rows):
        self.n += 1
        db = build_db(self.dir / f"c{self.n}.sqlite3", rows)
        c = corpus.load_corpus(db, expected_sha256=corpus.file_sha256(db))
        return c, cand.corpus_model(c.articles)

    def articles(self, rows):
        c, model = self.load(rows)
        return {a.id: a for a in c.articles}, model


# --- copy_of rule -----------------------------------------------------------------


class CopyOfRuleTests(Fixture):
    def pair(self, a_kwargs, b_kwargs, extra_rows=()):
        rows = [article(1, **a_kwargs), article(2, **b_kwargs), *extra_rows, *fillers(10)]
        arts, model = self.articles(rows)
        return sb.copy_of_qualifies(arts[1], arts[2], model)

    def base(self, **kw):
        d = dict(title="Acme opens Zeta widget plant", summary=BASE_SUMMARY,
                 url=None, published_at="2026-01-10T12:00:00Z")
        d.update(kw)
        return d

    def test_identical_pair_qualifies(self):
        self.assertTrue(self.pair(self.base(url="https://example.test/a"),
                                  self.base(url="https://example.test/b")))

    def test_title_jaccard_boundary(self):
        self.assertEqual(rules.COPY_TITLE_JACCARD, Fraction(9, 10))
        a, at, below = exact_variant("Acme opens Zeta widget plant", 9, 10)
        self.assertEqual(jaccard(char_trigrams(a), char_trigrams(at)), Fraction(9, 10))
        self.assertLess(jaccard(char_trigrams(a), char_trigrams(below)), Fraction(9, 10))
        self.assertTrue(self.pair(self.base(title=a, url="https://example.test/a"),
                                  self.base(title=at, url="https://example.test/b")))
        self.assertFalse(self.pair(self.base(title=a, url="https://example.test/a"),
                                   self.base(title=below, url="https://example.test/b")))

    def test_summary_jaccard_boundary(self):
        self.assertEqual(rules.COPY_SUMMARY_JACCARD, Fraction(4, 5))
        a, at, below = exact_variant(BASE_SUMMARY, 4, 5)
        self.assertEqual(jaccard(char_trigrams(a), char_trigrams(at)), Fraction(4, 5))
        self.assertTrue(self.pair(self.base(summary=a, url="https://example.test/a"),
                                  self.base(summary=at, url="https://example.test/b")))
        self.assertFalse(self.pair(self.base(summary=a, url="https://example.test/a"),
                                   self.base(summary=below, url="https://example.test/b")))

    def test_minimum_summary_length(self):
        self.assertEqual(rules.COPY_MIN_SUMMARY_CHARS, 80)
        s80 = BASE_SUMMARY[:80].rstrip() + "x" * (80 - len(BASE_SUMMARY[:80].rstrip()))
        self.assertEqual(len(clean_text(s80)), 80)
        self.assertTrue(self.pair(self.base(summary=s80, url="https://example.test/a"),
                                  self.base(summary=s80, url="https://example.test/b")))
        s79 = s80[:79]
        self.assertFalse(self.pair(self.base(summary=s79, url="https://example.test/a"),
                                   self.base(summary=s79, url="https://example.test/b")))

    def test_48_hour_boundary(self):
        self.assertEqual(rules.COPY_MAX_HOURS, 48)
        a = self.base(url="https://example.test/a", published_at="2026-01-10T12:00:00Z")
        self.assertTrue(self.pair(a, self.base(url="https://example.test/b",
                                               published_at="2026-01-12T12:00:00Z")))
        self.assertFalse(self.pair(a, self.base(url="https://example.test/b",
                                                published_at="2026-01-12T12:00:01Z")))

    def test_same_publisher_required(self):
        self.assertFalse(self.pair(self.base(url="https://example.test/a"),
                                   self.base(url="https://other.test/a")))
        self.assertTrue(self.pair(self.base(url="https://www.example.test/a"),
                                  self.base(url="https://example.test/b")))

    def test_same_document_type_or_format_class(self):
        gov = dict(source="gov-b", date_status="updated_only", published_at=None,
                   updated_at="2026-01-10T12:00:00Z")

        def g(url, cats):
            return self.base(url=f"https://www.canada.ca/en/unit/news/{url}.html",
                             sightings=((None, cats),), **gov)
        self.assertTrue(self.pair(g("a", ["news releases"]), g("b", ["news releases"])))
        self.assertFalse(self.pair(g("a", ["news releases"]), g("b", ["backgrounders"])))
        self.assertFalse(self.pair(g("a", ["news releases"]), g("b", ["Topic"])))
        self.assertFalse(self.pair(self.base(url="https://example.test/news/a"),
                                   self.base(url="https://example.test/commentisfree/b")))
        self.assertTrue(self.pair(self.base(url="https://example.test/commentisfree/a"),
                                  self.base(url="https://example.test/opinion/b")))

    def test_identical_distinctive_number_sets(self):
        seven = BASE_SUMMARY + " It cost £7m."
        eight = BASE_SUMMARY + " It cost £8m."
        self.assertFalse(self.pair(self.base(summary=seven, url="https://example.test/a"),
                                   self.base(summary=eight, url="https://example.test/b")))
        self.assertTrue(self.pair(self.base(summary=seven, url="https://example.test/a"),
                                  self.base(summary=seven, url="https://example.test/b")))
        # A non-distinctive number (DF > NUM_MAX_DF) does not count.
        common = [article(30 + i, url=f"https://example.test/common/{i}",
                          title=f"Common item {i}", summary=f"Unrelated note {i} on 300 units.")
                  for i in range(rules.NUM_MAX_DF + 1)]
        with_300 = BASE_SUMMARY + " It has 300 units."
        without = BASE_SUMMARY + " It has many units."
        self.assertTrue(self.pair(self.base(summary=with_300, url="https://example.test/a"),
                                  self.base(summary=without, url="https://example.test/b"),
                                  extra_rows=common))


# --- Loader and pair state ------------------------------------------------------


def state_rows(ids=(1, 2, 3, 4, 5, 6)):
    """Ordinary copy pair, container copy pair, near-but-failing pair, fillers."""
    i1, i2, i3, i4, i5, i6 = ids
    near = BASE_SUMMARY.replace("widget plant", "gadget works").replace("Fooland", "Barland")
    return [
        article(i1, url="https://example.test/news/a", title="Acme opens Zeta widget plant",
                summary=BASE_SUMMARY),
        article(i2, url="https://example.test/news/b", title="Acme opens Zeta widget plant",
                summary=BASE_SUMMARY, published_at="2026-01-10T13:00:00Z"),
        article(i3, url="https://example.test/business/live/c",
                title="Zeta port strike continues", summary=BASE_SUMMARY.replace("Acme", "Zeta")),
        article(i4, url="https://example.test/business/live/d",
                title="Zeta port strike continues", summary=BASE_SUMMARY.replace("Acme", "Zeta")),
        article(i5, url="https://example.test/news/e", title="Acme opens Zeta widget plant",
                summary=near),
        article(i6, url="https://example.test/news/f", title="Unrelated quarry note",
                summary="A short quarry note."),
        *fillers(100),
    ]


class StateFixture(Fixture):
    def build(self, ids=(1, 2, 3, 4, 5, 6)):
        c, model = self.load(state_rows(ids))
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        return c, model, artifact

    def write(self, artifact):
        self.n += 1
        path = self.dir / f"a{self.n}.json"
        path.write_text(predict.canonical_json(artifact), encoding="utf-8")
        return path


class LoaderTests(StateFixture):
    def setUp(self):
        super().setUp()
        self.c, self.model, self.artifact = self.build()

    def rejects(self, artifact):
        with self.assertRaises(sb.StageBInputError):
            sb.validate_candidate_artifact(artifact, self.c, self.model)

    def test_valid_artifact_accepted_from_file(self):
        path = self.write(self.artifact)
        pairs = sb.load_candidate_artifact(path, corpus.file_sha256(path), self.c, self.model)
        self.assertEqual(list(pairs), sorted((r["a"], r["b"]) for r in self.artifact["candidates"]))
        self.assertTrue(all(p.terminal is None and p.edge is None for p in pairs.values()))

    def test_wrong_file_sha_refused(self):
        path = self.write(self.artifact)
        with self.assertRaises(sb.StageBInputError):
            sb.load_candidate_artifact(path, "0" * 64, self.c, self.model)

    def test_header_mismatches_refused(self):
        for key, value in (("format", "x"), ("format_version", 2), ("stage", "B"),
                           ("run", "run2"), ("routes", list(cand.ROUTES)[::-1]),
                           ("pair_count_total", 1), ("candidate_count", 0)):
            with self.subTest(key=key):
                art = copy.deepcopy(self.artifact)
                art[key] = value
                self.rejects(art)
        for key, value in (("sha256", "0" * 64), ("article_count", 3)):
            with self.subTest(corpus=key):
                art = copy.deepcopy(self.artifact)
                art["corpus"][key] = value
                self.rejects(art)

    def test_configuration_mismatches_refused(self):
        art = copy.deepcopy(self.artifact)
        art["config"]["values"]["CAND_COSINE"] = "0.10"
        self.rejects(art)  # values differ from the committed configuration
        art["config"]["sha256"] = hashlib_sha(art["config"]["values"])
        self.rejects(art)  # internally consistent, still not the committed one
        art = copy.deepcopy(self.artifact)
        art["config"]["sha256"] = "0" * 64
        self.rejects(art)
        art = copy.deepcopy(self.artifact)
        art["config"]["design_commit"] = "0" * 40
        self.rejects(art)

    def test_bad_pairs_refused(self):
        first = self.artifact["candidates"][0]
        n = len(self.c.articles)
        for change in ({"a": first["b"], "b": first["a"]}, {"b": first["a"]}, {"a": 0},
                       {"b": 999}, {"a": True}, {"routes": []}, {"routes": ["bogus"]}):
            with self.subTest(change=change):
                art = copy.deepcopy(self.artifact)
                art["candidates"][0].update(change)
                self.rejects(art)
        art = copy.deepcopy(self.artifact)
        art["candidates"].append(dict(first))
        art["candidate_count"] += 1
        self.rejects(art)
        art = copy.deepcopy(self.artifact)
        del art["candidates"][0]["tags"]
        self.rejects(art)
        self.assertGreater(n, 0)

    def test_tampered_cosine_refused(self):
        art = copy.deepcopy(self.artifact)
        art["candidates"][0]["cosine"] += 1e-12
        self.rejects(art)

    def test_candidate_order_does_not_change_pair_state_order(self):
        shuffled = copy.deepcopy(self.artifact)
        random.Random(1).shuffle(shuffled["candidates"])
        a = sb.validate_candidate_artifact(self.artifact, self.c, self.model)
        b = sb.validate_candidate_artifact(shuffled, self.c, self.model)
        self.assertEqual(list(a), list(b))
        self.assertEqual(list(a), sorted(a))


def hashlib_sha(values):
    import hashlib
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode("ascii")).hexdigest()


class CopyOfStateTests(StateFixture):
    def run_copy(self, ids=(1, 2, 3, 4, 5, 6)):
        c, model, artifact = self.build(ids)
        state = sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))
        sb.apply_copy_of(state)
        return c, state

    def test_ordinary_container_and_failing_pairs(self):
        c, state = self.run_copy()
        for key in ((1, 2), (3, 4), (1, 5)):
            self.assertIn(key, state.pairs)  # fixture precondition: all are candidates
        ordinary, container, failing = state.pairs[(1, 2)], state.pairs[(3, 4)], state.pairs[(1, 5)]
        self.assertEqual((ordinary.terminal, ordinary.edge), ("copy_of", "copy_of"))
        self.assertEqual((container.terminal, container.edge), ("copy_of", None))
        self.assertEqual((failing.terminal, failing.edge), (None, None))
        self.assertEqual(state.edges, [sb.Edge("copy_of", 1, 2, ordinary.cosine)])
        terminal = {k for k, p in state.pairs.items() if p.terminal}
        self.assertEqual(terminal, {(1, 2), (3, 4)})

    def test_rerun_is_idempotent(self):
        c, state = self.run_copy()
        before = (copy.deepcopy(state.pairs), list(state.edges))
        sb.apply_copy_of(state)
        self.assertEqual((state.pairs, state.edges), before)

    def test_article_id_permutation_and_input_order(self):
        def by_url(c, state):
            url = {a.id: a.normalized_url for a in c.articles}
            return ({tuple(sorted((url[p.a], url[p.b]))): (p.terminal, p.edge)
                     for p in state.pairs.values()},
                    sorted(tuple(sorted((url[e.a], url[e.b]))) for e in state.edges))
        first = by_url(*self.run_copy((1, 2, 3, 4, 5, 6)))
        second = by_url(*self.run_copy((60, 7, 55, 9, 3, 41)))
        self.assertEqual(first, second)


# --- S5 step 2: template slot conflict --------------------------------------------

CARE_SUMMARY = "A resident died in care at the home this week, local officials said today."
NOTICE_SUMMARY = ("Official notice about the regional widget programme, setting out the "
                  "schedule, eligibility and contacts for applicants across the region.")


class SlotConflictPrimitiveTests(Fixture):
    def setUp(self):
        super().setUp()
        gov = dict(source="gov-b", date_status="updated_only", published_at=None,
                   updated_at="2026-01-10T12:00:00Z")
        titles = {
            1: "Death of a resident at Northwind Home",
            2: "Death of a resident at Barland House",
            3: "Statement by Doe with Fooland leader",
            4: "Statement by Roe with Fooland Doe",
            5: "Minister Doe visits Fooland",
            6: "Minister Roe tours Barland",
            7: "Fund grants £5m to Northwind",
            8: "Fund grants £7m to Northwind",
            9: "Fund grants £5 m to Northwind",
        }
        rows = [article(i, url=f"https://example.test/news/{i}", title=t, summary="Note.")
                for i, t in titles.items()]
        rows += [
            article(20, url="https://example.test/commentisfree/20", title=titles[2], summary="Note."),
            article(21, source="gov-b", url="https://www.canada.ca/en/u/news/21.html",
                    title=titles[2], summary="Note.", sightings=((None, ["news releases"]),),
                    **{k: v for k, v in gov.items() if k != "source"}),
            article(22, source="gov-b", url="https://www.canada.ca/en/u/news/22.html",
                    title=titles[1], summary="Note.", sightings=((None, ["news releases"]),),
                    **{k: v for k, v in gov.items() if k != "source"}),
            article(23, source="gov-b", url="https://www.canada.ca/en/u/news/23.html",
                    title=titles[1], summary="Note.", sightings=((None, ["backgrounders"]),),
                    **{k: v for k, v in gov.items() if k != "source"}),
            article(24, source="gov-b", url="https://www.canada.ca/en/u/news/24.html",
                    title=titles[1], summary="Note.", sightings=((None, ["Topic"]),),
                    **{k: v for k, v in gov.items() if k != "source"}),
        ]
        self.arts, _ = self.articles(rows)

    def conflict(self, x, y):
        return sb.slot_conflict(self.arts[x], self.arts[y])

    def test_same_template_different_slots_conflict(self):
        self.assertTrue(self.conflict(1, 2))
        self.assertTrue(self.conflict(2, 1))
        self.assertTrue(self.conflict(21, 22))  # gov-b, both news releases

    def test_overlapping_slots_and_low_similarity_do_not_conflict(self):
        self.assertFalse(self.conflict(3, 4))   # residual capitalized tokens overlap
        self.assertFalse(self.conflict(5, 6))   # shared share 1/4 < 0.50
        self.assertFalse(self.conflict(1, 1))   # identical title

    def test_scope_must_match(self):
        self.assertFalse(self.conflict(2, 21))   # different source
        self.assertFalse(self.conflict(1, 20))   # same source, format standard vs opinion
        self.assertFalse(self.conflict(21, 23))  # news releases vs backgrounders
        self.assertFalse(self.conflict(21, 24))  # document type vs none

    def test_amount_slots_follow_the_text_helper(self):
        self.assertTrue(self.conflict(7, 8))     # £5m vs £7m
        self.assertFalse(self.conflict(7, 9))    # £5m and £5 m are the same raw token
        same_scope = [(x, y) for x in (1, 2, 3, 4, 5, 6, 7, 8, 9)
                      for y in (1, 2, 3, 4, 5, 6, 7, 8, 9) if x < y]
        for x, y in same_scope:
            with self.subTest(pair=(x, y)):
                self.assertEqual(self.conflict(x, y), compare_title_templates(
                    self.arts[x].title, self.arts[y].title).slot_conflict)


def slot_rows(ids=(1, 2, 3, 4, 5, 6, 7)):
    s1, s2, s3, c1, c2, f1, f2 = ids
    return [
        article(s1, url="https://example.test/care/a", title="Death of a resident at Northwind Home",
                summary=CARE_SUMMARY),
        article(s2, url="https://example.test/care/b", title="Death of a resident at Barland House",
                summary=CARE_SUMMARY, published_at="2026-01-10T13:00:00Z"),
        # Same template, outside the 14-day gate: never a Stage A candidate.
        article(s3, url="https://example.test/care/c", title="Death of a resident at Corland Lodge",
                summary=CARE_SUMMARY, published_at="2026-02-20T12:00:00Z",
                first_seen_at="2026-02-20T12:00:00Z"),
        article(c1, url="https://example.test/notice/41",
                title="Official notice about the regional widget programme report 41",
                summary=NOTICE_SUMMARY),
        article(c2, url="https://example.test/notice/42",
                title="Official notice about the regional widget programme report 42",
                summary=NOTICE_SUMMARY, published_at="2026-01-10T12:30:00Z"),
        article(f1, url="https://example.test/news/f1", title="Acme expands Zeta widget plant",
                summary=BASE_SUMMARY),
        article(f2, url="https://example.test/news/f2",
                title="Zeta widget plant expansion approved for Acme", summary=BASE_SUMMARY,
                published_at="2026-01-10T14:00:00Z"),
        *fillers(100),
    ]


class SlotConflictStateTests(Fixture):
    def run_s5(self, ids=(1, 2, 3, 4, 5, 6, 7)):
        c, model = self.load(slot_rows(ids))
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        state = sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))
        sb.apply_copy_of(state)
        sb.apply_slot_conflict(state)
        return c, state

    def test_transitions(self):
        c, state = self.run_s5()
        for key in ((1, 2), (4, 5), (6, 7)):
            self.assertIn(key, state.pairs)  # fixture precondition: candidates
        conflict, overlap, failing = state.pairs[(1, 2)], state.pairs[(4, 5)], state.pairs[(6, 7)]
        self.assertEqual((conflict.terminal, conflict.cannot_link, conflict.edge),
                         ("template_slot_conflict", "template_slot_conflict", None))
        self.assertEqual((failing.terminal, failing.cannot_link, failing.edge), (None, None, None))
        # copy_of precedence: the pair satisfies both rules, copy_of stays.
        arts = {a.id: a for a in c.articles}
        self.assertTrue(sb.slot_conflict(arts[4], arts[5]))
        self.assertTrue(sb.copy_of_qualifies(arts[4], arts[5], state.model))
        self.assertEqual((overlap.terminal, overlap.cannot_link, overlap.edge),
                         ("copy_of", None, "copy_of"))
        self.assertEqual(state.edges, [sb.Edge("copy_of", 4, 5, overlap.cosine)])

    def test_non_candidate_conflict_is_a_blocking_fact_only(self):
        c, state = self.run_s5()
        self.assertNotIn((1, 3), state.pairs)
        self.assertNotIn((2, 3), state.pairs)
        arts = {a.id: a for a in c.articles}
        self.assertTrue(sb.slot_conflict(arts[1], arts[3]))
        self.assertTrue(sb.slot_conflict_cannot_link(state, 3, 1))
        # Querying creates no pair state, edge or decision for the non-candidate pair.
        self.assertNotIn((1, 3), state.pairs)
        self.assertEqual(len(state.edges), 1)

    def test_cannot_link_query_respects_copy_of_precedence(self):
        c, state = self.run_s5()
        self.assertTrue(sb.slot_conflict_cannot_link(state, 1, 2))
        self.assertFalse(sb.slot_conflict_cannot_link(state, 4, 5))   # copy_of pair
        self.assertFalse(sb.slot_conflict_cannot_link(state, 6, 7))

    def test_rerun_is_idempotent(self):
        c, state = self.run_s5()
        before = (copy.deepcopy(state.pairs), list(state.edges))
        sb.apply_copy_of(state)
        sb.apply_slot_conflict(state)
        self.assertEqual((state.pairs, state.edges), before)

    def test_article_id_permutation(self):
        def by_url(c, state):
            url = {a.id: a.normalized_url for a in c.articles}
            return {tuple(sorted((url[p.a], url[p.b]))): (p.terminal, p.cannot_link, p.edge)
                    for p in state.pairs.values()}
        self.assertEqual(by_url(*self.run_s5((1, 2, 3, 4, 5, 6, 7))),
                         by_url(*self.run_s5((70, 9, 33, 4, 58, 21, 2))))


# --- S5 step 3: companion documents ---------------------------------------------


def gov(i, unit, doc_type, title, summary, minute=0, second=0, hour=12, path="news"):
    slug = "-".join(title.lower().split())
    return article(i, source="gov-b", url=f"https://www.canada.ca/en/{unit}/{path}/{slug}.html",
                   title=title, summary=summary, date_status="updated_only", published_at=None,
                   updated_at=f"2026-01-10T{hour:02d}:{minute:02d}:{second:02d}Z",
                   sightings=((None, [doc_type]),))


REL, BKG = "news releases", "backgrounders"


def companion_rows(ids=tuple(range(1, 19))):
    i = iter(ids)
    harbour = "The harbour upgrade at Zeta adds new berths, cranes and storage for regional shippers."
    return [
        # 1, 2: similar text, 30 minutes apart -> cosine branch
        gov(next(i), "alpha-unit", REL, "Funding for Zeta harbour upgrade", harbour),
        gov(next(i), "alpha-unit", BKG, "Zeta harbour upgrade details", harbour, minute=30),
        # 3, 4: dissimilar text, shared distinctive amount -> number branch
        gov(next(i), "beta-unit", REL, "Ministers announce orchard grants",
            "Growers in the valley receive $20 million for irrigation."),
        gov(next(i), "beta-unit", BKG, "List of recipients",
            "Eligible cooperatives and schedules: $20 million total envelope."),
        # 5, 6: dissimilar text, only non-distinctive or excluded numbers shared
        gov(next(i), "gamma-unit", REL, "Lantern festival support",
            "In 2026 the 50 lantern makers share 300 units of support."),
        gov(next(i), "gamma-unit", BKG, "Programme schedule",
            "Timeline for 2026, 50 workshops and 300 units of materials."),
        # 7, 8: similar text, 60 minutes + 1 second apart
        gov(next(i), "delta-unit", REL, "Glacier station opens", harbour.replace("harbour", "glacier station")),
        gov(next(i), "delta-unit", BKG, "Glacier station details",
            harbour.replace("harbour", "glacier station"), hour=13, second=1),
        # 9, 10: similar text, exactly 60 minutes apart
        gov(next(i), "eta-unit", REL, "Meadow depot opens", harbour.replace("harbour", "meadow depot")),
        gov(next(i), "eta-unit", BKG, "Meadow depot details",
            harbour.replace("harbour", "meadow depot"), hour=13),
        # 11, 12: similar text, different issuing units
        gov(next(i), "theta-unit", REL, "Quarry road repaired", harbour.replace("harbour", "quarry road")),
        gov(next(i), "iota-unit", BKG, "Quarry road details", harbour.replace("harbour", "quarry road")),
        # 13, 14: similar text, both news releases
        gov(next(i), "kappa-unit", REL, "Saddle bridge reopens", harbour.replace("harbour", "saddle bridge")),
        gov(next(i), "kappa-unit", REL, "Saddle bridge reopens today",
            harbour.replace("harbour", "saddle bridge")),
        *[article(next(i), url=f"https://example.test/common/{k}", title=f"Common item {k}",
                  summary=f"Unrelated note {k} on 300 units.") for k in range(4)],
        *fillers(100),
    ]


class CompanionFixture(Fixture):
    IDS = tuple(range(1, 19))

    def build(self, ids=IDS, drop=()):
        c, model = self.load(companion_rows(ids))
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        artifact["candidates"] = [r for r in artifact["candidates"] if (r["a"], r["b"]) not in drop]
        artifact["candidate_count"] = len(artifact["candidates"])
        state = sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))
        return c, state

    def run_s5(self, ids=IDS, drop=()):
        c, state = self.build(ids, drop)
        sb.apply_copy_of(state)
        sb.apply_slot_conflict(state)
        sb.apply_companion(state)
        return c, state


class CompanionRuleTests(CompanionFixture):
    def setUp(self):
        super().setUp()
        self.c, self.state = self.build()
        self.arts = self.state.articles

    def qualifies(self, x, y, cos=None):
        p = self.state.pairs[(x, y)]
        return sb.companion_qualifies(self.arts[x], self.arts[y],
                                      p.cosine if cos is None else cos, self.state.model)

    def test_cosine_boundary_is_behavioural(self):
        # Pair 5, 6 meets every non-evidence condition and shares no distinctive number.
        self.assertTrue(self.qualifies(5, 6, cos=0.15))
        self.assertFalse(self.qualifies(5, 6, cos=math.nextafter(0.15, 0.0)))

    def test_number_branch_and_exclusions(self):
        self.assertLess(self.state.pairs[(3, 4)].cosine, 0.15)
        self.assertTrue(self.qualifies(3, 4))                 # shared $20 million
        self.assertTrue(self.qualifies(3, 4, cos=0.0))
        self.assertLess(self.state.pairs[(5, 6)].cosine, 0.15)
        self.assertFalse(self.qualifies(5, 6))                # 2026, 50, 300 do not count

    def test_time_boundary(self):
        self.assertTrue(self.qualifies(9, 10))                # exactly 60 minutes
        self.assertFalse(self.qualifies(7, 8))                # 60 minutes + 1 second

    def test_unit_and_type_pairing(self):
        self.assertIn((11, 12), self.state.pairs)             # fixture precondition
        self.assertFalse(self.qualifies(11, 12))              # different issuing units
        self.assertFalse(self.qualifies(13, 14))              # two news releases
        self.assertTrue(self.qualifies(1, 2))


class CompanionStateTests(CompanionFixture):
    def test_transitions(self):
        c, state = self.run_s5()
        for key in ((1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (13, 14)):
            self.assertIn(key, state.pairs)                   # fixture precondition
        for key in ((1, 2), (3, 4), (9, 10)):
            p = state.pairs[key]
            self.assertEqual((p.terminal, p.edge, p.cannot_link), ("companion", "companion", None))
        for key in ((5, 6), (7, 8), (11, 12), (13, 14)):
            self.assertIsNone(state.pairs[key].terminal)
        self.assertEqual(sorted((e.type, e.a, e.b) for e in state.edges),
                         [("companion", 1, 2), ("companion", 3, 4), ("companion", 9, 10)])

    def test_earlier_terminal_decisions_are_kept(self):
        for earlier, edge in (("copy_of", "copy_of"), ("template_slot_conflict", None)):
            with self.subTest(earlier=earlier):
                c, state = self.build()
                sb.apply_copy_of(state)
                sb.apply_slot_conflict(state)
                pair = state.pairs[(1, 2)]
                self.assertIsNone(pair.terminal)   # cannot arise naturally: types differ
                pair.terminal, pair.edge = earlier, edge
                sb.apply_companion(state)
                self.assertEqual((pair.terminal, pair.edge), (earlier, edge))
                self.assertNotIn((1, 2), {(e.a, e.b) for e in state.edges})

    def test_idempotent(self):
        c, state = self.run_s5()
        before = (copy.deepcopy(state.pairs), list(state.edges))
        sb.apply_companion(state)
        self.assertEqual((state.pairs, state.edges), before)

    def test_non_candidate_pair_gets_nothing(self):
        c, state = self.run_s5(drop={(1, 2)})
        self.assertNotIn((1, 2), state.pairs)
        self.assertTrue(sb.companion_qualifies(state.articles[1], state.articles[2],
                                               cand.pair_signals(state.model.features, 1, 2).cosine,
                                               state.model))
        self.assertNotIn((1, 2), {(e.a, e.b) for e in state.edges})

    def test_article_id_permutation(self):
        def by_url(c, state):
            url = {a.id: a.normalized_url for a in c.articles}
            return ({tuple(sorted((url[p.a], url[p.b]))): (p.terminal, p.edge)
                     for p in state.pairs.values()},
                    sorted(tuple(sorted((url[e.a], url[e.b]))) for e in state.edges))
        permuted = tuple(random.Random(4).sample(range(1, 60), 18))
        self.assertEqual(by_url(*self.run_s5()), by_url(*self.run_s5(permuted)))

    def test_container_companion_is_terminal_without_edge(self):
        rows = [gov(1, "alpha-unit", REL, "Funding for Zeta harbour upgrade",
                    "Harbour works cost $20 million."),
                gov(2, "alpha-unit", BKG, "Zeta harbour upgrade details",
                    "The $20 million harbour works.", path="live"),
                *fillers(100)]
        c, model = self.load(rows)
        self.assertTrue(next(a for a in c.articles if a.id == 2).container_flag)
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        state = sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))
        self.assertIn((1, 2), state.pairs)
        sb.apply_companion(state)
        pair = state.pairs[(1, 2)]
        self.assertEqual((pair.terminal, pair.edge, pair.cannot_link), ("companion", None, None))
        self.assertEqual(state.edges, [])


# --- S5 step 4: advisory match ---------------------------------------------------

ADV = "media advisories"
BOILER_GOV = "Media may attend and should register in advance with the press office before the event."


def adv_gov(i, unit, doc_type, title, summary, day=10, hour=10, minute=0, second=0, path="news",
            tag=""):
    slug = "-".join(title.lower().split()) + tag
    return article(i, source="gov-b", url=f"https://www.canada.ca/en/{unit}/{path}/{slug}.html",
                   title=title, summary=summary, date_status="updated_only", published_at=None,
                   updated_at=f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z",
                   sightings=((None, [doc_type]),) if doc_type else ((None, None),))


class AdvisoryRuleTests(Fixture):
    """Predicate-level tests; the cosine is passed explicitly to isolate anchor types."""

    def setUp(self):
        super().setUp()
        u = "anchor-unit"
        rows = [
            adv_gov(1, u, ADV, "Minister Doe to announce Zeta Harbour and Orchard Valley funding",
                    "Minister Doe will announce $21 million and $35 million for the orchard "
                    "irrigation scheme expansion near the harvest of plums at valley 2026 tonnes."),
            # numbers only (two shared distinctive amounts)
            adv_gov(2, u, REL, "Funding confirmed", "Grants of $21 million and $35 million.", hour=11),
            # overlapping rare trigrams only: "orchard irrigation scheme expansion"
            adv_gov(3, u, REL, "Works begin", "The orchard irrigation scheme expansion starts.", hour=11),
            # two shared entities only
            adv_gov(4, u, REL, "Zeta Harbour and Orchard Valley projects", "Projects proceed.", hour=11),
            # stopword trigram and number trigram only
            adv_gov(5, u, REL, "Valley update", "The harvest of plums at valley 2026 tonnes.", hour=11),
            # entity span that is also a rare trigram
            adv_gov(6, u, ADV, "Officials to visit Glacier Point Lighthouse", "A visit is planned."),
            adv_gov(7, u, REL, "Repairs at Glacier Point Lighthouse completed", "Repairs done.", hour=11),
            # title/summary boundary and cross-sentence summary
            adv_gov(8, "other-unit", REL, "Alpha Beta", "Gamma delta"),
            adv_gov(9, "other-unit", REL, "Note", "The orchard closed. Irrigation scheme restarted."),
            # unsuppressed boilerplate (repeated sentence)
            adv_gov(10, "board-unit", ADV, "Board meeting notice", BOILER_GOV),
            adv_gov(11, "board-unit", REL, "Board meeting held", BOILER_GOV, hour=11),
            # DF of "copper kettle festival": 5 articles, and "silver kettle festival": 6
            *[adv_gov(20 + k, "df-unit", REL, f"Item {k}", "The copper kettle festival returns.")
              for k in range(5)],
            *[adv_gov(30 + k, "df-unit", REL, f"Entry {k}", "The silver kettle festival returns.")
              for k in range(6)],
            *fillers(100),
        ]
        self.c, self.model = self.load(rows)
        self.arts = {a.id: a for a in self.c.articles}
        self.seqs = sb.rare_sequences(self.c.articles)

    def types(self, x, y, cos=0.0):
        return sb.advisory_anchor_types(self.arts[x], self.arts[y], cos, self.model, self.seqs)

    def qualifies(self, x, y, cos=0.0):
        return sb.advisory_qualifies(self.arts[x], self.arts[y], cos, self.model, self.seqs)

    def test_multiple_numbers_are_one_type(self):
        self.assertEqual(self.types(1, 2), {"number"})
        self.assertFalse(self.qualifies(1, 2))                 # one type alone fails
        self.assertTrue(self.qualifies(1, 2, cos=0.3))         # number + cosine

    def test_overlapping_trigrams_are_one_type(self):
        shared = self.seqs[1] & self.seqs[3]
        self.assertEqual(shared, {("orchard", "irrigation", "scheme"),
                                  ("irrigation", "scheme", "expansion")})
        self.assertEqual(self.types(1, 3), {"sequence"})
        self.assertFalse(self.qualifies(1, 3))

    def test_entities_are_one_type_and_capped(self):
        f = {x.id: x for x in self.model.features}
        self.assertEqual(f[1].entities & f[4].entities, {"zeta harbour", "orchard valley"})
        self.assertEqual(self.types(1, 4), {"entity"})
        self.assertFalse(self.qualifies(1, 4))                 # two entities alone fail
        self.assertTrue(self.qualifies(1, 4, cos=0.3))         # entity + cosine; not disqualified

    def test_entity_and_sequence_from_one_span(self):
        self.assertEqual(self.types(6, 7), {"entity", "sequence"})
        self.assertTrue(self.qualifies(6, 7))

    def test_cosine_boundary(self):
        self.assertTrue(self.qualifies(1, 4, cos=0.25))
        self.assertFalse(self.qualifies(1, 4, cos=math.nextafter(0.25, 0.0)))

    def test_trigram_eligibility(self):
        self.assertNotIn(("harvest", "of", "plums"), self.seqs[5])        # stopword
        self.assertFalse(any("2026" in t for t in self.seqs[5]))           # number expression
        self.assertEqual(self.types(1, 5), frozenset())
        self.assertEqual(self.seqs[8], frozenset())                       # no title->summary trigram
        self.assertIn(("closed", "irrigation", "scheme"), self.seqs[9])   # crosses sentences

    def test_unsuppressed_boilerplate_is_eligible(self):
        key = text_sentence_key(BOILER_GOV)
        self.assertIn(key, self.model.boilerplate["gov-b"])               # suppressed in vectors
        self.assertIn(("media", "may", "attend"), self.seqs[10])       # but used for sequences
        self.assertIn("sequence", self.types(10, 11))

    def test_df_boundary(self):
        self.assertIn(("copper", "kettle", "festival"), self.seqs[20])    # DF 5
        self.assertNotIn(("silver", "kettle", "festival"), self.seqs[30]) # DF 6

    def test_roles_time_and_unit(self):
        def roles(adv_kw, doc_kw):
            rows = [adv_gov(1, "u", ADV, "Zeta Harbour event", "Funds of $21 million.", **adv_kw),
                    adv_gov(2, doc_kw.pop("unit", "u"), doc_kw.pop("doc_type", REL),
                            "Zeta Harbour event held", "Funds of $21 million.", **doc_kw),
                    *fillers(100)]
            c, model = self.load(rows)
            arts = {a.id: a for a in c.articles}
            return sb.advisory_roles(arts[1], arts[2])
        start = dict(day=10, hour=10)
        self.assertIsNotNone(roles(start, dict(day=17, hour=10)))           # exactly 7 days
        self.assertIsNone(roles(start, dict(day=17, hour=10, second=1)))    # 7 days + 1 s
        self.assertIsNone(roles(start, dict(day=10, hour=10)))              # equal time
        self.assertIsNone(roles(start, dict(day=9, hour=10)))               # document earlier
        self.assertIsNone(roles(start, dict(day=10, hour=11, unit="v")))    # different unit
        self.assertIsNone(roles(start, dict(day=10, hour=11, doc_type=ADV)))  # two advisories
        self.assertIsNotNone(roles(start, dict(day=10, hour=11, doc_type=None)))  # untyped document
        rows = [article(1, title="Zeta Harbour event", summary="Funds of $21 million.",
                        sightings=((None, [ADV]),)),
                article(2, title="Zeta Harbour event held", summary="Funds of $21 million.",
                        published_at="2026-01-10T13:00:00Z"), *fillers(100)]
        c, _ = self.load(rows)
        arts = {a.id: a for a in c.articles}
        self.assertIsNone(sb.advisory_roles(arts[1], arts[2]))             # no issuing unit


def text_sentence_key(sentence):
    from research.e002.text import sentence_key
    return sentence_key(sentence)


def advisory_rows(ids=tuple(range(1, 20))):
    i = iter(ids)
    position = iter(range(100))  # row position, stable under ID permutation

    def pair_doc(unit, name, amount, hour, path="news", doc_type=REL):
        return adv_gov(next(i), unit, doc_type, f"{name} event held", f"Grants of {amount}.",
                       hour=hour, path=path, tag=f"-{next(position)}")

    def advisory(unit, name, amount):
        return adv_gov(next(i), unit, ADV, f"{name} event planned", f"Grants of {amount}.",
                       tag=f"-{next(position)}")
    return [
        # unit u: advisory with two qualifying documents and one non-qualifying sibling
        advisory("u-unit", "Zeta Harbour", "$21 million"),                     # 1
        pair_doc("u-unit", "Zeta Harbour", "$21 million", 11),                  # 2
        pair_doc("u-unit", "Zeta Harbour", "$21 million", 12),                  # 3
        adv_gov(next(i), "u-unit", REL, "Unrelated depot opening", "A depot opens.", hour=13),  # 4
        # unit v: one document, two advisories
        advisory("v-unit", "Orchard Valley", "$22 million"),                    # 5
        advisory("v-unit", "Orchard Valley", "$22 million"),                    # 6
        pair_doc("v-unit", "Orchard Valley", "$22 million", 11),                # 7
        # unit w: unique match
        advisory("w-unit", "Glacier Point", "$23 million"),                     # 8
        pair_doc("w-unit", "Glacier Point", "$23 million", 11),                 # 9
        # unit x: unique match with a container document
        advisory("x-unit", "Copper Kettle", "$24 million"),                     # 10
        pair_doc("x-unit", "Copper Kettle", "$24 million", 11, path="live", doc_type=None),  # 11
        # unit y: container and ordinary document both qualify for one advisory
        advisory("y-unit", "Silver Bridge", "$25 million"),                     # 12
        pair_doc("y-unit", "Silver Bridge", "$25 million", 11, path="live", doc_type=None),  # 13
        pair_doc("y-unit", "Silver Bridge", "$25 million", 12),                 # 14
        # unit z: chain A1-D1, A2-D1, A2-D2
        advisory("z-unit", "Amber Gate", "$26 million"),                        # 15
        advisory("z-unit", "Amber Gate", "$27 million"),                        # 16
        adv_gov(next(i), "z-unit", REL, "Amber Gate event held",
                "Grants of $26 million and $27 million.", hour=11),             # 17
        pair_doc("z-unit", "Amber Gate", "$27 million", 12),                    # 18
        pair_doc("w-unit", "Glacier Point", "$23 million", 9),                  # 19: before advisory
        *fillers(100),
    ]


class AdvisoryStateTests(Fixture):
    def build(self, ids=tuple(range(1, 20)), drop=()):
        c, model = self.load(advisory_rows(ids))
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        artifact["candidates"] = [r for r in artifact["candidates"] if (r["a"], r["b"]) not in drop]
        artifact["candidate_count"] = len(artifact["candidates"])
        return c, sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))

    def run_s5(self, ids=tuple(range(1, 20)), drop=()):
        c, state = self.build(ids, drop)
        for step in (sb.apply_copy_of, sb.apply_slot_conflict, sb.apply_companion, sb.apply_advisory):
            step(state)
        return c, state

    def outcome(self, state, x, y):
        p = state.pairs[(min(x, y), max(x, y))]
        return (p.terminal, p.edge, p.cannot_link)

    def test_preconditions(self):
        c, state = self.build()
        for key in ((1, 2), (1, 3), (1, 4), (5, 7), (6, 7), (8, 9), (10, 11), (12, 13), (12, 14),
                    (15, 17), (16, 17), (16, 18), (8, 19)):
            self.assertIn(key, state.pairs)
        arts = state.articles
        self.assertTrue(arts[11].container_flag and arts[13].container_flag)

    def test_multiplicity_and_unique_matches(self):
        c, state = self.run_s5()
        abstain = ("advisory_abstain", None, None)
        for key in ((1, 2), (1, 3), (5, 7), (6, 7), (12, 13), (12, 14), (15, 17), (16, 17), (16, 18)):
            with self.subTest(pair=key):
                self.assertEqual(self.outcome(state, *key), abstain)
        self.assertEqual(self.outcome(state, 1, 4), (None, None, None))     # non-qualifying sibling
        self.assertEqual(self.outcome(state, 8, 9), ("advisory", "advisory", None))
        self.assertEqual(self.outcome(state, 10, 11), ("advisory", None, None))  # container (C18)
        self.assertEqual(self.outcome(state, 8, 19), (None, None, None))    # document earlier
        self.assertEqual([(e.type, e.a, e.b) for e in state.edges], [("advisory", 8, 9)])

    def test_earlier_terminal_pair_is_skipped_and_not_counted(self):
        c, state = self.build()
        for step in (sb.apply_copy_of, sb.apply_slot_conflict, sb.apply_companion):
            step(state)
        state.pairs[(1, 2)].terminal = "copy_of"      # preset: H1 must hold generically
        sb.apply_advisory(state)
        self.assertEqual(self.outcome(state, 1, 2), ("copy_of", None, None))
        self.assertEqual(self.outcome(state, 1, 3), ("advisory", "advisory", None))

    def test_idempotent(self):
        c, state = self.run_s5()
        before = (copy.deepcopy(state.pairs), list(state.edges))
        sb.apply_advisory(state)
        self.assertEqual((state.pairs, state.edges), before)

    def test_non_candidate_pair_gets_nothing(self):
        c, state = self.run_s5(drop={(8, 9)})
        self.assertNotIn((8, 9), state.pairs)
        self.assertEqual(state.edges, [])

    def test_order_and_article_id_permutation(self):
        def by_url(c, state):
            url = {a.id: a.normalized_url for a in c.articles}
            return ({tuple(sorted((url[p.a], url[p.b]))): (p.terminal, p.edge)
                     for p in state.pairs.values()},
                    sorted(tuple(sorted((url[e.a], url[e.b]))) for e in state.edges))
        permuted = tuple(random.Random(6).sample(range(1, 80), 19))
        self.assertEqual(by_url(*self.run_s5()), by_url(*self.run_s5(permuted)))


# --- S5 step 5: follows_from cue ------------------------------------------------


def many_fillers(start, n=60):
    return [article(start + k, url=f"https://example.test/filler/n{k}", title=f"Note item{k}x",
                    summary=f"About entry{k}y only.", published_at="2026-01-10T08:00:00Z")
            for k in range(n)]


def pa(i, slug, title, summary, hour, minute=0, path="news"):
    return article(i, url=f"https://example.test/{path}/{slug}", title=title, summary=summary,
                   published_at=f"2026-01-10T{hour:02d}:{minute:02d}:00Z",
                   first_seen_at=f"2026-01-10T{hour:02d}:{minute:02d}:00Z")


class CueTests(unittest.TestCase):
    def cue(self, title):
        return sb.find_first_cue(title)

    def test_all_cue_forms(self):
        for phrase in sb.FOLLOWS_FROM_CUES:
            with self.subTest(phrase=phrase):
                c = self.cue(f"Rally {phrase} vote")
                self.assertEqual((c.phrase, c.antecedent, c.position), (phrase, "vote", 1))

    def test_whole_tokens_only(self):
        self.assertIsNone(self.cue("Afternoon session opens"))
        self.assertIsNone(self.cue("Thereafter calm returns"))
        self.assertEqual(self.cue("Shares fall After vote").phrase, "after")

    def test_first_cue_and_span(self):
        c = self.cue("Rally after vote following strike")
        self.assertEqual((c.phrase, c.antecedent), ("after", "vote following strike"))
        self.assertEqual(self.cue("Rally after vote; markets react").antecedent, "vote")
        self.assertEqual(self.cue("Rally after vote \u2013 as it happened").antecedent, "vote")
        self.assertEqual(self.cue("Rally after vote - live").antecedent, "vote")
        self.assertEqual(self.cue("Rally after vote\u2014live").antecedent, "vote-live")
        self.assertEqual(self.cue("Shares fall after; markets").antecedent, "")

    def test_time_expression_prefix(self):
        for span in ("10 years", "10 years of decline", "10-year decline", "two decades of decline",
                     "a decade of decline", "an hour later", "the year before the vote",
                     "\u00a35m years", "ten days"):
            with self.subTest(span=span):
                self.assertTrue(sb.time_expression_prefix(span))
        for span in ("years of decline", "several years of decline", "britain's 10 years of",
                     "eleven years", "a vote", "10 votes", "decade"):
            with self.subTest(span=span):
                self.assertFalse(sb.time_expression_prefix(span))


class ClauseMatchTests(Fixture):
    # |(2, 4, 2, 1)| = 5, so the cosine with {"x": 1} is exactly 2/5.
    AT_040 = {"x": 2.0, "a": 4.0, "b": 2.0, "c": 1.0}

    def test_cosine_boundary(self):
        empty = frozenset()
        self.assertTrue(sb.clause_matches(self.AT_040, empty, {"x": 1.0}, empty, empty))
        below = dict(self.AT_040, d=0.01)
        self.assertFalse(sb.clause_matches(below, empty, {"x": 1.0}, empty, empty))

    def test_number_branch_needs_number_and_positive_shared_token(self):
        rows = ([article(i, url=f"https://example.test/h/{i}", title=f"Harbour item{i}z",
                         summary="Harbour note.") for i in range(1, 5)]
                + [article(i, source="gov-b", url=f"https://example.test/g/{i}",
                           title=f"Gov item{i}z", summary="Plain note.") for i in range(5, 14)]
                + [article(14, source="gov-b", url="https://example.test/g/14",
                           title="Harbour quay works", summary="It cost \u00a37m."),
                   article(15, url="https://example.test/h/15", title="Other item15z",
                           summary="Also \u00a37m.")]
                + many_fillers(100, 6))
        c, model = self.load(rows)
        self.assertIn("harbour", model.common["pub-a"])
        self.assertNotIn("harbour", model.common["gov-b"])
        self.assertIn("\u00a37000000", model.distinctive)
        member = {"harbour": 1.0, "zz": 10.0}
        amounts = frozenset({"\u00a37000000"})
        span = "harbour \u00a37m"
        gov_vec = sb.clause_vector(span, "gov-b", model)
        pub_vec = sb.clause_vector(span, "pub-a", model)
        self.assertIn("harbour", gov_vec)
        self.assertNotIn("harbour", pub_vec)                     # R3: cue source zero weight
        span_amounts = frozenset({"\u00a37000000"})
        self.assertTrue(sb.clause_matches(gov_vec, span_amounts, member, amounts, model.distinctive))
        self.assertFalse(sb.clause_matches(pub_vec, span_amounts, member, amounts, model.distinctive))
        self.assertFalse(sb.clause_matches(gov_vec, span_amounts, {"zz": 1.0}, amounts,
                                           model.distinctive))   # number without shared token
        self.assertFalse(sb.clause_matches(gov_vec, frozenset(), member, frozenset(),
                                           model.distinctive))   # token without number


FLOOD = "Flooding at the copper mine halts output."


def follows_rows(ids=tuple(range(1, 20))):
    i = iter(ids)
    return [
        pa(next(i), "y1", "Copper mine flooding halts output", FLOOD, 10),                 # 1
        pa(next(i), "x1", "Shares slide after copper mine flooding", "Mining shares slid.", 12),  # 2
        pa(next(i), "ylate", "Copper mine flooding worsens", FLOOD, 14),                   # 3 later
        pa(next(i), "ycont", "Copper mine flooding - live", FLOOD, 11, path="live"),       # 4 container
        pa(next(i), "y2", "Copper mine flooding inquiry opens", FLOOD, 9),                 # 5
        pa(next(i), "x2", "Bank rallies following Zeta merger news", "Banks rallied.", 12),  # 6
        pa(next(i), "y3", "Zeta merger news lifts market", "The Zeta merger news.", 12),    # 7 equal time
        pa(next(i), "ma", "Fooland exports rise after Barland tariff cut",
           "Exports from Fooland rose.", 15),                                               # 8
        pa(next(i), "mb", "Barland tariff cut announced following Fooland exports rise",
           "Barland cut its tariff.", 15),                                                  # 9
        pa(next(i), "x3", "Prices ease after holiday lull", "Prices eased.", 16),           # 10
        pa(next(i), "y4", "Prices ease in quiet trading", "Prices eased in quiet trading.", 13),  # 11
        pa(next(i), "x4", "Rally after holiday lull; gains following copper mine flooding",
           "Gains.", 13),                                                                   # 12
        pa(next(i), "x5", "Rally after 10 years; gains following copper mine flooding",
           "Gains.", 13),                                                                   # 13
        pa(next(i), "x6", "Copper mine flooding hits shares",
           "Shares fell after copper mine flooding.", 13),                                 # 14
        pa(next(i), "x7", "Markets react after copper mine flooding - as it happened",
           "Markets reacted.", 13, path="live"),                                            # 15
        *many_fillers(200),
    ]


class FollowsFromStateTests(Fixture):
    IDS = tuple(range(1, 16))

    def build(self, ids=IDS, drop=()):
        c, model = self.load(follows_rows(ids))
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        artifact["candidates"] = [r for r in artifact["candidates"] if (r["a"], r["b"]) not in drop]
        artifact["candidate_count"] = len(artifact["candidates"])
        return c, sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))

    def run_s5(self, ids=IDS, drop=(), preset=None):
        c, state = self.build(ids, drop)
        for step in (sb.apply_copy_of, sb.apply_slot_conflict, sb.apply_companion, sb.apply_advisory):
            step(state)
        if preset:
            preset(state)
        sb.apply_follows_from(state)
        return c, state

    def p(self, state, x, y):
        return state.pairs[(min(x, y), max(x, y))]

    def test_preconditions(self):
        c, state = self.build()
        for key in ((1, 2), (2, 3), (2, 4), (2, 5), (6, 7), (8, 9), (10, 11), (1, 12), (1, 13),
                    (1, 14), (1, 15), (5, 15)):
            self.assertIn(key, state.pairs)
        self.assertTrue(state.articles[4].container_flag and state.articles[15].container_flag)
        for x in (2, 6, 8, 9):
            self.assertIsNone(state.pairs[(min(x, 1), max(x, 1))].terminal
                              if (min(x, 1), max(x, 1)) in state.pairs else None)

    def test_claims_and_cannot_links(self):
        c, state = self.run_s5()
        # X1: earlier Y2 and Y1 qualify; the later article and the container do not.
        claim = state.pending[2]
        self.assertEqual((claim.cue, claim.cue_position, claim.antecedents), ("after", 2, (5, 1)))
        self.assertEqual(self.p(state, 1, 2).cannot_link, "follows_from")
        self.assertEqual(self.p(state, 2, 5).cannot_link, "follows_from")
        self.assertIsNone(self.p(state, 2, 3).cannot_link)       # later partner (G4)
        self.assertIsNone(self.p(state, 2, 4).cannot_link)       # container partner (R2)
        self.assertEqual(state.pending[6].antecedents, (7,))     # equal time accepted
        # Container cue article allowed; earlier and equal-time matches, in (time, url) order.
        self.assertEqual(state.pending[15].antecedents, (5, 1, 2, 13, 14))
        for x in (10, 12, 13, 14):                               # no match, fallbacks, summary cue
            with self.subTest(x=x):
                self.assertNotIn(x, state.pending)
        self.assertIsNone(self.p(state, 10, 11).cannot_link)
        self.assertIsNone(self.p(state, 1, 12).cannot_link)
        self.assertIsNone(self.p(state, 1, 13).cannot_link)
        self.assertEqual(state.edges, [])
        # follows_from never makes a pair terminal.
        self.assertFalse([p for p in state.pairs.values()
                          if p.cannot_link == "follows_from" and p.terminal])

    def test_mutual_claims_are_both_kept(self):
        c, state = self.run_s5()
        self.assertEqual(state.pending[8].antecedents, (9,))
        self.assertEqual(state.pending[9].antecedents, (8,))
        pair = self.p(state, 8, 9)
        self.assertEqual((pair.terminal, pair.edge, pair.cannot_link), (None, None, "follows_from"))

    def test_earlier_terminal_pair_is_skipped(self):
        def preset(state):
            state.pairs[(1, 2)].terminal = "copy_of"
        c, state = self.run_s5(preset=preset)
        self.assertEqual(state.pending[2].antecedents, (5,))
        self.assertIsNone(self.p(state, 1, 2).cannot_link)

    def test_non_candidate_antecedent_ignored(self):
        c, state = self.run_s5(drop={(1, 2)})
        self.assertEqual(state.pending[2].antecedents, (5,))

    def test_idempotent(self):
        c, state = self.run_s5()
        before = (copy.deepcopy(state.pairs), dict(state.pending), list(state.edges))
        sb.apply_follows_from(state)
        self.assertEqual((state.pairs, state.pending, state.edges), before)

    def test_article_id_permutation(self):
        def by_url(c, state):
            url = {a.id: a.normalized_url for a in c.articles}
            pending = {url[x]: (r.cue, r.cue_position, tuple(url[y] for y in r.antecedents))
                       for x, r in state.pending.items()}
            pairs = {tuple(sorted((url[p.a], url[p.b]))): (p.terminal, p.cannot_link)
                     for p in state.pairs.values()}
            return pending, pairs
        permuted = tuple(random.Random(8).sample(range(1, 120), 15))
        self.assertEqual(by_url(*self.run_s5()), by_url(*self.run_s5(permuted)))


# --- S6: general same-event rule ----------------------------------------------------


def sa(i, slug, title, summary, hour=12, day=10, minute=0, second=0, path="news",
       source="pub-a"):
    ts = f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"
    return article(i, source=source, url=f"https://example.test/{path}/{slug}", title=title,
                   summary=summary, published_at=ts, first_seen_at=ts)


LUMBER = "Lumber mill expansion approved by county planners after review."


def same_event_rows(ids=tuple(range(1, 21)), second=None):
    """Pairs (1, 2) ... (19, 20). The second article of each pair is in the
    opinion format class, so no template slot conflict arises. ``second`` sets
    the time of the second article as (day, hour, minute, second)."""
    i = iter(ids)
    t2 = dict(zip(("day", "hour", "minute", "second"), second or (10, 12, 0, 0)))
    return [
        # full-vector branch only
        sa(next(i), "f1", "Kestrel remark", LUMBER),
        sa(next(i), "f2", "Osprey memo", LUMBER, path="opinion", **t2),
        # distinctive-number branch only
        sa(next(i), "n1", "Plover update on quarry",
           "Quarry fund of £7m set; granite crews wait for spring."),
        sa(next(i), "n2", "Heron bulletin",
           "Officials confirm £7m for quarry crews; fishermen protest loudly today.",
           path="opinion"),
        # title-only branch; the C10 token 'turbine' occurs only in the summaries
        sa(next(i), "t1", "Talks with Zeta Holdings",
           "Turbine contract signed on Monday by managers in the northern district while "
           "auditors reviewed spreadsheets carefully and clerks filed paperwork."),
        sa(next(i), "t2", "Visit by Zeta Holdings",
           "Engineers inspect each turbine blade during a long snowy morning shift beside "
           "frozen lakes and pine forests.", path="opinion"),
        # title-only branch; only entity tokens shared
        sa(next(i), "e1", "Talks with Omega Shipping",
           "Copper contract signed on Monday by managers in the eastern district while "
           "auditors reviewed ledgers slowly."),
        sa(next(i), "e2", "Visit by Omega Shipping",
           "Engineers inspect each kiln during a long rainy evening shift beside muddy "
           "rivers and oak woods.", path="opinion"),
        # entity tokens also occur outside the spans (C21, type level)
        sa(next(i), "c1", "Talks with Delta Freight", "Delta freight managers discuss winter routes."),
        sa(next(i), "c2", "Visit by Delta Freight", "Freight crews from delta docks rest.",
           path="opinion"),
        # entity span in one article only
        sa(next(i), "o1", "Strike at Harbour Board", "Dockers walked out over pay offers."),
        sa(next(i), "o2", "Pickets at harbour board", "Placards appeared near warehouses overnight.",
           path="opinion"),
        # shared amount only
        sa(next(i), "a1", "£9m levy", "Toll booth plans."),
        sa(next(i), "a2", "£9m charge", "Ferry fare rises.", path="opinion"),
        # all three branches
        sa(next(i), "g1", "Granite quarry reopens with £4m grant", "Granite quarry grant."),
        sa(next(i), "g2", "Granite quarry reopens with £4m grant", "Quarry grant approved.",
           path="opinion"),
        # full-vector branch only; only entity tokens shared
        sa(next(i), "x1", "Kestrel remark sparks debate over County Lumber Mill",
           "County lumber mill plans drew a crowd."),
        sa(next(i), "x2", "Osprey memo about County Lumber Mill reaches council",
           "Questions about county lumber mill persist.", path="opinion"),
        # number branch only; only entity tokens and the amount shared
        sa(next(i), "y1", "Plover note on Marsh Pumping Station",
           "Marsh pumping station receives £6m; residents cheer."),
        sa(next(i), "y2", "Heron letter",
           "The £6m marsh pumping station deal, critics say, arrives late for farmers "
           "and fishing villages nearby.", path="opinion"),
        *many_fillers(200),
    ]


S5_STEPS = (sb.apply_copy_of, sb.apply_slot_conflict, sb.apply_companion, sb.apply_advisory,
            sb.apply_follows_from)
FULL, NUMBER, TITLE = (float(rules.SAME_EVENT_COSINE), float(rules.SAME_EVENT_NUMBER_COSINE),
                       float(rules.SAME_EVENT_TITLE_COSINE))


def below(x):
    return math.nextafter(x, 0.0)


class SameEventFixture(Fixture):
    def build(self, rows, drop=()):
        c, model = self.load(rows)
        artifact = json.loads(predict.canonical_json(predict.stage_a_artifact(c)))
        artifact["candidates"] = [r for r in artifact["candidates"] if (r["a"], r["b"]) not in drop]
        artifact["candidate_count"] = len(artifact["candidates"])
        return c, sb.StageBState(c, model, sb.validate_candidate_artifact(artifact, c, model))

    def run_s6(self, rows=None, drop=(), preset=None):
        c, state = self.build(same_event_rows() if rows is None else rows, drop)
        for step in S5_STEPS:
            step(state)
        if preset:
            preset(state)
        sb.apply_same_event(state)
        return c, state

    def signals(self, state, x, y):
        """(full cosine, shared distinctive number, title cosine, C10 evidence tokens)."""
        model, a, b = state.model, state.articles[x], state.articles[y]
        fa, fb = state.features[x], state.features[y]
        title = cand.cosine(sb.title_vector(x, a.source_id, model),
                            sb.title_vector(y, b.source_id, model))
        excluded = (sb.amount_keys(a, model) | sb.amount_keys(b, model)
                    | sb.entity_token_types(fa.entities) | sb.entity_token_types(fb.entities))
        return (state.pairs[(x, y)].cosine,
                bool(model.numbers[x] & model.numbers[y] & model.distinctive), title,
                sb.shared_evidence_tokens(fa.vector, fb.vector, excluded))

    def edges_for(self, state, x, y):
        return [e for e in state.edges if (e.a, e.b) == (x, y)]


class SameEventRuleTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(rules.SAME_EVENT_MAX_HOURS, 72)
        self.assertEqual((rules.SAME_EVENT_COSINE, rules.SAME_EVENT_NUMBER_COSINE,
                          rules.SAME_EVENT_TITLE_COSINE),
                         (Fraction("0.35"), Fraction("0.15"), Fraction("0.50")))

    def test_branch_boundaries_are_inclusive(self):
        branches = sb.same_event_branches
        self.assertEqual(branches(FULL, False, 0.0), ("full_cosine",))
        self.assertEqual(branches(below(FULL), False, 0.0), ())
        self.assertEqual(branches(NUMBER, True, 0.0), ("number_cosine",))
        self.assertEqual(branches(below(NUMBER), True, 0.0), ())
        self.assertEqual(branches(below(FULL), False, 0.0), ())   # number branch needs a number
        self.assertEqual(branches(0.0, False, TITLE), ("title_cosine",))
        self.assertEqual(branches(0.0, True, below(TITLE)), ())
        self.assertEqual(branches(FULL, True, TITLE),
                         ("full_cosine", "number_cosine", "title_cosine"))

    def test_shared_evidence_helper(self):
        u = {"port": 1.0, "crane": 2.0, "£5000000": 3.0, "zero": 0.0}
        v = {"port": 1.0, "crane": 1.0, "£5000000": 1.0, "zero": 4.0, "other": 1.0}
        self.assertEqual(sb.shared_evidence_tokens(u, v, frozenset()),
                         ("crane", "port", "£5000000"))
        excluded = frozenset({"£5000000"}) | sb.entity_token_types({"crane hire ltd"})
        self.assertEqual(sb.shared_evidence_tokens(u, v, excluded), ("port",))

    def test_entity_token_types_use_the_committed_tokenizer(self):
        self.assertEqual(sb.entity_token_types({"bank of canada", "harbour boards"}),
                         frozenset({"bank", "canada", "harbour", "board"}))
        self.assertEqual(sb.entity_token_types(()), frozenset())


class SameEventStateTests(SameEventFixture):
    def test_preconditions(self):
        c, state = self.run_s6()
        expect = {  # pair: (branches that fire, has C10 evidence)
            (1, 2): (("full_cosine",), True),
            (3, 4): (("number_cosine",), True),
            (5, 6): (("title_cosine",), True),
            (7, 8): (("title_cosine",), False),
            (9, 10): (("full_cosine", "title_cosine"), False),
            (11, 12): (("full_cosine", "title_cosine"), False),
            (13, 14): (("number_cosine",), False),
            (15, 16): (("full_cosine", "number_cosine", "title_cosine"), True),
            (17, 18): (("full_cosine",), False),
            (19, 20): (("number_cosine",), False),
        }
        for (x, y), (branches, evidence) in expect.items():
            with self.subTest(pair=(x, y)):
                p = state.pairs[(x, y)]                  # every fixture pair is a candidate
                self.assertEqual((p.terminal, p.cannot_link), (None, None))
                fc, number, tc, tokens = self.signals(state, x, y)
                self.assertEqual(sb.same_event_branches(fc, number, tc), branches)
                self.assertEqual(bool(tokens), evidence)

    def test_each_branch_decides_with_evidence(self):
        c, state = self.run_s6()
        for key, branches in (((1, 2), ("full_cosine",)), ((3, 4), ("number_cosine",)),
                              ((5, 6), ("title_cosine",)),
                              ((15, 16), ("full_cosine", "number_cosine", "title_cosine"))):
            with self.subTest(pair=key):
                p = state.pairs[key]
                self.assertEqual((p.edge, p.s6, p.terminal, p.cannot_link),
                                 ("same_event", branches, None, None))
                self.assertEqual(self.edges_for(state, *key),
                                 [sb.Edge("same_event", *key, p.cosine)])

    def test_title_branch_evidence_from_full_vectors(self):
        c, state = self.run_s6()
        t5, t6 = set(state.model.title_tokens[5]), set(state.model.title_tokens[6])
        self.assertEqual(t5 & t6, {"zeta", "holding"})          # entity tokens only
        self.assertNotIn("turbine", t5 | t6)
        self.assertEqual(self.signals(state, 5, 6)[3], ("turbine",))
        self.assertEqual(state.pairs[(5, 6)].s6, ("title_cosine",))

    def test_c10_failure_blocks_every_branch(self):
        c, state = self.run_s6()
        for key in ((17, 18),            # full branch; shared tokens are entity tokens
                    (19, 20),            # number branch; entity tokens and the amount only
                    (7, 8),              # title branch; entity tokens only
                    (13, 14),            # shared amount alone
                    (9, 10),             # C21: entity tokens also outside the spans
                    (11, 12)):           # entity span in only one article
            with self.subTest(pair=key):
                p = state.pairs[key]
                self.assertEqual((p.edge, p.s6, p.terminal, p.cannot_link),
                                 (None, None, None, None))
                self.assertEqual(self.edges_for(state, *key), [])

    def test_c21_type_level_exclusion(self):
        c, state = self.run_s6()
        f9, f10 = state.features[9], state.features[10]
        self.assertEqual(sb.entity_token_types(f9.entities), frozenset({"delta", "freight"}))
        # 'delta' and 'freight' also occur outside the spans, in both summaries.
        for i in (9, 10):
            self.assertTrue({"delta", "freight"} <= set(cand.tokenize(state.articles[i].summary)))
        self.assertEqual(set(f9.vector) & set(f10.vector), {"delta", "freight"})
        # An entity span in article 11 only still excludes the types for the pair.
        f11, f12 = state.features[11], state.features[12]
        self.assertEqual(f12.entities, frozenset())
        self.assertEqual(sb.entity_token_types(f11.entities), frozenset({"harbour", "board"}))
        self.assertEqual(set(f11.vector) & set(f12.vector), {"harbour", "board"})

    def test_entity_span_in_either_article_excludes(self):
        # Swap IDs so the article with the span is the higher-ID side of the pair.
        ids = (*range(1, 11), 12, 11, *range(13, 21))
        c, state = self.run_s6(same_event_rows(ids))
        self.assertEqual(state.features[11].entities, frozenset())
        self.assertEqual(state.features[12].entities, frozenset({"harbour board"}))
        p = state.pairs[(11, 12)]
        self.assertEqual((p.edge, p.s6), (None, None))
        self.assertEqual(self.signals(state, 11, 12)[3], ())

    def test_amount_is_not_evidence(self):
        c, state = self.run_s6()
        a, b = state.articles[13], state.articles[14]
        shared = set(state.features[13].vector) & set(state.features[14].vector)
        self.assertEqual(shared, {"£9000000"})
        self.assertIn("£9000000",
                      sb.amount_keys(a, state.model) & sb.amount_keys(b, state.model))

    def test_cosine_boundaries_through_pair_state(self):
        for key, value, decided in (((1, 2), FULL, True), ((1, 2), below(FULL), False),
                                    ((3, 4), NUMBER, True), ((3, 4), below(NUMBER), False)):
            with self.subTest(pair=key, value=value):
                def preset(state, key=key, value=value):
                    state.pairs[key].cosine = value
                c, state = self.run_s6(preset=preset)
                self.assertEqual(state.pairs[key].edge == "same_event", decided)
                self.assertEqual(len(self.edges_for(state, *key)), int(decided))

    def test_time_boundary(self):
        for second, gap_seconds, decided in (((13, 12, 0, 0), 72 * 3600, True),
                                             ((13, 12, 0, 1), 72 * 3600 + 1, False),
                                             ((10, 12, 0, 0), 0, True)):
            with self.subTest(second=second):
                c, state = self.run_s6(same_event_rows(second=second))
                gap = state.articles[2].representative_time - state.articles[1].representative_time
                self.assertEqual(gap.total_seconds(), gap_seconds)
                self.assertGreaterEqual(state.pairs[(1, 2)].cosine, FULL)
                self.assertEqual(state.pairs[(1, 2)].s6, ("full_cosine",) if decided else None)
                self.assertEqual(len(self.edges_for(state, 1, 2)), int(decided))

    def test_container_pair_skipped(self):
        rows = same_event_rows()
        rows[1] = sa(2, "f2", "Osprey memo", LUMBER, path="live")
        c, state = self.run_s6(rows)
        self.assertTrue(state.articles[2].container_flag)
        self.assertGreaterEqual(state.pairs[(1, 2)].cosine, FULL)
        self.assertEqual((state.pairs[(1, 2)].edge, state.pairs[(1, 2)].s6), (None, None))
        self.assertFalse([e for e in state.edges if 2 in (e.a, e.b)])

    def test_terminal_and_cannot_link_pairs_skipped(self):
        def preset(state):
            state.pairs[(1, 2)].terminal = "advisory_abstain"
            state.pairs[(3, 4)].cannot_link = "follows_from"      # non-terminal
            state.pairs[(5, 6)].terminal = sb.SLOT_CONFLICT
            state.pairs[(5, 6)].cannot_link = sb.SLOT_CONFLICT
        c, state = self.run_s6(preset=preset)
        expect = {(1, 2): ("advisory_abstain", None), (3, 4): (None, "follows_from"),
                  (5, 6): (sb.SLOT_CONFLICT, sb.SLOT_CONFLICT)}
        for key, (terminal, cannot_link) in expect.items():
            with self.subTest(pair=key):
                p = state.pairs[key]
                self.assertEqual((p.terminal, p.cannot_link, p.edge, p.s6),
                                 (terminal, cannot_link, None, None))
                self.assertEqual(self.edges_for(state, *key), [])
        self.assertEqual(state.pairs[(15, 16)].edge, "same_event")   # others unaffected

    def test_real_follows_from_cannot_link_skipped(self):
        rows = same_event_rows()
        rows[1] = sa(2, "f2", "Osprey memo after lumber mill expansion", LUMBER, hour=13,
                     path="opinion")
        c, state = self.run_s6(rows)
        p = state.pairs[(1, 2)]
        self.assertIn(1, state.pending[2].antecedents)
        self.assertGreaterEqual(p.cosine, FULL)
        self.assertEqual((p.terminal, p.cannot_link, p.edge, p.s6),
                         (None, "follows_from", None, None))

    def test_s5_terminal_copy_pair_keeps_its_edge_only(self):
        rows = same_event_rows()
        text = LUMBER + " Residents welcomed the decision at a packed evening meeting."
        rows[0] = sa(1, "f1", "Kestrel remark", text)
        rows[1] = sa(2, "f2", "Kestrel remark", text, hour=13)
        c, state = self.run_s6(rows)
        p = state.pairs[(1, 2)]
        self.assertEqual((p.terminal, p.edge, p.s6), ("copy_of", "copy_of", None))
        self.assertEqual([e.type for e in self.edges_for(state, 1, 2)], ["copy_of"])

    def test_non_candidate_pair_untouched(self):
        c, state = self.run_s6(drop={(1, 2)})
        self.assertNotIn((1, 2), state.pairs)
        self.assertEqual(self.edges_for(state, 1, 2), [])
        self.assertEqual(state.pairs[(3, 4)].edge, "same_event")

    def test_s5_results_not_mutated(self):
        c, state = self.build(same_event_rows())
        for step in S5_STEPS:
            step(state)
        before = {k: (p.terminal, p.cannot_link, p.edge) for k, p in state.pairs.items()}
        s5_edges, pending = list(state.edges), dict(state.pending)
        sb.apply_same_event(state)
        self.assertEqual(state.edges[:len(s5_edges)], s5_edges)
        self.assertEqual(state.pending, pending)
        for k, p in state.pairs.items():
            terminal, cannot_link, edge = before[k]
            self.assertEqual((p.terminal, p.cannot_link), (terminal, cannot_link))
            if edge is not None:
                self.assertEqual((p.edge, p.s6), (edge, None))
        new = state.edges[len(s5_edges):]
        self.assertTrue(new and all(e.type == "same_event" for e in new))
        self.assertEqual(len({(e.a, e.b) for e in new}), len(new))
        self.assertEqual({(e.a, e.b) for e in new},
                         {k for k, p in state.pairs.items() if p.edge == "same_event"})

    def test_idempotent(self):
        c, state = self.run_s6()
        before = (copy.deepcopy(state.pairs), list(state.edges), dict(state.pending))
        sb.apply_same_event(state)
        self.assertEqual((state.pairs, state.edges, state.pending), before)

    def test_article_id_permutation(self):
        def by_url(c, state):
            url = {a.id: a.normalized_url for a in c.articles}
            pairs = {tuple(sorted((url[p.a], url[p.b]))): (p.terminal, p.cannot_link, p.edge, p.s6)
                     for p in state.pairs.values()}
            edges = sorted((e.type, *sorted((url[e.a], url[e.b])), e.cosine) for e in state.edges)
            return pairs, edges
        permuted = tuple(random.Random(6).sample(range(1, 150), 20))
        self.assertEqual(by_url(*self.run_s6()), by_url(*self.run_s6(same_event_rows(permuted))))


class SameEventSourceCommonTests(SameEventFixture):
    """'harbour' is source-common in gov-b only (3 of 10 gov-b articles)."""

    def rows(self):
        return [
            *[sa(k, f"g{k}", f"Gov item{k}z", "Plain note.", source="gov-b") for k in range(1, 8)],
            sa(8, "h8", "Harbour £5m", "Harbour levy.", source="gov-b"),
            sa(9, "h9", "Harbour toll", "Harbour gate.", source="gov-b"),
            sa(10, "h10", "Harbour fees rise", "Harbour fees.", source="gov-b"),
            sa(11, "p11", "Harbour £5m", "Harbour charge.", path="opinion"),
            sa(12, "p12", "Harbour cost", "Harbour cost £5m."),
            *many_fillers(200),
        ]

    def test_each_article_uses_its_own_zero_weights(self):
        c, state = self.run_s6(self.rows())
        model = state.model
        self.assertIn("harbour", model.common["gov-b"])
        self.assertNotIn("harbour", model.common["pub-a"])
        self.assertNotIn("harbour", sb.title_vector(8, "gov-b", model))
        self.assertIn("harbour", sb.title_vector(11, "pub-a", model))
        self.assertNotIn("harbour", state.features[8].vector)
        self.assertIn("harbour", state.features[11].vector)
        # gov-b with pub-a: the number branch fires, but 'harbour' has zero weight
        # in the gov-b article and the amount is excluded, so C10 fails.
        fc, number, tc, tokens = self.signals(state, 8, 11)
        self.assertTrue(number and fc >= NUMBER)
        self.assertEqual(tokens, ())
        self.assertEqual((state.pairs[(8, 11)].edge, state.pairs[(8, 11)].s6), (None, None))
        # Two pub-a articles: 'harbour' has positive weight in both and qualifies.
        self.assertEqual(self.signals(state, 11, 12)[3], ("harbour",))
        self.assertEqual(state.pairs[(11, 12)].edge, "same_event")


# --- S7: constrained clustering ------------------------------------------------------

FLOOR = float(rules.CLUSTER_MIN_CROSS_COSINE)
# |(3, 4)| = 5 and |(1, 3, 2, 1, 1)| = 4, so the cosine is exactly 3/20 = 0.15.
AT_FLOOR = ({"p": 3.0, "q": 4.0}, {"p": 1.0, "r": 3.0, "s": 2.0, "t": 1.0, "u": 1.0})


class ClusterFixture(Fixture):
    """Hand-built S5/S6 state over a small synthetic corpus.

    Articles are named by slug. Titles are lowercase and distinct, so no two
    articles have a template slot conflict unless a test gives them one. Each
    article's full vector is its own unit token (cosine 0 to every other
    article) unless ``vectors`` overrides it.
    """

    SLUGS = ("a", "b", "c", "d", "e")

    def make(self, pairs=(), edges=(), vectors=None, specs=None, ids=None):
        specs = specs or {s: dict(hour=10 + k) for k, s in enumerate(self.SLUGS)}
        ids = ids or tuple(range(1, len(specs) + 1))
        rows, self.id = [], {}
        for i, (slug, spec) in zip(ids, specs.items()):
            spec = dict(spec)
            title = spec.pop("title", f"{slug} lowercase story about item {slug}x")
            rows.append(sa(i, slug, title, "Made-up summary.", **spec))
            self.id[slug] = i
        c, model = self.load(rows)
        state = sb.StageBState(c, model, {})
        self.slug = {i: s for s, i in self.id.items()}
        for slug, i in self.id.items():
            vector = (vectors or {}).get(slug, {f"own-{slug}": 1.0})
            state.features[i] = dataclasses.replace(state.features[i], vector=vector)
        for x, y, value, *cannot_link in pairs:
            a, b = sorted((self.id[x], self.id[y]))
            state.pairs[(a, b)] = sb.PairState(a=a, b=b, cosine=value, routes=("lexical_cosine",),
                                               cannot_link=cannot_link[0] if cannot_link else None)
        state.pairs = dict(sorted(state.pairs.items()))
        for kind, x, y in edges:
            a, b = sorted((self.id[x], self.id[y]))
            state.pairs[(a, b)].edge = kind
            state.edges.append(sb.Edge(kind, a, b, state.pairs[(a, b)].cosine))
        return state

    def cluster(self, *args, **kwargs):
        state = self.make(*args, **kwargs)
        sb.apply_clustering(state)
        return state

    def edge(self, e):
        return (e.type, *sorted((self.slug[e.a], self.slug[e.b])))

    def clusters(self, state):
        return tuple(tuple(self.slug[i] for i in members) for members in state.clusters)

    def dispositions(self, state):
        return [(self.edge(d.edge), d.status,
                 tuple((self.slug[b.x], self.slug[b.y], b.kind, b.detail, b.candidate)
                       for b in d.blockers))
                for d in state.edge_dispositions]

    def status(self, state):
        return {self.edge(d.edge): d.status for d in state.edge_dispositions}


class ClusteringTests(ClusterFixture):
    def test_simple_merge(self):
        s = self.cluster([("a", "b", 0.5)], [("same_event", "a", "b")])
        self.assertEqual(self.clusters(s), (("a", "b"), ("c",), ("d",), ("e",)))
        self.assertEqual(self.dispositions(s), [(("same_event", "a", "b"), "merged", ())])

    def test_driving_edge_exempt_from_floor(self):
        s = self.cluster([("a", "b", 0.05)], [("same_event", "a", "b")])
        self.assertEqual(self.clusters(s)[0], ("a", "b"))

    def test_driving_cannot_link_rejects(self):
        s = self.cluster([("a", "b", 0.05, "follows_from")], [("same_event", "a", "b")])
        self.assertEqual(self.dispositions(s), [(("same_event", "a", "b"), "rejected",
                                                 (("a", "b", "cannot_link", "follows_from", True),))])
        self.assertEqual(self.clusters(s)[:2], (("a",), ("b",)))

    def three(self, ac=None, vectors=None, specs=None):
        pairs = [("a", "b", 0.9), ("b", "c", 0.5)] + ([("a", "c", *ac)] if ac else [])
        return self.cluster(pairs, [("copy_of", "a", "b"), ("same_event", "b", "c")],
                        vectors=vectors, specs=specs)

    def test_non_driving_floor(self):
        s = self.three(ac=(0.1,))
        self.assertEqual(self.dispositions(s)[1],
                         (("same_event", "b", "c"), "rejected",
                          (("a", "c", "cosine_floor", 0.1, True),)))
        self.assertEqual(self.clusters(s)[:2], (("a", "b"), ("c",)))
        self.assertEqual(self.status(self.three(ac=(FLOOR,)))[("same_event", "b", "c")], "merged")
        self.assertEqual(self.status(self.three(ac=(math.nextafter(FLOOR, 0.0),)))
                         [("same_event", "b", "c")], "rejected")

    def test_non_candidate_floor_uses_full_vectors(self):
        self.assertEqual(cand.cosine(*AT_FLOOR), FLOOR)
        at = self.three(vectors={"a": AT_FLOOR[0], "c": AT_FLOOR[1]})
        self.assertEqual(self.clusters(at)[0], ("a", "b", "c"))
        below = self.three()        # orthogonal vectors: non-candidate cosine 0
        self.assertEqual(self.dispositions(below)[1][2],
                         (("a", "c", "cosine_floor", 0.0, False),))

    def test_candidate_follows_from_cannot_link_blocks(self):
        s = self.three(ac=(0.8, "follows_from"))
        self.assertEqual(self.dispositions(s)[1][1:],
                         ("rejected", (("a", "c", "cannot_link", "follows_from", True),)))

    def test_non_candidate_slot_conflict_blocks(self):
        same = {"p": 1.0}
        specs = {"a": dict(hour=10, title="Acme opens Zeta widget plant"),
                 "b": dict(hour=11), "c": dict(hour=12, title="Acme opens Kappa widget plant"),
                 "d": dict(hour=13), "e": dict(hour=14)}
        s = self.three(vectors={"a": same, "c": same}, specs=specs)
        self.assertTrue(sb.slot_conflict(s.articles[self.id["a"]], s.articles[self.id["c"]]))
        self.assertEqual(self.dispositions(s)[1][1:],
                         ("rejected", (("a", "c", "cannot_link", sb.SLOT_CONFLICT, False),)))

    def test_passing_non_candidate_cross_pairs_merge(self):
        s = self.three(vectors={"a": {"p": 1.0, "x": 1.0}, "c": {"p": 1.0, "y": 1.0}})
        self.assertNotIn(tuple(sorted((self.id["a"], self.id["c"]))), s.pairs)
        self.assertEqual(self.clusters(s)[0], ("a", "b", "c"))

    def test_already_joined(self):
        s = self.cluster([("a", "b", 0.9), ("b", "c", 0.8), ("a", "c", 0.7)],
                     [("same_event", "a", "b"), ("same_event", "b", "c"),
                      ("same_event", "a", "c")])
        self.assertEqual([d[1] for d in self.dispositions(s)],
                         ["merged", "merged", "already_joined"])
        self.assertEqual(self.dispositions(s)[2][0], ("same_event", "a", "c"))

    def test_type_priority_beats_cosine(self):
        s = self.cluster([("a", "b", 0.9), ("b", "c", 0.2), ("a", "c", 0.1)],
                     [("same_event", "a", "b"), ("copy_of", "b", "c")])
        self.assertEqual(self.status(s), {("copy_of", "b", "c"): "merged",
                                          ("same_event", "a", "b"): "rejected"})
        self.assertEqual([d[0][0] for d in self.dispositions(s)], ["copy_of", "same_event"])

    def test_full_type_priority(self):
        pairs = [("a", "b", 0.2), ("b", "c", 0.3), ("c", "d", 0.4), ("d", "e", 0.5)]
        edges = [("same_event", "d", "e"), ("advisory", "c", "d"), ("companion", "b", "c"),
                 ("copy_of", "a", "b")]
        s = self.cluster(pairs, edges, vectors={k: {"p": 1.0} for k in "abcde"})
        self.assertEqual([d[0][0] for d in self.dispositions(s)], list(sb.EDGE_TYPES))

    def test_cosine_descending_within_type(self):
        s = self.cluster([("a", "b", 0.6), ("b", "c", 0.8), ("a", "c", 0.1)],
                     [("same_event", "a", "b"), ("same_event", "b", "c")])
        self.assertEqual(self.dispositions(s)[0][:2], (("same_event", "b", "c"), "merged"))
        self.assertEqual(self.status(s)[("same_event", "a", "b")], "rejected")

    def tie(self, specs):
        return self.cluster([("a", "b", 0.5), ("b", "c", 0.5), ("a", "c", 0.1)],
                        [("same_event", "b", "c"), ("same_event", "a", "b")], specs=specs)

    def test_c22_tie_break_decides_the_topology(self):
        early_a = {"a": dict(hour=10), "b": dict(hour=11), "c": dict(hour=12)}
        early_c = {"a": dict(hour=12), "b": dict(hour=11), "c": dict(hour=10)}
        s = self.tie(early_a)
        self.assertEqual(self.status(s), {("same_event", "a", "b"): "merged",
                                          ("same_event", "b", "c"): "rejected"})
        self.assertIn(("a", "b"), self.clusters(s))
        s = self.tie(early_c)
        self.assertEqual(self.status(s), {("same_event", "b", "c"): "merged",
                                          ("same_event", "a", "b"): "rejected"})
        self.assertIn(("c", "b"), self.clusters(s))       # members in (time, url) order

    def test_c22_sorts_each_edges_own_keys(self):
        # Times a=11, b=12, c=10: sorted pairs (b, c) = (10, 12) < (a, b) = (11, 12),
        # although b-c's unsorted (b, c) = (12, 10) would come after (11, 12).
        s = self.tie({"a": dict(hour=11), "b": dict(hour=12), "c": dict(hour=10)})
        self.assertEqual(self.status(s), {("same_event", "b", "c"): "merged",
                                          ("same_event", "a", "b"): "rejected"})

    def test_c22_equal_times_use_url(self):
        # Equal times; URLs .../news/a < .../news/b < .../news/c.
        s = self.tie({k: dict(hour=10) for k in "abc"})
        self.assertEqual(self.status(s)[("same_event", "a", "b")], "merged")
        # Article IDs ordered against the keys do not matter.
        s = self.cluster([("a", "b", 0.5), ("b", "c", 0.5), ("a", "c", 0.1)],
                     [("same_event", "a", "b"), ("same_event", "b", "c")],
                     specs={k: dict(hour=10) for k in "abc"}, ids=(9, 5, 2))
        self.assertEqual(self.status(s)[("same_event", "a", "b")], "merged")

    def test_rejected_edge_not_reopened(self):
        s = self.cluster([("a", "b", 0.9), ("b", "c", 0.8), ("a", "c", 0.1), ("c", "d", 0.5)],
                     [("copy_of", "a", "b"), ("same_event", "b", "c"),
                      ("same_event", "c", "d")])
        self.assertEqual(self.status(s), {("copy_of", "a", "b"): "merged",
                                          ("same_event", "b", "c"): "rejected",
                                          ("same_event", "c", "d"): "merged"})
        self.assertEqual(len(s.edge_dispositions), len(s.edges))
        self.assertEqual(self.clusters(s), (("a", "b"), ("c", "d"), ("e",)))

    def test_containers_absent(self):
        specs = {"a": dict(hour=10), "b": dict(hour=11), "c": dict(hour=12, path="live"),
                 "d": dict(hour=13), "e": dict(hour=14)}
        s = self.cluster([("a", "b", 0.5), ("b", "c", 0.9)], [("same_event", "a", "b")], specs=specs)
        container = self.id["c"]
        self.assertTrue(s.articles[container].container_flag)
        self.assertNotIn(container, s.cluster_of)
        self.assertNotIn(container, [i for members in s.clusters for i in members])
        self.assertEqual(self.clusters(s), (("a", "b"), ("d",), ("e",)))

    def test_cluster_and_member_order(self):
        s = self.cluster([("a", "b", 0.5), ("c", "e", 0.5)],
                     [("same_event", "a", "b"), ("same_event", "c", "e")],
                     specs={"a": dict(hour=14), "b": dict(hour=11), "c": dict(hour=12),
                            "d": dict(hour=10), "e": dict(hour=13)})
        self.assertEqual(self.clusters(s), (("d",), ("b", "a"), ("c", "e")))
        self.assertEqual({self.slug[i]: n for i, n in s.cluster_of.items()},
                         {"d": 0, "b": 1, "a": 1, "c": 2, "e": 2})

    def scenario(self, ids=None):
        pairs = [("a", "b", 0.9), ("b", "c", 0.5), ("c", "d", 0.5), ("a", "c", 0.1),
                 ("d", "e", 0.4, "follows_from"), ("b", "d", 0.6)]
        edges = [("same_event", "c", "d"), ("copy_of", "a", "b"), ("same_event", "b", "c"),
                 ("advisory", "d", "e"), ("same_event", "b", "d")]
        return self.make(pairs, edges, ids=ids)

    def summary(self, state):
        sb.apply_clustering(state)
        return self.clusters(state), self.dispositions(state)

    def test_article_id_permutation(self):
        first = self.summary(self.scenario())
        self.assertEqual(first, self.summary(self.scenario(ids=(41, 7, 23, 3, 15))))
        self.assertIn("rejected", [d[1] for d in first[1]])

    def test_incoming_edge_order(self):
        state = self.scenario()
        first = self.summary(state)
        for seed in range(5):
            random.Random(seed).shuffle(state.edges)
            self.assertEqual(self.summary(state), first)

    def test_all_blockers_in_order(self):
        # {a, b} and {c, d}; driving b-c. Blockers: a-c cannot-link and floor
        # (candidate), a-d floor (non-candidate), b-d floor (candidate).
        specs = {k: dict(hour=10 + n) for n, k in enumerate("abcde")}
        pairs = [("a", "b", 0.9), ("c", "d", 0.9), ("b", "c", 0.5),
                 ("a", "c", 0.05, "follows_from"), ("b", "d", 0.1)]
        edges = [("copy_of", "a", "b"), ("copy_of", "c", "d"), ("same_event", "b", "c")]
        for ids in ((1, 2, 3, 4, 5), (5, 4, 3, 2, 1), (3, 5, 1, 4, 2)):
            with self.subTest(ids=ids):
                s = self.cluster(pairs, edges, specs=specs, ids=ids)
                self.assertEqual(self.dispositions(s)[2],
                                 (("same_event", "b", "c"), "rejected",
                                  (("a", "c", "cannot_link", "follows_from", True),
                                   ("a", "c", "cosine_floor", 0.05, True),
                                   ("a", "d", "cosine_floor", 0.0, False),
                                   ("b", "d", "cosine_floor", 0.1, True))))

    def test_rerun_identical_and_inputs_unchanged(self):
        state = self.scenario()
        sb.apply_clustering(state)
        pairs, edges = copy.deepcopy(state.pairs), list(state.edges)
        result = (state.clusters, dict(state.cluster_of), state.edge_dispositions)
        sb.apply_clustering(state)
        self.assertEqual((state.clusters, state.cluster_of, state.edge_dispositions), result)
        self.assertEqual((state.pairs, state.edges), (pairs, edges))

    def test_malformed_edges_rejected(self):
        state = self.make([("a", "b", 0.5)], [("same_event", "a", "b")])
        state.edges.append(sb.Edge("copy_of", state.edges[0].a, state.edges[0].b, 0.5))
        with self.assertRaises(ValueError):
            sb.apply_clustering(state)
        state = self.make([("a", "b", 0.5)], [("same_event", "a", "b")])
        state.edges[0] = dataclasses.replace(state.edges[0], type="part_of")
        with self.assertRaises(ValueError):
            sb.apply_clustering(state)
        specs = {"a": dict(hour=10), "b": dict(hour=11, path="live")}
        state = self.make([("a", "b", 0.5)], [("same_event", "a", "b")], specs=specs)
        with self.assertRaises(ValueError):
            sb.apply_clustering(state)


class ClusteringPipelineTests(SameEventFixture):
    def test_every_non_container_once_after_s5_s6(self):
        c, state = self.run_s6()
        sb.apply_clustering(state)
        members = [i for cluster in state.clusters for i in cluster]
        non_containers = sorted(a.id for a in c.articles if not a.container_flag)
        self.assertEqual(sorted(members), non_containers)
        self.assertEqual(len(members), len(set(members)))
        self.assertEqual(sorted(state.cluster_of), non_containers)
        for n, cluster in enumerate(state.clusters):
            self.assertTrue(all(state.cluster_of[i] == n for i in cluster))
        for x, y in ((1, 2), (3, 4), (5, 6), (15, 16)):
            self.assertEqual(state.cluster_of[x], state.cluster_of[y])


if __name__ == "__main__":
    unittest.main()
