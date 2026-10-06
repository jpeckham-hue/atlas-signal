"""Experiment 002 text primitives. All text is made up for these tests."""

import math
import unittest
from decimal import Decimal
from fractions import Fraction

from research.e002 import rules, text


def keys(s):
    return [a.key for a in text.extract_numbers(s)]


class NormalizationTests(unittest.TestCase):
    def test_quotes_dashes_case_and_nfkc(self):
        self.assertEqual(text.normalize_text("“Quoted” ‘x’ a–b c—d"),
                         "\"quoted\" 'x' a-b c-d")
        self.assertEqual(text.normalize_text("ﬁne STRASSE straße"), "fine strasse strasse")
        self.assertEqual(text.normalize_text("  many\n\tspaces  "), "many spaces")

    def test_possessive_removed_case_preserved_in_clean_text(self):
        self.assertEqual(text.clean_text("Fooland’s plan"), "Fooland plan")
        self.assertEqual(text.clean_text("ACME'S results"), "ACME results")
        self.assertEqual(text.clean_text("the workers' union"), "the workers' union")

    def test_empty(self):
        self.assertEqual(text.normalize_text(None), "")
        self.assertEqual(text.tokenize(""), [])


class TokenizationTests(unittest.TestCase):
    def test_stopwords_removed_and_case_folded(self):
        self.assertEqual(text.tokenize("The Widget Factory is open for the Season"),
                         ["widget", "factory", "open", "season"])

    def test_number_expressions_kept_whole_and_digit_fragments_dropped(self):
        self.assertEqual(text.tokenize("Widget sales of £2.5bn in 2026, up 4.2%"),
                         ["widget", "sale", "£2500000000", "4.2%"])
        self.assertEqual(text.tokenize("G20 talks"), ["g20", "talk"])

    def test_raw_tokens_keep_everything(self):
        self.assertEqual(text.raw_tokens("The 2 October 2026 sale of £10k"),
                         ["the", "2", "october", "2026", "sale", "of", "£10k"])
        self.assertEqual(text.raw_tokens("Acme Corp", keep_case=True), ["Acme", "Corp"])

    def test_stopword_list_is_generic_and_frozen(self):
        self.assertGreaterEqual(len(rules.STOPWORDS), 120)
        self.assertTrue(all(w.isalpha() and w.islower() for w in rules.STOPWORDS))


class PluralTests(unittest.TestCase):
    def test_rules_and_minimum_stem(self):
        cases = {
            "companies": "company", "prices": "price", "bills": "bill",
            "cats": "cats",       # result "cat" would be shorter than 4
            "news": "news",       # result "new" would be shorter than 4
            "class": "class", "bonus": "bonus", "trees": "tree",
            "plays": "play", "series": "sery", "word": "word",
        }
        for word, expected in cases.items():
            with self.subTest(word=word):
                self.assertEqual(text.strip_plural(word), expected)

    def test_boundary_exactly_four(self):
        self.assertEqual(text.strip_plural("dogss"), "dogss")
        self.assertEqual(text.strip_plural("frogs"), "frog")
        self.assertEqual(text.strip_plural("ties"), "ties")


