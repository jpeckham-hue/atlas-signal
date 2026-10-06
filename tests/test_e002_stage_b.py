"""Experiment 002 Stage B: candidate-artifact loading and S5 step 1 (`copy_of`).

Synthetic fixtures only; no repository gold and no sealed real artifact.
"""

import copy
import json
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


if __name__ == "__main__":
    unittest.main()
