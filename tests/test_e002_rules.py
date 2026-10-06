"""The Experiment 002 Run 1 constants must equal the frozen design table."""

import re
import unittest
from fractions import Fraction
from pathlib import Path

from research.e002 import rules

DESIGN = Path(__file__).resolve().parents[1] / "docs" / "experiments" / "002-event-relationship-baseline.md"

# Design-table name -> (value text in the design, value(s) in rules.py)
EXPECTED = {
    "TITLE_WEIGHT": ("2", [(rules.TITLE_WEIGHT, 2)]),
    "NUM_MAX_DF": ("5 articles", [(rules.NUM_MAX_DF, 5)]),
    "WRAPPER_MIN_SHARE": ("0.30", [(rules.WRAPPER_MIN_SHARE, Fraction("0.30"))]),
    "BOILERPLATE_MIN_TOKENS": ("12", [(rules.BOILERPLATE_MIN_TOKENS, 12)]),
    "BOILERPLATE_MIN_ARTICLES": ("2 (same source)", [(rules.BOILERPLATE_MIN_ARTICLES, 2)]),
    "SOURCE_COMMON_SHARE": ("0.20", [(rules.SOURCE_COMMON_SHARE, Fraction("0.20"))]),
    "TEMPLATE_MIN_SHARED": ("0.50 of the shorter title",
                            [(rules.TEMPLATE_MIN_SHARED, Fraction("0.50"))]),
    "CANDIDATE_MAX_DAYS": ("14", [(rules.CANDIDATE_MAX_DAYS, 14)]),
    "CAND_COSINE": ("0.15", [(rules.CAND_COSINE, Fraction("0.15"))]),
    "CAND_TOP_K` / `CAND_TOP_K_MIN": ("5 / 0.08", [(rules.CAND_TOP_K, 5),
                                                   (rules.CAND_TOP_K_MIN, Fraction("0.08"))]),
    "CAND_ENTITY_MAX_DF": ("5 articles", [(rules.CAND_ENTITY_MAX_DF, 5)]),
    "CAND_TAG_MAX_DF": ("8 articles", [(rules.CAND_TAG_MAX_DF, 8)]),
    "CAND_ISSUER_HOURS": ("24", [(rules.CAND_ISSUER_HOURS, 24)]),
    "COPY_TITLE_JACCARD": ("0.90", [(rules.COPY_TITLE_JACCARD, Fraction("0.90"))]),
    "COPY_SUMMARY_JACCARD": ("0.80", [(rules.COPY_SUMMARY_JACCARD, Fraction("0.80"))]),
    "COPY_MIN_SUMMARY_CHARS": ("80", [(rules.COPY_MIN_SUMMARY_CHARS, 80)]),
    "COPY_MAX_HOURS": ("48", [(rules.COPY_MAX_HOURS, 48)]),
    "COMPANION_MAX_MINUTES": ("60", [(rules.COMPANION_MAX_MINUTES, 60)]),
    "COMPANION_MIN_COSINE": ("0.15", [(rules.COMPANION_MIN_COSINE, Fraction("0.15"))]),
    "ADVISORY_MAX_DAYS": ("7", [(rules.ADVISORY_MAX_DAYS, 7)]),
    "ADVISORY_MIN_ANCHORS": ("2 (at most 1 entity span)",
                             [(rules.ADVISORY_MIN_ANCHORS, 2),
                              (rules.ADVISORY_MAX_ENTITY_ANCHORS, 1)]),
    "ADVISORY_MIN_COSINE": ("0.25", [(rules.ADVISORY_MIN_COSINE, Fraction("0.25"))]),
    "SAME_EVENT_MAX_HOURS": ("72", [(rules.SAME_EVENT_MAX_HOURS, 72)]),
    "SAME_EVENT_COSINE": ("0.35", [(rules.SAME_EVENT_COSINE, Fraction("0.35"))]),
    "SAME_EVENT_NUMBER_COSINE": ("0.15", [(rules.SAME_EVENT_NUMBER_COSINE, Fraction("0.15"))]),
    "SAME_EVENT_TITLE_COSINE": ("0.50", [(rules.SAME_EVENT_TITLE_COSINE, Fraction("0.50"))]),
    "CLUSTER_MIN_CROSS_COSINE": ("0.15", [(rules.CLUSTER_MIN_CROSS_COSINE, Fraction("0.15"))]),
    "CONTAINER_CLAUSE_COSINE": ("0.40", [(rules.CONTAINER_CLAUSE_COSINE, Fraction("0.40"))]),
    "PART_OF_ENABLED": ("false", [(rules.PART_OF_ENABLED, False)]),
    "FOLLOWS_FROM_BASIS": ("`publisher_stated` only",
                           [(rules.FOLLOWS_FROM_BASIS, ("publisher_stated",))]),
    "DISTINCT_RULES": ("template slot conflict only",
                       [(rules.DISTINCT_RULES, ("template_slot_conflict",))]),
}


def design_table():
    text = DESIGN.read_text(encoding="utf-8")
    section = text.split("## Frozen Run 1 configuration", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `(.+?)` \| (.+?) \| .+? \|$", section, re.MULTILINE)
    return dict(rows)


class FrozenConfigurationTests(unittest.TestCase):
    def test_design_table_matches_rules(self):
        table = design_table()
        self.assertEqual(set(table), set(EXPECTED))
        for name, (design_value, pairs) in EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(table[name], design_value)
                for actual, expected in pairs:
                    self.assertEqual(actual, expected)
                    self.assertIs(type(actual), type(expected))

    def test_corpus_hash_matches_design(self):
        self.assertIn(rules.CORPUS_SHA256, DESIGN.read_text(encoding="utf-8"))

    def test_catch_all_categories_recorded_in_design(self):
        text = DESIGN.read_text(encoding="utf-8")
        for category in rules.CATCH_ALL_CATEGORIES:
            self.assertIn(f"Run 1 catch-all categories: `{category}`", text)
            self.assertIn(f"catch-all category for the `tag` signal is `{category}`", text)

    def test_run1_safety_settings(self):
        self.assertFalse(rules.PART_OF_ENABLED)
        self.assertNotIn("atlas_inferred", rules.FOLLOWS_FROM_BASIS)
        self.assertEqual(rules.DISTINCT_RULES, ("template_slot_conflict",))


if __name__ == "__main__":
    unittest.main()