class NumberTests(unittest.TestCase):
    def test_currencies_and_magnitudes(self):
        self.assertEqual(keys("costs £2 a litre"), ["£2"])
        self.assertEqual(keys("a $6.9M fund"), ["$6900000"])
        self.assertEqual(keys("a €2.9bn payment"), ["€2900000000"])
        self.assertEqual(keys("more than $20 million"), ["$20000000"])
        self.assertEqual(keys("£10k bonus"), ["£10000"])
        self.assertEqual(keys("1.5 billion people"), ["1500000000"])
        self.assertEqual(keys("5 thousand units"), ["5000"])
        self.assertEqual(keys("US$5 levy"), ["$5"])

    def test_magnitudes_are_whitespace_insensitive(self):
        groups = [
            ("£5m", "£5 m", "£5 million", "£5million", "£5 M", "£5 MILLION"),
            ("$2.9bn", "$2.9 bn", "$2.9 billion", "$2.9billion"),
            ("7k", "7 k", "7 thousand", "7thousand"),
        ]
        expected = ["£5000000", "$2900000000", "7000"]
        for forms, key in zip(groups, expected):
            for form in forms:
                with self.subTest(form=form):
                    self.assertEqual(keys(f"a {form} deal"), [key])
                    self.assertEqual(text.tokenize(f"a {form} deal"), [key, "deal"])

    def test_magnitude_must_be_a_whole_word(self):
        self.assertEqual(keys("5 kilometres"), [])
        self.assertEqual(keys("£5 more"), ["£5"])
        self.assertEqual(keys("50 bnx"), [])

    def test_grouping_and_percentages(self):
        self.assertEqual(keys("added 29,000 jobs"), ["29000"])
        self.assertEqual(keys("$20,533,121 total"), ["$20533121"])
        self.assertEqual(keys("rose 4.2% then 40 per cent then 7 percent"),
                         ["4.2%", "40%", "7%"])

    def test_amount_fields(self):
        (a,) = text.extract_numbers("€3.5m")
        self.assertEqual((a.currency, a.value, a.unit), ("€", Decimal("3500000.0"), None))

    def test_years_excluded_only_without_currency(self):
        self.assertEqual(keys("in 2026 and 1900 and 2100"), [])
        self.assertEqual(keys("in 1899 and 2101"), ["1899", "2101"])
        self.assertEqual(keys("cost £2026"), ["£2026"])
        self.assertEqual(keys("2,026 people"), ["2026"])

    def test_bare_integers_below_100_excluded(self):
        self.assertEqual(keys("99 people"), [])
        self.assertEqual(keys("100 people"), ["100"])
        self.assertEqual(keys("0 people"), [])
        self.assertEqual(keys("99% sure"), ["99%"])
        self.assertEqual(keys("£5 fee"), ["£5"])
        self.assertEqual(keys("2.5 points"), ["2.5"])

    def test_dates_excluded(self):
        for s in ("on 2 October 2026", "on October 2, 2026", "on 02 / 10 / 2026",
                  "on 2026-10-02", "on September 30", "on 30th of Sept 2026",
                  "from 15 Mar.", "in October 2026", "on 2/10/26"):
            with self.subTest(s=s):
                self.assertEqual(keys(s), [])

    def test_not_numbers(self):
        self.assertEqual(keys("G20 summit at 9.30am with 4x4 cars"), [])

    def test_distinct_and_deduplicated(self):
        self.assertEqual(keys("£2 here, £2 there, 300 more"), ["£2", "300"])

    def test_distinctive_numbers_threshold(self):
        docs = [["£2"]] * rules.NUM_MAX_DF + [["300"]] * (rules.NUM_MAX_DF + 1)
        self.assertEqual(text.distinctive_numbers(docs), frozenset({"£2"}))


class EntitySpanTests(unittest.TestCase):
    def test_runs_and_single_tokens(self):
        self.assertEqual(text.entity_spans("Talks between Northwind Bank and Acme stall"),
                         ("Northwind Bank", "Acme"))

    def test_title_initial_single_excluded_but_initial_run_kept(self):
        self.assertEqual(text.entity_spans("Lorem plans new widgets"), ())
        self.assertEqual(text.entity_spans("Jane Doe joins Acme"), ("Jane Doe", "Acme"))

    def test_initial_stopword_dropped(self):
        self.assertEqual(text.entity_spans("The Ministry approves plan"), ("Ministry",))

    def test_segment_initial_after_colon_and_spaced_dash(self):
        self.assertEqual(text.entity_spans("Watch: Why Fooland matters"), ("Fooland",))
        self.assertEqual(text.entity_spans("Deal for Acme - Northwind Bank reacts"),
                         ("Acme", "Northwind Bank"))
        self.assertEqual(text.entity_spans("Deal for Acme - Northwind reacts"), ("Acme",))

    def test_month_and_weekday_names_excluded(self):
        self.assertEqual(text.entity_spans("Results from Acme in October on Monday"), ("Acme",))

    def test_punctuation_breaks_runs_hyphen_joins(self):
        self.assertEqual(text.entity_spans("Talks with Acme, Northwind stall"), ("Acme", "Northwind"))
        self.assertEqual(text.entity_spans("New Fooland-Barland deal"), ("New Fooland Barland",))

    def test_possessive_and_duplicates(self):
        self.assertEqual(text.entity_spans("Report on Acme’s plan and Acme staff"), ("Acme",))


class SentenceTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(
            text.split_sentences("First one here. Second follows! Is it third? \"Quoted\" fourth."),
            ["First one here.", "Second follows!", "Is it third?", "\"Quoted\" fourth."])

    def test_abbreviations_initials_and_lowercase(self):
        self.assertEqual(
            text.split_sentences("Mr. Doe spoke. J. Bloggs left. Prices rose 2.5 per cent. then fell"),
            ["Mr. Doe spoke.", "J. Bloggs left.", "Prices rose 2.5 per cent. then fell"])

    def test_empty(self):
        self.assertEqual(text.split_sentences(None), [])


class TrigramTests(unittest.TestCase):
    def test_trigrams_and_jaccard(self):
        self.assertEqual(text.char_trigrams("Abcd"), frozenset({"abc", "bcd"}))
        self.assertEqual(text.char_trigrams("ab"), frozenset({"ab"}))
        self.assertEqual(text.char_trigrams(""), frozenset())
        self.assertEqual(text.jaccard({"a", "b"}, {"b", "c"}), Fraction(1, 3))
        self.assertEqual(text.jaccard(set(), set()), Fraction(0))
        self.assertEqual(text.jaccard(text.char_trigrams("Same “text”"),
                                      text.char_trigrams("same \"TEXT\"")), 1)


class TfIdfTests(unittest.TestCase):
    def setUp(self):
        self.docs = [["widget", "bank"], ["widget", "port"], ["widget", "bank", "rail"]]
        self.df = text.document_frequencies(self.docs)
        self.idf = text.inverse_document_frequencies(self.df, len(self.docs))

    def test_idf_values(self):
        self.assertEqual(self.idf["widget"], 0.0)
        self.assertEqual(self.idf["bank"], math.log(3 / 2))
        self.assertEqual(self.idf["rail"], math.log(3))
        self.assertEqual(list(self.idf), sorted(self.idf))

    def test_title_weighting_and_zero_weight(self):
        v = text.weighted_vector(["bank"], ["bank", "rail", "widget", "unknown"], self.idf)
        self.assertEqual(v, {"bank": 3 * math.log(1.5), "rail": math.log(3)})
        v = text.weighted_vector(["bank"], [], self.idf, title_weight=1, zero_weight={"bank"})
        self.assertEqual(v, {})

    def test_cosine_bounds_and_determinism(self):
        u = text.weighted_vector(["bank", "rail"], ["port"], self.idf)
        w = text.weighted_vector(["port"], ["rail"], self.idf)
        self.assertAlmostEqual(text.cosine(u, u), 1.0, places=12)
        self.assertEqual(text.cosine(u, {}), 0.0)
        self.assertEqual(text.cosine({"bank": 1.0}, {"rail": 1.0}), 0.0)
        reordered = dict(reversed(list(u.items())))
        self.assertEqual(text.cosine(u, w), text.cosine(reordered, w))
        self.assertEqual(text.cosine(u, w), text.cosine(w, u))


class WrapperTests(unittest.TestCase):
    def lists(self, shared):
        return [["agency", "notice", "item", str(i)] if i < shared else ["item", str(i), "end"]
                for i in range(10)]

    def test_frozen_share_boundary(self):
        self.assertEqual(rules.WRAPPER_MIN_SHARE, Fraction(3, 10))
        w = text.detect_wrappers(self.lists(3))
        self.assertIn(("agency", "notice", "item"), w.prefixes)
        w = text.detect_wrappers(self.lists(2))
        self.assertNotIn(("agency",), w.prefixes)

    def test_suffixes_and_stripping(self):
        lists = [["story", str(i), "continue", "reading"] for i in range(3)] + \
                [["other", str(i)] for i in range(7)]
        w = text.detect_wrappers(lists)
        self.assertIn(("continue", "reading"), w.suffixes)
        self.assertEqual(text.strip_wrappers(["story", "x", "continue", "reading"], w), ["x"])
        self.assertEqual(text.strip_wrappers(["story", "continue", "reading"], w), [])
        self.assertEqual(text.strip_wrappers(["plain"], w), ["plain"])

    def test_empty(self):
        self.assertEqual(text.detect_wrappers([]), text.Wrappers(frozenset(), frozenset()))


