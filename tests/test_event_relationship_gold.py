"""Offline validation of the Jeff-approved event-relationship gold encoding.

Checks the committed research artifact docs/research/event-relationship-gold.json
for structural integrity and for the counts approved on 2026-10-05. Standard
library only; no network and no database are needed.

An optional check compares the file against a local Experiment 001 database to
confirm that no stored article title or summary text was copied into it. It is
skipped unless ATLAS_GOLD_CHECK_DB names a database file, which is opened
read-only.
"""

import collections
import itertools
import json
import os
import sqlite3
import unittest
from pathlib import Path

GOLD = Path(__file__).resolve().parents[1] / "docs" / "research" / "event-relationship-gold.json"

LABELS = {"copy_of", "same_event", "linked", "distinct", "unresolved", "not_scored"}
ROLES = {"reports", "discusses", "anticipates", None}
MEMBERSHIP_STATUSES = {"accepted", "unresolved", "not_scored"}
FORBIDDEN_KEYS = {"summary", "summary_raw", "title_raw", "text", "url", "normalized_url"}

APPROVED_LABEL_COUNTS = {
    1: {"same_event": 6, "linked": 3, "distinct": 3, "copy_of": 1, "unresolved": 1, "not_scored": 1},
    2: {"same_event": 20, "linked": 3, "distinct": 19, "copy_of": 1, "unresolved": 7, "not_scored": 3},
}


def load_gold():
    with open(GOLD, encoding="utf-8") as fh:
        return json.load(fh)


def pair_key(pair):
    return tuple(sorted((pair["a"], pair["b"])))


def accepted_events(item):
    events = collections.defaultdict(set)
    for m in item["memberships"]:
        if m["status"] == "accepted":
            events[m["article"]].add(m["event"])
    return events


def all_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from all_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from all_keys(value)


class GoldStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = load_gold()
        cls.events = {e["id"] for e in cls.gold["events"]}
        cls.relations = {r["id"]: r for r in cls.gold["event_relations"]}
        cls.items = cls.gold["items"]

    def test_ids_are_unique(self):
        for name, ids in (("events", [e["id"] for e in self.gold["events"]]),
                          ("relations", [r["id"] for r in self.gold["event_relations"]]),
                          ("items", [i["id"] for i in self.items])):
            with self.subTest(name=name):
                self.assertEqual(len(ids), len(set(ids)))

    def test_references_point_to_defined_events_and_relations(self):
        for r in self.gold["event_relations"]:
            self.assertIn(r["from"], self.events, r["id"])
            self.assertIn(r["to"], self.events, r["id"])
        for item in self.items:
            for m in item["memberships"]:
                self.assertIn(m["article"], item["articles"], item["id"])
                self.assertIn(m["role"], ROLES, item["id"])
                self.assertIn(m["status"], MEMBERSHIP_STATUSES, item["id"])
                if m["event"] is not None:
                    self.assertIn(m["event"], self.events, item["id"])
            for p in item["pairs"]:
                for rid in p.get("via", []) + p.get("also_linked_via", []) + p.get("via_unresolved", []):
                    self.assertIn(rid, self.relations, item["id"])
        for t in self.gold["event_level_timelines"]:
            self.assertTrue(set(t["events"]) <= self.events)
            self.assertTrue(set(t["relations"]) <= set(self.relations))

    def test_every_article_pair_labelled_exactly_once(self):
        self.assertEqual(sum(i["pass"] == 1 for i in self.items), 15)
        self.assertEqual(sum(i["pass"] == 2 for i in self.items), 25)
        for item in self.items:
            with self.subTest(item=item["id"]):
                self.assertEqual(len(item["articles"]), len(set(item["articles"])))
                expected = {tuple(sorted(c)) for c in itertools.combinations(item["articles"], 2)}
                got = [pair_key(p) for p in item["pairs"]]
                self.assertEqual(len(got), len(expected))
                self.assertEqual(set(got), expected)
                for p in item["pairs"]:
                    self.assertIn(p["label"], LABELS)

    def test_approved_pair_label_counts(self):
        counts = {1: collections.Counter(), 2: collections.Counter()}
        for item in self.items:
            for p in item["pairs"]:
                counts[item["pass"]][p["label"]] += 1
        for ps in (1, 2):
            self.assertEqual(dict(counts[ps]), APPROVED_LABEL_COUNTS[ps])
        self.assertEqual(sum(sum(c.values()) for c in counts.values()), 68)

    def test_approved_membership_counts(self):
        counts = {1: collections.Counter(), 2: collections.Counter()}
        for item in self.items:
            for m in item["memberships"]:
                if m["status"] == "accepted":
                    key = "accepted_with_role" if m["role"] else "accepted_role_not_scored"
                else:
                    key = m["status"]
                counts[item["pass"]][key] += 1
        self.assertEqual(dict(counts[1]), {"accepted_with_role": 26, "unresolved": 1})
        self.assertEqual(dict(counts[2]), {"accepted_with_role": 53, "accepted_role_not_scored": 2,
                                           "unresolved": 2, "not_scored": 1})

    def test_pair_labels_agree_with_memberships_and_relations(self):
        for item in self.items:
            events = accepted_events(item)
            for p in item["pairs"]:
                with self.subTest(item=item["id"], pair=pair_key(p)):
                    shared = events[p["a"]] & events[p["b"]]
                    if p["label"] == "same_event":
                        self.assertTrue(shared)
                    if p["label"] in ("distinct", "linked"):
                        self.assertFalse(shared)
                    if p["label"] == "linked":
                        self.assertTrue(p.get("via"))
                    if "alt_ok" in p:
                        self.assertEqual(p["label"], "unresolved")
                    vias = p.get("via", []) + p.get("also_linked_via", [])
                    if vias:
                        rels = [self.relations[r] for r in vias]
                        for r in rels:
                            self.assertEqual(r["status"], "accepted")
                            self.assertEqual(r["scope"], "article_scorable")
                        # The relations must connect an event of one article to an
                        # event of the other, directly or through a shared parent.
                        adjacent = collections.defaultdict(set)
                        for r in rels:
                            adjacent[r["from"]].add(r["to"])
                            adjacent[r["to"]].add(r["from"])
                        reached, todo = set(events[p["a"]]), list(events[p["a"]])
                        while todo:
                            for nxt in adjacent[todo.pop()] - reached:
                                reached.add(nxt)
                                todo.append(nxt)
                        self.assertTrue(reached & events[p["b"]])
                    for rid in p.get("via_unresolved", []):
                        self.assertEqual(self.relations[rid]["status"], "unresolved")

    def test_article_roles_consistent_across_items(self):
        roles = collections.defaultdict(set)
        for item in self.items:
            for m in item["memberships"]:
                if m["status"] == "accepted":
                    roles[(m["article"], m["event"])].add(m["role"])
        for key, found in roles.items():
            self.assertEqual(len(found), 1, key)

    def test_repeated_pairs_are_consistent(self):
        seen = collections.defaultdict(set)
        occurrences = collections.Counter()
        for item in self.items:
            for p in item["pairs"]:
                seen[pair_key(p)].add(p["label"])
                occurrences[pair_key(p)] += 1
        repeated = {k for k, n in occurrences.items() if n > 1}
        self.assertEqual(repeated, {(168, 170), (168, 175), (94, 95), (97, 98)})
        for key in repeated:
            self.assertEqual(len(seen[key]), 1, key)

    def test_multi_event_memberships(self):
        events = collections.defaultdict(set)
        for item in self.items:
            for m in item["memberships"]:
                if m["status"] == "accepted":
                    events[m["article"]].add(m["event"])
        multi = {a: e for a, e in events.items() if len(e) > 1}
        self.assertEqual(multi, {97: {"ev20", "ev21"}, 104: {"ev50", "ev51"}})
        self.assertEqual(events[170], {"ev05"})
        notes = [n for item in self.items for n in item.get("article_notes", []) if n["article"] == 170]
        self.assertTrue(notes)

    def test_event_relations(self):
        accepted = [r for r in self.gold["event_relations"] if r["status"] == "accepted"]
        follows = [r for r in accepted if r["type"] == "follows_from"]
        part_of = [r for r in accepted if r["type"] == "part_of"]
        self.assertEqual(len(follows), 7)
        self.assertEqual(len(part_of), 3)
        self.assertEqual(sorted(r["basis"] for r in follows),
                         ["publisher_stated"] * 2 + ["unspecified"] * 5)
        self.assertFalse([r for r in self.gold["event_relations"] if r.get("basis") == "atlas_inferred"])
        self.assertEqual(sum(r["status"] == "unresolved" for r in self.gold["event_relations"]), 3)
        mentions = collections.Counter()
        for r in accepted:
            for source in r["sources"]:
                mentions[(source[:2], r["type"])] += 1
        self.assertEqual(dict(mentions), {("P1", "follows_from"): 4, ("P1", "part_of"): 1,
                                          ("P2", "follows_from"): 5, ("P2", "part_of"): 2})

    def test_item3_timeline_is_event_level_only(self):
        timeline = {t["id"]: t for t in self.gold["event_level_timelines"]}["t01"]
        self.assertFalse(timeline["article_scorable"])
        for rid in timeline["relations"]:
            self.assertEqual(self.relations[rid]["scope"], "event_level_only")
        used = {rid for item in self.items for p in item["pairs"]
                for rid in p.get("via", []) + p.get("also_linked_via", [])}
        self.assertFalse(used & set(timeline["relations"]))
        item3 = {i["id"]: i for i in self.items}["P2-03"]
        for p in item3["pairs"]:
            self.assertEqual(p["label"], "unresolved")
            self.assertEqual(p.get("constraint"), "not_same_event")
        statuses = {m["article"]: m["status"] for m in item3["memberships"]}
        self.assertEqual(statuses, {4: "accepted", 17: "unresolved", 88: "unresolved"})

    def test_no_article_text_fields(self):
        self.assertFalse(set(all_keys(self.gold)) & FORBIDDEN_KEYS)
        self.assertNotIn("source_role", set(all_keys(self.gold)))


@unittest.skipUnless(os.environ.get("ATLAS_GOLD_CHECK_DB"),
                     "set ATLAS_GOLD_CHECK_DB to a local Experiment 001 database to run")
class OptionalDatabaseTextTests(unittest.TestCase):
    def test_no_stored_title_or_summary_text_copied(self):
        db_path = Path(os.environ["ATLAS_GOLD_CHECK_DB"]).resolve()
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT id, title, COALESCE(summary, '') FROM articles").fetchall()
        finally:
            conn.close()
        text = GOLD.read_text(encoding="utf-8")
        hits = []
        for article_id, title, summary in rows:
            if title and title in text:
                hits.append((article_id, "title"))
            for start in range(0, max(0, len(summary) - 40), 20):
                if summary[start:start + 40] in text:
                    hits.append((article_id, "summary"))
                    break
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