class RepeatedSentenceTests(unittest.TestCase):
    TWELVE = "Our widget programme helps every small firm in the region grow faster."
    ELEVEN = "Our widget programme helps every small firm in the region grow."

    def test_frozen_token_and_article_thresholds(self):
        self.assertEqual(len(text.sentence_key(self.TWELVE)), rules.BOILERPLATE_MIN_TOKENS)
        docs = [("s", 1, f"Lead one. {self.TWELVE}"), ("s", 2, f"{self.TWELVE} Other lead.")]
        self.assertEqual(text.repeated_sentences(docs, frozenset()),
                         {"s": frozenset({text.sentence_key(self.TWELVE)})})

    def test_below_thresholds(self):
        self.assertEqual(len(text.sentence_key(self.ELEVEN)), rules.BOILERPLATE_MIN_TOKENS - 1)
        self.assertEqual(text.repeated_sentences(
            [("s", 1, self.ELEVEN), ("s", 2, self.ELEVEN)], frozenset()), {})
        self.assertEqual(text.repeated_sentences([("s", 1, self.TWELVE)], frozenset()), {})
        self.assertEqual(text.repeated_sentences(
            [("s", 1, self.TWELVE), ("s", 1, self.TWELVE)], frozenset()), {})
        self.assertEqual(text.repeated_sentences(
            [("s", 1, self.TWELVE), ("t", 2, self.TWELVE)], frozenset()), {})

    def test_distinctive_number_exempts(self):
        sentence = "Our widget programme gives £750 to every small firm in the region now."
        docs = [("s", 1, sentence), ("s", 2, sentence)]
        self.assertEqual(text.repeated_sentences(docs, frozenset({"£750"})), {})
        self.assertEqual(len(text.repeated_sentences(docs, frozenset())["s"]), 1)

    def test_remove_sentences(self):
        key = text.sentence_key(self.TWELVE)
        self.assertEqual(text.remove_sentences(f"Lead one. {self.TWELVE}", frozenset({key})),
                         "Lead one.")


class SourceCommonTests(unittest.TestCase):
    def test_frozen_share_is_strictly_greater(self):
        self.assertEqual(rules.SOURCE_COMMON_SHARE, Fraction(1, 5))
        docs = [("s", ["three"] if i < 3 else []) for i in range(10)]
        two = [("t", ["two"] if i < 2 else []) for i in range(10)]
        result = text.source_common_tokens(docs + two)
        self.assertEqual(result, {"s": frozenset({"three"}), "t": frozenset()})

    def test_counts_documents_not_occurrences(self):
        docs = [("s", ["x", "x", "x"])] + [("s", [])] * 9
        self.assertEqual(text.source_common_tokens(docs), {"s": frozenset()})


class TemplateTests(unittest.TestCase):
    def test_slot_conflict(self):
        c = text.compare_title_templates("Death of a resident at Northwind Home",
                                         "Death of a resident at Barland House")
        self.assertTrue(c.slot_conflict)
        self.assertEqual(c.residual_a, ("Northwind", "Home"))
        self.assertEqual(c.residual_b, ("Barland", "House"))

    def test_identical_titles_never_conflict(self):
        c = text.compare_title_templates("Acme opens plant", "Acme opens plant")
        self.assertEqual(c.shared_share, 1)
        self.assertFalse(c.slot_conflict)

    def test_share_boundary(self):
        at = text.compare_title_templates("Minister Doe visits Fooland", "Minister Roe tours Barland")
        self.assertEqual(at.shared_share, Fraction(1, 4))
        self.assertFalse(at.slot_conflict)
        half = text.compare_title_templates("Minister Doe visits Fooland",
                                            "Minister Roe visits Barland")
        self.assertEqual(half.shared_share, Fraction(1, 2))
        self.assertTrue(half.slot_conflict)

    def test_overlapping_or_missing_slots(self):
        c = text.compare_title_templates("Statement by Doe with Fooland leader",
                                         "Statement by Roe with Fooland Doe")
        self.assertFalse(c.slot_conflict)  # residual capitalized tokens overlap ("doe")
        c = text.compare_title_templates("Prices rise in the north today",
                                         "Prices rise in the south today")
        self.assertFalse(c.slot_conflict)  # residuals have no capitalized or numeric token

    def test_numeric_slots(self):
        c = text.compare_title_templates("Daily notice number 41", "Daily notice number 42")
        self.assertTrue(c.slot_conflict)

    def test_lcs_deterministic(self):
        self.assertEqual(text.longest_common_subsequence(["a", "b", "c"], ["A", "c", "b"]),
                         [(0, 0), (2, 1)])
        self.assertEqual(text.longest_common_subsequence([], ["a"]), [])


if __name__ == "__main__":
    unittest.main()
